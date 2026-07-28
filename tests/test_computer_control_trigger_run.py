import tempfile
import unittest
from pathlib import Path

from no1.computer_control.runtime import WorkflowRunner


class _TriggerStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, group_id, workflow_id, *, version=None):
        del group_id, workflow_id
        return {
            "version": int(version or 1),
            "definition": {
                "name": "triggered",
                "nodes": [
                    {"id": "start", "type": "start"},
                    {"id": "end", "type": "end"},
                ],
                "edges": [{"source": "start", "target": "end"}],
            },
        }

    def state_root(self, group_id):
        return self.root / group_id / "state"


class _TriggerLease:
    def acquire(self, **kwargs):
        return kwargs

    def require(self, **kwargs):
        return kwargs

    def heartbeat(self, **kwargs):
        return kwargs

    def release(self, **kwargs):
        return True

    def status(self):
        return {"active": False}


class _TriggerSession:
    transport_restarts = 0

    async def catalog(self):
        return []


class TestComputerControlTriggerRun(unittest.IsolatedAsyncioTestCase):
    async def test_trigger_context_is_persisted_with_an_allowlisted_shape(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            runner = WorkflowRunner(
                Path(td),
                _TriggerStore(Path(td)),
                _TriggerLease(),
                _TriggerSession(),
            )
            run = await runner.start(
                "group",
                "workflow",
                actor_id="foreman",
                version=1,
                inputs={},
                trigger_context={
                    "trigger_id": "trigger_element",
                    "name": "提交按钮出现",
                    "type": "element",
                    "detected_at": 123.0,
                    "source": "scheduler",
                    "evidence": {"match_count": 1},
                    "ignored": "must not be persisted",
                },
            )

            self.assertEqual(run["trigger"]["trigger_id"], "trigger_element")
            self.assertEqual(run["trigger"]["evidence"], {"match_count": 1})
            self.assertNotIn("ignored", run["trigger"])
            stored = runner.get("group", run["run_id"])
            self.assertEqual(stored["trigger"]["name"], "提交按钮出现")

            for task in list(runner._tasks.values()):
                await task


if __name__ == "__main__":
    unittest.main()
