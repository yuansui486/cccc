from __future__ import annotations

from collections import deque
import gzip
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, NamedTuple, Optional

from ..contracts.v1 import Event
from ..contracts.v1.event import normalize_event_data
from ..util.fs import atomic_write_text
from ..util.file_lock import acquire_lockfile, release_lockfile
from .ledger_index import (
    _index_path_for_ledger,
    _read_event_from_source,
    append_event_to_index,
    catch_up_ledger_index,
)
from .ledger_segments import read_last_lines_across_sources


MAX_EVENT_BYTES = 256_000
MAX_CHAT_TEXT_BYTES = 32_000

AppendHook = Callable[[Dict[str, Any]], None]

_APPEND_HOOK: Optional[AppendHook] = None
LOGGER = logging.getLogger(__name__)
_EVENT_ONCE_ID_RE = re.compile(r"^gbs_[0-9a-f]{32}$")
_EVENT_ONCE_JOURNAL_NAME = "event-once.jsonl"
_EVENT_ONCE_RUNTIME: Dict[str, Dict[str, Any]] = {}


class LedgerEventConflictError(ValueError):
    """A deterministic event id is already bound to different facts."""


class LedgerEventSourceError(LedgerEventConflictError):
    """Ledger source bytes cannot support a deterministic absence proof."""


def set_append_hook(hook: Optional[AppendHook]) -> None:
    """Set a best-effort callback invoked after a successful append_event().

    This is intended for in-process observers (e.g., daemon streaming) and MUST
    NOT be used as a correctness dependency (the ledger file is the source of truth).
    """
    global _APPEND_HOOK
    _APPEND_HOOK = hook


def _notify_append(event: Dict[str, Any]) -> None:
    hook = _APPEND_HOOK
    if hook is None:
        return
    try:
        hook(event)
    except Exception:
        return


def _spill_text(group_dir: Path, *, event_id: str, text: str) -> Dict[str, Any]:
    raw = text or ""
    b = raw.encode("utf-8", errors="replace")
    rel = Path("state") / "ledger" / "blobs" / f"chat.{event_id}.txt"
    abs_path = group_dir / rel
    atomic_write_text(abs_path, raw.rstrip("\n") + "\n")
    return {
        "kind": "text",
        "path": str(rel),
        "bytes": len(b),
        "sha256": hashlib.sha256(b).hexdigest(),
    }


def _lock_path(ledger_path: Path) -> Path:
    return ledger_path.parent / "state" / "ledger" / "ledger.lock"


def _prepare_event(
    ledger_path: Path,
    *,
    kind: str,
    group_id: str,
    scope_key: str,
    by: str,
    data: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> tuple[Dict[str, Any], str]:
    payload = normalize_event_data(kind, data or {})
    event_args: Dict[str, Any] = {
        "kind": kind,
        "group_id": group_id,
        "scope_key": scope_key,
        "by": by,
        "data": payload,
    }
    if event_id is not None:
        event_args["id"] = event_id
    event = Event(**event_args)

    # Hard rules: keep the ledger small and stable. Large payloads belong in files referenced from the ledger.
    if kind == "chat.message":
        text = event.data.get("text")
        if isinstance(text, str):
            b = text.encode("utf-8", errors="replace")
            if len(b) > MAX_CHAT_TEXT_BYTES:
                att = _spill_text(ledger_path.parent, event_id=event.id, text=text)
                event.data["text"] = f"[onecolleague] (chat text stored at {att.get('path')})"
                attachments = event.data.get("attachments")
                if not isinstance(attachments, list):
                    attachments = []
                attachments.append(att)
                event.data["attachments"] = attachments

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    out = event.model_dump()
    line = json.dumps(out, ensure_ascii=False)
    if len(line.encode("utf-8", errors="replace")) > MAX_EVENT_BYTES:
        raise ValueError(f"ledger event too large (>{MAX_EVENT_BYTES} bytes): {kind}")
    return out, line


def _append_line_locked(ledger_path: Path, line: str) -> int:
    with ledger_path.open("a", encoding="utf-8") as f:
        start_offset = int(f.tell() or 0)
        encoded = (line + "\n").encode("utf-8", errors="replace")
        f.write(line + "\n")
        return start_offset + len(encoded)


def _finish_append(ledger_path: Path, event: Dict[str, Any], *, next_offset: int) -> None:
    try:
        append_event_to_index(ledger_path, event, next_offset_bytes=next_offset)
    except Exception:
        pass
    try:
        from .ledger_status_cache import update_message_status_cache_on_append

        update_message_status_cache_on_append(event)
    except Exception:
        pass
    _notify_append(event)


def _ledger_source_paths(ledger_path: Path) -> list[Path]:
    segment_dir = ledger_path.parent / "state" / "ledger" / "segments"
    selected: Dict[str, Path] = {}
    try:
        candidates = list(segment_dir.iterdir()) if segment_dir.exists() else []
    except OSError as exc:
        raise LedgerEventSourceError(f"failed to enumerate ledger sources: {segment_dir}") from exc
    for candidate in candidates:
        name = candidate.name
        if not name.startswith("ledger.") or not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
            continue
        try:
            if not candidate.is_file():
                continue
        except OSError as exc:
            raise LedgerEventSourceError(f"failed to inspect ledger source: {candidate}") from exc
        source_key = name[:-3] if name.endswith(".gz") else name
        previous = selected.get(source_key)
        if previous is None or name.endswith(".gz"):
            selected[source_key] = candidate
    sources = [selected[key] for key in sorted(selected)]
    try:
        if ledger_path.exists():
            sources.append(ledger_path)
    except OSError as exc:
        raise LedgerEventSourceError(f"failed to inspect active ledger source: {ledger_path}") from exc
    return sources


def _source_relative_path(ledger_path: Path, source_path: Path) -> str:
    try:
        return str(source_path.relative_to(ledger_path.parent))
    except ValueError as exc:
        raise LedgerEventSourceError(f"ledger source is outside group: {source_path}") from exc


def _find_event_in_ledger_sources(ledger_path: Path, event_id: str) -> Optional[Dict[str, Any]]:
    result = _scan_event_once_sources(ledger_path)
    found = result.events.get(event_id)
    return found[0] if found is not None else None


def _event_once_journal_path(ledger_path: Path) -> Path:
    return ledger_path.parent / "state" / "ledger" / _EVENT_ONCE_JOURNAL_NAME


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _source_snapshot(ledger_path: Path) -> list[Dict[str, Any]]:
    snapshot: list[Dict[str, Any]] = []
    for source_path in _ledger_source_paths(ledger_path):
        try:
            stat = source_path.stat()
        except OSError as exc:
            raise LedgerEventSourceError(f"failed to stat ledger source: {source_path}") from exc
        # ctime is the non-user-settable version signal for the process-crash
        # contract; size and mtime alone can be restored after an in-place edit.
        snapshot.append(
            {
                "path": str(source_path.relative_to(ledger_path.parent)),
                "device": int(stat.st_dev),
                "inode": int(stat.st_ino),
                "size": int(stat.st_size),
                "mtime_ns": int(getattr(stat, "st_mtime_ns", 0) or 0),
                "ctime_ns": int(getattr(stat, "st_ctime_ns", 0) or 0),
                "compressed": source_path.name.endswith(".gz"),
                "line_count": 0,
            }
        )
    return snapshot


def _source_snapshot_with_counts(ledger_path: Path, counts: Dict[str, int]) -> list[Dict[str, Any]]:
    snapshot = _source_snapshot(ledger_path)
    for item in snapshot:
        item["line_count"] = max(0, int(counts.get(str(item["path"]), 0)))
    return snapshot


def _source_change_kind(
    previous: Optional[list[Dict[str, Any]]], current: list[Dict[str, Any]]
) -> str:
    if previous is None:
        return "full"
    old_by_path = {str(item.get("path") or ""): item for item in previous}
    new_by_path = {str(item.get("path") or ""): item for item in current}
    if set(old_by_path) != set(new_by_path):
        return "full"
    delta = False
    for path, old in old_by_path.items():
        new = new_by_path[path]
        if bool(old.get("compressed")) or bool(new.get("compressed")) or path != "ledger.jsonl":
            if (
                old.get("device") != new.get("device")
                or old.get("ctime_ns") != new.get("ctime_ns")
                or old.get("inode") != new.get("inode")
                or old.get("size") != new.get("size")
                or old.get("mtime_ns") != new.get("mtime_ns")
                or old.get("compressed") != new.get("compressed")
            ):
                return "full"
            continue
        if (
            old.get("device") != new.get("device")
            or old.get("inode") != new.get("inode")
            or int(new.get("size") or 0) < int(old.get("size") or 0)
        ):
            return "full"
        if int(new.get("size") or 0) == int(old.get("size") or 0):
            if (
                old.get("mtime_ns") != new.get("mtime_ns")
                or old.get("ctime_ns") != new.get("ctime_ns")
            ):
                return "full"
        else:
            delta = True
    return "delta" if delta else "same"


def _parse_bounded_source_line(raw_line: bytes, source_path: Path, line_number: int) -> Optional[Dict[str, Any]]:
    if not raw_line.endswith(b"\n"):
        reason = "an oversized or unterminated line"
        if len(raw_line) <= MAX_EVENT_BYTES:
            reason = "an unterminated line"
        raise LedgerEventSourceError(f"ledger source has {reason}: {source_path}:{line_number}")
    line = raw_line[:-1].strip()
    if not line:
        return None
    try:
        event = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except Exception as exc:
        raise LedgerEventSourceError(f"ledger source has malformed JSON: {source_path}:{line_number}") from exc
    if type(event) is not dict:
        raise LedgerEventSourceError(f"ledger source event is not an object: {source_path}:{line_number}")
    return event


def _iter_bounded_source_records(
    ledger_path: Path,
    source_path: Path,
    *,
    start_offset: int = 0,
    start_line: int = 0,
) -> Iterable[tuple[Optional[Dict[str, Any]], Dict[str, Any]]]:
    compressed = source_path.name.endswith(".gz")
    opener = gzip.open if compressed else open
    try:
        with opener(source_path, "rb") as handle:
            if not compressed and start_offset:
                handle.seek(start_offset)
            line_number = max(0, int(start_line))
            offset = int(start_offset) if not compressed else 0
            while True:
                raw_line = handle.readline(MAX_EVENT_BYTES + 1)
                if not raw_line:
                    break
                line_number += 1
                event = _parse_bounded_source_line(raw_line, source_path, line_number)
                locator = {
                    "source_path": str(source_path.relative_to(ledger_path.parent)),
                    "line_no": line_number,
                    "offset_bytes": offset,
                    "length_bytes": len(raw_line),
                    "sha256": hashlib.sha256(raw_line).hexdigest(),
                }
                yield event, locator
                if not compressed:
                    offset += len(raw_line)
    except LedgerEventConflictError:
        raise
    except Exception as exc:
        raise LedgerEventSourceError(f"failed to read ledger source: {source_path}") from exc


class _EventOnceScanResult(NamedTuple):
    events: Dict[str, tuple[Dict[str, Any], Dict[str, Any]]]
    counts: Dict[str, int]
    duplicate_ids: set[str]


def _scan_event_once_sources(
    ledger_path: Path,
    *,
    source_paths: Optional[list[Path]] = None,
    start_offsets: Optional[Dict[str, tuple[int, int]]] = None,
) -> _EventOnceScanResult:
    events: Dict[str, tuple[Dict[str, Any], Dict[str, Any]]] = {}
    counts: Dict[str, int] = {}
    duplicate_ids: set[str] = set()
    paths = source_paths if source_paths is not None else _ledger_source_paths(ledger_path)
    for source_path in paths:
        source_key = _source_relative_path(ledger_path, source_path)
        start_offset, start_line = (start_offsets or {}).get(source_key, (0, 0))
        counts[source_key] = int(start_line)
        for event, locator in _iter_bounded_source_records(
            ledger_path,
            source_path,
            start_offset=int(start_offset),
            start_line=int(start_line),
        ):
            counts[source_key] = max(counts[source_key], int(locator.get("line_no") or 0))
            if event is None:
                continue
            identity = event.get("id")
            if not isinstance(identity, str) or _EVENT_ONCE_ID_RE.fullmatch(identity) is None:
                continue
            if identity in events:
                duplicate_ids.add(identity)
                continue
            events[identity] = (event, locator)
    return _EventOnceScanResult(events=events, counts=counts, duplicate_ids=duplicate_ids)


def _strict_json_object(pairs: list[tuple[str, object]]) -> Dict[str, object]:
    value: Dict[str, object] = {}
    for key, item in pairs:
        if type(key) is not str or key in value:
            raise ValueError("JSON object keys must be unique strings")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant is invalid: {value}")


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number is invalid")
    return parsed


def _json_values_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        if len(left) != len(right):
            return False
        return all(key in right and _json_values_equal(value, right[key]) for key, value in left.items())
    if type(left) is list:
        return len(left) == len(right) and all(
            _json_values_equal(left_value, right_value) for left_value, right_value in zip(left, right)
        )
    if left is None:
        return True
    if type(left) in (bool, int, str):
        return bool(left == right)
    if type(left) is float:
        return math.isfinite(left) and math.isfinite(right) and left == right
    return False


def _event_once_locator_equal(left: object, right: object) -> bool:
    return _json_values_equal(left, right)


def _event_once_journal_record(
    *,
    seq: int,
    previous_hash: str,
    operation: str,
    event: Dict[str, Any],
    locator: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], bytes]:
    body: Dict[str, Any] = {
        "seq": int(seq),
        "prev_hash": str(previous_hash),
        "op": operation,
        "event": event,
        "locator": locator,
    }
    record_hash = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()
    record = {**body, "record_hash": record_hash}
    raw = _canonical_json_bytes(record) + b"\n"
    if len(raw) > MAX_EVENT_BYTES:
        raise ValueError("event-once journal record is too large")
    return record, raw


def _event_once_apply_journal_record(
    entries: Dict[str, Dict[str, Any]],
    record: Dict[str, Any],
) -> None:
    operation = record.get("op")
    event = record.get("event")
    locator = record.get("locator")
    if operation not in {"reserve", "commit", "repair"} or type(event) is not dict:
        raise LedgerEventSourceError("event-once journal record has an invalid operation")
    event_id = event.get("id")
    if type(event_id) is not str or _EVENT_ONCE_ID_RE.fullmatch(event_id) is None:
        raise LedgerEventSourceError("event-once journal contains an invalid event id")
    if type(event.get("ts")) is not str or not event.get("ts"):
        raise LedgerEventSourceError("event-once journal event has no timestamp")
    existing = entries.get(event_id)
    if operation == "reserve":
        if locator is not None or existing is not None:
            raise LedgerEventSourceError("event-once journal has a duplicate reserve")
        entries[event_id] = {"status": "reserved", "event": event, "locator": None}
        return
    if type(locator) is not dict:
        raise LedgerEventSourceError("event-once journal commit has no locator")
    if existing is not None and (
        existing.get("status") != "reserved"
        or not _json_values_equal(existing.get("event"), event)
    ):
        raise LedgerEventSourceError("event-once journal has an invalid commit transition")
    if operation == "commit" and existing is None:
        raise LedgerEventSourceError("event-once journal commit has no reserve")
    if operation == "repair" and existing is not None:
        raise LedgerEventSourceError("event-once journal has a duplicate repair")
    entries[event_id] = {"status": "committed", "event": event, "locator": locator}


def _event_once_read_journal(
    journal_path: Path,
    *,
    start_offset: int = 0,
    initial_seq: int = 0,
    initial_hash: str = "",
    initial_entries: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    entries = dict(initial_entries or {})
    sequence = int(initial_seq)
    previous_hash = str(initial_hash)
    offset = int(start_offset)
    try:
        with journal_path.open("rb") as handle:
            if offset:
                handle.seek(offset)
            while True:
                raw = handle.readline(MAX_EVENT_BYTES + 1)
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    raise LedgerEventSourceError("event-once journal has an oversized or partial record")
                try:
                    record = json.loads(
                        raw[:-1].decode("utf-8"),
                        object_pairs_hook=_strict_json_object,
                        parse_constant=_reject_json_constant,
                        parse_float=_finite_json_float,
                    )
                except Exception as exc:
                    raise LedgerEventSourceError("event-once journal contains malformed JSON") from exc
                if type(record) is not dict:
                    raise LedgerEventSourceError("event-once journal record is not an object")
                if set(record) != {"seq", "prev_hash", "record_hash", "op", "event", "locator"}:
                    raise LedgerEventSourceError("event-once journal record has an invalid shape")
                if type(record.get("seq")) is not int or record["seq"] != sequence + 1:
                    raise LedgerEventSourceError("event-once journal sequence is not continuous")
                if type(record.get("prev_hash")) is not str or record["prev_hash"] != previous_hash:
                    raise LedgerEventSourceError("event-once journal hash chain is broken")
                supplied_hash = record.get("record_hash")
                body = {key: value for key, value in record.items() if key != "record_hash"}
                if type(supplied_hash) is not str or supplied_hash != hashlib.sha256(
                    _canonical_json_bytes(body)
                ).hexdigest():
                    raise LedgerEventSourceError("event-once journal record hash is invalid")
                _event_once_apply_journal_record(entries, record)
                sequence = int(record["seq"])
                previous_hash = supplied_hash
            offset = int(handle.tell() or 0)
    except LedgerEventSourceError:
        raise
    except Exception as exc:
        raise LedgerEventSourceError("failed to read event-once journal") from exc
    return {"seq": sequence, "head": previous_hash, "offset": offset, "entries": entries}


def _event_once_append_journal_locked(
    journal_path: Path,
    state: Dict[str, Any],
    *,
    operation: str,
    event: Dict[str, Any],
    locator: Optional[Dict[str, Any]],
) -> None:
    record, raw = _event_once_journal_record(
        seq=int(state.get("seq") or 0) + 1,
        previous_hash=str(state.get("head") or ""),
        operation=operation,
        event=event,
        locator=locator,
    )
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with journal_path.open("ab") as handle:
            handle.write(raw)
            handle.flush()
    except Exception as exc:
        raise LedgerEventSourceError("failed to append event-once journal") from exc
    _event_once_apply_journal_record(state["entries"], record)
    state["seq"] = int(record["seq"])
    state["head"] = str(record["record_hash"])
    state["offset"] = int(state.get("offset") or 0) + len(raw)


def _event_once_snapshot_with_counts(
    ledger_path: Path,
    counts: Dict[str, int],
) -> list[Dict[str, Any]]:
    snapshot = _source_snapshot(ledger_path)
    for item in snapshot:
        item["line_count"] = max(0, int(counts.get(str(item.get("path") or ""), 0)))
    return snapshot


def _event_once_rebuild_journal_locked(
    ledger_path: Path,
) -> Dict[str, Any]:
    journal_path = _event_once_journal_path(ledger_path)
    prior_journal: Optional[Dict[str, Any]] = None
    if journal_path.exists():
        try:
            prior_journal = _event_once_read_journal(journal_path)
        except LedgerEventSourceError:
            # A corrupt journal is only a cache failure; the bounded ledger scan
            # below is authoritative and will replace it.
            prior_journal = None
    scan = _scan_event_once_sources(ledger_path)
    found = scan.events
    counts = scan.counts
    fact_conflict_ids: set[str] = set()
    if prior_journal is not None:
        for event_id, entry in prior_journal.get("entries", {}).items():
            if entry.get("status") != "committed":
                continue
            source_entry = found.get(event_id)
            if source_entry is None:
                raise LedgerEventSourceError(
                    f"committed event is missing from ledger sources: {event_id}"
                )
            source_event = source_entry[0]
            journal_event = entry.get("event")
            source_facts = {key: value for key, value in source_event.items() if key != "ts"}
            journal_facts = {key: value for key, value in journal_event.items() if key != "ts"}
            if not _json_values_equal(source_facts, journal_facts):
                # A corrupted identity must remain permanently rejected, but it
                # must not prevent unrelated event-once identities from making
                # progress while the ledger is being repaired or inspected.
                fact_conflict_ids.add(event_id)
    temporary = journal_path.with_name(f"{journal_path.name}.rebuild.{os.getpid()}.{time.time_ns()}")
    state = {"seq": 0, "head": "", "offset": 0, "entries": {}}
    try:
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("wb") as handle:
            for event_id in sorted(found):
                event, locator = found[event_id]
                record, raw = _event_once_journal_record(
                    seq=int(state["seq"]) + 1,
                    previous_hash=str(state["head"]),
                    operation="repair",
                    event=event,
                    locator=locator,
                )
                handle.write(raw)
                _event_once_apply_journal_record(state["entries"], record)
                state["seq"] = int(record["seq"])
                state["head"] = str(record["record_hash"])
                state["offset"] += len(raw)
            handle.flush()
        os.replace(temporary, journal_path)
    finally:
        temporary.unlink(missing_ok=True)
    stat = journal_path.stat()
    state.update(
        {
            "journal_inode": int(stat.st_ino),
            "journal_device": int(stat.st_dev),
            "journal_size": int(stat.st_size),
            "journal_mtime_ns": int(getattr(stat, "st_mtime_ns", 0) or 0),
            "journal_ctime_ns": int(getattr(stat, "st_ctime_ns", 0) or 0),
            "sources": _event_once_snapshot_with_counts(ledger_path, counts),
            "duplicate_ids": set(scan.duplicate_ids),
            "fact_conflict_ids": fact_conflict_ids,
        }
    )
    return state


def _event_once_refresh_delta_locked(
    ledger_path: Path,
    state: Dict[str, Any],
    current_sources: list[Dict[str, Any]],
) -> None:
    old_by_path = {str(item.get("path") or ""): item for item in state.get("sources", [])}
    current_by_path = {str(item.get("path") or ""): item for item in current_sources}
    active = current_by_path.get("ledger.jsonl")
    if active is None:
        raise LedgerEventSourceError("active ledger source disappeared")
    source_path = ledger_path.parent / "ledger.jsonl"
    old_active = old_by_path.get("ledger.jsonl", {"size": 0, "line_count": 0})
    scan = _scan_event_once_sources(
        ledger_path,
        source_paths=[source_path],
        start_offsets={
            "ledger.jsonl": (
                int(old_active.get("size") or 0),
                int(old_active.get("line_count") or 0),
            )
        },
    )
    if scan.duplicate_ids:
        state.setdefault("duplicate_ids", set()).update(scan.duplicate_ids)
    for event_id, (event, locator) in scan.events.items():
        existing = state["entries"].get(event_id)
        if existing is None:
            _event_once_append_journal_locked(
                _event_once_journal_path(ledger_path),
                state,
                operation="repair",
                event=event,
                locator=locator,
            )
            continue
        if existing.get("status") == "reserved" and _json_values_equal(existing.get("event"), event):
            _event_once_append_journal_locked(
                _event_once_journal_path(ledger_path),
                state,
                operation="commit",
                event=event,
                locator=locator,
            )
            continue
        if (
            existing.get("status") == "committed"
            and _json_values_equal(existing.get("event"), event)
            and _event_once_locator_equal(existing.get("locator"), locator)
        ):
            # Another process may have appended both the journal commit and the
            # source line since this runtime snapshot. The suffix and delta are
            # two observations of that one commit, not duplicate source facts.
            continue
        state.setdefault("duplicate_ids", set()).add(event_id)
    state["sources"] = []
    for item in current_sources:
        path = str(item.get("path") or "")
        old = old_by_path.get(path)
        count = scan.counts.get(path, int(old.get("line_count") or 0) if old else 0)
        state["sources"].append({**item, "line_count": max(0, int(count))})


def _event_once_journal_covers_active_growth(
    previous_entries: Dict[str, Dict[str, Any]],
    current_entries: Dict[str, Dict[str, Any]],
    *,
    start_offset: int,
    end_offset: int,
) -> bool:
    ranges: list[tuple[int, int]] = []
    for event_id, entry in current_entries.items():
        if _json_values_equal(previous_entries.get(event_id), entry):
            continue
        locator = entry.get("locator")
        if entry.get("status") != "committed" or type(locator) is not dict:
            continue
        if locator.get("source_path") != "ledger.jsonl":
            continue
        offset = locator.get("offset_bytes")
        length = locator.get("length_bytes")
        if type(offset) is not int or type(length) is not int or length <= 0:
            return False
        ranges.append((offset, offset + length))
    cursor = int(start_offset)
    for range_start, range_end in sorted(ranges):
        if range_start != cursor or range_end <= range_start:
            return False
        cursor = range_end
    return cursor == int(end_offset)


def _event_once_ensure_runtime_locked(ledger_path: Path) -> Dict[str, Any]:
    key = str(ledger_path.resolve())
    state = _EVENT_ONCE_RUNTIME.get(key)
    if state is None:
        state = _event_once_rebuild_journal_locked(ledger_path)
        state["index_needs_repair"] = True
        _EVENT_ONCE_RUNTIME[key] = state
        return state
    journal_path = _event_once_journal_path(ledger_path)
    try:
        journal_stat = journal_path.stat()
        current_sources = _source_snapshot(ledger_path)
    except OSError:
        state = _event_once_rebuild_journal_locked(ledger_path)
        state["index_needs_repair"] = True
        _EVENT_ONCE_RUNTIME[key] = state
        return state
    if (
        int(journal_stat.st_dev) != int(state.get("journal_device") or 0)
        or int(journal_stat.st_ino) != int(state.get("journal_inode") or 0)
        or int(journal_stat.st_size) < int(state.get("journal_size") or 0)
        or (
            int(journal_stat.st_size) == int(state.get("journal_size") or 0)
            and (
                int(getattr(journal_stat, "st_mtime_ns", 0) or 0)
                != int(state.get("journal_mtime_ns") or 0)
                or int(getattr(journal_stat, "st_ctime_ns", 0) or 0)
                != int(state.get("journal_ctime_ns") or 0)
            )
        )
    ):
        state = _event_once_rebuild_journal_locked(ledger_path)
        state["index_needs_repair"] = True
        _EVENT_ONCE_RUNTIME[key] = state
        return state
    entries_before_suffix = dict(state.get("entries", {}))
    if int(journal_stat.st_size) > int(state.get("journal_size") or 0):
        try:
            suffix = _event_once_read_journal(
                journal_path,
                start_offset=int(state["offset"]),
                initial_seq=int(state["seq"]),
                initial_hash=str(state["head"]),
                initial_entries=state["entries"],
            )
        except LedgerEventSourceError:
            state = _event_once_rebuild_journal_locked(ledger_path)
            _EVENT_ONCE_RUNTIME[key] = state
            return state
        state.update(suffix)
    change = _source_change_kind(state.get("sources", []), current_sources)
    if change == "full":
        state = _event_once_rebuild_journal_locked(ledger_path)
        state["index_needs_repair"] = True
        _EVENT_ONCE_RUNTIME[key] = state
        return state
    if change == "delta":
        previous_active = next(
            (item for item in state.get("sources", []) if item.get("path") == "ledger.jsonl"),
            None,
        )
        previous_size = int((previous_active or {}).get("size") or 0)
        current_active = next(
            (item for item in current_sources if item.get("path") == "ledger.jsonl"),
            None,
        )
        current_size = int((current_active or {}).get("size") or 0)
        if not _event_once_journal_covers_active_growth(
            entries_before_suffix,
            state.get("entries", {}),
            start_offset=previous_size,
            end_offset=current_size,
        ):
            state = _event_once_rebuild_journal_locked(ledger_path)
            state["index_needs_repair"] = True
            _EVENT_ONCE_RUNTIME[key] = state
            return state
        if previous_size > 0:
            try:
                with (ledger_path.parent / "ledger.jsonl").open("rb") as handle:
                    handle.seek(previous_size - 1)
                    if handle.read(1) != b"\n":
                        state = _event_once_rebuild_journal_locked(ledger_path)
                        _EVENT_ONCE_RUNTIME[key] = state
                        return state
            except OSError as exc:
                raise LedgerEventSourceError("failed to verify active ledger frontier") from exc
        _event_once_refresh_delta_locked(ledger_path, state, current_sources)
        journal_stat = journal_path.stat()
    state["journal_inode"] = int(journal_stat.st_ino)
    state["journal_device"] = int(journal_stat.st_dev)
    state["journal_size"] = int(journal_stat.st_size)
    state["journal_mtime_ns"] = int(getattr(journal_stat, "st_mtime_ns", 0) or 0)
    state["journal_ctime_ns"] = int(getattr(journal_stat, "st_ctime_ns", 0) or 0)
    return state


def _event_once_note_ordinary_append(ledger_path: Path) -> None:
    state = _EVENT_ONCE_RUNTIME.get(str(ledger_path.resolve()))
    if state is None:
        return
    try:
        current = _source_snapshot(ledger_path)
    except Exception:
        state["sources"] = []
        return
    old_by_path = {str(item.get("path") or ""): item for item in state.get("sources", [])}
    for item in current:
        old = old_by_path.get(str(item.get("path") or ""))
        if old is not None:
            is_active_growth = (
                str(item.get("path") or "") == "ledger.jsonl"
                and int(item.get("size") or 0) > int(old.get("size") or 0)
            )
            item["line_count"] = int(old.get("line_count") or 0) + (
                1 if is_active_growth else 0
            )
    state["sources"] = current


def _event_once_verify_locator(
    ledger_path: Path,
    event: Dict[str, Any],
    locator: Dict[str, Any],
    verified_sources: list[Dict[str, Any]],
) -> bool:
    relative_path = str(locator.get("source_path") or "")
    verified_source = next(
        (item for item in verified_sources if item.get("path") == relative_path),
        None,
    )
    if verified_source is None:
        return False
    source_path = ledger_path.parent / relative_path
    target_line = int(locator.get("line_no") or 0)
    target_offset = int(locator.get("offset_bytes") or 0)

    def stat_matches(stat: os.stat_result) -> bool:
        return bool(
            int(stat.st_dev) == int(verified_source.get("device") or 0)
            and int(stat.st_ino) == int(verified_source.get("inode") or 0)
            and int(stat.st_size) == int(verified_source.get("size") or 0)
            and int(getattr(stat, "st_mtime_ns", 0) or 0)
            == int(verified_source.get("mtime_ns") or 0)
            and int(getattr(stat, "st_ctime_ns", 0) or 0)
            == int(verified_source.get("ctime_ns") or 0)
        )

    try:
        with source_path.open("rb") as handle:
            handle_stat = os.fstat(handle.fileno())
            if not stat_matches(handle_stat):
                return False
            path_stat = source_path.stat()
            if (
                not stat_matches(path_stat)
                or int(path_stat.st_dev) != int(handle_stat.st_dev)
                or int(path_stat.st_ino) != int(handle_stat.st_ino)
            ):
                return False
            if source_path.name.endswith(".gz"):
                # A cold/full rebuild already verified every decompressed record
                # and bound this locator into the journal hash chain. The open fd
                # pins that verified source without scanning from the gzip start.
                locator_hash = locator.get("sha256")
                return bool(
                    bool(verified_source.get("compressed"))
                    and 0 < target_line <= int(verified_source.get("line_count") or 0)
                    and target_offset == 0
                    and 0 < int(locator.get("length_bytes") or 0) <= MAX_EVENT_BYTES + 1
                    and type(locator_hash) is str
                    and re.fullmatch(r"[0-9a-f]{64}", locator_hash) is not None
                    and type(event) is dict
                )
            handle.seek(target_offset)
            raw = handle.readline(MAX_EVENT_BYTES + 1)
        if not raw.endswith(b"\n") or len(raw) != int(locator.get("length_bytes") or 0):
            return False
        source_event = _parse_bounded_source_line(raw, source_path, target_line)
        if source_event is None:
            return False
        source_locator = {
            "source_path": str(locator.get("source_path") or ""),
            "line_no": target_line,
            "offset_bytes": target_offset,
            "length_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        return _json_values_equal(source_event, event) and _event_once_locator_equal(source_locator, locator)
    except Exception:
        return False


def _repair_event_once_index(
    ledger_path: Path,
    event_id: str,
    event: Dict[str, Any],
) -> bool:
    """Refresh the derived index after replay without touching the source ledger."""
    try:
        index_path = _index_path_for_ledger(ledger_path)
        if not index_path.exists():
            indexed = None
        else:
            with sqlite3.connect(str(index_path), timeout=1.0) as conn:
                row = conn.execute(
                    "SELECT source_path, line_no, offset_bytes FROM events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
            indexed = None if row is None else _read_event_from_source(
                ledger_path.parent,
                source_path=str(row[0] or ""),
                line_no=int(row[1] or 0),
                offset_bytes=int(row[2] or 0),
            )
        if _json_values_equal(indexed, event):
            return True
        catch_up_ledger_index(ledger_path, force_rebuild=True)
        return True
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
        return False


def _force_repair_event_once_index(ledger_path: Path) -> bool:
    try:
        catch_up_ledger_index(ledger_path, force_rebuild=True)
        return True
    except Exception:
        return False


def append_event(
    ledger_path: Path,
    *,
    kind: str,
    group_id: str,
    scope_key: str,
    by: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out, line = _prepare_event(
        ledger_path,
        kind=kind,
        group_id=group_id,
        scope_key=scope_key,
        by=by,
        data=data,
    )
    lock = _lock_path(ledger_path)
    lk = acquire_lockfile(lock, blocking=True)
    try:
        if str(ledger_path.resolve()) in _EVENT_ONCE_RUNTIME:
            _event_once_ensure_runtime_locked(ledger_path)
        next_offset = _append_line_locked(ledger_path, line)
        _event_once_note_ordinary_append(ledger_path)
    finally:
        release_lockfile(lk)
    _finish_append(ledger_path, out, next_offset=next_offset)
    return out


def append_event_once(
    ledger_path: Path,
    *,
    event_id: str,
    kind: str,
    group_id: str,
    scope_key: str,
    by: str,
    data: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], bool]:
    """Append one deterministic Group Bridge event, or return the exact event."""

    identity = str(event_id or "").strip()
    if identity != event_id or _EVENT_ONCE_ID_RE.fullmatch(identity) is None:
        raise ValueError("event_id is invalid")
    expected_data = normalize_event_data(kind, data or {})
    expected_facts = {
        "v": 1,
        "id": identity,
        "kind": kind,
        "group_id": group_id,
        "scope_key": scope_key,
        "by": by,
        "data": expected_data,
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(ledger_path)
    lk = acquire_lockfile(lock, blocking=True)
    appended_event: Optional[Dict[str, Any]] = None
    replayed_event: Optional[Dict[str, Any]] = None
    replay_needs_index_repair = False
    next_offset = 0
    try:
        runtime = _event_once_ensure_runtime_locked(ledger_path)
        journal_path = _event_once_journal_path(ledger_path)
        replay_needs_index_repair = bool(runtime.get("index_needs_repair"))
        if identity in set(runtime.get("duplicate_ids") or set()):
            raise LedgerEventConflictError(f"ledger contains duplicate event id: {identity}")
        if identity in set(runtime.get("fact_conflict_ids") or set()):
            raise LedgerEventConflictError(f"ledger event id conflicts with persisted facts: {identity}")
        existing = runtime["entries"].get(identity)
        if existing is not None and existing["status"] == "reserved":
            runtime = _event_once_rebuild_journal_locked(ledger_path)
            _EVENT_ONCE_RUNTIME[str(ledger_path.resolve())] = runtime
            existing = runtime["entries"].get(identity)
        if existing is not None:
            existing_event = existing["event"]
            existing_facts = {key: value for key, value in existing_event.items() if key != "ts"}
            if not _json_values_equal(existing_facts, expected_facts):
                raise LedgerEventConflictError(f"ledger event id conflicts with persisted facts: {identity}")
            if existing["status"] == "committed":
                if _event_once_verify_locator(
                    ledger_path,
                    existing_event,
                    existing["locator"],
                    runtime.get("sources", []),
                ):
                    replayed_event = existing_event
                else:
                    replay_needs_index_repair = True
                    runtime = _event_once_rebuild_journal_locked(ledger_path)
                    _EVENT_ONCE_RUNTIME[str(ledger_path.resolve())] = runtime
                    existing = runtime["entries"].get(identity)
                    if existing is None:
                        raise LedgerEventSourceError(
                            f"committed event is missing from ledger sources: {identity}"
                        )
                    existing_event = existing["event"]
                    existing_facts = {key: value for key, value in existing_event.items() if key != "ts"}
                    if not _json_values_equal(existing_facts, expected_facts):
                        raise LedgerEventConflictError(f"ledger event id conflicts with persisted facts: {identity}")
                    if _event_once_verify_locator(
                        ledger_path,
                        existing_event,
                        existing["locator"],
                        runtime.get("sources", []),
                    ):
                        replayed_event = existing_event
                    else:
                        raise LedgerEventSourceError(
                            f"committed event locator cannot be verified: {identity}"
                        )
        if replayed_event is None:
            out, line = _prepare_event(
                ledger_path,
                event_id=identity,
                kind=kind,
                group_id=group_id,
                scope_key=scope_key,
                by=by,
                data=data,
            )
            _event_once_append_journal_locked(journal_path, runtime, operation="reserve", event=out, locator=None)
            next_offset = _append_line_locked(ledger_path, line)
            encoded = (line + "\n").encode("utf-8", errors="replace")
            active_line_count = 0
            for source in runtime.get("sources", []):
                if source.get("path") == "ledger.jsonl":
                    active_line_count = int(source.get("line_count") or 0)
                    break
            locator = {
                "source_path": "ledger.jsonl",
                "line_no": active_line_count + 1,
                "offset_bytes": max(0, next_offset - len(encoded)),
                "length_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
            _event_once_append_journal_locked(journal_path, runtime, operation="commit", event=out, locator=locator)
            runtime["sources"] = _source_snapshot_with_counts(ledger_path, {"ledger.jsonl": active_line_count + 1})
            journal_stat = journal_path.stat()
            runtime["journal_inode"] = int(journal_stat.st_ino)
            runtime["journal_device"] = int(journal_stat.st_dev)
            runtime["journal_size"] = int(journal_stat.st_size)
            runtime["journal_mtime_ns"] = int(getattr(journal_stat, "st_mtime_ns", 0) or 0)
            runtime["journal_ctime_ns"] = int(getattr(journal_stat, "st_ctime_ns", 0) or 0)
            _EVENT_ONCE_RUNTIME[str(ledger_path.resolve())] = runtime
            appended_event = out
    finally:
        release_lockfile(lk)
    if replayed_event is not None:
        if replay_needs_index_repair:
            if _repair_event_once_index(ledger_path, identity, replayed_event):
                runtime["index_needs_repair"] = False
        return replayed_event, True
    if appended_event is None:
        raise LedgerEventSourceError("event-once append did not produce an event")
    _finish_append(ledger_path, appended_event, next_offset=next_offset)
    if replay_needs_index_repair and _force_repair_event_once_index(ledger_path):
        runtime["index_needs_repair"] = False
    return appended_event, False


def read_last_lines(path: Path, n: int) -> list[str]:
    if n <= 0:
        return []
    if path.name == "ledger.jsonl" and (path.parent / "group.yaml").exists():
        try:
            return read_last_lines_across_sources(path.parent, n)
        except Exception as e:
            LOGGER.warning("failed to read ledger tail across sources: path=%s err=%s", path, e)
    try:
        if not path.exists():
            return []
        keep = deque(maxlen=n)
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.rstrip("\n")
                if line:
                    keep.append(line)
        return list(keep)
    except Exception as e:
        LOGGER.error("failed to read text tail: path=%s err=%s", path, e)
        return []


def follow(path: Path, *, sleep_seconds: float = 0.2) -> Iterable[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    inode = -1
    f = None

    def _open() -> None:
        nonlocal f, inode
        if f is not None:
            try:
                f.close()
            except Exception:
                pass
        f = path.open("r", encoding="utf-8", errors="replace")
        try:
            st = os.fstat(f.fileno())
            inode = int(getattr(st, "st_ino", -1) or -1)
        except Exception:
            inode = -1
        f.seek(0, 2)

    _open()
    assert f is not None

    while True:
        line = f.readline()
        if line:
            yield line.rstrip("\n")
            continue

        time.sleep(sleep_seconds)
        try:
            st = path.stat()
            cur_inode = int(getattr(st, "st_ino", -1) or -1)
            if inode != -1 and cur_inode != -1 and cur_inode != inode:
                _open()
                continue
            if st.st_size < f.tell():
                _open()
                continue
        except Exception:
            try:
                path.touch(exist_ok=True)
            except Exception:
                pass
            _open()
