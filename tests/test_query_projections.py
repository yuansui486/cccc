from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch


class TestQueryProjections(unittest.TestCase):
    def test_groups_projection_refreshes_after_group_state_change(self) -> None:
        from cccc.daemon.ops.registry_ops import handle_groups
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                gid = create_group(reg, title="proj", topic="").group_id

                first = handle_groups({})
                self.assertTrue(first.ok)
                items = (first.result or {}).get("groups", [])
                self.assertEqual(items[0]["state"], "active")

                group = load_group(gid)
                self.assertIsNotNone(group)
                group.doc["state"] = "paused"  # type: ignore[union-attr]
                time.sleep(0.001)
                group.save()  # type: ignore[union-attr]

                second = handle_groups({})
                self.assertTrue(second.ok)
                items2 = (second.result or {}).get("groups", [])
                self.assertEqual(items2[0]["state"], "paused")

        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_groups_projection_avoids_reloading_yaml_on_cache_hit(self) -> None:
        from cccc.daemon.ops.registry_ops import handle_groups
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                create_group(reg, title="proj", topic="")

                first = handle_groups({})
                self.assertTrue(first.ok)

                with patch("cccc.kernel.query_projections.load_group", side_effect=AssertionError("cache miss")):
                    second = handle_groups({})
                self.assertTrue(second.ok)
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_actor_projection_refreshes_after_actor_update(self) -> None:
        from cccc.daemon.actors.actor_ops import handle_actor_list
        from cccc.kernel.actors import add_actor
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                gid = create_group(reg, title="proj", topic="").group_id
                group = load_group(gid)
                self.assertIsNotNone(group)
                add_actor(group, actor_id="peer1", title="Peer One")  # type: ignore[arg-type]
                group.save()  # type: ignore[union-attr]

                first = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")
                self.assertTrue(first.ok)
                actors = (first.result or {}).get("actors", [])
                self.assertEqual(actors[0]["title"], "Peer One")

                group = load_group(gid)
                self.assertIsNotNone(group)
                group.doc["actors"][0]["title"] = "Peer Two"  # type: ignore[index,union-attr]
                time.sleep(0.001)
                group.save()  # type: ignore[union-attr]

                second = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")
                self.assertTrue(second.ok)
                actors2 = (second.result or {}).get("actors", [])
                self.assertEqual(actors2[0]["title"], "Peer Two")
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_actor_projection_avoids_rebuilding_on_cache_hit(self) -> None:
        from cccc.daemon.actors.actor_ops import handle_actor_list
        from cccc.kernel.actors import add_actor
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                gid = create_group(reg, title="proj", topic="").group_id
                group = load_group(gid)
                self.assertIsNotNone(group)
                add_actor(group, actor_id="peer1", title="Peer One")  # type: ignore[arg-type]
                group.save()  # type: ignore[union-attr]

                first = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")
                self.assertTrue(first.ok)

                with patch("cccc.kernel.query_projections.list_actors", side_effect=AssertionError("cache miss")):
                    second = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")
                self.assertTrue(second.ok)
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_actor_projection_marks_running_pty_without_prompt_as_waiting(self) -> None:
        from cccc.daemon.actors.actor_ops import handle_actor_list
        from cccc.kernel.actors import add_actor
        from cccc.kernel.context import ContextStorage
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                gid = create_group(reg, title="proj", topic="").group_id
                group = load_group(gid)
                self.assertIsNotNone(group)
                add_actor(group, actor_id="peer1", title="Peer One")  # type: ignore[arg-type]
                group.save()  # type: ignore[union-attr]

                storage = ContextStorage(group)  # type: ignore[arg-type]
                storage.update_agent_state("peer1", "Implement auth", active_task_id="T100")

                with patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.actor_running", return_value=True), patch(
                    "cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.idle_seconds",
                    return_value=12.0,
                ):
                    resp = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")

                self.assertTrue(resp.ok, getattr(resp, "error", None))
                actors = (resp.result or {}).get("actors", [])
                self.assertEqual(actors[0]["effective_working_state"], "waiting")
                self.assertEqual(actors[0]["effective_working_reason"], "pty_no_prompt_waiting")
                self.assertEqual(actors[0]["effective_active_task_id"], "T100")

        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_actor_projection_prefers_terminal_prompt_over_stale_active_task_for_codex(self) -> None:
        from cccc.daemon.actors.actor_ops import handle_actor_list
        from cccc.kernel.actors import add_actor
        from cccc.kernel.context import ContextStorage
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                gid = create_group(reg, title="proj", topic="").group_id
                group = load_group(gid)
                self.assertIsNotNone(group)
                add_actor(group, actor_id="peer1", title="Peer One", runtime="codex", runner="pty")  # type: ignore[arg-type]
                group.save()  # type: ignore[union-attr]

                storage = ContextStorage(group)  # type: ignore[arg-type]
                storage.update_agent_state("peer1", "Implement auth", active_task_id="T100")

                with (
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.actor_running", return_value=True),
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.idle_seconds", return_value=12.0),
                    patch(
                        "cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.tail_output",
                        return_value="› Run /review on my current changes\n".encode("utf-8"),
                    ),
                ):
                    resp = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")

                self.assertTrue(resp.ok, getattr(resp, "error", None))
                actors = (resp.result or {}).get("actors", [])
                self.assertEqual(actors[0]["effective_working_state"], "idle")
                self.assertEqual(actors[0]["effective_working_reason"], "pty_terminal_prompt_visible")

        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_actor_projection_marks_recent_running_pty_without_prompt_as_working(self) -> None:
        from cccc.daemon.actors.actor_ops import handle_actor_list
        from cccc.kernel.actors import add_actor
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                gid = create_group(reg, title="proj", topic="").group_id
                group = load_group(gid)
                self.assertIsNotNone(group)
                add_actor(group, actor_id="peer1", title="Peer One", runtime="codex", runner="pty")  # type: ignore[arg-type]
                group.save()  # type: ignore[union-attr]

                with (
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.actor_running", return_value=True),
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.idle_seconds", return_value=1.2),
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.tail_output", return_value=b"still running"),
                ):
                    resp = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")

                self.assertTrue(resp.ok, getattr(resp, "error", None))
                actors = (resp.result or {}).get("actors", [])
                self.assertEqual(actors[0]["effective_working_state"], "working")
                self.assertEqual(actors[0]["effective_working_reason"], "pty_no_prompt_recent_output")

        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_actor_projection_does_not_mark_fresh_running_pty_without_output_as_working(self) -> None:
        from cccc.daemon.actors.actor_ops import handle_actor_list
        from cccc.kernel.actors import add_actor
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                reg = load_registry()
                gid = create_group(reg, title="proj", topic="").group_id
                group = load_group(gid)
                self.assertIsNotNone(group)
                add_actor(group, actor_id="peer1", title="Peer One", runtime="codex", runner="pty")  # type: ignore[arg-type]
                group.save()  # type: ignore[union-attr]

                with (
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.actor_running", return_value=True),
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.idle_seconds", return_value=0.4),
                    patch("cccc.daemon.actors.actor_ops.pty_runner.SUPERVISOR.tail_output", return_value=b""),
                ):
                    resp = handle_actor_list({"group_id": gid, "include_unread": False}, effective_runner_kind=lambda _: "pty")

                self.assertTrue(resp.ok, getattr(resp, "error", None))
                actors = (resp.result or {}).get("actors", [])
                self.assertEqual(actors[0]["effective_working_state"], "waiting")
                self.assertEqual(actors[0]["effective_working_reason"], "pty_no_prompt_waiting")

        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home
