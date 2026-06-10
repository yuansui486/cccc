from __future__ import annotations

import os
from typing import Any, Dict
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from ..codex_client_config import sync_codex_custom_provider_config
from ..schemas import AccountLoginRequest, AccountSelfRequest, RouteContext

_ACCOUNT_TIMEOUT = 15.0
_DEFAULT_SMART_OPS_BASE_URL = "https://dongdongkc.shierkeji.com:6201"
_DEFAULT_OCS_PUBLIC_BASE_URL = "https://dongdongkc.shierkeji.com:6201/ocs"
_CLIENT_CONFIG_ERROR_MESSAGE = "Login succeeded, but local Codex config.toml could not be verified."


def _normalize_base_url(raw: str, *, code: str, label: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=503, detail={"code": code, "message": f"{label} must be an absolute http(s) URL"})
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _api_v1_base_url(raw: str, *, service_path: str, code: str, label: str) -> str:
    base_url = _normalize_base_url(raw, code=code, label=label)
    lowered = base_url.rstrip("/").lower()
    if lowered.endswith("/api/v1"):
        return base_url
    service_suffix = f"/{service_path.strip('/')}".lower()
    if lowered.endswith(service_suffix):
        return f"{base_url}/api/v1"
    return f"{base_url}/{service_path.strip('/')}/api/v1"


def _smart_ops_base_url() -> str:
    return _normalize_base_url(
        os.environ.get("ONECOLLEAGUE_SMART_OPS_BASE_URL")
        or os.environ.get("SMART_OPS_BASE_URL")
        or _DEFAULT_SMART_OPS_BASE_URL,
        code="invalid_smart_ops_base_url",
        label="SMART_OPS_BASE_URL",
    )


def _ua2_base_url() -> str:
    return _api_v1_base_url(
        os.environ.get("ONECOLLEAGUE_UA2_BASE_URL") or _smart_ops_base_url(),
        service_path="ua2",
        code="invalid_ua2_base_url",
        label="ONECOLLEAGUE_UA2_BASE_URL",
    )


def _ocs_base_url() -> str:
    return _api_v1_base_url(
        os.environ.get("ONECOLLEAGUE_OCS_BASE_URL")
        or os.environ.get("OCS_BASE_URL")
        or _DEFAULT_OCS_PUBLIC_BASE_URL,
        service_path="ocs",
        code="invalid_ocs_base_url",
        label="ONECOLLEAGUE_OCS_BASE_URL",
    )


def _default_tenant_code() -> str:
    return str(os.environ.get("ONECOLLEAGUE_UA2_TENANT_CODE") or os.environ.get("UA2_TENANT_CODE") or "").strip()


def _extract_error_message(payload: Any, *, fallback: str) -> str:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, dict):
            message = str(detail.get("message") or "").strip()
            if message:
                return message
        message = str(payload.get("message") or "").strip()
        if message:
            return message
        error = payload.get("error")
        if isinstance(error, dict):
            nested = str(error.get("message") or "").strip()
            if nested:
                return nested
    return fallback


async def _json_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    stage: str,
    json: Dict[str, Any] | None = None,
    params: Dict[str, Any] | None = None,
    access_token: str = "",
) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
    resp = await client.request(method, url, json=json, params=params, headers=headers)
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=resp.status_code if resp.status_code in {401, 403, 422} else 502,
            detail={
                "code": "account_upstream_error",
                "message": _extract_error_message(payload, fallback=f"upstream returned HTTP {resp.status_code}"),
                "stage": stage,
                "url": url,
            },
        )
    return payload if isinstance(payload, dict) else {}


def _unwrap_ocs_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("ok") is False:
        error = payload.get("error")
        raise HTTPException(
            status_code=502,
            detail={
                "code": "ocs_request_failed",
                "message": _extract_error_message(error, fallback="OCS request failed"),
            },
        )
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _normalize_session(*, access_token: str, ua2_me: Dict[str, Any], ocs_account: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "base_url": _smart_ops_base_url(),
        "access_token": access_token,
        "codex_api_key": str(ocs_account.get("codex_api_key") or "").strip() or None,
        "codex_model": str(ocs_account.get("codex_model") or "").strip() or None,
        "username": str(ua2_me.get("username") or ocs_account.get("ua2_username") or "").strip(),
        "display_name": str(ua2_me.get("display_name") or "").strip(),
        "group": str(ocs_account.get("donehub_group") or "").strip(),
        "quota": int(ocs_account.get("quota") or 0),
        "used_quota": int(ocs_account.get("used_quota") or 0),
        "role": 0,
        "status": 1 if str(ua2_me.get("status") or "").strip().lower() in {"enabled", "normal", "1"} else 0,
        "allow_multi_client_login": bool(ocs_account.get("allow_multi_client_login", True)),
        "onecolleague_session_version": int(ocs_account.get("onecolleague_session_version") or 0),
        "ua2_actor_type": str(ua2_me.get("actor_type") or "").strip(),
        "ua2_user_id": str(ua2_me.get("id") or "").strip(),
        "ua2_tenant_id": str(ua2_me.get("tenant_id") or "").strip(),
        "donehub_username": str(ocs_account.get("donehub_username") or "").strip(),
    }


async def _sync_codex_config_for_session(session: Dict[str, Any]) -> None:
    if not str(session.get("codex_api_key") or "").strip():
        return
    await run_in_threadpool(sync_codex_custom_provider_config)


async def _fetch_account_session(
    access_token: str,
    *,
    start_onecolleague_session: bool = False,
    onecolleague_session_version: int | None = None,
) -> Dict[str, Any]:
    token = str(access_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail={"code": "missing_access_token", "message": "missing access_token"})
    try:
        async with httpx.AsyncClient(timeout=_ACCOUNT_TIMEOUT, follow_redirects=True) as client:
            ua2_me = await _json_request(client, "GET", f"{_ua2_base_url()}/auth/me", stage="ua2_self", access_token=token)
            if start_onecolleague_session:
                ocs_payload = await _json_request(
                    client,
                    "POST",
                    f"{_ocs_base_url()}/donehub/ensure",
                    stage="ocs_donehub_ensure",
                    json={"start_onecolleague_session": True},
                    access_token=token,
                )
            else:
                params = None
                if onecolleague_session_version is not None:
                    params = {"onecolleague_session_version": int(onecolleague_session_version)}
                ocs_payload = await _json_request(
                    client,
                    "GET",
                    f"{_ocs_base_url()}/donehub/me",
                    stage="ocs_donehub_me",
                    params=params,
                    access_token=token,
                )
            ocs_account = _unwrap_ocs_result(ocs_payload)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        error_code = str(detail.get("code") or "account_upstream_error")
        if int(exc.status_code or 0) in {401, 403}:
            error_code = "session_expired"
        return {
            "ok": False,
            "error": {
                "code": error_code,
                "message": str(detail.get("message") or "account upstream request failed"),
                "details": {
                    "stage": str(detail.get("stage") or ""),
                    "url": str(detail.get("url") or ""),
                },
            },
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": {"code": "account_network_error", "message": str(exc)}}
    session = _normalize_session(access_token=token, ua2_me=ua2_me, ocs_account=ocs_account)
    try:
        await _sync_codex_config_for_session(session)
    except (OSError, ValueError, TypeError):
        return {
            "ok": False,
            "error": {
                "code": "account_client_config_failed",
                "message": _CLIENT_CONFIG_ERROR_MESSAGE,
            },
        }
    return {"ok": True, "result": {"session": session}}


def create_routers(ctx: RouteContext) -> list[APIRouter]:
    router = APIRouter(prefix="/api/v1/account")

    @router.post("/login")
    async def account_login(req: AccountLoginRequest) -> Dict[str, Any]:
        if ctx.read_only:
            raise HTTPException(status_code=403, detail={"code": "read_only", "message": "account login is disabled in read-only mode"})
        tenant_code = str(req.tenant_code or "").strip() or _default_tenant_code()
        username = str(req.username or "").strip()
        password = str(req.password or "")
        if not tenant_code:
            raise HTTPException(status_code=400, detail={"code": "missing_tenant_code", "message": "missing tenant_code"})
        if not username:
            raise HTTPException(status_code=400, detail={"code": "missing_username", "message": "missing username"})
        if not password:
            raise HTTPException(status_code=400, detail={"code": "missing_password", "message": "missing password"})
        try:
            async with httpx.AsyncClient(timeout=_ACCOUNT_TIMEOUT, follow_redirects=True) as client:
                login_payload = await _json_request(
                    client,
                    "POST",
                    f"{_ua2_base_url()}/auth/tenant-user/login",
                    stage="ua2_login",
                    json={"tenant_code": tenant_code, "username": username, "password": password},
                )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return {
                "ok": False,
                "error": {
                    "code": str(detail.get("code") or "ua2_login_failed"),
                    "message": str(detail.get("message") or "UA2 login failed"),
                    "details": {
                        "stage": str(detail.get("stage") or "ua2_login"),
                        "url": str(detail.get("url") or ""),
                    },
                },
            }
        except httpx.HTTPError as exc:
            return {"ok": False, "error": {"code": "account_network_error", "message": str(exc)}}
        access_token = str(login_payload.get("access_token") or "").strip()
        if not access_token:
            return {"ok": False, "error": {"code": "ua2_missing_access_token", "message": "UA2 login response did not include access_token"}}
        return await _fetch_account_session(access_token, start_onecolleague_session=True)

    @router.post("/self")
    async def account_self(req: AccountSelfRequest) -> Dict[str, Any]:
        return await _fetch_account_session(
            req.access_token,
            onecolleague_session_version=req.onecolleague_session_version,
        )

    return [router]
