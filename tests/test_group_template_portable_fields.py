import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml  # type: ignore


class TestGroupTemplatePortableFields(unittest.TestCase):
    def _call(self, op: str, args: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def _create_group_with_scope(self, td: str, title: str = "bp") -> str:
        create_resp, _ = self._call("group_create", {"title": title, "topic": "", "by": "user"})
        self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
        group_id = str((create_resp.result or {}).get("group_id") or "").strip()
        self.assertTrue(group_id)

        scope_dir = os.path.join(td, "scope")
        os.makedirs(scope_dir, exist_ok=True)
        attach_resp, _ = self._call("attach", {"path": scope_dir, "group_id": group_id, "by": "user"})
        self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))
        return group_id

    def test_export_includes_actor_autoload_and_feature_toggles(self) -> None:
        from cccc.kernel.group import load_group

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td

                create_resp, _ = self._call("group_create", {"title": "bp", "topic": "", "by": "user"})
                self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
                group_id = str((create_resp.result or {}).get("group_id") or "").strip()
                self.assertTrue(group_id)

                add_resp, _ = self._call(
                    "actor_add",
                    {
                        "group_id": group_id,
                        "actor_id": "peer1",
                        "runtime": "codex",
                        "runner": "pty",
                        "capability_autoload": ["pack:space", "skill:anthropic:triage"],
                        "by": "user",
                    },
                )
                self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

                group = load_group(group_id)
                self.assertIsNotNone(group)
                assert group is not None
                group.doc["features"] = {"desktop_pet_enabled": True, "panorama_enabled": True}
                group.save()

                export_resp, _ = self._call("group_template_export", {"group_id": group_id})
                self.assertTrue(export_resp.ok, getattr(export_resp, "error", None))

                template_text = str((export_resp.result or {}).get("template") or "")
                parsed = yaml.safe_load(template_text) or {}
                actors = parsed.get("actors") if isinstance(parsed.get("actors"), list) else []
                self.assertEqual(actors[0].get("capability_autoload"), ["pack:space", "skill:anthropic:triage"])
                settings = parsed.get("settings") if isinstance(parsed.get("settings"), dict) else {}
                self.assertTrue(bool(settings.get("desktop_pet_enabled")))
                self.assertTrue(bool(settings.get("panorama_enabled")))
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_export_profile_linked_actor_inlines_actor_scope_profile_autoload_defaults(self) -> None:
        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td

                create_resp, _ = self._call("group_create", {"title": "bp", "topic": "", "by": "user"})
                self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
                group_id = str((create_resp.result or {}).get("group_id") or "").strip()
                self.assertTrue(group_id)

                profile_upsert, _ = self._call(
                    "actor_profile_upsert",
                    {
                        "by": "user",
                        "profile": {
                            "id": "linked-prof",
                            "name": "Linked",
                            "runtime": "custom",
                            "runner": "headless",
                            "command": [],
                            "submit": "enter",
                            "capability_defaults": {
                                "autoload_capabilities": ["pack:space"],
                                "default_scope": "actor",
                            },
                        },
                    },
                )
                self.assertTrue(profile_upsert.ok, getattr(profile_upsert, "error", None))

                add_resp, _ = self._call(
                    "actor_add",
                    {
                        "group_id": group_id,
                        "actor_id": "peer1",
                        "runtime": "custom",
                        "runner": "headless",
                        "profile_id": "linked-prof",
                        "capability_autoload": ["skill:anthropic:triage"],
                        "by": "user",
                    },
                )
                self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

                export_resp, _ = self._call("group_template_export", {"group_id": group_id})
                self.assertTrue(export_resp.ok, getattr(export_resp, "error", None))

                template_text = str((export_resp.result or {}).get("template") or "")
                parsed = yaml.safe_load(template_text) or {}
                actors = parsed.get("actors") if isinstance(parsed.get("actors"), list) else []
                self.assertEqual(
                    actors[0].get("capability_autoload"),
                    ["pack:space", "skill:anthropic:triage"],
                )
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_preview_diff_detects_actor_autoload_and_feature_toggle_changes(self) -> None:
        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td

                create_resp, _ = self._call("group_create", {"title": "bp", "topic": "", "by": "user"})
                self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
                group_id = str((create_resp.result or {}).get("group_id") or "").strip()
                self.assertTrue(group_id)

                add_resp, _ = self._call(
                    "actor_add",
                    {"group_id": group_id, "actor_id": "peer1", "runtime": "codex", "runner": "pty", "by": "user"},
                )
                self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

                template = """
kind: cccc.group_template
v: 1
actors:
  - id: peer1
    title: ""
    runtime: codex
    runner: pty
    command: []
    submit: enter
    capability_autoload:
      - pack:space
    enabled: true
settings:
  panorama_enabled: true
  desktop_pet_enabled: true
prompts: {}
automation:
  rules: []
  snippets: {}
"""
                preview_resp, _ = self._call(
                    "group_template_preview",
                    {"group_id": group_id, "by": "user", "template": template},
                )
                self.assertTrue(preview_resp.ok, getattr(preview_resp, "error", None))
                diff = (preview_resp.result or {}).get("diff") if isinstance(preview_resp.result, dict) else {}
                self.assertIsInstance(diff, dict)
                assert isinstance(diff, dict)
                self.assertIn("peer1", diff.get("actors_update") or [])
                settings_changed = diff.get("settings_changed") if isinstance(diff.get("settings_changed"), dict) else {}
                self.assertIn("panorama_enabled", settings_changed)
                self.assertIn("desktop_pet_enabled", settings_changed)
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_import_replace_applies_actor_autoload_and_feature_toggles(self) -> None:
        from cccc.kernel.group import load_group

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td

                create_resp, _ = self._call("group_create", {"title": "bp", "topic": "", "by": "user"})
                self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
                group_id = str((create_resp.result or {}).get("group_id") or "").strip()
                self.assertTrue(group_id)

                scope_dir = os.path.join(td, "scope")
                os.makedirs(scope_dir, exist_ok=True)
                attach_resp, _ = self._call("attach", {"path": scope_dir, "group_id": group_id, "by": "user"})
                self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))

                template = """
kind: cccc.group_template
v: 1
actors:
  - id: peer1
    title: Worker
    runtime: codex
    runner: pty
    command: []
    submit: enter
    capability_autoload:
      - pack:space
      - skill:anthropic:triage
    enabled: true
settings:
  panorama_enabled: true
  desktop_pet_enabled: true
prompts: {}
automation:
  rules: []
  snippets: {}
"""
                import_resp, _ = self._call(
                    "group_template_import_replace",
                    {"group_id": group_id, "by": "user", "confirm": group_id, "template": template},
                )
                self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

                group = load_group(group_id)
                self.assertIsNotNone(group)
                assert group is not None
                actors = group.doc.get("actors") if isinstance(group.doc.get("actors"), list) else []
                actor = next((item for item in actors if isinstance(item, dict) and item.get("id") == "peer1"), None)
                self.assertIsNotNone(actor)
                assert isinstance(actor, dict)
                self.assertEqual(
                    actor.get("capability_autoload"),
                    ["pack:space", "skill:anthropic:triage"],
                )
                features = group.doc.get("features") if isinstance(group.doc.get("features"), dict) else {}
                self.assertTrue(bool(features.get("panorama_enabled")))
                self.assertTrue(bool(features.get("desktop_pet_enabled")))
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_import_replace_invalidates_summary_snapshot_after_actor_removal(self) -> None:
        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                group_id = self._create_group_with_scope(td, title="snapshot")

                add_resp, _ = self._call(
                    "actor_add",
                    {"group_id": group_id, "actor_id": "peer1", "runtime": "codex", "runner": "pty", "by": "user"},
                )
                self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

                sync_resp, _ = self._call(
                    "context_sync",
                    {
                        "group_id": group_id,
                        "by": "peer1",
                        "ops": [{"op": "agent_state.update", "actor_id": "peer1", "focus": "Working"}],
                    },
                )
                self.assertTrue(sync_resp.ok, getattr(sync_resp, "error", None))

                from cccc.daemon.context.context_ops import _rebuild_summary_snapshot

                _rebuild_summary_snapshot(group_id)

                summary_before, _ = self._call("context_get", {"group_id": group_id, "detail": "summary"})
                self.assertTrue(summary_before.ok, getattr(summary_before, "error", None))
                before_agents = (summary_before.result or {}).get("agent_states") or []
                self.assertEqual([str(item.get("id") or "") for item in before_agents], ["peer1"])
                before_version = str((summary_before.result or {}).get("version") or "")
                self.assertTrue(before_version.startswith("ctxv:"))

                template = """
kind: cccc.group_template
v: 1
actors: []
prompts: {}
automation:
  rules: []
  snippets: {}
"""
                import_resp, _ = self._call(
                    "group_template_import_replace",
                    {"group_id": group_id, "by": "user", "confirm": group_id, "template": template},
                )
                self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

                summary_after, _ = self._call("context_get", {"group_id": group_id, "detail": "summary"})
                self.assertTrue(summary_after.ok, getattr(summary_after, "error", None))
                after_agents = (summary_after.result or {}).get("agent_states") or []
                self.assertEqual([str(item.get("id") or "") for item in after_agents], ["peer1"])
                self.assertEqual(
                    ((summary_after.result or {}).get("meta") or {}).get("summary_snapshot", {}).get("state"),
                    "stale",
                )
                after_version = str((summary_after.result or {}).get("version") or "")
                self.assertEqual(after_version, before_version)

                full_after, _ = self._call("context_get", {"group_id": group_id, "detail": "full"})
                self.assertTrue(full_after.ok, getattr(full_after, "error", None))
                self.assertEqual((full_after.result or {}).get("agent_states") or [], [])

                from cccc.kernel.group import load_group

                group = load_group(group_id)
                assert group is not None
                snapshot_path = Path(group.path) / "context" / "summary_snapshot.json"
                self.assertTrue(snapshot_path.exists())
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_import_replace_schedules_summary_snapshot_rebuild_on_reorder(self) -> None:
        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                group_id = self._create_group_with_scope(td, title="template-reorder")

                add_peer1, _ = self._call(
                    "actor_add",
                    {"group_id": group_id, "actor_id": "peer1", "runtime": "codex", "runner": "pty", "by": "user"},
                )
                self.assertTrue(add_peer1.ok, getattr(add_peer1, "error", None))
                add_peer2, _ = self._call(
                    "actor_add",
                    {"group_id": group_id, "actor_id": "peer2", "runtime": "codex", "runner": "pty", "by": "user"},
                )
                self.assertTrue(add_peer2.ok, getattr(add_peer2, "error", None))

                template = """
kind: cccc.group_template
v: 1
actors:
  - id: peer2
    title: Peer 2
    runtime: codex
  - id: peer1
    title: Peer 1
    runtime: codex
prompts: {}
automation:
  rules: []
  snippets: {}
"""
                with patch(
                    "cccc.daemon.ops.template_ops._schedule_summary_snapshot_rebuild",
                    return_value=True,
                ) as mock_schedule:
                    import_resp, _ = self._call(
                        "group_template_import_replace",
                        {"group_id": group_id, "by": "user", "confirm": group_id, "template": template},
                    )
                self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))
                mock_schedule.assert_called_once_with(group_id)
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
