"""OneColleague provider configuration for OpenCode actors."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..paths import ensure_home
from ..util.fs import atomic_write_json, read_json

OPENCODE_PROVIDER_ID = "onecolleague"
OPENCODE_PROVIDER_NAME = "OneColleague"
OPENCODE_DEFAULT_BASE_URL = "https://peer.shierkeji.com/v1"
OPENCODE_API_KEY_ENV = "ONECOLLEAGUE_API_KEY"
OPENCODE_MODEL_CATALOG_TTL_SECONDS = 600
OPENCODE_MODEL_CATALOG_TIMEOUT_SECONDS = 1.5

# Keep a useful offline fallback. The server catalog is authoritative when it is available.
OPENCODE_FALLBACK_MODELS = (
    "gpt-5.4",
    "gpt-5.5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "qwen3.6-plus",
    "qwen3.6-flash",
    "GLM-4.7",
    "doubao-seed-2-0-pro-260215",
    "kimi-k2.6",
)


def opencode_base_url(env: Dict[str, Any] | None = None) -> str:
    values = env if isinstance(env, dict) else os.environ
    raw = str(values.get("ONECOLLEAGUE_OPENCODE_BASE_URL") or OPENCODE_DEFAULT_BASE_URL).strip()
    return raw.rstrip("/") or OPENCODE_DEFAULT_BASE_URL


def _catalog_url(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return "https://peer.shierkeji.com/api/available_model"
    root = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    return f"{root}/api/available_model"


def _cache_path(env: Dict[str, Any] | None = None) -> Path:
    values = env if isinstance(env, dict) else os.environ
    raw_home = str(values.get("ONECOLLEAGUE_HOME") or values.get("CCCC_HOME") or "").strip()
    home = Path(raw_home).expanduser() if raw_home else ensure_home()
    return home / "state" / "cache" / "opencode_models.json"


def _normalize_model_rows(payload: Any) -> list[Dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        rows = [(str(key), value) for key, value in data.items()]
    elif isinstance(data, list):
        rows = [(str(item), {}) if not isinstance(item, dict) else (str(item.get("model") or item.get("id") or item.get("name") or ""), item) for item in data]

    out: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for name, value in rows:
        model = name.strip()
        if isinstance(value, dict):
            model = str(value.get("model") or model).strip()
            price = value.get("price") if isinstance(value.get("price"), dict) else value
            locked = bool(price.get("locked")) if isinstance(price, dict) else False
        else:
            locked = False
        key = model.lower()
        if not model or key in seen:
            continue
        seen.add(key)
        out.append({"model": model, "locked": locked})
    return out


def _fallback_catalog() -> list[Dict[str, Any]]:
    return [{"model": model, "locked": False} for model in OPENCODE_FALLBACK_MODELS]


def _read_cached_catalog(path: Path) -> tuple[float, list[Dict[str, Any]]]:
    payload = read_json(path)
    fetched_at = float(payload.get("fetched_at") or 0) if isinstance(payload, dict) else 0.0
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return fetched_at, []
    normalized = _normalize_model_rows({"data": models})
    return fetched_at, normalized


def _write_cached_catalog(path: Path, models: list[Dict[str, Any]]) -> None:
    try:
        atomic_write_json(path, {"fetched_at": time.time(), "models": models})
    except Exception:
        pass


def get_opencode_model_catalog(env: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
    """Return the server model catalog with cache and offline fallback."""
    path = _cache_path(env)
    fetched_at, cached = _read_cached_catalog(path)
    now = time.time()
    if cached and now - fetched_at < OPENCODE_MODEL_CATALOG_TTL_SECONDS:
        return cached

    try:
        response = httpx.get(
            _catalog_url(opencode_base_url(env)),
            timeout=OPENCODE_MODEL_CATALOG_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        fresh = _normalize_model_rows(response.json())
        if fresh:
            _write_cached_catalog(path, fresh)
            return fresh
    except Exception:
        pass
    return cached or _fallback_catalog()


def opencode_model_ids(env: Dict[str, Any] | None = None) -> list[str]:
    return [str(item.get("model") or "").strip() for item in get_opencode_model_catalog(env) if str(item.get("model") or "").strip()]


def _model_config(models: Iterable[str]) -> Dict[str, Dict[str, str]]:
    return {model: {"name": model} for model in models if str(model or "").strip()}


def merge_opencode_provider_config(doc: Dict[str, Any], env: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Add the managed OneColleague provider while preserving user config."""
    result = dict(doc or {})
    providers = result.get("provider")
    providers = dict(providers) if isinstance(providers, dict) else {}
    current = providers.get(OPENCODE_PROVIDER_ID)
    current = dict(current) if isinstance(current, dict) else {}
    options = current.get("options")
    options = dict(options) if isinstance(options, dict) else {}
    options["baseURL"] = opencode_base_url(env)
    options["apiKey"] = f"{{env:{OPENCODE_API_KEY_ENV}}}"
    current.update(
        {
            "npm": "@ai-sdk/openai-compatible",
            "name": OPENCODE_PROVIDER_NAME,
            "options": options,
            "models": _model_config(opencode_model_ids(env)),
        }
    )
    providers[OPENCODE_PROVIDER_ID] = current
    result["provider"] = providers
    return result
