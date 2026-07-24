from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from ..util.file_lock import acquire_lockfile, release_lockfile
from ..util.fs import atomic_write_text


class LeaseConflict(RuntimeError):
    def __init__(self, lease: Dict[str, Any]):
        super().__init__("computer control is already in use")
        self.lease = lease


class ComputerControlLease:
    TTL_SECONDS = 30
    HEARTBEAT_SECONDS = 10

    def __init__(self, home: Path):
        self.state_dir = home / "state" / "computer-control"
        self.path = self.state_dir / "lease.json"
        self.lock_path = self.state_dir / "lease.lock"
        self._process_lock = threading.RLock()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self._process_lock:
            handle = acquire_lockfile(self.lock_path, blocking=True)
            try:
                yield
            finally:
                release_lockfile(handle)

    def _read_unlocked(self) -> Optional[Dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _active(lease: Optional[Dict[str, Any]], now: Optional[float] = None) -> bool:
        if not lease:
            return False
        return float(lease.get("expires_at") or 0) > float(now if now is not None else time.time())

    def status(self) -> Dict[str, Any]:
        with self._locked():
            lease = self._read_unlocked()
            if not self._active(lease):
                if self.path.exists():
                    self.path.unlink(missing_ok=True)
                return {"active": False}
            return {"active": True, "lease": lease}

    def acquire(self, *, group_id: str, actor_id: str, run_id: str, observe_only: bool = False) -> Dict[str, Any]:
        now = time.time()
        with self._locked():
            current = self._read_unlocked()
            if self._active(current, now) and str(current.get("run_id")) != run_id:
                raise LeaseConflict(current or {})
            lease = {
                "group_id": group_id,
                "actor_id": actor_id,
                "run_id": run_id,
                "observe_only": bool(observe_only),
                "owner_pid": os.getpid(),
                "acquired_at": now,
                "heartbeat_at": now,
                "expires_at": now + self.TTL_SECONDS,
            }
            atomic_write_text(self.path, json.dumps(lease, ensure_ascii=False, indent=2) + "\n")
            return lease

    def require(self, *, group_id: str, actor_id: str, run_id: str, allow_observe: bool = True) -> Dict[str, Any]:
        status = self.status()
        lease = status.get("lease") if status.get("active") else None
        if not isinstance(lease, dict) or any(
            str(lease.get(key) or "") != expected
            for key, expected in (("group_id", group_id), ("actor_id", actor_id), ("run_id", run_id))
        ):
            raise PermissionError("computer_control_lease_required")
        if not allow_observe and bool(lease.get("observe_only")):
            raise PermissionError("computer_control_write_lease_required")
        return lease

    def heartbeat(self, *, group_id: str, actor_id: str, run_id: str) -> Dict[str, Any]:
        now = time.time()
        with self._locked():
            lease = self._read_unlocked()
            if not self._active(lease, now) or not isinstance(lease, dict):
                raise PermissionError("computer_control_lease_required")
            if any(str(lease.get(k) or "") != v for k, v in (("group_id", group_id), ("actor_id", actor_id), ("run_id", run_id))):
                raise PermissionError("computer_control_lease_required")
            lease["heartbeat_at"] = now
            lease["expires_at"] = now + self.TTL_SECONDS
            atomic_write_text(self.path, json.dumps(lease, ensure_ascii=False, indent=2) + "\n")
            return lease

    def release(self, *, run_id: str, force: bool = False) -> bool:
        with self._locked():
            lease = self._read_unlocked()
            if not lease:
                return False
            if not force and str(lease.get("run_id") or "") != run_id:
                raise PermissionError("computer_control_lease_owner_required")
            self.path.unlink(missing_ok=True)
            return True
