"""Web adapters for the signed Group Bridge session operations.

The Web layer owns authentication and request shaping only. Session
validation, authorization, signing, and local user-only delivery remain in
the daemon operation boundary.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ....contracts.v1.group_bridge import (
    GroupBridgePairingConnectionEnvelope,
    GroupBridgePairingRequestEnvelope,
    GroupBridgeSessionMessage,
    GroupBridgeSignedMessageEnvelope,
)
from ..middleware import get_access_token_cookie
from ..schemas import RouteContext, check_group, require_user

_MAX_PUBLIC_ENVELOPE_BYTES = 256_000


def _unique_json_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if not isinstance(key, str) or key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


async def _bounded_public_json(request: Request) -> Dict[str, Any]:
    encoding = str(request.headers.get("content-encoding") or "").strip().lower()
    if encoding not in {"", "identity"}:
        raise HTTPException(status_code=400, detail={"code": "invalid_envelope", "message": "invalid envelope"})
    length = str(request.headers.get("content-length") or "").strip()
    if length:
        if not length.isascii() or not length.isdigit():
            raise HTTPException(status_code=400, detail={"code": "invalid_envelope", "message": "invalid envelope"})
        if int(length) > _MAX_PUBLIC_ENVELOPE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "envelope_too_large", "message": "envelope too large"})
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        if not isinstance(chunk, bytes):
            raise HTTPException(status_code=400, detail={"code": "invalid_envelope", "message": "invalid envelope"})
        size += len(chunk)
        if size > _MAX_PUBLIC_ENVELOPE_BYTES:
            raise HTTPException(status_code=413, detail={"code": "envelope_too_large", "message": "envelope too large"})
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_json_object)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_envelope", "message": "invalid envelope"},
        ) from None
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail={"code": "invalid_envelope", "message": "invalid envelope"})
    return value


async def _public_model(request: Request, model_type: Any) -> Any:
    try:
        return model_type.model_validate(await _bounded_public_json(request))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_envelope", "message": "invalid envelope"},
        ) from None


class GroupBridgeSessionSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1, max_length=256)
    local_endpoint: str = Field(min_length=1, max_length=2048)
    remote_group_id: str = Field(min_length=1, max_length=256)
    remote_peer_id: str = Field(min_length=1, max_length=256)
    remote_endpoint: str = Field(default="", max_length=2048)
    client_nonce: str = Field(min_length=43, max_length=43)
    payload: Dict[str, Any]


class GroupBridgeRemoteSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    group_id: str = Field(min_length=1, max_length=256)
    registration_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(pattern=r"gbs_[0-9a-f]{32}")
    payload: GroupBridgeSessionMessage


class GroupBridgeRemoteStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    group_id: str = Field(min_length=1, max_length=256)
    registration_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str = Field(pattern=r"gbs_[0-9a-f]{32}")


class GroupBridgeManagementGroupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    group_id: str = Field(min_length=1, max_length=256)


class GroupBridgePairingInviteRequest(GroupBridgeManagementGroupRequest):
    expected_remote_group_id: str = Field(default="", max_length=256)
    expected_remote_peer_id: str = Field(default="", max_length=256)
    multiaddrs: list[str] = Field(default_factory=list, max_length=32)
    ttl_seconds: int = Field(default=600, ge=60, le=3600)


class GroupBridgePairingConnectionRequest(GroupBridgePairingInviteRequest):
    pass


class GroupBridgeRemotePairingSubmitRequest(GroupBridgeManagementGroupRequest):
    group_title: str = Field(default="", max_length=256)
    connection: GroupBridgePairingConnectionEnvelope


class GroupBridgePairingRequestAction(GroupBridgeManagementGroupRequest):
    pass


class GroupBridgePairingRejectRequest(GroupBridgePairingRequestAction):
    reason: str = Field(default="", max_length=1024)


class GroupBridgeTrustAccessRequest(GroupBridgeManagementGroupRequest):
    access_level: Literal["messages", "read", "full"]
    expected_revision: int = Field(ge=0)


class GroupBridgeTrustRevokeRequest(GroupBridgeManagementGroupRequest):
    expected_revision: int = Field(ge=0)


def _daemon_error(resp: Dict[str, Any]) -> HTTPException:
    error = resp.get("error") if isinstance(resp, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "group_bridge_session_failed")
    message = str(error.get("message") or "Group Bridge session operation failed")
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    status = 503 if code in {"daemon_unavailable", "transport_unavailable", "remote_receipt_store_corrupt"} else 400
    return HTTPException(status_code=status, detail={"code": code, "message": message, "details": details})


def _unwrap_daemon(resp: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(resp, dict) or not resp.get("ok"):
        raise _daemon_error(resp)
    result = resp.get("result")
    return result if isinstance(result, dict) else {}


def _request_access_token(request: Request) -> str:
    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return str(authorization[7:] or "").strip()
    cookie = get_access_token_cookie(request)
    if cookie:
        return cookie
    return str(request.query_params.get("token") or "").strip()


def _management_args(request: Request, values: Dict[str, Any]) -> Dict[str, Any]:
    args = dict(values)
    args["access_token"] = _request_access_token(request)
    return args


def create_routers(ctx: RouteContext) -> list[APIRouter]:
    management_router = APIRouter(
        prefix="/api/group-bridge",
        dependencies=[Depends(require_user)],
    )
    public_router = APIRouter(prefix="/api/group-bridge")

    @management_router.post("/session/send")
    async def group_bridge_session_send(
        request: Request,
        req: GroupBridgeSessionSendRequest,
    ) -> Dict[str, Any]:
        # Authentication is established by the Web middleware; this second
        # check prevents a token from reaching a group it cannot manage.
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_session_send",
                "args": req.model_dump(),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/remote/send")
    async def group_bridge_remote_send(
        request: Request,
        req: GroupBridgeRemoteSendRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "remote_send",
                "args": req.model_dump(),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/remote/status")
    async def group_bridge_remote_status(
        request: Request,
        req: GroupBridgeRemoteStatusRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "remote_delivery_status",
                "args": req.model_dump(),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.get("/identity")
    async def group_bridge_management_identity(
        request: Request,
        group_id: str = Query(..., min_length=1, max_length=256),
    ) -> Dict[str, Any]:
        check_group(request, group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_identity",
                "args": _management_args(request, {"group_id": group_id}),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.get("/registrations")
    async def group_bridge_management_registrations(
        request: Request,
        group_id: str = Query(..., min_length=1, max_length=256),
    ) -> Dict[str, Any]:
        check_group(request, group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_registrations",
                "args": _management_args(request, {"group_id": group_id}),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.get("/trusts")
    async def group_bridge_management_trusts(
        request: Request,
        group_id: str = Query(..., min_length=1, max_length=256),
    ) -> Dict[str, Any]:
        check_group(request, group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_trusts",
                "args": _management_args(request, {"group_id": group_id}),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.get("/pairing/requests")
    async def group_bridge_management_pairing_requests(
        request: Request,
        group_id: str = Query(..., min_length=1, max_length=256),
    ) -> Dict[str, Any]:
        check_group(request, group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_pairing_requests",
                "args": _management_args(request, {"group_id": group_id}),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/pairing/invites")
    async def group_bridge_management_pairing_invite(
        request: Request,
        req: GroupBridgePairingInviteRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_pairing_invite",
                "args": _management_args(request, req.model_dump()),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/pairing/connections")
    async def group_bridge_management_pairing_connection(
        request: Request,
        req: GroupBridgePairingConnectionRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_pairing_connection",
                "args": _management_args(request, req.model_dump()),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/pairing/remote/submit")
    async def group_bridge_management_pairing_remote_submit(
        request: Request,
        req: GroupBridgeRemotePairingSubmitRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_pairing_remote_submit",
                "args": _management_args(request, req.model_dump()),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/pairing/remote/{outbound_id}/sync")
    async def group_bridge_management_pairing_remote_sync(
        outbound_id: str,
        request: Request,
        req: GroupBridgeManagementGroupRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_pairing_remote_sync",
                "args": _management_args(request, {"group_id": req.group_id, "outbound_id": outbound_id}),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/pairing/requests/{request_id}/approve")
    async def group_bridge_management_pairing_approve(
        request: Request,
        request_id: str,
        req: GroupBridgePairingRequestAction,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_pairing_approve",
                "args": _management_args(request, {"group_id": req.group_id, "request_id": request_id}),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/pairing/requests/{request_id}/reject")
    async def group_bridge_management_pairing_reject(
        request: Request,
        request_id: str,
        req: GroupBridgePairingRejectRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_pairing_reject",
                "args": _management_args(
                    request,
                    {"group_id": req.group_id, "request_id": request_id, "reason": req.reason},
                ),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/trusts/{trust_id}/access")
    async def group_bridge_management_trust_access(
        request: Request,
        trust_id: str,
        req: GroupBridgeTrustAccessRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_trust_access",
                "args": _management_args(
                    request,
                    {
                        "group_id": req.group_id,
                        "trust_id": trust_id,
                        "access_level": req.access_level,
                        "expected_revision": req.expected_revision,
                    },
                ),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @management_router.post("/trusts/{trust_id}/revoke")
    async def group_bridge_management_trust_revoke(
        request: Request,
        trust_id: str,
        req: GroupBridgeTrustRevokeRequest,
    ) -> Dict[str, Any]:
        check_group(request, req.group_id)
        response = await ctx.daemon(
            {
                "op": "group_bridge_management_trust_revoke",
                "args": _management_args(
                    request,
                    {"group_id": req.group_id, "trust_id": trust_id, "expected_revision": req.expected_revision},
                ),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    @public_router.post("/pairing/remote/requests")
    async def group_bridge_pairing_remote_request(
        request: Request,
    ) -> Dict[str, Any]:
        req = await _public_model(request, GroupBridgePairingRequestEnvelope)
        response = await ctx.daemon(
            {"op": "group_bridge_pairing_remote_request", "args": {"envelope": req.model_dump()}}
        )
        return _unwrap_daemon(response)["status"]

    @public_router.post("/pairing/remote/status")
    async def group_bridge_pairing_remote_status(
        request: Request,
    ) -> Dict[str, Any]:
        req = await _public_model(request, GroupBridgePairingRequestEnvelope)
        response = await ctx.daemon(
            {"op": "group_bridge_pairing_remote_status", "args": {"envelope": req.model_dump()}}
        )
        return _unwrap_daemon(response)["status"]

    @public_router.post("/session/receive")
    async def group_bridge_session_receive(
        request: Request,
    ) -> Dict[str, Any]:
        req = await _public_model(request, GroupBridgeSignedMessageEnvelope)
        # This endpoint is reachable by a remote peer, so authentication is
        # the signed envelope and the daemon's exact-target/trust checks.
        response = await ctx.daemon(
            {
                "op": "group_bridge_session_receive",
                "args": {
                    "group_id": req.target_group_id,
                    "envelope": req.model_dump(),
                },
            }
        )
        return _unwrap_daemon(response)["receipt"]

    return [public_router, management_router]


__all__ = [
    "GroupBridgeSessionSendRequest",
    "GroupBridgeRemoteSendRequest",
    "GroupBridgeRemoteStatusRequest",
    "GroupBridgeManagementGroupRequest",
    "GroupBridgePairingInviteRequest",
    "GroupBridgePairingRequestAction",
    "GroupBridgePairingRejectRequest",
    "GroupBridgeTrustAccessRequest",
    "GroupBridgeTrustRevokeRequest",
    "create_routers",
]
