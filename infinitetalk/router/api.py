"""REST endpoints for the Router."""
from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from common.settings import RouterSettings
from router.job_queue import (
    ERROR,
    FETCHING_RESULT,
    PROCESSING,
    QUEUED,
    RECEIVING,
    SUCCESS,
    SUBMITTING,
    RouterJobStore,
)
from router.prompt_routes import PromptRouteStore
from router.worker_pool import WorkerPool

log = logging.getLogger(__name__)


def _first_header_value(request: Request, name: str) -> str | None:
    value = request.headers.get(name)
    if not value:
        return None
    return value.split(",", 1)[0].strip() or None


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    parts = urlsplit(raw if "://" in raw else f"//{raw}")
    return parts.hostname


def _has_port(host: str) -> bool:
    try:
        return urlsplit(f"//{host}").port is not None
    except ValueError:
        return False


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().lower().strip("[]")
    if normalized in {"localhost", "0.0.0.0"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _host_with_forwarded_port(host: str, scheme: str, port: str | None) -> str:
    if not port or _has_port(host):
        return host
    if (scheme == "https" and port == "443") or (scheme == "http" and port == "80"):
        return host
    return f"{host}:{port}"


def _public_base_url(request: Request, settings: RouterSettings) -> str:
    configured = settings.public_base_url.rstrip("/")
    if not _is_loopback_host(_hostname(configured)):
        return configured

    forwarded_host = _first_header_value(request, "x-forwarded-host")
    host = forwarded_host or _first_header_value(request, "host")
    if host and not _is_loopback_host(_hostname(host)):
        scheme = _first_header_value(request, "x-forwarded-proto") or request.url.scheme
        port = _first_header_value(request, "x-forwarded-port")
        return f"{scheme}://{_host_with_forwarded_port(host, scheme, port)}".rstrip("/")

    return configured


def build_api_router(
    pool: WorkerPool,
    routes: PromptRouteStore,
    settings: RouterSettings,
    jobs: RouterJobStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    async def get_job_or_404(task_or_prompt_id: str):
        job = await jobs.get(task_or_prompt_id)
        if job is not None:
            return job
        job = await jobs.get_by_prompt(task_or_prompt_id)
        if job is not None:
            return job
        raise HTTPException(status_code=404, detail="task_id or prompt_id not found")

    @router.post("/predict_talking_video")
    async def submit(request: Request):
        max_bytes = settings.max_upload_mb * 1024 * 1024
        job = await jobs.enqueue_from_request(request, max_bytes)
        return JSONResponse(
            status_code=202,
            content={
                "status": "queued",
                "task_id": job.task_id,
                "status_url": f"/api/v1/predict_talking_video/status/{job.task_id}",
                "message": "Queued",
            },
        )

    @router.get("/predict_talking_video/status/{task_or_prompt_id}")
    async def status(task_or_prompt_id: str, request: Request):
        job = await get_job_or_404(task_or_prompt_id)
        payload = _public_status_payload(job)
        if job.status == SUCCESS and job.result:
            payload["video_url"] = (
                f"{_public_base_url(request, settings)}/api/v1/predict_talking_video/result/{job.task_id}"
            )
        return JSONResponse(content=payload)

    @router.get("/predict_talking_video/result/{task_or_prompt_id}")
    async def result(task_or_prompt_id: str):
        job = await get_job_or_404(task_or_prompt_id)
        if job.status != SUCCESS or job.result is None:
            raise HTTPException(status_code=409, detail="result not ready")
        if not job.result.path.exists():
            raise HTTPException(status_code=404, detail="result file not found")
        return FileResponse(
            job.result.path,
            media_type=job.result.content_type,
            filename=job.result.filename,
        )

    @router.get("/workers")
    async def workers():
        return {"workers": pool.snapshot(), "queue": await jobs.summary()}

    @router.get("/queue")
    async def queue():
        return {"tasks": await jobs.snapshot()}

    return router


def _public_status_payload(job) -> dict:
    public_status, message = _public_status(job.status)
    payload = {
        "status": public_status,
        "task_id": job.task_id,
        "attempts": job.attempts,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "message": message,
    }
    if job.prompt_id:
        payload["prompt_id"] = job.prompt_id
    if public_status == "success" and job.result:
        payload["result"] = {
            "filename": job.result.filename,
            "content_type": job.result.content_type,
            "size": job.result.size,
        }
    return payload


def _public_status(internal_status: str) -> tuple[str, str]:
    if internal_status == SUCCESS:
        return "success", "Completed"
    if internal_status in (SUBMITTING, PROCESSING, FETCHING_RESULT):
        return "processing", "Processing"
    if internal_status in (RECEIVING, QUEUED, ERROR):
        return "queued", "Queued"
    return "processing", "Processing"
