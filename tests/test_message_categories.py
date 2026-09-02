from pathlib import Path

from no1.daemon.messaging.message_categories import actor_role_prompt, normalize_role_prompt_every


def test_normalize_role_prompt_every_bounds():
    assert normalize_role_prompt_every(-2) == 0
    assert normalize_role_prompt_every(5000) == 1000
    assert normalize_role_prompt_every("bad") == 5


def test_actor_role_prompt_reads_actor_note(tmp_path):
    class Group:
        path = Path(tmp_path)
        group_id = "g1"
        doc = {"title": "Team A", "actors": [{"id": "peer-1"}]}

    prompt = Group.path / "prompts" / "ONECOLLEAGUE_HELP.md"
    prompt.parent.mkdir()
    prompt.write_text("## @actor: peer-1\n\nFocus on tests.", encoding="utf-8")
    value = actor_role_prompt(Group(), "peer-1")
    assert value.startswith("[role] You are peer-1 (foreman) in group Team A.")
    assert value.endswith("Focus on tests.")
