import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class TestAutomationTaskReminders(unittest.TestCase):
    def _setup_group(self):
        from no1.kernel.actors import add_actor
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        reg = load_registry()
        group = create_group(reg, title="task-reminders")
        add_actor(group, actor_id="lead1", runtime="codex", runner="pty", enabled=True)
        add_actor(group, actor_id="worker1", runtime="codex", runner="pty", enabled=True)
        automation = group.doc.get("automation") if isinstance(group.doc.get("automation"), dict) else {}
        automation.update(
            {
                "task_reminder_enabled": True,
                "task_empty_cooldown_seconds": 300,
                "task_active_overdue_milestones_seconds": [1800, 3000, 3600, 5400],
                "task_planned_unassigned_milestones_seconds": [900, 1800],
            }
        )
        group.doc["automation"] = automation
        group.save()
        return group

    def _save_task(self, group, task):
        from no1.kernel.context import ContextStorage

        ContextStorage(group).save_task(task)

    def _events(self, group):
        lines = [line for line in group.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [json.loads(line) for line in lines]

    def _notify_events(self, group, kind: str):
        out = []
        for ev in self._events(group):
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if str(ev.get("kind") or "") == "system.notify" and str(data.get("kind") or "") == kind:
                out.append(ev)
        return out

    def test_active_task_milestones_notify_executor_foreman_and_escalate_user(self) -> None:
        from no1.daemon.automation import AutomationManager, _cfg
        from no1.kernel.context import ChecklistItem, ChecklistStatus, Task, TaskStatus

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                group = self._setup_group()
                now = datetime(2026, 6, 12, 6, 0, 0, tzinfo=timezone.utc)
                self._save_task(
                    group,
                    Task(
                        id="T001",
                        title="Implement task reminder automation",
                        status=TaskStatus.ACTIVE,
                        assignee="worker1",
                        created_at=_iso(now - timedelta(hours=2)),
                        updated_at=_iso(now - timedelta(hours=2)),
                        checklist=[
                            ChecklistItem(id="c1", text="backend", status=ChecklistStatus.DONE),
                            ChecklistItem(id="c2", text="tests", status=ChecklistStatus.IN_PROGRESS),
                        ],
                    ),
                )

                manager = AutomationManager()
                with patch("no1.daemon.automation.engine._queue_notify_to_pty", return_value=None):
                    manager._check_task_reminders(group, _cfg(group), now)
                    manager._check_task_reminders(group, _cfg(group), now)

                events = self._notify_events(group, "task_active_overdue")
                milestones = sorted(
                    int((ev.get("data") or {}).get("context", {}).get("milestone_seconds") or 0)
                    for ev in events
                    if (ev.get("data") or {}).get("target_actor_id") in {"worker1", "lead1"}
                )
                self.assertEqual(milestones, [1800, 1800, 3000, 3000, 3600, 3600, 5400, 5400])
                user_events = [ev for ev in events if (ev.get("data") or {}).get("target_actor_id") == "user"]
                self.assertEqual(len(user_events), 1)
                self.assertIn("90 minutes", str((user_events[0].get("data") or {}).get("title") or ""))
                self.assertIn("already complete", str((events[0].get("data") or {}).get("message") or ""))
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_empty_board_reminders_are_stateful_and_cooldown_limited(self) -> None:
        from no1.daemon.automation import AutomationManager, _cfg

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                group = self._setup_group()
                manager = AutomationManager()
                now = datetime(2026, 6, 12, 6, 0, 0, tzinfo=timezone.utc)

                with patch("no1.daemon.automation.engine._queue_notify_to_pty", return_value=None):
                    manager._check_task_reminders(group, _cfg(group), now)
                    manager._check_task_reminders(group, _cfg(group), now + timedelta(seconds=120))
                    manager._check_task_reminders(group, _cfg(group), now + timedelta(seconds=360))

                self.assertEqual(len(self._notify_events(group, "task_empty_active")), 2)
                self.assertEqual(len(self._notify_events(group, "task_empty_planned")), 2)
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_planned_unassigned_milestones_notify_foreman_once(self) -> None:
        from no1.daemon.automation import AutomationManager, _cfg
        from no1.kernel.context import Task, TaskStatus

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                group = self._setup_group()
                now = datetime(2026, 6, 12, 6, 0, 0, tzinfo=timezone.utc)
                self._save_task(
                    group,
                    Task(
                        id="T002",
                        title="Write release notes",
                        status=TaskStatus.PLANNED,
                        created_at=_iso(now - timedelta(minutes=35)),
                        updated_at=_iso(now - timedelta(minutes=35)),
                    ),
                )

                manager = AutomationManager()
                with patch("no1.daemon.automation.engine._queue_notify_to_pty", return_value=None):
                    manager._check_task_reminders(group, _cfg(group), now)
                    manager._check_task_reminders(group, _cfg(group), now)

                events = self._notify_events(group, "task_planned_unassigned")
                self.assertEqual(len(events), 2)
                milestones = sorted(int((ev.get("data") or {}).get("context", {}).get("milestone_seconds") or 0) for ev in events)
                self.assertEqual(milestones, [900, 1800])
                self.assertTrue(all((ev.get("data") or {}).get("target_actor_id") == "lead1" for ev in events))
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
