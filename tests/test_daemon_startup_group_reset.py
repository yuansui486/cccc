from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestDaemonStartupGroupReset(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        home = Path(td_ctx.__enter__())
        os.environ["CCCC_HOME"] = str(home)

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return home, cleanup

    def test_reset_stops_all_groups_and_preserves_recovery_state(self) -> None:
        from no1.daemon.group.bootstrap_actor_ops import reset_groups_for_daemon_start
        from no1.daemon.runtime_session_ops import runtime_session_path
        from no1.kernel.actors import add_actor, find_actor
        from no1.kernel.context import ContextStorage
        from no1.kernel.group import create_group, load_group
        from no1.kernel.registry import load_registry
        from no1.util.fs import atomic_write_json, read_json

        home, cleanup = self._with_home()
        try:
            registry = load_registry()
            first = create_group(registry, title="running", topic="")
            first_group = load_group(first.group_id)
            self.assertIsNotNone(first_group)
            assert first_group is not None
            add_actor(first_group, actor_id="openclaw-peer", title="OpenClaw", runtime="openclaw", runner="pty")
            add_actor(first_group, actor_id="codex-peer", title="Codex", runtime="codex", runner="headless")
            first_group.doc["running"] = True
            first_group.doc["state"] = "paused"
            first_group.save()

            second = create_group(registry, title="idle", topic="")
            second_group = load_group(second.group_id)
            self.assertIsNotNone(second_group)
            assert second_group is not None
            add_actor(second_group, actor_id="peer", title="Peer", runtime="codex", runner="pty")
            second_group.doc["running"] = False
            second_group.doc["state"] = "idle"
            second_group.save()

            pty_marker = first_group.path / "state" / "runners" / "pty" / "openclaw-peer.json"
            headless_marker = first_group.path / "state" / "runners" / "headless" / "codex-peer.json"
            atomic_write_json(pty_marker, {"kind": "pty", "pid": 123})
            atomic_write_json(headless_marker, {"kind": "headless", "status": "working"})
            startup_path = first_group.path / "state" / "actor_startups.json"
            atomic_write_json(
                startup_path,
                {
                    "v": 1,
                    "actors": {
                        "openclaw-peer": {
                            "state": "initializing",
                            "phase": "gateway",
                            "generation": 7,
                            "attempt_id": "old-attempt",
                        },
                        "failed-peer": {
                            "state": "failed",
                            "phase": "failed",
                            "generation": 3,
                            "attempt_id": "failed-attempt",
                            "error": "keep-diagnostic",
                        }
                    },
                },
                indent=2,
            )

            storage = ContextStorage(first_group)
            storage.update_agent_state("openclaw-peer", "working", active_task_id="task-1")
            agents = storage.load_agents()
            agent_state = next(item for item in agents.agents if item.id == "openclaw-peer")
            agent_state.hot.next_action = "continue"
            agent_state.warm.what_changed = "changed"
            agent_state.warm.open_loops = ["keep-loop"]
            agent_state.warm.environment_summary = "keep-environment"
            storage.save_agents(agents)

            turn_grant_path = first_group.path / "state" / "turn-grants" / "openclaw-peer.json"
            atomic_write_json(
                turn_grant_path,
                {
                    "v": 1,
                    "generation": 4,
                    "pending_attempt": {"attempt_id": "pending"},
                    "current_grant": None,
                },
                indent=2,
            )

            session_path = runtime_session_path(first.group_id, "openclaw-peer")
            session_doc = {"provider_session_id": "session-keep", "runtime": "openclaw"}
            atomic_write_json(session_path, session_doc, indent=2)
            skill_path = home / "runtime" / "openclaw" / "skills" / "actors" / "actor-hash" / "skills" / "skill" / "SKILL.md"
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text("keep-skill\n", encoding="utf-8")
            ledger_before = first_group.ledger_path.read_bytes()

            result = reset_groups_for_daemon_start(home)

            self.assertEqual(result["groups"], 2)
            self.assertEqual(result["groups_changed"], 2)
            self.assertEqual(result["actors_disabled"], 3)
            self.assertEqual(result["runner_markers_removed"], 2)
            self.assertEqual(result["openclaw_starts_cancelled"], 1)
            self.assertEqual(result["agent_states_cleared"], 1)
            self.assertEqual(result["turn_grants_invalidated"], 1)

            for group_id, actor_ids in (
                (first.group_id, ("openclaw-peer", "codex-peer")),
                (second.group_id, ("peer",)),
            ):
                group = load_group(group_id)
                self.assertIsNotNone(group)
                assert group is not None
                self.assertFalse(group.doc.get("running"))
                self.assertEqual(group.doc.get("state"), "stopped")
                for actor_id in actor_ids:
                    actor = find_actor(group, actor_id)
                    self.assertIsNotNone(actor)
                    self.assertIs(actor.get("enabled"), False)  # type: ignore[union-attr]

            self.assertFalse(pty_marker.exists())
            self.assertFalse(headless_marker.exists())
            startup = read_json(startup_path)["actors"]["openclaw-peer"]
            self.assertEqual(startup.get("state"), "stopped")
            self.assertEqual(startup.get("phase"), "stopped")
            self.assertEqual(startup.get("generation"), 8)
            self.assertNotEqual(startup.get("attempt_id"), "old-attempt")
            failed_startup = read_json(startup_path)["actors"]["failed-peer"]
            self.assertEqual(failed_startup.get("state"), "failed")
            self.assertEqual(failed_startup.get("attempt_id"), "failed-attempt")
            self.assertEqual(failed_startup.get("error"), "keep-diagnostic")

            reset_agents = ContextStorage(load_group(first.group_id)).load_agents()  # type: ignore[arg-type]
            reset_agent = next(item for item in reset_agents.agents if item.id == "openclaw-peer")
            self.assertIsNone(reset_agent.hot.active_task_id)
            self.assertEqual(reset_agent.hot.focus, "")
            self.assertEqual(reset_agent.hot.next_action, "")
            self.assertEqual(reset_agent.warm.what_changed, "")
            self.assertEqual(reset_agent.warm.open_loops, ["keep-loop"])
            self.assertEqual(reset_agent.warm.environment_summary, "keep-environment")
            grant = read_json(turn_grant_path)
            self.assertIsNone(grant.get("pending_attempt"))
            self.assertIsNone(grant.get("current_grant"))
            self.assertEqual(grant.get("invalidated_reason"), "daemon_start_stopped")

            self.assertEqual(read_json(session_path), session_doc)
            self.assertEqual(skill_path.read_text(encoding="utf-8"), "keep-skill\n")
            self.assertEqual(first_group.ledger_path.read_bytes(), ledger_before)

            group_bytes = (first_group.path / "group.yaml").read_bytes()
            startup_bytes = startup_path.read_bytes()
            second_result = reset_groups_for_daemon_start(home)
            self.assertEqual(second_result["groups_changed"], 0)
            self.assertEqual(second_result["actors_disabled"], 0)
            self.assertEqual(second_result["runner_markers_removed"], 0)
            self.assertEqual(second_result["openclaw_starts_cancelled"], 0)
            self.assertEqual(second_result["agent_states_cleared"], 0)
            self.assertEqual(second_result["turn_grants_invalidated"], 0)
            self.assertEqual((first_group.path / "group.yaml").read_bytes(), group_bytes)
            self.assertEqual(startup_path.read_bytes(), startup_bytes)
        finally:
            cleanup()

    def test_reset_failure_stops_daemon_before_computer_control_and_socket(self) -> None:
        from no1.daemon import server

        home, cleanup = self._with_home()
        try:
            paths = server.DaemonPaths(home=home)
            lock_handle = object()
            with (
                patch("no1.daemon.server.acquire_lockfile", return_value=lock_handle),
                patch("no1.daemon.server.release_lockfile") as release_lock,
                patch("no1.daemon.server.get_observability_settings", return_value={}),
                patch("no1.daemon.server._apply_observability_settings"),
                patch("no1.daemon.server._apply_space_provider_runtime_flags_from_state"),
                patch("no1.daemon.server._cleanup_stale_daemon_endpoints"),
                patch("no1.daemon.server._is_daemon_alive", return_value=False),
                patch("no1.daemon.server.im_cleanup_invalid", return_value={}),
                patch("no1.daemon.server._cleanup_stale_pty_state"),
                patch("no1.daemon.server.stop_all_openclaw_gateways"),
                patch("no1.daemon.server.reset_groups_for_daemon_start", side_effect=OSError("state locked")),
                patch("no1.daemon.server._start_daemon_computer_control_after_lock") as start_control,
                patch("no1.daemon.server.bind_server_socket") as bind_socket,
            ):
                result = server.serve_forever(paths)

            self.assertEqual(result, 1)
            release_lock.assert_called_once_with(lock_handle)
            start_control.assert_not_called()
            bind_socket.assert_not_called()
        finally:
            cleanup()

    def test_post_listen_bootstrap_only_starts_im_bridge(self) -> None:
        from no1.daemon.serve_ops import start_bootstrap_thread

        calls: list[str] = []
        thread = start_bootstrap_thread(maybe_autostart_enabled_im_bridges=lambda: calls.append("im"))
        thread.join(timeout=1.0)
        self.assertEqual(calls, ["im"])


if __name__ == "__main__":
    unittest.main()
