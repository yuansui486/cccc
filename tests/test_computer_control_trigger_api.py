import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from no1.computer_control.models import WorkflowDefinition
from no1.computer_control.storage import WorkflowStore
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

            with patch("no1.computer_control.storage.load_group", return_value=group), patch(
                "no1.ports.web.routes.computer_control._service", return_value=service
            ), patch("no1.ports.web.routes.computer_control.audit"), patch(
                "no1.ports.web.routes.computer_control._emit"
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
                self.assertTrue(scheduler.calls)

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
