from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class TestProjectExperience(unittest.TestCase):
    def setUp(self) -> None:
        self._old_home = os.environ.get("CCCC_HOME")
        self._home = tempfile.TemporaryDirectory()
        os.environ["CCCC_HOME"] = self._home.name

    def tearDown(self) -> None:
        self._home.cleanup()
        if self._old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = self._old_home

    def _group(self, workspace: Path, *, title: str = "experience"):
        from no1.kernel.group import attach_scope_to_group, create_group
        from no1.kernel.registry import load_registry
        from no1.kernel.scope import detect_scope

        registry = load_registry()
        group = create_group(registry, title=title, topic="")
        return attach_scope_to_group(registry, group, detect_scope(workspace), set_active=True)

    def test_attach_creates_template_without_overwriting_existing_content(self) -> None:
        from no1.kernel.experience import EXPERIENCE_FILENAME

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            group = self._group(workspace)
            path = workspace / EXPERIENCE_FILENAME
            self.assertTrue(path.exists())
            self.assertIn("# 项目经验", path.read_text(encoding="utf-8"))

            path.write_text("# Existing\n\nKeep me.\n", encoding="utf-8")
            self._group(workspace, title="another-team")
            self.assertEqual(path.read_text(encoding="utf-8"), "# Existing\n\nKeep me.\n")
            self.assertTrue(group.doc.get("active_scope_key"))

    def test_groups_attached_to_same_project_share_append_and_replace(self) -> None:
        from no1.kernel.experience import ExperienceError, append_experience, read_experience, replace_experience

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            first = self._group(workspace, title="first")
            second = self._group(workspace, title="second")

            appended = append_experience(first, content="### Verified lesson\n- Resolution: use the shared path")
            self.assertIn("Verified lesson", read_experience(second).content)

            duplicate = append_experience(second, content="### Verified lesson\n- Resolution: use the shared path")
            self.assertEqual(duplicate.content.count("### Verified lesson"), 1)

            replaced = replace_experience(
                second,
                content="# Project Experience\n\n## Lessons\n\nConsolidated.",
                expected_revision=appended.revision,
            )
            self.assertIn("Consolidated", replaced.content)
            with self.assertRaises(ExperienceError) as ctx:
                replace_experience(first, content="stale overwrite", expected_revision=appended.revision)
            self.assertEqual(ctx.exception.code, "revision_conflict")

    def test_existing_scope_is_backfilled_on_first_read(self) -> None:
        from no1.kernel.experience import read_experience
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw).resolve()
            group = create_group(load_registry(), title="legacy", topic="")
            group.doc["scopes"] = [{"scope_key": "s_legacy", "url": str(workspace), "label": "legacy"}]
            group.doc["active_scope_key"] = "s_legacy"
            group.save()

            self.assertFalse((workspace / "EXPERIENCE.md").exists())
            doc = read_experience(group, ensure=True)
            self.assertTrue(doc.created)
            self.assertTrue((workspace / "EXPERIENCE.md").exists())


class TestExperienceReminder(unittest.TestCase):
    def setUp(self) -> None:
        self._old_home = os.environ.get("CCCC_HOME")
        self._home = tempfile.TemporaryDirectory()
        os.environ["CCCC_HOME"] = self._home.name
        self._workspace = tempfile.TemporaryDirectory()

        from no1.kernel.group import attach_scope_to_group, create_group
        from no1.kernel.registry import load_registry
        from no1.kernel.scope import detect_scope

        registry = load_registry()
        group = create_group(registry, title="reminder", topic="")
        self.group = attach_scope_to_group(
            registry,
            group,
            detect_scope(Path(self._workspace.name)),
            set_active=True,
        )

    def tearDown(self) -> None:
        self._workspace.cleanup()
        self._home.cleanup()
        if self._old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = self._old_home

    @staticmethod
    def _message(number: int, *, by: str = "user") -> dict[str, str]:
        return {"id": f"e-{number}", "kind": "chat.message", "by": by}

    def test_default_tenth_user_message_is_due_and_state_is_idempotent(self) -> None:
        from no1.daemon.messaging.experience_reminder import commit_experience_reminder, plan_experience_reminder

        for number in range(1, 10):
            decision = plan_experience_reminder(self.group, actor_id="peer-1", messages=[self._message(number)])
            self.assertFalse(decision.due)
            commit_experience_reminder(self.group, decision)

        tenth = plan_experience_reminder(self.group, actor_id="peer-1", messages=[self._message(10)])
        self.assertTrue(tenth.due)
        commit_experience_reminder(self.group, tenth)

        duplicate = plan_experience_reminder(self.group, actor_id="peer-1", messages=[self._message(10)])
        self.assertFalse(duplicate.due)
        self.assertEqual(duplicate.event_ids, ())

        other_actor = plan_experience_reminder(self.group, actor_id="peer-2", messages=[self._message(10)])
        self.assertFalse(other_actor.due)
        self.assertEqual(len(other_actor.event_ids), 1)

    def test_non_user_messages_and_disabled_policy_do_not_count(self) -> None:
        from no1.daemon.messaging.experience_reminder import commit_experience_reminder, plan_experience_reminder

        ai_message = plan_experience_reminder(self.group, actor_id="peer", messages=[self._message(1, by="foreman")])
        self.assertEqual(ai_message.event_ids, ())

        self.group.doc["experience"] = {"reminder_enabled": False, "reminder_every_user_messages": 1}
        self.group.save()
        disabled = plan_experience_reminder(self.group, actor_id="peer", messages=[self._message(2)])
        self.assertFalse(disabled.due)
        self.assertEqual(disabled.event_ids, ())
        commit_experience_reminder(self.group, disabled)

    def test_custom_frequency_persists_through_disk_state(self) -> None:
        from no1.daemon.messaging.experience_reminder import commit_experience_reminder, plan_experience_reminder

        self.group.doc["experience"] = {"reminder_enabled": True, "reminder_every_user_messages": 2}
        self.group.save()
        first = plan_experience_reminder(self.group, actor_id="peer", messages=[self._message(1)])
        commit_experience_reminder(self.group, first)
        second = plan_experience_reminder(self.group, actor_id="peer", messages=[self._message(2)])
        self.assertTrue(second.due)
        commit_experience_reminder(self.group, second)

        state_path = self.group.path / "state" / "experience_reminders.json"
        self.assertTrue(state_path.exists())
        third = plan_experience_reminder(self.group, actor_id="peer", messages=[self._message(3)])
        self.assertFalse(third.due)


class TestExperienceMcpHandler(unittest.TestCase):
    def test_handler_maps_actions_to_daemon_operations(self) -> None:
        from no1.ports.mcp.handlers.experience import _handle_experience_namespace

        calls: list[dict] = []

        class FakeError(Exception):
            pass

        result = _handle_experience_namespace(
            "onecolleague_experience",
            {"action": "replace", "content": "organized", "expected_revision": "sha256:old"},
            resolve_group_id=lambda _args: "g-test",
            resolve_actor_id=lambda _args: "peer",
            call_daemon_or_raise=lambda request: calls.append(request) or {"ok": True},
            mcp_error_cls=FakeError,
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls[0]["op"], "experience_replace")
        self.assertEqual(calls[0]["args"]["by"], "peer")
