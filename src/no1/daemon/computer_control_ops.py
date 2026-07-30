"""Daemon-owned computer-control command handling."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional, Tuple

from ..computer_control.mcp import MCPUnavailable
from ..computer_control.risk import annotate_catalog, workflow_risk
from ..computer_control.mcp import validate_workflow_tools
from ..computer_control.lease import LeaseConflict
from ..computer_control.elements import normalize_snapshot
from ..computer_control.models import WorkflowDefinition
from ..computer_control.authorization import (
    RecordingStartClaim,
    request_id_requiring_turn_binding,
    requires_live_turn_claim,
)
from ..computer_control.derived_authority import (
    DerivedAuthorityClaim,
    DerivedAuthorityStore,
    RecordingStopOwnerClaim,
)
from ..computer_control.requests import ComputerRequestStore
from ..computer_control.services import get_services
from ..computer_control.storage import WorkflowStore
from ..contracts.v1 import DaemonError, DaemonResponse
from ..kernel.actors import find_actor
from ..kernel.group import load_group
from ..kernel.ledger_index import lookup_event_by_id
from ..paths import ensure_home
from .messaging.turn_provenance import (
    consume_turn_grant_receipt,
    get_actor_turn_generation,
    get_daemon_turn_issuer_epoch,
    load_event_turn_provenance,
    validate_turn_grant_receipt,
)


def _error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> Tuple[DaemonResponse, bool]:
    return DaemonResponse(ok=False, error=DaemonError(code=code, message=message, details=details or {})), False


def _ok(value: Any) -> Tuple[DaemonResponse, bool]:
    return DaemonResponse(ok=True, result={"ok": True, "result": value}), False


def _observation_status(service: Any) -> Dict[str, Any]:
    observation = getattr(service, "observation", None)
    status = getattr(observation, "status", None)
    if not callable(status):
        return {}
    value = status()
    return value if isinstance(value, dict) else {}


def _infrastructure_details(service: Any) -> Dict[str, Any]:
    setup = service.setup.status()
    return {
        "phase": setup.get("phase"),
        "version": setup.get("version"),
        "session_running": bool(setup.get("session_running")),
        "transport_restarts": int(getattr(service.session, "transport_restarts", 0)),
        "stderr_tail": list(setup.get("logs") or [])[-10:],
        "lease": service.lease.status(),
    }


def _recording(
    service: Any,
    args: Dict[str, Any],
    group_id: str,
    actor_id: str,
    *,
    start_claim: Optional[RecordingStartClaim],
    authority: Optional[DerivedAuthorityClaim],
    stop_claim: Optional[RecordingStopOwnerClaim],
) -> Any:
    action = str(args.get("action") or "").strip().lower()
    recording_id = str(args.get("recording_id") or "").strip()
    if action == "start":
        return service.recordings.start(
            group_id,
            actor_id=actor_id,
            request_id=str(args.get("request_id") or "").strip(),
            start_claim=start_claim,
            name=str(args.get("name") or "电脑控制工作流"),
            description=str(args.get("description") or ""),
            inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else {},
            triggers=args.get("triggers") if isinstance(args.get("triggers"), list) else [],
        )
    if action == "get":
        return service.recordings.get(
            group_id,
            recording_id,
            actor_id=actor_id,
            authority=authority,
            compact=args.get("full") is not True,
            evidence_offset=int(args.get("evidence_offset") or 0),
            evidence_limit=int(args.get("evidence_limit") or 20),
        )
    if action == "resume":
        return service.recordings.resume(
            group_id,
            recording_id,
            actor_id=actor_id,
            authority=authority,
        )
    if action == "call":
        return service.recordings.call(
            group_id,
            recording_id,
            actor_id=actor_id,
            authority=authority,
            tool=str(args.get("tool") or "").strip(),
            arguments=args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
            workflow_arguments=args.get("workflow_arguments") if isinstance(args.get("workflow_arguments"), dict) else None,
            record=args.get("record") is not False,
            title=str(args.get("title") or ""),
            success_condition=args.get("success_condition") or "",
            target=args.get("target") if isinstance(args.get("target"), dict) else None,
            element_id=str(args.get("element_id") or ""),
            timeout_seconds=(
                float(args["timeout_seconds"])
                if args.get("timeout_seconds") is not None
                else None
            ),
        )
    if action == "wait":
        return service.recordings.wait(
            group_id,
            recording_id,
            actor_id=actor_id,
            authority=authority,
            duration_seconds=float(args.get("duration_seconds") or 0),
            title=str(args.get("title") or ""),
        )
    if action == "update_step":
        return service.recordings.update_step(
            group_id,
            recording_id,
            actor_id=actor_id,
            authority=authority,
            step_id=str(args.get("step_id") or ""),
            patch=args.get("patch") if isinstance(args.get("patch"), dict) else {},
        )
    if action == "undo":
        return service.recordings.undo(group_id, recording_id, actor_id=actor_id, authority=authority)
    if action == "commit":
        return service.recordings.commit(group_id, recording_id, actor_id=actor_id, authority=authority)
    if action == "abort":
        if str(args.get("caller_surface") or "").strip().lower() == "local_mcp":
            return service.recordings.abort(
                group_id,
                recording_id,
                actor_id=actor_id,
                stop_claim=stop_claim,
                reason=str(args.get("reason") or "aborted"),
            )
        return service.recordings.abort_local_admin(
            group_id,
            recording_id,
            actor_id=actor_id,
            reason=str(args.get("reason") or "aborted"),
        )
    raise ValueError(f"unsupported recording action: {action}")


def _run(service: Any, args: Dict[str, Any], group_id: str, actor_id: str) -> Any:
    action = str(args.get("action") or "start").strip().lower()
    run_id = str(args.get("run_id") or "").strip()
    if action in {"status", "recover", "verify", "cancel", "recovery"}:
        if not run_id:
            raise ValueError("run_id is required")
        run = service.runner.get(group_id, run_id)
        if str(run.get("actor_id") or "") != actor_id:
            raise PermissionError("run belongs to another actor")
        if action == "status":
            return run
        if action == "recovery":
            return service.runner.recovery_context(group_id, run_id)
        if action == "cancel":
            return service.runner.cancel_sync(group_id, run_id, emergency=bool(args.get("emergency")))
        if action == "verify":
            verified = service.runner.verify(
                group_id,
                run_id,
                actor_id=actor_id,
                passed=bool(args.get("passed")),
                summary=str(args.get("summary") or ""),
                evidence_ids=args.get("evidence_ids") if isinstance(args.get("evidence_ids"), list) else [],
                fingerprint=str(service.setup.status().get("fingerprint") or ""),
            )
            authorization = verified.get("authorization") if isinstance(verified.get("authorization"), dict) else {}
            request_id = str(authorization.get("request_id") or "").strip()
            if request_id:
                service.requests.update(
                    group_id,
                    request_id,
                    status="completed" if bool(args.get("passed")) else "verification_failed",
                    completed_at=verified.get("verification", {}).get("verified_at"),
                )
            return verified
        return service.runner.submit_recovery(
            group_id,
            run_id,
            str(args.get("recovery_id") or "").strip(),
            actor_id=actor_id,
            tool=str(args.get("tool") or ""),
            arguments=args.get("arguments") if isinstance(args.get("arguments"), dict) else None,
            resolution=str(args.get("resolution") or "retry"),
            node_id=str(args.get("node_id") or ""),
            target=args.get("target") if isinstance(args.get("target"), dict) else None,
            idempotency_key=str(args.get("idempotency_key") or ""),
        )
    if action != "start":
        raise ValueError(f"unsupported run action: {action}")
    workflow_id = str(args.get("workflow_id") or "").strip()
    if not workflow_id:
        raise ValueError("workflow_id is required")
    manifest = service.store.get(group_id, workflow_id)["manifest"]
    fingerprint = str(service.setup.status().get("fingerprint") or "")
    version = int(args.get("version") or service.store.effective_version(manifest, fingerprint) or 0)
    if not version:
        raise PermissionError("工作流没有已发布且受信的可运行版本")
    trusted = manifest.get("trusted") if isinstance(manifest.get("trusted"), dict) else {}
    is_trusted = isinstance(trusted.get(str(version)), dict) and trusted[str(version)].get("fingerprint") == fingerprint
    request = None
    if not is_trusted:
        request_id = str(args.get("request_id") or "").strip()
        request = service.requests.require_authorized(group_id, request_id, actor_id)
        if str(request.get("mode") or "") != "create_and_run" or str(request.get("workflow_id") or "") != workflow_id:
            raise PermissionError("request does not authorize this workflow")
        risk = workflow_risk(service.store.get(group_id, workflow_id, version=version)["definition"])
        if risk["level"] == "high" and not bool(request.get("allow_high_risk")) and not bool(request.get("high_risk_approved")):
            service.requests.update(group_id, request_id, status="pending_approval", risk=risk)
            raise PermissionError("workflow contains high-risk computer operations and requires approval")
    run = service.runner.start_sync(
        group_id,
        workflow_id,
        actor_id=actor_id,
        version=version,
        inputs=args.get("inputs") if isinstance(args.get("inputs"), dict) else {},
        authorization=request,
    )
    if request is not None:
        service.requests.update(
            group_id,
            str(request["request_id"]),
            status="running",
            run_id=run.get("run_id"),
            workflow_id=workflow_id,
        )
    return run


def _picker(service: Any, args: Dict[str, Any], group_id: str, actor_id: str) -> Any:
    """Handle the daemon-owned native element picker lifecycle."""
    action = str(args.get("action") or "status").strip().lower()
    session_id = str(args.get("session_id") or "").strip()
    picker = service.picker
    if action == "start":
        return picker.start(
            group_id,
            actor_id or "user",
            hotkey=str(args.get("hotkey") or "Ctrl+Shift+LeftClick"),
            session_id=session_id,
        )
    if not session_id:
        raise ValueError("session_id is required")
    if action in {"status", "get"}:
        return picker.status(session_id)
    if action == "events":
        try:
            after_seq = int(args.get("after_seq") or 0)
        except (TypeError, ValueError):
            after_seq = 0
        return picker.events(session_id, after_seq=after_seq)
    if action == "lock":
        raw_point = args.get("point")
        point = None
        if isinstance(raw_point, (list, tuple)) and len(raw_point) >= 2:
            try:
                point = [int(float(raw_point[0])), int(float(raw_point[1]))]
            except (TypeError, ValueError):
                point = None
        candidate = args.get("element") if isinstance(args.get("element"), dict) else None
        value = picker.lock(session_id, point=point, element=candidate)
        session = value.get("session") if isinstance(value, dict) and isinstance(value.get("session"), dict) else {}
        return {**session, "element": value.get("element"), "stable": value.get("stable")}
    if action == "confirm":
        locator = args.get("locator") if isinstance(args.get("locator"), dict) else None
        value = picker.confirm(session_id, locator=locator)
        session = value.get("session") if isinstance(value, dict) and isinstance(value.get("session"), dict) else {}
        return {**session, "element": value.get("element"), "locator": value.get("locator")}
    if action in {"cancel", "end", "stop"}:
        return picker.cancel(session_id, reason=str(args.get("reason") or "cancelled"))
    raise ValueError(f"unsupported picker action: {action}")


def _workflow(service: Any, args: Dict[str, Any], group_id: str, actor_id: str) -> Any:
    action = str(args.get("action") or "list").strip().lower()
    workflow_id = str(args.get("workflow_id") or "").strip()
    fingerprint = str(service.setup.status().get("fingerprint") or "")
    if action == "list":
        return {"workflows": service.store.list(group_id)}
    if action == "get":
        if not workflow_id:
            raise ValueError("workflow_id is required")
        return service.store.get(group_id, workflow_id, version=args.get("version"))
    definition = None
    if action in {"validate", "create", "update", "propose", "update_triggers"}:
        definition = WorkflowDefinition.model_validate(args.get("definition") or {})
        validate_workflow_tools(definition, service.session.catalog_sync())
    if action == "validate":
        return {"valid": True, "risk": workflow_risk(definition.model_dump(mode="json"))}

    request_id = str(args.get("request_id") or "").strip()
    request = service.requests.require_authorized(group_id, request_id, actor_id)
    if str(request.get("mode") or "") != "create_and_run":
        raise PermissionError("该请求未授权 AI 编辑电脑控制工作流")
    if action in {"create", "update", "update_triggers", "propose", "repair"} and not bool(request.get("allow_workflow_edit")):
        raise PermissionError("用户未授权 AI 修改电脑控制工作流")
    if workflow_id and str(request.get("workflow_id") or "") not in {"", workflow_id}:
        raise PermissionError("该请求已绑定其他工作流")

    def finalize(value: Dict[str, Any]) -> Dict[str, Any]:
        version = int(value["version"])
        if not bool(request.get("allow_publish")) or not bool(request.get("allow_trust")):
            return value
        return service.store.auto_finalize(group_id, str(value["manifest"]["workflow_id"]), version, fingerprint=fingerprint)

    if action == "create" and definition is not None:
        value = service.store.create(group_id, definition, created_by=actor_id, source_request_id=request_id)
        workflow_id = str(value["manifest"]["workflow_id"])
        value = finalize(value)
        service.requests.mark_workflow_created(
            group_id,
            request_id,
            created_workflow_id=workflow_id,
            status="draft_created",
        )
        return value
    if not workflow_id:
        raise ValueError(f"workflow_id is required for action={action}")
    current = service.store.get(group_id, workflow_id)
    if action in {"update", "update_triggers"} and definition is not None:
        if action == "update_triggers" and not all(bool(request.get(key)) for key in ("allow_publish", "allow_trust", "allow_unattended_triggers")):
            raise PermissionError("开启无人值守触发需要用户授权")
        value = service.store.update(
            group_id,
            workflow_id,
            definition,
            expected_revision=int(args.get("expected_revision") or current["manifest"].get("revision") or 0),
            changed_by=actor_id,
            change_note=str(args.get("change_note") or "AI 编排更新"),
        )
        value = finalize(value)
        service.requests.update(group_id, request_id, workflow_id=workflow_id, status="draft_updated")
        return value
    if action == "propose" and definition is not None:
        return service.store.create_proposal(
            group_id,
            workflow_id,
            definition,
            base_version=int(args.get("version") or current["version"]),
            created_by=actor_id,
            summary=str(args.get("change_note") or "AI 自适应优化建议"),
        )
    if action == "repair":
        patch = args.get("patch") if isinstance(args.get("patch"), dict) else {}
        payload = current["definition"]
        nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
        node_id = str(args.get("node_id") or "")
        updated = False
        for node in nodes:
            if isinstance(node, dict) and str(node.get("id") or "") == node_id:
                node.update({key: value for key, value in patch.items() if key in {"tool", "arguments", "target", "title", "success_condition", "timeout_seconds", "retries", "adaptive"}})
                updated = True
                break
        if not updated:
            raise ValueError("repair node not found")
        repaired = WorkflowDefinition.model_validate({key: value for key, value in payload.items() if key != "change_note"})
        value = service.store.update(
            group_id,
            workflow_id,
            repaired,
            expected_revision=int(args.get("expected_revision") or current["manifest"].get("revision") or 0),
            changed_by=actor_id,
            change_note=str(args.get("change_note") or "AI 修复失败步骤"),
        )
        value = finalize(value)
        service.requests.update(group_id, request_id, workflow_id=workflow_id, status="draft_updated")
        return value
    if action == "publish":
        if not bool(request.get("allow_publish")):
            raise PermissionError("用户未授权 AI 发布工作流")
        return service.store.publish(group_id, workflow_id, int(args.get("version") or current["version"]))
    if action == "trust":
        if not bool(request.get("allow_trust")):
            raise PermissionError("用户未授权 AI 授予长期信任")
        if not fingerprint:
            raise RuntimeError("Windows-MCP 尚未完成验证")
        return service.store.trust(group_id, workflow_id, int(args.get("version") or current["version"]), fingerprint=fingerprint, permissions=["all_windows_mcp_tools"])
    raise ValueError(f"unsupported workflow action: {action}")


def try_handle_computer_control_op(op: str, args: Dict[str, Any]) -> Optional[Tuple[DaemonResponse, bool]]:
    if op != "computer_control":
        return None
    command = str(args.get("command") or "").strip()
    group_id = str(args.get("group_id") or "").strip()
    actor_id = str(args.get("actor_id") or "").strip()
    caller_surface = str(args.get("caller_surface") or "").strip().lower()
    action_defaults = {"workflow": "list", "run": "start", "picker": "status", "lease": "status", "setup": "status"}
    action = str(args.get("action") or action_defaults.get(command, "")).strip().lower()
    if caller_surface not in {"local_mcp", "local_web"}:
        return _error(
            "permission_denied",
            "computer control is restricted to trusted local surfaces",
            details={"caller_surface": caller_surface or "missing"},
        )
    if not group_id:
        return _error("invalid_request", "group_id is required")
    recording_start_authority: Optional[RecordingStartClaim] = None
    recording_authority: Optional[DerivedAuthorityClaim] = None
    recording_stop_owner: Optional[RecordingStopOwnerClaim] = None
    if caller_surface == "local_mcp":
        group = load_group(group_id)
        actor = find_actor(group, actor_id) if group is not None and actor_id else None
        if not isinstance(actor, dict) or actor_id == "user":
            return _error("permission_denied", "computer control requires a bound local actor runtime")
        if str(actor.get("runtime") or "").strip().lower() == "web_model":
            return _error("permission_denied", "computer control is unavailable to Web Model actors")
        if requires_live_turn_claim(command, action):
            claim = validate_turn_grant_receipt(
                group,
                actor_id,
                turn_grant_receipt=args.get("turn_grant_receipt"),
            )
            if claim is None:
                return _error(
                    "permission_denied",
                    "computer control requires the exact live local-user turn grant",
                    details={"command": command, "action": action, "reason": "turn_grant_required"},
                )
    if caller_surface == "local_mcp" and requires_live_turn_claim(command, action):
        try:
            request_id = request_id_requiring_turn_binding(command, action, args)
            request_binding_required = bool(
                (command == "recording" and action == "start")
                or (command == "workflow" and action not in {"list", "get", "validate"})
            )
            if request_binding_required and not request_id:
                raise PermissionError("computer control request_id is required for this action")
            if request_id:
                assert group is not None
                request_store = ComputerRequestStore(WorkflowStore(ensure_home()))

                def activate_request(claim: Any) -> Any:
                    if command == "recording" and action == "start":
                        return request_store.activate_recording_start_for_turn_claim(
                            group_id,
                            request_id,
                            actor_id,
                            claim=claim,
                        )
                    request = request_store.require_authorized(group_id, request_id, actor_id)
                    event_id = str(request.get("event_id") or "").strip()
                    ledger_event = lookup_event_by_id(group.ledger_path, event_id) if event_id else None
                    provenance = load_event_turn_provenance(group, event_id) if event_id else None
                    return request_store.activate_for_turn_claim(
                        group_id,
                        request_id,
                        actor_id,
                        claim=claim,
                        provenance=provenance,
                        ledger_event=ledger_event,
                    )

                activated = consume_turn_grant_receipt(
                    group,
                    actor_id,
                    turn_grant_receipt=args.get("turn_grant_receipt"),
                    consumer=activate_request,
                )
                if command == "recording" and action == "start":
                    if not isinstance(activated, RecordingStartClaim):
                        raise PermissionError("computer control turn grant is no longer current")
                    recording_start_authority = activated
                elif not isinstance(activated, dict):
                    raise PermissionError("computer control turn grant is no longer current")
        except PermissionError as exc:
            return _error("permission_denied", str(exc), details={"retryable": False})
        except Exception as exc:
            details = {
                "layer": str(getattr(exc, "layer", "computer_control")),
                "next_action": str(getattr(exc, "next_action", "") or ""),
                "field_errors": getattr(exc, "field_errors", {})
                if isinstance(getattr(exc, "field_errors", {}), dict)
                else {},
                "retryable": bool(getattr(exc, "retryable", False)),
            }
            return _error(str(getattr(exc, "code", "computer_control_failed")), str(exc), details=details)
    if command == "recording" and action not in {"start", "abort"}:
        try:
            authority_store = DerivedAuthorityStore(
                ensure_home(),
                issuer_epoch_provider=get_daemon_turn_issuer_epoch,
                generation_provider=get_actor_turn_generation,
            )
            if action == "resume":
                recording_authority = authority_store.validate_suspended_receipt(
                    args.get("recording_authority_receipt"),
                    expected_group_id=group_id,
                    expected_actor_id=actor_id,
                    expected_resource_id=str(args.get("recording_id") or "").strip(),
                    expected_kind="recording",
                )
            elif action == "get":
                recording_authority = authority_store.validate_read_receipt(
                    args.get("recording_authority_receipt"),
                    expected_group_id=group_id,
                    expected_actor_id=actor_id,
                    expected_resource_id=str(args.get("recording_id") or "").strip(),
                    expected_kind="recording",
                )
            else:
                recording_authority = authority_store.validate_active_receipt(
                    args.get("recording_authority_receipt"),
                    expected_group_id=group_id,
                    expected_actor_id=actor_id,
                    expected_resource_id=str(args.get("recording_id") or "").strip(),
                    expected_kind="recording",
                )
        except PermissionError as exc:
            return _error("permission_denied", str(exc), details={"retryable": False})
        except Exception as exc:
            return _error("permission_denied", str(exc), details={"retryable": False})
    if caller_surface == "local_mcp" and command == "recording" and action == "abort":
        try:
            recording_stop_owner = DerivedAuthorityStore(
                ensure_home(),
                issuer_epoch_provider=get_daemon_turn_issuer_epoch,
                generation_provider=get_actor_turn_generation,
            ).validate_recording_stop_owner(
                expected_group_id=group_id,
                expected_actor_id=actor_id,
                expected_resource_id=str(args.get("recording_id") or "").strip(),
                expected_kind="recording",
            )
        except PermissionError as exc:
            return _error("permission_denied", str(exc), details={"retryable": False})
        except Exception as exc:
            return _error("permission_denied", str(exc), details={"retryable": False})
    service = get_services(ensure_home())
    try:
        if command == "catalog":
            tools = annotate_catalog(service.session.catalog_sync())
            requested = str(args.get("tool") or "").strip()
            if requested:
                selected = next((item for item in tools if str(item.get("name") or "") == requested), None)
                if selected is None:
                    return _error("tool_not_found", f"Windows-MCP tool not found: {requested}")
                value = {"setup": service.setup.status(), "lease": service.lease.status(), "tool": selected}
            else:
                include_schema = bool(args.get("include_schema"))
                value = {
                    "setup": service.setup.status(),
                    "lease": service.lease.status(),
                    "tools": [
                        ({
                            "name": item.get("name"),
                            "title": item.get("title") or item.get("name"),
                            "risk_level": item.get("risk_level"),
                            "description": str(item.get("description") or "")[:240],
                        } if not include_schema else item)
                        for item in tools
                    ],
                }
            return _ok(value)
        if command == "lease":
            action = str(args.get("action") or "status").strip().lower()
            if action == "status":
                return _ok(service.lease.status())
            return _error("invalid_request", f"unsupported lease action: {action}")
        if command == "setup":
            action = str(args.get("action") or "status").strip().lower()
            if action == "status":
                return _ok({**service.setup.status(), **_observation_status(service)})
            if action == "ensure":
                setup_status = service.setup.status()
                needs_change = bool(args.get("force")) or not (
                    setup_status.get("phase") == "ready" and bool(setup_status.get("session_running"))
                )
                lease_status = service.lease.status()
                if needs_change and lease_status.get("active"):
                    return _error(
                        "computer_control_busy",
                        "computer control is already in use",
                        details=lease_status.get("lease") if isinstance(lease_status.get("lease"), dict) else {},
                    )
                return _ok(service.session.run_sync(service.setup.ensure(force=bool(args.get("force")))))
            if action == "repair":
                lease_status = service.lease.status()
                if lease_status.get("active"):
                    return _error(
                        "computer_control_busy",
                        "computer control is already in use",
                        details=lease_status.get("lease") if isinstance(lease_status.get("lease"), dict) else {},
                    )
                return _ok(service.session.run_sync(service.setup.repair()))
            if action == "upgrade":
                lease_status = service.lease.status()
                if lease_status.get("active"):
                    return _error(
                        "computer_control_busy",
                        "computer control is already in use",
                        details=lease_status.get("lease") if isinstance(lease_status.get("lease"), dict) else {},
                    )
                return _ok(service.session.run_sync(service.setup.upgrade()))
            if action == "cancel":
                return _ok(service.session.run_sync(service.setup.cancel()))
            if action == "restart_session":
                setup_status = service.setup.status()
                if bool(setup_status.get("in_progress")):
                    return _error(
                        "computer_control_setup_in_progress",
                        "Windows-MCP 正在安装或更新，会话暂时不能重启",
                        details={"phase": setup_status.get("phase")},
                    )
                restart_id = "session_restart_" + uuid.uuid4().hex[:14]
                with service.lease.hold(
                    group_id="_global",
                    actor_id="session-restart",
                    run_id=restart_id,
                    observe_only=False,
                ) as lease_guard:
                    restarted = service.session.restart_sync(timeout=None)
                    if lease_guard["lost"].is_set():
                        raise PermissionError("computer_control_lease_required")
                    tools = service.session.catalog_sync()
                    refreshed_setup = service.setup.refresh_catalog(tools)
                    invalidate = getattr(service.observation, "invalidate", None)
                    if callable(invalidate):
                        invalidate()
                    return _ok(
                        {
                            **restarted,
                            "setup_phase": refreshed_setup.get("phase"),
                            "fingerprint": refreshed_setup.get("fingerprint"),
                            **_observation_status(service),
                        }
                    )
            if action == "force_release":
                released = service.lease.release(run_id="", force=True)
                service.session.stop_sync()
                return _ok({"released": released})
            return _error("invalid_request", f"unsupported setup action: {action}")
        if command == "recording":
            return _ok(
                _recording(
                    service,
                    args,
                    group_id,
                    actor_id,
                    start_claim=recording_start_authority,
                    authority=recording_authority,
                    stop_claim=recording_stop_owner,
                )
            )
        if command == "workflow":
            return _ok(_workflow(service, args, group_id, actor_id))
        if command == "run":
            return _ok(_run(service, args, group_id, actor_id))
        if command == "picker":
            return _ok(_picker(service, args, group_id, actor_id))
        if command == "element_snapshot":
            capture_id = str(args.get("capture_id") or "capture")
            with service.lease.hold(
                group_id=group_id,
                actor_id="element-picker",
                run_id=capture_id,
                observe_only=True,
            ) as lease_guard:
                tools = service.session.catalog_sync()
                snapshot_name = next((str(item.get("name")) for item in tools if str(item.get("name") or "").lower() == "snapshot"), "")
                if not snapshot_name:
                    raise RuntimeError("Windows-MCP Snapshot tool is unavailable")
                raw_snapshot = service.session.call_tool_sync(snapshot_name, {})
                if lease_guard["lost"].is_set():
                    raise PermissionError("computer_control_lease_required")
                locator = args.get("locator") if isinstance(args.get("locator"), dict) else None
                if locator is not None:
                    observation = service.observation.enhance_sync(normalize_snapshot(raw_snapshot), locator)
                    raw_snapshot = {**raw_snapshot, "_onecolleague_observation": observation}
                return _ok(raw_snapshot)
        return _error("invalid_request", f"unsupported computer-control command: {command}")
    except MCPUnavailable as exc:
        return _error(
            str(getattr(exc, "code", "windows_mcp_transport_failed")),
            str(exc),
            details=_infrastructure_details(service),
        )
    except LeaseConflict as exc:
        return _error("computer_control_busy", str(exc), details=exc.lease)
    except PermissionError as exc:
        code = str(exc).strip()
        if code.startswith("computer_control_"):
            return _error(code, code, details={"lease": service.lease.status(), "retryable": True})
        return _error("permission_denied", str(exc), details={"retryable": False})
    except Exception as exc:
        details = {
            "layer": str(getattr(exc, "layer", "computer_control")),
            "next_action": str(getattr(exc, "next_action", "") or ""),
            "field_errors": getattr(exc, "field_errors", {}) if isinstance(getattr(exc, "field_errors", {}), dict) else {},
            "retryable": bool(getattr(exc, "retryable", False)),
        }
        return _error(str(getattr(exc, "code", "computer_control_failed")), str(exc), details=details)
