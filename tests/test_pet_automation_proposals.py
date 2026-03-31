import tempfile
import unittest
from pathlib import Path


class _FakeGroup:
    def __init__(self, group_id: str, root: Path) -> None:
        self.group_id = group_id
        self.path = root
        self.doc = {
            "group_id": group_id,
            "title": "demo",
            "automation": {
                "version": 1,
                "rules": [],
                "snippets": {},
            },
        }

    @property
    def ledger_path(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path / "ledger.jsonl"


class TestPetAutomationProposals(unittest.TestCase):
    def test_builds_one_shot_waiting_user_candidate(self) -> None:
        from cccc.contracts.v1 import AutomationRuleSet
        from cccc.kernel.pet_automation_proposals import build_pet_automation_proposal_candidates

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            candidates = build_pet_automation_proposal_candidates(
                group,
                signal_payload={
                    "proposal_ready": {
                        "ready": True,
                        "focus": "waiting_user",
                    }
                },
                ruleset=AutomationRuleSet(rules=[], snippets={}),
            )

        self.assertEqual(len(candidates), 1)
        action = candidates[0]["action"]
        self.assertEqual(action["type"], "automation_proposal")
        self.assertEqual(action["actions"][0]["type"], "create_rule")
        rule = action["actions"][0]["rule"]
        self.assertEqual(rule["id"], "pet-user-dependency-followup-once")
        self.assertEqual(rule["trigger"]["kind"], "at")
        self.assertEqual(rule["action"]["kind"], "notify")

    def test_reuses_existing_rule_id_as_update(self) -> None:
        from cccc.contracts.v1 import AutomationRuleSet
        from cccc.kernel.pet_automation_proposals import build_pet_automation_proposal_candidates

        with tempfile.TemporaryDirectory() as tmp:
            group = _FakeGroup("g-demo", Path(tmp))
            ruleset = AutomationRuleSet.model_validate(
                {
                    "rules": [
                        {
                            "id": "pet-reply-followup-once",
                            "enabled": True,
                            "scope": "group",
                            "to": ["@foreman"],
                            "trigger": {"kind": "at", "at": "2026-03-27T00:00:00Z"},
                            "action": {
                                "kind": "notify",
                                "title": "Reply follow-up check",
                                "message": "old",
                            },
                        }
                    ],
                    "snippets": {},
                }
            )
            candidates = build_pet_automation_proposal_candidates(
                group,
                signal_payload={
                    "proposal_ready": {
                        "ready": True,
                        "focus": "reply_pressure",
                    }
                },
                ruleset=ruleset,
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["action"]["actions"][0]["type"], "update_rule")


if __name__ == "__main__":
    unittest.main()
