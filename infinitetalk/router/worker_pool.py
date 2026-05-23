"""Worker connection pool and per-worker RPC plumbing for the Router."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from fastapi import WebSocket

from common import protocol as P

log = logging.getLogger(__name__)


class WorkerError(Exception):
    pass


class WorkerOffline(WorkerError):
    pass


class WorkerBusy(WorkerError):
    pass


@dataclass
class ResultStream:
    meta_future: asyncio.Future
    queue: asyncio.Queue  # items: ("chunk", bytes) | ("end", None) | ("error", dict)


@dataclass
class SubmitSession:
    accepted: asyncio.Future
    result: asyncio.Future  # resolves with submit.result payload or rejects with WorkerError


class WorkerConnection:
    def __init__(self, worker_id: str, ws: WebSocket, capabilities: dict | None = None):
        self.worker_id = worker_id
        self.ws = ws
        self.capabilities = capabilities or {}
        self.status: str = "idle"  # idle | busy | offline
        self._send_lock = asyncio.Lock()
        self._rpc_futures: Dict[str, asyncio.Future] = {}
        self._result_streams: Dict[str, ResultStream] = {}
        self._submit: Optional[SubmitSession] = None
        # When binary frames arrive, they belong to the rpc_id of the last
        # *.chunk metadata message we received from this worker.
        self._pending_binary_rpc: Optional[str] = None
        self._closed = False

    # --- low level send helpers ---------------------------------------
    async def send_json(self, msg: dict) -> None:
        if self._closed:
            raise WorkerOffline(f"worker {self.worker_id} disconnected")
        async with self._send_lock:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))

    async def send_chunk(self, meta: dict, payload: bytes) -> None:
        """Send a metadata frame immediately followed by a binary frame."""
        if self._closed:
            raise WorkerOffline(f"worker {self.worker_id} disconnected")
        async with self._send_lock:
            await self.ws.send_text(json.dumps(meta, ensure_ascii=False))
            await self.ws.send_bytes(payload)

    # --- submit lifecycle ---------------------------------------------
    def begin_submit(self) -> SubmitSession:
        if self._submit is not None and not self._submit.result.done():
            raise WorkerBusy(f"worker {self.worker_id} already has an active submit")
        loop = asyncio.get_event_loop()
        self._submit = SubmitSession(
            accepted=loop.create_future(),
            result=loop.create_future(),
        )
        return self._submit

    def end_submit(self) -> None:
        self._submit = None

    # --- json RPC (status query) --------------------------------------
    def new_rpc_future(self) -> tuple[str, asyncio.Future]:
        rpc_id = "rpc_" + uuid.uuid4().hex[:12]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._rpc_futures[rpc_id] = fut
        return rpc_id, fut

    def drop_rpc(self, rpc_id: str) -> None:
        self._rpc_futures.pop(rpc_id, None)

    # --- result streams (download) ------------------------------------
    def new_result_stream(self) -> tuple[str, ResultStream]:
        rpc_id = "rpc_" + uuid.uuid4().hex[:12]
        loop = asyncio.get_event_loop()
        stream = ResultStream(
            meta_future=loop.create_future(),
            queue=asyncio.Queue(maxsize=64),
        )
        self._result_streams[rpc_id] = stream
        return rpc_id, stream

    def drop_result_stream(self, rpc_id: str) -> None:
        self._result_streams.pop(rpc_id, None)

    # --- incoming message dispatch ------------------------------------
    async def handle_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            log.warning("worker %s sent invalid json: %r", self.worker_id, raw[:200])
            return
        t = msg.get("type")
        if t == P.HEARTBEAT:
            await self.send_json({"type": P.HEARTBEAT_ACK})
            return
        if t == P.SUBMIT_ACCEPTED:
            if self._submit and not self._submit.accepted.done():
                self._submit.accepted.set_result(msg)
            return
        if t == P.SUBMIT_RESULT:
            if self._submit and not self._submit.result.done():
                self._submit.result.set_result(msg)
            return
        if t == P.SUBMIT_ERROR:
            if self._submit and not self._submit.result.done():
                self._submit.result.set_exception(WorkerError(msg.get("message") or "submit error"))
                # attach the full payload on the exception for upstream use
                self._submit.result.exception().payload = msg  # type: ignore[attr-defined]
            return
        if t == P.STATUS_RESULT:
            rpc_id = msg.get("rpc_id")
            fut = self._rpc_futures.pop(rpc_id, None) if rpc_id else None
            if fut and not fut.done():
                fut.set_result(msg)
            return
        if t == P.RESULT_START:
            rpc_id = msg.get("rpc_id")
            stream = self._result_streams.get(rpc_id) if rpc_id else None
            if stream and not stream.meta_future.done():
                stream.meta_future.set_result(msg)
            return
        if t == P.RESULT_CHUNK:
            # next binary frame belongs to this rpc_id
            self._pending_binary_rpc = msg.get("rpc_id")
            return
        if t == P.RESULT_END:
            rpc_id = msg.get("rpc_id")
            stream = self._result_streams.get(rpc_id) if rpc_id else None
            if stream:
                await stream.queue.put(("end", None))
            return
        if t == P.RESULT_ERROR:
            rpc_id = msg.get("rpc_id")
            stream = self._result_streams.get(rpc_id) if rpc_id else None
            if stream:
                if not stream.meta_future.done():
                    stream.meta_future.set_exception(WorkerError(msg.get("message") or "result error"))
                await stream.queue.put(("error", msg))
            return
        log.debug("worker %s sent unhandled message type %s", self.worker_id, t)

    async def handle_binary(self, payload: bytes) -> None:
        rpc_id = self._pending_binary_rpc
        self._pending_binary_rpc = None
        if not rpc_id:
            log.warning("worker %s sent binary frame without metadata", self.worker_id)
            return
        stream = self._result_streams.get(rpc_id)
        if stream:
            await stream.queue.put(("chunk", payload))

    # --- shutdown ------------------------------------------------------
    def mark_offline(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.status = "offline"
        err = WorkerOffline(f"worker {self.worker_id} disconnected")
        if self._submit:
            for fut in (self._submit.accepted, self._submit.result):
                if not fut.done():
                    fut.set_exception(err)
        for fut in self._rpc_futures.values():
            if not fut.done():
                fut.set_exception(err)
        self._rpc_futures.clear()
        for stream in self._result_streams.values():
            if not stream.meta_future.done():
                stream.meta_future.set_exception(err)
            try:
                stream.queue.put_nowait(("error", {"message": str(err)}))
            except asyncio.QueueFull:
                pass
        self._result_streams.clear()


class WorkerPool:
    def __init__(self) -> None:
        self._workers: Dict[str, WorkerConnection] = {}
        self._lock = asyncio.Lock()

    async def add(self, worker: WorkerConnection) -> None:
        async with self._lock:
            # If an old connection with the same id exists, evict it.
            old = self._workers.get(worker.worker_id)
            if old is not None and old is not worker:
                old.mark_offline()
            self._workers[worker.worker_id] = worker

    async def remove(self, worker_id: str, conn: WorkerConnection) -> None:
        async with self._lock:
            current = self._workers.get(worker_id)
            if current is conn:
                self._workers.pop(worker_id, None)
        conn.mark_offline()

    def get(self, worker_id: str) -> Optional[WorkerConnection]:
        return self._workers.get(worker_id)

    async def reserve_idle(self) -> Optional[WorkerConnection]:
        """Atomically pick an idle worker and mark it busy."""
        async with self._lock:
            for w in self._workers.values():
                if w.status == "idle":
                    w.status = "busy"
                    return w
            return None

    async def release(self, worker_id: str) -> None:
        async with self._lock:
            w = self._workers.get(worker_id)
            if w and w.status != "offline":
                w.status = "idle"

    def snapshot(self) -> list[dict]:
        return [
            {"worker_id": w.worker_id, "status": w.status, "capabilities": w.capabilities}
            for w in self._workers.values()
        ]
