import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import HTTPException

from no1.computer_control.models import WorkflowDefinition
from no1.computer_control.storage import WorkflowStore
from no1.computer_control.storage import RevisionConflict
from no1.daemon.computer_control_ops import try_handle_computer_control_op
from no1.ports.web.routes.computer_control import create_routers
from no1.ports.web.schemas import RouteContext


def _definition(*, enabled: bool = False) -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "name": "Trigger workflow",
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "end", "type": "end"},
            ],
            "edges": [{"source": "start", "target": "end"}],
            "triggers": [
                {
                    "id": "every-minute",
                    "type": "interval",
                    "enabled": enabled,
                    "actor_id": "foreman",
                    "config": {"seconds": 60},
                }
            ],
        }
    )


class _Setup:
    def __init__(self) -> None:
        self.value = {"phase": "ready", "fingerprint": "fp-current", "session_running": True}

    def status(self):
        return dict(self.value)


class _Scheduler:
    def __init__(self) -> None:
        self.calls = []

    def status(self, group_id: str, workflow_id: str):
        self.calls.append((group_id, workflow_id))
        return {"available": True, "running": True, "triggers": {}}

    def test_trigger(self, group_id: str, workflow_id: str, trigger):
        self.calls.append((group_id, workflow_id, trigger["id"], "test"))
        return {"available": True, "preview": {"source": "scheduler"}}


def _context(home: Path) -> RouteContext:
    async def daemon(*args, **kwargs):
        del args, kwargs
        return {}

    async def cached_json(*args, **kwargs):
        del args, kwargs
        return {}

    return RouteContext(
        home=home,
        version="test",
        web_mode="local",
        read_only=False,
        exhibit_cache_ttl_s=0,
        exhibit_allow_terminal=False,
        dist_dir=None,
        daemon=daemon,
        cached_json=cached_json,
        apply_web_logging=lambda *args, **kwargs: None,
    )


def _endpoint(routers, method: str, suffix: str):
    for router in routers:
        for route in router.routes:
            if method in getattr(route, "methods", set()) and str(getattr(route, "path", "")).endswith(suffix):
                return route.endpoint
    raise AssertionError(f"route not found: {method} {suffix}")


class TestComputerControlTriggerApi(unittest.TestCase):
    def test_daemon_trigger_update_fails_closed_before_write_when_setup_is_unready(self):
        store = Mock()
        service = SimpleNamespace(
            store=store,
            setup=SimpleNamespace(
                status=lambda: {"phase": "failed", "fingerprint": ""}
            ),
        )
        definition = _definition(enabled=True).model_dump(mode="json")
        with patch(
            "no1.daemon.computer_control_ops.get_services", return_value=service
        ):
            response, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "admin_trigger",
                    "action": "update",
                    "group_id": "g-test",
                    "workflow_id": "wf-test",
                    "caller_surface": "local_web",
                    "definition": definition,
                    "expected_revision": 1,
                    "allow_unready_disable": False,
                },
            )

        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "windows_mcp_not_ready")
        store.update_triggers.assert_not_called()

    def test_daemon_trigger_update_preserves_revision_conflict(self):
        store = Mock()
        store.update_triggers.side_effect = RevisionConflict(7)
        service = SimpleNamespace(
            store=store,
            setup=SimpleNamespace(
                status=lambda: {"phase": "ready", "fingerprint": "fp-current"}
            ),
        )
        with patch(
            "no1.daemon.computer_control_ops.get_services", return_value=service
        ):
            response, _ = try_handle_computer_control_op(
                "computer_control",
                {
                    "command": "admin_trigger",
                    "action": "update",
                    "group_id": "g-test",
                    "workflow_id": "wf-test",
                    "caller_surface": "local_web",
                    "definition": _definition(enabled=True).model_dump(mode="json"),
                    "expected_revision": 1,
                },
            )

        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "revision_conflict")
        self.assertEqual(response.error.details["current_revision"], 7)

    def test_trigger_lifecycle_has_status_read_only_test_and_fail_closed_disable(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            group_path = home / "groups" / "g-test"
            group_path.mkdir(parents=True)
            group = SimpleNamespace(path=group_path)
            store = WorkflowStore(home)
            setup = _Setup()
            scheduler = _Scheduler()
            service = SimpleNamespace(store=store, setup=setup, scheduler=scheduler)
            daemon_calls = []

            def fake_call_daemon(request, **kwargs):
                del kwargs
                daemon_calls.append(request)
                args = request["args"]
                if args.get("command") not in {"scheduler", "admin_trigger"}:
                    return {"ok": False, "error": {"code": "unexpected_command"}}
                if args.get("command") == "scheduler" and args.get("action") == "status":
                    result = {"available": True, "running": True, "triggers": {}}
                elif args.get("command") == "admin_trigger":
                    definition = WorkflowDefinition.model_validate(args["definition"])
                    value = service.store.update_triggers(
                        args["group_id"],
                        args["workflow_id"],
                        definition,
                        expected_revision=int(args["expected_revision"]),
                    )
                    setup_status = setup.status()
                    ready = setup_status["phase"] == "ready" and bool(setup_status["fingerprint"])
                    if ready:
                        service.store.publish(args["group_id"], args["workflow_id"], value["version"])
                        service.store.trust(
                            args["group_id"],
                            args["workflow_id"],
                            value["version"],
                            fingerprint=setup_status["fingerprint"],
                            permissions=["all_windows_mcp_tools"],
                        )
                        for trigger in definition.triggers:
                            service.store.set_trigger_activation(
                                args["group_id"],
                                args["workflow_id"],
                                trigger.id,
                                enabled=bool(trigger.enabled),
                                version=value["version"],
                                fingerprint=setup_status["fingerprint"] if trigger.enabled else "",
                                reason="active" if trigger.enabled else "disabled",
                            )
                        value = service.store.get(args["group_id"], args["workflow_id"], version=value["version"])
                    result = {
                        "value": value,
                        "finalization": {
                            "auto_published": ready,
                            "auto_trusted": ready,
                            "runtime_activation_opened": ready and any(item.enabled for item in definition.triggers),
                            "setup_ready": ready,
                        },
                    }
                else:
                    result = {"available": True, "preview": {"source": "scheduler"}}
                return {"ok": True, "result": {"result": result}}

            with patch("no1.computer_control.storage.load_group", return_value=group), patch(
                "no1.ports.web.routes.computer_control._service", return_value=service
            ), patch("no1.ports.web.routes.computer_control.audit"), patch(
                "no1.ports.web.routes.computer_control._emit"
            ), patch(
                "no1.ports.web.routes.computer_control.call_daemon",
                side_effect=fake_call_daemon,
            ):
                created = store.create("g-test", _definition())
                workflow_id = created["manifest"]["workflow_id"]
                routers = create_routers(_context(home))
                get_triggers = _endpoint(routers, "GET", "/triggers")
                put_triggers = _endpoint(routers, "PUT", "/triggers")
                patch_activation = _endpoint(routers, "PATCH", "/activation")
                test_trigger = _endpoint(routers, "POST", "/test")

                initial = asyncio.run(get_triggers("g-test", workflow_id))["result"]
                self.assertFalse(initial["trigger_status"]["every-minute"]["runtime_enabled"])
                self.assertTrue(initial["scheduler"]["running"])

                enabled_trigger = _definition(enabled=True).triggers[0].model_dump(mode="json")
                enabled = asyncio.run(
                    put_triggers(
                        "g-test",
                        workflow_id,
                        {"expected_revision": initial["revision"], "triggers": [enabled_trigger]},
                    )
                )["result"]
                self.assertEqual(enabled["version"], 2)
                self.assertEqual(enabled["published_version"], 2)
                self.assertTrue(enabled["finalization"]["auto_published"])
                self.assertTrue(enabled["finalization"]["auto_trusted"])
                self.assertTrue(enabled["activation"]["every-minute"]["enabled"])

                version_before_test = store.get("g-test", workflow_id)["version"]
                tested = asyncio.run(test_trigger("g-test", workflow_id, "every-minute", {}))["result"]
                self.assertTrue(tested["valid"])
                self.assertTrue(tested["read_only"])
                self.assertEqual(tested["preview"]["source"], "scheduler")
                self.assertGreater(tested["preview"]["next_fire_at"], 0)
                self.assertEqual(store.get("g-test", workflow_id)["version"], version_before_test)

                at_preview = asyncio.run(
                    test_trigger(
                        "g-test",
                        workflow_id,
                        "every-minute",
                        {
                            "trigger": {
                                "id": "every-minute",
                                "type": "at",
                                "config": {"at": "2030-01-02T03:04:05+00:00"},
                            }
                        },
                    )
                )["result"]
                self.assertEqual(at_preview["preview"]["at"], "2030-01-02T03:04:05+00:00")
                self.assertGreater(at_preview["preview"]["next_fire_at"], 0)
                self.assertEqual(store.get("g-test", workflow_id)["version"], version_before_test)

                schedule_preview = asyncio.run(
                    test_trigger(
                        "g-test",
                        workflow_id,
                        "every-minute",
                        {
                            "trigger": {
                                "id": "every-minute",
                                "type": "schedule",
                                "config": {
                                    "time": "09:30",
                                    "weekdays": [0, 2, 4],
                                    "timezone": "Asia/Shanghai",
                                },
                            }
                        },
                    )
                )["result"]
                self.assertEqual(schedule_preview["preview"]["time"], "09:30")
                self.assertEqual(schedule_preview["preview"]["weekdays"], [0, 2, 4])
                self.assertGreater(schedule_preview["preview"]["next_fire_at"], 0)
                self.assertEqual(store.get("g-test", workflow_id)["version"], version_before_test)

                setup.value = {"phase": "failed", "fingerprint": "", "session_running": False}
                disabled = asyncio.run(
                    patch_activation(
                        "g-test",
                        workflow_id,
                        "every-minute",
                        {"enabled": False, "expected_revision": enabled["revision"]},
                    )
                )["result"]
                self.assertEqual(disabled["version"], 3)
                self.assertEqual(disabled["published_version"], 2)
                self.assertFalse(disabled["activation"]["every-minute"]["enabled"])
                self.assertFalse(disabled["finalization"]["auto_published"])
                self.assertFalse(disabled["finalization"]["auto_trusted"])

                disabled_trigger = _definition(enabled=False).triggers[0].model_dump(mode="json")
                disabled_saved = asyncio.run(
                    put_triggers(
                        "g-test",
                        workflow_id,
                        {
                            "expected_revision": disabled["revision"],
                            "triggers": [disabled_trigger],
                        },
                    )
                )["result"]
                self.assertFalse(disabled_saved["activation"]["every-minute"]["enabled"])
                self.assertFalse(disabled_saved["finalization"]["auto_published"])

                current_version = store.get("g-test", workflow_id)["version"]
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(
                        patch_activation(
                            "g-test",
                            workflow_id,
                            "every-minute",
                            {"enabled": True, "expected_revision": disabled["revision"]},
                        )
                    )
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(store.get("g-test", workflow_id)["version"], current_version)
                self.assertFalse(scheduler.calls)
                self.assertTrue(daemon_calls)
                self.assertTrue(
                    all(
                        call["args"].get("command") in {"scheduler", "admin_trigger"}
                        for call in daemon_calls
                    )
                )

    def test_store_refuses_to_open_activation_gate_without_current_trust(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            group_path = home / "groups" / "g-test"
            group_path.mkdir(parents=True)
            group = SimpleNamespace(path=group_path)
            store = WorkflowStore(home)
            with patch("no1.computer_control.storage.load_group", return_value=group):
                created = store.create("g-test", _definition(enabled=True))
                workflow_id = created["manifest"]["workflow_id"]
                store.publish("g-test", workflow_id, 1)
                with self.assertRaisesRegex(ValueError, "trusted"):
                    store.set_trigger_activation(
                        "g-test",
                        workflow_id,
                        "every-minute",
                        enabled=True,
                        version=1,
                        fingerprint="fp-current",
                    )


if __name__ == "__main__":
    unittest.main()
