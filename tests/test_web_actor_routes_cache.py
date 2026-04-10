import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, mock_open, patch

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

    def _local_call_daemon(self, req: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        request = DaemonRequest.model_validate(req)
        resp, _ = handle_request(request)
        return resp.model_dump(exclude_none=True)

    def _create_group(self) -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        return create_group(reg, title="actor-cache-test", topic="").group_id

    def _add_actor(self, group_id: str, actor_id: str = "peer-1", *, runtime: str = "codex") -> None:
        created = self._local_call_daemon(
            {
                "op": "actor_add",
                "args": {
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "title": actor_id,
                    "runtime": runtime,
                    "runner": "pty",
                    "command": [],
                    "env": {},
                    "by": "user",
                },
            }
        )
        self.assertTrue(bool(created.get("ok")), created)

    def test_pid_matches_actor_context_requires_exact_proc_environ_match(self) -> None:
        from cccc.ports.web.routes.actors import _pid_matches_actor_context

        environ_text = b"CCCC_GROUP_ID=group-1\0CCCC_ACTOR_ID=peer-10\0"
        with patch("builtins.open", mock_open(read_data=environ_text)), patch(
            "cccc.ports.web.routes.actors.subprocess.run",
            return_value=MagicMock(stdout=""),
        ):
            self.assertFalse(_pid_matches_actor_context(43210, group_id="group-1", actor_id="peer-1"))

    def test_pid_matches_actor_context_requires_exact_ps_match(self) -> None:
        from cccc.ports.web.routes.actors import _pid_matches_actor_context

        with patch("builtins.open", side_effect=OSError("proc unavailable")), patch(
            "cccc.ports.web.routes.actors.subprocess.run",
            return_value=MagicMock(stdout="CCCC_GROUP_ID=group-1 CCCC_ACTOR_ID=peer-10 /bin/sh"),
        ):
            self.assertFalse(_pid_matches_actor_context(43210, group_id="group-1", actor_id="peer-1"))

        with patch("builtins.open", side_effect=OSError("proc unavailable")), patch(
            "cccc.ports.web.routes.actors.subprocess.run",
            return_value=MagicMock(stdout="CCCC_GROUP_ID=group-1 CCCC_ACTOR_ID=peer-1 /bin/sh"),
        ):
            self.assertTrue(_pid_matches_actor_context(43210, group_id="group-1", actor_id="peer-1"))

    def test_normal_mode_readonly_actor_list_uses_inflight_without_ttl(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            actor_list_reads = 0
            call_lock = threading.Lock()
            barrier = threading.Barrier(3)

            def fake_read_actor_list_local(group_id: str, *, include_unread: bool):
                nonlocal actor_list_reads
                self.assertFalse(include_unread)
                self.assertTrue(group_id)
                with call_lock:
                    actor_list_reads += 1
                time.sleep(0.12)
                return {"ok": True, "result": {"actors": [{"id": "peer-1", "title": "Peer 1"}]}}

            with patch("cccc.ports.web.routes.actors._read_actor_list_local", side_effect=fake_read_actor_list_local):
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

                    self.assertEqual(actor_list_reads, 1)

                    follow_up = client.get(path)
                    self.assertEqual(follow_up.status_code, 200)
                    self.assertEqual(actor_list_reads, 2)

                    second_follow_up = client.get(path)
                    self.assertEqual(second_follow_up.status_code, 200)
                    self.assertEqual(actor_list_reads, 2)
        finally:
            cleanup()

    def test_exhibit_mode_readonly_actor_list_uses_short_ttl(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ["CCCC_WEB_MODE"] = "exhibit"
            group_id = self._create_group()
            actor_list_reads = 0

            def fake_read_actor_list_local(group_id: str, *, include_unread: bool):
                nonlocal actor_list_reads
                self.assertFalse(include_unread)
                self.assertTrue(group_id)
                actor_list_reads += 1
                return {"ok": True, "result": {"actors": [{"id": "peer-1", "title": "Peer 1"}]}}

            with patch("cccc.ports.web.routes.actors._read_actor_list_local", side_effect=fake_read_actor_list_local):
                with self._client() as client:
                    path = f"/api/v1/groups/{group_id}/actors"
                    first = client.get(path)
                    second = client.get(path)
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(second.status_code, 200)
                    self.assertEqual(actor_list_reads, 1)
        finally:
            cleanup()

    def test_normal_mode_actor_write_invalidates_server_inflight(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            self._add_actor(group_id, runtime="claude")
            actor_list_reads = 0
            actor_list_lock = threading.Lock()
            first_read_release = threading.Event()

            def fake_read_actor_list_local(group_id: str, *, include_unread: bool):
                nonlocal actor_list_reads
                self.assertFalse(include_unread)
                with actor_list_lock:
                    actor_list_reads += 1
                    current = actor_list_reads
                if current == 1:
                    first_read_release.wait(timeout=2)
                    return {"ok": True, "result": {"actors": [{"id": "peer-1", "title": "Peer stale"}]}}
                return {"ok": True, "result": {"actors": [{"id": "peer-1", "title": "Peer fresh"}]}}

            with patch("cccc.ports.web.app.call_daemon", side_effect=self._local_call_daemon), patch(
                "cccc.ports.web.routes.actors._read_actor_list_local",
                side_effect=fake_read_actor_list_local,
            ):
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
                        self.assertEqual(fresh_resp.json()["result"]["actors"][0]["title"], "Peer fresh")

                        first_read_release.set()
                        stale_resp = stale_future.result(timeout=3)
                        self.assertEqual(stale_resp.status_code, 200)
                        self.assertEqual(stale_resp.json()["result"]["actors"][0]["title"], "Peer stale")

                    self.assertEqual(actor_list_reads, 2)
        finally:
            cleanup()

    def test_normal_mode_group_lifecycle_write_invalidates_server_inflight(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            actor_list_reads = 0
            actor_list_lock = threading.Lock()
            first_read_release = threading.Event()

            def fake_read_actor_list_local(group_id: str, *, include_unread: bool):
                nonlocal actor_list_reads
                self.assertFalse(include_unread)
                with actor_list_lock:
                    actor_list_reads += 1
                    current = actor_list_reads
                if current == 1:
                    first_read_release.wait(timeout=2)
                    return {"ok": True, "result": {"actors": [{"id": "peer-1", "running": False}]}}
                return {"ok": True, "result": {"actors": [{"id": "peer-1", "running": True}]}}

            def fake_call_daemon(req: dict):
                if str(req.get("op") or "") == "group_start":
                    return {"ok": True, "result": {}}
                return self._local_call_daemon(req)

            with patch("cccc.ports.web.app.call_daemon", side_effect=fake_call_daemon), patch(
                "cccc.ports.web.routes.actors._read_actor_list_local",
                side_effect=fake_read_actor_list_local,
            ):
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

                    self.assertEqual(actor_list_reads, 2)
        finally:
            cleanup()

    def test_group_start_invalidates_actor_cache_after_stale_read_finishes(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            actor_list_reads = 0
            actor_list_lock = threading.Lock()
            first_read_release = threading.Event()

            def fake_read_actor_list_local(group_id: str, *, include_unread: bool):
                nonlocal actor_list_reads
                self.assertFalse(include_unread)
                with actor_list_lock:
                    actor_list_reads += 1
                    current = actor_list_reads
                if current == 1:
                    first_read_release.wait(timeout=2)
                    return {"ok": True, "result": {"actors": [{"id": "peer-1", "running": False}]}}
                return {"ok": True, "result": {"actors": [{"id": "peer-1", "running": True}]}}

            def fake_call_daemon(req: dict):
                if str(req.get("op") or "") == "group_start":
                    return {"ok": True, "result": {}}
                return self._local_call_daemon(req)

            with patch("cccc.ports.web.app.call_daemon", side_effect=fake_call_daemon), patch(
                "cccc.ports.web.routes.actors._read_actor_list_local",
                side_effect=fake_read_actor_list_local,
            ):
                with self._client() as client:
                    path = f"/api/v1/groups/{group_id}/actors"
                    start_path = f"/api/v1/groups/{group_id}/start?by=user"

                    with ThreadPoolExecutor(max_workers=1) as executor:
                        stale_future = executor.submit(client.get, path)
                        time.sleep(0.05)

                        start_resp = client.post(start_path)
                        self.assertEqual(start_resp.status_code, 200)

                        first_read_release.set()
                        stale_resp = stale_future.result(timeout=3)
                        self.assertEqual(stale_resp.status_code, 200)
                        self.assertFalse(bool(stale_resp.json()["result"]["actors"][0]["running"]))

                    fresh_resp = client.get(path)
                    self.assertEqual(fresh_resp.status_code, 200)
                    self.assertTrue(bool(fresh_resp.json()["result"]["actors"][0]["running"]))
                    self.assertEqual(actor_list_reads, 2)
        finally:
            cleanup()

    def test_actor_list_route_uses_local_effective_working_state_projection(self) -> None:
        _, cleanup = self._with_home()
        try:
            from cccc.kernel.context import AgentState, AgentStateHot, AgentsData

            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            self._add_actor(group_id, runtime="claude")

            agent_state = AgentsData(agents=[AgentState(id="peer-1", hot=AgentStateHot(active_task_id="T123"))])

            with patch("cccc.ports.web.routes.actors.ContextStorage.load_agents", return_value=agent_state), patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.actor_running",
                return_value=True,
            ), patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.idle_seconds",
                return_value=301.0,
            ), patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.tail_output",
                return_value=b"",
            ):
                with self._client() as client:
                    resp = client.get(f"/api/v1/groups/{group_id}/actors")
                    self.assertEqual(resp.status_code, 200)
                    actor = resp.json()["result"]["actors"][0]
                    self.assertEqual(actor["effective_working_state"], "stuck")
                    self.assertEqual(actor["effective_working_reason"], "pty_no_prompt_stuck")
                    self.assertEqual(actor["effective_active_task_id"], "T123")
        finally:
            cleanup()

    def test_actor_list_route_falls_back_to_pty_state_file_for_running_status(self) -> None:
        _, cleanup = self._with_home()
        try:
            from cccc.daemon.runner_state_ops import write_pty_state

            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            self._add_actor(group_id, runtime="claude")
            write_pty_state(group_id, "peer-1", pid=43210)

            with patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.actor_running",
                return_value=False,
            ), patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.idle_seconds",
                return_value=None,
            ), patch(
                "cccc.ports.web.routes.actors.pid_is_alive",
                return_value=True,
            ), patch(
                "cccc.ports.web.routes.actors._pid_matches_actor_context",
                return_value=True,
            ):
                with self._client() as client:
                    resp = client.get(f"/api/v1/groups/{group_id}/actors")

            self.assertEqual(resp.status_code, 200)
            actor = resp.json()["result"]["actors"][0]
            self.assertTrue(bool(actor["running"]))
            self.assertEqual(actor["effective_working_state"], "waiting")
            self.assertEqual(actor["effective_working_reason"], "pty_running_state_unknown")
        finally:
            cleanup()

    def test_actor_list_route_ignores_stale_pty_state_file_when_supervisor_is_not_running(self) -> None:
        _, cleanup = self._with_home()
        try:
            from cccc.daemon.runner_state_ops import pty_state_path, write_pty_state

            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            self._add_actor(group_id, runtime="claude")
            write_pty_state(group_id, "peer-1", pid=43210)
            state_path = pty_state_path(group_id, "peer-1")
            state_doc = json.loads(state_path.read_text(encoding="utf-8"))
            state_doc["pid"] = 43210
            state_path.write_text(json.dumps(state_doc), encoding="utf-8")

            with patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.actor_running",
                return_value=False,
            ), patch(
                "cccc.ports.web.routes.actors.pid_is_alive",
                return_value=True,
            ), patch(
                "cccc.ports.web.routes.actors._pid_matches_actor_context",
                return_value=False,
            ):
                with self._client() as client:
                    resp = client.get(f"/api/v1/groups/{group_id}/actors")

            self.assertEqual(resp.status_code, 200)
            actor = resp.json()["result"]["actors"][0]
            self.assertFalse(bool(actor["running"]))
            self.assertEqual(actor["effective_working_state"], "stopped")
            self.assertEqual(actor["effective_working_reason"], "runner_not_running")
        finally:
            cleanup()

    def test_actor_list_route_reads_codex_headless_state_file(self) -> None:
        _, cleanup = self._with_home()
        try:
            from cccc.daemon.runner_state_ops import headless_state_path
            from cccc.util.fs import atomic_write_json

            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            created = self._local_call_daemon(
                {
                    "op": "actor_add",
                    "args": {
                        "group_id": group_id,
                        "actor_id": "peer-1",
                        "title": "peer-1",
                        "runtime": "codex",
                        "runner": "headless",
                        "command": [],
                        "env": {},
                        "by": "user",
                    },
                }
            )
            self.assertTrue(bool(created.get("ok")), created)

            atomic_write_json(
                headless_state_path(group_id, "peer-1"),
                {
                    "v": 1,
                    "kind": "headless",
                    "runtime": "codex",
                    "group_id": group_id,
                    "actor_id": "peer-1",
                    "pid": os.getpid(),
                    "status": "working",
                    "current_task_id": "turn-1",
                    "updated_at": "2026-04-02T10:00:00Z",
                },
            )

            with self._client() as client:
                resp = client.get(f"/api/v1/groups/{group_id}/actors")

            self.assertEqual(resp.status_code, 200)
            actor = resp.json()["result"]["actors"][0]
            self.assertTrue(bool(actor["running"]))
            self.assertEqual(actor["runner_effective"], "headless")
            self.assertEqual(actor["effective_working_state"], "working")
            self.assertEqual(actor["effective_working_reason"], "headless_working")
            self.assertEqual(actor["effective_active_task_id"], "turn-1")
        finally:
            cleanup()

    def test_actor_list_route_codex_pty_prefers_headless_state_file(self) -> None:
        _, cleanup = self._with_home()
        try:
            from cccc.daemon.runner_state_ops import headless_state_path
            from cccc.util.fs import atomic_write_json

            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            self._add_actor(group_id, runtime="codex")

            atomic_write_json(
                headless_state_path(group_id, "peer-1"),
                {
                    "v": 1,
                    "kind": "headless",
                    "runtime": "codex",
                    "group_id": group_id,
                    "actor_id": "peer-1",
                    "pid": os.getpid(),
                    "status": "working",
                },
            )

            with patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.actor_running",
                return_value=True,
            ), patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.idle_seconds",
                return_value=12.0,
            ), patch(
                "cccc.ports.web.routes.actors.pty_runner.SUPERVISOR.tail_output",
                return_value=b"",
            ):
                with self._client() as client:
                    resp = client.get(f"/api/v1/groups/{group_id}/actors")

            self.assertEqual(resp.status_code, 200)
            actor = resp.json()["result"]["actors"][0]
            self.assertTrue(bool(actor["running"]))
            self.assertEqual(actor["runner_effective"], "headless")
            self.assertEqual(actor["effective_working_state"], "working")
            self.assertEqual(actor["effective_working_reason"], "headless_working")
        finally:
            cleanup()

    def test_normal_mode_actor_list_include_unread_uses_inflight_without_ttl(self) -> None:
        _, cleanup = self._with_home()
        try:
            os.environ.pop("CCCC_WEB_MODE", None)
            group_id = self._create_group()
            actor_list_reads = 0
            call_lock = threading.Lock()
            barrier = threading.Barrier(3)

            def fake_read_actor_list_local(group_id: str, *, include_unread: bool):
                nonlocal actor_list_reads
                self.assertTrue(include_unread)
                self.assertTrue(group_id)
                with call_lock:
                    actor_list_reads += 1
                time.sleep(0.12)
                return {"ok": True, "result": {"actors": [{"id": "peer-1", "unread_count": 2}]}}

            with patch("cccc.ports.web.routes.actors._read_actor_list_local", side_effect=fake_read_actor_list_local):
                with self._client() as client:
                    path = f"/api/v1/groups/{group_id}/actors?include_unread=1"

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

                    self.assertEqual(actor_list_reads, 1)

                    follow_up = client.get(path)
                    self.assertEqual(follow_up.status_code, 200)
                    self.assertEqual(actor_list_reads, 2)

                    second_follow_up = client.get(path)
                    self.assertEqual(second_follow_up.status_code, 200)
                    self.assertEqual(actor_list_reads, 2)
        finally:
            cleanup()
