from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import queue
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Iterable

import psutil
from starlette.datastructures import UploadFile

from .compat import resolve_scope_root, load_group
from .engine import engine_call, engine_context, group_engine_root, model_call, model_capabilities

SUPPORTED = {"txt", "md", "docx", "xlsx", "xlsm", "pdf", "png", "jpg", "jpeg", "bmp", "tif", "tiff"}
CHUNK_BYTES = 8 * 1024 * 1024
SCHEMA_VERSION = 2
TERMINAL_BATCH_STATUSES = {"completed", "partial", "failed", "cancelled"}
ACTIVE_BATCH_STATUSES = {"queued", "analyzing", "awaiting_review", "running"}

_batch_queue: queue.Queue[tuple[str, str]] = queue.Queue()
_queued: set[tuple[str, str]] = set()
_batch_secrets: dict[tuple[str, str], str] = {}
_queue_guard = threading.RLock()
_worker_started = False


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def limits() -> dict[str, int]:
    return {
        "max_file_bytes": _env_int("ONECOLLEAGUE_DESENSITIZATION_MAX_FILE_BYTES", 0),
        "max_batch_files": _env_int("ONECOLLEAGUE_DESENSITIZATION_MAX_BATCH_FILES", 0),
        "max_batch_bytes": _env_int("ONECOLLEAGUE_DESENSITIZATION_MAX_BATCH_BYTES", 0),
        "min_free_disk_bytes": _env_int("ONECOLLEAGUE_DESENSITIZATION_MIN_FREE_DISK_BYTES", 1024**3),
    }


def _safe_name(value: str) -> str:
    import re

    value = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip().replace("\x00", "")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    return value[:240] or "unnamed"


def _relative_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip("/")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." or "\x00" in part for part in parts):
        raise ValueError("invalid relative path")
    safe = [_safe_name(part) for part in parts]
    if any(part in {"", ".", ".."} for part in safe):
        raise ValueError("invalid relative path")
    return "/".join(safe)


def _contained(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    resolved.relative_to(root.resolve())
    return resolved


def _snapshot_roots(group_id: str) -> tuple[Path, Path, str, Path]:
    group = load_group(group_id)
    if group is None:
        raise ValueError("group not found")
    scope = resolve_scope_root(group_id)
    scope_key = str(group.doc.get("active_scope_key") or "") if scope is not None else ""
    base = scope if scope is not None else group.path
    output = _contained(base / "脱敏后", base)
    output.mkdir(parents=True, exist_ok=True)
    state = group.path / "state" / "desensitization"
    state.mkdir(parents=True, exist_ok=True)
    return state, output, scope_key, base.resolve()


def _state(group_id: str) -> Path:
    return _snapshot_roots(group_id)[0]


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _connection(group_id: str) -> sqlite3.Connection:
    state = _state(group_id)
    conn = sqlite3.connect(state / "desensitization.sqlite3", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
    if conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
        conn.execute("INSERT INTO schema_version(version) VALUES(0)")
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks(
      id TEXT PRIMARY KEY, group_id TEXT NOT NULL, batch_id TEXT DEFAULT '',
      original_name TEXT NOT NULL, relative_path TEXT DEFAULT '', kind TEXT NOT NULL,
      status TEXT NOT NULL, source_path TEXT DEFAULT '', artifact_path TEXT DEFAULT '',
      source_sha256 TEXT DEFAULT '', artifact_sha256 TEXT DEFAULT '',
      created REAL NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL,
      selected_count INTEGER DEFAULT 0, error_code TEXT DEFAULT '', error_message TEXT DEFAULT '')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS batches(
      id TEXT PRIMARY KEY, group_id TEXT NOT NULL, root_name TEXT DEFAULT '',
      status TEXT NOT NULL, total INTEGER DEFAULT 0, completed INTEGER DEFAULT 0,
      failed INTEGER DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
      output_path TEXT DEFAULT '', error_message TEXT DEFAULT '')""")
    _ensure_columns(conn, "tasks", {
        "output_root": "TEXT DEFAULT ''", "scope_key": "TEXT DEFAULT ''", "scope_root": "TEXT DEFAULT ''",
        "warnings_json": "TEXT DEFAULT '[]'", "report_path": "TEXT DEFAULT ''",
        "restored_path": "TEXT DEFAULT ''", "reversible": "INTEGER DEFAULT 0",
    })
    _ensure_columns(conn, "batches", {
        "scope_key": "TEXT DEFAULT ''", "scope_root": "TEXT DEFAULT ''", "mode": "TEXT DEFAULT 'auto'",
        "cancel_requested": "INTEGER DEFAULT 0", "report_path": "TEXT DEFAULT ''",
        "archive_path": "TEXT DEFAULT ''", "options_json": "TEXT DEFAULT '{}'",
        "idempotency_key": "TEXT DEFAULT ''",
    })
    conn.execute("""CREATE TABLE IF NOT EXISTS batch_items(
      batch_id TEXT NOT NULL, sequence INTEGER NOT NULL, relative_path TEXT NOT NULL,
      input_path TEXT DEFAULT '', task_id TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
      review_json TEXT DEFAULT '{}', error_code TEXT DEFAULT '', error_message TEXT DEFAULT '',
      created REAL NOT NULL, updated REAL NOT NULL,
      PRIMARY KEY(batch_id, sequence), FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE CASCADE)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_group_created ON tasks(group_id,created DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_batches_group_created ON batches(group_id,created DESC)")
    conn.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
    conn.commit()
    return conn


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_capacity(root: Path, incoming_bytes: int) -> None:
    configured = limits()
    max_file = configured["max_file_bytes"]
    if max_file and incoming_bytes > max_file:
        raise ValueError(f"file exceeds configured limit ({max_file} bytes)")
    free = shutil.disk_usage(root).free
    if free - incoming_bytes < configured["min_free_disk_bytes"]:
        raise ValueError("insufficient disk space")
    available = int(psutil.virtual_memory().available)
    if incoming_bytes > max(256 * 1024 * 1024, int(available * 0.75)):
        raise ValueError("file is too large for available memory")


def capabilities() -> dict[str, Any]:
    required = (
        ("presidio_analyzer", "presidio-analyzer"), ("presidio_anonymizer", "presidio-anonymizer"),
        ("PIL", "Pillow"), ("docx", "python-docx"), ("fitz", "PyMuPDF"),
        ("openpyxl", "openpyxl"), ("modelscope", "modelscope"), ("torch", "torch"),
        ("transformers", "transformers"), ("paddleocr", "paddleocr"), ("paddle", "paddlepaddle"),
    )
    missing = [package for module, package in required if importlib.util.find_spec(module) is None]
    enabled = os.getenv("ONECOLLEAGUE_DESENSITIZATION_ENABLED", "1").lower() not in {"0", "false", "no"}
    return {
        "available": enabled and not missing, "enabled": enabled, "missing_dependencies": missing,
        "error_code": "missing_dependencies" if missing else "",
        "install_command": "python -m pip install -e ." if missing else "",
        "supported_extensions": sorted(SUPPORTED), "chunk_bytes": CHUNK_BYTES, "limits": limits(),
        "schema_version": SCHEMA_VERSION,
    }


def model_status() -> dict[str, Any]:
    base = capabilities()
    if base["missing_dependencies"]:
        return {**base, "status": "error", "phase": "dependency_check", "retryable": False}
    try:
        status = model_capabilities()
        return {**base, **status, "phase": "ready" if status.get("status") == "ready" else "initializing"}
    except BaseException as exc:
        # Importing the model stack can briefly fail while its metadata and
        # native backends are being initialized.  Keep this state pollable so
        # the host UI does not turn a transient startup condition into a
        # permanent failure.  Actual missing dependencies are reported above.
        return {**base, "status": "starting", "phase": "model_load",
                "error_code": "model_metadata_unavailable", "error": str(exc), "retryable": True}


def initialize_models() -> dict[str, Any]:
    current = capabilities()
    if not current["enabled"]:
        raise ValueError("desensitization is disabled")
    if current["missing_dependencies"]:
        raise ValueError("missing desensitization dependencies")
    return dict(model_call("initialize"))


def model_job(job_id: str) -> dict[str, Any]:
    return dict(model_call("model_job", job_id))


def output_info(group_id: str) -> dict[str, Any]:
    state, output, scope_key, scope_root = _snapshot_roots(group_id)
    return {"output_dir": str(output), "state_dir": str(state), "scope_key": scope_key,
            "scope_root": str(scope_root), "writable": os.access(output, os.W_OK)}


def create_upload_path(group_id: str, filename: str) -> Path:
    root = _state(group_id) / "incoming"
    root.mkdir(parents=True, exist_ok=True)
    return _contained(root / f"{uuid.uuid4().hex}-{_safe_name(filename)}.part", root)


def write_stream(group_id: str, source: BinaryIO, filename: str) -> tuple[Path, int]:
    target = create_upload_path(group_id, filename)
    total = 0
    max_file = limits()["max_file_bytes"]
    try:
        with target.open("xb") as output:
            while True:
                chunk = source.read(CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if max_file and total > max_file:
                    raise ValueError(f"file exceeds configured limit ({max_file} bytes)")
                output.write(chunk)
        _validate_capacity(target.parent, total)
        return target, total
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _rewrite_urls(value: Any, group_id: str, task_id: str) -> Any:
    prefix = f"/api/v1/groups/{group_id}/desensitization/tasks/{task_id}"
    if isinstance(value, dict):
        return {key: _rewrite_urls(item, group_id, task_id) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_urls(item, group_id, task_id) for item in value]
    if isinstance(value, str):
        old = f"/api/tasks/{task_id}"
        return value.replace(old, prefix) if value.startswith(old) else value
    return value


def analyze_path(group_id: str, path: Path, filename: str, relative_path: str = "", *,
                 batch_id: str = "", output_root: Path | None = None, scope_key: str = "",
                 scope_root: Path | None = None) -> dict[str, Any]:
    filename = _safe_name(filename)
    safe_rel = _relative_path(relative_path or filename)
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED:
        raise ValueError(f"unsupported format: .{extension}")
    _validate_capacity(path.parent, path.stat().st_size)
    with path.open("rb") as stream:
        result = dict(engine_call(group_id, "files_analyze", UploadFile(file=stream, filename=filename)))
    task_id = str(result["analysis_id"])
    _, current_output, current_scope_key, current_scope_root = _snapshot_roots(group_id)
    final_output = (output_root or current_output).resolve()
    final_scope = (scope_root or current_scope_root).resolve()
    _contained(final_output, final_scope)
    now = time.time()
    conn = _connection(group_id)
    conn.execute("""INSERT OR REPLACE INTO tasks(
        id,group_id,batch_id,original_name,relative_path,kind,status,source_path,source_sha256,
        created,updated,expires,output_root,scope_key,scope_root,warnings_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (task_id, group_id, batch_id, filename, safe_rel, extension, "awaiting_review", "source.bin",
         str(result.get("sha256") or _hash_file(path)), now, now,
         now + _env_int("ONECOLLEAGUE_DESENSITIZATION_SOURCE_TTL_HOURS", 24) * 3600,
         str(final_output), scope_key or current_scope_key, str(final_scope),
         json.dumps(result.get("warnings") or [], ensure_ascii=False)))
    conn.commit(); conn.close()
    result["task_id"] = task_id
    return _image_preview_urls(_rewrite_urls(result, group_id, task_id), group_id, task_id)


def analyze_file(group_id: str, data: bytes, filename: str, relative_path: str = "") -> dict[str, Any]:
    path = create_upload_path(group_id, filename)
    try:
        path.write_bytes(data)
        return analyze_path(group_id, path, filename, relative_path)
    finally:
        path.unlink(missing_ok=True)


def _task_row(group_id: str, task_id: str) -> sqlite3.Row:
    conn = _connection(group_id)
    row = conn.execute("SELECT * FROM tasks WHERE id=? AND group_id=?", (task_id, group_id)).fetchone()
    conn.close()
    if row is None:
        raise ValueError("task not found")
    return row


def get_analysis(group_id: str, task_id: str) -> dict[str, Any]:
    _task_row(group_id, task_id)
    return _image_preview_urls(_rewrite_urls(dict(engine_call(group_id, "task_analysis", task_id)), group_id, task_id), group_id, task_id)


def _image_preview_urls(result: dict, group_id: str, task_id: str) -> dict:
    for item in result.get("embedded_images", []):
        item["source_preview"] = f"/api/v1/groups/{group_id}/desensitization/tasks/{task_id}/embedded-images/{item['image_id']}"
    return result


def embedded_image(group_id: str, task_id: str, image_id: str, preview_id: str = "") -> bytes:
    import io
    from docx import Document
    from .embedded_images import resources
    _task_row(group_id, task_id)
    analysis = get_analysis(group_id, task_id)
    if image_id not in {item["image_id"] for item in analysis.get("embedded_images", [])}:
        raise ValueError("image not found")
    with engine_context(group_id) as engine:
        if preview_id:
            _, path, record = engine._preview_record(task_id, preview_id)
            data = engine._preview_bytes(path, record)
        else:
            data = (engine._task_folder(task_id) / "source.bin").read_bytes()
        for key, part, _locations in resources(Document(io.BytesIO(data))):
            if key == image_id and part is not None:
                return engine._image_preview_png(part.blob)
    raise ValueError("image not found")


def analyze_text(group_id: str, text: str) -> list[dict[str, Any]]:
    return list(engine_call(group_id, "analyze", text))


def mask_text(group_id: str, text: str, entities: list[dict[str, Any]] | None = None,
              policies: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run the legacy quick-text workflow through the scoped task service."""
    analysis = analyze_file(group_id, text.encode("utf-8"), "text.txt")
    task_id = str(analysis["analysis_id"])
    effective = entities if entities is not None else analysis.get("entities", [])
    result = mask_file(group_id, task_id, effective, "text.txt", policies or {}, False, None, [], False)
    artifact, _, _ = task_file(group_id, task_id, "artifact")
    return {"task_id": task_id, "masked_text": artifact.read_text(encoding="utf-8"),
            "artifact_url": result.get("artifact_url"), "entities": effective}


def unmask_text(group_id: str, task_id: str, masked_text: str | None = None) -> dict[str, Any]:
    row = _task_row(group_id, task_id)
    source, _, _ = task_file(group_id, task_id, "source")
    original = source.read_text(encoding="utf-8")
    if masked_text is None:
        restored = original
    else:
        artifact_path, _, _ = task_file(group_id, task_id, "artifact")
        masked = artifact_path.read_text(encoding="utf-8")
        restored = masked_text.replace(masked, original) if masked else masked_text
    return {"task_id": task_id, "text": restored, "status": row["status"]}


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("output name collision limit exceeded")


def _copy_engine_artifact(group_id: str, task_id: str, engine_name: str, target: Path) -> Path:
    root = group_engine_root(group_id) / "tasks" / task_id
    source = _contained(root / Path(engine_name).name, root)
    if not source.is_file():
        raise ValueError("artifact not found")
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.tmp")
    shutil.copyfile(source, temp)
    os.replace(temp, target)
    return target


def mask_file(group_id: str, task_id: str, entities: list[dict[str, Any]] | None = None,
              filename: str = "", policies: dict[str, dict[str, Any]] | None = None,
              reversible: bool = False, password: str | None = None,
              boxes: list[dict[str, Any]] | None = None, image_override: bool = False,
              embedded_images: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    row = _task_row(group_id, task_id)
    payload = {
        "analysis_id": task_id, "filename": filename or row["original_name"], "entities": entities,
        "policies": policies or {}, "reviewed": True, "reversible": reversible,
        "password": password, "boxes": boxes, "image_override": image_override, "embedded_images": embedded_images,
    }
    with engine_context(group_id) as engine:
        result = dict(engine.files_mask(engine.FileMaskIn(**payload)))
    output_root = Path(row["output_root"]).resolve()
    scope_root = Path(row["scope_root"]).resolve()
    _contained(output_root, scope_root)
    if row["batch_id"]:
        result_dir = output_root
        relative_parent = Path(str(row["relative_path"])).parent
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(row["created"]))
        result_dir = output_root / f"单文件-{stamp}-{task_id[:8]}"
        relative_parent = Path()
    requested = _safe_name(filename or row["original_name"])
    target = _unique(result_dir / relative_parent / f"{Path(requested).stem}_脱敏{Path(requested).suffix}")
    engine_name = f"{Path(row['original_name']).stem}_masked.{row['kind']}"
    artifact = _copy_engine_artifact(group_id, task_id, engine_name, target)
    report_target = target.with_name(f"{target.stem}.report.json")
    _copy_engine_artifact(group_id, task_id, "report.json", report_target)
    conn = _connection(group_id)
    conn.execute("""UPDATE tasks SET status=?,warnings_json=?,artifact_path=?,report_path=?,artifact_sha256=?,updated=?,
        selected_count=?,reversible=?,error_code='',error_message='' WHERE id=? AND group_id=?""",
        (result.get("status", "completed"), json.dumps(result.get("warnings", [])), str(artifact), str(report_target), _hash_file(artifact), time.time(),
         int(result.get("selected_entity_count", 0)), int(reversible), task_id, group_id))
    conn.commit(); conn.close()
    return {
        **_rewrite_urls(result, group_id, task_id), "artifact": str(artifact),
        "artifact_url": f"/api/v1/groups/{group_id}/desensitization/tasks/{task_id}/artifact",
        "report_url": f"/api/v1/groups/{group_id}/desensitization/tasks/{task_id}/report",
    }


def preview_file(group_id: str, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = _task_row(group_id, task_id)
    request = {**payload, "analysis_id": task_id, "filename": payload.get("filename") or row["original_name"]}
    with engine_context(group_id) as engine:
        result = dict(engine.files_preview(engine.FilePreviewIn(**request)))
    return _rewrite_urls(result, group_id, task_id)


def restore_file(group_id: str, task_id: str, password: str) -> dict[str, Any]:
    row = _task_row(group_id, task_id)
    with engine_context(group_id) as engine:
        result = dict(engine.restore_file(task_id, engine.RestoreFileIn(password=password)))
    engine_name = Path(str(result["artifact"])).name
    output_root = Path(row["output_root"])
    _contained(output_root, Path(row["scope_root"]))
    restored = _copy_engine_artifact(
        group_id, task_id, engine_name,
        _unique(output_root / f"{Path(row['original_name']).stem}_恢复{Path(row['original_name']).suffix}"))
    conn = _connection(group_id)
    conn.execute("UPDATE tasks SET restored_path=?,updated=? WHERE id=? AND group_id=?",
                 (str(restored), time.time(), task_id, group_id))
    conn.commit(); conn.close()
    return {"task_id": task_id, "artifact_url": f"/api/v1/groups/{group_id}/desensitization/tasks/{task_id}/restored"}


def list_tasks(group_id: str, limit: int = 50, offset: int = 0, status: str = "",
               kind: str = "", query: str = "") -> dict[str, Any]:
    clauses = ["group_id=?"]
    values: list[Any] = [group_id]
    if status:
        clauses.append("status=?"); values.append(status)
    if kind:
        clauses.append("kind=?"); values.append(kind)
    if query:
        clauses.append("(original_name LIKE ? OR relative_path LIKE ?)")
        values.extend([f"%{query}%", f"%{query}%"])
    where = " AND ".join(clauses)
    conn = _connection(group_id)
    total = int(conn.execute(f"SELECT COUNT(*) FROM tasks WHERE {where}", values).fetchone()[0])
    rows = conn.execute(f"""SELECT id,batch_id,original_name,relative_path,kind,status,artifact_path,
        report_path,restored_path,reversible,created,updated,error_code,error_message
        FROM tasks WHERE {where} ORDER BY created DESC LIMIT ? OFFSET ?""",
        [*values, limit, offset]).fetchall()
    conn.close()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def delete_task(group_id: str, task_id: str) -> dict[str, Any]:
    row = _task_row(group_id, task_id)
    if row["batch_id"]:
        batch = get_batch(group_id, str(row["batch_id"]), include_items=False)
        if batch["status"] in ACTIVE_BATCH_STATUSES:
            raise ValueError("batch is still active")
    for key in ("artifact_path", "report_path", "restored_path"):
        if row[key]:
            _contained(Path(row[key]), Path(row["output_root"])).unlink(missing_ok=True)
    shutil.rmtree(group_engine_root(group_id) / "tasks" / task_id, ignore_errors=True)
    conn = _connection(group_id)
    conn.execute("DELETE FROM tasks WHERE id=? AND group_id=?", (task_id, group_id))
    conn.commit(); conn.close()
    return {"deleted": task_id}


def task_file(group_id: str, task_id: str, kind: str) -> tuple[Path, str, str]:
    row = _task_row(group_id, task_id)
    if kind == "source":
        root = group_engine_root(group_id) / "tasks" / task_id
        path = _contained(root / "source.bin", root)
        disposition = "inline"
    else:
        column = {"artifact": "artifact_path", "report": "report_path", "restored": "restored_path"}.get(kind)
        if not column:
            raise ValueError("artifact not found")
        path = _contained(Path(row[column]), Path(row["output_root"]))
        disposition = "attachment"
    if not path.is_file():
        raise ValueError("artifact not found")
    name = row["original_name"] if kind == "source" else path.name
    return path, str(name), disposition


def preview_file_path(group_id: str, task_id: str, preview_id: str, variant: str,
                      page: int = 0) -> tuple[Path | None, bytes | None, str]:
    _task_row(group_id, task_id)
    with engine_context(group_id) as engine:
        filename, output, metadata = engine._preview_record(task_id, preview_id)
        data = engine._preview_bytes(output, metadata)
        if variant == "image":
            return None, engine._image_preview_png(data), "image/png"
        if variant == "page":
            return None, engine._pdf_page_png(data, page), "image/png"
        media = engine._SOURCE_MEDIA_TYPES.get(engine.extension(filename)) or "application/octet-stream"
        return Path(output), None, media


def source_page(group_id: str, task_id: str, variant: str, page: int = 0) -> tuple[bytes, str]:
    _task_row(group_id, task_id)
    with engine_context(group_id) as engine:
        _row, path, _filename = engine._task_source(task_id)
        data = path.read_bytes()
        if variant == "image":
            return engine._image_preview_png(data), "image/png"
        return engine._pdf_page_png(data, page), "image/png"


def _legacy_crud(group_id: str, method: str, payload: dict[str, Any] | None = None,
                 *args: Any) -> dict[str, Any]:
    with engine_context(group_id) as engine:
        if method == "add_custom_sensitive":
            return dict(engine.add_custom_sensitive(engine.CustomSensitiveIn(**(payload or {}))))
        if method == "update_custom_sensitive":
            return dict(engine.update_custom_sensitive(str(args[0]), engine.CustomSensitiveIn(**(payload or {}))))
        if method == "add_rule":
            return dict(engine.add_rule(engine.RuleIn(**(payload or {}))))
        if method == "update_rule":
            return dict(engine.update_rule(str(args[0]), engine.RulePatch(**(payload or {}))))
        if method == "add_rule_category":
            return dict(engine.add_rule_category(engine.RuleCategoryIn(**(payload or {}))))
        if method == "rename_rule_category":
            return dict(engine.rename_rule_category(str(args[0]), engine.RuleCategoryPatch(**(payload or {}))))
        return dict(getattr(engine, method)(*args))


def list_rules(group_id: str, category: str = "", scope: str = "", query: str = "") -> dict[str, Any]:
    return _legacy_crud(group_id, "rules", None, category or None, scope or None, query or None)


def create_rule(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _legacy_crud(group_id, "add_rule", payload)


def update_rule(group_id: str, rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _legacy_crud(group_id, "update_rule", payload, rule_id)


def delete_rule(group_id: str, rule_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "remove_rule", None, rule_id)


def toggle_builtin_rule(group_id: str, rule_id: str, enabled: bool) -> dict[str, Any]:
    return _legacy_crud(group_id, "toggle_builtin", None, rule_id, {"enabled": enabled})


def toggle_model_rule(group_id: str, enabled: bool) -> dict[str, Any]:
    return _legacy_crud(group_id, "toggle_model", None, {"enabled": enabled})


def list_rule_categories(group_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "list_rule_categories")


def create_rule_category(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _legacy_crud(group_id, "add_rule_category", payload)


def rename_rule_category(group_id: str, category_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _legacy_crud(group_id, "rename_rule_category", payload, category_id)


def delete_rule_category(group_id: str, category_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "delete_rule_category", None, category_id)


def get_policies(group_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "get_policies")


def put_policies(group_id: str, payload: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return _legacy_crud(group_id, "put_policies", None, payload)


def reset_policies(group_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "reset_policies")


def entity_directory(group_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "entity_directory")


def list_custom_sensitive(group_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "list_custom_sensitive")


def create_custom_sensitive(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _legacy_crud(group_id, "add_custom_sensitive", payload)


def update_custom_sensitive(group_id: str, rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _legacy_crud(group_id, "update_custom_sensitive", payload, rule_id)


def delete_custom_sensitive(group_id: str, rule_id: str) -> dict[str, Any]:
    return _legacy_crud(group_id, "delete_custom_sensitive", None, rule_id)


def create_batch_from_paths(group_id: str, files: Iterable[tuple[str, Path]], root_name: str = "",
                            mode: str = "auto", options: dict[str, Any] | None = None,
                            idempotency_key: str = "") -> dict[str, Any]:
    entries = [(_relative_path(rel), Path(path)) for rel, path in files]
    if not entries:
        raise ValueError("batch requires at least one file")
    configured = limits()
    if configured["max_batch_files"] and len(entries) > configured["max_batch_files"]:
        raise ValueError("batch file count exceeds configured limit")
    normalized = [rel.casefold() for rel, _ in entries]
    if len(set(normalized)) != len(normalized):
        raise ValueError("batch contains duplicate relative paths")
    total_bytes = sum(path.stat().st_size for _, path in entries)
    if configured["max_batch_bytes"] and total_bytes > configured["max_batch_bytes"]:
        raise ValueError("batch size exceeds configured limit")
    if mode not in {"auto", "review"}:
        raise ValueError("invalid batch mode")
    public_options = dict(options or {})
    password = str(public_options.pop("password", "") or "")
    if public_options.get("reversible") and not password:
        raise ValueError("reversible mode requires a password")
    state, output, scope_key, scope_root = _snapshot_roots(group_id)
    conn = _connection(group_id)
    if idempotency_key:
        existing = conn.execute("SELECT id FROM batches WHERE group_id=? AND idempotency_key=?",
                                (group_id, idempotency_key)).fetchone()
        if existing:
            conn.close()
            return get_batch(group_id, str(existing[0]))
    batch_id = uuid.uuid4().hex
    relative_output = public_options.get("output_relative_path")
    if relative_output is not None:
        try:
            if public_options.get("scope_key") != scope_key or public_options.get("scope_root") != str(scope_root):
                raise ValueError("working directory changed; confirm output again")
            parts = str(relative_output).replace("\\", "/").split("/")
            if any(not part or part in {".", ".."} or _safe_name(part) != part
                   or part.endswith((".", " ")) or Path(part).is_reserved() for part in parts):
                raise ValueError("invalid output relative path")
            batch_output = _contained(scope_root.joinpath(*parts), scope_root)
            if batch_output.exists():
                raise ValueError("output directory already exists")
        except Exception:
            conn.close()
            raise
    else:
        batch_output = output / f"批次-{time.strftime('%Y%m%d-%H%M%S')}-{batch_id[:8]}"
    try:
        batch_output.mkdir(parents=True, exist_ok=False)
    except OSError:
        conn.close()
        raise
    input_root = state / "batches" / batch_id / "inputs"
    input_root.mkdir(parents=True, exist_ok=False)
    now = time.time()
    conn.execute("""INSERT INTO batches(id,group_id,root_name,status,total,created,updated,output_path,
        scope_key,scope_root,mode,options_json,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (batch_id, group_id, _safe_name(root_name) if root_name else "", "queued", len(entries), now, now,
         str(batch_output), scope_key, str(scope_root), mode, json.dumps(public_options, ensure_ascii=False), idempotency_key))
    try:
        for index, (rel, source) in enumerate(entries):
            target = _contained(input_root / rel, input_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            conn.execute("""INSERT INTO batch_items(batch_id,sequence,relative_path,input_path,status,created,updated)
                VALUES(?,?,?,?,?,?,?)""", (batch_id, index, rel, str(target), "queued", now, now))
        conn.commit()
    except Exception:
        conn.rollback()
        shutil.rmtree(input_root.parent, ignore_errors=True)
        shutil.rmtree(batch_output, ignore_errors=True)
        raise
    finally:
        conn.close()
    if password:
        _batch_secrets[(group_id, batch_id)] = password
    _enqueue(group_id, batch_id)
    return get_batch(group_id, batch_id)


def enqueue_batch(group_id: str, files: list[tuple[str, bytes]], root_name: str = "",
                  mode: str = "auto") -> dict[str, Any]:
    staged: list[tuple[str, Path]] = []
    try:
        for rel, data in files:
            path = create_upload_path(group_id, rel)
            path.write_bytes(data)
            staged.append((rel, path))
        return create_batch_from_paths(group_id, staged, root_name, mode)
    except Exception:
        for _, path in staged:
            path.unlink(missing_ok=True)
        raise


def create_batch(group_id: str, files: list[tuple[str, bytes]], root_name: str = "") -> dict[str, Any]:
    return enqueue_batch(group_id, files, root_name, "auto")


def _enqueue(group_id: str, batch_id: str) -> None:
    global _worker_started
    key = (group_id, batch_id)
    with _queue_guard:
        if key not in _queued:
            _queued.add(key)
            _batch_queue.put(key)
        if not _worker_started:
            threading.Thread(target=_worker_loop, daemon=True, name="desensitization-worker").start()
            _worker_started = True


def recover_pending(group_id: str) -> None:
    conn = _connection(group_id)
    rows = conn.execute("""SELECT id FROM batches WHERE group_id=?
        AND status IN ('queued','analyzing','running')""", (group_id,)).fetchall()
    conn.execute("""UPDATE batches SET status='queued',updated=? WHERE group_id=?
        AND status IN ('analyzing','running')""", (time.time(), group_id))
    conn.commit(); conn.close()
    for row in rows:
        _enqueue(group_id, str(row["id"]))


def _worker_loop() -> None:
    while True:
        key = _batch_queue.get()
        try:
            _run_batch(*key)
        except Exception as exc:
            group_id, batch_id = key
            try:
                conn = _connection(group_id)
                conn.execute("""UPDATE batches SET status='failed',error_message=?,updated=?
                    WHERE id=? AND group_id=?""", (str(exc)[:1000], time.time(), batch_id, group_id))
                conn.commit(); conn.close()
            except Exception:
                pass
        finally:
            with _queue_guard:
                _queued.discard(key)
            _batch_queue.task_done()


def _run_batch(group_id: str, batch_id: str) -> None:
    conn = _connection(group_id)
    batch = conn.execute("SELECT * FROM batches WHERE id=? AND group_id=?", (batch_id, group_id)).fetchone()
    if batch is None or batch["status"] not in {"queued", "analyzing", "running"}:
        conn.close()
        return
    options = json.loads(batch["options_json"] or "{}")
    if options.get("reversible"):
        password = _batch_secrets.get((group_id, batch_id), "")
        if not password:
            conn.execute("UPDATE batches SET status='failed',error_message='reversible password required after restart',updated=? WHERE id=?", (time.time(), batch_id))
            conn.commit(); conn.close()
            return
        options["password"] = password
    items = conn.execute("SELECT * FROM batch_items WHERE batch_id=? ORDER BY sequence", (batch_id,)).fetchall()
    conn.execute("UPDATE batches SET status='analyzing',updated=? WHERE id=?", (time.time(), batch_id))
    conn.commit()
    for item in items:
        if conn.execute("SELECT cancel_requested FROM batches WHERE id=?", (batch_id,)).fetchone()[0]:
            break
        if item["status"] in {"completed", "partial"}:
            continue
        try:
            task_id = str(item["task_id"] or "")
            if not task_id:
                input_path = Path(item["input_path"])
                input_root = _state(group_id) / "batches" / batch_id / "inputs"
                _contained(input_path, input_root)
                result = analyze_path(
                    group_id, input_path, Path(item["relative_path"]).name, item["relative_path"],
                    batch_id=batch_id, output_root=Path(batch["output_path"]),
                    scope_key=str(batch["scope_key"] or ""), scope_root=Path(batch["scope_root"]))
                task_id = str(result["analysis_id"])
                conn.execute("""UPDATE batch_items SET task_id=?,status='awaiting_review',updated=?
                    WHERE batch_id=? AND sequence=?""", (task_id, time.time(), batch_id, item["sequence"]))
                conn.commit()
                if batch["mode"] == "review":
                    continue
            review = json.loads(item["review_json"] or "{}")
            analysis = get_analysis(group_id, task_id)
            conn.execute("UPDATE batches SET status='running',updated=? WHERE id=?", (time.time(), batch_id))
            conn.commit()
            result = mask_file(
                group_id, task_id, review.get("entities", analysis.get("entities")),
                Path(item["relative_path"]).name, review.get("policies", options.get("policies", {})),
                bool(options.get("reversible")), options.get("password"),
                review.get("boxes", analysis.get("boxes")), bool(review.get("image_override", False)), review.get("embedded_images"))
            conn.execute("""UPDATE batch_items SET status=?,input_path='',updated=?,
                error_code='',error_message='' WHERE batch_id=? AND sequence=?""",
                (result.get("status", "completed"), time.time(), batch_id, item["sequence"]))
            conn.commit()
            Path(item["input_path"]).unlink(missing_ok=True)
        except Exception as exc:
            conn.execute("""UPDATE batch_items SET status='failed',error_code='processing_error',
                error_message=?,updated=? WHERE batch_id=? AND sequence=?""",
                (str(exc)[:1000], time.time(), batch_id, item["sequence"]))
            conn.commit()
    counts = {str(row[0]): int(row[1]) for row in conn.execute(
        "SELECT status,COUNT(*) FROM batch_items WHERE batch_id=? GROUP BY status", (batch_id,)).fetchall()}
    completed = counts.get("completed", 0) + counts.get("partial", 0)
    failed = counts.get("failed", 0)
    cancelled = bool(conn.execute("SELECT cancel_requested FROM batches WHERE id=?", (batch_id,)).fetchone()[0])
    if cancelled:
        status = "cancelled"
    elif batch["mode"] == "review" and counts.get("awaiting_review", 0):
        status = "awaiting_review"
    else:
        status = "completed" if failed == 0 and not counts.get("partial", 0) else ("partial" if completed else "failed")
    report = archive = ""
    if status in TERMINAL_BATCH_STATUSES:
        report_path, archive_path = _build_batch_artifacts(group_id, batch_id, Path(batch["output_path"]), status)
        report, archive = str(report_path), str(archive_path)
    conn.execute("""UPDATE batches SET status=?,completed=?,failed=?,updated=?,report_path=?,archive_path=?
        WHERE id=?""", (status, completed, failed, time.time(), report, archive, batch_id))
    conn.commit(); conn.close()
    if status in TERMINAL_BATCH_STATUSES:
        _batch_secrets.pop((group_id, batch_id), None)


def _build_batch_artifacts(group_id: str, batch_id: str, output: Path,
                           status: str) -> tuple[Path, Path]:
    batch = get_batch(group_id, batch_id, include_items=True)
    report = output / "report.json"
    items = []
    for item in batch["items"]:
        public = {key: value for key, value in item.items() if key != "analysis"}
        if item.get("task_id"):
            public["warnings"] = json.loads(_task_row(group_id, item["task_id"])["warnings_json"] or "[]")
        items.append(public)
    report.write_text(json.dumps({"batch_id": batch_id, "status": status, "items": items},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    archive = output / "results.zip"
    temp = output / f".{uuid.uuid4().hex}.zip.tmp"
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
        for item in output.rglob("*"):
            if item.is_file() and item not in {archive, temp}:
                bundle.write(item, item.relative_to(output).as_posix())
    os.replace(temp, archive)
    return report, archive


def get_batch(group_id: str, batch_id: str, *, include_items: bool = True) -> dict[str, Any]:
    conn = _connection(group_id)
    row = conn.execute("SELECT * FROM batches WHERE id=? AND group_id=?", (batch_id, group_id)).fetchone()
    if row is None:
        conn.close()
        raise ValueError("batch not found")
    result = dict(row)
    result["batch_id"] = result["id"]
    if include_items:
        raw_items = conn.execute("""SELECT sequence,relative_path,task_id,status,error_code,error_message
            FROM batch_items WHERE batch_id=? ORDER BY sequence""", (batch_id,)).fetchall()
        conn.close()
        items = []
        for raw in raw_items:
            item = dict(raw)
            if item["task_id"] and item["status"] == "awaiting_review":
                try:
                    item["analysis"] = get_analysis(group_id, item["task_id"])
                except Exception:
                    item["analysis"] = None
            items.append(item)
        result["items"] = items
    else:
        conn.close()
    return result


def list_batches(group_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    conn = _connection(group_id)
    total = int(conn.execute("SELECT COUNT(*) FROM batches WHERE group_id=?", (group_id,)).fetchone()[0])
    rows = conn.execute("SELECT * FROM batches WHERE group_id=? ORDER BY created DESC LIMIT ? OFFSET ?",
                        (group_id, limit, offset)).fetchall()
    conn.close()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def cancel_batch(group_id: str, batch_id: str) -> dict[str, Any]:
    current = get_batch(group_id, batch_id, include_items=False)
    if current["status"] in TERMINAL_BATCH_STATUSES:
        return current
    conn = _connection(group_id)
    conn.execute("UPDATE batches SET cancel_requested=1,updated=? WHERE id=? AND group_id=?",
                 (time.time(), batch_id, group_id))
    conn.commit(); conn.close()
    return get_batch(group_id, batch_id)


def mask_batch(group_id: str, batch_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    batch = get_batch(group_id, batch_id)
    if batch["mode"] != "review" or batch["status"] != "awaiting_review":
        raise ValueError("batch is not awaiting review")
    submitted = payload or {}
    reviews = {str(item.get("task_id") or ""): item for item in submitted.get("items", [])
               if isinstance(item, dict)}
    options = {key: submitted[key] for key in ("policies", "reversible") if key in submitted}
    password = str(submitted.get("password") or "")
    if options.get("reversible") and not password:
        raise ValueError("reversible mode requires a password")
    conn = _connection(group_id)
    for item in batch.get("items", []):
        task_id = str(item.get("task_id") or "")
        conn.execute("""UPDATE batch_items SET review_json=?,updated=?
            WHERE batch_id=? AND task_id=?""",
            (json.dumps(reviews.get(task_id, {}), ensure_ascii=False), time.time(), batch_id, task_id))
    conn.execute("""UPDATE batches SET mode='auto',status='queued',cancel_requested=0,
        options_json=?,updated=? WHERE id=? AND group_id=?""",
        (json.dumps(options, ensure_ascii=False), time.time(), batch_id, group_id))
    conn.commit(); conn.close()
    if password:
        _batch_secrets[(group_id, batch_id)] = password
    _enqueue(group_id, batch_id)
    return get_batch(group_id, batch_id)


def batch_artifact_path(group_id: str, batch_id: str, kind: str) -> Path:
    batch = get_batch(group_id, batch_id, include_items=False)
    output = Path(batch["output_path"])
    _contained(output, Path(batch["scope_root"]))
    key = "archive_path" if kind == "download" else "report_path"
    path = _contained(Path(batch[key]), output)
    if not path.is_file():
        raise ValueError("batch artifact not found")
    return path


def cleanup_expired(group_id: str) -> int:
    now = time.time()
    removed = 0
    conn = _connection(group_id)
    rows = conn.execute("SELECT id FROM tasks WHERE group_id=? AND expires>0 AND expires<?",
                        (group_id, now)).fetchall()
    for row in rows:
        folder = group_engine_root(group_id) / "tasks" / row["id"]
        for name in ("source.bin", "analysis.json", ".ocr-input.png"):
            target = folder / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        conn.execute("UPDATE tasks SET source_path='',expires=0,updated=? WHERE id=? AND group_id=?",
                     (now, row["id"], group_id))
        removed += 1
    conn.commit(); conn.close()
    return removed


def artifact_path(group_id: str, task_id: str, kind: str) -> Path:
    return task_file(group_id, task_id, kind)[0]
