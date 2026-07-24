from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ....computer_control.models import (
    ElementLocator,
    TrustRequest,
    WorkflowCreateRequest,
    WorkflowRunRequest,
    WorkflowUpdateRequest,
)
from ....computer_control.services import ComputerControlServices, get_services
from ....computer_control.storage import RevisionConflict, WorkflowNotFound
from ....computer_control.models import WorkflowDefinition
from ....computer_control.audit import audit
from ....computer_control.risk import annotate_catalog
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

    @global_router.post("/setup/ensure")
    async def setup_ensure(force: bool = False) -> Dict[str, Any]:
        result = await service.setup.ensure(force=force)
        audit(ctx.home, "computer_control.setup", details={"phase": result.get("phase"), "version": result.get("version")})
        _emit("setup", phase=result.get("phase"), version=result.get("version"))
        return {"ok": True, "result": result}

    @global_router.get("/setup/status")
    async def setup_status() -> Dict[str, Any]:
        return {"ok": True, "result": service.setup.status()}

    @global_router.post("/setup/repair")
    async def setup_repair() -> Dict[str, Any]:
        result = await service.setup.repair()
        audit(ctx.home, "computer_control.setup_repair", details={"phase": result.get("phase")})
        _emit("setup_repair", phase=result.get("phase"))
        return {"ok": True, "result": result}

    @global_router.post("/setup/upgrade")
    async def setup_upgrade() -> Dict[str, Any]:
        result = await service.setup.upgrade()
        audit(ctx.home, "computer_control.setup_upgrade", details={"version": result.get("version"), "phase": result.get("phase")})
        return {"ok": True, "result": result}

    @global_router.get("/catalog")
    async def catalog() -> Dict[str, Any]:
        try:
            tools = annotate_catalog(await service.session.catalog())
        except Exception as exc:
            raise _error("windows_mcp_unavailable", str(exc), 503) from exc
        status = service.setup.status()
        return {
            "ok": True,
            "result": {
                "version": status.get("version"),
                "fingerprint": status.get("fingerprint"),
                "tools": tools,
                "healthy": service.session.running,
            },
        }

    @global_router.get("/lease/status")
    async def lease_status() -> Dict[str, Any]:
        return {"ok": True, "result": service.lease.status()}

    @global_router.post("/lease/force-release", dependencies=[Depends(require_admin)])
    async def force_release() -> Dict[str, Any]:
        released = service.lease.release(run_id="", force=True)
        await service.session.stop()
        audit(ctx.home, "computer_control.force_release", details={"released": released})
        _emit("lease.force_released", released=released)
        return {"ok": True, "result": {"released": released}}

    @group_router.get("/workflows")
    async def workflows(group_id: str, include_archived: bool = False) -> Dict[str, Any]:
        try:
            items = service.store.list(group_id, include_archived=include_archived)  # type: ignore[name-defined]
        except WorkflowNotFound as exc:
            raise _error("group_not_found", str(exc), 404) from exc
        return {"ok": True, "result": {"workflows": items}}

    @group_router.post("/workflows")
    async def create_workflow(group_id: str, request: WorkflowCreateRequest) -> Dict[str, Any]:
        try:
            value = service.store.create(group_id, request.definition)
        except WorkflowNotFound as exc:
            raise _error("group_not_found", str(exc), 404) from exc
        except ValueError as exc:
            raise _error("workflow_invalid", str(exc), 422) from exc
        return {"ok": True, "result": value}

    @group_router.get("/workflows/{workflow_id}")
    async def get_workflow(group_id: str, workflow_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        try:
            return {"ok": True, "result": service.store.get(group_id, workflow_id, version=version)}
        except WorkflowNotFound as exc:
            raise _error("workflow_not_found", str(exc), 404) from exc

    @group_router.put("/workflows/{workflow_id}")
    async def update_workflow(group_id: str, workflow_id: str, request: WorkflowUpdateRequest) -> Dict[str, Any]:
        try:
            value = service.store.update(group_id, workflow_id, request.definition, expected_revision=request.expected_revision, change_note=request.change_note)
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
            return {"ok": True, "result": service.store.rollback(group_id, workflow_id, version)}
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
            return {"ok": True, "result": service.store.duplicate(group_id, workflow_id)}
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
        version = int(request.version or manifest.get("published_version") or 0)
        trusted = manifest.get("trusted") if isinstance(manifest.get("trusted"), dict) else {}
        trust = trusted.get(str(version)) if isinstance(trusted, dict) else None
        fingerprint = str(service.setup.status().get("fingerprint") or "")
        if not isinstance(trust, dict) or str(trust.get("fingerprint") or "") != fingerprint:
            raise _error("workflow_not_trusted", "publish and trust this exact workflow version before running", 409)
        try:
            run = await service.runner.start(group_id, workflow_id, actor_id=request.actor_id, version=version, inputs=request.inputs)
        except Exception as exc:
            from ....computer_control.lease import LeaseConflict

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
            "allow_high_risk": payload.get("allow_high_risk") is not False,
            "allow_publish": payload.get("allow_publish") is True,
            "allow_trust": payload.get("allow_trust") is True,
            "allow_unattended_triggers": payload.get("allow_unattended_triggers") is True,
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
            result = await service.runner.cancel(group_id, run_id, emergency=emergency)
            audit(ctx.home, "computer_control.emergency_stop" if emergency else "computer_control.run_cancelled", group_id=group_id, details={"run_id": run_id})
            _emit("run.cancelled", group_id=group_id, run_id=run_id, emergency=emergency)
            return {"ok": True, "result": result}
        except KeyError as exc:
            raise _error("run_not_found", run_id, 404) from exc

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

    @group_router.post("/element-picker/capture")
    async def element_capture(group_id: str, request: Request) -> Dict[str, Any]:
        del request
        capture_id = "cap_" + uuid.uuid4().hex[:12]
        try:
            service.lease.acquire(group_id=group_id, actor_id="element-picker", run_id=capture_id, observe_only=True)
        except Exception as exc:
            from ....computer_control.lease import LeaseConflict

            if isinstance(exc, LeaseConflict):
                raise _error("computer_control_busy", "computer control is currently occupied", 409, exc.lease) from exc
            raise
        try:
            tools = await service.session.catalog()
            snapshot_name = next((str(item.get("name")) for item in tools if str(item.get("name") or "").lower() == "snapshot"), "")
            if not snapshot_name:
                raise RuntimeError("Windows-MCP Snapshot tool is unavailable")
            result = await service.session.call_tool(snapshot_name, {})
        except Exception as exc:
            service.lease.release(run_id=capture_id, force=True)
            raise _error("snapshot_failed", str(exc), 503) from exc
        service.lease.release(run_id=capture_id, force=True)
        capture = {"capture_id": capture_id, "created_at": time.time(), "expires_at": time.time() + 300, "result": result, "elements": _extract_elements(result)}
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
    async def validate_locator(locator: ElementLocator) -> Dict[str, Any]:
        return {"ok": True, "result": {"valid": True, "locator": locator.model_dump(mode="json"), "matches": []}}

    return [global_router, group_router]


def _extract_elements(result: Dict[str, Any]) -> list[Dict[str, Any]]:
    # MCP text is intentionally parsed server-side; clients only receive normalized items.
    items: list[Dict[str, Any]] = []
    for content in result.get("content", []) if isinstance(result, dict) else []:
        if not isinstance(content, dict) or content.get("type") != "text":
            continue
        try:
            payload = json.loads(str(content.get("text") or ""))
        except ValueError:
            continue
        candidates = payload.get("elements") if isinstance(payload, dict) else payload
        if isinstance(candidates, list):
            for item in candidates:
                if isinstance(item, dict):
                    items.append({
                        "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                        "window_name": str(item.get("window_name") or item.get("window") or ""),
                        "name": str(item.get("name") or item.get("text") or ""),
                        "control_type": str(item.get("control_type") or item.get("type") or ""),
                        "bounds": item.get("bounds") if isinstance(item.get("bounds"), dict) else None,
                    })
    return items


def _extract_image(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return ""
    for content in result.get("content", []):
        if isinstance(content, dict) and content.get("type") == "image" and isinstance(content.get("data"), str):
            return content["data"]
    return ""
