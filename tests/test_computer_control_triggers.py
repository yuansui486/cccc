import asyncio
import contextlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from no1.computer_control.lease import LeaseConflict
from no1.computer_control.models import WorkflowTrigger
from no1.computer_control.scheduler import ComputerControlScheduler
from no1.computer_control.triggers import (
    advance_element_edge,
    element_poll_seconds,
    next_cron_time,
    next_schedule_time,
    parse_at_timestamp,
    validate_trigger,
)


class _Store:
    def __init__(self, root: Path):
        self.root = root

    def state_root(self, group_id: str) -> Path:
        return self.root / group_id


class _Lease:
    @contextlib.contextmanager
    def hold(self, **_kwargs):
        yield {}


class _Session:
    def __init__(self):
        self.snapshots = []
        self.calls = 0

    def call_tool_sync(self, tool, arguments, *, timeout=None):
        self.calls += 1
        self.assert_snapshot_call = (tool, arguments, timeout)
        return self.snapshots.pop(0)


class _Observation:
    def __init__(self):
        self.desktop_available = True

    def desktop_session_available(self):
        return self.desktop_available

    async def enhance(self, snapshot, locator):
        result = dict(snapshot)
        target_window = str(locator.get("window_name") or "")
        count = sum(1 for item in result.get("elements", []) if item.get("window_name") == target_window)
        result.update({
            "target_window": target_window,
            "target_window_element_count": count,
            "snapshot_health": "target_available" if count else "target_elements_unavailable",
        })
        return result


class _Runner:
    def __init__(self):
        self.attempts = 0
        self.calls = []
        self.busy_once = False
        self.runs = {}

    async def start(self, group_id, workflow_id, **kwargs):
        self.attempts += 1
        if self.busy_once:
            self.busy_once = False
            raise LeaseConflict({"run_id": "other"})
        run_id = f"run-{len(self.calls) + 1}"
        self.calls.append((group_id, workflow_id, kwargs))
        self.runs[run_id] = {"run_id": run_id, "status": "running"}
        return dict(self.runs[run_id])

    def get(self, _group_id, run_id):
        return dict(self.runs[run_id])


def _service(root: Path):
    return SimpleNamespace(
        home=root,
        store=_Store(root),
        lease=_Lease(),
        session=_Session(),
        observation=_Observation(),
        runner=_Runner(),
    )


def _snapshot(names):
    return {
        "focused_window": "Orders",
        "elements": [
            {
                "window_name": "Orders",
                "name": name,
                "control_type": "Button",
                "automation_id": f"id-{index}",
                "visible": True,
                "enabled": True,
                "bounds": {"x": index * 10, "y": 10, "width": 8, "height": 8},
            }
            for index, name in enumerate(names)
        ],
    }


class TestTriggerHelpers(unittest.TestCase):
    def test_new_trigger_types_validate_and_keep_legacy_compatibility(self):
        interval = WorkflowTrigger(id="fast", type="interval", enabled=True, config={"seconds": 1})
        self.assertEqual(interval.actor_id, "foreman")
        validate_trigger(interval)
        with self.assertRaisesRegex(ValueError, "between 1 second"):
            validate_trigger(WorkflowTrigger(id="too-fast", type="interval", config={"seconds": 0.5}))
        validate_trigger(WorkflowTrigger(id="legacy", type="event", config={"kind": "demo"}))

    def test_calendar_helpers_are_deterministic(self):
        at = parse_at_timestamp("2026-08-01T10:30:00Z")
        self.assertEqual(at, 1785580200.0)
        scheduled = next_schedule_time(
            {"time": "10:30", "weekdays": [5], "timezone": "UTC"},
            parse_at_timestamp("2026-08-01T10:29:00Z"),
        )
        self.assertEqual(scheduled, at)
        cron = next_cron_time("30 10 * * *", parse_at_timestamp("2026-08-01T10:29:00Z"), timezone="UTC")
        self.assertEqual(cron, at)
        with self.assertRaisesRegex(ValueError, "five field"):
            next_cron_time("0 30 10 * * *", at, timezone="UTC")

    def test_element_defaults_and_unknown_observation_preserve_edge(self):
        self.assertEqual(element_poll_seconds({}), 2.0)
        with self.assertRaisesRegex(ValueError, "0.5 seconds"):
            element_poll_seconds({"poll_seconds": 0.49})
        fire, state = advance_element_edge({"armed": True, "hit_count": 0}, "present", required_hits=2)
        self.assertFalse(fire)
        fire, state = advance_element_edge(state, "unknown", required_hits=2)
        self.assertFalse(fire)
        self.assertTrue(state["armed"])
        self.assertEqual(state["hit_count"], 1)
        fire, state = advance_element_edge(state, "present", required_hits=2)
        self.assertTrue(fire)


class TestComputerControlScheduler(unittest.TestCase):
    def test_activation_gate_requires_the_published_version(self):
        self.assertTrue(ComputerControlScheduler._activation_allows({}, "trigger", 3))
        self.assertFalse(ComputerControlScheduler._activation_allows({"trigger": {"enabled": False, "version": 3}}, "trigger", 3))
        self.assertFalse(ComputerControlScheduler._activation_allows({"trigger": {"enabled": True, "version": 2}}, "trigger", 3))
        self.assertTrue(ComputerControlScheduler._activation_allows({"trigger": {"enabled": True, "version": 3}}, "trigger", 3))

    def test_interval_pending_waits_for_desktop_and_coalesces_busy_run(self):
        async def exercise(root: Path):
            service = _service(root)
            scheduler = ComputerControlScheduler(service)
            trigger = {
                "id": "interval",
                "type": "interval",
                "enabled": True,
                "actor_id": "foreman",
                "config": {"seconds": 1},
                "inputs": {"message": "hello"},
                "cooldown_seconds": 30,
            }
            states = {}
            await scheduler._process_trigger("group", "workflow", 2, trigger, states, now=100.0)
            state = states["workflow:interval"]
            self.assertEqual(state["next_due_at"], 101.0)

            service.observation.desktop_available = False
            await scheduler._process_trigger("group", "workflow", 2, trigger, states, now=101.1)
            self.assertTrue(state["pending"])
            self.assertEqual(state["waiting_on"], "desktop")
            self.assertEqual(service.runner.attempts, 0)

            service.observation.desktop_available = True
            service.runner.busy_once = True
            await scheduler._process_trigger("group", "workflow", 2, trigger, states, now=102.0)
            self.assertTrue(state["pending"])
            self.assertEqual(state["waiting_on"], "computer_control_busy")
            await scheduler._process_trigger("group", "workflow", 2, trigger, states, now=103.1)
            self.assertFalse(state["pending"])
            self.assertEqual(len(service.runner.calls), 1)
            call = service.runner.calls[0][2]
            self.assertEqual(call["inputs"], {"message": "hello"})
            self.assertEqual(call["trigger_context"]["scheduled_for"], 101.0)
            self.assertEqual(state["next_due_at"], 104.1)

            await scheduler._process_trigger("group", "workflow", 2, trigger, states, now=105.0)
            self.assertTrue(state["pending"])
            self.assertEqual(state["waiting_on"], "previous_trigger_run")
            self.assertEqual(len(service.runner.calls), 1)
            service.runner.runs["run-1"]["status"] = "completed"
            await scheduler._process_trigger("group", "workflow", 2, trigger, states, now=106.1)
            self.assertEqual(len(service.runner.calls), 2)

            await scheduler._save_states("group", states)
            restored = await ComputerControlScheduler(service)._load_states("group")
            self.assertEqual(restored["workflow:interval"]["last_run_id"], "run-2")

        with tempfile.TemporaryDirectory() as td:
            asyncio.run(exercise(Path(td)))

    def test_element_trigger_fires_on_two_hits_and_rearms_only_after_absence(self):
        async def exercise(root: Path):
            service = _service(root)
            scheduler = ComputerControlScheduler(service)
            service.session.snapshots = [
                _snapshot(["Other"]),
                _snapshot(["Ready"]),
                _snapshot(["Ready"]),
                _snapshot(["Ready", "Ready"]),
                _snapshot(["Other"]),
                _snapshot(["Ready"]),
                _snapshot(["Ready"]),
            ]
            trigger = {
                "id": "element",
                "type": "element",
                "enabled": True,
                "actor_id": "foreman",
                "config": {
                    "poll_seconds": 0.5,
                    "required_hits": 2,
                    "locator": {"window_name": "Orders", "control_type": "Button", "name": "Ready"},
                },
                "cooldown_seconds": 0,
            }
            states = {}
            for now in (200.0, 201.0, 202.0):
                await scheduler._process_trigger("group", "workflow", 1, trigger, states, now=now)
            state = states["workflow:element"]
            self.assertEqual(len(service.runner.calls), 1)
            self.assertFalse(state["armed"])

            await scheduler._process_trigger("group", "workflow", 1, trigger, states, now=203.0)
            self.assertFalse(state["armed"])
            self.assertEqual(state["hit_count"], 2)

            await scheduler._process_trigger("group", "workflow", 1, trigger, states, now=204.0)
            self.assertTrue(state["armed"])
            service.runner.runs["run-1"]["status"] = "completed"
            await scheduler._process_trigger("group", "workflow", 1, trigger, states, now=205.0)
            await scheduler._process_trigger("group", "workflow", 1, trigger, states, now=206.0)
            self.assertEqual(len(service.runner.calls), 2)

        with tempfile.TemporaryDirectory() as td:
            asyncio.run(exercise(Path(td)))


if __name__ == "__main__":
    unittest.main()
