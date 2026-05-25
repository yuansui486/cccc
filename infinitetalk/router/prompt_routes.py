"""In-memory prompt_id -> worker_id map with TTL."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

from router.database import RouterDatabase, seconds_to_ms, unix_ms


@dataclass
class PromptRoute:
    prompt_id: str
    worker_id: str
    created_at: int
    last_accessed_at: int


class PromptRouteStore:
    def __init__(self, ttl_seconds: float, database: RouterDatabase | None = None) -> None:
        self._ttl_ms = seconds_to_ms(ttl_seconds)
        self._database = database
        self._routes: dict[str, PromptRoute] = {}
        self._lock = asyncio.Lock()

    async def put(self, prompt_id: str, worker_id: str, task_id: str | None = None) -> None:
        now = unix_ms()
        if self._database is not None:
            routes_table = self._database.table("prompt_routes")
            async with self._database.pool.acquire() as connection:
                await connection.execute(
                    f"""
                    INSERT INTO {routes_table}
                        (prompt_id, task_id, worker_id, created_at, last_accessed_at)
                    VALUES ($1, $2, $3, $4, $4)
                    ON CONFLICT (prompt_id) DO UPDATE SET
                        task_id = EXCLUDED.task_id,
                        worker_id = EXCLUDED.worker_id,
                        created_at = EXCLUDED.created_at,
                        last_accessed_at = EXCLUDED.last_accessed_at
                    """,
                    prompt_id,
                    task_id,
                    worker_id,
                    now,
                )
            return
        async with self._lock:
            self._routes[prompt_id] = PromptRoute(
                prompt_id=prompt_id,
                worker_id=worker_id,
                created_at=now,
                last_accessed_at=now,
            )

    async def get(self, prompt_id: str) -> Optional[PromptRoute]:
        if self._database is not None:
            now = unix_ms()
            routes_table = self._database.table("prompt_routes")
            async with self._database.pool.acquire() as connection:
                record = await connection.fetchrow(
                    f"SELECT prompt_id, worker_id, created_at, last_accessed_at FROM {routes_table} WHERE prompt_id = $1",
                    prompt_id,
                )
                if record is None:
                    return None
                if now - record["created_at"] > self._ttl_ms:
                    await connection.execute(f"DELETE FROM {routes_table} WHERE prompt_id = $1", prompt_id)
                    return None
                await connection.execute(
                    f"UPDATE {routes_table} SET last_accessed_at = $2 WHERE prompt_id = $1",
                    prompt_id,
                    now,
                )
                return PromptRoute(
                    prompt_id=record["prompt_id"],
                    worker_id=record["worker_id"],
                    created_at=record["created_at"],
                    last_accessed_at=now,
                )
        async with self._lock:
            r = self._routes.get(prompt_id)
            if r is None:
                return None
            now = unix_ms()
            if now - r.created_at > self._ttl_ms:
                self._routes.pop(prompt_id, None)
                return None
            r.last_accessed_at = now
            return r

    async def gc(self) -> int:
        now = unix_ms()
        if self._database is not None:
            routes_table = self._database.table("prompt_routes")
            async with self._database.pool.acquire() as connection:
                result = await connection.execute(
                    f"DELETE FROM {routes_table} WHERE created_at < $1",
                    now - self._ttl_ms,
                )
            return int(result.rsplit(" ", 1)[-1])
        removed = 0
        async with self._lock:
            for pid in list(self._routes):
                if now - self._routes[pid].created_at > self._ttl_ms:
                    self._routes.pop(pid)
                    removed += 1
        return removed
