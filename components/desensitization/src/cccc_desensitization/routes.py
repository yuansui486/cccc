from __future__ import annotations

import json
import hmac
import mimetypes
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import service


async def require_internal(authorization: str | None = Header(default=None)) -> None:
    import os
    token = os.environ.get("CCCC_DESENSITIZATION_TOKEN", "").strip()
    expected = f"Bearer {token}" if token else ""
    if not token or not authorization or not hmac.compare_digest(str(authorization), expected):
        raise HTTPException(status_code=401, detail={"code": "invalid_sidecar_token", "message": "invalid sidecar token", "details": {}})


class FileMaskRequest(BaseModel):
    embedded_images: list[dict[str, Any]] | None = Field(default=None, max_length=10000)
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(min_length=32, max_length=32)
    filename: str = Field(default="", max_length=240)
    entities: list[dict[str, Any]] | None = Field(default=None, max_length=10000)
    policies: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=500)
    boxes: list[dict[str, Any]] | None = Field(default=None, max_length=10000)
    reversible: bool = False
    password: str | None = Field(default=None, max_length=256)
    image_override: bool = False


class PreviewRequest(BaseModel):
    embedded_images: list[dict[str, Any]] | None = Field(default=None, max_length=10000)
    model_config = ConfigDict(extra="forbid")
    analysis_id: str | None = Field(default=None, min_length=32, max_length=32)
    filename: str = Field(default="", max_length=240)
    entities: list[dict[str, Any]] | None = Field(default=None, max_length=10000)
    policies: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=500)
    boxes: list[dict[str, Any]] | None = Field(default=None, max_length=10000)
    image_override: bool = False


class TextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2_000_000)


class TextMaskRequest(TextRequest):
    entities: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    policies: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=500)


class TextRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=32, max_length=32)
    masked_text: str | None = Field(default=None, max_length=2_000_000)


class ImageMaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_id: str = Field(min_length=32, max_length=32)
    boxes: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    blur_radius: int = Field(default=14, ge=1, le=80)
    policies: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=500)


class DocumentMaskRequest(FileMaskRequest):
    reviewed: bool = True


class CustomSensitiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(default="literal", pattern="^(literal|category)$")
    value: str = Field(min_length=1, max_length=10000)
    name: str | None = Field(default=None, max_length=120)
    entity_type: str = Field(default="CUSTOM", min_length=1, max_length=80)
    replacement: str = Field(default="已脱敏", max_length=500)
    enabled: bool = True


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=12, max_length=256)


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _error(exc: Exception, default_status: int = 422) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    message = str(exc)[:1000]
    status, code = default_status, "desensitization_error"
    if "group not found" in message:
        status, code = 404, "group_not_found"
    elif "not found" in message or "expired" in message:
        status, code = 404, "not_found"
    elif "unsupported" in message or "signature" in message or "格式" in message:
        status, code = 415, "unsupported_format"
    elif "limit" in message or "disk space" in message or "available memory" in message:
        status, code = 413, "resource_limit"
    elif "still active" in message or "awaiting review" in message:
        status, code = 409, "state_conflict"
    elif "disabled" in message or "dependencies" in message:
        status, code = 503, "capability_unavailable"
    return HTTPException(status_code=status, detail={"code": code, "message": message, "details": {}})


async def _call(fn: Callable[..., Any], *args: Any, status: int = 422, **kwargs: Any) -> Any:
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except Exception as exc:
        raise _error(exc, status) from exc


def _require_ready() -> None:
    status = service.model_status()
    if not status.get("enabled"):
        raise _error(ValueError("desensitization is disabled"), 503)
    if status.get("status") != "ready":
        raise HTTPException(status_code=503, detail={
            "code": "models_not_ready", "message": "脱敏模型尚未就绪", "details": status,
        })


async def _stage_upload(group_id: str, upload: UploadFile) -> tuple[Path, int]:
    try:
        await upload.seek(0)
        return await run_in_threadpool(service.write_stream, group_id, upload.file, upload.filename or "upload.bin")
    finally:
        await upload.close()


def _file_response(group_id: str, task_id: str, kind: str) -> FileResponse:
    path, filename, disposition = service.task_file(group_id, task_id, kind)
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, filename=filename, media_type=media_type, content_disposition_type=disposition,
                        headers={"Cache-Control": "private, no-store"})


def create_routers() -> list[APIRouter]:
    global_router = APIRouter(prefix="/api/v1/desensitization", dependencies=[Depends(require_internal)])
    group_router = APIRouter(prefix="/api/v1/groups/{group_id}/desensitization",
                             dependencies=[Depends(require_internal)])

    @global_router.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {"ok": True, "result": service.capabilities()}

    @global_router.get("/models/status")
    async def models_status() -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.model_status, status=503)}

    @global_router.post("/models/initialize", dependencies=[Depends(require_internal)])
    async def models_initialize() -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.initialize_models, status=503)}

    @global_router.get("/models/jobs/{job_id}")
    async def models_job(job_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.model_job, job_id, status=404)}

    @group_router.get("/metadata")
    async def metadata(group_id: str) -> dict[str, Any]:
        await _call(service.recover_pending, group_id)
        result = await _call(service.output_info, group_id, status=404)
        return {"ok": True, "result": {"group_id": group_id, **result, "capabilities": service.capabilities()}}

    @group_router.get("/entities")
    async def entities(group_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.entity_directory, group_id)}

    @group_router.post("/files/analyze")
    async def files_analyze(group_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        _require_ready()
        filename = file.filename or "upload.bin"
        path, _size = await _stage_upload(group_id, file)
        try:
            result = await _call(service.analyze_path, group_id, path, filename)
            return {"ok": True, "result": result}
        finally:
            path.unlink(missing_ok=True)

    @group_router.post("/files/mask")
    async def files_mask(group_id: str, payload: FileMaskRequest) -> dict[str, Any]:
        _require_ready()
        result = await _call(service.mask_file, group_id, payload.analysis_id, payload.entities,
                             payload.filename, payload.policies, payload.reversible, payload.password,
                             payload.boxes, payload.image_override, payload.embedded_images)
        return {"ok": True, "result": result}

    @group_router.post("/tasks/{task_id}/preview")
    async def task_preview(group_id: str, task_id: str, payload: PreviewRequest) -> dict[str, Any]:
        _require_ready()
        return {"ok": True, "result": await _call(service.preview_file, group_id, task_id,
                                                     payload.model_dump(exclude_none=True))}

    @group_router.post("/files/preview")
    async def files_preview(group_id: str, payload: PreviewRequest) -> dict[str, Any]:
        _require_ready()
        data = payload.model_dump(exclude_none=True)
        task_id = str(data.get("analysis_id") or "")
        if not task_id:
            raise _error(ValueError("analysis_id is required"), 422)
        return {"ok": True, "result": await _call(service.preview_file, group_id, task_id, data)}

    @group_router.get("/tasks/{task_id}/analysis")
    async def task_analysis(group_id: str, task_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.get_analysis, group_id, task_id, status=404)}

    @group_router.get("/tasks")
    async def tasks(group_id: str, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                    task_status: str = Query("", alias="status", max_length=32),
                    kind: str = Query("", max_length=32), q: str = Query("", max_length=120)) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.list_tasks, group_id, limit, offset,
                                                     task_status, kind, q)}

    @group_router.delete("/tasks/{task_id}")
    async def task_delete(group_id: str, task_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.delete_task, group_id, task_id, status=404)}

    @group_router.post("/tasks/{task_id}/restore")
    async def task_restore(group_id: str, task_id: str, payload: RestoreRequest) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.restore_file, group_id, task_id, payload.password)}

    @group_router.get("/tasks/{task_id}/source")
    async def task_source(group_id: str, task_id: str) -> FileResponse:
        try:
            return _file_response(group_id, task_id, "source")
        except Exception as exc:
            raise _error(exc, 404) from exc

    @group_router.get("/tasks/{task_id}/source-preview")
    async def task_source_preview(group_id: str, task_id: str) -> FileResponse:
        return await task_source(group_id, task_id)

    async def _task_artifact_response(group_id: str, task_id: str, kind: str) -> FileResponse:
        try:
            return _file_response(group_id, task_id, kind)
        except Exception as exc:
            raise _error(exc, 404) from exc

    @group_router.get("/tasks/{task_id}/artifact")
    async def task_artifact(group_id: str, task_id: str) -> FileResponse:
        return await _task_artifact_response(group_id, task_id, "artifact")

    @group_router.get("/tasks/{task_id}/artifacts/{name}")
    async def task_artifact_legacy(group_id: str, task_id: str, name: str) -> FileResponse:
        path, filename, disposition = await _call(service.task_file, group_id, task_id, "artifact", status=404)
        if Path(name).name != name or Path(name).name != Path(filename).name:
            raise _error(ValueError("artifact not found"), 404)
        return FileResponse(path, filename=filename, content_disposition_type=disposition,
                            headers={"Cache-Control": "private, no-store"})

    @group_router.get("/tasks/{task_id}/report")
    async def task_report(group_id: str, task_id: str) -> FileResponse:
        return await _task_artifact_response(group_id, task_id, "report")

    @group_router.get("/tasks/{task_id}/restored")
    async def task_restored(group_id: str, task_id: str) -> FileResponse:
        return await _task_artifact_response(group_id, task_id, "restored")

    @group_router.get("/tasks/{task_id}/source-image")
    async def task_source_image(group_id: str, task_id: str) -> Response:
        data, media = await _call(service.source_page, group_id, task_id, "image", status=404)
        return Response(data, media_type=media, headers={"Cache-Control": "private, no-store"})

    @group_router.get("/tasks/{task_id}/embedded-images/{image_id}")
    async def embedded_image(group_id: str, task_id: str, image_id: str, preview_id: str = "") -> Response:
        data = await _call(service.embedded_image, group_id, task_id, image_id, preview_id, status=404)
        return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @group_router.get("/tasks/{task_id}/source-pages/{page_number}")
    async def task_source_page(group_id: str, task_id: str, page_number: int) -> Response:
        data, media = await _call(service.source_page, group_id, task_id, "page", page_number, status=404)
        return Response(data, media_type=media, headers={"Cache-Control": "private, no-store"})

    @group_router.get("/tasks/{task_id}/previews/{preview_id}")
    async def preview(group_id: str, task_id: str, preview_id: str) -> Response:
        path, data, media = await _call(service.preview_file_path, group_id, task_id, preview_id, "file", status=404)
        if path is not None:
            return FileResponse(path, media_type=media, content_disposition_type="inline",
                                headers={"Cache-Control": "private, no-store"})
        return Response(data or b"", media_type=media)

    @group_router.get("/tasks/{task_id}/previews/{preview_id}/image")
    async def preview_image(group_id: str, task_id: str, preview_id: str) -> Response:
        _path, data, media = await _call(service.preview_file_path, group_id, task_id, preview_id, "image", status=404)
        return Response(data or b"", media_type=media, headers={"Cache-Control": "private, no-store"})

    @group_router.get("/tasks/{task_id}/previews/{preview_id}/pages/{page_number}")
    async def preview_page(group_id: str, task_id: str, preview_id: str, page_number: int) -> Response:
        _path, data, media = await _call(service.preview_file_path, group_id, task_id, preview_id,
                                          "page", page_number, status=404)
        return Response(data or b"", media_type=media, headers={"Cache-Control": "private, no-store"})

    @group_router.post("/text/analyze")
    async def text_analyze(group_id: str, payload: TextRequest) -> dict[str, Any]:
        _require_ready()
        found = await _call(service.analyze_text, group_id, payload.text)
        return {"ok": True, "result": {"text": payload.text, "entities": found}}

    @group_router.post("/text/mask")
    async def text_mask(group_id: str, payload: TextMaskRequest) -> dict[str, Any]:
        _require_ready()
        return {"ok": True, "result": await _call(service.mask_text, group_id, payload.text,
                                                    payload.entities, payload.policies)}

    @group_router.post("/text/unmask")
    async def text_unmask(group_id: str, payload: TextRestoreRequest) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.unmask_text, group_id, payload.task_id,
                                                    payload.masked_text, status=404)}

    @group_router.post("/text/restore")
    async def text_restore(group_id: str, payload: TextRestoreRequest) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.unmask_text, group_id, payload.task_id,
                                                    payload.masked_text, status=404)}

    @group_router.post("/image/analyze")
    async def image_analyze(group_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        _require_ready()
        filename = file.filename or "upload.png"
        path, _size = await _stage_upload(group_id, file)
        try:
            result = await _call(service.analyze_path, group_id, path, filename)
            return {"ok": True, "result": result}
        finally:
            path.unlink(missing_ok=True)

    @group_router.post("/image/mask")
    async def image_mask(group_id: str, payload: ImageMaskRequest) -> dict[str, Any]:
        _require_ready()
        result = await _call(service.mask_file, group_id, payload.analysis_id, None,
                             "", payload.policies or {"DEFAULT": {"image_action": "blur", "blur_radius": payload.blur_radius}},
                             False, None, payload.boxes, True, status=404)
        return {"ok": True, "result": result}

    @group_router.post("/document/analyze")
    async def document_analyze(group_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        _require_ready()
        filename = file.filename or "upload.bin"
        path, _size = await _stage_upload(group_id, file)
        try:
            result = await _call(service.analyze_path, group_id, path, filename)
            return {"ok": True, "result": result}
        finally:
            path.unlink(missing_ok=True)

    @group_router.post("/document/mask")
    async def document_mask(group_id: str, payload: DocumentMaskRequest) -> dict[str, Any]:
        _require_ready()
        result = await _call(service.mask_file, group_id, payload.analysis_id, payload.entities,
                             payload.filename, payload.policies, payload.reversible, payload.password,
                             payload.boxes, payload.image_override, payload.embedded_images, status=404)
        return {"ok": True, "result": result}

    @group_router.get("/rules")
    async def rules(group_id: str, category: str = "", scope: str = "", q: str = "") -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.list_rules, group_id, category, scope, q)}

    @group_router.post("/rules", dependencies=[Depends(require_internal)])
    async def rules_create(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.create_rule, group_id, payload)}

    @group_router.patch("/rules/model", dependencies=[Depends(require_internal)])
    async def rules_model(group_id: str, payload: EnabledRequest) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.toggle_model_rule, group_id, payload.enabled)}

    @group_router.patch("/rules/builtin/{rule_id}", dependencies=[Depends(require_internal)])
    async def rules_builtin(group_id: str, rule_id: str, payload: EnabledRequest) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.toggle_builtin_rule, group_id, rule_id, payload.enabled)}

    @group_router.patch("/rules/{rule_id}", dependencies=[Depends(require_internal)])
    async def rules_update(group_id: str, rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.update_rule, group_id, rule_id, payload)}

    @group_router.delete("/rules/{rule_id}", dependencies=[Depends(require_internal)])
    async def rules_delete(group_id: str, rule_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.delete_rule, group_id, rule_id)}

    @group_router.get("/custom-sensitive")
    async def custom_sensitive_list(group_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.list_custom_sensitive, group_id)}

    @group_router.post("/custom-sensitive", dependencies=[Depends(require_internal)])
    async def custom_sensitive_create(group_id: str, payload: CustomSensitiveRequest) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.create_custom_sensitive, group_id,
                                                     payload.model_dump())}

    @group_router.patch("/custom-sensitive/{rule_id}", dependencies=[Depends(require_internal)])
    async def custom_sensitive_update(group_id: str, rule_id: str,
                                      payload: CustomSensitiveRequest) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.update_custom_sensitive, group_id, rule_id,
                                                     payload.model_dump(), status=404)}

    @group_router.delete("/custom-sensitive/{rule_id}", dependencies=[Depends(require_internal)])
    async def custom_sensitive_delete(group_id: str, rule_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.delete_custom_sensitive, group_id, rule_id,
                                                     status=404)}

    @group_router.get("/rule-categories")
    async def categories(group_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.list_rule_categories, group_id)}

    @group_router.post("/rule-categories", dependencies=[Depends(require_internal)])
    async def categories_create(group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.create_rule_category, group_id, payload)}

    @group_router.patch("/rule-categories/{category_id}", dependencies=[Depends(require_internal)])
    async def categories_update(group_id: str, category_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.rename_rule_category, group_id, category_id, payload)}

    @group_router.delete("/rule-categories/{category_id}", dependencies=[Depends(require_internal)])
    async def categories_delete(group_id: str, category_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.delete_rule_category, group_id, category_id)}

    @group_router.get("/policies")
    async def policies(group_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.get_policies, group_id)}

    @group_router.put("/policies", dependencies=[Depends(require_internal)])
    async def policies_put(group_id: str, payload: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.put_policies, group_id, payload)}

    @group_router.post("/policies/reset", dependencies=[Depends(require_internal)])
    async def policies_reset(group_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.reset_policies, group_id)}

    @group_router.get("/batches")
    async def batches_list(group_id: str, limit: int = Query(50, ge=1, le=500),
                           offset: int = Query(0, ge=0)) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.list_batches, group_id, limit, offset)}

    @group_router.post("/batches")
    async def batches_create(request: Request, group_id: str, files: list[UploadFile] = File(...),
                             manifest: str = Form("{}")) -> dict[str, Any]:
        _require_ready()
        staged: list[tuple[str, Path]] = []
        try:
            parsed = json.loads(manifest) if manifest else {}
            if not isinstance(parsed, dict):
                raise ValueError("invalid batch manifest")
            entries = parsed.get("entries") or []
            if entries and len(entries) != len(files):
                raise ValueError("batch manifest does not match uploaded files")
            seen: set[str] = set()
            for index, upload in enumerate(files):
                raw = entries[index].get("relative_path") if entries and isinstance(entries[index], dict) else upload.filename
                relative = service._relative_path(str(raw or upload.filename or "upload.bin"))
                if any(part.startswith(".") for part in relative.split("/")):
                    await upload.close()
                    continue
                key = relative.casefold()
                if key in seen:
                    raise ValueError("batch contains duplicate relative paths")
                seen.add(key)
                path, _size = await _stage_upload(group_id, upload)
                staged.append((relative, path))
            result = await _call(
                service.create_batch_from_paths, group_id, staged, str(parsed.get("root_name") or ""),
                str(parsed.get("mode") or "auto"), parsed.get("options") or {},
                str(request.headers.get("Idempotency-Key") or parsed.get("idempotency_key") or ""))
            return {"ok": True, "result": result}
        except Exception as exc:
            for _, path in staged:
                path.unlink(missing_ok=True)
            raise _error(exc) from exc

    @group_router.get("/batches/{batch_id}")
    async def batch_status(group_id: str, batch_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.get_batch, group_id, batch_id, status=404)}

    @group_router.post("/batches/{batch_id}/cancel")
    async def batch_cancel(group_id: str, batch_id: str) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.cancel_batch, group_id, batch_id, status=404)}

    @group_router.post("/batches/{batch_id}/mask")
    async def batch_mask(group_id: str, batch_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "result": await _call(service.mask_batch, group_id, batch_id, payload or {}, status=409)}

    @group_router.get("/batches/{batch_id}/{kind}")
    async def batch_download(group_id: str, batch_id: str, kind: str) -> FileResponse:
        if kind not in {"download", "report"}:
            raise _error(ValueError("batch artifact not found"), 404)
        path = await _call(service.batch_artifact_path, group_id, batch_id, kind, status=404)
        filename = f"desensitization-{batch_id[:8]}" + (".zip" if kind == "download" else "-report.json")
        return FileResponse(path, filename=filename, headers={"Cache-Control": "private, no-store"})

    @group_router.get("/batches/{batch_id}/download")
    async def batch_download_legacy(group_id: str, batch_id: str) -> FileResponse:
        return await batch_download(group_id, batch_id, "download")

    return [global_router, group_router]
