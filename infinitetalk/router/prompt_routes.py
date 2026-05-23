"""In-memory prompt_id -> worker_id map with TTL."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class PromptRoute:
    prompt_id: str
    worker_id: str
    created_at: float
    last_accessed_at: float


class PromptRouteStore:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._routes: dict[str, PromptRoute] = {}
        self._lock = asyncio.Lock()

    async def put(self, prompt_id: str, worker_id: str) -> None:
        now = time.time()
        async with self._lock:
            self._routes[prompt_id] = PromptRoute(
                prompt_id=prompt_id,
                worker_id=worker_id,
                created_at=now,
                last_accessed_at=now,
            )

    async def get(self, prompt_id: str) -> Optional[PromptRoute]:
        async with self._lock:
            r = self._routes.get(prompt_id)
            if r is None:
                return None
            if time.time() - r.created_at > self._ttl:
                self._routes.pop(prompt_id, None)
                return None
            r.last_accessed_at = time.time()
            return r

    async def gc(self) -> int:
        now = time.time()
        removed = 0
        async with self._lock:
            for pid in list(self._routes):
                if now - self._routes[pid].created_at > self._ttl:
                    self._routes.pop(pid)
                    removed += 1
        return removed
