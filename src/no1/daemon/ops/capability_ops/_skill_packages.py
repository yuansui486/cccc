"""Codex local skill package materialization for capability records."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import re
import secrets
import shutil
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen

import yaml

from ....kernel.actors import find_actor
from ....paths import ensure_home
from ....util.fs import atomic_write_bytes, atomic_write_json, read_json
from ....util.time import utc_now_iso

from ._common import _capability_root, _env_int
from ._admission import resolve_current_admission

_PACKAGE_MODE = "codex_skill_package"
_MAX_PACKAGE_BYTES = 50 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
_MAX_PACKAGE_FILES = 10000
_SAFE_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_logger = logging.getLogger("no1.daemon.openclaw.skills")
_OPENCLAW_PROJECTION_LOCKS: Dict[Tuple[str, str], threading.RLock] = {}
_OPENCLAW_PROJECTION_LOCKS_GUARD = threading.RLock()


def _skill_package_root() -> Path:
    return _capability_root() / "skill_packages"


def _skill_package_blob_dir() -> Path:
    return _skill_package_root() / "blobs"


def _skill_package_extract_dir() -> Path:
    return _skill_package_root() / "extracted"


def _skill_package_install_state_path() -> Path:
    return _skill_package_root() / "install_state.json"


def _normalize_sha256(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value.startswith("sha256:"):
        value = value.split(":", 1)[1].strip()
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        return ""
    return value


def _safe_token(raw: Any, *, default: str = "skill") -> str:
    token = _SAFE_SLUG_RE.sub("-", str(raw or "").strip().lower()).strip("-_")
    return token or default


def _package_format_from_url(url: str) -> str:
    return "zip"


def normalize_codex_skill_package_spec(raw_record: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    install_mode = str(raw_record.get("install_mode") or "").strip().lower()
    raw_spec = raw_record.get("install_spec") if isinstance(raw_record.get("install_spec"), dict) else {}
    spec = dict(raw_spec)
    package_url = (
        str(spec.get("package_url") or spec.get("skill_package_url") or raw_record.get("package_url") or raw_record.get("skill_package_url") or "").strip()
    )
    package_sha256 = _normalize_sha256(
        spec.get("package_sha256")
        or spec.get("skill_package_sha256")
        or raw_record.get("package_sha256")
        or raw_record.get("skill_package_sha256")
        or raw_record.get("sha256")
    )
    has_package = bool(package_url or package_sha256 or install_mode == _PACKAGE_MODE)
    if not has_package:
        return "builtin", {}
    if not package_url:
        raise ValueError("codex_skill_package requires install_spec.package_url")
    if not package_sha256:
        raise ValueError("codex_skill_package requires install_spec.package_sha256")
    try:
        package_size = int(
            spec.get("package_size")
            or spec.get("skill_package_size")
            or raw_record.get("package_size")
            or raw_record.get("skill_package_size")
            or 0
        )
    except Exception:
        package_size = 0
    if package_size < 0:
        package_size = 0
    cap_id = str(raw_record.get("capability_id") or "").strip()
    name = str(raw_record.get("name") or "").strip()
    version = str(raw_record.get("source_record_version") or spec.get("package_version") or raw_record.get("package_version") or "").strip()
    package_format = str(spec.get("package_format") or raw_record.get("package_format") or _package_format_from_url(package_url)).strip().lower()
    if package_format != "zip":
        raise ValueError("codex_skill_package package_format must be zip")
    entrypoint = str(spec.get("entrypoint") or raw_record.get("entrypoint") or "SKILL.md").strip() or "SKILL.md"
    if entrypoint.replace("\\", "/").strip("/") != "SKILL.md":
        raise ValueError("codex_skill_package entrypoint must be SKILL.md")
    skill_slug = _safe_token(spec.get("skill_slug") or raw_record.get("skill_slug") or name or cap_id.rsplit(":", 1)[-1])
    out = {
        "package_url": package_url,
        "package_sha256": package_sha256,
        "package_size": package_size,
        "package_format": package_format,
        "skill_slug": skill_slug,
        "package_version": version,
        "entrypoint": entrypoint,
    }
    for key in ("files_manifest_sha256", "signature_status"):
        value = str(spec.get(key) or raw_record.get(key) or "").strip()
        if value:
            out[key] = value
    return _PACKAGE_MODE, out


def is_codex_skill_package_record(rec: Dict[str, Any]) -> bool:
    return (
        isinstance(rec, dict)
        and str(rec.get("kind") or "").strip().lower() == "skill"
        and str(rec.get("install_mode") or "").strip().lower() == _PACKAGE_MODE
    )


def _load_install_state() -> Tuple[Path, Dict[str, Any]]:
    path = _skill_package_install_state_path()
    doc = read_json(path)
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("v", 1)
    doc.setdefault("packages", {})
    if not isinstance(doc.get("packages"), dict):
        doc["packages"] = {}
    return path, doc


def _save_install_state(path: Path, doc: Dict[str, Any]) -> None:
    doc["updated_at"] = utc_now_iso()
    atomic_write_json(path, doc, indent=2)


def _max_package_bytes() -> int:
    return max(1024, _env_int("CCCC_SKILL_PACKAGE_MAX_BYTES", _MAX_PACKAGE_BYTES))


def _max_extracted_bytes() -> int:
    return max(1024, _env_int("CCCC_SKILL_PACKAGE_MAX_EXTRACTED_BYTES", _MAX_EXTRACTED_BYTES))


def _max_package_files() -> int:
    return max(1, _env_int("CCCC_SKILL_PACKAGE_MAX_FILES", _MAX_PACKAGE_FILES))


def _download_package_bytes(url: str, *, max_bytes: int) -> bytes:
    req = Request(str(url or ""), method="GET")
    chunks: List[bytes] = []
    total = 0
    with urlopen(req, timeout=30.0) as resp:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("skill package exceeds max download size")
            chunks.append(chunk)
    return b"".join(chunks)


def _member_parts(name: str) -> List[str]:
    normalized = posixpath.normpath(str(name or "").replace("\\", "/"))
    if normalized in {"", "."} or normalized.startswith("/") or normalized.startswith("../"):
        raise ValueError(f"unsafe package path: {name}")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError(f"unsafe package path: {name}")
    return parts


def _write_member(root: Path, parts: List[str], data: bytes) -> None:
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target, data)


def _extract_zip(blob_path: Path, tmp_dir: Path, *, max_files: int, max_bytes: int) -> None:
    total = 0
    count = 0
    with zipfile.ZipFile(blob_path) as zf:
        for info in zf.infolist():
            mode = (int(info.external_attr) >> 16) & 0o170000
            if mode in {0o120000, 0o10000, 0o20000, 0o60000}:
                raise ValueError(f"unsupported package entry type: {info.filename}")
            parts = _member_parts(info.filename)
            if not parts:
                continue
            if info.is_dir():
                tmp_dir.joinpath(*parts).mkdir(parents=True, exist_ok=True)
                continue
            count += 1
            total += int(info.file_size or 0)
            if count > max_files:
                raise ValueError("skill package has too many files")
            if total > max_bytes:
                raise ValueError("skill package extracted size exceeds limit")
            _write_member(tmp_dir, parts, zf.read(info))


def _skill_root_from_tmp(tmp_dir: Path) -> Path:
    if (tmp_dir / "SKILL.md").is_file():
        return tmp_dir
    dirs = [p for p in tmp_dir.iterdir() if p.is_dir() and p.name != "__MACOSX"]
    dirs = [p for p in dirs if not p.name.startswith(".")]
    candidates = [p for p in dirs if (p / "SKILL.md").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("skill package missing SKILL.md")
    raise ValueError("skill package has multiple SKILL.md roots")


def _extract_package(blob_path: Path, final_dir: Path, *, package_format: str) -> None:
    parent = final_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=final_dir.name + ".", dir=str(parent)) as tmp:
        tmp_dir = Path(tmp)
        if package_format != "zip":
            raise ValueError("unsupported skill package format")
        _extract_zip(blob_path, tmp_dir, max_files=_max_package_files(), max_bytes=_max_extracted_bytes())
        root = _skill_root_from_tmp(tmp_dir)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.copytree(root, final_dir)


def ensure_codex_skill_package_installed(record: Dict[str, Any]) -> Dict[str, Any]:
    if not is_codex_skill_package_record(record):
        return {"installed": False, "reason": "not_codex_skill_package"}
    spec = record.get("install_spec") if isinstance(record.get("install_spec"), dict) else {}
    package_url = str(spec.get("package_url") or "").strip()
    package_sha256 = _normalize_sha256(spec.get("package_sha256"))
    package_format = str(spec.get("package_format") or _package_format_from_url(package_url)).strip().lower()
    skill_slug = _safe_token(spec.get("skill_slug") or record.get("name") or record.get("capability_id"))
    version = str(spec.get("package_version") or record.get("source_record_version") or package_sha256[:12]).strip() or package_sha256[:12]
    if not package_url:
        raise ValueError("skill package missing package_url")
    if not package_sha256:
        raise ValueError("skill package missing package_sha256")
    if package_format != "zip":
        raise ValueError("skill package package_format must be zip")
    try:
        expected_size = int(spec.get("package_size") or 0)
    except Exception:
        expected_size = 0
    max_bytes = _max_package_bytes()
    if expected_size > max_bytes:
        raise ValueError("skill package exceeds max configured size")

    safe_cap = _safe_token(record.get("capability_id"), default="capability")
    blob_path = _skill_package_blob_dir() / f"{package_sha256}.zip"
    final_dir = _skill_package_extract_dir() / safe_cap / _safe_token(version, default=package_sha256[:12])

    path, state = _load_install_state()
    packages = state.get("packages") if isinstance(state.get("packages"), dict) else {}
    existing = packages.get(str(record.get("capability_id") or "")) if isinstance(packages, dict) else None
    if isinstance(existing, dict):
        existing_path = Path(str(existing.get("extracted_path") or ""))
        if (
            str(existing.get("package_sha256") or "") == package_sha256
            and existing_path.is_dir()
            and (existing_path / "SKILL.md").is_file()
        ):
            return dict(existing)

    if not blob_path.exists():
        data = _download_package_bytes(package_url, max_bytes=max_bytes)
        actual_size = len(data)
        if expected_size and actual_size != expected_size:
            raise ValueError("skill package size mismatch")
        actual_sha = hashlib.sha256(data).hexdigest()
        if actual_sha != package_sha256:
            raise ValueError("skill package sha256 mismatch")
        atomic_write_bytes(blob_path, data)
    else:
        data = blob_path.read_bytes()
        if expected_size and len(data) != expected_size:
            raise ValueError("cached skill package size mismatch")
        if hashlib.sha256(data).hexdigest() != package_sha256:
            raise ValueError("cached skill package sha256 mismatch")

    _extract_package(blob_path, final_dir, package_format=package_format)
    row = {
        "capability_id": str(record.get("capability_id") or ""),
        "skill_slug": skill_slug,
        "package_version": version,
        "package_sha256": package_sha256,
        "package_size": int(expected_size or blob_path.stat().st_size),
        "package_format": package_format,
        "blob_path": str(blob_path),
        "extracted_path": str(final_dir),
        "installed_at": utc_now_iso(),
        "state": "installed",
    }
    packages[str(record.get("capability_id") or "")] = row
    state["packages"] = packages
    _save_install_state(path, state)
    return dict(row)


def _copy_or_link(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
    except Exception:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def _active_scope_path_for_group(group: Any) -> Path | None:
    doc = getattr(group, "doc", {}) if group is not None else {}
    if not isinstance(doc, dict):
        return None
    active_scope_key = str(doc.get("active_scope_key") or "").strip()
    scopes = doc.get("scopes") if isinstance(doc.get("scopes"), list) else []
    for item in scopes:
        if not isinstance(item, dict):
            continue
        scope_key = str(item.get("scope_key") or "").strip()
        if active_scope_key and scope_key != active_scope_key:
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        path = Path(url).expanduser()
        if path.exists() and path.is_dir():
            return path.resolve()
    return None


def _managed_project_skill_root(workspace: Path) -> Path:
    return workspace / ".onecolleague" / "skills"


def _materialized_skill_root(workspace: Path, link_name: str) -> Path:
    return _managed_project_skill_root(workspace) / _safe_token(link_name)


def _project_skill_link_name(record: Dict[str, Any], install_row: Dict[str, Any]) -> str:
    return _safe_token(
        install_row.get("skill_slug")
        or (record.get("install_spec") or {}).get("skill_slug")
        or record.get("name")
        or str(record.get("capability_id") or "").rsplit(":", 1)[-1]
    )


def materialize_skill_package_for_group(
    *,
    group: Any,
    record: Dict[str, Any],
    install_row: Dict[str, Any],
) -> Dict[str, Any]:
    workspace = _active_scope_path_for_group(group)
    if workspace is None:
        return {"project_linked": False, "reason": "missing_active_scope"}
    extracted_path = Path(str(install_row.get("extracted_path") or ""))
    if not extracted_path.is_dir() or not (extracted_path / "SKILL.md").is_file():
        return {"project_linked": False, "reason": "missing_extracted_package"}
    link_name = _project_skill_link_name(record, install_row)
    target = workspace / link_name
    managed_target = _materialized_skill_root(workspace, link_name)
    managed_target.parent.mkdir(parents=True, exist_ok=True)
    if not managed_target.exists() and not managed_target.is_symlink():
        _copy_or_link(extracted_path, managed_target)
    resource_paths: List[str] = []
    try:
        for child in sorted(managed_target.iterdir(), key=lambda p: p.name.lower()):
            if child.name == "SKILL.md":
                continue
            if child.name.startswith("."):
                continue
            resource_target = workspace / child.name
            _copy_or_link(child, resource_target)
            if resource_target.exists() or resource_target.is_symlink():
                resource_paths.append(str(resource_target))
    except Exception:
        resource_paths = []
    if target.exists() or target.is_symlink():
        return {
            "project_linked": True,
            "project_path": str(target),
            "managed_path": str(managed_target),
            "resource_paths": resource_paths,
            "source": "existing",
            "extracted_path": str(extracted_path),
        }
    _copy_or_link(managed_target, target)
    return {
        "project_linked": True,
        "project_path": str(target),
        "managed_path": str(managed_target),
        "resource_paths": resource_paths,
        "source": "created",
        "extracted_path": str(extracted_path),
    }


def _source_codex_home(env: Dict[str, Any], overlay: Path) -> Path:
    raw = str(env.get("CODEX_HOME") or os.environ.get("CODEX_HOME") or "").strip()
    path = Path(raw).expanduser() if raw else (Path.home() / ".codex")
    try:
        if path.resolve() == overlay.resolve():
            return Path.home() / ".codex"
    except Exception:
        pass
    return path


def _prepare_overlay_base(overlay: Path, env: Dict[str, Any]) -> None:
    overlay.mkdir(parents=True, exist_ok=True)
    src_home = _source_codex_home(env, overlay)
    if not src_home.exists():
        return
    for name in ("auth.json", "config.toml"):
        src = src_home / name
        if src.exists():
            _copy_or_link(src, overlay / name)
    src_system = src_home / "skills" / ".system"
    if src_system.exists():
        skills_dir = overlay / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        _copy_or_link(src_system, skills_dir / ".system")


def _normalize_capability_id_list(raw: Any) -> List[str]:
    out: List[str] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for item in raw:
        cap_id = str(item or "").strip()
        if not cap_id or cap_id in seen:
            continue
        seen.add(cap_id)
        out.append(cap_id)
    return out


def _effective_package_autoload_for_actor(
    group: Any,
    actor: Dict[str, Any],
    *,
    admission: Dict[str, Any] | None = None,
) -> List[str]:
    group_id = str(getattr(group, "group_id", "") or "").strip()
    actor_id = str(actor.get("id") or "").strip()
    if not group_id or not actor_id:
        return []
    current = admission if isinstance(admission, dict) else resolve_current_admission(
        group_id=group_id,
        actor_id=actor_id,
    )
    return list(current.get("admitted_capabilities") or [])


def _reconcile_managed_overlay_skills(overlay: Path, wanted: set[str]) -> None:
    skills_dir = overlay / "skills"
    if not skills_dir.is_dir():
        return
    keep = {".system", *{str(item or "").strip() for item in wanted if str(item or "").strip()}}
    for child in list(skills_dir.iterdir()):
        if child.name in keep:
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)


def prepare_codex_skill_package_overlay_for_actor(group: Any, actor_id: str, env: Dict[str, Any]) -> Dict[str, Any]:
    overlay = ensure_home() / "runtime" / "codex_homes" / _safe_token(
        getattr(group, "group_id", ""), default="group"
    ) / _safe_token(actor_id, default="actor")
    actor = find_actor(group, actor_id)
    if not isinstance(actor, dict):
        _reconcile_managed_overlay_skills(overlay, set())
        return {}
    admission = resolve_current_admission(
        group_id=str(getattr(group, "group_id", "") or ""),
        actor_id=actor_id,
    )
    autoload = _effective_package_autoload_for_actor(group, actor, admission=admission)
    if not autoload:
        _reconcile_managed_overlay_skills(overlay, set())
        return {}
    records = admission.get("admitted_records") if isinstance(admission.get("admitted_records"), dict) else {}
    selected: List[Dict[str, Any]] = []
    for cap_id in autoload:
        rec = records.get(cap_id) if isinstance(records.get(cap_id), dict) else None
        if (
            isinstance(rec, dict)
            and is_codex_skill_package_record(rec)
            and str(rec.get("qualification_status") or "").strip().lower() == "qualified"
        ):
            selected.append(dict(rec))
    if not selected:
        _reconcile_managed_overlay_skills(overlay, set())
        return {}

    _prepare_overlay_base(overlay, env)
    skills_dir = overlay / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    package_state = read_json(_skill_package_install_state_path())
    packages = package_state.get("packages") if isinstance(package_state.get("packages"), dict) else {}
    materialized: List[Dict[str, str]] = []
    wanted = {".system"}
    for rec in selected:
        cap_id = str(rec.get("capability_id") or "").strip()
        row = packages.get(cap_id) if isinstance(packages.get(cap_id), dict) else {}
        extracted_path = Path(str(row.get("extracted_path") or ""))
        if not extracted_path.is_dir() or not (extracted_path / "SKILL.md").is_file():
            _reconcile_managed_overlay_skills(overlay, set())
            raise ValueError(f"skill package is not installed: {cap_id}")
        skill_slug = _safe_token(row.get("skill_slug") or (rec.get("install_spec") or {}).get("skill_slug") or rec.get("name") or cap_id)
        target = skills_dir / skill_slug
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
        _copy_or_link(extracted_path, target)
        wanted.add(skill_slug)
        materialized.append({"capability_id": cap_id, "skill_slug": skill_slug, "path": str(target)})
    _reconcile_managed_overlay_skills(overlay, wanted)
    return {
        "CODEX_HOME": str(overlay),
        "CCCC_CODEX_SKILLS_OVERLAY": "1",
        "CCCC_CODEX_SKILLS_OVERLAY_COUNT": str(len(materialized)),
    }


def _openclaw_skill_name(skill_root: Path, fallback: str) -> str:
    skill_file = skill_root / "SKILL.md"
    try:
        raw = skill_file.read_text(encoding="utf-8", errors="replace")
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1])
                if isinstance(frontmatter, dict):
                    name = str(frontmatter.get("name") or "").strip()
                    if name:
                        return name
    except Exception:
        pass
    return str(fallback or "").strip()


def _openclaw_actor_skill_root(group_id: str, actor_id: str) -> Tuple[Path, Path]:
    digest = hashlib.sha256(f"{str(group_id).strip()}\0{str(actor_id).strip()}".encode("utf-8")).hexdigest()[:16]
    managed_root = ensure_home() / "runtime" / "openclaw" / "skills" / "actors"
    return managed_root, managed_root / digest


def openclaw_actor_skill_projection_lock(group_id: str, actor_id: str) -> threading.RLock:
    """Return the process-local lock for one actor's OpenClaw projection."""
    key = (str(group_id or "").strip(), str(actor_id or "").strip())
    with _OPENCLAW_PROJECTION_LOCKS_GUARD:
        lock = _OPENCLAW_PROJECTION_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _OPENCLAW_PROJECTION_LOCKS[key] = lock
        return lock


def _remove_openclaw_skill_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _best_effort_remove_openclaw_skill_path(path: Path, *, attempts: int = 3) -> bool:
    """Remove a managed path without making a live projection fail on Windows."""
    if not (path.exists() or path.is_symlink()) and not path.is_file():
        return True
    last_error: Optional[BaseException] = None
    for attempt in range(max(1, attempts)):
        try:
            _remove_openclaw_skill_path(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.05 * (2**attempt))
    _logger.warning("OpenClaw managed skill cleanup deferred for %s: %s", path, last_error)
    return False


def _openclaw_revision_path(actor_root: Path, fingerprint: str) -> Path:
    return actor_root / "revisions" / str(fingerprint or "")[:24]


def _openclaw_projection_result(
    *,
    managed_root: Path,
    actor_root: Path,
    revision_root: Optional[Path],
    managed: Dict[str, Dict[str, str]],
    selected_names: List[str],
    fingerprint: str,
    cleanup_pending: Optional[List[str]] = None,
) -> Dict[str, Any]:
    root = revision_root / "skills" if revision_root is not None and managed else None
    managed_names = sorted(
        {str(item.get("name") or "").strip() for item in managed.values() if str(item.get("name") or "").strip()}
    )
    return {
        "root": str(root) if root is not None else "",
        "managed_root": str(managed_root),
        "actor_root": str(actor_root),
        "legacy_root": str(actor_root / "skills"),
        "revision_root": str(revision_root) if revision_root is not None else "",
        "selected_names": sorted(set(selected_names)),
        "managed_names": managed_names,
        "fingerprint": fingerprint,
        "cleanup_pending": sorted(set(cleanup_pending or [])),
    }


def _openclaw_revision_is_complete(revision_root: Path, fingerprint: str) -> bool:
    metadata = read_json(revision_root / "managed-skills.json")
    if str(metadata.get("fingerprint") or "") != str(fingerprint or ""):
        return False
    skills = metadata.get("skills") if isinstance(metadata.get("skills"), dict) else {}
    if not skills:
        return False
    for row in skills.values():
        if not isinstance(row, dict):
            return False
        slug = _safe_token(row.get("skill_slug"), default="")
        if not slug or not (revision_root / "skills" / slug / "SKILL.md").is_file():
            return False
    return True


def _find_complete_openclaw_revision(actor_root: Path, fingerprint: str) -> Optional[Path]:
    revisions_root = actor_root / "revisions"
    if not revisions_root.is_dir():
        return None
    canonical = _openclaw_revision_path(actor_root, fingerprint)
    candidates = [canonical, *sorted(revisions_root.glob(f"{str(fingerprint)[:24]}-*"))]
    for candidate in candidates:
        if _openclaw_revision_is_complete(candidate, fingerprint):
            return candidate
    return None


def _openclaw_skill_slug_map(entries: List[Dict[str, Any]]) -> Dict[str, str]:
    counts: Dict[str, int] = {}
    for entry in entries:
        base = str(entry["base_slug"])
        counts[base] = counts.get(base, 0) + 1
    used: set[str] = set()
    result: Dict[str, str] = {}
    for entry in sorted(entries, key=lambda item: str(item["capability_id"])):
        capability_id = str(entry["capability_id"])
        base = str(entry["base_slug"])
        if counts.get(base, 0) > 1:
            candidate = f"{base}--{hashlib.sha256(capability_id.encode('utf-8')).hexdigest()[:12]}"
        else:
            candidate = base
        if candidate in used:
            suffix = hashlib.sha256(capability_id.encode("utf-8")).hexdigest()
            for width in (16, 24, 32, 64):
                candidate = f"{base}--{suffix[:width]}"
                if candidate not in used:
                    break
        used.add(candidate)
        result[capability_id] = candidate
    return result


def _rewrite_openclaw_skill_name(skill_path: Path, capability_id: str) -> str:
    stable_name = f"onecolleague-{hashlib.sha256(str(capability_id).encode('utf-8')).hexdigest()[:16]}"
    source = skill_path.read_text(encoding="utf-8")
    if source.startswith("---"):
        parts = source.split("---", 2)
        if len(parts) == 3:
            frontmatter = yaml.safe_load(parts[1])
            frontmatter = dict(frontmatter) if isinstance(frontmatter, dict) else {}
            frontmatter["name"] = stable_name
            rendered = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
            skill_path.write_text(f"---\n{rendered}\n---{parts[2]}", encoding="utf-8")
            return stable_name
    skill_path.write_text(f"---\nname: {stable_name}\n---\n\n{source}", encoding="utf-8")
    return stable_name


def prepare_openclaw_skill_package_overlay_for_actor(group: Any, actor_id: str) -> Dict[str, Any]:
    """Materialize admitted AgentSkills into an actor-specific OpenClaw skill root."""
    group_id = str(getattr(group, "group_id", "") or "").strip()
    managed_root, actor_root = _openclaw_actor_skill_root(group_id, actor_id)
    with openclaw_actor_skill_projection_lock(group_id, actor_id):
        actor = find_actor(group, actor_id)
        if not isinstance(actor, dict):
            pending = []
            if actor_root.exists():
                if not _best_effort_remove_openclaw_skill_path(actor_root):
                    pending.append(str(actor_root))
            return _openclaw_projection_result(
                managed_root=managed_root,
                actor_root=actor_root,
                revision_root=None,
                managed={},
                selected_names=[],
                fingerprint="",
                cleanup_pending=pending,
            )

        admission = resolve_current_admission(group_id=group_id, actor_id=actor_id)
        autoload = _effective_package_autoload_for_actor(group, actor, admission=admission)
        records = admission.get("admitted_records") if isinstance(admission.get("admitted_records"), dict) else {}
        package_state = read_json(_skill_package_install_state_path())
        packages = package_state.get("packages") if isinstance(package_state.get("packages"), dict) else {}
        entries: List[Dict[str, Any]] = []
        for cap_id in sorted(set(str(item or "").strip() for item in autoload if str(item or "").strip())):
            rec = records.get(cap_id) if isinstance(records.get(cap_id), dict) else None
            if not (
                isinstance(rec, dict)
                and is_codex_skill_package_record(rec)
                and str(rec.get("qualification_status") or "").strip().lower() == "qualified"
            ):
                continue
            row = packages.get(cap_id) if isinstance(packages.get(cap_id), dict) else {}
            extracted_path = Path(str(row.get("extracted_path") or ""))
            if not extracted_path.is_dir() or not (extracted_path / "SKILL.md").is_file():
                raise ValueError(f"skill package is not installed: {cap_id}")
            base_slug = _safe_token(
                row.get("skill_slug")
                or (rec.get("install_spec") or {}).get("skill_slug")
                or rec.get("name")
                or cap_id
            )
            entries.append(
                {
                    "capability_id": cap_id,
                    "row": row,
                    "extracted_path": extracted_path,
                    "base_slug": base_slug,
                    "name": f"onecolleague-{hashlib.sha256(cap_id.encode('utf-8')).hexdigest()[:16]}",
                }
            )
        if not entries:
            return _openclaw_projection_result(
                managed_root=managed_root,
                actor_root=actor_root,
                revision_root=None,
                managed={},
                selected_names=[],
                fingerprint="",
            )

        slug_map = _openclaw_skill_slug_map(entries)
        logical = [
            {
                "capability_id": str(entry["capability_id"]),
                "name": str(entry["name"]),
                "skill_slug": slug_map[str(entry["capability_id"])],
                "sha256": str(
                    entry["row"].get("sha256") or entry["row"].get("package_sha256") or ""
                ).strip(),
            }
            for entry in sorted(entries, key=lambda item: str(item["capability_id"]))
        ]
        fingerprint = hashlib.sha256(
            json.dumps(logical, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        revisions_root = actor_root / "revisions"
        revision_root = _openclaw_revision_path(actor_root, fingerprint)
        managed_root.mkdir(parents=True, exist_ok=True)
        revisions_root.mkdir(parents=True, exist_ok=True)
        complete_revision = _find_complete_openclaw_revision(actor_root, fingerprint)
        if complete_revision is not None:
            revision_root = complete_revision
            metadata = read_json(revision_root / "managed-skills.json")
            managed = metadata.get("skills") if isinstance(metadata.get("skills"), dict) else {}
            selected_names = [str(item.get("name") or "").strip() for item in logical]
            return _openclaw_projection_result(
                managed_root=managed_root,
                actor_root=actor_root,
                revision_root=revision_root,
                managed={str(key): dict(value) for key, value in managed.items() if isinstance(value, dict)},
                selected_names=selected_names,
                fingerprint=fingerprint,
            )

        staging_root = revisions_root / f".{fingerprint[:24]}.{secrets.token_hex(6)}.tmp"
        staging_skills = staging_root / "skills"
        managed: Dict[str, Dict[str, str]] = {}
        selected_names: List[str] = []
        try:
            for entry in entries:
                cap_id = str(entry["capability_id"])
                skill_slug = slug_map[cap_id]
                target = staging_skills / skill_slug
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(Path(entry["extracted_path"]), target)
                skill_name = _rewrite_openclaw_skill_name(target / "SKILL.md", cap_id)
                selected_names.append(skill_name)
                managed[cap_id] = {
                    "capability_id": cap_id,
                    "name": skill_name,
                    "skill_slug": skill_slug,
                    "path": str(revision_root / "skills" / skill_slug),
                    "sha256": str(
                        entry["row"].get("sha256") or entry["row"].get("package_sha256") or ""
                    ).strip(),
                }
            staging_root.mkdir(parents=True, exist_ok=True)
            atomic_write_json(
                staging_root / "managed-skills.json",
                {"v": 2, "fingerprint": fingerprint, "skills": managed},
                indent=2,
            )
            if revision_root.exists() and not _openclaw_revision_is_complete(revision_root, fingerprint):
                revision_root = revisions_root / f"{fingerprint[:24]}-{secrets.token_hex(4)}"
                for row in managed.values():
                    row["path"] = str(revision_root / "skills" / str(row["skill_slug"]))
                atomic_write_json(
                    staging_root / "managed-skills.json",
                    {"v": 2, "fingerprint": fingerprint, "skills": managed},
                    indent=2,
                )
            os.replace(staging_root, revision_root)
        except Exception:
            if staging_root.exists() or staging_root.is_symlink():
                _best_effort_remove_openclaw_skill_path(staging_root)
            raise

        return _openclaw_projection_result(
            managed_root=managed_root,
            actor_root=actor_root,
            revision_root=revision_root,
            managed=managed,
            selected_names=selected_names,
            fingerprint=fingerprint,
        )
