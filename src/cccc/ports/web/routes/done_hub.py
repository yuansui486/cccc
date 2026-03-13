from __future__ import annotations

from typing import Any, Dict, Tuple
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException

from ..schemas import DoneHubLoginRequest, DoneHubSelfRequest, RouteContext

_DONE_HUB_TIMEOUT = 15.0


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
        except httpx.HTTPError as exc:
            return {"ok": False, "error": {"code": "done_hub_network_error", "message": str(exc)}}

        return {"ok": True, "result": {"session": _normalize_profile(base_url, self_payload)}}

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
