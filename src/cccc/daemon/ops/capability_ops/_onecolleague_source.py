"""Fixed OneColleague skill library source integration.

This module intentionally supports one source only:
``onecolleague_skill_library`` served by done-hub ``agent-service``.
It keeps source sync/pending state separate from the existing capability
catalog and reuses ``handle_capability_import`` for the real import path.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin

from ....contracts.v1 import DaemonResponse
from ....util.fs import atomic_write_json, read_json
from ....util.time import utc_now_iso

from ._common import _CATALOG_LOCK, _capability_root, _error, _http_get_json_obj
from ._documents import _load_catalog_doc, _save_catalog_doc, _source_state_template
from ._handlers import _normalize_import_record, _refresh_source_record_counts, handle_capability_import
from ._skill_packages import ensure_codex_skill_package_installed, is_codex_skill_package_record

ONECOLLEAGUE_SOURCE_ID = "onecolleague_skill_library"
ONECOLLEAGUE_DEFAULT_BASE_URL = "http://dongdongkc.top:8012/api/v1/skill-library"
_SOURCE_LOCK = threading.RLock()
_HIGH_RISK_TAGS = {
    "computer_use",
    "desktop_control",
    "browser_automation",
    "filesystem_write",
    "terminal_exec",
    "trading",
    "payment_write",
    "payment",
}
_DIFF_FIELDS = (
    "kind",
    "name",
    "description_short",
    "source_uri",
    "source_record_version",
    "updated_at_source",
    "tags",
    "license",
    "trust_tier",
    "source_tier",
    "qualification_status",
    "qualification_reasons",
    "install_mode",
    "install_spec",
    "requirements",
    "capsule_text",
    "requires_capabilities",
)
_VOLATILE_RECORD_FIELDS = {"last_synced_at", "sync_state", "health_status"}
_HASH_FIELDS = {"content_hash", "checksum"}


def _source_doc_path():
    return _capability_root() / "onecolleague_skill_library_source.json"


def _pending_doc_path():
    return _capability_root() / "onecolleague_skill_library_pending.json"


def _new_source_doc() -> Dict[str, Any]:
    now = utc_now_iso()
    return {
        "v": 1,
        "created_at": now,
        "updated_at": now,
        "source": {
            "source_id": ONECOLLEAGUE_SOURCE_ID,
            "enabled": True,
            "base_url": ONECOLLEAGUE_DEFAULT_BASE_URL,
            "last_synced_at": "",
            "last_success_at": "",
            "last_error": "",
            "last_cursor": "",
            "updated_since": "",
            "last_summary": {},
        },
    }


def _normalize_source_doc(raw: Any) -> Dict[str, Any]:
    doc = dict(raw) if isinstance(raw, dict) else _new_source_doc()
    now = utc_now_iso()
    doc["v"] = 1
    if not str(doc.get("created_at") or "").strip():
        doc["created_at"] = now
    if not str(doc.get("updated_at") or "").strip():
        doc["updated_at"] = now
    source_raw = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    source = {
        "source_id": ONECOLLEAGUE_SOURCE_ID,
        "enabled": bool(source_raw.get("enabled", True)),
        "base_url": _normalize_base_url(source_raw.get("base_url")),
        "last_synced_at": str(source_raw.get("last_synced_at") or "").strip(),
        "last_success_at": str(source_raw.get("last_success_at") or "").strip(),
        "last_error": str(source_raw.get("last_error") or "").strip(),
        "last_cursor": str(source_raw.get("last_cursor") or "").strip(),
        "updated_since": str(source_raw.get("updated_since") or "").strip(),
        "last_summary": source_raw.get("last_summary") if isinstance(source_raw.get("last_summary"), dict) else {},
    }
    doc["source"] = source
    return doc


def _new_pending_doc() -> Dict[str, Any]:
    now = utc_now_iso()
    return {"v": 1, "created_at": now, "updated_at": now, "items": {}}


def _normalize_pending_doc(raw: Any) -> Dict[str, Any]:
    doc = dict(raw) if isinstance(raw, dict) else _new_pending_doc()
    now = utc_now_iso()
    doc["v"] = 1
    if not str(doc.get("created_at") or "").strip():
        doc["created_at"] = now
    if not str(doc.get("updated_at") or "").strip():
        doc["updated_at"] = now
    items_raw = doc.get("items")
    items: Dict[str, Dict[str, Any]] = {}
    if isinstance(items_raw, dict):
        for pending_id, item in items_raw.items():
            pid = str(pending_id or "").strip()
            if pid and isinstance(item, dict):
                row = dict(item)
                row["pending_id"] = pid
                items[pid] = row
    doc["items"] = items
    return doc


def _load_source_doc() -> Tuple[Any, Dict[str, Any]]:
    path = _source_doc_path()
    return path, _normalize_source_doc(read_json(path))


def _save_source_doc(path: Any, doc: Dict[str, Any]) -> None:
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(path, doc, indent=2)


def _load_pending_doc() -> Tuple[Any, Dict[str, Any]]:
    path = _pending_doc_path()
    return path, _normalize_pending_doc(read_json(path))


def _save_pending_doc(path: Any, doc: Dict[str, Any]) -> None:
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(path, doc, indent=2)


def _normalize_base_url(raw: Any) -> str:
    value = str(raw or "").strip() or ONECOLLEAGUE_DEFAULT_BASE_URL
    return value.rstrip("/")


def _source_result(source: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(source)
    out["subscription_link"] = str(out.get("base_url") or "")
    return out


def _join_url(base_url: str, rel: str, params: Optional[Dict[str, str]] = None) -> str:
    base = _normalize_base_url(base_url) + "/"
    url = urljoin(base, str(rel or "").lstrip("/"))
    if params:
        clean = {str(k): str(v) for k, v in params.items() if str(v or "").strip()}
        if clean:
            url = f"{url}?{urlencode(clean)}"
    return url


def _unwrap_payload(data: Any) -> Dict[str, Any]:
    if isinstance(data, dict) and data.get("ok") is True and isinstance(data.get("result"), dict):
        return dict(data.get("result") or {})
    return dict(data) if isinstance(data, dict) else {}


def _extract_items(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("items", "records", "capabilities", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [dict(x) for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = _extract_items(value)
            if nested:
                return nested
    return []


def _canonical_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in record.items() if k not in _VOLATILE_RECORD_FIELDS}


def _canonical_platform_hash(record: Dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k not in _HASH_FIELDS}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _validate_platform_hash(raw: Dict[str, Any], index_item: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    idx = index_item if isinstance(index_item, dict) else {}
    actual = _canonical_platform_hash(raw)
    record_hash = str(raw.get("content_hash") or "").strip()
    index_hash = str(idx.get("checksum") or idx.get("content_hash") or "").strip()
    errors: List[str] = []
    if not record_hash:
        errors.append("missing_record_content_hash")
    elif record_hash != actual:
        errors.append("record_content_hash_mismatch")
    if index_hash and record_hash and index_hash != record_hash:
        errors.append("index_checksum_mismatch")
    elif index_hash and index_hash != actual:
        errors.append("index_checksum_mismatch")
    return actual, errors


def _validate_pending_record_hash(record: Dict[str, Any], item: Dict[str, Any]) -> List[str]:
    expected = str(item.get("record_content_hash") or "").strip()
    if not expected:
        return []
    actual = "sha256:" + hashlib.sha256(
        json.dumps(_canonical_record(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if actual != expected:
        return ["pending_record_content_hash_mismatch"]
    return []


def _pending_record_hash(record: Dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(_canonical_record(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _records_equal(a: Optional[Dict[str, Any]], b: Dict[str, Any]) -> bool:
    if not isinstance(a, dict):
        return False
    return _canonical_record(a) == _canonical_record(b)


def _pending_id(capability_id: str, *, version: str = "", checksum: str = "", status: str = "") -> str:
    raw = "|".join([ONECOLLEAGUE_SOURCE_ID, str(capability_id or ""), str(version or ""), str(checksum or ""), str(status or "")])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _record_version(record: Dict[str, Any], index_item: Optional[Dict[str, Any]] = None) -> str:
    idx = index_item if isinstance(index_item, dict) else {}
    return (
        str(record.get("source_record_version") or "").strip()
        or str(idx.get("source_record_version") or "").strip()
        or str(record.get("updated_at_source") or "").strip()
        or str(idx.get("updated_at_source") or "").strip()
    )


def _record_checksum(record: Dict[str, Any], index_item: Optional[Dict[str, Any]] = None) -> str:
    idx = index_item if isinstance(index_item, dict) else {}
    checksum = str(record.get("checksum") or idx.get("checksum") or "").strip()
    if checksum:
        return checksum
    return hashlib.sha1(json.dumps(_canonical_record(record), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _diff_records(previous: Optional[Dict[str, Any]], record: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(previous, dict):
        return [{"field": "*", "kind": "new"}]
    diffs: List[Dict[str, Any]] = []
    for field in _DIFF_FIELDS:
        before = previous.get(field)
        after = record.get(field)
        if before != after:
            diffs.append({"field": field, "before": before, "after": after})
    return diffs


def _risk_for_change(previous: Optional[Dict[str, Any]], record: Dict[str, Any], diffs: List[Dict[str, Any]], index_item: Optional[Dict[str, Any]]) -> Tuple[str, List[str]]:
    reasons: List[str] = []
    kind = str(record.get("kind") or "").strip().lower()
    if kind == "mcp_toolpack":
        reasons.append("mcp_toolpack")
    tags = {str(x or "").strip().lower() for x in (record.get("tags") or []) if str(x or "").strip()}
    idx_tags = set()
    if isinstance(index_item, dict):
        idx_tags = {str(x or "").strip().lower() for x in (index_item.get("risk_tags") or []) if str(x or "").strip()}
    high_tags = sorted((tags | idx_tags).intersection(_HIGH_RISK_TAGS))
    if high_tags:
        reasons.extend([f"risk_tag:{tag}" for tag in high_tags])
    changed = {str(d.get("field") or "") for d in diffs if isinstance(d, dict)}
    for field in ("install_spec", "install_mode", "requirements"):
        if field in changed:
            reasons.append(f"{field}_changed")
    if "requires_capabilities" in changed and isinstance(previous, dict):
        before = set(previous.get("requires_capabilities") or [])
        after = set(record.get("requires_capabilities") or [])
        if after - before:
            reasons.append("requires_capabilities_added")
    sig_status = ""
    if isinstance(index_item, dict):
        sig_status = str(index_item.get("signature_status") or "").strip().lower()
    if sig_status and sig_status not in {"verified", "valid", "ok"}:
        reasons.append(f"signature:{sig_status}")
    return ("high" if reasons else "low"), reasons


def _normalize_platform_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    candidate = dict(raw)
    candidate["source_id"] = ONECOLLEAGUE_SOURCE_ID
    return _normalize_import_record(candidate)


def _http_get_platform(base_url: str, rel: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    # The OneColleague skill library read API is public by design; do not send Authorization.
    return _unwrap_payload(_http_get_json_obj(_join_url(base_url, rel, params=params), timeout=12.0))


def _fetch_platform_records(base_url: str, ids: List[str]) -> List[Dict[str, Any]]:
    if not ids:
        return []
    payload = _http_get_platform(base_url, "capabilities/records", {"ids": ",".join(ids)})
    rows = _extract_items(payload)
    if rows:
        return rows
    out: List[Dict[str, Any]] = []
    for cap_id in ids:
        try:
            row = _http_get_platform(base_url, f"capabilities/{cap_id}")
            if row:
                out.append(row)
        except Exception:
            continue
    return out


def _append_source_audit(action: str, details: Dict[str, Any]) -> str:
    action_id = f"ocsl_{uuid.uuid4().hex[:16]}"
    path = _capability_root() / "onecolleague_skill_library_audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "action_id": action_id,
        "ts": utc_now_iso(),
        "source_id": ONECOLLEAGUE_SOURCE_ID,
        "action": str(action or ""),
        "details": details if isinstance(details, dict) else {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return action_id


def _selected_pending_items(doc: Dict[str, Any], raw_ids: Any) -> List[Dict[str, Any]]:
    items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
    ids = [str(x or "").strip() for x in raw_ids] if isinstance(raw_ids, list) else []
    ids = [x for x in ids if x]
    if not ids:
        return [dict(x) for x in items.values() if isinstance(x, dict) and str(x.get("status") or "") not in {"ignored", "imported", "rolled_back"}]
    return [dict(items[x]) for x in ids if isinstance(items.get(x), dict)]


def handle_capability_source_config_get(args: Dict[str, Any]) -> DaemonResponse:
    try:
        with _SOURCE_LOCK:
            _, doc = _load_source_doc()
        return DaemonResponse(ok=True, result={"source": _source_result(doc["source"])})
    except Exception as e:
        return _error("capability_source_config_get_failed", str(e))


def handle_capability_source_config_update(args: Dict[str, Any]) -> DaemonResponse:
    try:
        with _SOURCE_LOCK:
            path, doc = _load_source_doc()
            source = dict(doc.get("source") or {})
            if "enabled" in args:
                source["enabled"] = bool(args.get("enabled"))
            source_url = args.get("base_url") if "base_url" in args else args.get("subscription_link")
            if source_url is not None:
                source["base_url"] = _normalize_base_url(source_url)
            doc["source"] = _normalize_source_doc({"source": source})["source"]
            _save_source_doc(path, doc)
        action_id = _append_source_audit("config_update", {"source": doc["source"], "by": str(args.get("by") or "")})
        return DaemonResponse(ok=True, result={"action_id": action_id, "source": _source_result(doc["source"])})
    except Exception as e:
        return _error("capability_source_config_update_failed", str(e))


def handle_capability_source_test(args: Dict[str, Any]) -> DaemonResponse:
    try:
        with _SOURCE_LOCK:
            _, doc = _load_source_doc()
        base_url = _normalize_base_url(args.get("base_url") or args.get("subscription_link") or doc["source"].get("base_url"))
        metadata = _http_get_platform(base_url, "source/metadata")
        source_id = str(metadata.get("source_id") or "").strip()
        if source_id and source_id != ONECOLLEAGUE_SOURCE_ID:
            return _error(
                "capability_source_test_failed",
                f"unexpected source_id: {source_id}",
                details={"expected_source_id": ONECOLLEAGUE_SOURCE_ID, "metadata": metadata},
            )
        action_id = _append_source_audit("test", {"ok": True, "base_url": base_url})
        return DaemonResponse(ok=True, result={"action_id": action_id, "source_id": ONECOLLEAGUE_SOURCE_ID, "base_url": base_url, "metadata": metadata})
    except Exception as e:
        _append_source_audit("test", {"ok": False, "error": str(e)})
        return _error("capability_source_test_failed", str(e))


def handle_capability_source_refresh(args: Dict[str, Any]) -> DaemonResponse:
    try:
        limit = max(1, min(int(args.get("limit") or 200), 1000))
    except Exception:
        return _error("capability_source_refresh_invalid", "invalid limit")
    try:
        with _SOURCE_LOCK:
            source_path, source_doc = _load_source_doc()
            source = dict(source_doc.get("source") or {})
            if not bool(source.get("enabled", True)):
                return _error("capability_source_disabled", "onecolleague skill library source is disabled")
            base_url = _normalize_base_url(args.get("base_url") or args.get("subscription_link") or source.get("base_url"))
            updated_since = str(args.get("updated_since") or source.get("updated_since") or source.get("last_success_at") or "").strip()

        index_payload = _http_get_platform(base_url, "capabilities/index", {"updated_since": updated_since, "limit": str(limit)})
        index_items = _extract_items(index_payload)
        ids_to_fetch: List[str] = []
        index_by_id: Dict[str, Dict[str, Any]] = {}
        deleted_items: List[Dict[str, Any]] = []
        for item in index_items:
            cap_id = str(item.get("capability_id") or "").strip()
            if not cap_id:
                continue
            index_by_id[cap_id] = item
            if bool(item.get("deleted")):
                deleted_items.append(item)
                continue
            if not ("capsule_text" in item or "install_spec" in item):
                ids_to_fetch.append(cap_id)

        raw_records = [dict(item) for item in index_items if not bool(item.get("deleted")) and ("capsule_text" in item or "install_spec" in item)]
        raw_records.extend(_fetch_platform_records(base_url, ids_to_fetch))

        records: Dict[str, Dict[str, Any]] = {}
        invalid: List[Dict[str, str]] = []
        for raw in raw_records:
            raw_cap_id = str(raw.get("capability_id") or "").strip()
            try:
                actual_hash, hash_errors = _validate_platform_hash(raw, index_by_id.get(raw_cap_id))
                if hash_errors:
                    invalid.append({"capability_id": raw_cap_id, "error": ",".join(hash_errors), "computed_hash": actual_hash})
                    continue
                rec = _normalize_platform_record(raw)
                records[str(rec.get("capability_id") or "")] = rec
            except Exception as e:
                invalid.append({"capability_id": raw_cap_id, "error": str(e)})

        with _CATALOG_LOCK:
            _, catalog_doc = _load_catalog_doc()
            catalog_rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}

        now = utc_now_iso()
        pending_path, pending_doc = _load_pending_doc()
        pending_items = pending_doc.get("items") if isinstance(pending_doc.get("items"), dict) else {}
        summary = {"new": 0, "updated": 0, "unchanged": 0, "deleted": 0, "deprecated": 0, "invalid": len(invalid), "pending": 0, "auto_imported": 0}
        auto_imported: List[Dict[str, Any]] = []

        for cap_id, record in records.items():
            previous = catalog_rows.get(cap_id) if isinstance(catalog_rows.get(cap_id), dict) else None
            index_item = index_by_id.get(cap_id, {})
            deprecated = bool(index_item.get("deprecated") or record.get("deprecated"))
            status = "deprecated" if deprecated else ("new" if not previous else ("unchanged" if _records_equal(previous, record) else "updated"))
            summary[status] = int(summary.get(status) or 0) + 1
            if status == "unchanged":
                continue
            if status == "new":
                try:
                    package_install: Dict[str, Any] = {}
                    if is_codex_skill_package_record(record):
                        package_install = ensure_codex_skill_package_installed(record)
                    with _CATALOG_LOCK:
                        catalog_path, catalog_doc = _load_catalog_doc()
                        rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
                        rows[cap_id] = record
                        catalog_doc["records"] = rows
                        sources = catalog_doc.get("sources") if isinstance(catalog_doc.get("sources"), dict) else {}
                        source_state = sources.get(ONECOLLEAGUE_SOURCE_ID) if isinstance(sources.get(ONECOLLEAGUE_SOURCE_ID), dict) else _source_state_template("never")
                        source_state["sync_state"] = "imported"
                        source_state["last_synced_at"] = now
                        source_state["staleness_seconds"] = 0
                        source_state["error"] = ""
                        sources[ONECOLLEAGUE_SOURCE_ID] = source_state
                        catalog_doc["sources"] = sources
                        _refresh_source_record_counts(catalog_doc)
                        _save_catalog_doc(catalog_path, catalog_doc)
                    for existing_item in pending_items.values():
                        if not isinstance(existing_item, dict):
                            continue
                        if str(existing_item.get("capability_id") or "").strip() != cap_id:
                            continue
                        if str(existing_item.get("status") or "").strip() != "new":
                            continue
                        existing_item["status"] = "imported"
                        existing_item["imported_at"] = now
                        existing_item["updated_at"] = now
                    payload: Dict[str, Any] = {"capability_id": cap_id, "kind": str(record.get("kind") or "")}
                    if package_install:
                        payload["package_install"] = package_install
                    auto_imported.append(payload)
                    summary["auto_imported"] += 1
                except Exception as e:
                    invalid.append({"capability_id": cap_id, "error": f"auto_import_failed:{str(e)}"})
                    summary["invalid"] = int(summary.get("invalid") or 0) + 1
                continue
            version = _record_version(record, index_item)
            checksum = _record_checksum(record, index_item)
            pid = _pending_id(cap_id, version=version, checksum=checksum, status=status)
            existing = pending_items.get(pid) if isinstance(pending_items.get(pid), dict) else {}
            if str(existing.get("status") or "") == "ignored" and str(existing.get("new_version") or "") == version:
                continue
            diffs = _diff_records(previous, record)
            risk_level, risk_reasons = _risk_for_change(previous, record, diffs, index_item)
            pending_items[pid] = {
                "pending_id": pid,
                "source_id": ONECOLLEAGUE_SOURCE_ID,
                "capability_id": cap_id,
                "kind": str(record.get("kind") or ""),
                "name": str(record.get("name") or cap_id),
                "status": status,
                "old_version": str(previous.get("source_record_version") or "") if isinstance(previous, dict) else "",
                "new_version": version,
                "checksum": checksum,
                "record_content_hash": _pending_record_hash(record),
                "risk_level": risk_level,
                "risk_reasons": risk_reasons,
                "requires_confirmation": True,
                "diff": diffs,
                "previous_record": dict(previous) if isinstance(previous, dict) else None,
                "record": record,
                "index_item": index_item,
                "created_at": str(existing.get("created_at") or now),
                "updated_at": now,
            }

        for item in deleted_items:
            cap_id = str(item.get("capability_id") or "").strip()
            previous = catalog_rows.get(cap_id) if isinstance(catalog_rows.get(cap_id), dict) else None
            if not cap_id or not previous:
                continue
            summary["deleted"] += 1
            version = str(item.get("source_record_version") or item.get("updated_at_source") or "").strip()
            checksum = str(item.get("checksum") or "").strip()
            pid = _pending_id(cap_id, version=version, checksum=checksum, status="deleted")
            pending_items[pid] = {
                "pending_id": pid,
                "source_id": ONECOLLEAGUE_SOURCE_ID,
                "capability_id": cap_id,
                "kind": str(previous.get("kind") or ""),
                "name": str(previous.get("name") or cap_id),
                "status": "deleted",
                "old_version": str(previous.get("source_record_version") or ""),
                "new_version": version,
                "checksum": checksum,
                "risk_level": "high",
                "risk_reasons": ["deleted_at_source"],
                "requires_confirmation": True,
                "diff": [{"field": "*", "kind": "deleted_at_source"}],
                "previous_record": dict(previous),
                "record": None,
                "index_item": item,
                "created_at": now,
                "updated_at": now,
            }

        pending_doc["items"] = pending_items
        summary["pending"] = len([x for x in pending_items.values() if isinstance(x, dict) and str(x.get("status") or "") not in {"ignored", "imported", "rolled_back"}])
        with _SOURCE_LOCK:
            _save_pending_doc(pending_path, pending_doc)
            source_path, source_doc = _load_source_doc()
            source = dict(source_doc.get("source") or {})
            source["base_url"] = base_url
            source["last_synced_at"] = now
            source["last_success_at"] = now
            source["last_error"] = ""
            source["last_cursor"] = str(index_payload.get("next_cursor") or index_payload.get("cursor") or "")
            source["updated_since"] = str(index_payload.get("server_time") or now)
            source["last_summary"] = summary
            source_doc["source"] = _normalize_source_doc({"source": source})["source"]
            _save_source_doc(source_path, source_doc)
        action_id = _append_source_audit("refresh", {"summary": summary, "invalid": invalid, "auto_imported": auto_imported})
        return DaemonResponse(ok=True, result={"action_id": action_id, "source": _source_result(source_doc["source"]), "summary": summary, "invalid": invalid, "auto_imported": auto_imported, "pending_count": summary["pending"]})
    except Exception as e:
        with _SOURCE_LOCK:
            path, doc = _load_source_doc()
            source = dict(doc.get("source") or {})
            source["last_synced_at"] = utc_now_iso()
            source["last_error"] = str(e)
            doc["source"] = _normalize_source_doc({"source": source})["source"]
            _save_source_doc(path, doc)
        _append_source_audit("refresh", {"ok": False, "error": str(e)})
        return _error("capability_source_refresh_failed", str(e))


def handle_capability_source_pending_list(args: Dict[str, Any]) -> DaemonResponse:
    try:
        status_filter = str(args.get("status") or "").strip()
        with _SOURCE_LOCK:
            _, doc = _load_pending_doc()
        items = [dict(x) for x in (doc.get("items") or {}).values() if isinstance(x, dict)]
        if status_filter:
            items = [x for x in items if str(x.get("status") or "") == status_filter]
        items.sort(key=lambda x: (str(x.get("status") or ""), str(x.get("name") or ""), str(x.get("pending_id") or "")))
        return DaemonResponse(ok=True, result={"items": items, "count": len(items)})
    except Exception as e:
        return _error("capability_source_pending_list_failed", str(e))


def handle_capability_source_pending_probe(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    by = str(args.get("by") or args.get("actor_id") or "user").strip() or "user"
    actor_id = str(args.get("actor_id") or by).strip() or by
    probe = bool(args.get("probe", True))
    try:
        with _SOURCE_LOCK:
            path, doc = _load_pending_doc()
            selected = _selected_pending_items(doc, args.get("pending_ids"))
        results: List[Dict[str, Any]] = []
        for item in selected:
            pid = str(item.get("pending_id") or "")
            record = item.get("record") if isinstance(item.get("record"), dict) else None
            if not record:
                results.append({"pending_id": pid, "ok": False, "error": {"code": "missing_record", "message": "pending item has no import record"}})
                continue
            resp = handle_capability_import(
                {
                    "group_id": group_id,
                    "by": by,
                    "actor_id": actor_id,
                    "record": record,
                    "dry_run": True,
                    "probe": probe,
                    "enable_after_import": False,
                    "scope": "session",
                    "reason": "onecolleague_skill_library_probe",
                }
            )
            payload = {"pending_id": pid, "ok": bool(resp.ok), "result": resp.result if resp.ok else None}
            if not resp.ok and resp.error is not None:
                payload["error"] = {"code": resp.error.code, "message": resp.error.message, "details": resp.error.details}
            results.append(payload)
        with _SOURCE_LOCK:
            path, doc = _load_pending_doc()
            items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
            for payload in results:
                pid = str(payload.get("pending_id") or "")
                if isinstance(items.get(pid), dict):
                    items[pid]["probe_result"] = payload
                    items[pid]["updated_at"] = utc_now_iso()
            doc["items"] = items
            _save_pending_doc(path, doc)
        action_id = _append_source_audit("probe", {"group_id": group_id, "count": len(results)})
        return DaemonResponse(ok=True, result={"action_id": action_id, "results": results})
    except Exception as e:
        return _error("capability_source_pending_probe_failed", str(e))


def handle_capability_source_pending_confirm(args: Dict[str, Any]) -> DaemonResponse:
    group_id = str(args.get("group_id") or "").strip()
    if not group_id:
        return _error("missing_group_id", "missing group_id")
    by = str(args.get("by") or args.get("actor_id") or "user").strip() or "user"
    actor_id = str(args.get("actor_id") or by).strip() or by
    try:
        with _SOURCE_LOCK:
            path, doc = _load_pending_doc()
            selected = _selected_pending_items(doc, args.get("pending_ids"))
        results: List[Dict[str, Any]] = []
        for item in selected:
            pid = str(item.get("pending_id") or "")
            record = item.get("record") if isinstance(item.get("record"), dict) else None
            if not record:
                results.append({"pending_id": pid, "ok": False, "error": {"code": "missing_record", "message": "pending item has no import record"}})
                continue
            hash_errors = _validate_pending_record_hash(record, item)
            if hash_errors:
                results.append(
                    {
                        "pending_id": pid,
                        "ok": False,
                        "error": {
                            "code": "pending_record_hash_mismatch",
                            "message": ",".join(hash_errors),
                            "details": {"hash_errors": hash_errors},
                        },
                    }
                )
                continue
            package_install: Dict[str, Any] = {}
            if is_codex_skill_package_record(record):
                try:
                    package_install = ensure_codex_skill_package_installed(record)
                except Exception as e:
                    results.append({"pending_id": pid, "ok": False, "error": {"code": "skill_package_install_failed", "message": str(e)}})
                    continue
            resp = handle_capability_import(
                {
                    "group_id": group_id,
                    "by": by,
                    "actor_id": actor_id,
                    "record": record,
                    "dry_run": False,
                    "probe": False,
                    "enable_after_import": False,
                    "scope": "session",
                    "reason": "onecolleague_skill_library_confirm",
                }
            )
            payload = {"pending_id": pid, "ok": bool(resp.ok), "result": resp.result if resp.ok else None}
            if not resp.ok and resp.error is not None:
                payload["error"] = {"code": resp.error.code, "message": resp.error.message, "details": resp.error.details}
            if package_install:
                payload["package_install"] = package_install
            results.append(payload)
        with _SOURCE_LOCK:
            path, doc = _load_pending_doc()
            items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
            for payload in results:
                pid = str(payload.get("pending_id") or "")
                if bool(payload.get("ok")) and isinstance(items.get(pid), dict):
                    items[pid]["status"] = "imported"
                    items[pid]["import_result"] = payload
                    items[pid]["imported_at"] = utc_now_iso()
                    items[pid]["updated_at"] = utc_now_iso()
            doc["items"] = items
            _save_pending_doc(path, doc)
        action_id = _append_source_audit("confirm", {"group_id": group_id, "count": len(results)})
        return DaemonResponse(ok=True, result={"action_id": action_id, "results": results})
    except Exception as e:
        return _error("capability_source_pending_confirm_failed", str(e))


def handle_capability_source_pending_ignore(args: Dict[str, Any]) -> DaemonResponse:
    reason = str(args.get("reason") or "").strip()
    try:
        with _SOURCE_LOCK:
            path, doc = _load_pending_doc()
            selected = _selected_pending_items(doc, args.get("pending_ids"))
            items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
            ignored: List[str] = []
            for item in selected:
                pid = str(item.get("pending_id") or "")
                if not isinstance(items.get(pid), dict):
                    continue
                items[pid]["status"] = "ignored"
                items[pid]["ignore_reason"] = reason
                items[pid]["ignored_at"] = utc_now_iso()
                items[pid]["updated_at"] = utc_now_iso()
                ignored.append(pid)
            doc["items"] = items
            _save_pending_doc(path, doc)
        action_id = _append_source_audit("ignore", {"count": len(ignored), "reason": reason})
        return DaemonResponse(ok=True, result={"action_id": action_id, "ignored": ignored, "count": len(ignored)})
    except Exception as e:
        return _error("capability_source_pending_ignore_failed", str(e))


def handle_capability_source_rollback(args: Dict[str, Any]) -> DaemonResponse:
    pending_id = str(args.get("pending_id") or "").strip()
    capability_id = str(args.get("capability_id") or "").strip()
    try:
        with _SOURCE_LOCK:
            pending_path, pending_doc = _load_pending_doc()
            items = pending_doc.get("items") if isinstance(pending_doc.get("items"), dict) else {}
            item = items.get(pending_id) if pending_id and isinstance(items.get(pending_id), dict) else None
            if item is None and capability_id:
                for candidate in items.values():
                    if isinstance(candidate, dict) and str(candidate.get("capability_id") or "") == capability_id:
                        item = candidate
                        pending_id = str(candidate.get("pending_id") or "")
                        break
            if not isinstance(item, dict):
                return _error("pending_update_not_found", "pending update not found")
            cap_id = str(item.get("capability_id") or "").strip()
            previous = item.get("previous_record") if isinstance(item.get("previous_record"), dict) else None
            with _CATALOG_LOCK:
                catalog_path, catalog_doc = _load_catalog_doc()
                rows = catalog_doc.get("records") if isinstance(catalog_doc.get("records"), dict) else {}
                if previous:
                    rows[cap_id] = previous
                    action = "restored_previous"
                else:
                    rows.pop(cap_id, None)
                    action = "removed_new_record"
                catalog_doc["records"] = rows
                sources = catalog_doc.get("sources") if isinstance(catalog_doc.get("sources"), dict) else {}
                state = sources.get(ONECOLLEAGUE_SOURCE_ID) if isinstance(sources.get(ONECOLLEAGUE_SOURCE_ID), dict) else _source_state_template("never")
                state["sync_state"] = "rolled_back"
                state["last_synced_at"] = utc_now_iso()
                sources[ONECOLLEAGUE_SOURCE_ID] = state
                catalog_doc["sources"] = sources
                _refresh_source_record_counts(catalog_doc)
                _save_catalog_doc(catalog_path, catalog_doc)
            items[pending_id]["status"] = "rolled_back"
            items[pending_id]["rollback_action"] = action
            items[pending_id]["rolled_back_at"] = utc_now_iso()
            pending_doc["items"] = items
            _save_pending_doc(pending_path, pending_doc)
        action_id = _append_source_audit("rollback", {"pending_id": pending_id, "capability_id": cap_id, "action": action})
        return DaemonResponse(ok=True, result={"action_id": action_id, "pending_id": pending_id, "capability_id": cap_id, "rollback_action": action})
    except Exception as e:
        return _error("capability_source_rollback_failed", str(e))
