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
from .derived_authority import DerivedAuthorityClaim


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
        self.lineage_path = self.state_dir / "lease-authority-lineages.json"
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

    def _read_lineages_unlocked(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.lineage_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            return {}

    def _retire_authority_unlocked(self, lease: Dict[str, Any]) -> None:
        identity = self._authority_identity(lease)
        if not identity:
            return
        lineages = self._read_lineages_unlocked()
        authority_id = str(identity["authority_id"])
        previous = lineages.get(authority_id) if isinstance(lineages.get(authority_id), dict) else {}
        if int(identity["revision"]) >= int(previous.get("revision") or 0):
            lineages[authority_id] = identity
            atomic_write_text(
                self.lineage_path,
                json.dumps(lineages, ensure_ascii=False, indent=2) + "\n",
            )

    def _lineage_allows_unlocked(self, identity: Dict[str, Any]) -> bool:
        if not identity:
            return True
        lineages = self._read_lineages_unlocked()
        previous = lineages.get(str(identity.get("authority_id") or ""))
        if not isinstance(previous, dict):
            return True
        immutable = ("issuer_epoch", "kind", "authority_id", "group_id", "actor_id", "resource_id", "generation")
        return bool(
            all(previous.get(key) == identity.get(key) for key in immutable)
            and int(identity.get("revision") or 0) > int(previous.get("revision") or 0)
        )

    @staticmethod
    def _active(lease: Optional[Dict[str, Any]], now: Optional[float] = None) -> bool:
        if not lease:
            return False
        return float(lease.get("expires_at") or 0) > float(now if now is not None else time.time())

    def status(self) -> Dict[str, Any]:
        with self._locked():
            lease = self._read_unlocked()
            if not self._active(lease):
                if isinstance(lease, dict):
                    self._retire_authority_unlocked(lease)
                self.path.unlink(missing_ok=True)
                return {"active": False}
            return {"active": True, "lease": lease}

    @staticmethod
    def _authority_identity(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        identity = value.get("authority")
        if not isinstance(identity, dict):
            return {}
        try:
            generation = int(identity.get("generation") or 0)
            revision = int(identity.get("revision") or 0)
        except Exception:
            return {}
        normalized = {
            "issuer_epoch": str(identity.get("issuer_epoch") or ""),
            "kind": str(identity.get("kind") or ""),
            "authority_id": str(identity.get("authority_id") or ""),
            "group_id": str(identity.get("group_id") or ""),
            "actor_id": str(identity.get("actor_id") or ""),
            "resource_id": str(identity.get("resource_id") or ""),
            "generation": generation,
            "revision": revision,
            "state": str(identity.get("state") or ""),
        }
        if (
            not normalized["issuer_epoch"]
            or normalized["kind"] != "recording"
            or not normalized["authority_id"]
            or not normalized["group_id"]
            or not normalized["actor_id"]
            or not normalized["resource_id"]
            or generation <= 0
            or revision <= 0
            or not normalized["state"]
        ):
            return {}
        return normalized

    @staticmethod
    def _claim_identity(
        authority: Optional[DerivedAuthorityClaim],
        *,
        allowed_states: frozenset[str],
        group_id: str = "",
        actor_id: str = "",
        run_id: str = "",
    ) -> Dict[str, Any]:
        if authority is None:
            return {}
        if not isinstance(authority, DerivedAuthorityClaim):
            raise PermissionError("validated derived authority claim is required")
        if (
            authority.kind != "recording"
            or authority.state not in allowed_states
            or (group_id and authority.group_id != group_id)
            or (actor_id and authority.actor_id != actor_id)
            or (run_id and authority.resource_id != run_id)
        ):
            raise PermissionError("computer_control_lease_authority_required")
        return authority.lease_identity()

    def acquire(
        self,
        *,
        group_id: str,
        actor_id: str,
        run_id: str,
        observe_only: bool = False,
        authority: Optional[DerivedAuthorityClaim] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        authority_identity = self._claim_identity(
            authority,
            allowed_states=frozenset({"active"}),
            group_id=group_id,
            actor_id=actor_id,
            run_id=run_id,
        )
        with self._locked():
            current = self._read_unlocked()
            current_authority = self._authority_identity(current)
            if not self._active(current, now) and isinstance(current, dict):
                self._retire_authority_unlocked(current)
                self.path.unlink(missing_ok=True)
                current = None
                current_authority = {}
            if authority_identity and not self._lineage_allows_unlocked(authority_identity):
                raise PermissionError("computer_control_lease_authority_required")
            if self._active(current, now):
                same_owner = isinstance(current, dict) and all(
                    str(current.get(key) or "") == expected
                    for key, expected in (
                        ("group_id", group_id),
                        ("actor_id", actor_id),
                        ("run_id", run_id),
                    )
                )
                same_mode = (
                    isinstance(current, dict)
                    and bool(current.get("observe_only")) == bool(observe_only)
                )
                if not same_owner or not same_mode or current_authority != authority_identity:
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
            if authority_identity:
                lease["authority"] = authority_identity
            atomic_write_text(self.path, json.dumps(lease, ensure_ascii=False, indent=2) + "\n")
            return lease

    def reserve(
        self,
        *,
        group_id: str,
        actor_id: str,
        run_id: str,
        authority: DerivedAuthorityClaim,
    ) -> Dict[str, Any]:
        authority_identity = self._claim_identity(
            authority,
            allowed_states=frozenset({"pending"}),
            group_id=group_id,
            actor_id=actor_id,
            run_id=run_id,
        )
        now = time.time()
        with self._locked():
            current = self._read_unlocked()
            if not self._active(current, now) and isinstance(current, dict):
                self._retire_authority_unlocked(current)
                self.path.unlink(missing_ok=True)
                current = None
            if self._active(current, now):
                raise LeaseConflict(current or {})
            if not self._lineage_allows_unlocked(authority_identity):
                raise PermissionError("computer_control_lease_authority_required")
            lease = {
                "group_id": group_id,
                "actor_id": actor_id,
                "run_id": run_id,
                "observe_only": False,
                "owner_pid": os.getpid(),
                "acquired_at": now,
                "heartbeat_at": now,
                "expires_at": now + self.TTL_SECONDS,
                "authority": authority_identity,
                "reservation": True,
            }
            atomic_write_text(self.path, json.dumps(lease, ensure_ascii=False, indent=2) + "\n")
            return lease

    def activate_reservation(
        self,
        *,
        pending: DerivedAuthorityClaim,
        active: DerivedAuthorityClaim,
    ) -> Dict[str, Any]:
        pending_identity = self._claim_identity(
            pending,
            allowed_states=frozenset({"pending"}),
            group_id=pending.group_id,
            actor_id=pending.actor_id,
            run_id=pending.resource_id,
        )
        active_identity = self._claim_identity(
            active,
            allowed_states=frozenset({"active"}),
            group_id=pending.group_id,
            actor_id=pending.actor_id,
            run_id=pending.resource_id,
        )
        immutable = ("issuer_epoch", "kind", "authority_id", "group_id", "actor_id", "resource_id", "generation")
        if (
            any(pending_identity[key] != active_identity[key] for key in immutable)
            or int(active_identity["revision"]) != int(pending_identity["revision"]) + 1
        ):
            raise PermissionError("computer_control_lease_authority_transition_required")
        with self._locked():
            lease = self._read_unlocked()
            if (
                not self._active(lease)
                or not isinstance(lease, dict)
                or lease.get("reservation") is not True
                or self._authority_identity(lease) != pending_identity
            ):
                raise PermissionError("computer_control_lease_authority_transition_required")
            lease["authority"] = active_identity
            lease.pop("reservation", None)
            lease["heartbeat_at"] = time.time()
            lease["expires_at"] = time.time() + self.TTL_SECONDS
            atomic_write_text(self.path, json.dumps(lease, ensure_ascii=False, indent=2) + "\n")
            return lease

    def cancel_reservation(self, *, authority: DerivedAuthorityClaim) -> bool:
        identity = self._claim_identity(
            authority,
            allowed_states=frozenset({"pending"}),
            group_id=authority.group_id,
            actor_id=authority.actor_id,
            run_id=authority.resource_id,
        )
        with self._locked():
            lease = self._read_unlocked()
            if not lease:
                return False
            if (
                not isinstance(lease, dict)
                or lease.get("reservation") is not True
                or self._authority_identity(lease) != identity
            ):
                raise PermissionError("computer_control_lease_authority_required")
            self._retire_authority_unlocked(lease)
            self.path.unlink(missing_ok=True)
            return True

    def require(
        self,
        *,
        group_id: str,
        actor_id: str,
        run_id: str,
        allow_observe: bool = True,
        authority: Optional[DerivedAuthorityClaim] = None,
    ) -> Dict[str, Any]:
        status = self.status()
        lease = status.get("lease") if status.get("active") else None
        if not isinstance(lease, dict) or any(
            str(lease.get(key) or "") != expected
            for key, expected in (("group_id", group_id), ("actor_id", actor_id), ("run_id", run_id))
        ):
            raise PermissionError("computer_control_lease_required")
        if self._authority_identity(lease) != self._claim_identity(
            authority,
            allowed_states=frozenset({"active"}),
            group_id=group_id,
            actor_id=actor_id,
            run_id=run_id,
        ):
            raise PermissionError("computer_control_lease_authority_required")
        if not allow_observe and bool(lease.get("observe_only")):
            raise PermissionError("computer_control_write_lease_required")
        return lease

    def heartbeat(
        self,
        *,
        group_id: str,
        actor_id: str,
        run_id: str,
        authority: Optional[DerivedAuthorityClaim] = None,
    ) -> Dict[str, Any]:
        now = time.time()
        authority_identity = self._claim_identity(
            authority,
            allowed_states=frozenset({"active"}),
            group_id=group_id,
            actor_id=actor_id,
            run_id=run_id,
        )
        with self._locked():
            lease = self._read_unlocked()
            if not self._active(lease, now) or not isinstance(lease, dict):
                raise PermissionError("computer_control_lease_required")
            if any(str(lease.get(k) or "") != v for k, v in (("group_id", group_id), ("actor_id", actor_id), ("run_id", run_id))):
                raise PermissionError("computer_control_lease_required")
            if self._authority_identity(lease) != authority_identity:
                raise PermissionError("computer_control_lease_authority_required")
            lease["heartbeat_at"] = now
            lease["expires_at"] = now + self.TTL_SECONDS
            atomic_write_text(self.path, json.dumps(lease, ensure_ascii=False, indent=2) + "\n")
            return lease

    def release(
        self,
        *,
        run_id: str,
        force: bool = False,
        authority: Optional[DerivedAuthorityClaim] = None,
    ) -> bool:
        with self._locked():
            lease = self._read_unlocked()
            if not lease:
                return False
            if not force and str(lease.get("run_id") or "") != run_id:
                raise PermissionError("computer_control_lease_owner_required")
            lease_authority = self._authority_identity(lease)
            if not force and lease_authority != self._claim_identity(
                authority,
                allowed_states=frozenset({"active", "terminating"}),
                run_id=run_id,
            ):
                raise PermissionError("computer_control_lease_authority_required")
            if lease_authority and not force:
                self._retire_authority_unlocked(lease)
            self.path.unlink(missing_ok=True)
            return True

    def release_recording_for_stop(self, *, group_id: str, actor_id: str, run_id: str) -> bool:
        """Release only the exact recording lease; this cannot authorize new work."""

        with self._locked():
            lease = self._read_unlocked()
            if not lease:
                return False
            identity = self._authority_identity(lease)
            if (
                not identity
                or identity.get("kind") != "recording"
                or str(identity.get("group_id") or "") != group_id
                or str(identity.get("actor_id") or "") != actor_id
                or str(identity.get("resource_id") or "") != run_id
                or str(lease.get("group_id") or "") != group_id
                or str(lease.get("actor_id") or "") != actor_id
                or str(lease.get("run_id") or "") != run_id
            ):
                raise PermissionError("computer_control_lease_authority_required")
            self._retire_authority_unlocked(lease)
            self.path.unlink(missing_ok=True)
            return True

    @contextmanager
    def hold(
        self,
        *,
        group_id: str,
        actor_id: str,
        run_id: str,
        observe_only: bool = False,
        authority: Optional[DerivedAuthorityClaim] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Acquire a lease and keep it alive for one synchronous operation.

        Setup, session restart, and snapshot calls can legitimately take
        longer than the lease TTL.  Their heartbeat must not depend on the
        blocked caller thread, and cleanup must never force-release a newer
        owner that acquired the machine after this operation lost its lease.
        """

        lease = self.acquire(
            group_id=group_id,
            actor_id=actor_id,
            run_id=run_id,
            observe_only=observe_only,
            authority=authority,
        )
        stopped = threading.Event()
        lost = threading.Event()

        def heartbeat_loop() -> None:
            interval = max(0.01, float(self.HEARTBEAT_SECONDS) / 2.0)
            while not stopped.wait(interval):
                try:
                    self.heartbeat(group_id=group_id, actor_id=actor_id, run_id=run_id, authority=authority)
                except Exception:
                    lost.set()
                    return

        thread = threading.Thread(
            target=heartbeat_loop,
            name=f"computer-control-lease-{run_id[:24]}",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            self.release(run_id=run_id, force=False, authority=authority)
            raise
        try:
            yield {**lease, "lost": lost}
        finally:
            stopped.set()
            thread.join(timeout=max(1.0, float(self.HEARTBEAT_SECONDS)))
            try:
                self.release(run_id=run_id, force=False, authority=authority)
            except (OSError, PermissionError):
                # Ownership changed after expiry.  The new lease belongs to a
                # different operation and must remain untouched.
                pass
