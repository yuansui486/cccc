from dataclasses import dataclass
from pathlib import Path
import os
import re
import yaml

GROUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

@dataclass
class Group:
    group_id: str
    path: Path
    doc: dict

def ensure_home() -> Path:
    value = os.environ.get("ONECOLLEAGUE_HOME") or os.environ.get("CCCC_HOME")
    p = Path(value or (Path.home() / ".onecolleague")).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p

def load_group(group_id: str) -> Group | None:
    gid = str(group_id or "").strip()
    if not GROUP_ID_RE.fullmatch(gid) or gid in {".", ".."}:
        return None
    groups = (ensure_home() / "groups").resolve()
    root = (groups / gid).resolve()
    try:
        root.relative_to(groups)
    except ValueError:
        return None
    document = root / "group.yaml"
    if not gid or not document.is_file():
        return None
    try:
        doc = yaml.safe_load(document.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    document_gid = str(doc.get("group_id") or gid).strip()
    if document_gid != gid:
        return None
    return Group(gid, root, doc)

def resolve_scope_root(group: Group | str) -> Path | None:
    if isinstance(group, str):
        group = load_group(group)
        if group is None:
            return None
    scopes = group.doc.get("scopes") if isinstance(group.doc.get("scopes"), list) else []
    active = str(group.doc.get("active_scope_key") or "").strip()
    active_candidate = ""
    fallbacks: list[str] = []
    for item in scopes:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        if active and str(item.get("scope_key") or "").strip() == active:
            active_candidate = url
        else:
            fallbacks.append(url)
    # An explicitly configured active scope must never silently redirect
    # output into a different scope when its path is temporarily unavailable.
    candidates = [active_candidate] if active and active_candidate else (fallbacks if not active else [])
    for raw in candidates:
        try:
            root = Path(raw).expanduser().resolve()
        except Exception:
            continue
        if root.is_dir():
            return root
    return None
