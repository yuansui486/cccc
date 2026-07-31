from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import multiprocessing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


def _spawn_claim_same_nonce(home: str, start: object, results: object) -> None:
    from pathlib import Path

    from no1.daemon.group_bridge import session

    start.wait(timeout=10)  # type: ignore[attr-defined]
    try:
        session._reserve(
            direction="outbound",
            nonce_hash="a" * 64,
            fingerprint="b" * 64,
            home=Path(home),
        )
        entry, _revision = session._claim_attempt(
            direction="outbound",
            nonce_hash="a" * 64,
            home=Path(home),
        )
        results.put(entry["status"])  # type: ignore[attr-defined]
    except session.GroupBridgeSessionError as exc:
        results.put(exc.code)  # type: ignore[attr-defined]


class _FakeStreamResponse:
    def __init__(
        self,
        *,
        chunks: list[bytes],
        content_length: str | None = None,
        content_encoding: str | None = None,
        status_code: int = 200,
    ) -> None:
        self.chunks = chunks
        self.headers = {} if content_length is None else {"content-length": content_length}
        if content_encoding is not None:
            self.headers["content-encoding"] = content_encoding
        self.status_code = status_code
        self.read_count = 0
        self.raw_chunk_sizes: list[int | None] = []
        self.closed = False

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def iter_raw(self, chunk_size: int | None = None):
        self.raw_chunk_sizes.append(chunk_size)
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk


class TestGroupBridgeSession(unittest.TestCase):
    def setUp(self) -> None:
        from no1.daemon.group_bridge.identity import get_group_bridge_identity

        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.home_a = self.root / "a"
        self.home_b = self.root / "b"
        self.endpoint_a = "https://a.example.test/api/v1/group-bridge/session"
        self.endpoint_b = "https://b.example.test/api/v1/group-bridge/session"
        self.identity_a = get_group_bridge_identity(home=self.home_a)
        self.identity_b = get_group_bridge_identity(home=self.home_b)
        self.authorized = True
        self.auth_calls: list[dict[str, object]] = []
        self.address_calls: list[tuple[str, str, Path]] = []

        def authorize(**kwargs: object) -> dict[str, object] | None:
            self.auth_calls.append(dict(kwargs))
            if not self.authorized:
                return None
            return {
                "remote_endpoint": kwargs["remote_endpoint"],
                "remote_group_id": kwargs["remote_group_id"],
                "remote_peer_id": kwargs["remote_peer_id"],
                "access_level": "messages",
                "status": "active",
            }

        def resolve(peer_id: str, *, remote_group_id: str, home: Path) -> tuple[str, ...]:
            self.address_calls.append((peer_id, remote_group_id, Path(home)))
            return (f"/dns4/{remote_group_id}.example.test/tcp/443/https",)

        self._auth_patch = patch("no1.daemon.group_bridge.session.authorize_remote_principal", side_effect=authorize)
        self._address_patch = patch("no1.daemon.group_bridge.session.resolve_peer_multiaddrs", side_effect=resolve)
        self._auth_patch.start()
        self._address_patch.start()
        self.addCleanup(self._auth_patch.stop)
        self.addCleanup(self._address_patch.stop)
        self.addCleanup(self._td.cleanup)

    def _payload(self, text: str = "hello") -> dict[str, object]:
        return {"text": text, "format": "plain", "priority": "normal", "reply_required": False}

    def _receive(self, body: dict[str, object], delivered: list[tuple[dict[str, object], str]]) -> dict[str, object]:
        from no1.daemon.group_bridge.session import receive_group_bridge_session_message

        return receive_group_bridge_session_message(
            body,
            group_id="g-b",
            local_endpoint=self.endpoint_b,
            home=self.home_b,
            deliver=lambda payload, delivery_id: self._deliver(delivered, payload, delivery_id),
        )

    @staticmethod
    def _deliver(
        delivered: list[tuple[dict[str, object], str]], payload: dict[str, object], delivery_id: str
    ) -> str:
        delivered.append((payload, delivery_id))
        return "event-b-1"

    def _send(self, *, nonce: str, http_post, text: str = "hello", remote_group_id: str = "g-b"):
        from no1.daemon.group_bridge.session import send_group_bridge_session_message

        return send_group_bridge_session_message(
            group_id="g-a",
            local_endpoint=self.endpoint_a,
            remote_group_id=remote_group_id,
            remote_peer_id=self.identity_b.peer_id,
            remote_endpoint=self.endpoint_b,
            client_nonce=nonce,
            payload=self._payload(text),
            home=self.home_a,
            http_post=http_post,
        )

    def _signed_request(
        self,
        *,
        nonce: str,
        issued_at: str | None = None,
        target_peer_id: str | None = None,
    ) -> dict[str, object]:
        from no1.contracts.v1.group_bridge import GroupBridgeSessionMessage
        from no1.daemon.group_bridge import identity as identity_module
        from no1.daemon.group_bridge import session

        envelope = session._signed_message(
            nonce=nonce,
            source_group_id="g-a",
            source_endpoint=self.endpoint_a,
            target_group_id="g-b",
            target_peer_id=target_peer_id or self.identity_b.peer_id,
            target_endpoint=self.endpoint_b,
            payload=GroupBridgeSessionMessage.model_validate(self._payload()),
            home=self.home_a,
        ).model_dump()
        if issued_at is not None:
            envelope["issued_at"] = issued_at
            unsigned = {key: value for key, value in envelope.items() if key != "signature"}
            envelope["signature"] = identity_module.sign_group_bridge_payload(
                identity_module.canonical_payload_bytes(unsigned), home=self.home_a
            )
        return envelope

    def _write_state(
        self,
        *,
        home: Path,
        direction: str,
        status: str,
        label: str,
        now: datetime,
    ) -> tuple[str, dict[str, object]]:
        from no1.daemon.group_bridge import session

        nonce_hash = hashlib.sha256(f"nonce:{label}".encode("ascii")).hexdigest()
        fingerprint = hashlib.sha256(f"facts:{label}".encode("ascii")).hexdigest()
        with patch.object(session, "_now", return_value=now):
            session._reserve(
                direction=direction,
                nonce_hash=nonce_hash,
                fingerprint=fingerprint,
                home=home,
            )
            if status != "reserved":
                entry, revision = session._claim_attempt(direction=direction, nonce_hash=nonce_hash, home=home)
                active_status = "sending" if direction == "outbound" else "delivering"
                self.assertEqual(entry["status"], active_status)
                if status == "retrying":
                    session._finish_attempt(
                        direction=direction,
                        nonce_hash=nonce_hash,
                        revision=revision,
                        home=home,
                        error_code="transport_error" if direction == "outbound" else "delivery_failed",
                    )
                elif status == "accepted":
                    session._finish_attempt(
                        direction=direction,
                        nonce_hash=nonce_hash,
                        revision=revision,
                        home=home,
                        accepted_event_id=f"event-{label}",
                    )
                elif status != active_status:
                    self.fail(f"unsupported writer state: {status}")
        entry = session.load_session_reservations(home=home)[direction][nonce_hash]
        return nonce_hash, entry

    def test_roundtrip_restart_replay_and_safe_persistence(self) -> None:
        from no1.daemon.group_bridge.session import (
            load_session_reservations,
            new_group_bridge_session_nonce,
            session_reservation_path,
        )

        nonce = new_group_bridge_session_nonce()
        delivered: list[tuple[dict[str, object], str]] = []
        network_calls: list[str] = []

        def post(endpoint: str, body: dict[str, object]) -> dict[str, object]:
            network_calls.append(endpoint)
            return self._receive(body, delivered)

        first = self._send(nonce=nonce, http_post=post)
        second = self._send(nonce=nonce, http_post=post)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(first["remote_event_id"], "event-b-1")
        self.assertEqual(len(network_calls), 1)
        self.assertEqual(len(delivered), 1)
        self.assertTrue(delivered[0][1].startswith("gbs_"))
        outbound = load_session_reservations(home=self.home_a)["outbound"]
        self.assertEqual(len(outbound), 1)
        self.assertEqual(next(iter(outbound.values()))["status"], "accepted")
        persisted = session_reservation_path(home=self.home_a).read_text(encoding="utf-8")
        self.assertNotIn(nonce, persisted)
        self.assertNotIn("hello", persisted)
        self.assertNotIn("signature", persisted)

    def test_response_loss_retries_same_facts_without_redelivery(self) -> None:
        from no1.daemon.group_bridge.session import GroupBridgeSessionError, new_group_bridge_session_nonce

        nonce = new_group_bridge_session_nonce()
        delivered: list[tuple[dict[str, object], str]] = []
        calls = 0

        def post(endpoint: str, body: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            response = self._receive(body, delivered)
            if calls == 1:
                raise TimeoutError("response lost")
            return response

        with self.assertRaises(GroupBridgeSessionError) as raised:
            self._send(nonce=nonce, http_post=post)
        self.assertEqual(raised.exception.code, "transport_error")
        result = self._send(nonce=nonce, http_post=post)
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(calls, 2)
        self.assertEqual(len(delivered), 1)

    def test_crash_after_delivery_reuses_deterministic_delivery_id(self) -> None:
        from no1.daemon.group_bridge import session

        nonce = session.new_group_bridge_session_nonce()
        request = self._signed_request(nonce=nonce)
        delivery_ids: list[str] = []
        real_finish = session._finish_attempt

        def deliver(_payload: dict[str, object], delivery_id: str) -> str:
            delivery_ids.append(delivery_id)
            return "event-b-1"

        def crash_before_terminal(**kwargs: object) -> dict[str, object]:
            if kwargs.get("accepted_event_id"):
                raise RuntimeError("simulated crash after delivery")
            return real_finish(**kwargs)  # type: ignore[arg-type]

        with patch.object(session, "_finish_attempt", side_effect=crash_before_terminal):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                session.receive_group_bridge_session_message(
                    request,
                    group_id="g-b",
                    local_endpoint=self.endpoint_b,
                    home=self.home_b,
                    deliver=deliver,
                )

        future = session._now() + timedelta(seconds=session._LEASE_SECONDS + 1)
        with patch.object(session, "_now", return_value=future):
            receipt = session.receive_group_bridge_session_message(
                request,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=deliver,
            )
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(delivery_ids, [delivery_ids[0], delivery_ids[0]])

    def test_crash_before_network_recovers_after_attempt_lease(self) -> None:
        from no1.daemon.group_bridge import session

        class SimulatedCrash(BaseException):
            pass

        nonce = session.new_group_bridge_session_nonce()
        with patch.object(session, "_signed_message", side_effect=SimulatedCrash):
            with self.assertRaises(SimulatedCrash):
                self._send(nonce=nonce, http_post=lambda _endpoint, _body: self.fail("network called"))

        delivered: list[tuple[dict[str, object], str]] = []
        future = session._now() + timedelta(seconds=session._LEASE_SECONDS + 1)
        with patch.object(session, "_now", return_value=future):
            result = self._send(nonce=nonce, http_post=lambda _endpoint, body: self._receive(body, delivered))
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(delivered), 1)

    def test_same_nonce_conflict_and_concurrent_attempt_lease(self) -> None:
        from no1.daemon.group_bridge.session import GroupBridgeSessionError, new_group_bridge_session_nonce

        nonce = new_group_bridge_session_nonce()
        entered = threading.Event()
        release = threading.Event()
        delivered: list[tuple[dict[str, object], str]] = []

        def post(endpoint: str, body: dict[str, object]) -> dict[str, object]:
            entered.set()
            self.assertTrue(release.wait(timeout=5))
            return self._receive(body, delivered)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self._send, nonce=nonce, http_post=post)
            self.assertTrue(entered.wait(timeout=5))
            with self.assertRaises(GroupBridgeSessionError) as raised:
                self._send(nonce=nonce, http_post=post)
            self.assertEqual(raised.exception.code, "session_in_progress")
            release.set()
            self.assertEqual(future.result(timeout=5)["status"], "accepted")
        with self.assertRaises(GroupBridgeSessionError) as conflict:
            self._send(nonce=nonce, http_post=post, text="different")
        self.assertEqual(conflict.exception.code, "nonce_conflict")
        self.assertNotIn(nonce, str(conflict.exception))
        self.assertEqual(len(delivered), 1)

    def test_spawn_same_nonce_only_one_process_claims_attempt(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        home = self.root / "spawn"
        start = ctx.Event()
        results = ctx.Queue()
        processes = [
            ctx.Process(target=_spawn_claim_same_nonce, args=(str(home), start, results)) for _ in range(2)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 0)
        observed = sorted(results.get(timeout=5) for _ in processes)
        self.assertEqual(observed, ["sending", "session_in_progress"])

    def test_retry_reauthorizes_and_revocation_prevents_network(self) -> None:
        from no1.daemon.group_bridge.session import GroupBridgeSessionError, new_group_bridge_session_nonce

        nonce = new_group_bridge_session_nonce()
        delivered: list[tuple[dict[str, object], str]] = []
        calls = 0

        def response_lost(endpoint: str, body: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            self._receive(body, delivered)
            raise TimeoutError

        with self.assertRaises(GroupBridgeSessionError):
            self._send(nonce=nonce, http_post=response_lost)
        self.authorized = False
        with self.assertRaises(GroupBridgeSessionError) as raised:
            self._send(nonce=nonce, http_post=response_lost)
        self.assertEqual(raised.exception.code, "unauthorized")
        self.assertEqual(calls, 1)
        self.assertEqual(len(delivered), 1)
        self.assertTrue(all(call["required_access_level"] == "messages" for call in self.auth_calls))

    def test_exact_group_address_resolution_and_endpointless_denial(self) -> None:
        from no1.daemon.group_bridge.session import GroupBridgeSessionError, new_group_bridge_session_nonce

        nonce = new_group_bridge_session_nonce()
        with self.assertRaises(GroupBridgeSessionError) as raised:
            from no1.daemon.group_bridge.session import send_group_bridge_session_message

            send_group_bridge_session_message(
                group_id="g-a",
                local_endpoint=self.endpoint_a,
                remote_group_id="g-b",
                remote_peer_id=self.identity_b.peer_id,
                remote_endpoint="",
                client_nonce=nonce,
                payload=self._payload(),
                home=self.home_a,
                http_post=lambda _endpoint, _body: self.fail("endpointless request reached network"),
            )
        self.assertEqual(raised.exception.code, "transport_unavailable")
        self.assertIn((self.identity_b.peer_id, "g-b", self.home_a), self.address_calls)
        self.assertNotIn((self.identity_b.peer_id, "g-other", self.home_a), self.address_calls)

    def test_network_and_delivery_run_without_session_store_lock(self) -> None:
        from no1.daemon.group_bridge.session import new_group_bridge_session_nonce, session_reservation_path
        from no1.util.file_lock import acquire_lockfile, release_lockfile

        delivered: list[tuple[dict[str, object], str]] = []

        def assert_unlocked(home: Path) -> None:
            from no1.daemon.group_bridge import identity
            from no1.kernel.group_bridge import pairing, peer_addresses

            lock_paths = (
                session_reservation_path(home=home).with_suffix(".json.lock"),
                identity._lock_path(home),
                pairing._lock_path(home),
                peer_addresses._lock_path(home),
            )
            for lock_path in lock_paths:
                lock = acquire_lockfile(lock_path, blocking=False)
                release_lockfile(lock)

        def deliver(payload: dict[str, object], delivery_id: str) -> str:
            assert_unlocked(self.home_b)
            return self._deliver(delivered, payload, delivery_id)

        def post(endpoint: str, body: dict[str, object]) -> dict[str, object]:
            from no1.daemon.group_bridge.session import receive_group_bridge_session_message

            assert_unlocked(self.home_a)
            return receive_group_bridge_session_message(
                body,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=deliver,
            )

        result = self._send(nonce=new_group_bridge_session_nonce(), http_post=post)
        self.assertEqual(result["status"], "accepted")

    def test_canonical_home_survives_symlink_retarget_after_authorization(self) -> None:
        from no1.daemon.group_bridge import session

        alias = self.root / "alias"
        alias.symlink_to(self.home_a, target_is_directory=True)
        auth_homes: list[Path] = []

        def authorize(**kwargs: object) -> dict[str, object]:
            auth_homes.append(Path(kwargs["home"]))
            alias.unlink()
            alias.symlink_to(self.home_b, target_is_directory=True)
            return {"remote_endpoint": kwargs["remote_endpoint"]}

        with patch.object(session, "authorize_remote_principal", side_effect=authorize):
            with self.assertRaises(session.GroupBridgeSessionError):
                session.send_group_bridge_session_message(
                    group_id="g-a",
                    local_endpoint=self.endpoint_a,
                    remote_group_id="g-b",
                    remote_peer_id=self.identity_b.peer_id,
                    remote_endpoint=self.endpoint_b,
                    client_nonce=session.new_group_bridge_session_nonce(),
                    payload=self._payload(),
                    home=alias,
                    http_post=lambda _endpoint, _body: (_ for _ in ()).throw(TimeoutError()),
                )
        self.assertEqual(auth_homes, [self.home_a.resolve()])
        self.assertTrue(session.session_reservation_path(home=self.home_a).exists())
        self.assertFalse(session.session_reservation_path(home=self.home_b).exists())

    def test_signature_target_timestamp_and_tamper_matrix(self) -> None:
        from no1.daemon.group_bridge import identity as identity_module
        from no1.daemon.group_bridge import session

        delivered: list[tuple[dict[str, object], str]] = []
        valid = self._signed_request(nonce=session.new_group_bridge_session_nonce())

        invalid_cases: list[tuple[str, dict[str, object], str]] = []
        tampered_payload = copy.deepcopy(valid)
        tampered_payload["payload"]["text"] = "tampered"  # type: ignore[index]
        invalid_cases.append(("payload", tampered_payload, "invalid_signature"))
        wrong_target = copy.deepcopy(valid)
        wrong_target["target_peer_id"] = self.identity_a.peer_id
        invalid_cases.append(("target", wrong_target, "invalid_target"))
        extra = copy.deepcopy(valid)
        extra["credential"] = "must-not-be-accepted"
        invalid_cases.append(("extra", extra, "invalid_envelope"))
        bad_signature = copy.deepcopy(valid)
        bad_signature["signature"] = ("A" * 86) + "=="
        invalid_cases.append(("signature", bad_signature, "invalid_signature"))

        for name, envelope, code in invalid_cases:
            with self.subTest(name=name), self.assertRaises(session.GroupBridgeSessionError) as raised:
                session.receive_group_bridge_session_message(
                    envelope,
                    group_id="g-b",
                    local_endpoint=self.endpoint_b,
                    home=self.home_b,
                    deliver=lambda payload, delivery_id: self._deliver(delivered, payload, delivery_id),
                )
            self.assertEqual(raised.exception.code, code)

        old = session._timestamp(session._now() - timedelta(seconds=301))
        stale = self._signed_request(nonce=session.new_group_bridge_session_nonce(), issued_at=old)
        with self.assertRaises(session.GroupBridgeSessionError) as raised:
            session.receive_group_bridge_session_message(
                stale,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=lambda payload, delivery_id: self._deliver(delivered, payload, delivery_id),
            )
        self.assertEqual(raised.exception.code, "stale_request")
        self.assertEqual(delivered, [])

        unsigned = {key: value for key, value in valid.items() if key != "signature"}
        self.assertTrue(
            identity_module.verify_group_bridge_signature(
                identity_module.canonical_payload_bytes(unsigned),
                valid["signature"],
                public_key_b64=valid["source_public_key"],
                peer_id=valid["source_peer_id"],
            )
        )

    def test_terminal_inbound_replay_can_answer_stale_request_but_still_reauthorizes(self) -> None:
        from no1.daemon.group_bridge import session

        nonce = session.new_group_bridge_session_nonce()
        delivered: list[tuple[dict[str, object], str]] = []
        fresh = self._signed_request(nonce=nonce)
        first = self._receive(fresh, delivered)
        old = session._timestamp(session._now() - timedelta(days=1))
        stale_replay = self._signed_request(nonce=nonce, issued_at=old)
        second = self._receive(stale_replay, delivered)
        self.assertEqual(first["remote_event_id"], second["remote_event_id"])
        self.assertEqual(len(delivered), 1)

        self.authorized = False
        with self.assertRaises(session.GroupBridgeSessionError) as raised:
            self._receive(stale_replay, delivered)
        self.assertEqual(raised.exception.code, "unauthorized")
        self.assertEqual(len(delivered), 1)

    def test_closed_contract_rejects_bool_version_and_unbounded_fields(self) -> None:
        from no1.contracts.v1.group_bridge import GroupBridgeSessionMessage, GroupBridgeSignedMessageEnvelope
        from no1.daemon.group_bridge import session

        valid = self._signed_request(nonce=session.new_group_bridge_session_nonce())
        for name, patch_value in (
            ("version", True),
            ("source_group_id", "g" * 257),
            ("source_endpoint", "https://" + ("a" * 2048)),
        ):
            candidate = copy.deepcopy(valid)
            candidate[name] = patch_value
            with self.subTest(name=name), self.assertRaises(Exception):
                GroupBridgeSignedMessageEnvelope.model_validate(candidate)

        bypassed = GroupBridgeSessionMessage.model_construct(
            text="hello",
            format="plain",
            priority="normal",
            reply_required="yes",
        )
        with self.assertRaises(session.GroupBridgeSessionError) as raised:
            session.send_group_bridge_session_message(
                group_id="g-a",
                local_endpoint=self.endpoint_a,
                remote_group_id="g-b",
                remote_peer_id=self.identity_b.peer_id,
                remote_endpoint=self.endpoint_b,
                client_nonce=session.new_group_bridge_session_nonce(),
                payload=bypassed,
                home=self.home_a,
                http_post=lambda _endpoint, _body: self.fail("invalid model reached network"),
            )
        self.assertEqual(raised.exception.code, "invalid_request")

    def test_explicit_verification_never_loads_local_private_identity(self) -> None:
        from no1.daemon.group_bridge import identity as identity_module

        payload = b"explicit remote payload"
        signature = identity_module.sign_group_bridge_payload(payload, home=self.home_a)
        with patch.object(identity_module, "_load_unlocked", side_effect=AssertionError("private identity read")):
            self.assertTrue(
                identity_module.verify_group_bridge_signature(
                    payload,
                    signature,
                    public_key_b64=self.identity_a.public_key_b64,
                    peer_id=self.identity_a.peer_id,
                )
            )
            self.assertFalse(
                identity_module.verify_group_bridge_signature(
                    payload + b"!",
                    signature,
                    public_key_b64=self.identity_a.public_key_b64,
                    peer_id=self.identity_a.peer_id,
                )
            )
            alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
            public_index = alphabet.index(self.identity_a.public_key_b64[-2])
            noncanonical_public = self.identity_a.public_key_b64[:-2] + alphabet[public_index + 1] + "="
            self.assertEqual(
                identity_module.peer_id_from_public_key_b64(noncanonical_public),
                self.identity_a.peer_id,
            )
            self.assertFalse(
                identity_module.verify_group_bridge_signature(
                    payload,
                    signature,
                    public_key_b64=noncanonical_public,
                    peer_id=self.identity_a.peer_id,
                )
            )
            signature_index = alphabet.index(signature[-3])
            noncanonical_signature = signature[:-3] + alphabet[signature_index + 1] + "=="
            self.assertEqual(
                base64.b64decode(noncanonical_signature),
                base64.b64decode(signature),
            )
            self.assertFalse(
                identity_module.verify_group_bridge_signature(
                    payload,
                    noncanonical_signature,
                    public_key_b64=self.identity_a.public_key_b64,
                    peer_id=self.identity_a.peer_id,
                )
            )

    def test_corrupt_store_fails_closed_and_mutation_preserves_bytes(self) -> None:
        from no1.daemon.group_bridge.session import (
            GroupBridgeSessionStoreError,
            load_session_reservations,
            new_group_bridge_session_nonce,
            session_reservation_path,
        )

        path = session_reservation_path(home=self.home_a)
        path.parent.mkdir(parents=True, exist_ok=True)
        malformed_documents = (
            b'{"version":1,"version":1,"outbound":{},"inbound":{}}\n',
            b'{"version":true,"outbound":{},"inbound":{}}\n',
        )
        for malformed in malformed_documents:
            with self.subTest(malformed=malformed):
                path.write_bytes(malformed)
                with self.assertRaises(GroupBridgeSessionStoreError):
                    load_session_reservations(home=self.home_a)
                with self.assertRaises(GroupBridgeSessionStoreError):
                    self._send(
                        nonce=new_group_bridge_session_nonce(),
                        http_post=lambda _endpoint, _body: self.fail("corrupt store reached network"),
                    )
                self.assertEqual(path.read_bytes(), malformed)

    def test_receipt_tamper_is_retryable_and_never_accepted(self) -> None:
        from no1.daemon.group_bridge.session import GroupBridgeSessionError, new_group_bridge_session_nonce

        delivered: list[tuple[dict[str, object], str]] = []

        def post(endpoint: str, body: dict[str, object]) -> dict[str, object]:
            receipt = self._receive(body, delivered)
            receipt["remote_event_id"] = "attacker-event"
            return receipt

        with self.assertRaises(GroupBridgeSessionError) as raised:
            self._send(nonce=new_group_bridge_session_nonce(), http_post=post)
        self.assertEqual(raised.exception.code, "invalid_response")
        self.assertTrue(raised.exception.retriable)
        self.assertEqual(len(delivered), 1)

    def test_signed_receipt_with_invalid_event_identity_is_invalid_response(self) -> None:
        from no1.daemon.group_bridge import identity as identity_module
        from no1.daemon.group_bridge.session import GroupBridgeSessionError, new_group_bridge_session_nonce

        delivered: list[tuple[dict[str, object], str]] = []

        def post(endpoint: str, body: dict[str, object]) -> dict[str, object]:
            receipt = self._receive(body, delivered)
            receipt["remote_event_id"] = " bad-event "
            unsigned = {key: value for key, value in receipt.items() if key != "signature"}
            receipt["signature"] = identity_module.sign_group_bridge_payload(
                identity_module.canonical_payload_bytes(unsigned), home=self.home_b
            )
            return receipt

        with self.assertRaises(GroupBridgeSessionError) as raised:
            self._send(nonce=new_group_bridge_session_nonce(), http_post=post)
        self.assertEqual(raised.exception.code, "invalid_response")
        self.assertTrue(raised.exception.retriable)

    def test_default_http_stream_enforces_declared_and_chunked_limits(self) -> None:
        from no1.daemon.group_bridge import session

        valid_body = b'{"status":"ok"}'
        valid = _FakeStreamResponse(
            chunks=[valid_body[:5], valid_body[5:]],
            content_length=str(len(valid_body)),
            content_encoding="identity",
        )
        with patch.object(session.httpx, "stream", return_value=valid) as stream:
            self.assertEqual(session._default_http_post(self.endpoint_b, {}), {"status": "ok"})
        self.assertEqual(stream.call_args.kwargs["headers"], {"Accept-Encoding": "identity"})
        self.assertEqual(valid.read_count, 2)
        self.assertEqual(valid.raw_chunk_sizes, [session._HTTP_RAW_CHUNK_BYTES])
        self.assertTrue(valid.closed)

        declared = _FakeStreamResponse(
            chunks=[b"must-not-be-read"],
            content_length=str(session._MAX_RESPONSE_BYTES + 1),
        )
        with patch.object(session.httpx, "stream", return_value=declared):
            with self.assertRaises(session.GroupBridgeSessionError) as raised:
                session._default_http_post(self.endpoint_b, {})
        self.assertEqual(raised.exception.code, "invalid_response")
        self.assertTrue(raised.exception.retriable)
        self.assertEqual(declared.read_count, 0)
        self.assertTrue(declared.closed)

        chunked = _FakeStreamResponse(
            chunks=[b"a" * session._MAX_RESPONSE_BYTES, b"b", b"must-not-be-read"],
        )
        with patch.object(session.httpx, "stream", return_value=chunked):
            with self.assertRaises(session.GroupBridgeSessionError) as raised:
                session._default_http_post(self.endpoint_b, {})
        self.assertEqual(raised.exception.code, "invalid_response")
        self.assertTrue(raised.exception.retriable)
        self.assertEqual(chunked.read_count, 2)
        self.assertEqual(chunked.raw_chunk_sizes, [session._HTTP_RAW_CHUNK_BYTES])
        self.assertTrue(chunked.closed)

    def test_default_http_rejects_compressed_responses_before_body_iteration(self) -> None:
        from no1.daemon.group_bridge import session

        expanded = b"x" * (session._MAX_RESPONSE_BYTES * 4)
        compressed = gzip.compress(expanded)
        self.assertLess(len(compressed), session._MAX_RESPONSE_BYTES)
        bomb = _FakeStreamResponse(
            chunks=[compressed],
            content_length=str(len(compressed)),
            content_encoding="gzip",
        )
        with patch.object(session.httpx, "stream", return_value=bomb):
            with self.assertRaises(session.GroupBridgeSessionError) as raised:
                session._default_http_post(self.endpoint_b, {})
        self.assertEqual(raised.exception.code, "invalid_response")
        self.assertTrue(raised.exception.retriable)
        self.assertEqual(bomb.read_count, 0)
        self.assertEqual(bomb.raw_chunk_sizes, [])
        self.assertTrue(bomb.closed)

        for encoding in ("Identity", " identity", "identity ", "gzip, identity"):
            response = _FakeStreamResponse(
                chunks=[b'must-not-be-read'],
                content_encoding=encoding,
            )
            with self.subTest(content_encoding=encoding):
                with patch.object(session.httpx, "stream", return_value=response):
                    with self.assertRaises(session.GroupBridgeSessionError) as raised:
                        session._default_http_post(self.endpoint_b, {})
                self.assertEqual(raised.exception.code, "invalid_response")
                self.assertEqual(response.read_count, 0)
                self.assertEqual(response.raw_chunk_sizes, [])
                self.assertTrue(response.closed)

    def test_default_http_invalid_utf8_is_invalid_response(self) -> None:
        from no1.daemon.group_bridge import session

        response = _FakeStreamResponse(
            chunks=[b'{"status":"\xff"}'],
            content_encoding="identity",
        )
        with patch.object(session.httpx, "stream", return_value=response):
            with self.assertRaises(session.GroupBridgeSessionError) as raised:
                session._default_http_post(self.endpoint_b, {})
        self.assertEqual(raised.exception.code, "invalid_response")
        self.assertTrue(raised.exception.retriable)
        self.assertEqual(response.read_count, 1)

        retry_response = _FakeStreamResponse(chunks=[b"\xff"])
        with patch.object(session.httpx, "stream", return_value=retry_response):
            with self.assertRaises(session.GroupBridgeSessionError) as raised:
                self._send(nonce=session.new_group_bridge_session_nonce(), http_post=None)
        self.assertEqual(raised.exception.code, "invalid_response")
        outbound = session.load_session_reservations(home=self.home_a)["outbound"]
        self.assertEqual(next(iter(outbound.values()))["error_code"], "invalid_response")

    def test_oversized_store_and_utf8_write_are_bounded_before_atomic_replace(self) -> None:
        from no1.daemon.group_bridge import session

        path = session.session_reservation_path(home=self.home_a)
        path.parent.mkdir(parents=True, exist_ok=True)
        oversized = b"{" + (b" " * session._MAX_STORE_BYTES)
        path.write_bytes(oversized)
        before = hashlib.sha256(oversized).hexdigest()
        with self.assertRaises(session.GroupBridgeSessionStoreError):
            session.load_session_reservations(home=self.home_a)
        with patch.object(session, "sign_group_bridge_payload") as sign:
            with self.assertRaises(session.GroupBridgeSessionStoreError):
                self._send(
                    nonce=session.new_group_bridge_session_nonce(),
                    http_post=lambda _endpoint, _body: self.fail("oversized store reached network"),
                )
        sign.assert_not_called()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

        candidate_home = self.root / "utf8-write"
        nonce_hash, _entry = self._write_state(
            home=candidate_home,
            direction="outbound",
            status="accepted",
            label="utf8",
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        candidate = session.load_session_reservations(home=candidate_home)
        candidate["outbound"][nonce_hash]["remote_event_id"] = "界" * 512
        encoded = json.dumps(session._validate_store(candidate), ensure_ascii=False, indent=2) + "\n"
        self.assertGreater(len(encoded.encode("utf-8")), len(encoded))
        with (
            patch.object(session, "_MAX_STORE_BYTES", len(encoded.encode("utf-8")) - 1),
            patch.object(session, "atomic_write_text") as atomic_write,
            self.assertRaises(session.GroupBridgeSessionError) as raised,
        ):
            session._save_store(candidate, candidate_home)
        self.assertEqual(raised.exception.code, "session_capacity")
        atomic_write.assert_not_called()

    def test_capacity_rejects_flood_without_evicting_live_entries_or_side_effects(self) -> None:
        from no1.daemon.group_bridge import session

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 2):
            self._write_state(
                home=self.home_a,
                direction="outbound",
                status="sending",
                label="live-1",
                now=now,
            )
            self._write_state(
                home=self.home_a,
                direction="outbound",
                status="sending",
                label="live-2",
                now=now,
            )
            path = session.session_reservation_path(home=self.home_a)
            before = path.read_bytes()
            before_sha = hashlib.sha256(before).hexdigest()
            network_calls = 0

            def network(_endpoint: str, _body: dict[str, object]) -> dict[str, object]:
                nonlocal network_calls
                network_calls += 1
                return {}

            with patch.object(session, "_now", return_value=now), patch.object(
                session, "sign_group_bridge_payload"
            ) as sign:
                with self.assertRaises(session.GroupBridgeSessionError) as raised:
                    self._send(
                        nonce=session.new_group_bridge_session_nonce(),
                        http_post=network,
                    )
            self.assertEqual(raised.exception.code, "session_capacity")
            self.assertTrue(raised.exception.retriable)
            sign.assert_not_called()
            self.assertEqual(network_calls, 0)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before_sha)
            self.assertEqual(len(session.load_session_reservations(home=self.home_a)["outbound"]), 2)

    def test_persisted_entry_flood_is_semantic_corruption(self) -> None:
        from no1.daemon.group_bridge import session

        home = self.root / "persisted-flood"
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 3):
            for index in range(3):
                self._write_state(
                    home=home,
                    direction="outbound",
                    status="reserved",
                    label=f"flood-{index}",
                    now=now,
                )
        path = session.session_reservation_path(home=home)
        before = path.read_bytes()
        with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 2):
            with self.assertRaises(session.GroupBridgeSessionStoreError):
                session.load_session_reservations(home=home)
            with self.assertRaises(session.GroupBridgeSessionStoreError):
                session._reserve(
                    direction="outbound",
                    nonce_hash="a" * 64,
                    fingerprint="b" * 64,
                    home=home,
                )
        self.assertEqual(path.read_bytes(), before)

    def test_retention_boundaries_include_terminal_inactive_and_active_grace(self) -> None:
        from no1.daemon.group_bridge import session

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        cases = (
            ("accepted", "outbound", session._TERMINAL_RETENTION_SECONDS, "updated_at"),
            ("retrying", "outbound", session._INACTIVE_RETENTION_SECONDS, "updated_at"),
            ("reserved", "inbound", session._INACTIVE_RETENTION_SECONDS, "updated_at"),
            ("sending", "outbound", session._EXPIRED_LEASE_RETENTION_SECONDS, "lease_expires_at"),
            ("delivering", "inbound", session._EXPIRED_LEASE_RETENTION_SECONDS, "lease_expires_at"),
        )
        for index, (status, direction, retention, anchor_field) in enumerate(cases):
            home = self.root / f"retention-{index}"
            _key, entry = self._write_state(
                home=home,
                direction=direction,
                status=status,
                label=f"retention-{index}",
                now=now,
            )
            anchor = session._parse_timestamp(entry[anchor_field])
            self.assertIsNotNone(anchor)
            assert anchor is not None
            with self.subTest(status=status, boundary="minus-one"):
                self.assertFalse(session._reclaimable(entry, now=anchor + timedelta(seconds=retention - 1)))
            with self.subTest(status=status, boundary="exact"):
                self.assertTrue(session._reclaimable(entry, now=anchor + timedelta(seconds=retention)))
            with self.subTest(status=status, boundary="plus-one"):
                self.assertTrue(session._reclaimable(entry, now=anchor + timedelta(seconds=retention + 1)))

    def test_elapsed_reclamation_is_total_at_datetime_limits_and_boundaries(self) -> None:
        from no1.daemon.group_bridge import session

        minimum = datetime.min.replace(tzinfo=timezone.utc)
        maximum = datetime.max.replace(tzinfo=timezone.utc)
        self.assertFalse(session._elapsed_at_least(anchor=maximum, now=minimum, seconds=1))
        self.assertTrue(session._elapsed_at_least(anchor=minimum, now=maximum, seconds=1))
        anchor = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for retention in (
            session._TERMINAL_RETENTION_SECONDS,
            session._INACTIVE_RETENTION_SECONDS,
            session._EXPIRED_LEASE_RETENTION_SECONDS,
        ):
            with self.subTest(retention=retention, boundary="minus-one"):
                self.assertFalse(
                    session._elapsed_at_least(
                        anchor=anchor,
                        now=anchor + timedelta(seconds=retention - 1),
                        seconds=retention,
                    )
                )
            with self.subTest(retention=retention, boundary="exact"):
                self.assertTrue(
                    session._elapsed_at_least(
                        anchor=anchor,
                        now=anchor + timedelta(seconds=retention),
                        seconds=retention,
                    )
                )
            with self.subTest(retention=retention, boundary="plus-one"):
                self.assertTrue(
                    session._elapsed_at_least(
                        anchor=anchor,
                        now=anchor + timedelta(seconds=retention + 1),
                        seconds=retention,
                    )
                )

    def test_future_max_nonactive_entries_are_valid_capacity_owners(self) -> None:
        from no1.daemon.group_bridge import session

        maximum = datetime.max.replace(tzinfo=timezone.utc)
        maximum_text = session._timestamp(maximum)
        for direction in ("outbound", "inbound"):
            for status in ("reserved", "retrying", "accepted"):
                with self.subTest(direction=direction, status=status):
                    home = self.root / f"future-max-{direction}-{status}"
                    key, _entry = self._write_state(
                        home=home,
                        direction=direction,
                        status=status,
                        label=f"future-max-{direction}-{status}",
                        now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    )
                    store = session.load_session_reservations(home=home)
                    stored = store[direction][key]
                    stored["created_at"] = maximum_text
                    stored["updated_at"] = maximum_text
                    path = session.session_reservation_path(home=home)
                    path.write_text(json.dumps(store), encoding="utf-8")
                    before = path.read_bytes()
                    loaded = session.load_session_reservations(home=home)[direction][key]
                    self.assertFalse(session._reclaimable(loaded, now=maximum))

                    with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 1):
                        with self.assertRaises(session.GroupBridgeSessionError) as raised:
                            session._reserve(
                                direction=direction,
                                nonce_hash="a" * 64,
                                fingerprint="b" * 64,
                                home=home,
                            )
                        self.assertEqual(raised.exception.code, "session_capacity")
                        request = None
                        if direction == "inbound":
                            local_identity = session.get_group_bridge_identity(home=home)
                            request = self._signed_request(
                                nonce=session.new_group_bridge_session_nonce(),
                                target_peer_id=local_identity.peer_id,
                            )
                        with patch.object(session, "sign_group_bridge_payload") as sign:
                            if direction == "outbound":
                                network_calls = 0

                                def network(_endpoint: str, _body: dict[str, object]) -> dict[str, object]:
                                    nonlocal network_calls
                                    network_calls += 1
                                    return {}

                                with self.assertRaises(session.GroupBridgeSessionError) as raised:
                                    session.send_group_bridge_session_message(
                                        group_id="g-a",
                                        local_endpoint=self.endpoint_a,
                                        remote_group_id="g-b",
                                        remote_peer_id=self.identity_b.peer_id,
                                        remote_endpoint=self.endpoint_b,
                                        client_nonce=session.new_group_bridge_session_nonce(),
                                        payload=self._payload(),
                                        home=home,
                                        http_post=network,
                                    )
                                self.assertEqual(network_calls, 0)
                            else:
                                deliveries = 0
                                assert request is not None

                                def deliver(_payload: dict[str, object], _delivery_id: str) -> str:
                                    nonlocal deliveries
                                    deliveries += 1
                                    return "event-impossible"

                                with self.assertRaises(session.GroupBridgeSessionError) as raised:
                                    session.receive_group_bridge_session_message(
                                        request,
                                        group_id="g-b",
                                        local_endpoint=self.endpoint_b,
                                        home=home,
                                        deliver=deliver,
                                    )
                                self.assertEqual(deliveries, 0)
                        self.assertEqual(raised.exception.code, "session_capacity")
                        sign.assert_not_called()
                    self.assertEqual(path.read_bytes(), before)

    def test_active_extreme_times_validate_without_overflow(self) -> None:
        from no1.daemon.group_bridge import session

        minimum = datetime.min.replace(tzinfo=timezone.utc)
        maximum = datetime.max.replace(tzinfo=timezone.utc)
        active_statuses = {"outbound": "sending", "inbound": "delivering"}
        for direction, status in active_statuses.items():
            with self.subTest(direction=direction, case="near-max"):
                home = self.root / f"active-near-max-{direction}"
                key, _entry = self._write_state(
                    home=home,
                    direction=direction,
                    status=status,
                    label=f"active-near-max-{direction}",
                    now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
                store = session.load_session_reservations(home=home)
                entry = store[direction][key]
                updated = maximum - timedelta(seconds=session._LEASE_SECONDS)
                entry["created_at"] = session._timestamp(updated)
                entry["updated_at"] = session._timestamp(updated)
                entry["lease_expires_at"] = session._timestamp(maximum)
                path = session.session_reservation_path(home=home)
                path.write_text(json.dumps(store), encoding="utf-8")
                loaded = session.load_session_reservations(home=home)[direction][key]
                self.assertFalse(session._reclaimable(loaded, now=maximum))

            with self.subTest(direction=direction, case="unrepresentable-max"):
                entry["created_at"] = session._timestamp(maximum)
                entry["updated_at"] = session._timestamp(maximum)
                entry["lease_expires_at"] = session._timestamp(maximum)
                path.write_text(json.dumps(store), encoding="utf-8")
                before = path.read_bytes()
                with self.assertRaises(session.GroupBridgeSessionStoreError):
                    session.load_session_reservations(home=home)
                with self.assertRaises(session.GroupBridgeSessionStoreError):
                    session._reserve(
                        direction=direction,
                        nonce_hash="c" * 64,
                        fingerprint="d" * 64,
                        home=home,
                    )
                self.assertEqual(path.read_bytes(), before)

            with self.subTest(direction=direction, case="minimum"):
                entry["created_at"] = session._timestamp(minimum)
                entry["updated_at"] = session._timestamp(minimum)
                entry["lease_expires_at"] = session._timestamp(
                    minimum + timedelta(seconds=session._LEASE_SECONDS)
                )
                path.write_text(json.dumps(store), encoding="utf-8")
                loaded = session.load_session_reservations(home=home)[direction][key]
                self.assertTrue(session._reclaimable(loaded, now=maximum))

            with self.subTest(direction=direction, case="writer-max"):
                home = self.root / f"active-writer-max-{direction}"
                key, _entry = self._write_state(
                    home=home,
                    direction=direction,
                    status="reserved",
                    label=f"active-writer-max-{direction}",
                    now=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
                path = session.session_reservation_path(home=home)
                before = path.read_bytes()
                with patch.object(session, "_now", return_value=maximum):
                    with self.assertRaises(session.GroupBridgeSessionStoreError):
                        session._claim_attempt(direction=direction, nonce_hash=key, home=home)
                self.assertEqual(path.read_bytes(), before)

    def test_terminal_retention_and_post_eviction_replay_contract(self) -> None:
        from no1.daemon.group_bridge import session

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old_nonce = session.new_group_bridge_session_nonce()
        delivery_ids: list[str] = []

        def deliver(_payload: dict[str, object], delivery_id: str) -> str:
            delivery_ids.append(delivery_id)
            return f"event-{len(delivery_ids)}"

        with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 1), patch.object(session, "_now", return_value=start):
            old_request = self._signed_request(nonce=old_nonce)
            first = session.receive_group_bridge_session_message(
                old_request,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=deliver,
            )

        before_retention = start + timedelta(seconds=session._TERMINAL_RETENTION_SECONDS - 1)
        with (
            patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 1),
            patch.object(session, "_now", return_value=before_retention),
        ):
            replay = session.receive_group_bridge_session_message(
                old_request,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=deliver,
            )
        self.assertEqual(first["request_fingerprint"], replay["request_fingerprint"])
        self.assertEqual(len(delivery_ids), 1)

        eviction_time = start + timedelta(seconds=session._TERMINAL_RETENTION_SECONDS)
        with (
            patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 1),
            patch.object(session, "_now", return_value=eviction_time),
        ):
            replacement = self._signed_request(nonce=session.new_group_bridge_session_nonce())
            session.receive_group_bridge_session_message(
                replacement,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=deliver,
            )
        self.assertEqual(len(delivery_ids), 2)
        path = session.session_reservation_path(home=self.home_b)
        before_stale = path.read_bytes()
        with (
            patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 2),
            patch.object(session, "_now", return_value=eviction_time),
            self.assertRaises(session.GroupBridgeSessionError) as raised,
        ):
            session.receive_group_bridge_session_message(
                old_request,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=deliver,
            )
        self.assertEqual(raised.exception.code, "stale_request")
        self.assertEqual(path.read_bytes(), before_stale)
        self.assertEqual(len(delivery_ids), 2)

        with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 2), patch.object(
            session, "_now", return_value=eviction_time
        ):
            fresh_reuse = self._signed_request(nonce=old_nonce)
            reused = session.receive_group_bridge_session_message(
                fresh_reuse,
                group_id="g-b",
                local_endpoint=self.endpoint_b,
                home=self.home_b,
                deliver=deliver,
            )
        self.assertEqual(first["request_fingerprint"], reused["request_fingerprint"])
        self.assertEqual(delivery_ids[0], delivery_ids[2])
        self.assertEqual(len(delivery_ids), 3)

    def test_expired_active_and_retrying_entries_reclaim_only_at_boundary(self) -> None:
        from no1.daemon.group_bridge import session

        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 1):
            active_key, active = self._write_state(
                home=self.home_a,
                direction="outbound",
                status="sending",
                label="active-boundary",
                now=start,
            )
            lease = session._parse_timestamp(active["lease_expires_at"])
            assert lease is not None
            before_boundary = lease + timedelta(seconds=session._EXPIRED_LEASE_RETENTION_SECONDS - 1)
            path = session.session_reservation_path(home=self.home_a)
            before = path.read_bytes()
            with patch.object(session, "_now", return_value=before_boundary):
                with self.assertRaises(session.GroupBridgeSessionError) as raised:
                    session._reserve(
                        direction="outbound",
                        nonce_hash="c" * 64,
                        fingerprint="d" * 64,
                        home=self.home_a,
                    )
            self.assertEqual(raised.exception.code, "session_capacity")
            self.assertEqual(path.read_bytes(), before)
            exact = lease + timedelta(seconds=session._EXPIRED_LEASE_RETENTION_SECONDS)
            with patch.object(session, "_now", return_value=exact):
                session._reserve(
                    direction="outbound",
                    nonce_hash="c" * 64,
                    fingerprint="d" * 64,
                    home=self.home_a,
                )
            self.assertNotIn(active_key, session.load_session_reservations(home=self.home_a)["outbound"])

        retry_home = self.root / "retry-retention"
        with patch.object(session, "_MAX_ENTRIES_PER_DIRECTION", 1):
            retry_key, retry = self._write_state(
                home=retry_home,
                direction="outbound",
                status="retrying",
                label="retry-boundary",
                now=start,
            )
            updated = session._parse_timestamp(retry["updated_at"])
            assert updated is not None
            exact = updated + timedelta(seconds=session._INACTIVE_RETENTION_SECONDS)
            with patch.object(session, "_now", return_value=exact):
                session._reserve(
                    direction="outbound",
                    nonce_hash="e" * 64,
                    fingerprint="f" * 64,
                    home=retry_home,
                )
            self.assertNotIn(retry_key, session.load_session_reservations(home=retry_home)["outbound"])

    def test_every_writer_state_reloads_through_the_same_semantic_validator(self) -> None:
        from no1.daemon.group_bridge import session

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for direction, statuses in (
            ("outbound", ("reserved", "sending", "retrying", "accepted")),
            ("inbound", ("reserved", "delivering", "retrying", "accepted")),
        ):
            for status in statuses:
                with self.subTest(direction=direction, status=status):
                    home = self.root / f"writer-{direction}-{status}"
                    key, entry = self._write_state(
                        home=home,
                        direction=direction,
                        status=status,
                        label=f"{direction}-{status}",
                        now=now,
                    )
                    reloaded = session.load_session_reservations(home=home)[direction][key]
                    self.assertEqual(reloaded, entry)
                    self.assertEqual(reloaded["attempt"], reloaded["revision"])

    def test_single_field_and_state_tamper_matrix_fails_closed(self) -> None:
        from no1.daemon.group_bridge import session

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def set_value(name: str, value: object):
            return lambda entry: entry.__setitem__(name, value)

        tamper_cases = (
            ("accepted", set_value("direction", "inbound")),
            ("accepted", set_value("nonce_hash", "a" * 64)),
            ("accepted", set_value("request_fingerprint", "A" * 64)),
            ("accepted", set_value("status", "sending")),
            ("accepted", set_value("attempt", 2)),
            ("accepted", set_value("revision", True)),
            (
                "accepted",
                lambda entry: entry.update(
                    attempt=session._MAX_ATTEMPTS + 1,
                    revision=session._MAX_ATTEMPTS + 1,
                ),
            ),
            ("accepted", set_value("lease_expires_at", "2030-01-01T00:00:00Z")),
            ("accepted", set_value("remote_event_id", "")),
            ("accepted", set_value("remote_event_id", "bad\nevent")),
            ("accepted", set_value("remote_event_id", "x" * 513)),
            ("accepted", set_value("error_code", "transport_error")),
            ("accepted", set_value("created_at", "2030-01-01T00:00:00Z")),
            ("reserved", set_value("updated_at", "2026-01-01T00:00:01Z")),
            ("sending", set_value("lease_expires_at", "")),
            ("sending", set_value("lease_expires_at", "2030-01-01T00:00:00Z")),
            ("retrying", set_value("error_code", "")),
            ("retrying", set_value("error_code", "delivery_failed")),
            ("retrying", set_value("remote_event_id", "event-impossible")),
            ("accepted", set_value("unexpected", "field")),
        )
        for index, (status, tamper) in enumerate(tamper_cases):
            with self.subTest(index=index, status=status):
                home = self.root / f"tamper-{index}"
                key, _entry = self._write_state(
                    home=home,
                    direction="outbound",
                    status=status,
                    label=f"tamper-{index}",
                    now=now,
                )
                store = session.load_session_reservations(home=home)
                tamper(store["outbound"][key])
                path = session.session_reservation_path(home=home)
                path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
                before = path.read_bytes()
                with self.assertRaises(session.GroupBridgeSessionStoreError):
                    session.load_session_reservations(home=home)
                with self.assertRaises(session.GroupBridgeSessionStoreError):
                    session._reserve(
                        direction="outbound",
                        nonce_hash="e" * 64,
                        fingerprint="f" * 64,
                        home=home,
                    )
                self.assertEqual(path.read_bytes(), before)

        inbound_home = self.root / "tamper-inbound-error"
        inbound_key, _entry = self._write_state(
            home=inbound_home,
            direction="inbound",
            status="retrying",
            label="tamper-inbound-error",
            now=now,
        )
        inbound_store = session.load_session_reservations(home=inbound_home)
        inbound_store["inbound"][inbound_key]["error_code"] = "transport_error"
        inbound_path = session.session_reservation_path(home=inbound_home)
        inbound_path.write_text(json.dumps(inbound_store), encoding="utf-8")
        with self.assertRaises(session.GroupBridgeSessionStoreError):
            session.load_session_reservations(home=inbound_home)

    def test_all_store_entry_points_reject_corruption_before_side_effects(self) -> None:
        from no1.daemon.group_bridge import session

        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        key, _entry = self._write_state(
            home=self.home_a,
            direction="outbound",
            status="accepted",
            label="all-entry-points-out",
            now=now,
        )
        path = session.session_reservation_path(home=self.home_a)
        store = session.load_session_reservations(home=self.home_a)
        store["outbound"][key]["attempt"] = 2
        path.write_text(json.dumps(store), encoding="utf-8")
        before = path.read_bytes()

        operations = (
            lambda: session.load_session_reservations(home=self.home_a),
            lambda: session._reserve(
                direction="outbound",
                nonce_hash="a" * 64,
                fingerprint="b" * 64,
                home=self.home_a,
            ),
            lambda: session._claim_attempt(direction="outbound", nonce_hash=key, home=self.home_a),
            lambda: session._finish_attempt(
                direction="outbound",
                nonce_hash=key,
                revision=1,
                home=self.home_a,
                error_code="transport_error",
            ),
        )
        for operation in operations:
            with self.assertRaises(session.GroupBridgeSessionStoreError):
                operation()
            self.assertEqual(path.read_bytes(), before)

        auth_before = len(self.auth_calls)
        network_calls = 0

        def network(_endpoint: str, _body: dict[str, object]) -> dict[str, object]:
            nonlocal network_calls
            network_calls += 1
            return {}

        with patch.object(session, "sign_group_bridge_payload") as sign:
            with self.assertRaises(session.GroupBridgeSessionStoreError):
                self._send(nonce=session.new_group_bridge_session_nonce(), http_post=network)
        self.assertGreater(len(self.auth_calls), auth_before)
        sign.assert_not_called()
        self.assertEqual(network_calls, 0)
        self.assertEqual(path.read_bytes(), before)

        request = self._signed_request(nonce=session.new_group_bridge_session_nonce())
        inbound_key, _entry = self._write_state(
            home=self.home_b,
            direction="inbound",
            status="accepted",
            label="all-entry-points-in",
            now=now,
        )
        inbound_path = session.session_reservation_path(home=self.home_b)
        inbound_store = session.load_session_reservations(home=self.home_b)
        inbound_store["inbound"][inbound_key]["revision"] = 0
        inbound_path.write_text(json.dumps(inbound_store), encoding="utf-8")
        inbound_before = inbound_path.read_bytes()
        deliveries = 0

        def deliver(_payload: dict[str, object], _delivery_id: str) -> str:
            nonlocal deliveries
            deliveries += 1
            return "event-should-not-exist"

        auth_before = len(self.auth_calls)
        with patch.object(session, "sign_group_bridge_payload") as sign:
            with self.assertRaises(session.GroupBridgeSessionStoreError):
                session.receive_group_bridge_session_message(
                    request,
                    group_id="g-b",
                    local_endpoint=self.endpoint_b,
                    home=self.home_b,
                    deliver=deliver,
                )
        self.assertGreater(len(self.auth_calls), auth_before)
        sign.assert_not_called()
        self.assertEqual(deliveries, 0)
        self.assertEqual(inbound_path.read_bytes(), inbound_before)


if __name__ == "__main__":
    unittest.main()
