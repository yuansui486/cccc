import os
import tempfile
import unittest
from unittest.mock import patch

import yaml  # type: ignore


class TestGroupTemplateAutomationReplace(unittest.TestCase):
    def test_import_replace_clears_automation_when_template_omits_automation_block(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.group import load_group

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td

                create_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {"op": "group_create", "args": {"title": "t", "topic": "", "by": "user"}}
                    )
                )
                self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
                group_id = str((create_resp.result or {}).get("group_id") or "").strip()
                self.assertTrue(group_id)

                scope_dir = os.path.join(td, "scope")
                os.makedirs(scope_dir, exist_ok=True)
                attach_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {"op": "attach", "args": {"path": scope_dir, "group_id": group_id, "by": "user"}}
                    )
                )
                self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))

                update_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {
                            "op": "group_automation_update",
                            "args": {
                                "group_id": group_id,
                                "by": "user",
                                "ruleset": {
                                    "rules": [
                                        {
                                            "id": "custom_rule",
                                            "enabled": True,
                                            "scope": "group",
                                            "to": ["@foreman"],
                                            "trigger": {"kind": "interval", "every_seconds": 60},
                                            "action": {
                                                "kind": "notify",
                                                "title": "Custom",
                                                "message": "hello",
                                                "priority": "normal",
                                                "requires_ack": False,
                                            },
                                        }
                                    ],
                                    "snippets": {"custom_snippet": "hello"},
                                },
                            },
                        }
                    )
                )
                self.assertTrue(update_resp.ok, getattr(update_resp, "error", None))

                template = """
kind: cccc.group_template
v: 1
actors: []
prompts: {}
"""
                import_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {
                            "op": "group_template_import_replace",
                            "args": {
                                "group_id": group_id,
                                "by": "user",
                                "confirm": group_id,
                                "template": template,
                            },
                        }
                    )
                )
                self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

                group = load_group(group_id)
                self.assertIsNotNone(group)
                automation = group.doc.get("automation") if isinstance(group.doc.get("automation"), dict) else {}
                self.assertEqual(automation.get("rules"), [])
                self.assertEqual(automation.get("snippets"), {})
                self.assertEqual(automation.get("snippet_overrides"), {})
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_export_always_includes_automation_block_when_empty(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td

                create_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {"op": "group_create", "args": {"title": "t", "topic": "", "by": "user"}}
                    )
                )
                self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
                group_id = str((create_resp.result or {}).get("group_id") or "").strip()
                self.assertTrue(group_id)

                clear_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {
                            "op": "group_automation_update",
                            "args": {
                                "group_id": group_id,
                                "by": "user",
                                "ruleset": {"rules": [], "snippets": {}},
                            },
                        }
                    )
                )
                self.assertTrue(clear_resp.ok, getattr(clear_resp, "error", None))

                export_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {"op": "group_template_export", "args": {"group_id": group_id}}
                    )
                )
                self.assertTrue(export_resp.ok, getattr(export_resp, "error", None))

                template_text = str((export_resp.result or {}).get("template") or "")
                self.assertTrue(template_text)
                parsed = yaml.safe_load(template_text) or {}
                self.assertIn("automation", parsed)
                self.assertEqual((parsed.get("automation") or {}).get("rules"), [])
                self.assertEqual((parsed.get("automation") or {}).get("snippets"), {})
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

    def test_import_replace_clears_execution_state_but_preserves_mind_context(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.context import ContextStorage
        from cccc.kernel.group import load_group

        old_home = os.environ.get("CCCC_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td

                create_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {"op": "group_create", "args": {"title": "t", "topic": "", "by": "user"}}
                    )
                )
                self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
                group_id = str((create_resp.result or {}).get("group_id") or "").strip()
                self.assertTrue(group_id)

                add_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {
                            "op": "actor_add",
                            "args": {
                                "group_id": group_id,
                                "actor_id": "peer1",
                                "runtime": "codex",
                                "runner": "headless",
                                "by": "user",
                            },
                        }
                    )
                )
                self.assertTrue(add_resp.ok, getattr(add_resp, "error", None))

                sync_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {
                            "op": "context_sync",
                            "args": {
                                "group_id": group_id,
                                "by": "peer1",
                                "ops": [
                                    {
                                        "op": "agent_state.update",
                                        "actor_id": "peer1",
                                        "active_task_id": "T009",
                                        "focus": "doing x",
                                        "next_action": "continue",
                                        "blockers": ["waiting on review"],
                                        "what_changed": "started",
                                        "environment_summary": "repo dirty",
                                        "user_model": "prefers concise status",
                                        "persona_notes": "stay skeptical",
                                        "resume_hint": "open the failing test",
                                    }
                                ],
                            },
                        }
                    )
                )
                self.assertTrue(sync_resp.ok, getattr(sync_resp, "error", None))

                export_resp, _ = handle_request(
                    DaemonRequest.model_validate(
                        {"op": "group_template_export", "args": {"group_id": group_id}}
                    )
                )
                self.assertTrue(export_resp.ok, getattr(export_resp, "error", None))
                template_text = str((export_resp.result or {}).get("template") or "")
                self.assertTrue(template_text)

                with patch("cccc.daemon.ops.template_ops.pty_runner.SUPERVISOR.actor_running", return_value=False), patch(
                    "cccc.daemon.ops.template_ops.headless_runner.SUPERVISOR.actor_running",
                    return_value=True,
                ):
                    import_resp, _ = handle_request(
                        DaemonRequest.model_validate(
                            {
                                "op": "group_template_import_replace",
                                "args": {
                                    "group_id": group_id,
                                    "by": "user",
                                    "confirm": group_id,
                                    "template": template_text,
                                },
                            }
                        )
                    )
                self.assertTrue(import_resp.ok, getattr(import_resp, "error", None))

                group = load_group(group_id)
                self.assertIsNotNone(group)
                storage = ContextStorage(group)
                agents = storage.load_agents().agents
                peer = next((agent for agent in agents if agent.id == "peer1"), None)
                self.assertIsNotNone(peer)
                assert peer is not None
                self.assertIsNone(peer.hot.active_task_id)
                self.assertEqual(peer.hot.focus, "")
                self.assertEqual(peer.hot.next_action, "")
                self.assertEqual(peer.hot.blockers, [])
                self.assertEqual(peer.warm.what_changed, "")
                self.assertEqual(peer.warm.environment_summary, "repo dirty")
                self.assertEqual(peer.warm.user_model, "prefers concise status")
                self.assertEqual(peer.warm.persona_notes, "stay skeptical")
                self.assertEqual(peer.warm.resume_hint, "open the failing test")
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home


if __name__ == "__main__":
    unittest.main()
