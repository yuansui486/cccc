import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestWebGroupsLocalProjection(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return cleanup

    def _client(self) -> TestClient:
        from no1.ports.web.app import create_app

        return TestClient(create_app())

    def test_groups_route_reads_local_projection_without_daemon(self) -> None:
        cleanup = self._with_home()
        try:
            with patch(
                "no1.ports.web.routes.groups._read_groups_local",
                return_value={"ok": True, "result": {"groups": [{"group_id": "g1", "title": "T"}], "registry_health": {}}},
            ), patch("no1.ports.web.app.call_daemon", side_effect=AssertionError("daemon should not be called")):
                with self._client() as client:
                    resp = client.get("/api/v1/groups")
                    self.assertEqual(resp.status_code, 200)
                    data = resp.json()
                    self.assertEqual(data["result"]["groups"][0]["group_id"], "g1")
        finally:
            cleanup()

    def test_groups_route_marks_headless_codex_group_running_from_local_supervisor(self) -> None:
        cleanup = self._with_home()
        try:
            from no1.kernel.actors import add_actor
            from no1.kernel.group import create_group, load_group
            from no1.kernel.registry import load_registry
            from no1.daemon.runner_state_ops import headless_state_path
            from no1.util.fs import atomic_write_json

            reg = load_registry()
            gid = create_group(reg, title="codex-running", topic="").group_id
            group = load_group(gid)
            self.assertIsNotNone(group)
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="headless")  # type: ignore[arg-type]
            group.save()  # type: ignore[union-attr]

            atomic_write_json(
                headless_state_path(gid, "peer1"),
                {
                    "v": 1,
                    "kind": "headless",
                    "runtime": "codex",
                    "group_id": gid,
                    "actor_id": "peer1",
                    "pid": os.getpid(),
                    "status": "idle",
                },
            )
            with self._client() as client:
                resp = client.get("/api/v1/groups")

            self.assertEqual(resp.status_code, 200)
            groups = resp.json()["result"]["groups"]
            match = next(item for item in groups if str(item.get("group_id") or "") == gid)
            self.assertTrue(bool(match.get("running")))
            self.assertTrue(bool(((match.get("runtime_status") or {}).get("runtime_running"))))
            control_state = match.get("control_state") or {}
            self.assertEqual(control_state.get("status_key"), "run")
            self.assertEqual(control_state.get("primary_action"), "pause")
            self.assertTrue(bool(control_state.get("can_pause")))
        finally:
            cleanup()

    def test_group_show_includes_canonical_control_state(self) -> None:
        cleanup = self._with_home()
        try:
            from no1.kernel.actors import add_actor
            from no1.kernel.group import create_group, load_group
            from no1.kernel.registry import load_registry

            reg = load_registry()
            gid = create_group(reg, title="control-state", topic="").group_id
            group = load_group(gid)
            self.assertIsNotNone(group)
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="headless")  # type: ignore[arg-type]
            group.save()  # type: ignore[union-attr]

            with self._client() as client:
                resp = client.get(f"/api/v1/groups/{gid}")

            self.assertEqual(resp.status_code, 200)
            doc = resp.json()["result"]["group"]
            control_state = doc.get("control_state") or {}
            self.assertEqual(control_state.get("status_key"), "stop")
            self.assertEqual(control_state.get("primary_action"), "start")
            self.assertTrue(bool(control_state.get("can_start")))
            self.assertFalse(bool(control_state.get("can_pause")))
            self.assertTrue(bool(control_state.get("can_stop")))
            self.assertEqual(control_state.get("actor_count"), 1)
        finally:
            cleanup()

    def test_group_control_state_route_reads_uncached_canonical_state(self) -> None:
        cleanup = self._with_home()
        try:
            from no1.kernel.actors import add_actor
            from no1.kernel.group import create_group, load_group
            from no1.kernel.registry import load_registry

            reg = load_registry()
            gid = create_group(reg, title="control-state-route", topic="").group_id
            group = load_group(gid)
            self.assertIsNotNone(group)
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="headless")  # type: ignore[arg-type]
            group.save()  # type: ignore[union-attr]

            with self._client() as client:
                resp = client.get(f"/api/v1/groups/{gid}/control_state?fresh=1")

            self.assertEqual(resp.status_code, 200)
            result = resp.json()["result"]
            self.assertEqual(result.get("group_id"), gid)
            control_state = result.get("control_state") or {}
            self.assertEqual(control_state.get("status_key"), "stop")
            self.assertEqual(control_state.get("primary_action"), "start")
            self.assertTrue(bool(control_state.get("can_start")))
        finally:
            cleanup()

    def test_groups_route_marks_web_model_marker_running(self) -> None:
        cleanup = self._with_home()
        try:
            from no1.daemon.runner_state_ops import write_headless_state
            from no1.kernel.actors import add_actor
            from no1.kernel.group import create_group, load_group
            from no1.kernel.registry import load_registry

            reg = load_registry()
            gid = create_group(reg, title="web-model-running", topic="").group_id
            group = load_group(gid)
            self.assertIsNotNone(group)
            add_actor(group, actor_id="webpeer", title="Web Peer", runtime="web_model", runner="headless")  # type: ignore[arg-type]
            group.save()  # type: ignore[union-attr]
            write_headless_state(gid, "webpeer")

            with self._client() as client:
                resp = client.get("/api/v1/groups")

            self.assertEqual(resp.status_code, 200)
            groups = resp.json()["result"]["groups"]
            match = next(item for item in groups if str(item.get("group_id") or "") == gid)
            self.assertTrue(bool(match.get("running")))
            self.assertTrue(bool(((match.get("runtime_status") or {}).get("runtime_running"))))
        finally:
            cleanup()

    def test_groups_route_prefers_group_level_supervisor_running_signal(self) -> None:
        cleanup = self._with_home()
        try:
            from no1.kernel.group import create_group
            from no1.kernel.registry import load_registry

            reg = load_registry()
            gid = create_group(reg, title="group-running-supervisor", topic="").group_id

            with patch("no1.ports.web.routes.groups.codex_app_supervisor.group_running", return_value=True):
                with self._client() as client:
                    resp = client.get("/api/v1/groups")

            self.assertEqual(resp.status_code, 200)
            groups = resp.json()["result"]["groups"]
            match = next(item for item in groups if str(item.get("group_id") or "") == gid)
            self.assertTrue(bool(match.get("running")))
            self.assertTrue(bool(((match.get("runtime_status") or {}).get("runtime_running"))))
        finally:
            cleanup()

    def test_groups_route_treats_codex_pty_actor_as_internal_headless_on_restart(self) -> None:
        cleanup = self._with_home()
        try:
            from no1.kernel.actors import add_actor
            from no1.kernel.group import create_group, load_group
            from no1.kernel.registry import load_registry
            from no1.daemon.runner_state_ops import headless_state_path
            from no1.util.fs import atomic_write_json

            reg = load_registry()
            gid = create_group(reg, title="codex-pty-restarts-running", topic="").group_id
            group = load_group(gid)
            self.assertIsNotNone(group)
            add_actor(group, actor_id="peer1", title="Peer 1", runtime="codex", runner="pty")  # type: ignore[arg-type]
            group.save()  # type: ignore[union-attr]

            atomic_write_json(
                headless_state_path(gid, "peer1"),
                {
                    "v": 1,
                    "kind": "headless",
                    "runtime": "codex",
                    "group_id": gid,
                    "actor_id": "peer1",
                    "pid": os.getpid(),
                    "status": "idle",
                },
            )
            with self._client() as client:
                resp = client.get("/api/v1/groups")

            self.assertEqual(resp.status_code, 200)
            groups = resp.json()["result"]["groups"]
            match = next(item for item in groups if str(item.get("group_id") or "") == gid)
            self.assertTrue(bool(match.get("running")))
        finally:
            cleanup()

    def test_startup_reset_projects_group_as_stopped(self) -> None:
        cleanup = self._with_home()
        try:
            from no1.daemon.group.bootstrap_actor_ops import reset_groups_for_daemon_start
            from no1.daemon.runner_state_ops import write_headless_state
            from no1.kernel.actors import add_actor
            from no1.kernel.group import create_group, load_group
            from no1.kernel.registry import load_registry

            reg = load_registry()
            gid = create_group(reg, title="startup-reset", topic="").group_id
            group = load_group(gid)
            self.assertIsNotNone(group)
            assert group is not None
            add_actor(group, actor_id="webpeer", title="Web Peer", runtime="web_model", runner="headless")
            group.doc["running"] = True
            group.doc["state"] = "active"
            group.save()
            write_headless_state(gid, "webpeer")

            reset_groups_for_daemon_start(Path(os.environ["CCCC_HOME"]))

            with self._client() as client:
                response = client.get(f"/api/v1/groups/{gid}")

            self.assertEqual(response.status_code, 200)
            doc = response.json()["result"]["group"]
            self.assertFalse(doc.get("running"))
            runtime_status = doc.get("runtime_status") or {}
            self.assertFalse(runtime_status.get("runtime_running"))
            self.assertFalse(runtime_status.get("booting"))
            control_state = doc.get("control_state") or {}
            self.assertEqual(control_state.get("status_key"), "stop")
            self.assertEqual(control_state.get("primary_action"), "start")
        finally:
            cleanup()
