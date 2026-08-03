from __future__ import annotations

import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from no1.ports.web.routes.computer_control import create_routers
from no1.ports.web.schemas import RouteContext


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
            if method in getattr(route, "methods", set()) and str(
                getattr(route, "path", "")
            ).endswith(suffix):
                return route.endpoint
    raise AssertionError(f"route not found: {method} {suffix}")


class TestComputerControlWebOwnerSurface(unittest.TestCase):
    def test_non_windows_availability_remains_readable_and_other_routes_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch(
            "no1.ports.web.routes.computer_control._service",
            return_value=SimpleNamespace(),
        ), patch(
            "no1.ports.web.routes.computer_control.require_admin",
            return_value=object(),
        ), patch(
            "no1.computer_control.platform_support._platform_name",
            return_value="darwin",
        ):
            app = FastAPI()
            for router in create_routers(_context(Path(td))):
                app.include_router(router)
            with TestClient(app) as client:
                availability = client.get("/api/v1/computer-control/availability")
                setup = client.get("/api/v1/computer-control/setup/status")

        self.assertEqual(availability.status_code, 200)
        self.assertEqual(
            availability.json()["result"],
            {
                "supported": False,
                "platform": "darwin",
                "reason": "windows_only",
            },
        )
        self.assertEqual(setup.status_code, 409)
        self.assertEqual(
            setup.json()["detail"]["code"],
            "computer_control_platform_unsupported",
        )

    def test_web_routes_have_no_passive_workflow_or_request_writers(self) -> None:
        from no1.ports.web.routes.computer_control import create_routers

        source = inspect.getsource(create_routers)
        for forbidden in (
            "service.store.create(",
            "service.store.update(",
            "service.store.publish(",
            "service.store.trust(",
            "service.store.archive(",
            "service.store.duplicate(",
            "service.store.delete(",
            "service.store.update_settings(",
            "service.store.update_triggers(",
            "service.store.decide_proposal(",
            "service.requests.append(",
            "service.requests.update(",
        ):
            self.assertNotIn(forbidden, source)

    def test_web_lifespan_does_not_start_or_stop_passive_scheduler(self) -> None:
        async def exercise(home: Path, service: SimpleNamespace) -> None:
            from no1.ports.web.app import create_app

            with patch("no1.ports.web.app.ensure_home", return_value=home), patch(
                "no1.computer_control.services.get_services", return_value=service
            ), patch("no1.ports.web.app._apply_web_logging"), patch(
                "no1.ports.web.app._close_web_logging"
            ), patch("no1.ports.web.app.clear_web_runtime_state"):
                app = create_app()
                async with app.router.lifespan_context(app):
                    pass

        with tempfile.TemporaryDirectory() as td:
            scheduler = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
            service = SimpleNamespace(scheduler=scheduler)
            asyncio.run(exercise(Path(td), service))
            scheduler.start.assert_not_awaited()
            scheduler.stop.assert_not_awaited()

    def test_run_mutators_use_daemon_and_never_passive_runner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            runner = Mock()
            runner.get.return_value = {"run_id": "run-1", "actor_id": "foreman"}
            service = SimpleNamespace(runner=runner)
            daemon_calls = []

            def fake_call_daemon(request, **kwargs):
                del kwargs
                daemon_calls.append(request["args"])
                return {
                    "ok": True,
                    "result": {"result": {"run_id": "run-1", "status": "ok"}},
                }

            with patch(
                "no1.ports.web.routes.computer_control._service",
                return_value=service,
            ), patch(
                "no1.ports.web.routes.computer_control.call_daemon",
                side_effect=fake_call_daemon,
            ), patch(
                "no1.ports.web.routes.computer_control.audit"
            ), patch(
                "no1.ports.web.routes.computer_control._emit"
            ):
                routers = create_routers(_context(home))
                verify = _endpoint(routers, "POST", "/verify")
                recover = _endpoint(routers, "POST", "/recovery/resolve")
                approve = _endpoint(routers, "POST", "/approvals/{node_id}")

                asyncio.run(
                    verify(
                        "g-test",
                        "run-1",
                        {"passed": True, "summary": "done", "evidence_ids": []},
                    )
                )
                asyncio.run(
                    recover(
                        "g-test",
                        "run-1",
                        {"recovery_id": "recovery-1", "resolution": "retry"},
                    )
                )
                asyncio.run(approve("g-test", "run-1", "node-1", True))

            runner.verify.assert_not_called()
            runner.submit_recovery.assert_not_called()
            runner.decide_approval.assert_not_called()
            self.assertEqual(
                [call["action"] for call in daemon_calls],
                ["verify", "recover", "approve"],
            )
            self.assertTrue(
                all(call["command"] == "run" for call in daemon_calls)
            )


if __name__ == "__main__":
    unittest.main()
