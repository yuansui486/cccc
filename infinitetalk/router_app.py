"""Router entry point."""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI

from common.settings import RouterSettings
from router.api import build_api_router
from router.database import RouterDatabase
from router.job_queue import RouterJobDispatcher, RouterJobStore
from router.prompt_routes import PromptRouteStore
from router.worker_pool import WorkerPool
from router.ws import build_ws_router


def create_app(settings: RouterSettings | None = None) -> FastAPI:
    settings = settings or RouterSettings.from_env()
    app = FastAPI(title="InfiniteTalk Router")

    database = (
        RouterDatabase(settings.database_url, table_prefix=settings.database_table_prefix)
        if settings.database_url
        else None
    )
    pool = WorkerPool(database=database)
    routes = PromptRouteStore(
        ttl_seconds=settings.prompt_route_ttl_hours * 3600.0,
        database=database,
    )
    jobs = RouterJobStore(
        queue_dir=settings.router_queue_dir,
        result_dir=settings.router_result_dir,
        max_jobs=settings.router_queue_max_jobs,
        finished_ttl_seconds=settings.prompt_route_ttl_hours * 3600.0,
        database=database,
    )
    dispatcher = RouterJobDispatcher(jobs, pool, routes, settings)

    app.state.settings = settings
    app.state.database = database
    app.state.worker_pool = pool
    app.state.prompt_routes = routes
    app.state.job_store = jobs
    app.state.job_dispatcher = dispatcher

    app.include_router(build_ws_router(pool, settings.worker_token))
    app.include_router(build_api_router(pool, routes, settings, jobs))

    @app.on_event("startup")
    async def start_dispatcher():
        if database is not None:
            await database.connect()
            await database.ensure_schema()
            await pool.mark_all_offline()
            await jobs.prepare()
            await routes.gc()
        dispatcher.start()

    @app.on_event("shutdown")
    async def stop_dispatcher():
        await dispatcher.stop()
        if database is not None:
            await pool.mark_all_offline()
            await database.close()

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "workers": await pool.snapshot(), "queue": await jobs.summary()}

    return app


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    settings = RouterSettings.from_env()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
