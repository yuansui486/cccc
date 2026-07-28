from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from ....daemon.server import DaemonPaths, call_daemon

from ....computer_control.models import (
    ElementLocator,
    TrustRequest,
    WorkflowCreateRequest,
    WorkflowRunRequest,
    WorkflowUpdateRequest,
    computer_control_permissions,
)
from ....computer_control.services import ComputerControlServices, get_services
from ....computer_control.storage import RevisionConflict, WorkflowNotFound
from ....computer_control.models import WorkflowDefinition
from ....computer_control.audit import audit
from ....computer_control.risk import annotate_catalog
from ....computer_control.mcp import validate_workflow_tools
from ....computer_control.compiler import compile_workflow, compile_or_raise
from ....computer_control.elements import normalize_snapshot, resolve_locator
from ..schemas import RouteContext, require_admin, require_group, require_user


def _error(code: str, message: str, status: int = 400, details: Any = None) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, "details": details or {}})


def _service(ctx: RouteContext) -> ComputerControlServices:
    return get_services(ctx.home)


def _emit(kind: str, **data: Any) -> None:
    try:
        from ....kernel.events import publish_event

        publish_event(f"computer_control.{kind}", data)
    except Exception:
        pass


def create_routers(ctx: RouteContext) -> list[APIRouter]:
    service = _service(ctx)
    global_router = APIRouter(prefix="/api/v1/computer-control", dependencies=[Depends(require_user)])
    group_router = APIRouter(prefix="/api/v1/groups/{group_id}/computer-control", dependencies=[Depends(require_group)])

    async def daemon_control(command: str, **payload: Any) -> Any:
        response = await asyncio.to_thread(
            call_daemon,
            {"op": "computer_control", "args": {"command": command, **payload}},
            paths=DaemonPaths(ctx.home),
            # Computer operations and recordings are user-cancellable rather
            # than wall-clock limited; the MCP call itself owns cancellation.
            timeout_s=None,
        )
        if not response.get("ok"):
            error = response.get("error") if isinstance(response.get("error"), dict) else {}
            code = str(error.get("code") or "computer_control_failed")
            if code in {
                "computer_control_busy",
                "computer_control_lease_required",
                "computer_control_write_lease_required",
                "computer_control_setup_in_progress",
            }:
                status = 409
            elif code == "permission_denied":
                status = 403
            elif code in {"invalid_request", "invalid_argument_shape"}:
                status = 400
            else:
                status = 503
            raise _error(code, str(error.get("message") or "daemon request failed"), status, error.get("details"))
        envelope = response.get("result") if isinstance(response.get("result"), dict) else {}
        return envelope.get("result")

    def current_fingerprint() -> str:
        fingerprint = str(service.setup.status().get("fingerprint") or "")
        if fingerprint:
            return fingerprint
        try:
            setup_doc = json.loads((ctx.home / "state" / "computer-control" / "setup.json").read_text(encoding="utf-8"))
            return str(setup_doc.get("fingerprint") or "") if isinstance(setup_doc, dict) else ""
        except (OSError, ValueError):
            return ""

    def with_effective_version(manifest: Dict[str, Any]) -> Dict[str, Any]:
        return {**manifest, "effective_version": service.store.effective_version(manifest, current_fingerprint())}

    def enrich_lease(value: Any) -> Dict[str, Any]:
        raw = value if isinstance(value, dict) else {}
        lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else raw
        if raw.get("active") is False:
            lease = {}
        if not lease:
            return {"active": False}
        result: Dict[str, Any] = {"active": True, "lease": lease}
        group_id = str(lease.get("group_id") or "")
        run_id = str(lease.get("run_id") or "")
        if group_id and run_id:
            try:
                run = service.runner.get(group_id, run_id)
            except (KeyError, ValueError):
                run = None
            if isinstance(run, dict):
                result["run"] = {
                    "run_id": run.get("run_id"),
                    "group_id": run.get("group_id"),
                    "workflow_id": run.get("workflow_id"),
                    "actor_id": run.get("actor_id"),
                    "status": run.get("status"),
                    "current_node_id": run.get("current_node_id"),
                    "started_at": run.get("started_at"),
                    "updated_at": run.get("updated_at"),
                }
                try:
                    workflow = service.store.get(group_id, str(run.get("workflow_id") or ""))
                    result["workflow"] = {
                        "workflow_id": run.get("workflow_id"),
                        "name": workflow.get("manifest", {}).get("name") or workflow.get("definition", {}).get("name"),
                        "version": run.get("version"),
                    }
                except (KeyError, ValueError):
                    pass
        return result

    async def validate_definition(definition: WorkflowDefinition) -> None:
        catalog_result = await daemon_control("catalog", group_id="_global", include_schema=True)
        compile_or_raise(definition, catalog_result.get("tools") if isinstance(catalog_result, dict) else [])

    async def validate_and_finalize(group_id: str, value: Dict[str, Any]) -> Dict[str, Any]:
        definition = WorkflowDefinition.model_validate({key: child for key, child in value["definition"].items() if key != "change_note"})
        await validate_definition(definition)
        finalized = service.store.auto_finalize(
            group_id,
            str(value["manifest"]["workflow_id"]),
            int(value["version"]),
            fingerprint=current_fingerprint(),
        )
        finalized["manifest"] = with_effective_version(finalized["manifest"])
        return finalized

    @global_router.post("/setup/ensure")
    async def setup_ensure(force: bool = False) -> Dict[str, Any]:
        result = await daemon_control("setup", group_id="_global", action="ensure", force=force)
        audit(ctx.home, "computer_control.setup", details={"phase": result.get("phase"), "version": result.get("version")})
        _emit("setup", phase=result.get("phase"), version=result.get("version"))
        return {"ok": True, "result": result}

    @global_router.get("/setup/status")
    async def setup_status() -> Dict[str, Any]:
        return {"ok": True, "result": await daemon_control("setup", group_id="_global", action="status")}

    @global_router.post("/setup/restart-session")
    async def setup_restart_session() -> Dict[str, Any]:
        try:
            result = await daemon_control("setup", group_id="_global", action="restart_session")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            audit(
                ctx.home,
                "computer_control.setup_session_restart",
                details={
                    "success": False,
                    "code": detail.get("code"),
                    "status_code": exc.status_code,
                },
            )
            raise
        except Exception as exc:
            audit(
                ctx.home,
                "computer_control.setup_session_restart",
                details={
                    "success": False,
                    "code": "daemon_unavailable",
                    "error_type": type(exc).__name__,
                },
            )
            raise
        audit(
            ctx.home,
            "computer_control.setup_session_restart",
            details={
                "success": True,
                "started_at": result.get("started_at"),
                "transport_restarts": result.get("transport_restarts"),
                "session_running": result.get("session_running"),
                "tool_count": result.get("tool_count"),
            },
        )
        _emit(
            "setup_session_restarted",
            started_at=result.get("started_at"),
            transport_restarts=result.get("transport_restarts"),
            session_running=result.get("session_running"),
        )
        return {"ok": True, "result": result}

    @global_router.post("/setup/repair")
    async def setup_repair() -> Dict[str, Any]:
        result = await daemon_control("setup", group_id="_global", action="repair")
        audit(ctx.home, "computer_control.setup_repair", details={"phase": result.get("phase")})
        _emit("setup_repair", phase=result.get("phase"))
        return {"ok": True, "result": result}

    @global_router.post("/setup/upgrade")
    async def setup_upgrade() -> Dict[str, Any]:
        result = await daemon_control("setup", group_id="_global", action="upgrade")
        audit(ctx.home, "computer_control.setup_upgrade", details={"version": result.get("version"), "phase": result.get("phase")})
        return {"ok": True, "result": result}

    @global_router.get("/catalog")
    async def catalog(tool: Optional[str] = Query(None)) -> Dict[str, Any]:
        try:
            result = await daemon_control("catalog", group_id="_global", tool=str(tool or "").strip())
        except Exception as exc:
            raise _error("windows_mcp_unavailable", str(exc), 503) from exc
        status = result.get("setup") if isinstance(result, dict) and isinstance(result.get("setup"), dict) else {}
        payload = {"version": status.get("version"), "fingerprint": status.get("fingerprint"), "tools": result.get("tools", []), "healthy": status.get("session_running", False)}
        if isinstance(result.get("tool"), dict):
            payload["tool"] = result["tool"]
        return {"ok": True, "result": payload}

    @global_router.get("/lease/status")
    async def lease_status() -> Dict[str, Any]:
        result = await daemon_control("lease", group_id="_global", action="status")
        return {"ok": True, "result": enrich_lease(result)}

    @global_router.post("/lease/interrupt", dependencies=[Depends(require_admin)])
    async def interrupt_lease(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        lease_status = await daemon_control("lease", group_id="_global", action="status")
        lease = lease_status.get("lease") if isinstance(lease_status, dict) and isinstance(lease_status.get("lease"), dict) else lease_status
        if isinstance(lease_status, dict) and lease_status.get("active") is False:
            lease = None
        if not isinstance(lease, dict) or not lease.get("run_id"):
            return {"ok": True, "result": {"interrupted": False, "active": False}}
        requested_run_id = str(payload.get("run_id") or "").strip()
        run_id = str(lease.get("run_id") or "")
        if requested_run_id and requested_run_id != run_id:
            raise _error("lease_mismatch", "当前电脑占用已发生变化，请刷新后重试", 409, enrich_lease(lease))
        emergency = bool(payload.get("emergency"))
        group_id = str(lease.get("group_id") or "")
        actor_id = str(lease.get("actor_id") or "")
        try:
            if run_id.startswith("rec_"):
                result = await daemon_control(
                    "recording",
                    group_id=group_id,
                    actor_id=actor_id,
                    action="abort",
                    recording_id=run_id,
                    reason="user_interrupt",
                )
                if emergency:
                    await daemon_control("setup", group_id="_global", action="force_release")
            else:
                result = await daemon_control(
                    "run",
                    group_id=group_id,
                    actor_id=actor_id,
                    action="cancel",
                    run_id=run_id,
                    emergency=emergency,
                )
        except HTTPException:
            raise
        audit(ctx.home, "computer_control.lease_interrupted", group_id=group_id, actor_id=actor_id, details={"run_id": run_id, "emergency": emergency})
        _emit("lease.interrupted", group_id=group_id, run_id=run_id, emergency=emergency)
        return {"ok": True, "result": {"interrupted": True, "emergency": emergency, "run": result}}

    @global_router.post("/lease/force-release", dependencies=[Depends(require_admin)])
    async def force_release() -> Dict[str, Any]:
        result = await daemon_control("setup", group_id="_global", action="force_release")
        released = bool(result.get("released"))
        audit(ctx.home, "computer_control.force_release", details={"released": released})
        _emit("lease.force_released", released=released)
        return {"ok": True, "result": {"released": released}}

    @group_router.get("/workflows")
    async def workflows(group_id: str, include_archived: bool = False) -> Dict[str, Any]:
        try:
            items = service.store.list(group_id, include_archived=include_archived)  # type: ignore[name-defined]
        except WorkflowNotFound as exc:
            raise _error("group_not_found", str(exc), 404) from exc
        return {"ok": True, "result": {"workflows": [with_effective_version(item) for item in items]}}

    @group_router.get("/settings")
    async def computer_control_settings(group_id: str) -> Dict[str, Any]:
        return {"ok": True, "result": service.store.settings(group_id, current_fingerprint=current_fingerprint())}

    @group_router.put("/settings")
    async def update_computer_control_settings(group_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            value = service.store.update_settings(
                group_id,
                auto_publish_and_trust=payload.get("auto_publish_and_trust") is not False,
                current_fingerprint=current_fingerprint(),
                authorize_current_fingerprint=bool(payload.get("authorize_current_fingerprint")),
            )
        except ValueError as exc:
            raise _error("settings_invalid", str(exc), 409) from exc
        audit(ctx.home, "computer_control.settings_updated", group_id=group_id, details={"auto_publish_and_trust": value.get("auto_publish_and_trust"), "fingerprint_authorized": bool(payload.get("authorize_current_fingerprint"))})
        return {"ok": True, "result": value}

    @group_router.post("/workflows")
    async def create_workflow(group_id: str, request: WorkflowCreateRequest) -> Dict[str, Any]:
        try:
            await validate_definition(request.definition)
            value = service.store.create(group_id, request.definition)
            value = await validate_and_finalize(group_id, value)
        except WorkflowNotFound as exc:
            raise _error("group_not_found", str(exc), 404) from exc
        except ValueError as exc:
            raise _error("workflow_invalid", str(exc), 422) from exc
        return {"ok": True, "result": value}

    @group_router.post("/workflows/compile")
    async def compile_workflow_route(group_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """Return field-level diagnostics without creating a workflow version."""
        del group_id
        catalog_result = await daemon_control("catalog", group_id="_global", include_schema=True)
        result = compile_workflow(payload.get("definition") if isinstance(payload.get("definition"), dict) else payload, catalog_result.get("tools") if isinstance(catalog_result, dict) else [])
        return {"ok": True, "result": result}

    @group_router.get("/workflows/{workflow_id}")
    async def get_workflow(group_id: str, workflow_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        try:
            value = service.store.get(group_id, workflow_id, version=version)
            value["manifest"] = with_effective_version(value["manifest"])
            return {"ok": True, "result": value}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.put("/workflows/{workflow_id}")
    async def update_workflow(group_id: str, workflow_id: str, request: WorkflowUpdateRequest) -> Dict[str, Any]:
        try:
            await validate_definition(request.definition)
            value = service.store.update(group_id, workflow_id, request.definition, expected_revision=request.expected_revision, change_note=request.change_note)
            value = await validate_and_finalize(group_id, value)
        except RevisionConflict as exc:
            raise _error("revision_conflict", str(exc), 409, {"current_revision": exc.current_revision}) from exc
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc
        except ValueError as exc:
            raise _error("workflow_invalid", str(exc), 422) from exc
        return {"ok": True, "result": value}

    @group_router.get("/workflows/{workflow_id}/versions")
    async def workflow_versions(group_id: str, workflow_id: str) -> Dict[str, Any]:
        try:
            return {"ok": True, "result": {"versions": service.store.versions(group_id, workflow_id)}}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.post("/workflows/{workflow_id}/publish")
    async def publish_workflow(group_id: str, workflow_id: str, version: int = Body(..., embed=True)) -> Dict[str, Any]:
        try:
            return {"ok": True, "result": service.store.publish(group_id, workflow_id, version)}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.post("/workflows/{workflow_id}/versions/{version}/rollback")
    async def rollback_workflow(group_id: str, workflow_id: str, version: int) -> Dict[str, Any]:
        try:
            value = service.store.rollback(group_id, workflow_id, version)
            return {"ok": True, "result": await validate_and_finalize(group_id, value)}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.get("/workflows/{workflow_id}/triggers")
    async def workflow_triggers(group_id: str, workflow_id: str) -> Dict[str, Any]:
        try:
            value = service.store.get(group_id, workflow_id)
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc
        definition = WorkflowDefinition.model_validate({k: v for k, v in value["definition"].items() if k != "change_note"})
        return {"ok": True, "result": {"revision": value["manifest"].get("revision"), "triggers": [item.model_dump(mode="json") for item in definition.triggers]}}

    @group_router.put("/workflows/{workflow_id}/triggers")
    async def update_workflow_triggers(group_id: str, workflow_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        expected = int(payload.get("expected_revision") or 0)
        try:
            current = service.store.get(group_id, workflow_id)
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc
        definition_payload = dict(current["definition"])
        definition_payload["triggers"] = payload.get("triggers") if isinstance(payload.get("triggers"), list) else []
        try:
            definition = WorkflowDefinition.model_validate({k: v for k, v in definition_payload.items() if k != "change_note"})
            value = service.store.update(group_id, workflow_id, definition, expected_revision=expected, change_note="trigger update")
            value = await validate_and_finalize(group_id, value)
        except RevisionConflict as exc:
            raise _error("revision_conflict", str(exc), 409, {"current_revision": exc.current_revision}) from exc
        except (WorkflowNotFound, ValueError) as exc:
            raise _error("workflow_invalid", str(exc), 422) from exc
        return {"ok": True, "result": value}

    @group_router.get("/workflows/{workflow_id}/optimization-proposals")
    async def optimization_proposals(group_id: str, workflow_id: str) -> Dict[str, Any]:
        try:
            service.store.get(group_id, workflow_id)
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc
        return {"ok": True, "result": {"proposals": service.store.proposals(group_id, workflow_id)}}

    @group_router.post("/workflows/{workflow_id}/optimization-proposals/{proposal_id}/accept")
    async def accept_optimization_proposal(group_id: str, workflow_id: str, proposal_id: str) -> Dict[str, Any]:
        try:
            value = service.store.decide_proposal(group_id, workflow_id, proposal_id, accept=True)
            if value.get("accepted_version"):
                accepted = service.store.get(group_id, workflow_id, version=int(value["accepted_version"]))
                await validate_and_finalize(group_id, accepted)
        except WorkflowNotFound as exc:
            raise _error("proposal_not_found", str(exc), 404) from exc
        except (RevisionConflict, ValueError) as exc:
            raise _error("proposal_conflict", str(exc), 409) from exc
        audit(ctx.home, "computer_control.optimization_accepted", group_id=group_id, details={"workflow_id": workflow_id, "proposal_id": proposal_id})
        _emit("optimization.accepted", group_id=group_id, workflow_id=workflow_id, proposal_id=proposal_id)
        return {"ok": True, "result": value}

    @group_router.post("/workflows/{workflow_id}/optimization-proposals/{proposal_id}/reject")
    async def reject_optimization_proposal(group_id: str, workflow_id: str, proposal_id: str) -> Dict[str, Any]:
        try:
            value = service.store.decide_proposal(group_id, workflow_id, proposal_id, accept=False)
        except WorkflowNotFound as exc:
            raise _error("proposal_not_found", str(exc), 404) from exc
        except ValueError as exc:
            raise _error("proposal_conflict", str(exc), 409) from exc
        audit(ctx.home, "computer_control.optimization_rejected", group_id=group_id, details={"workflow_id": workflow_id, "proposal_id": proposal_id})
        return {"ok": True, "result": value}

    @group_router.post("/workflows/{workflow_id}/trust")
    async def trust_workflow(group_id: str, workflow_id: str, request: TrustRequest) -> Dict[str, Any]:
        fingerprint = str(service.setup.status().get("fingerprint") or "")
        if not fingerprint:
            raise _error("windows_mcp_not_ready", "install and verify Windows-MCP before trusting a workflow", 409)
        try:
            return {"ok": True, "result": service.store.trust(group_id, workflow_id, request.version, fingerprint=fingerprint, permissions=request.permissions)}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.post("/workflows/{workflow_id}/archive")
    async def archive_workflow(group_id: str, workflow_id: str, archived: bool = Body(True, embed=True)) -> Dict[str, Any]:
        try:
            return {"ok": True, "result": service.store.archive(group_id, workflow_id, archived)}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.post("/workflows/{workflow_id}/duplicate")
    async def duplicate_workflow(group_id: str, workflow_id: str) -> Dict[str, Any]:
        try:
            value = service.store.duplicate(group_id, workflow_id)
            return {"ok": True, "result": await validate_and_finalize(group_id, value)}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.delete("/workflows/{workflow_id}")
    async def delete_workflow(group_id: str, workflow_id: str) -> Dict[str, Any]:
        try:
            service.store.delete(group_id, workflow_id)
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc
        return {"ok": True, "result": {"deleted": True}}

    @group_router.post("/workflows/{workflow_id}/runs")
    async def run_workflow(group_id: str, workflow_id: str, request: WorkflowRunRequest) -> Dict[str, Any]:
        try:
            current = service.store.get(group_id, workflow_id, version=request.version)
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc
        manifest = current["manifest"]
        effective_version = service.store.effective_version(manifest, current_fingerprint())
        version = int(request.version or effective_version or 0)
        trusted = manifest.get("trusted") if isinstance(manifest.get("trusted"), dict) else {}
        trust = trusted.get(str(version)) if isinstance(trusted, dict) else None
        fingerprint = current_fingerprint()
        if not isinstance(trust, dict) or str(trust.get("fingerprint") or "") != fingerprint:
            raise _error("workflow_not_trusted", "publish and trust this exact workflow version before running", 409)
        try:
            run = await daemon_control(
                "run",
                group_id=group_id,
                actor_id=request.actor_id,
                action="start",
                workflow_id=workflow_id,
                version=version,
                inputs=request.inputs,
            )
        except Exception as exc:
            from ....computer_control.lease import LeaseConflict

            if isinstance(exc, HTTPException):
                raise
            if isinstance(exc, LeaseConflict):
                raise _error("computer_control_busy", "computer control is currently occupied", 409, exc.lease) from exc
            raise _error("run_failed", str(exc), 409) from exc
        audit(ctx.home, "computer_control.run_started", group_id=group_id, actor_id=request.actor_id, details={"workflow_id": workflow_id, "version": version, "run_id": run.get("run_id")})
        _emit("run.started", group_id=group_id, run_id=run.get("run_id"), workflow_id=workflow_id)
        return {"ok": True, "result": run}

    @group_router.get("/runs")
    async def runs(group_id: str, limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
        return {"ok": True, "result": {"runs": service.runner.list(group_id, limit)}}

    @group_router.post("/requests")
    async def create_computer_request(group_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """Structured chat/control-plane handoff; execution remains trust-gated."""
        text = str(payload.get("text") or "").strip()
        if not text:
            raise _error("invalid_request", "text is required", 422)
        try:
            WorkflowDefinition._reject_plain_secrets(payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {})
        except ValueError as exc:
            raise _error("sensitive_value_rejected", str(exc), 422) from exc
        request_id = "ccreq_" + uuid.uuid4().hex[:14]
        request = {
            "request_id": request_id,
            "group_id": group_id,
            "text": text,
            "workflow_id": str(payload.get("workflow_id") or "").strip(),
            "mode": str(payload.get("mode") or ("run_existing" if payload.get("workflow_id") else "create_and_run")),
            "actor_id": str(payload.get("actor_id") or "foreman").strip() or "foreman",
            "inputs": payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {},
            **computer_control_permissions(payload),
            "created_at": time.time(),
            "created_ts": time.time(),
            "status": "accepted",
        }
        service.requests.append(group_id, request)
        audit(ctx.home, "computer_control.request", group_id=group_id, actor_id=request["actor_id"], details={"request_id": request_id, "workflow_id": request["workflow_id"]})
        _emit("request.accepted", group_id=group_id, request_id=request_id)
        return {"ok": True, "result": {"request": request, "computer_control_request": True}}

    @group_router.get("/requests")
    async def list_computer_requests(group_id: str, limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
        path = service.store.state_root(group_id) / "requests.jsonl"
        if not path.exists():
            return {"ok": True, "result": {"requests": []}}
        by_id: Dict[str, Dict[str, Any]] = {}
        order: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                request_id = str(value.get("request_id") or "")
                if request_id:
                    if request_id not in by_id:
                        order.append(request_id)
                    by_id[request_id] = {**by_id.get(request_id, {}), **value}
        rows = [by_id[request_id] for request_id in order[-limit:]]
        return {"ok": True, "result": {"requests": list(reversed(rows))}}

    @group_router.post("/requests/{request_id}/approve")
    async def approve_computer_request(group_id: str, request_id: str) -> Dict[str, Any]:
        try:
            value = service.requests.update(group_id, request_id, status="approved", approved_at=time.time(), approved_by="user")
        except KeyError as exc:
            raise _error("request_not_found", request_id, 404) from exc
        audit(ctx.home, "computer_control.request_approved", group_id=group_id, details={"request_id": request_id})
        _emit("request.approved", group_id=group_id, request_id=request_id)
        return {"ok": True, "result": {"request": value}}

    @group_router.post("/requests/{request_id}/reject")
    async def reject_computer_request(group_id: str, request_id: str) -> Dict[str, Any]:
        try:
            value = service.requests.update(group_id, request_id, status="rejected", rejected_at=time.time(), rejected_by="user")
        except KeyError as exc:
            raise _error("request_not_found", request_id, 404) from exc
        audit(ctx.home, "computer_control.request_rejected", group_id=group_id, details={"request_id": request_id})
        _emit("request.rejected", group_id=group_id, request_id=request_id)
        return {"ok": True, "result": {"request": value}}

    @group_router.get("/runs/{run_id}")
    async def get_run(group_id: str, run_id: str) -> Dict[str, Any]:
        try:
            return {"ok": True, "result": service.runner.get(group_id, run_id)}
        except KeyError as exc:
            raise _error("run_not_found", run_id, 404) from exc

    @group_router.post("/runs/{run_id}/cancel")
    async def cancel_run(group_id: str, run_id: str, emergency: bool = Body(False, embed=True)) -> Dict[str, Any]:
        try:
            run = service.runner.get(group_id, run_id)
            result = await daemon_control("run", group_id=group_id, actor_id=str(run.get("actor_id") or "foreman"), action="cancel", run_id=run_id, emergency=emergency)
            audit(ctx.home, "computer_control.emergency_stop" if emergency else "computer_control.run_cancelled", group_id=group_id, details={"run_id": run_id})
            _emit("run.cancelled", group_id=group_id, run_id=run_id, emergency=emergency)
            return {"ok": True, "result": result}
        except KeyError as exc:
            raise _error("run_not_found", run_id, 404) from exc

    @group_router.post("/runs/{run_id}/verify")
    async def verify_run(group_id: str, run_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            run = service.runner.get(group_id, run_id)
            result = service.runner.verify(
                group_id,
                run_id,
                actor_id=str(run.get("actor_id") or "foreman"),
                passed=bool(payload.get("passed")),
                summary=str(payload.get("summary") or ""),
                evidence_ids=payload.get("evidence_ids") if isinstance(payload.get("evidence_ids"), list) else [],
                fingerprint=str(service.setup.status().get("fingerprint") or ""),
            )
            audit(ctx.home, "computer_control.run_verified", group_id=group_id, details={"run_id": run_id, "passed": bool(payload.get("passed"))})
            return {"ok": True, "result": result}
        except KeyError as exc:
            raise _error("run_not_found", run_id, 404) from exc
        except (ValueError, PermissionError) as exc:
            raise _error("verification_failed", str(exc), 409) from exc

    @group_router.get("/runs/{run_id}/recovery")
    async def recovery_context(group_id: str, run_id: str) -> Dict[str, Any]:
        try:
            return {"ok": True, "result": service.runner.recovery_context(group_id, run_id)}
        except KeyError as exc:
            raise _error("run_not_found", run_id, 404) from exc

    @group_router.post("/runs/{run_id}/recovery/resolve")
    async def resolve_recovery(group_id: str, run_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        try:
            run = service.runner.get(group_id, run_id)
            actor_id = str(payload.get("actor_id") or run.get("actor_id") or "foreman")
            result = service.runner.submit_recovery(
                group_id,
                run_id,
                str(payload.get("recovery_id") or ""),
                actor_id=actor_id,
                tool=str(payload.get("tool") or ""),
                arguments=payload.get("arguments") if isinstance(payload.get("arguments"), dict) else None,
                resolution=str(payload.get("resolution") or "retry"),
                node_id=str(payload.get("node_id") or ""),
                target=payload.get("target") if isinstance(payload.get("target"), dict) else None,
                idempotency_key=str(payload.get("idempotency_key") or ""),
            )
            audit(ctx.home, "computer_control.recovery_resolved", group_id=group_id, actor_id=actor_id, details={"run_id": run_id, "resolution": payload.get("resolution")})
            return {"ok": True, "result": result}
        except KeyError as exc:
            raise _error("run_not_found", run_id, 404) from exc
        except (ValueError, PermissionError) as exc:
            raise _error("recovery_invalid", str(exc), 409) from exc

    @group_router.post("/runs/{run_id}/approvals/{node_id}")
    async def decide_run_approval(group_id: str, run_id: str, node_id: str, approved: bool = Body(..., embed=True)) -> Dict[str, Any]:
        try:
            result = service.runner.decide_approval(group_id, run_id, node_id, approved=approved)
        except KeyError as exc:
            raise _error("run_not_found", run_id, 404) from exc
        except ValueError as exc:
            raise _error("approval_not_pending", str(exc), 409) from exc
        audit(ctx.home, "computer_control.run_approval", group_id=group_id, details={"run_id": run_id, "node_id": node_id, "approved": approved})
        _emit("run.approval", group_id=group_id, run_id=run_id, node_id=node_id, approved=approved)
        return {"ok": True, "result": result}

    # Native picker sessions are daemon-owned.  Keeping these routes ahead of
    # the legacy ``/{capture_id}`` routes prevents "sessions" from being
    # interpreted as a temporary snapshot id.
    @group_router.post("/element-picker/sessions")
    async def picker_start(group_id: str, payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        actor_id = str(payload.get("actor_id") or "user").strip() or "user"
        try:
            result = await daemon_control(
                "picker",
                group_id=group_id,
                actor_id=actor_id,
                action="start",
                session_id=str(payload.get("session_id") or ""),
                hotkey=str(payload.get("hotkey") or "Ctrl+Shift+LeftClick"),
            )
        except HTTPException:
            raise
        audit(ctx.home, "computer_control.picker_started", group_id=group_id, actor_id=actor_id, details={"session_id": result.get("session_id")})
        _emit("picker.started", group_id=group_id, actor_id=actor_id, session_id=result.get("session_id"))
        return {"ok": True, "result": result}

    @group_router.get("/element-picker/sessions/{session_id}")
    async def picker_status(group_id: str, session_id: str) -> Dict[str, Any]:
        result = await daemon_control("picker", group_id=group_id, actor_id="user", action="status", session_id=session_id)
        return {"ok": True, "result": result}

    @group_router.get("/element-picker/sessions/{session_id}/events")
    async def picker_events(group_id: str, session_id: str, request: Request, after_seq: int = Query(0, ge=0)) -> StreamingResponse:
        async def stream() -> Any:
            cursor = int(after_seq or 0)
            try:
                while True:
                    if await request.is_disconnected():
                        try:
                            await daemon_control("picker", group_id=group_id, actor_id="user", action="cancel", session_id=session_id, reason="browser_disconnected")
                        except Exception:
                            pass
                        break
                    try:
                        value = await daemon_control("picker", group_id=group_id, actor_id="user", action="events", session_id=session_id, after_seq=cursor)
                    except HTTPException as exc:
                        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                        yield f"event: error\ndata: {json.dumps(detail, ensure_ascii=False)}\n\n"
                        break
                    events = value.get("events") if isinstance(value, dict) else []
                    if isinstance(events, list):
                        for event in events:
                            if not isinstance(event, dict):
                                continue
                            cursor = max(cursor, int(event.get("seq") or 0))
                            yield f"id: {cursor}\nevent: {event.get('type') or 'picker'}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    session = value.get("session") if isinstance(value, dict) else {}
                    if isinstance(session, dict) and str(session.get("status") or "") not in {"active", "locked"}:
                        break
                    # The stream has no operation deadline.  The browser may
                    # disconnect or call DELETE to end it explicitly.
                    await asyncio.sleep(0.25)
            finally:
                # Do not release confirmed sessions; cancellation is handled by
                # the explicit endpoint or the disconnect path above.
                pass

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @group_router.post("/element-picker/sessions/{session_id}/lock")
    async def picker_lock(group_id: str, session_id: str, payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        result = await daemon_control(
            "picker",
            group_id=group_id,
            actor_id=str(payload.get("actor_id") or "user"),
            action="lock",
            session_id=session_id,
            point=payload.get("point"),
            element=payload.get("element") if isinstance(payload.get("element"), dict) else None,
        )
        _emit("picker.locked", group_id=group_id, session_id=session_id, stable=result.get("stable"))
        return {"ok": True, "result": result}

    @group_router.post("/element-picker/sessions/{session_id}/confirm")
    async def picker_confirm(group_id: str, session_id: str, payload: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
        result = await daemon_control(
            "picker",
            group_id=group_id,
            actor_id=str(payload.get("actor_id") or "user"),
            action="confirm",
            session_id=session_id,
            locator=payload.get("locator") if isinstance(payload.get("locator"), dict) else None,
        )
        audit(ctx.home, "computer_control.picker_confirmed", group_id=group_id, details={"session_id": session_id})
        _emit("picker.confirmed", group_id=group_id, session_id=session_id)
        return {"ok": True, "result": result}

    @group_router.delete("/element-picker/sessions/{session_id}")
    async def picker_cancel(group_id: str, session_id: str, reason: str = Query("cancelled")) -> Dict[str, Any]:
        result = await daemon_control("picker", group_id=group_id, actor_id="user", action="cancel", session_id=session_id, reason=reason)
        audit(ctx.home, "computer_control.picker_cancelled", group_id=group_id, details={"session_id": session_id, "reason": reason})
        _emit("picker.ended", group_id=group_id, session_id=session_id, reason=reason)
        return {"ok": True, "result": result}

    @group_router.post("/element-picker/capture")
    async def element_capture(group_id: str, request: Request) -> Dict[str, Any]:
        del request
        capture_id = "cap_" + uuid.uuid4().hex[:12]
        result = await daemon_control("element_snapshot", group_id=group_id, capture_id=capture_id)
        normalized = normalize_snapshot(result)
        capture = {"capture_id": capture_id, "created_at": time.time(), "expires_at": time.time() + 300, "result": result, "elements": normalized.get("elements", []), "warnings": normalized.get("warnings", [])}
        service.store.state_root(group_id).mkdir(parents=True, exist_ok=True)
        path = service.store.state_root(group_id) / "element-captures" / f"{capture_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        image_data = _extract_image(result)
        if image_data:
            try:
                (path.parent / f"{capture_id}.png").write_bytes(base64.b64decode(image_data))
            except Exception:
                pass
        capture.pop("result", None)
        capture["image_url"] = f"/api/v1/groups/{group_id}/computer-control/element-picker/{capture_id}/image" if image_data else None
        path.write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "result": {k: v for k, v in capture.items() if k != "result"}}

    @group_router.get("/element-picker/{capture_id}")
    async def get_capture(group_id: str, capture_id: str) -> Dict[str, Any]:
        path = service.store.state_root(group_id) / "element-captures" / f"{capture_id}.json"
        if not path.exists():
            raise _error("capture_not_found", capture_id, 404)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise _error("capture_not_found", capture_id, 404) from exc
        if float(value.get("expires_at") or 0) < time.time():
            path.unlink(missing_ok=True)
            raise _error("capture_expired", capture_id, 410)
        return {"ok": True, "result": value}

    @group_router.get("/element-picker/{capture_id}/image")
    async def capture_image(group_id: str, capture_id: str) -> FileResponse:
        image_path = service.store.state_root(group_id) / "element-captures" / f"{capture_id}.png"
        if not image_path.exists():
            raise _error("capture_image_not_found", capture_id, 404)
        try:
            expires_at = float(json.loads((image_path.parent / f"{capture_id}.json").read_text(encoding="utf-8")).get("expires_at") or 0)
        except (OSError, ValueError):
            expires_at = 0
        if expires_at < time.time():
            image_path.unlink(missing_ok=True)
            raise _error("capture_expired", capture_id, 410)
        return FileResponse(str(image_path), media_type="image/png")

    @group_router.post("/element-picker/validate")
    async def validate_locator(group_id: str, locator: ElementLocator) -> Dict[str, Any]:
        capture_id = "cap_validate_" + uuid.uuid4().hex[:12]
        snapshot_result = await daemon_control(
            "element_snapshot",
            group_id=group_id,
            capture_id=capture_id,
            locator=locator.model_dump(mode="json"),
        )
        normalized = normalize_snapshot(snapshot_result)
        resolved = resolve_locator(normalized, locator.model_dump(mode="json"))
        return {
            "ok": True,
            "result": {
                "valid": resolved.get("status") == "unique",
                "status": resolved.get("status"),
                "confidence": resolved.get("confidence"),
                "match_count": resolved.get("match_count", 0),
                "locator": locator.model_dump(mode="json"),
                "matches": resolved.get("matches", []),
                "warnings": normalized.get("warnings", []),
            },
        }

    @group_router.post("/element-picker/resolve")
    async def resolve_element_locator(group_id: str, locator: ElementLocator) -> Dict[str, Any]:
        # Alias with an explicit, UI-friendly status used by the editor's
        # "测试定位" action.
        return await validate_locator(group_id, locator)

    return [global_router, group_router]


def _extract_elements(result: Dict[str, Any]) -> list[Dict[str, Any]]:
    return normalize_snapshot(result).get("elements", [])


def _extract_image(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    for content in result.get("content", []):
        if isinstance(content, dict) and content.get("type") == "image" and isinstance(content.get("data"), str):
            return content["data"]
    return ""
