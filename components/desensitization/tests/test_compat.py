from __future__ import annotations

from pathlib import Path

import yaml

from cccc_desensitization import compat


def test_load_group_rejects_traversal_and_mismatched_document(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ONECOLLEAGUE_HOME", str(tmp_path))
    groups = tmp_path / "groups"
    groups.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "group.yaml").write_text("group_id: outside\n", encoding="utf-8")
    assert compat.load_group("../outside") is None

    group = groups / "group-a"
    group.mkdir()
    (group / "group.yaml").write_text(yaml.safe_dump({"group_id": "other"}), encoding="utf-8")
    assert compat.load_group("group-a") is None


def test_active_scope_does_not_fall_back_to_another_scope(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ONECOLLEAGUE_HOME", str(tmp_path))
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    group = compat.Group(
        "group-a",
        tmp_path / "groups" / "group-a",
        {"group_id": "group-a", "active_scope_key": "missing", "scopes": [
            {"scope_key": "first", "url": str(first)},
            {"scope_key": "second", "url": str(second)},
        ]},
    )
    assert compat.resolve_scope_root(group) is None

