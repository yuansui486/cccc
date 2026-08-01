"""Web adapters for the signed Group Bridge session operations.

The Web layer owns authentication and request shaping only. Session
validation, authorization, signing, and local user-only delivery remain in
the daemon operation boundary.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ....contracts.v1.group_bridge import GroupBridgeSessionMessage
from ..schemas import RouteContext, check_group, require_user


class GroupBridgeSessionSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1, max_length=256)
    local_endpoint: str = Field(min_length=1, max_length=2048)
    remote_group_id: str = Field(min_length=1, max_length=256)
    remote_peer_id: str = Field(min_length=1, max_length=256)
    remote_endpoint: str = Field(default="", max_length=2048)
    client_nonce: str = Field(min_length=43, max_length=43)
    payload: Dict[str, Any]


class GroupBridgeSessionReceiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1, max_length=256)
    local_endpoint: str = Field(min_length=1, max_length=2048)
    envelope: Dict[str, Any]


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

    @public_router.post("/session/receive")
    async def group_bridge_session_receive(
        req: GroupBridgeSessionReceiveRequest,
    ) -> Dict[str, Any]:
        # This endpoint is reachable by a remote peer, so authentication is
        # the signed envelope and the daemon's exact-target/trust checks.
        response = await ctx.daemon(
            {
                "op": "group_bridge_session_receive",
                "args": req.model_dump(),
            }
        )
        return {"ok": True, "result": _unwrap_daemon(response)}

    return [public_router, management_router]


__all__ = [
    "GroupBridgeSessionReceiveRequest",
    "GroupBridgeSessionSendRequest",
    "GroupBridgeRemoteSendRequest",
    "GroupBridgeRemoteStatusRequest",
    "create_routers",
]
