import unittest
from types import SimpleNamespace

from cccc.kernel.pet_task_triage import build_task_triage_payload, join_task_briefs


def _task(
    task_id: str,
    *,
    title: str,
    status: str = "planned",
    assignee: str = "",
    blocked_by: list[str] | None = None,
    waiting_on: str = "none",
    handoff_to: str = "",
):
    return SimpleNamespace(
        id=task_id,
        title=title,
        status=SimpleNamespace(value=status),
        assignee=assignee,
        blocked_by=list(blocked_by or []),
        waiting_on=SimpleNamespace(value=waiting_on),
        handoff_to=handoff_to,
    )


class TestPetTaskTriage(unittest.TestCase):
    def test_build_task_triage_payload_groups_high_value_task_buckets(self) -> None:
        tasks = [
            _task("T1", title="Need user scope", status="active", waiting_on="user"),
            _task("T2", title="Blocked on actor output", status="active", blocked_by=["T9"]),
            _task("T3", title="Foreman should pick this up", status="planned", handoff_to="foreman"),
            _task("T4", title="Backlog cleanup", status="planned"),
            _task("T5", title="Already done", status="done"),
        ]

        triage = build_task_triage_payload(tasks, limit=3)

        self.assertEqual([item.id for item in triage["waiting_user_tasks"]], ["T1"])
        self.assertEqual([item.id for item in triage["blocked_tasks"]], ["T2"])
        self.assertEqual([item.id for item in triage["handoff_tasks"]], ["T3"])
        self.assertEqual([item.id for item in triage["planned_backlog_tasks"]], ["T3", "T4"])

    def test_join_task_briefs_includes_assignee_and_trims_output(self) -> None:
        rendered = join_task_briefs(
            [
                _task("T8", title="Review pet reminder routing before release", assignee="foreman"),
                _task("T9", title="Trim stale planned tasks"),
            ],
            limit=2,
        )
        self.assertIn("T8:Review pet reminder routing before release @foreman", rendered)
        self.assertIn("T9:Trim stale planned tasks", rendered)


if __name__ == "__main__":
    unittest.main()
