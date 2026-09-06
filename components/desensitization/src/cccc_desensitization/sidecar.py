from __future__ import annotations
import os
import hmac
import threading
import time
import asyncio
import logging
import psutil
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from . import service
from .routes import create_routers

app = FastAPI(title="CCCC Desensitization Sidecar")
logger = logging.getLogger("cccc_desensitization.sidecar")
_startup_task: asyncio.Task[None] | None = None

@app.exception_handler(HTTPException)
async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "desensitization_error", "message": str(exc.detail), "details": {}}
    if "code" not in detail:
        detail = {"code": "desensitization_error", "message": str(detail.get("message") or detail), "details": detail}
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": detail})

def _watch_parent() -> None:
    try:
        parent = int(os.environ.get("CCCC_DESENSITIZATION_PARENT_PID", "0") or 0)
    except (TypeError, ValueError):
        parent = 0
    if not parent:
        return
    try:
        expected_create_time = 0.0
        try:
            expected_create_time = float(os.environ.get("CCCC_DESENSITIZATION_PARENT_CREATE_TIME", "0") or 0)
        except (TypeError, ValueError):
            expected_create_time = 0.0
        parent_process = psutil.Process(parent)
        if expected_create_time:
            try:
                if abs(parent_process.create_time() - expected_create_time) > 2.0:
                    os._exit(0)
            except (psutil.Error, OSError):
                os._exit(0)
        while True:
            try:
                if not parent_process.is_running() or parent_process.status() == psutil.STATUS_ZOMBIE:
                    break
                if expected_create_time and abs(parent_process.create_time() - expected_create_time) > 2.0:
                    break
            except (psutil.Error, OSError):
                break
            time.sleep(1)
    except (ImportError, psutil.Error, OSError, ValueError):
        # A configured parent cannot be verified. Exit rather than leaving an
        # orphaned service behind after a failed host startup.
        os._exit(0)
    os._exit(0)

if os.environ.get("CCCC_DESENSITIZATION_PARENT_PID"):
    threading.Thread(target=_watch_parent, name="cccc-desensitization-parent-watch", daemon=True).start()

def _auth(authorization: str | None) -> None:
    token = os.environ.get("CCCC_DESENSITIZATION_TOKEN", "").strip()
    expected = f"Bearer {token}" if token else ""
    if not token or not authorization or not hmac.compare_digest(str(authorization), expected):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_sidecar_token",
                "message": "invalid sidecar token",
                "details": {},
            },
        )


async def _initialize_models_in_background() -> None:
    try:
        # ``initialize_models`` creates/returns a job and performs the actual
        # download and native predictor construction on its own worker.  Keep
        # this call off the Uvicorn event loop so health checks are immediate.
        await asyncio.to_thread(service.initialize_models)
    except Exception:
        logger.exception("background model initialization failed")


@app.on_event("startup")
async def startup() -> None:
    global _startup_task
    enabled = os.environ.get("LOCAL_DESENSITIZATION_AUTO_INIT", "1").lower() not in {"0", "false", "no"}
    test_mode = os.environ.get("LOCAL_DESENSITIZATION_TEST_MODE", "").lower() in {"1", "true", "yes"}
    # The manager always supplies a port.  Requiring it keeps importing the
    # ASGI app in unit tests and tooling side-effect free.
    if enabled and not test_mode and os.environ.get("CCCC_DESENSITIZATION_PORT"):
        _startup_task = asyncio.create_task(_initialize_models_in_background())

@app.get("/internal/healthz")
async def healthz() -> dict[str, bool]:
    return {"ok": True}

@app.get("/internal/status")
async def status(authorization: str | None = Header(default=None)):
    _auth(authorization)
    return {"ok": True, "result": service.model_status()}

for router in create_routers():
    app.include_router(router)

def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CCCC_DESENSITIZATION_PORT", "0")))
