from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response, StreamingResponse

from ..schemas import RouteContext, require_admin, require_group, require_user

_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade", "host"}
_ADMIN_PATHS = {
    ("POST", "/rules"), ("PATCH", "/rules/model"), ("PATCH", "/rules/builtin"),
    ("PATCH", "/rules"), ("DELETE", "/rules"),
    ("POST", "/custom-sensitive"), ("PATCH", "/custom-sensitive"), ("DELETE", "/custom-sensitive"),
    ("POST", "/rule-categories"), ("PATCH", "/rule-categories"), ("DELETE", "/rule-categories"),
    ("PUT", "/policies"), ("POST", "/policies/reset"),
    ("POST", "/models/initialize"),
}

def _admin_operation(method: str, path: str) -> bool:
    return any(method == known and (path == prefix or path.startswith(prefix + "/")) for known, prefix in _ADMIN_PATHS)

def _unavailable(manager) -> Response:
    state = manager.status()
    phase = str(state.get("service_phase") or "stopped")
    code = state.get("service_error_code") or {"installing": "desensitization_installing", "starting": "desensitization_starting"}.get(phase, "desensitization_service_unavailable")
    message = state.get("service_error_message") or code
    return Response(json.dumps({"ok": False, "error": {"code": code, "message": message, "details": state}}, ensure_ascii=False), status_code=503, media_type="application/json")

async def _proxy(request: Request, ctx: RouteContext, relative_path: str, *, admin: bool = False) -> Response:
    if admin:
        require_admin(request)
    manager = ctx.desensitization_sidecar
    if manager is not None and request.method == "POST" and relative_path == "/api/v1/desensitization/service/retry":
        started = manager.retry()
        return Response(json.dumps({"ok": True, "result": {**manager.status(), "started": started}}, ensure_ascii=False), status_code=202, media_type="application/json")
    if manager is not None and relative_path == "/api/v1/desensitization/models/status" and not manager.ensure_started():
        state = manager.status()
        state["status"] = "starting" if state["service_phase"] in {"installing", "starting"} else "error"
        return Response(json.dumps({"ok": True, "result": state}, ensure_ascii=False), media_type="application/json")
    if manager is None or not manager.ensure_started():
        return _unavailable(manager) if manager is not None else Response(
            json.dumps({"ok": False, "error": {"code": "desensitization_service_unavailable", "message": "sidecar unavailable", "details": {}}}),
            status_code=503, media_type="application/json",
        )
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP and k.lower() != "authorization"}
    headers["authorization"] = f"Bearer {manager.token}"
    target = manager.url(relative_path, request.url.query)

    async def body() -> AsyncIterator[bytes]:
        async for chunk in request.stream():
            yield chunk

    # Loopback traffic must never be sent through a user-configured HTTP
    # proxy.  ``trust_env=False`` also makes local startup deterministic on
    # machines whose proxy excludes 127.0.0.1 inconsistently.
    client = httpx.AsyncClient(timeout=None, trust_env=False)
    try:
        upstream_request = client.build_request(request.method, target, headers=headers, content=body())
        upstream = await client.send(upstream_request, stream=True)
    except Exception as exc:
        await client.aclose()
        # A transient connection error should not poison an otherwise healthy
        # manager.  Only mark the service failed when the child is actually
        # gone; the next request can then retry while it remains alive.
        process = getattr(manager, "process", None)
        if process is None or process.poll() is not None:
            manager.mark_failed("desensitization_service_unavailable", str(exc))
        return _unavailable(manager)
    if relative_path == "/api/v1/desensitization/models/status":
        upstream_headers = dict(upstream.headers)
        raw = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        try:
            payload = json.loads(raw.decode("utf-8"))
            manager_state = ctx.desensitization_sidecar.status()
            if isinstance(payload, dict):
                # Attach the host lifecycle state to both successful and
                # failed model responses.  The component may return HTTP 503
                # while dependencies/models are unavailable; without this
                # merge the browser loses the installation log and phase.
                if isinstance(payload.get("result"), dict):
                    payload["result"] = {**payload["result"], **manager_state}
                elif isinstance(payload.get("error"), dict):
                    error = dict(payload["error"])
                    details = error.get("details")
                    details = dict(details) if isinstance(details, dict) else {}
                    details.update(manager_state)
                    error["details"] = details
                    payload["error"] = error
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        except Exception:
            pass
        headers = {k: v for k, v in upstream_headers.items() if k.lower() not in _HOP_BY_HOP and k.lower() not in {"content-length", "content-encoding"}}
        return Response(raw, status_code=upstream.status_code, headers=headers, media_type="application/json")
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
    return StreamingResponse(stream(), status_code=upstream.status_code, headers=response_headers)

def create_routers(ctx: RouteContext) -> list[APIRouter]:
    global_router = APIRouter(prefix="/api/v1/desensitization", dependencies=[Depends(require_user)])
    group_router = APIRouter(prefix="/api/v1/groups/{group_id}/desensitization", dependencies=[Depends(require_group)])

    @global_router.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"])
    async def global_proxy(request: Request, path: str) -> Response:
        return await _proxy(request, ctx, "/api/v1/desensitization/" + path, admin=_admin_operation(request.method, "/" + path))

    @group_router.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"])
    async def group_proxy(request: Request, group_id: str, path: str) -> Response:
        del group_id
        return await _proxy(request, ctx, request.url.path, admin=_admin_operation(request.method, "/" + path))
    return [global_router, group_router]
