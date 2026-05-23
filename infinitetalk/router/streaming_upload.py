"""Streaming multipart upload -> WebSocket forwarder.

Reads the HTTP request body in chunks, parses multipart on the fly with
streaming-form-data, and forwards file chunks to the worker over WebSocket
without ever buffering full files on disk or in memory.
"""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from typing import Any, Deque, Optional

from fastapi import HTTPException, Request
from streaming_form_data import StreamingFormDataParser
from streaming_form_data.targets import BaseTarget, ValueTarget

from common import protocol as P
from router.worker_pool import WorkerConnection, WorkerError

log = logging.getLogger(__name__)


class UploadAborted(Exception):
    pass


class _EventTarget(BaseTarget):
    def __init__(self, file_id: str, events: Deque[tuple]):
        super().__init__()
        self.file_id = file_id
        self._events = events
        self._size = 0
        self._hash = hashlib.sha256()

    def on_start(self) -> None:
        self._events.append((
            "file_start",
            self.file_id,
            getattr(self, "multipart_filename", None),
            getattr(self, "multipart_content_type", None),
        ))

    def on_data_received(self, chunk: bytes) -> None:
        self._size += len(chunk)
        self._hash.update(chunk)
        self._events.append(("file_chunk", self.file_id, chunk))

    def on_finish(self) -> None:
        self._events.append(("file_end", self.file_id, self._size, self._hash.hexdigest()))


class _ParamTarget(ValueTarget):
    def __init__(self, name: str, events: Deque[tuple]):
        super().__init__()
        self._name = name
        self._events = events

    def on_finish(self) -> None:
        super().on_finish()
        try:
            value = bytes(self.value).decode("utf-8")
        except UnicodeDecodeError:
            value = ""
        self._events.append(("param", self._name, value))


async def stream_upload_to_worker(
    request: Request,
    worker: WorkerConnection,
    max_upload_bytes: int,
) -> None:
    """Parse the multipart body of ``request`` and forward chunks to ``worker``.

    Raises HTTPException for client-visible failures and WorkerError if the
    worker connection breaks.
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        raise HTTPException(status_code=400, detail="expected multipart/form-data")

    events: Deque[tuple] = deque()
    parser = StreamingFormDataParser(headers={"Content-Type": content_type})

    image_target = _EventTarget(P.FILE_ID_IMAGE, events)
    audio_target = _EventTarget(P.FILE_ID_AUDIO, events)
    width_target = _ParamTarget("width", events)
    height_target = _ParamTarget("height", events)

    parser.register("image", image_target)
    parser.register("audio", audio_target)
    parser.register("width", width_target)
    parser.register("height", height_target)

    params: dict[str, Any] = {}
    params_sent = False
    open_files: set[str] = set()
    total_bytes = 0

    await worker.send_json({"type": P.SUBMIT_START})
    # Wait briefly for accept? The worker should respond fast; we won't block
    # the upload stream on it -- accepted future is consulted later. The
    # protocol allows the worker to start receiving file frames immediately.

    async def flush_events() -> None:
        nonlocal params_sent
        while events:
            ev = events.popleft()
            kind = ev[0]
            if kind == "param":
                _, name, value = ev
                try:
                    params[name] = int(value)
                except (TypeError, ValueError):
                    params[name] = value
                # If files are not opened yet, defer; otherwise send update.
                if params_sent and open_files:
                    await worker.send_json({"type": P.SUBMIT_PARAMS, "params": params})
            elif kind == "file_start":
                if not params_sent:
                    await worker.send_json({"type": P.SUBMIT_PARAMS, "params": params})
                    params_sent = True
                _, file_id, filename, ctype = ev
                open_files.add(file_id)
                await worker.send_json({
                    "type": P.FILE_START,
                    "file_id": file_id,
                    "filename": filename,
                    "content_type": ctype,
                })
            elif kind == "file_chunk":
                _, file_id, chunk = ev
                await worker.send_chunk(
                    {
                        "type": P.FILE_CHUNK,
                        "file_id": file_id,
                        "size": len(chunk),
                    },
                    chunk,
                )
            elif kind == "file_end":
                _, file_id, size, sha = ev
                open_files.discard(file_id)
                await worker.send_json({
                    "type": P.FILE_END,
                    "file_id": file_id,
                    "size": size,
                    "sha256": sha,
                })

    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            total_bytes += len(chunk)
            if total_bytes > max_upload_bytes:
                # Try to tell the worker to discard whatever it received.
                try:
                    await worker.send_json({"type": P.SUBMIT_CANCEL, "reason": "upload_too_large"})
                except Exception:
                    pass
                raise HTTPException(status_code=413, detail="upload too large")
            parser.data_received(chunk)
            await flush_events()

        if not params_sent:
            await worker.send_json({"type": P.SUBMIT_PARAMS, "params": params})
            params_sent = True
        await flush_events()
        await worker.send_json({"type": P.SUBMIT_COMPLETE})
    except HTTPException:
        raise
    except WorkerError:
        raise
    except Exception as exc:
        log.exception("upload streaming failed")
        try:
            await worker.send_json({"type": P.SUBMIT_CANCEL, "reason": "upload_error"})
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"upload failed: {exc}") from exc
