from __future__ import annotations

import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock


class TestGroupBridgeRemoteOutbox(unittest.TestCase):
    def setUp(self) -> None:
        from no1.daemon.group_bridge.remote_dispatch import enqueue_remote_send
        from no1.kernel.group_bridge.registration import upsert_registration

        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self._old_home = os.environ.get("CCCC_HOME")
        os.environ["CCCC_HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        self.addCleanup(self._td.cleanup)
        self.registration = upsert_registration(
            "local-group",
            "https://remote.example.test/api/v1/group-bridge/session",
            transport="group_bridge_session",
            remote_group_id="remote-group",
            remote_peer_id="remote-peer",
            credential_ref="sec_test_ref",
            home=self.home,
            _approved_by_pairing=True,
        )
        self.enqueue = enqueue_remote_send

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = self._old_home

    def _queue(self, key: str = "gbs_" + "a" * 32) -> None:
        self.enqueue(
            group_id="local-group",
            registration_id=self.registration["registration_id"],
            idempotency_key=key,
            payload={"text": "hello", "format": "markdown", "priority": "attention", "reply_required": True},
            home=self.home,
        )

    def test_worker_drives_queued_to_sent_once_and_replays_terminal(self) -> None:
        from no1.daemon.group_bridge.remote_outbox_worker import sweep_remote_outbox
        from no1.kernel.group_bridge.receipts import get_receipt

        self._queue()
        calls: list[dict[str, object]] = []

        def sender(**kwargs: object) -> dict[str, object]:
            calls.append(kwargs)
            return {"status": "accepted", "remote_event_id": "remote-event-1"}

        result = sweep_remote_outbox(
            home=self.home,
            local_endpoint="https://local.example.test/api/v1/group-bridge/session",
            session_sender=sender,
        )
        self.assertEqual(result, {"attempted": 1, "sent": 1, "retrying": 0, "failed": 0})
        receipt = get_receipt(self.registration["registration_id"], "gbs_" + "a" * 32, self.home)
        self.assertEqual(receipt["status"], "sent")
        self.assertEqual(receipt["remote_event_id"], "remote-event-1")
        self.assertEqual(receipt["attempt"], 1)
        self.assertEqual(calls[0]["payload"], {"text": "hello", "format": "markdown", "priority": "attention", "reply_required": True})
        self.assertEqual(len(calls[0]["client_nonce"]), 43)

        again = sweep_remote_outbox(home=self.home, session_sender=sender)
        self.assertEqual(again["attempted"], 0)
        self.assertEqual(len(calls), 1)

    def test_retry_reuses_session_nonce_and_reaches_sent(self) -> None:
        from no1.daemon.group_bridge.remote_outbox_worker import sweep_remote_outbox
        from no1.kernel.group_bridge.receipts import get_receipt, update_receipt
        from no1.daemon.group_bridge.session import GroupBridgeSessionError

        key = "gbs_" + "b" * 32
        self._queue(key)
        calls: list[str] = []

        def sender(**kwargs: object) -> dict[str, object]:
            calls.append(str(kwargs["client_nonce"]))
            if len(calls) == 1:
                raise GroupBridgeSessionError("transport_error", "temporary", retriable=True)
            return {"status": "accepted", "remote_event_id": "remote-event-2"}

        first = sweep_remote_outbox(home=self.home, session_sender=sender)
        self.assertEqual(first["retrying"], 1)
        self.assertEqual(get_receipt(self.registration["registration_id"], key, self.home)["status"], "retrying")
        update_receipt(self.registration["registration_id"], key, self.home, next_attempt_at="")
        second = sweep_remote_outbox(home=self.home, session_sender=sender)
        self.assertEqual(second["sent"], 1)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(get_receipt(self.registration["registration_id"], key, self.home)["attempt"], 2)

    def test_nonretriable_failure_is_terminal_and_stale_sending_recovers(self) -> None:
        from no1.daemon.group_bridge.remote_outbox_worker import sweep_remote_outbox
        from no1.daemon.group_bridge.session import GroupBridgeSessionError
        from no1.kernel.group_bridge.receipts import get_receipt, update_receipt

        failed_key = "gbs_" + "c" * 32
        self._queue(failed_key)

        def reject(**_kwargs: object) -> dict[str, object]:
            raise GroupBridgeSessionError("unauthorized", "rejected", retriable=False)

        result = sweep_remote_outbox(home=self.home, session_sender=reject)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(get_receipt(self.registration["registration_id"], failed_key, self.home)["status"], "failed")

        stale_key = "gbs_" + "d" * 32
        self._queue(stale_key)
        old = (datetime.now(timezone.utc) - timedelta(seconds=121)).isoformat().replace("+00:00", "Z")
        update_receipt(
            self.registration["registration_id"],
            stale_key,
            self.home,
            status="sending",
            attempt=1,
            last_attempt_at=old,
        )
        calls = Mock(return_value={"status": "accepted", "remote_event_id": "recovered"})
        result = sweep_remote_outbox(home=self.home, session_sender=calls)
        self.assertEqual(result["sent"], 1)
        self.assertEqual(get_receipt(self.registration["registration_id"], stale_key, self.home)["attempt"], 2)

        exhausted_key = "gbs_" + "e" * 32
        self._queue(exhausted_key)
        update_receipt(
            self.registration["registration_id"],
            exhausted_key,
            self.home,
            status="sending",
            attempt=5,
            max_attempts=5,
            last_attempt_at=old,
        )
        result = sweep_remote_outbox(home=self.home, session_sender=Mock())
        self.assertEqual(result["failed"], 1)
        self.assertEqual(get_receipt(self.registration["registration_id"], exhausted_key, self.home)["status"], "failed")

    def test_worker_start_and_stop_are_daemon_owned(self) -> None:
        from no1.daemon.group_bridge.remote_outbox_worker import RemoteOutboxWorker

        stopped = threading.Event()
        worker = RemoteOutboxWorker(home=self.home, interval_seconds=0.5, session_sender=lambda **_: {})
        original = worker._run

        def run() -> None:
            try:
                original()
            finally:
                stopped.set()

        worker._run = run  # type: ignore[method-assign]
        worker.start()
        worker.start()
        worker.stop(timeout=2.0)
        self.assertTrue(stopped.wait(1.0))
        self.assertFalse(worker._thread.is_alive())

    def test_short_shutdown_retains_lock_until_blocking_sender_exits(self) -> None:
        from no1.daemon.group_bridge.remote_outbox_worker import RemoteOutboxWorker
        from no1.daemon.serve_ops import cleanup_after_stop
        from no1.util.file_lock import acquire_lockfile, is_lockfile_handle, release_lockfile

        self._queue("gbs_" + "0" * 32)
        sender_started = threading.Event()
        release_sender = threading.Event()

        def blocking_sender(**_kwargs: object) -> dict[str, object]:
            sender_started.set()
            release_sender.wait(5.0)
            return {"status": "accepted", "remote_event_id": "released"}

        worker = RemoteOutboxWorker(home=self.home, interval_seconds=0.5, session_sender=blocking_sender)
        worker.start()
        self.assertTrue(sender_started.wait(2.0))
        lock_handle = acquire_lockfile(self.home / "onecolleagued.lock", blocking=False)
        cleanup_called = False
        try:
            self.assertFalse(worker.stop(timeout=0.01))
            self.assertTrue(is_lockfile_handle(lock_handle))
            if worker.stop(timeout=0.01):
                cleanup_called = True
            self.assertFalse(cleanup_called)

            release_sender.set()
            self.assertTrue(worker.stop(timeout=2.0))
            cleanup_called = True
            cleanup_after_stop(
                stop_event=threading.Event(),
                home=self.home,
                best_effort_killpg=lambda *_args, **_kwargs: None,
                im_stop_all=lambda *_args, **_kwargs: None,
                codex_stop_all=lambda: None,
                pty_stop_all=lambda: None,
                headless_stop_all=lambda: None,
                sock_path=self.home / "daemon.sock",
                addr_path=self.home / "daemon.addr",
                pid_path=self.home / "daemon.pid",
                release_lockfile=release_lockfile,
                lock_handle=lock_handle,
            )
            self.assertTrue(cleanup_called)
            self.assertFalse(is_lockfile_handle(lock_handle))
        finally:
            release_sender.set()
            worker.stop(timeout=2.0)
            if is_lockfile_handle(lock_handle):
                release_lockfile(lock_handle)

    def test_web_lifespan_does_not_own_outbox_and_server_does(self) -> None:
        import inspect

        from no1.daemon import server
        from no1.ports.web import app

        self.assertIn("RemoteOutboxWorker", inspect.getsource(server.serve_forever))
        self.assertNotIn("RemoteOutboxWorker", inspect.getsource(app._lifespan) if hasattr(app, "_lifespan") else "")

    def test_stale_owner_success_and_failure_are_fenced_after_reclaim(self) -> None:
        from no1.kernel.group_bridge.receipts import claim_receipt_attempt, get_receipt, update_receipt

        key = "gbs_" + "f" * 32
        self._queue(key)
        base = datetime.now(timezone.utc)
        old = claim_receipt_attempt(self.registration["registration_id"], key, self.home, now=base)
        self.assertEqual(old["status"], "sending")
        reclaim_time = base + timedelta(seconds=121)
        claims: list[dict[str, object] | None] = []
        barrier = threading.Barrier(2)

        def reclaim() -> None:
            barrier.wait()
            claims.append(claim_receipt_attempt(self.registration["registration_id"], key, self.home, now=reclaim_time))

        threads = [threading.Thread(target=reclaim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        current = [claim for claim in claims if claim is not None]
        self.assertEqual(len(current), 1)
        new = current[0]
        self.assertNotEqual(old["_claim_token"], new["_claim_token"])
        self.assertEqual(new["attempt"], 2)
        self.assertIsNone(claims[0] if claims[0] is None else claims[1])
        path = self.home / "group_bridge_receipts.yaml"
        before_stale_writes = path.read_bytes()

        self.assertIsNone(
            update_receipt(
                self.registration["registration_id"],
                key,
                self.home,
                status="sent",
                remote_event_id="stale-success",
                expected_attempt=old["attempt"],
                claim_token=old["_claim_token"],
            )
        )
        self.assertIsNone(
            update_receipt(
                self.registration["registration_id"],
                key,
                self.home,
                status="failed",
                expected_attempt=old["attempt"],
                claim_token=old["_claim_token"],
            )
        )
        still_sending = get_receipt(self.registration["registration_id"], key, self.home)
        self.assertEqual(still_sending["status"], "sending")
        self.assertEqual(still_sending["attempt"], 2)
        self.assertNotIn("_claim_token", still_sending)
        self.assertEqual(path.read_bytes(), before_stale_writes)
        final = update_receipt(
            self.registration["registration_id"],
            key,
            self.home,
            status="sent",
            remote_event_id="new-owner-success",
            expected_attempt=new["attempt"],
            claim_token=new["_claim_token"],
        )
        self.assertEqual(final["remote_event_id"], "new-owner-success")


if __name__ == "__main__":
    unittest.main()
