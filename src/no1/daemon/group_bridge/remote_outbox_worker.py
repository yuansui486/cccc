"""Daemon-owned Group Bridge transport worker for queued remote sends."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ...kernel.group_bridge.receipts import (
    claim_receipt_attempt,
    get_queued_request,
    load_receipts,
    update_receipt,
)
from ...kernel.group_bridge.registration import get_registration
from ...util.time import parse_utc_iso, utc_now_iso
from .session import GroupBridgeSessionError, send_group_bridge_session_message

logger = logging.getLogger("no1.group_bridge.outbox")

_TRANSPORT = "group_bridge_session"
_STALE_SENDING_SECONDS = 120
_BACKOFF_SECONDS = (2, 5, 15, 30, 60)


@dataclass(frozen=True)
class OutboxSweepResult:
    attempted: int = 0
    sent: int = 0
    retrying: int = 0
    failed: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "attempted": self.attempted,
            "sent": self.sent,
            "retrying": self.retrying,
            "failed": self.failed,
        }


SessionSender = Callable[..., Dict[str, Any]]


def default_local_endpoint() -> str:
    configured = str(os.environ.get("CCCC_GROUP_BRIDGE_LOCAL_ENDPOINT") or "").strip()
    if configured:
        return configured
    host = str(os.environ.get("CCCC_WEB_EFFECTIVE_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = str(os.environ.get("CCCC_WEB_EFFECTIVE_PORT") or "8848").strip() or "8848"
    return f"http://{host}:{port}/api/group-bridge/session"


def _client_nonce(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _due(receipt: Dict[str, Any], *, now: datetime) -> bool:
    status = str(receipt.get("status") or "")
    if status in {"queued", "retrying"}:
        next_attempt = parse_utc_iso(str(receipt.get("next_attempt_at") or ""))
        return next_attempt is None or next_attempt <= now
    if status == "sending":
        last_attempt = parse_utc_iso(str(receipt.get("last_attempt_at") or ""))
        return last_attempt is None or now - last_attempt >= timedelta(seconds=_STALE_SENDING_SECONDS)
    return False


def iter_due_receipts(*, home: Optional[Path] = None, now: Optional[datetime] = None) -> list[Dict[str, Any]]:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    due: list[Dict[str, Any]] = []
    for receipt in load_receipts(home=home).values():
        if not _due(receipt, now=current_time):
            continue
        try:
            attempt = int(receipt.get("attempt") or 0)
            max_attempts = max(1, int(receipt.get("max_attempts") or 1))
        except Exception:
            continue
        if attempt >= max_attempts and str(receipt.get("status") or "") != "sending":
            continue
        due.append(dict(receipt))
    return due


def _failure(error_code: str, *, retriable: bool) -> Dict[str, Any]:
    if error_code in {"unauthorized", "invalid_request", "remote_rejected"}:
        code = error_code
    else:
        code = "transport_error" if retriable else "remote_rejected"
    return {"code": code, "retriable": retriable, "transport": _TRANSPORT}


def _finish_failure(
    registration_id: str,
    idempotency_key: str,
    receipt: Dict[str, Any],
    *,
    error_code: str,
    retriable: bool,
    home: Optional[Path],
) -> Optional[Dict[str, Any]]:
    attempt = int(receipt.get("attempt") or 0)
    max_attempts = max(1, int(receipt.get("max_attempts") or 1))
    exhausted = attempt >= max_attempts
    retry = retriable and not exhausted
    next_attempt_at = ""
    if retry:
        delay = _BACKOFF_SECONDS[min(max(0, attempt - 1), len(_BACKOFF_SECONDS) - 1)]
        next_attempt_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z")
    return update_receipt(
        registration_id,
        idempotency_key,
        home,
        ok=False,
        status="retrying" if retry else "failed",
        next_attempt_at=next_attempt_at,
        error=_failure(error_code, retriable=retry),
        transport=_TRANSPORT,
        expected_attempt=int(receipt.get("attempt") or 0),
        claim_token=str(receipt.get("_claim_token") or ""),
    )


def attempt_receipt(
    receipt: Dict[str, Any],
    *,
    local_endpoint: str,
    home: Optional[Path] = None,
    session_sender: SessionSender = send_group_bridge_session_message,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    registration_id = str(receipt.get("registration_id") or "").strip()
    idempotency_key = str(receipt.get("idempotency_key") or "").strip()
    if not registration_id or not idempotency_key:
        return None
    claimed = claim_receipt_attempt(
        registration_id,
        idempotency_key,
        home,
        now=now,
        stale_after_seconds=_STALE_SENDING_SECONDS,
    )
    if claimed is None:
        return None
    if str(claimed.get("status") or "") != "sending":
        return claimed
    queued = get_queued_request(registration_id, idempotency_key, home)
    registration = get_registration(registration_id, home)
    if not isinstance(queued, dict) or not isinstance(registration, dict):
        return _finish_failure(
            registration_id,
            idempotency_key,
            claimed,
            error_code="invalid_request",
            retriable=False,
            home=home,
        )
    if (
        str(registration.get("status") or "") != "active"
        or str(registration.get("transport") or "") != _TRANSPORT
        or str(registration.get("group_id") or "") != str(queued.get("src_group_id") or "")
    ):
        return _finish_failure(
            registration_id,
            idempotency_key,
            claimed,
            error_code="unauthorized",
            retriable=False,
            home=home,
        )
    try:
        result = session_sender(
            group_id=str(queued["src_group_id"]),
            local_endpoint=local_endpoint,
            remote_group_id=str(registration.get("remote_group_id") or ""),
            remote_peer_id=str(registration.get("remote_peer_id") or ""),
            remote_endpoint=str(registration.get("url") or ""),
            client_nonce=_client_nonce(idempotency_key),
            payload=queued["payload"],
            home=home,
        )
        if not isinstance(result, dict) or not result.get("remote_event_id"):
            raise GroupBridgeSessionError("invalid_response", "remote session receipt is invalid", retriable=True)
    except GroupBridgeSessionError as exc:
        return _finish_failure(
            registration_id,
            idempotency_key,
            claimed,
            error_code=exc.code,
            retriable=exc.retriable,
            home=home,
        )
    except Exception:
        logger.exception("Group Bridge remote delivery failed for registration_id=%s", registration_id)
        return _finish_failure(
            registration_id,
            idempotency_key,
            claimed,
            error_code="transport_error",
            retriable=True,
            home=home,
        )
    return update_receipt(
        registration_id,
        idempotency_key,
        home,
        ok=True,
        status="sent",
        remote_event_id=str(result["remote_event_id"]),
        transport=_TRANSPORT,
        error=None,
        accepted_at=utc_now_iso(),
        expected_attempt=int(claimed.get("attempt") or 0),
        claim_token=str(claimed.get("_claim_token") or ""),
    )


def sweep_remote_outbox(
    *,
    home: Optional[Path] = None,
    local_endpoint: Optional[str] = None,
    session_sender: SessionSender = send_group_bridge_session_message,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    attempted = sent = retrying = failed = 0
    for receipt in iter_due_receipts(home=home, now=now):
        attempted += 1
        try:
            updated = attempt_receipt(
                receipt,
                local_endpoint=local_endpoint or default_local_endpoint(),
                home=home,
                session_sender=session_sender,
                now=now,
            )
        except Exception:
            logger.exception("Group Bridge outbox attempt crashed")
            failed += 1
            continue
        if not updated:
            continue
        status = str(updated.get("status") or "")
        if status == "sent":
            sent += 1
        elif status == "retrying":
            retrying += 1
        elif status == "failed":
            failed += 1
    return OutboxSweepResult(attempted, sent, retrying, failed).as_dict()


class RemoteOutboxWorker:
    def __init__(
        self,
        *,
        home: Optional[Path] = None,
        local_endpoint: Optional[str] = None,
        interval_seconds: float = 2.0,
        session_sender: SessionSender = send_group_bridge_session_message,
    ) -> None:
        self._home = Path(home) if home is not None else None
        self._local_endpoint = local_endpoint or default_local_endpoint()
        self._interval_seconds = max(0.5, float(interval_seconds or 2.0))
        self._session_sender = session_sender
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="no1-group-bridge-outbox", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> bool:
        """Request shutdown and report whether the worker fully exited.

        A timed-out network sender cannot be interrupted safely.  Callers must
        keep daemon ownership until a later stop observes the thread exited.
        """
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, float(timeout or 0.0)))
        return not thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sweep_remote_outbox(
                    home=self._home,
                    local_endpoint=self._local_endpoint,
                    session_sender=self._session_sender,
                )
            except Exception:
                logger.exception("Group Bridge outbox sweep crashed")
            self._stop.wait(self._interval_seconds)


__all__ = [
    "OutboxSweepResult",
    "RemoteOutboxWorker",
    "attempt_receipt",
    "default_local_endpoint",
    "iter_due_receipts",
    "sweep_remote_outbox",
]
