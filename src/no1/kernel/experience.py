from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..util.fs import atomic_write_text
from .group import Group
from .prompt_files import resolve_active_scope_root


EXPERIENCE_FILENAME = "EXPERIENCE.md"
MAX_EXPERIENCE_BYTES = 512 * 1024

DEFAULT_EXPERIENCE_TEMPLATE = """# 项目经验

> 记录本项目中已经验证、可复用的经验，内容保持简洁明确。
> 不要保存密钥、凭据、原始日志或未经验证的猜测。

## 经验条目

<!-- 建议使用以下格式：
### YYYY-MM-DD - 简短标题
- 情况：
- 现象：
- 根因：
- 解决方案：
- 验证结果：
- 适用范围：
-->

## 用户偏好

<!-- 只记录用户明确表达、重复确认或有直接证据的稳定偏好。
不要记录推测、一次性要求、敏感信息或完整聊天记录。
用户改变偏好时更新原条目，避免保留互相冲突的规则。

建议使用以下格式：
### 简短偏好标题
- 偏好类型：
- 触发场景：
- 用户更希望：
- 用户不希望：
- 执行准则：
- 验收信号：
- 置信度：low | medium | high
- 来源证据：
-->
"""


class ExperienceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExperienceDocument:
    path: Path
    content: str
    revision: str
    created: bool = False


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.absolute())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _revision(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_content(content: str) -> str:
    text = str(content or "")
    if len(text.encode("utf-8")) > MAX_EXPERIENCE_BYTES:
        raise ExperienceError(
            "experience_too_large",
            f"{EXPERIENCE_FILENAME} exceeds {MAX_EXPERIENCE_BYTES} bytes",
        )
    return text


def resolve_experience_path(group: Group) -> Path:
    root = resolve_active_scope_root(group)
    if root is None:
        raise ExperienceError("missing_scope", "group has no active project scope")
    if not root.exists() or not root.is_dir():
        raise ExperienceError("invalid_scope", f"project root does not exist: {root}")

    root_resolved = root.resolve()
    path = root_resolved / EXPERIENCE_FILENAME
    if path.exists():
        try:
            path.resolve().relative_to(root_resolved)
        except ValueError as exc:
            raise ExperienceError(
                "experience_path_out_of_scope",
                f"{EXPERIENCE_FILENAME} resolves outside the project root",
            ) from exc
        if not path.is_file():
            raise ExperienceError("experience_invalid_path", f"not a file: {path}")
    return path


def ensure_experience_file(project_root: Path) -> ExperienceDocument:
    root = Path(project_root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ExperienceError("invalid_scope", f"project root does not exist: {root}")
    path = root / EXPERIENCE_FILENAME
    with _path_lock(path):
        if path.exists():
            try:
                path.resolve().relative_to(root)
            except ValueError as exc:
                raise ExperienceError(
                    "experience_path_out_of_scope",
                    f"{EXPERIENCE_FILENAME} resolves outside the project root",
                ) from exc
            if not path.is_file():
                raise ExperienceError("experience_invalid_path", f"not a file: {path}")
            return read_experience_path(path)
        atomic_write_text(path, DEFAULT_EXPERIENCE_TEMPLATE, encoding="utf-8")
        return ExperienceDocument(
            path=path,
            content=DEFAULT_EXPERIENCE_TEMPLATE,
            revision=_revision(DEFAULT_EXPERIENCE_TEMPLATE),
            created=True,
        )


def read_experience_path(path: Path) -> ExperienceDocument:
    raw = path.read_bytes()
    if len(raw) > MAX_EXPERIENCE_BYTES:
        raise ExperienceError(
            "experience_too_large",
            f"{EXPERIENCE_FILENAME} exceeds {MAX_EXPERIENCE_BYTES} bytes",
        )
    content = raw.decode("utf-8", errors="replace")
    return ExperienceDocument(path=path, content=content, revision=_revision(content))


def read_experience(group: Group, *, ensure: bool = True) -> ExperienceDocument:
    path = resolve_experience_path(group)
    if ensure:
        return ensure_experience_file(path.parent)
    if not path.exists():
        return ExperienceDocument(path=path, content="", revision=_revision(""), created=False)
    return read_experience_path(path)


def replace_experience(group: Group, *, content: str, expected_revision: str) -> ExperienceDocument:
    text = _validate_content(content)
    path = resolve_experience_path(group)
    with _path_lock(path):
        current = ensure_experience_file(path.parent)
        expected = str(expected_revision or "").strip()
        if not expected:
            raise ExperienceError("missing_revision", "expected_revision is required")
        if expected != current.revision:
            raise ExperienceError("revision_conflict", "EXPERIENCE.md changed; read it again before replacing")
        if text and not text.endswith("\n"):
            text += "\n"
        atomic_write_text(path, text, encoding="utf-8")
        return read_experience_path(path)


def append_experience(group: Group, *, content: str) -> ExperienceDocument:
    entry = str(content or "").strip()
    if not entry:
        raise ExperienceError("missing_content", "experience content is required")
    path = resolve_experience_path(group)
    with _path_lock(path):
        current = ensure_experience_file(path.parent)
        normalized_entry = entry.replace("\r\n", "\n").replace("\r", "\n")
        normalized_current = current.content.replace("\r\n", "\n").replace("\r", "\n")
        if normalized_entry in normalized_current:
            return current
        merged = current.content.rstrip() + "\n\n" + entry + "\n"
        _validate_content(merged)
        atomic_write_text(path, merged, encoding="utf-8")
        return read_experience_path(path)
