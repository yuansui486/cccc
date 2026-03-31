import unittest
from pathlib import Path

from cccc.ports.mcp.utils.help_markdown import (
    build_help_markdown,
    _select_help_markdown,
    parse_help_markdown,
    update_actor_help_note,
)

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "help_markdown_pet_roundtrip.md"


class TestHelpMarkdownPet(unittest.TestCase):
    def test_parse_help_markdown_reads_pet_block(self) -> None:
        markdown = """
Shared guidance.

## @pet

Keep the Web Pet low-noise.

## @actor: peer-1

Actor note.
""".strip()

        parsed = parse_help_markdown(markdown)
        self.assertEqual(str(parsed.get("pet") or ""), "Keep the Web Pet low-noise.")
        actor_notes = parsed.get("actor_notes") if isinstance(parsed.get("actor_notes"), dict) else {}
        self.assertEqual(str(actor_notes.get("peer-1") or ""), "Actor note.")

    def test_update_actor_help_note_preserves_pet_block(self) -> None:
        markdown = """
## @pet

Keep the Web Pet low-noise.

## @actor: peer-1

Old actor note.
""".strip()

        updated = update_actor_help_note(markdown, "peer-1", "New actor note.", ["peer-1"])
        parsed = parse_help_markdown(updated)
        actor_notes = parsed.get("actor_notes") if isinstance(parsed.get("actor_notes"), dict) else {}

        self.assertEqual(str(parsed.get("pet") or ""), "Keep the Web Pet low-noise.")
        self.assertEqual(str(actor_notes.get("peer-1") or ""), "New actor note.")

    def test_parse_help_markdown_recovers_inline_actor_note_text(self) -> None:
        markdown = """
## @actor: peer-1 first line
second line
""".strip()

        parsed = parse_help_markdown(markdown)
        actor_notes = parsed.get("actor_notes") if isinstance(parsed.get("actor_notes"), dict) else {}

        self.assertEqual(str(actor_notes.get("peer-1") or ""), "first line\nsecond line")

    def test_select_help_markdown_hides_pet_block_from_actor_playbooks(self) -> None:
        markdown = """
Common guidance.

## @pet

Pet-only persona.

## @actor: peer-1

Actor-only note.
""".strip()

        selected = _select_help_markdown(markdown, role="peer", actor_id="peer-1")
        self.assertIn("Common guidance.", selected)
        self.assertIn("Actor-only note.", selected)
        self.assertNotIn("Pet-only persona.", selected)

    def test_select_help_markdown_can_include_pet_block_for_pet_context(self) -> None:
        markdown = """
Common guidance.

## @pet

Pet-only persona.

## @actor: peer-1

Actor-only note.
""".strip()

        selected = _select_help_markdown(markdown, role="peer", actor_id="peer-1", include_pet=True)
        self.assertIn("Common guidance.", selected)
        self.assertIn("Actor-only note.", selected)
        self.assertIn("Pet-only persona.", selected)

    def test_roundtrip_fixture_matches_expected_shape(self) -> None:
        markdown = _FIXTURE.read_text(encoding="utf-8")

        parsed = parse_help_markdown(markdown)
        self.assertEqual(str(parsed.get("common") or ""), "Shared guidance.")
        self.assertEqual(str(parsed.get("foreman") or ""), "Foreman note.")
        self.assertEqual(str(parsed.get("peer") or ""), "Peer note.")
        self.assertEqual(str(parsed.get("pet") or ""), "Pet note.")
        actor_notes = parsed.get("actor_notes") if isinstance(parsed.get("actor_notes"), dict) else {}
        self.assertEqual(str(actor_notes.get("peer-1") or ""), "Actor note.")
        self.assertEqual(str(actor_notes.get("reviewer-1") or ""), "Reviewer note.")
        self.assertEqual(
            list(parsed.get("extra_tagged_blocks") or []),
            ["## @role: observer\n\nObserver note."],
        )

        rebuilt = build_help_markdown(
            common=str(parsed.get("common") or ""),
            foreman=str(parsed.get("foreman") or ""),
            peer=str(parsed.get("peer") or ""),
            pet=str(parsed.get("pet") or ""),
            actor_notes=actor_notes,
            actor_order=["peer-1", "reviewer-1"],
            extra_tagged_blocks=list(parsed.get("extra_tagged_blocks") or []),
        )
        self.assertEqual(rebuilt, markdown)


if __name__ == "__main__":
    unittest.main()
