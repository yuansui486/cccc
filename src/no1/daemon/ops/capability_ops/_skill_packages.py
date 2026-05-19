"""Codex local skill package materialization for capability records."""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.request import Request, urlopen

from ....kernel.actors import find_actor
from ....paths import ensure_home
from ....util.fs import atomic_write_bytes, atomic_write_json, read_json
from ....util.time import utc_now_iso

from ._common import _capability_root, _env_int
from ._documents import _load_catalog_doc

_PACKAGE_MODE = "codex_skill_package"
_MAX_PACKAGE_BYTES = 50 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
_MAX_PACKAGE_FILES = 1000
_SAFE_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


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
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
    except Exception:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


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


def _effective_package_autoload_for_actor(group: Any, actor: Dict[str, Any]) -> List[str]:
    group_autoload: List[str] = []
    try:
        from ....kernel.group import normalize_group_capability_defaults

        defaults = normalize_group_capability_defaults(getattr(group, "doc", {}).get("capability_defaults"))
        group_autoload = _normalize_capability_id_list(defaults.get("autoload_capabilities"))
    except Exception:
        group_autoload = []

    actor_autoload = _normalize_capability_id_list(actor.get("capability_autoload"))
    return _normalize_capability_id_list([*group_autoload, *actor_autoload])


def prepare_codex_skill_package_overlay_for_actor(group: Any, actor_id: str, env: Dict[str, Any]) -> Dict[str, Any]:
    actor = find_actor(group, actor_id)
    if not isinstance(actor, dict):
        return {}
    autoload = _effective_package_autoload_for_actor(group, actor)
    if not autoload:
        return {}
    _, catalog = _load_catalog_doc()
    records = catalog.get("records") if isinstance(catalog.get("records"), dict) else {}
    selected: List[Dict[str, Any]] = []
    for cap_id in autoload:
        rec = records.get(cap_id) if isinstance(records.get(cap_id), dict) else None
        if isinstance(rec, dict) and is_codex_skill_package_record(rec):
            selected.append(dict(rec))
    if not selected:
        return {}

    overlay = ensure_home() / "runtime" / "codex_homes" / _safe_token(getattr(group, "group_id", ""), default="group") / _safe_token(actor_id, default="actor")
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
    for child in list(skills_dir.iterdir()):
        if child.name not in wanted:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
    return {
        "CODEX_HOME": str(overlay),
        "CCCC_CODEX_SKILLS_OVERLAY": "1",
        "CCCC_CODEX_SKILLS_OVERLAY_COUNT": str(len(materialized)),
    }
