import re
import unittest
from pathlib import Path

from pydantic import ValidationError


class TestEventKindModelParity(unittest.TestCase):
    def test_actor_delivery_failed_accepts_all_delivery_payload_shapes(self) -> None:
        from no1.contracts.v1.event import normalize_event_data

        common = {
            "actor_id": "peer1",
            "event_ids": ["evt1"],
            "accepted": None,
            "retryable": False,
            "reason": "submit_write_unknown",
            "error": "write was not confirmed",
        }
        accepted = {
            **common,
            "attempt_id": "attempt1",
            "generation": 3,
            "accepted": True,
            "reason": "grant_finalize_uncertain",
        }
        with_attempt = {**common, "attempt_id": "attempt2", "generation": 4}
        preamble = {key: value for key, value in common.items() if key not in {"accepted"}}

        self.assertEqual(normalize_event_data("actor.delivery.failed", accepted)["generation"], 3)
        self.assertEqual(normalize_event_data("actor.delivery.failed", with_attempt)["attempt_id"], "attempt2")
        self.assertIsNone(normalize_event_data("actor.delivery.failed", preamble)["generation"])

        with self.assertRaises(ValidationError):
            normalize_event_data("actor.delivery.failed", {**common, "unexpected": True})

    def test_standard_append_event_kinds_are_modeled(self) -> None:
        from no1.contracts.v1.event import _KIND_TO_MODEL

        repo_root = Path(__file__).resolve().parents[1]
        cli_file = repo_root / "src" / "no1" / "cli.py"
        cli_main_file = repo_root / "src" / "no1" / "cli" / "main.py"
        cli_source = cli_file if cli_file.exists() else cli_main_file
        files = [
            *Path(repo_root / "src" / "no1" / "daemon").glob("**/*.py"),
            *Path(repo_root / "src" / "no1" / "kernel").glob("**/*.py"),
            cli_source,
        ]

        pattern = re.compile(r'append_event\([^\)]*?kind\s*=\s*"([a-z0-9_.-]+)"', re.S)
        used_kinds = set()
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            used_kinds.update(pattern.findall(text))

        standard_kinds = {
            kind
            for kind in used_kinds
            if kind.startswith(("group.", "actor.", "chat.", "system.", "context."))
        }
        modeled = set(_KIND_TO_MODEL.keys())
        missing = sorted(standard_kinds - modeled)
        self.assertEqual(
            missing,
            [],
            msg=f"Standard append_event kinds missing from contracts.v1.event models: {', '.join(missing)}",
        )


if __name__ == "__main__":
    unittest.main()
