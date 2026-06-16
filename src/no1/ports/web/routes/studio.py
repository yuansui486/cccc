from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from ....paths import ensure_studio_dirs
from ..schemas import RouteContext, require_user


def _studio_paths_payload(ctx: RouteContext) -> Dict[str, Any]:
    paths = ensure_studio_dirs(ctx.home)
    return {
        "home_path": str(paths["home"]),
        "studio_path": str(paths["studio"]),
        "asstes_path": str(paths["asstes"]),
        "drafts_path": str(paths["drafts"]),
    }


def create_routers(ctx: RouteContext) -> list[APIRouter]:
    router = APIRouter(prefix="/api/v1/studio", dependencies=[Depends(require_user)])

    @router.get("/paths")
    async def studio_paths() -> Dict[str, Any]:
        return {"ok": True, "result": _studio_paths_payload(ctx)}

    return [router]
