"""Router entry point."""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from common.settings import RouterSettings
from router.api import build_api_router
from router.prompt_routes import PromptRouteStore
from router.worker_pool import WorkerPool
from router.ws import build_ws_router


def create_app(settings: RouterSettings | None = None) -> FastAPI:
    settings = settings or RouterSettings.from_env()
    app = FastAPI(title="InfiniteTalk Router")

    pool = WorkerPool()
    routes = PromptRouteStore(ttl_seconds=settings.prompt_route_ttl_hours * 3600.0)

    app.state.settings = settings
    app.state.worker_pool = pool
    app.state.prompt_routes = routes

    app.include_router(build_ws_router(pool, settings.worker_token))
    app.include_router(build_api_router(pool, routes, settings))

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "workers": pool.snapshot()}

    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = RouterSettings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
