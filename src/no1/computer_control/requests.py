from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from .storage import WorkflowStore


class ComputerRequestStore:
    AUTHORIZATION_TTL_SECONDS = 3600

    def __init__(self, workflows: WorkflowStore):
        self.workflows = workflows

    def _path(self, group_id: str):
        return self.workflows.state_root(group_id) / "requests.jsonl"

    def append(self, group_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        path = self._path(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {**request, "group_id": group_id, "updated_ts": time.time()}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return record

    def get(self, group_id: str, request_id: str) -> Optional[Dict[str, Any]]:
        path = self._path(group_id)
        if not path.exists():
            return None
        found: Optional[Dict[str, Any]] = None
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and str(value.get("request_id") or "") == request_id:
                found = {**(found or {}), **value}
        return found

    def update(self, group_id: str, request_id: str, **patch: Any) -> Dict[str, Any]:
        current = self.get(group_id, request_id)
        if current is None:
            raise KeyError(request_id)
        return self.append(group_id, {"request_id": request_id, **patch})

    def require_authorized(self, group_id: str, request_id: str, actor_id: str) -> Dict[str, Any]:
        request = self.get(group_id, request_id)
        if request is None:
            raise PermissionError("computer control request was not found")
        if str(request.get("actor_id") or "") != actor_id:
            raise PermissionError("computer control request belongs to another actor")
        status = str(request.get("status") or "")
        if status in {"rejected", "cancelled", "completed"}:
            raise PermissionError("computer control request is no longer active")
        # Once a recording has acquired the global lease, its authorization
        # follows that recording. The pre-start TTL prevents stale requests
        # from being used, but must not interrupt an intentionally long task.
        if not str(request.get("recording_id") or "").strip():
            created_ts = float(request.get("created_ts") or request.get("updated_ts") or 0)
            if created_ts <= 0 or time.time() - created_ts > self.AUTHORIZATION_TTL_SECONDS:
                raise PermissionError("computer control request authorization has expired")
        return request
