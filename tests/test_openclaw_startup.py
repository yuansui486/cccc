import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


class TestOpenClawStartup(unittest.TestCase):
    def setUp(self) -> None:
        from no1.daemon.openclaw_startup import shutdown_openclaw_startup_workers

        shutdown_openclaw_startup_workers()
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"ONECOLLEAGUE_HOME": str(self.home)}, clear=False)
        self.env.start()
        group_path = self.home / "groups" / "g-test"
        (group_path / "state").mkdir(parents=True)
        (group_path / "context").mkdir()
        (group_path / "scopes").mkdir()
        (group_path / "group.yaml").write_text(
            yaml.safe_dump(
                {
                    "v": 1,
                    "group_id": "g-test",
                    "running": True,
                    "state": "active",
                    "actors": [
                        {
                            "v": 1,
                            "id": "actor-a",
                            "enabled": True,
                            "runtime": "openclaw",
                            "runner": "pty",
                            "command": ["openclaw", "tui"],
                            "env": {},
                            "runtime_options": {},
                        }
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        from no1.daemon.openclaw_startup import shutdown_openclaw_startup_workers

        shutdown_openclaw_startup_workers()
        self.env.stop()
        self.temp.cleanup()

    def _wait_for_state(self, expected: str) -> dict:
        from no1.daemon.openclaw_startup import read_openclaw_startup

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            row = read_openclaw_startup("g-test", "actor-a")
            if row.get("state") == expected:
                return row
            time.sleep(0.01)
        self.fail(f"startup state did not become {expected}")

    def test_background_start_persists_progress_and_success(self) -> None:
        from no1.daemon.openclaw_startup import queue_openclaw_actor_start, start_openclaw_startup_workers

        phases = []

        def start_actor_process(_group, _actor_id, **kwargs):
            phases.append("called")
            kwargs["start_phase"]("starting_gateway")
            self.assertTrue(kwargs["start_guard"]())
            return {"success": True, "event": {"id": "ev-start"}}

        start_openclaw_startup_workers()
        queued = queue_openclaw_actor_start(
            "g-test",
            "actor-a",
            by="user",
            caller_id="",
            is_admin=True,
            start_actor_process=start_actor_process,
        )
        self.assertEqual(queued.get("state"), "queued")
        row = self._wait_for_state("running")
        self.assertEqual(row.get("phase"), "running")
        self.assertEqual(phases, ["called"])

    def test_cancelled_attempt_cannot_overwrite_stopped_state(self) -> None:
        from no1.daemon.openclaw_startup import (
            cancel_openclaw_actor_start,
            queue_openclaw_actor_start,
            start_openclaw_startup_workers,
        )

        entered = threading.Event()
        release = threading.Event()

        def start_actor_process(_group, _actor_id, **_kwargs):
            entered.set()
            release.wait(timeout=2.0)
            return {"success": True, "event": {"id": "stale"}}

        start_openclaw_startup_workers()
        queue_openclaw_actor_start(
            "g-test",
            "actor-a",
            by="user",
            caller_id="",
            is_admin=True,
            start_actor_process=start_actor_process,
        )
        self.assertTrue(entered.wait(timeout=2.0))
        cancel_openclaw_actor_start("g-test", "actor-a")
        release.set()
        row = self._wait_for_state("stopped")
        time.sleep(0.05)
        from no1.daemon.openclaw_startup import read_openclaw_startup

        row = read_openclaw_startup("g-test", "actor-a")
        self.assertEqual(row.get("state"), "stopped")

    def test_repeated_start_attempts_for_same_actor_do_not_overlap(self) -> None:
        from no1.daemon.openclaw_startup import queue_openclaw_actor_start, start_openclaw_startup_workers

        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()

        def first_start(_group, _actor_id, **_kwargs):
            first_entered.set()
            release_first.wait(timeout=2.0)
            return {"success": True}

        def second_start(_group, _actor_id, **_kwargs):
            second_entered.set()
            return {"success": True}

        start_openclaw_startup_workers()
        queue_openclaw_actor_start(
            "g-test",
            "actor-a",
            by="user",
            caller_id="",
            is_admin=True,
            start_actor_process=first_start,
        )
        self.assertTrue(first_entered.wait(timeout=2.0))
        queue_openclaw_actor_start(
            "g-test",
            "actor-a",
            by="user",
            caller_id="",
            is_admin=True,
            start_actor_process=second_start,
        )
        self.assertFalse(second_entered.wait(timeout=0.1))
        release_first.set()
        self.assertTrue(second_entered.wait(timeout=2.0))
        self._wait_for_state("running")

    def test_actor_list_projects_durable_startup_state(self) -> None:
        from no1.daemon.actors.actor_ops import handle_actor_list
        from no1.daemon.openclaw_startup import queue_openclaw_actor_start

        queued = queue_openclaw_actor_start(
            "g-test",
            "actor-a",
            by="user",
            caller_id="",
            is_admin=True,
            start_actor_process=lambda *_args, **_kwargs: {"success": True},
        )
        self.assertEqual(queued.get("state"), "queued")

        response = handle_actor_list(
            {"group_id": "g-test", "include_unread": False},
            effective_runner_kind=lambda runner: str(runner or "pty"),
        )

        self.assertTrue(response.ok)
        actors = response.result.get("actors") if isinstance(response.result, dict) else []
        actor = next(item for item in actors if item.get("id") == "actor-a")
        self.assertEqual(actor.get("runtime_startup", {}).get("state"), "queued")


if __name__ == "__main__":
    unittest.main()
