from __future__ import annotations

from no1.daemon.ops.capability_ops._handlers import _normalize_import_record
from no1.daemon.ops.capability_ops._onecolleague_source import (
    _METADATA_DIFF_FIELDS,
    _diff_records,
    _normalize_platform_record,
)


def _record(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "capability_id": "skill:onecolleague:writer",
        "kind": "skill",
        "name": "Writer",
        "description_short": "Write documents",
        "capsule_text": "Use this skill to write documents.",
        "source_record_version": "1.0.0",
    }
    value.update(overrides)
    return value


def test_skill_metadata_is_kept_during_import_normalization() -> None:
    result = _normalize_import_record(
        _record(
            category_id="cat-office",
            category_slug="office",
            category_name="Office",
            category_order=3,
            display_order=8,
            icon_url="https://HOST/icons/hash.webp",
            icon_sha256="hash",
        )
    )

    assert result["category_slug"] == "office"
    assert result["category_name"] == "Office"
    assert result["category_order"] == 3
    assert result["display_order"] == 8
    assert result["icon_url"] == "https://HOST/icons/hash.webp"
    assert result["icon_sha256"] == "hash"


def test_platform_record_accepts_nested_category_and_icon_shape() -> None:
    result = _normalize_platform_record(
        _record(
            category={"id": "cat-office", "slug": "office", "name": "Office", "display_order": 2},
            icon={"url": "https://HOST/icons/hash.webp", "sha256": "hash"},
        )
    )

    assert result["category_id"] == "cat-office"
    assert result["category_order"] == 2
    assert result["icon_sha256"] == "hash"


def test_category_icon_and_order_changes_are_metadata_only() -> None:
    previous = _normalize_platform_record(_record(category_slug="office", display_order=1))
    current = _normalize_platform_record(
        _record(
            category_slug="automation",
            display_order=2,
            icon_url="https://HOST/icons/hash.webp",
        )
    )
    changed = {item["field"] for item in _diff_records(previous, current)}

    assert changed
    assert changed.issubset(_METADATA_DIFF_FIELDS)


def test_capsule_change_still_requires_content_confirmation() -> None:
    previous = _normalize_platform_record(_record())
    current = _normalize_platform_record(_record(capsule_text="Changed instructions"))
    changed = {item["field"] for item in _diff_records(previous, current)}

    assert "capsule_text" in changed
    assert not changed.issubset(_METADATA_DIFF_FIELDS)
