import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestWebActorRoutesCache(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        old_mode = os.environ.get("CCCC_WEB_MODE")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home
            if old_mode is None:
                os.environ.pop("CCCC_WEB_MODE", None)
            else:
                os.environ["CCCC_WEB_MODE"] = old_mode

        return td, cleanup

    def _client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def _create_group(self) -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        return create_group(reg, title="actor-cache-test", topic="").group_id

    def test_normal_mode_readonly_actor_list_uses_inflight_without_ttl(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            actor_list_calls = 0
            call_lock = threading.Lock()
            barrier = threading.Barrier(3)

            def fake_call_daemon(req: dict):
                nonlocal actor_list_calls
                op = str(req.get("op") or "")
                if op == "actor_list":
                    with call_lock:
                        actor_list_calls += 1
                    time.sleep(0.12)
                    return {"ok": True, "result": {"actors": [{"id": "peer-1", "title": "Peer 1"}]}}
                return {"ok": True, "result": {}}

            with patch("cccc.ports.web.app.call_daemon", side_effect=fake_call_daemon):
                with self._client() as client:
                    path = f"/api/v1/groups/{group_id}/actors"

                    def do_get() -> int:
                        barrier.wait(timeout=2)
                        resp = client.get(path)
                        return resp.status_code

                    with ThreadPoolExecutor(max_workers=2) as executor:
                        fut1 = executor.submit(do_get)
                        fut2 = executor.submit(do_get)
                        barrier.wait(timeout=2)
                        self.assertEqual(fut1.result(timeout=3), 200)
                        self.assertEqual(fut2.result(timeout=3), 200)

                    self.assertEqual(actor_list_calls, 1)

                    follow_up = client.get(path)
                    self.assertEqual(follow_up.status_code, 200)
                    self.assertEqual(actor_list_calls, 1)

                    second_follow_up = client.get(path)
                    self.assertEqual(second_follow_up.status_code, 200)
                    self.assertEqual(actor_list_calls, 2)
        finally:
            cleanup()

    def test_exhibit_mode_readonly_actor_list_uses_short_ttl(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ["CCCC_WEB_MODE"] = "exhibit"
            group_id = self._create_group()
            actor_list_calls = 0

            def fake_call_daemon(req: dict):
                nonlocal actor_list_calls
                if str(req.get("op") or "") == "actor_list":
                    actor_list_calls += 1
                    return {"ok": True, "result": {"actors": [{"id": "peer-1", "title": "Peer 1"}]}}
                return {"ok": True, "result": {}}

            with patch("cccc.ports.web.app.call_daemon", side_effect=fake_call_daemon):
                with self._client() as client:
                    path = f"/api/v1/groups/{group_id}/actors"
                    first = client.get(path)
                    second = client.get(path)
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(second.status_code, 200)
                    self.assertEqual(actor_list_calls, 1)
        finally:
            cleanup()

    def test_normal_mode_actor_write_invalidates_server_inflight(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            actor_list_calls = 0
            actor_list_lock = threading.Lock()
            first_read_release = threading.Event()

            def fake_call_daemon(req: dict):
                nonlocal actor_list_calls
                op = str(req.get("op") or "")
                if op == "actor_list":
                    with actor_list_lock:
                        actor_list_calls += 1
                        current = actor_list_calls
                    if current == 1:
                        first_read_release.wait(timeout=2)
                        return {"ok": True, "result": {"actors": [{"id": "peer-stale"}]}}
                    return {"ok": True, "result": {"actors": [{"id": "peer-fresh"}]}}
                if op == "actor_update":
                    return {"ok": True, "result": {"actor": {"id": "peer-1"}}}
                return {"ok": True, "result": {}}

            with patch("cccc.ports.web.app.call_daemon", side_effect=fake_call_daemon):
                with self._client() as client:
                    path = f"/api/v1/groups/{group_id}/actors"
                    update_path = f"/api/v1/groups/{group_id}/actors/peer-1"

                    with ThreadPoolExecutor(max_workers=1) as executor:
                        stale_future = executor.submit(client.get, path)
                        time.sleep(0.05)

                        update_resp = client.post(update_path, json={"by": "user", "title": "Peer 1"})
                        self.assertEqual(update_resp.status_code, 200)

                        fresh_resp = client.get(path)
                        self.assertEqual(fresh_resp.status_code, 200)
                        self.assertEqual(fresh_resp.json()["result"]["actors"][0]["id"], "peer-fresh")

                        first_read_release.set()
                        stale_resp = stale_future.result(timeout=3)
                        self.assertEqual(stale_resp.status_code, 200)
                        self.assertEqual(stale_resp.json()["result"]["actors"][0]["id"], "peer-stale")

                    self.assertEqual(actor_list_calls, 2)
        finally:
            cleanup()

    def test_normal_mode_group_lifecycle_write_invalidates_server_inflight(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            actor_list_calls = 0
            actor_list_lock = threading.Lock()
            first_read_release = threading.Event()

            def fake_call_daemon(req: dict):
                nonlocal actor_list_calls
                op = str(req.get("op") or "")
                if op == "actor_list":
                    with actor_list_lock:
                        actor_list_calls += 1
                        current = actor_list_calls
                    if current == 1:
                        first_read_release.wait(timeout=2)
                        return {"ok": True, "result": {"actors": [{"id": "peer-stale", "running": False}]}}
                    return {"ok": True, "result": {"actors": [{"id": "peer-fresh", "running": True}]}}
                if op == "group_start":
                    return {"ok": True, "result": {}}
                return {"ok": True, "result": {}}

            with patch("cccc.ports.web.app.call_daemon", side_effect=fake_call_daemon):
                with self._client() as client:
                    path = f"/api/v1/groups/{group_id}/actors"
                    start_path = f"/api/v1/groups/{group_id}/start?by=user"

                    with ThreadPoolExecutor(max_workers=1) as executor:
                        stale_future = executor.submit(client.get, path)
                        time.sleep(0.05)

                        start_resp = client.post(start_path)
                        self.assertEqual(start_resp.status_code, 200)

                        fresh_resp = client.get(path)
                        self.assertEqual(fresh_resp.status_code, 200)
                        self.assertTrue(bool(fresh_resp.json()["result"]["actors"][0]["running"]))

                        first_read_release.set()
                        stale_resp = stale_future.result(timeout=3)
                        self.assertEqual(stale_resp.status_code, 200)
                        self.assertFalse(bool(stale_resp.json()["result"]["actors"][0]["running"]))

                    self.assertEqual(actor_list_calls, 2)
        finally:
            cleanup()

    def test_actor_list_route_uses_daemon_effective_working_state_projection(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()

            def fake_call_daemon(req: dict):
                if str(req.get("op") or "") != "actor_list":
                    return {"ok": True, "result": {}}
                return {
                    "ok": True,
                    "result": {
                        "actors": [
                            {
                                "id": "peer-1",
                                "title": "Peer 1",
                                "runtime": "codex",
                                "runner": "pty",
                                "runner_effective": "pty",
                                "running": True,
                                "idle_seconds": 301.0,
                                "effective_working_state": "stuck",
                                "effective_working_reason": "pty_idle_timeout_with_active_task",
                                "effective_active_task_id": "T123",
                            }
                        ]
                    },
                }

            with patch("cccc.ports.web.app.call_daemon", side_effect=fake_call_daemon):
                with self._client() as client:
                    resp = client.get(f"/api/v1/groups/{group_id}/actors")
                    self.assertEqual(resp.status_code, 200)
                    actor = resp.json()["result"]["actors"][0]
                    self.assertEqual(actor["effective_working_state"], "stuck")
                    self.assertEqual(actor["effective_working_reason"], "pty_idle_timeout_with_active_task")
                    self.assertEqual(actor["effective_active_task_id"], "T123")
        finally:
            cleanup()
