from __future__ import annotations

import unittest


class TestPromptDefaults(unittest.TestCase):
    def test_default_preamble_is_compact_and_actionable(self) -> None:
        from cccc.kernel.prompt_files import DEFAULT_PREAMBLE_BODY

        body = str(DEFAULT_PREAMBLE_BODY or "")
        self.assertIn("Startup routes:", body)
        self.assertIn("Working stance:", body)
        self.assertIn("cccc_bootstrap", body)
        self.assertIn("context_hygiene", body)
        self.assertIn("cccc_help", body)
        self.assertIn("cccc_context_get", body)
        self.assertIn("cccc_project_info", body)
        self.assertIn("Reuse working paths first.", body)
        self.assertIn("Prefer silence over low-signal chatter", body)
        self.assertIn("routine `@all` updates", body)
        self.assertIn("finish it end-to-end", body)
        self.assertLessEqual(len(body.split()), 90)

    def test_default_preamble_avoids_long_rule_duplication(self) -> None:
        from cccc.kernel.prompt_files import DEFAULT_PREAMBLE_BODY

        body = str(DEFAULT_PREAMBLE_BODY or "")
        self.assertNotIn("Execution checklist:", body)
        self.assertNotIn("Gap routing:", body)
        self.assertNotIn("Memory boundary:", body)
        self.assertNotIn("cccc_capability_search", body)
        self.assertNotIn("cccc_agent_state(action=update", body)

    def test_builtin_help_is_compact(self) -> None:
        from cccc.kernel.prompt_files import load_builtin_help_markdown

        body = str(load_builtin_help_markdown() or "")
        self.assertLessEqual(len(body.split()), 1500)
        self.assertIn("This is your working playbook for this group.", body)
        self.assertIn("## Working Stance", body)
        self.assertIn("## Communication Patterns", body)
        self.assertIn("## Core Routes", body)
        self.assertIn("## Control Plane", body)
        self.assertIn("## Memory and Recall", body)
        self.assertIn("## Capability", body)
        self.assertIn("## Role Notes", body)
        self.assertIn("## Appendix", body)
        self.assertIn("present the post-review version, not the first draft", body)
        self.assertIn("Prefer silence over low-signal chatter.", body)
        self.assertIn('"standing by"', body)
        self.assertIn("routine status, acknowledgements", body)
        self.assertIn("Do not drip-feed obvious in-scope next steps", body)
        self.assertNotIn("## Quick Card", body)
        self.assertNotIn("## Where Things Live", body)
        self.assertNotIn("### NotebookLM Work vs Memory Lane", body)
        self.assertNotIn("### NotebookLM Artifact Runs", body)
        self.assertNotIn("### Capsule Skill Boundary", body)
        self.assertNotIn("### Terminal Transcript", body)
        self.assertNotIn("### Automation Tools", body)
        self.assertNotIn("## Quick Card", body)
        self.assertIn("Treat `done`, `idle`, and silence as evaluation signals, not closure truth.", body)
        self.assertIn("Protect verifier boundaries unless changing the verifier is explicitly in scope.", body)

    def test_mcp_reminder_line_stays_single_purpose(self) -> None:
        from cccc.daemon.messaging.delivery import MCP_REMINDER_LINE

        self.assertIn("use MCP", MCP_REMINDER_LINE)
        self.assertIn("Terminal output isn't delivered.", MCP_REMINDER_LINE)
        self.assertNotIn("Help: cccc_help", MCP_REMINDER_LINE)

    def test_default_standup_stays_short_ritual(self) -> None:
        from cccc.kernel.group import _DEFAULT_AUTOMATION_STANDUP_SNIPPET

        body = str(_DEFAULT_AUTOMATION_STANDUP_SNIPPET or "")
        self.assertIn("Checklist:", body)
        self.assertIn("Recall:", body)
        self.assertIn("cccc_capability_use(...)", body)
        self.assertIn("cccc_help", body)
        self.assertNotIn('cccc_capability_search(kind="mcp_toolpack"|"skill"', body)
        self.assertNotIn("diagnostics", body)


if __name__ == "__main__":
    unittest.main()
