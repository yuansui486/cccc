from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException

from ..schemas import DoneHubLoginRequest, DoneHubSelfRequest, RouteContext

_DONE_HUB_TIMEOUT = 15.0
_TOKEN_PAGE_SIZE = 100
_CODEX_BASE_URL = "https://peer.shierkeji.com/v1"
_GEMINI_BASE_URL = "https://peer.shierkeji.com/gemini"
_CLIENT_CONFIG_ERROR_MESSAGE = "登录成功，但本机客户端配置写入失败，请稍后重试或联系管理员。"


class _DoneHubClientConfigError(Exception):
    pass


def _normalize_base_url(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail={"code": "missing_base_url", "message": "missing base_url"})
    value = value.rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_base_url", "message": "base_url must be an absolute http(s) URL"},
        )
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _extract_error_message(payload: Any, *, fallback: str) -> str:
    if isinstance(payload, dict):
        message = str(payload.get("message") or "").strip()
        if message:
            return message
        error = payload.get("error")
        if isinstance(error, dict):
            nested = str(error.get("message") or "").strip()
            if nested:
                return nested
    return fallback


def _extract_ok(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get("success"))


def _parse_json_response(resp: httpx.Response) -> Tuple[bool, Dict[str, Any] | None, str]:
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if resp.status_code >= 400:
        return False, payload if isinstance(payload, dict) else None, _extract_error_message(
            payload,
            fallback=f"done-hub returned HTTP {resp.status_code}",
        )
    if not _extract_ok(payload):
        return False, payload if isinstance(payload, dict) else None, _extract_error_message(
            payload,
            fallback="done-hub request failed",
        )
    return True, payload if isinstance(payload, dict) else None, ""


def _normalize_profile(base_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    data = payload.get("data")
    record = data if isinstance(data, dict) else {}
    access_token = str(record.get("access_token") or "").strip()
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail={"code": "done_hub_missing_access_token", "message": "done-hub self response did not include access_token"},
        )
    return {
        "base_url": base_url,
        "access_token": access_token,
        "username": str(record.get("username") or "").strip(),
        "display_name": str(record.get("display_name") or "").strip(),
        "group": str(record.get("group") or "").strip(),
        "quota": int(record.get("quota") or 0),
        "used_quota": int(record.get("used_quota") or 0),
        "role": int(record.get("role") or 0),
        "status": int(record.get("status") or 0),
    }


def _auth_headers(access_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _extract_token_rows(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    data = payload.get("data")
    record = data if isinstance(data, dict) else {}
    rows = record.get("data")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _find_named_token(rows: list[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    target = str(name or "").strip()
    if not target:
        return None
    matches: list[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("name") or "").strip() == target:
            matches.append(row)
    if not matches:
        return None
    matches.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
    return matches[0]


def _extract_token_key(row: Dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("key") or "").strip()


async def _fetch_token_rows(client: httpx.AsyncClient, *, base_url: str, access_token: str) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    page = 1
    total_count: int | None = None
    while True:
        resp = await client.get(
            f"{base_url}/api/token/",
            params={"page": page, "size": _TOKEN_PAGE_SIZE, "keyword": "", "order": "-id"},
            headers=_auth_headers(access_token),
        )
        ok, payload, error = _parse_json_response(resp)
        if not ok or payload is None:
            raise _DoneHubClientConfigError(error or _CLIENT_CONFIG_ERROR_MESSAGE)
        rows = _extract_token_rows(payload)
        out.extend(rows)
        data = payload.get("data")
        record = data if isinstance(data, dict) else {}
        if total_count is None:
            total_count = int(record.get("total_count") or 0)
        if not rows or len(rows) < _TOKEN_PAGE_SIZE:
            break
        if total_count > 0 and len(out) >= total_count:
            break
        page += 1
    return out


async def _create_named_token(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    access_token: str,
    name: str,
    quota: int,
) -> None:
    remain_quota = max(int(quota or 0), 0)
    resp = await client.post(
        f"{base_url}/api/token/",
        json={
            "name": name,
            "expired_time": -1,
            "remain_quota": remain_quota,
            "unlimited_quota": True,
        },
        headers=_auth_headers(access_token),
    )
    ok, _payload, error = _parse_json_response(resp)
    if not ok:
        raise _DoneHubClientConfigError(error or _CLIENT_CONFIG_ERROR_MESSAGE)


async def _ensure_named_token(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    access_token: str,
    name: str,
    quota: int,
) -> str:
    rows = await _fetch_token_rows(client, base_url=base_url, access_token=access_token)
    row = _find_named_token(rows, name)
    key = _extract_token_key(row)
    if key:
        return key
    await _create_named_token(client, base_url=base_url, access_token=access_token, name=name, quota=quota)
    rows = await _fetch_token_rows(client, base_url=base_url, access_token=access_token)
    row = _find_named_token(rows, name)
    key = _extract_token_key(row)
    if key:
        return key
    raise _DoneHubClientConfigError(f"missing {name} token key")


async def _fetch_available_models(client: httpx.AsyncClient, *, base_url: str) -> Dict[str, Any]:
    resp = await client.get(f"{base_url}/api/available_model")
    ok, payload, error = _parse_json_response(resp)
    if not ok or payload is None:
        raise _DoneHubClientConfigError(error or _CLIENT_CONFIG_ERROR_MESSAGE)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise _DoneHubClientConfigError("available_model response missing data")
    return data


def _pick_first_model_name(models: Dict[str, Any], prefix: str) -> str:
    wanted = str(prefix or "").strip().lower()
    for name in models.keys():
        candidate = str(name or "").strip()
        if candidate.lower().startswith(wanted):
            return candidate
    raise _DoneHubClientConfigError(f"missing model with prefix {prefix}")


def _codex_auth_content(raw_key: str) -> str:
    api_key = str(raw_key or "").strip()
    if not api_key:
        raise _DoneHubClientConfigError("missing codex token key")
    if not api_key.startswith("sk-"):
        api_key = f"sk-{api_key}"
    return json.dumps({"OPENAI_API_KEY": api_key}, ensure_ascii=False, indent=2) + "\n"


def _codex_config_prefix(model_name: str) -> str:
    model = str(model_name or "").strip()
    if not model:
        raise _DoneHubClientConfigError("missing gpt model name")
    return (
        f'model_provider = "custom"\n'
        f'model = "{model}"\n'
        'model_reasoning_effort = "high"\n'
        "disable_response_storage = true\n"
        "\n"
        "[model_providers.custom]\n"
        'name = "custom"\n'
        'wire_api = "responses"\n'
        "requires_openai_auth = true\n"
        f'base_url = "{_CODEX_BASE_URL}"\n'
    )


def _merge_codex_config(existing: str, model_name: str) -> str:
    prefix = _codex_config_prefix(model_name)
    text = str(existing or "")
    match = re.search(r"(?m)^\[projects\b", text)
    if not match:
        return prefix
    suffix = text[match.start():]
    return f"{prefix}\n{suffix}"


def _gemini_env_content(raw_key: str, model_name: str) -> str:
    api_key = str(raw_key or "").strip()
    model = str(model_name or "").strip()
    if not api_key:
        raise _DoneHubClientConfigError("missing gemini token key")
    if not model:
        raise _DoneHubClientConfigError("missing gemini model name")
    return (
        f"GOOGLE_GEMINI_BASE_URL={_GEMINI_BASE_URL}\n"
        f"GEMINI_API_KEY={api_key}\n"
        f"GEMINI_MODEL={model}\n"
    )


def _gemini_settings_content() -> str:
    return (
        "{\n"
        '  "ide": {\n'
        '    "enabled": true\n'
        "  },\n"
        '  "security": {\n'
        '    "auth": {\n'
        '      "selectedType": "gemini-api-key"\n'
        "    }\n"
        "  }\n"
        "}\n"
    )


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8", newline="\n")
    tmp_path.replace(path)


def _sync_local_client_files(*, codex_key: str, codex_model: str, gemini_key: str, gemini_model: str) -> None:
    home_dir = Path.home()
    codex_dir = home_dir / ".codex"
    gemini_dir = home_dir / ".gemini"
    codex_auth_path = codex_dir / "auth.json"
    codex_config_path = codex_dir / "config.toml"
    gemini_env_path = gemini_dir / ".env"
    gemini_settings_path = gemini_dir / "settings.json"

    existing_codex_config = ""
    if codex_config_path.exists():
        existing_codex_config = codex_config_path.read_text(encoding="utf-8")

    _write_text_atomic(codex_auth_path, _codex_auth_content(codex_key))
    _write_text_atomic(codex_config_path, _merge_codex_config(existing_codex_config, codex_model))
    _write_text_atomic(gemini_env_path, _gemini_env_content(gemini_key, gemini_model))
    _write_text_atomic(gemini_settings_path, _gemini_settings_content())


async def _configure_local_clients(client: httpx.AsyncClient, *, base_url: str, session: Dict[str, Any]) -> None:
    group = str(session.get("group") or "").strip().lower()
    if group == "pro":
        return

    access_token = str(session.get("access_token") or "").strip()
    if not access_token:
        raise _DoneHubClientConfigError("missing access token")
    quota = int(session.get("quota") or 0)

    codex_key = await _ensure_named_token(
        client,
        base_url=base_url,
        access_token=access_token,
        name="codex",
        quota=quota,
    )
    gemini_key = await _ensure_named_token(
        client,
        base_url=base_url,
        access_token=access_token,
        name="gemini",
        quota=quota,
    )
    models = await _fetch_available_models(client, base_url=base_url)
    codex_model = _pick_first_model_name(models, "gpt")
    gemini_model = _pick_first_model_name(models, "gemini")
    try:
        _sync_local_client_files(
            codex_key=codex_key,
            codex_model=codex_model,
            gemini_key=gemini_key,
            gemini_model=gemini_model,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        raise _DoneHubClientConfigError(_CLIENT_CONFIG_ERROR_MESSAGE) from None


def create_routers(ctx: RouteContext) -> list[APIRouter]:
    router = APIRouter(prefix="/api/v1/done_hub")

    @router.post("/login")
    async def done_hub_login(req: DoneHubLoginRequest) -> Dict[str, Any]:
        if ctx.read_only:
            raise HTTPException(
                status_code=403,
                detail={"code": "read_only", "message": "done-hub login is disabled in read-only mode"},
            )

        base_url = _normalize_base_url(req.base_url)
        username = str(req.username or "").strip()
        password = str(req.password or "")
        if not username:
            raise HTTPException(status_code=400, detail={"code": "missing_username", "message": "missing username"})
        if not password:
            raise HTTPException(status_code=400, detail={"code": "missing_password", "message": "missing password"})

        try:
            async with httpx.AsyncClient(timeout=_DONE_HUB_TIMEOUT, follow_redirects=True) as client:
                login_resp = await client.post(
                    f"{base_url}/api/user/login",
                    json={"username": username, "password": password},
                )
                login_ok, _login_payload, login_error = _parse_json_response(login_resp)
                if not login_ok:
                    return {"ok": False, "error": {"code": "done_hub_login_failed", "message": login_error}}

                self_resp = await client.get(f"{base_url}/api/user/self")
                self_ok, self_payload, self_error = _parse_json_response(self_resp)
                if not self_ok or self_payload is None:
                    return {"ok": False, "error": {"code": "done_hub_self_failed", "message": self_error}}
                session = _normalize_profile(base_url, self_payload)
                await _configure_local_clients(client, base_url=base_url, session=session)
        except _DoneHubClientConfigError:
            return {
                "ok": False,
                "error": {
                    "code": "done_hub_client_config_failed",
                    "message": _CLIENT_CONFIG_ERROR_MESSAGE,
                },
            }
        except httpx.HTTPError as exc:
            return {"ok": False, "error": {"code": "done_hub_network_error", "message": str(exc)}}

        return {"ok": True, "result": {"session": session}}

    @router.post("/self")
    async def done_hub_self(req: DoneHubSelfRequest) -> Dict[str, Any]:
        base_url = _normalize_base_url(req.base_url)
        access_token = str(req.access_token or "").strip()
        if not access_token:
            raise HTTPException(
                status_code=400,
                detail={"code": "missing_access_token", "message": "missing access_token"},
            )

        try:
            async with httpx.AsyncClient(timeout=_DONE_HUB_TIMEOUT, follow_redirects=True) as client:
                self_resp = await client.get(
                    f"{base_url}/api/user/self",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                self_ok, self_payload, self_error = _parse_json_response(self_resp)
                if not self_ok or self_payload is None:
                    return {"ok": False, "error": {"code": "done_hub_self_failed", "message": self_error}}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": {"code": "done_hub_network_error", "message": str(exc)}}

        return {"ok": True, "result": {"session": _normalize_profile(base_url, self_payload)}}

    return [router]
