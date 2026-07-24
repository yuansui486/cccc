from __future__ import annotations

import asyncio
import time
from typing import Dict, TYPE_CHECKING

from ..kernel.group import load_group
from .lease import LeaseConflict
if TYPE_CHECKING:
    from .services import ComputerControlServices


class ComputerControlScheduler:
    """Small lifecycle-owned scheduler for safe interval triggers.

    Other trigger kinds are evaluated by their dedicated event adapters; they
    remain disabled until those adapters provide a normalized event.
    """

    def __init__(self, service: "ComputerControlServices"):
        self.service = service
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_fire: Dict[str, float] = {}

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="onecolleague-computer-trigger-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                continue

    async def _tick(self) -> None:
        for group_dir in (self.service.home / "groups").glob("*"):
            group_id = group_dir.name
            group = load_group(group_id)
            if group is None or str(group.doc.get("state") or "active") not in {"active", "idle"}:
                continue
            for manifest in self.service.store.list(group_id):
                workflow_id = str(manifest.get("workflow_id") or "")
                published = manifest.get("published_version")
                if not workflow_id or not published:
                    continue
                trusted = manifest.get("trusted") if isinstance(manifest.get("trusted"), dict) else {}
                fp = str(self.service.setup.status().get("fingerprint") or "")
                if not isinstance(trusted.get(str(published)), dict) or trusted[str(published)].get("fingerprint") != fp:
                    continue
                try:
                    workflow = self.service.store.get(group_id, workflow_id, version=int(published))
                except Exception:
                    continue
                for trigger in workflow["definition"].get("triggers", []) if isinstance(workflow["definition"].get("triggers"), list) else []:
                    if not isinstance(trigger, dict) or not trigger.get("enabled") or trigger.get("type") != "interval":
                        continue
                    actor_id = str(trigger.get("actor_id") or "").strip()
                    seconds = int((trigger.get("config") or {}).get("seconds") or 0)
                    if not actor_id or seconds < 10:
                        continue
                    key = f"{group_id}:{workflow_id}:{trigger.get('id')}"
                    now = time.time()
                    if now - self._last_fire.get(key, 0) < seconds:
                        continue
                    self._last_fire[key] = now
                    try:
                        await self.service.runner.start(group_id, workflow_id, actor_id=actor_id, version=int(published), inputs={})
                    except LeaseConflict:
                        continue
                    except Exception:
                        continue
