"""WebSocket endpoint that accepts Worker connections."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from common import protocol as P
from router.worker_pool import WorkerConnection, WorkerPool

log = logging.getLogger(__name__)


def build_ws_router(pool: WorkerPool, expected_token: str) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws/workers")
    async def ws_workers(
        websocket: WebSocket,
        worker_id: str = Query(...),
        token: str = Query(...),
    ):
        if token != expected_token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()

        # Expect a worker.register message first.
        try:
            first = await websocket.receive_text()
            msg = json.loads(first)
            if msg.get("type") != P.WORKER_REGISTER or msg.get("worker_id") != worker_id:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
        except (WebSocketDisconnect, ValueError, KeyError):
            return

        conn = WorkerConnection(worker_id=worker_id, ws=websocket, capabilities=msg.get("capabilities") or {})
        await pool.add(conn)
        try:
            await websocket.send_text(json.dumps({"type": P.WORKER_REGISTERED, "worker_id": worker_id}))
            log.info("worker %s connected", worker_id)
            while True:
                event = await websocket.receive()
                if event.get("type") == "websocket.disconnect":
                    break
                text = event.get("text")
                if text is not None:
                    await conn.handle_text(text)
                    continue
                data = event.get("bytes")
                if data is not None:
                    await conn.handle_binary(data)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("worker %s ws loop crashed", worker_id)
        finally:
            log.info("worker %s disconnected", worker_id)
            await pool.remove(worker_id, conn)

    return router
