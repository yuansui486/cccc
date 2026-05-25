"""Worker WebSocket client: receives upload streams, drives ComfyUI."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from common import protocol as P
from common.settings import WorkerSettings
from worker.comfyui import ComfyUIClient, ComfyUIError
from worker.workflow import Workflow, WorkflowConfig, WorkflowError

log = logging.getLogger(__name__)


@dataclass
class _UploadState:
    submit_id: str
    dir: Path
    params: dict[str, Any] = field(default_factory=dict)
    files: dict[str, dict] = field(default_factory=dict)  # file_id -> {path, fh, filename, content_type, size}
    pending_binary_file_id: Optional[str] = None
    cancelled: bool = False


class _Sender:
    """Serializes writes to the WebSocket so metadata and binary chunks stay paired."""

    def __init__(self, ws):
        self._ws = ws
        self._lock = asyncio.Lock()

    async def send_json(self, msg: dict) -> None:
        async with self._lock:
            await self._ws.send(json.dumps(msg, ensure_ascii=False))

    async def send_chunk(self, meta: dict, payload: bytes) -> None:
        async with self._lock:
            await self._ws.send(json.dumps(meta, ensure_ascii=False))
            await self._ws.send(payload)


class WorkerApp:
    def __init__(self, settings: WorkerSettings):
        self.settings = settings
        self.workflow = Workflow(WorkflowConfig(
            workflow_file=Path(settings.workflow_file),
            image_node_id=settings.image_node_id,
            audio_node_id=settings.audio_node_id,
            video_node_id=settings.video_node_id,
            width_node_id=settings.width_node_id,
            height_node_id=settings.height_node_id,
        ))
        self.comfy = ComfyUIClient(settings.comfyui_base_url)
        self._tmp_root = Path(settings.worker_tmp_dir)
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        self._upload: Optional[_UploadState] = None
        self._sender: Optional[_Sender] = None
        self._bg_tasks: set[asyncio.Task] = set()

    # ------------- main loop ------------------------------------------
    async def run_forever(self) -> None:
        while True:
            try:
                await self._connect_and_serve()
            except Exception:
                log.exception("worker session crashed")
            log.info("reconnecting in %.1fs", self.settings.reconnect_seconds)
            await asyncio.sleep(self.settings.reconnect_seconds)

    async def _connect_and_serve(self) -> None:
        qs = urlencode({"worker_id": self.settings.worker_id, "token": self.settings.worker_token})
        url = f"{self.settings.router_ws_url}?{qs}"
        log.info("connecting to %s", url)
        async with websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=20) as ws:
            self._sender = _Sender(ws)
            await self._sender.send_json({
                "type": P.WORKER_REGISTER,
                "worker_id": self.settings.worker_id,
                "capabilities": {"kind": "infinitetalk"},
            })
            # Expect worker.registered
            ack_raw = await ws.recv()
            try:
                ack = json.loads(ack_raw) if isinstance(ack_raw, str) else {}
            except Exception:
                ack = {}
            if ack.get("type") != P.WORKER_REGISTERED:
                log.warning("unexpected register ack: %s", ack)
                return
            log.info("registered as %s", self.settings.worker_id)

            hb_task = asyncio.create_task(self._heartbeat_loop())
            try:
                async for raw in ws:
                    if isinstance(raw, str):
                        await self._on_text(raw)
                    else:
                        await self._on_binary(bytes(raw))
            except ConnectionClosed:
                pass
            finally:
                hb_task.cancel()
                for t in list(self._bg_tasks):
                    t.cancel()
                self._bg_tasks.clear()
                self._cleanup_upload()
                self._sender = None

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.heartbeat_seconds)
                if self._sender is None:
                    return
                try:
                    await self._sender.send_json({"type": P.HEARTBEAT})
                except Exception:
                    return
        except asyncio.CancelledError:
            return

    # ------------- message handlers -----------------------------------
    async def _on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            log.warning("invalid json from router: %r", raw[:200])
            return
        t = msg.get("type")
        if t == P.HEARTBEAT_ACK:
            return
        if t == P.SUBMIT_START:
            await self._handle_submit_start()
            return
        if t == P.SUBMIT_PARAMS:
            self._handle_submit_params(msg)
            return
        if t == P.FILE_START:
            self._handle_file_start(msg)
            return
        if t == P.FILE_CHUNK:
            # next binary frame goes to msg.file_id
            if self._upload is not None:
                self._upload.pending_binary_file_id = msg.get("file_id")
            return
        if t == P.FILE_END:
            self._handle_file_end(msg)
            return
        if t == P.SUBMIT_COMPLETE:
            self._spawn(self._handle_submit_complete())
            return
        if t == P.SUBMIT_CANCEL:
            await self._handle_submit_cancel(msg)
            return
        if t == P.STATUS_QUERY:
            self._spawn(self._handle_status_query(msg))
            return
        if t == P.RESULT_OPEN:
            self._spawn(self._handle_result_open(msg))
            return
        log.debug("unhandled message type %s", t)

    async def _on_binary(self, data: bytes) -> None:
        if self._upload is None or self._upload.pending_binary_file_id is None:
            log.warning("dropping binary frame without prior file.chunk metadata")
            return
        file_id = self._upload.pending_binary_file_id
        self._upload.pending_binary_file_id = None
        entry = self._upload.files.get(file_id)
        if entry is None or entry.get("fh") is None:
            log.warning("no open file handle for file_id=%s", file_id)
            return
        entry["fh"].write(data)
        entry["size"] = entry.get("size", 0) + len(data)

    # ------------- submit flow ----------------------------------------
    async def _handle_submit_start(self) -> None:
        self._cleanup_upload()
        submit_id = uuid.uuid4().hex[:12]
        d = self._tmp_root / submit_id
        d.mkdir(parents=True, exist_ok=True)
        self._upload = _UploadState(submit_id=submit_id, dir=d)
        if self._sender:
            await self._sender.send_json({"type": P.SUBMIT_ACCEPTED})

    def _handle_submit_params(self, msg: dict) -> None:
        if self._upload is None:
            return
        params = msg.get("params") or {}
        self._upload.params.update(params)

    def _handle_file_start(self, msg: dict) -> None:
        if self._upload is None:
            return
        file_id = msg.get("file_id")
        if not isinstance(file_id, str):
            return
        filename = msg.get("filename") or f"{file_id}.bin"
        content_type = msg.get("content_type") or "application/octet-stream"
        safe_name = self._safe_filename(filename, fallback=f"{file_id}.bin")
        path = self._upload.dir / safe_name
        fh = open(path, "wb")
        self._upload.files[file_id] = {
            "path": path,
            "fh": fh,
            "filename": safe_name,
            "content_type": content_type,
            "size": 0,
        }

    def _handle_file_end(self, msg: dict) -> None:
        if self._upload is None:
            return
        file_id = msg.get("file_id")
        if not isinstance(file_id, str):
            return
        entry = self._upload.files.get(file_id)
        if entry and entry.get("fh"):
            entry["fh"].close()
            entry["fh"] = None

    async def _handle_submit_cancel(self, msg: dict) -> None:
        if self._upload:
            self._upload.cancelled = True
        self._cleanup_upload()

    async def _handle_submit_complete(self) -> None:
        upload = self._upload
        sender = self._sender
        if upload is None or sender is None:
            return
        try:
            if upload.cancelled:
                raise RuntimeError("upload cancelled")
            image_entry = upload.files.get(P.FILE_ID_IMAGE)
            audio_entry = upload.files.get(P.FILE_ID_AUDIO)
            if not image_entry or not audio_entry:
                raise RuntimeError("missing image or audio file")
            width = int(upload.params.get("width", 480))
            height = int(upload.params.get("height", 640))

            image_bytes = Path(image_entry["path"]).read_bytes()
            audio_bytes = Path(audio_entry["path"]).read_bytes()

            comfy_image = await self.comfy.upload_image(
                image_bytes, image_entry["filename"], image_entry["content_type"]
            )
            comfy_audio = await self.comfy.upload_image(
                audio_bytes, audio_entry["filename"], audio_entry["content_type"]
            )
            prompt_json = self.workflow.build(comfy_image, comfy_audio, width, height)
            prompt_id = await self.comfy.queue_prompt(prompt_json)

            await sender.send_json({
                "type": P.SUBMIT_RESULT,
                "status": "submitted",
                "prompt_id": prompt_id,
            })
        except (ComfyUIError, WorkflowError) as exc:
            await sender.send_json({
                "type": P.SUBMIT_ERROR,
                "message": str(exc),
                "detail": {"stage": "queue_prompt", "reason": str(exc)},
            })
        except Exception as exc:
            log.exception("submit failed")
            await sender.send_json({
                "type": P.SUBMIT_ERROR,
                "message": str(exc),
                "detail": {"stage": "submit", "reason": str(exc)},
            })
        finally:
            self._cleanup_upload()

    # ------------- status query ---------------------------------------
    async def _handle_status_query(self, msg: dict) -> None:
        rpc_id = msg.get("rpc_id")
        prompt_id = msg.get("prompt_id")
        sender = self._sender
        if sender is None or not rpc_id or not prompt_id:
            return
        try:
            payload = await self._query_status(prompt_id)
        except Exception as exc:
            log.exception("status query failed")
            payload = {
                "status": "error",
                "message": f"Failed to query ComfyUI task status: {exc}",
            }
        payload["type"] = P.STATUS_RESULT
        payload["rpc_id"] = rpc_id
        payload["prompt_id"] = prompt_id
        await sender.send_json(payload)

    async def _query_status(self, prompt_id: str) -> dict:
        history = await self.comfy.history(prompt_id)
        if prompt_id not in history:
            return {
                "status": "processing",
                "message": "Task is still queued or processing",
            }
        task_result = history[prompt_id]
        outputs = task_result.get("outputs", {})
        video_node_output = outputs.get(self.settings.video_node_id, {})
        video_outputs = (
            video_node_output.get("gifs")
            or video_node_output.get("videos")
            or []
        )
        if video_outputs:
            return {"status": "success"}
        task_status = task_result.get("status", {})
        status_text = task_status.get("status_str")
        if status_text and status_text != "success":
            return {
                "status": "error",
                "message": f"Unexpected ComfyUI task status: {status_text}",
                "detail": task_status,
            }
        return {
            "status": "error",
            "message": "Task completed but no output file was found in the video node.",
        }

    # ------------- result download ------------------------------------
    async def _handle_result_open(self, msg: dict) -> None:
        rpc_id = msg.get("rpc_id")
        prompt_id = msg.get("prompt_id")
        sender = self._sender
        if sender is None or not rpc_id or not prompt_id:
            return
        try:
            history = await self.comfy.history(prompt_id)
            if prompt_id not in history:
                raise ComfyUIError("prompt_id not in history")
            outputs = history[prompt_id].get("outputs", {})
            video_node_output = outputs.get(self.settings.video_node_id, {})
            video_outputs = (
                video_node_output.get("gifs")
                or video_node_output.get("videos")
                or []
            )
            if not video_outputs:
                raise ComfyUIError("no video output found")
            view_url = self.comfy.build_view_url(video_outputs[0])
            stream = await self.comfy.open_view(view_url)
            async with stream:
                await sender.send_json({
                    "type": P.RESULT_START,
                    "rpc_id": rpc_id,
                    "prompt_id": prompt_id,
                    "filename": video_outputs[0].get("filename") or f"{prompt_id}.mp4",
                    "content_type": stream.content_type,
                    "size": stream.content_length,
                })
                async for chunk in stream.aiter_bytes(chunk_size=262144):
                    await sender.send_chunk(
                        {
                            "type": P.RESULT_CHUNK,
                            "rpc_id": rpc_id,
                            "size": len(chunk),
                        },
                        chunk,
                    )
                await sender.send_json({
                    "type": P.RESULT_END,
                    "rpc_id": rpc_id,
                    "prompt_id": prompt_id,
                })
        except Exception as exc:
            log.exception("result open failed")
            try:
                await sender.send_json({
                    "type": P.RESULT_ERROR,
                    "rpc_id": rpc_id,
                    "prompt_id": prompt_id,
                    "message": str(exc),
                })
            except Exception:
                pass

    # ------------- helpers --------------------------------------------
    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    def _cleanup_upload(self) -> None:
        upload = self._upload
        self._upload = None
        if upload is None:
            return
        for entry in upload.files.values():
            fh = entry.get("fh")
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
        try:
            shutil.rmtree(upload.dir, ignore_errors=True)
        except Exception:
            pass

    @staticmethod
    def _safe_filename(name: str, fallback: str) -> str:
        base = os.path.basename(name or "")
        base = base.replace("\\", "").replace("/", "").strip()
        if not base or base in (".", ".."):
            return fallback
        # Keep simple ASCII-safe characters.
        cleaned = "".join(c for c in base if c.isalnum() or c in "._-")
        return cleaned or fallback
