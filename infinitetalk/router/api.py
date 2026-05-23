"""REST endpoints for the Router."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from common import protocol as P
from common.settings import RouterSettings
from router.prompt_routes import PromptRouteStore
from router.streaming_upload import stream_upload_to_worker
from router.worker_pool import WorkerError, WorkerOffline, WorkerPool

log = logging.getLogger(__name__)


def build_api_router(
    pool: WorkerPool,
    routes: PromptRouteStore,
    settings: RouterSettings,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.post("/predict_talking_video")
    async def submit(request: Request):
        worker = await pool.reserve_idle()
        if worker is None:
            return JSONResponse(
                status_code=503,
                content={"status": "error", "message": "当前没有可用执行端，请稍后重试"},
            )

        max_bytes = settings.max_upload_mb * 1024 * 1024
        submit_session = worker.begin_submit()
        try:
            try:
                await stream_upload_to_worker(request, worker, max_bytes)
            except HTTPException:
                worker.end_submit()
                await pool.release(worker.worker_id)
                raise

            # Wait for the worker to finish ComfyUI submission.
            try:
                msg = await asyncio.wait_for(
                    submit_session.result,
                    timeout=settings.worker_rpc_timeout_seconds,
                )
            except asyncio.TimeoutError:
                return JSONResponse(
                    status_code=504,
                    content={
                        "status": "error",
                        "message": "执行端响应超时",
                    },
                )
            except WorkerOffline:
                return JSONResponse(
                    status_code=503,
                    content={"status": "error", "message": "执行端已断开"},
                )
            except WorkerError as exc:
                payload = getattr(exc, "payload", None) or {}
                return JSONResponse(
                    status_code=502,
                    content={
                        "status": "error",
                        "message": "提交到执行端或 ComfyUI 失败",
                        "detail": payload.get("detail")
                        or {"reason": str(exc)},
                    },
                )

            prompt_id = msg.get("prompt_id")
            if not prompt_id:
                return JSONResponse(
                    status_code=502,
                    content={
                        "status": "error",
                        "message": "执行端未返回 prompt_id",
                    },
                )

            await routes.put(prompt_id, worker.worker_id)
            return JSONResponse(
                status_code=200,
                content={
                    "status": "submitted",
                    "prompt_id": prompt_id,
                    "status_url": f"/api/v1/predict_talking_video/status/{prompt_id}",
                },
            )
        finally:
            worker.end_submit()
            await pool.release(worker.worker_id)

    @router.get("/predict_talking_video/status/{prompt_id}")
    async def status(prompt_id: str):
        route = await routes.get(prompt_id)
        if route is None:
            raise HTTPException(status_code=404, detail="prompt_id not found")
        worker = pool.get(route.worker_id)
        if worker is None or worker.status == "offline":
            raise HTTPException(status_code=503, detail="worker offline")

        rpc_id, fut = worker.new_rpc_future()
        try:
            await worker.send_json({
                "type": P.STATUS_QUERY,
                "rpc_id": rpc_id,
                "prompt_id": prompt_id,
            })
            try:
                msg = await asyncio.wait_for(fut, timeout=settings.worker_rpc_timeout_seconds)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="worker rpc timeout")
            except WorkerOffline:
                raise HTTPException(status_code=503, detail="worker offline")
        finally:
            worker.drop_rpc(rpc_id)

        # Strip rpc fields before returning to the client.
        payload = {k: v for k, v in msg.items() if k not in ("type", "rpc_id")}
        payload.setdefault("prompt_id", prompt_id)
        if payload.get("status") == "success":
            payload["video_url"] = (
                f"{settings.public_base_url.rstrip('/')}/api/v1/predict_talking_video/result/{prompt_id}"
            )
        return JSONResponse(content=payload)

    @router.get("/predict_talking_video/result/{prompt_id}")
    async def result(prompt_id: str):
        route = await routes.get(prompt_id)
        if route is None:
            raise HTTPException(status_code=404, detail="prompt_id not found")
        worker = pool.get(route.worker_id)
        if worker is None or worker.status == "offline":
            raise HTTPException(status_code=503, detail="worker offline")

        rpc_id, stream = worker.new_result_stream()
        try:
            await worker.send_json({
                "type": P.RESULT_OPEN,
                "rpc_id": rpc_id,
                "prompt_id": prompt_id,
            })
            try:
                meta = await asyncio.wait_for(
                    stream.meta_future, timeout=settings.worker_rpc_timeout_seconds
                )
            except asyncio.TimeoutError:
                worker.drop_result_stream(rpc_id)
                raise HTTPException(status_code=504, detail="worker rpc timeout")
            except WorkerError as exc:
                worker.drop_result_stream(rpc_id)
                raise HTTPException(status_code=502, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            worker.drop_result_stream(rpc_id)
            raise HTTPException(status_code=502, detail=str(exc))

        filename = meta.get("filename") or f"{prompt_id}.mp4"
        content_type = meta.get("content_type") or "application/octet-stream"
        size = meta.get("size")

        async def body_iter():
            try:
                while True:
                    kind, data = await stream.queue.get()
                    if kind == "chunk":
                        yield data
                    elif kind == "end":
                        return
                    elif kind == "error":
                        log.warning("result stream error %s: %s", prompt_id, data)
                        return
            finally:
                worker.drop_result_stream(rpc_id)

        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if size is not None:
            headers["Content-Length"] = str(size)
        return StreamingResponse(body_iter(), media_type=content_type, headers=headers)

    @router.get("/workers")
    async def workers():
        return {"workers": pool.snapshot()}

    return router
