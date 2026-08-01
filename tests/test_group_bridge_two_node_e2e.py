from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import Mock, patch

import uvicorn
from fastapi import FastAPI


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(app: FastAPI, port: int) -> Iterator[None]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    else:
        raise AssertionError("loopback Web server did not start")
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise AssertionError("loopback Web server did not stop")


class TestGroupBridgeTwoNodeE2E(unittest.TestCase):
    def _claim(self, home: Path, group_id: str, user_id: str):
        from no1.kernel.access_tokens import create_access_token, issue_access_token_principal_claim

        token = create_access_token(user_id, allowed_groups=[group_id], home=home)["token"]
        return issue_access_token_principal_claim(token, group_id=group_id, home=home)

    def _issuer_app(self, *, home: Path, local_endpoint: str, ledger_path: Path) -> FastAPI:
        from no1.contracts.v1 import ChatMessageData
        from no1.daemon.group_bridge.pairing_transport import (
            receive_remote_pairing_request,
            remote_pairing_status,
        )
        from no1.daemon.group_bridge.session import receive_group_bridge_session_message
        from no1.kernel.ledger import append_event_once
        from no1.ports.web.routes.group_bridge import create_routers
        from no1.ports.web.schemas import RouteContext

        async def daemon(request: dict[str, Any]) -> dict[str, Any]:
            op = request.get("op")
            args = request.get("args")
            if not isinstance(args, dict):
                return {"ok": False, "error": {"code": "invalid_request", "message": "invalid"}}
            if op == "group_bridge_pairing_remote_request":
                status = receive_remote_pairing_request(
                    args["envelope"], local_endpoint=local_endpoint, home=home
                )
                return {"ok": True, "result": {"status": status}}
            if op == "group_bridge_pairing_remote_status":
                status = remote_pairing_status(
                    args["envelope"], local_endpoint=local_endpoint, home=home
                )
                return {"ok": True, "result": {"status": status}}
            if op == "group_bridge_session_receive":
                envelope = args["envelope"]

                def deliver(payload: dict[str, Any], delivery_id: str) -> str:
                    data = ChatMessageData(
                        text=payload["text"],
                        format=payload["format"],
                        priority=payload["priority"],
                        reply_required=payload["reply_required"],
                        collaboration_required=False,
                        to=["user"],
                        attachments=[],
                        refs=[],
                        quote_text="",
                        source_platform="group_bridge_session",
                        source_user_id=envelope["source_peer_id"],
                        src_group_id=envelope["source_group_id"],
                        src_event_id=delivery_id,
                        client_id=delivery_id,
                    ).model_dump()
                    event, _ = append_event_once(
                        ledger_path,
                        event_id=delivery_id,
                        kind="chat.message",
                        group_id=envelope["target_group_id"],
                        scope_key="",
                        by="system",
                        data=data,
                    )
                    return str(event["id"])

                receipt = receive_group_bridge_session_message(
                    envelope,
                    group_id=envelope["target_group_id"],
                    local_endpoint=local_endpoint,
                    deliver=deliver,
                    home=home,
                )
                return {"ok": True, "result": {"receipt": receipt}}
            return {"ok": False, "error": {"code": "not_found", "message": "invalid"}}

        context = RouteContext(
            home=home,
            version="test",
            web_mode="normal",
            read_only=False,
            exhibit_cache_ttl_s=1.0,
            exhibit_allow_terminal=False,
            dist_dir=None,
            daemon=daemon,
            cached_json=lambda *args, **kwargs: None,
            apply_web_logging=lambda *args, **kwargs: None,
        )
        app = FastAPI()
        for router in create_routers(context):
            app.include_router(router)
        return app

    def test_real_loopback_pairing_restart_sync_and_exactly_once_message(self) -> None:
        from no1.daemon.group_bridge.pairing_transport import (
            create_pairing_connection,
            submit_remote_pairing,
            sync_remote_pairing,
        )
        from no1.daemon.group_bridge.remote_dispatch import enqueue_remote_send, remote_delivery_status
        from no1.daemon.group_bridge.remote_outbox_worker import sweep_remote_outbox
        from no1.kernel.group_bridge.pairing import approve_pairing_request, list_pairing_requests, list_trusts
        from no1.kernel.group_bridge.registration import list_registrations

        with tempfile.TemporaryDirectory() as requester_td, tempfile.TemporaryDirectory() as issuer_td:
            requester_home = Path(requester_td)
            issuer_home = Path(issuer_td)
            port = _free_port()
            issuer_endpoint = f"http://127.0.0.1:{port}/api/group-bridge/session/receive"
            requester_endpoint = "http://127.0.0.1:39191/api/group-bridge/session/receive"
            ledger_path = issuer_home / "groups" / "issuer-group" / "ledger.jsonl"
            app = self._issuer_app(home=issuer_home, local_endpoint=issuer_endpoint, ledger_path=ledger_path)

            connection = create_pairing_connection(
                group_id="issuer-group",
                local_endpoint=issuer_endpoint,
                remote_group_id="requester-group",
                ttl_seconds=600,
                home=issuer_home,
            )
            raw_pairing_code = connection["pairing_code"]
            requester_claim = self._claim(requester_home, "requester-group", "requester-user")

            with _serve(app, port):
                pending = submit_remote_pairing(
                    connection,
                    local_group_id="requester-group",
                    local_group_title="Requester",
                    requester_endpoint=requester_endpoint,
                    claim=requester_claim,
                    home=requester_home,
                )
                self.assertEqual(pending["status"], "pending")
                self.assertNotIn("credential_ref", pending)
                self.assertNotIn("client_nonce_hash", pending)
                outbound_path = requester_home / "state/group_bridge/pairing_outbounds.json"
                self.assertNotIn(raw_pairing_code, outbound_path.read_text())

                issuer_request = list_pairing_requests(group_id="issuer-group", home=issuer_home)[0]
                approve_pairing_request(
                    issuer_request["request_id"],
                    claim=self._claim(issuer_home, "issuer-group", "issuer-user"),
                    home=issuer_home,
                )

                # A fresh claim after the simulated requester restart proves
                # the persisted outbound is sufficient to converge approval.
                requester_claim = self._claim(requester_home, "requester-group", "requester-user-2")
                approved = sync_remote_pairing(
                    pending["outbound_id"],
                    local_group_id="requester-group",
                    claim=requester_claim,
                    home=requester_home,
                )
                self.assertEqual(approved["status"], "approved")
                self.assertNotIn("pairing_code_hash", approved)
                terminal_replay = submit_remote_pairing(
                    connection,
                    local_group_id="requester-group",
                    local_group_title="Requester",
                    requester_endpoint=requester_endpoint,
                    claim=self._claim(requester_home, "requester-group", "requester-user-3"),
                    home=requester_home,
                    http_post=lambda *_args: self.fail("terminal replay must not perform network I/O"),
                )
                self.assertEqual(terminal_replay, approved)

                for home, group_id, remote_group in (
                    (requester_home, "requester-group", "issuer-group"),
                    (issuer_home, "issuer-group", "requester-group"),
                ):
                    trusts = list_trusts(group_id=group_id, home=home)
                    registrations = list_registrations(home=home)
                    self.assertEqual(len(trusts), 1)
                    self.assertEqual(trusts[0]["access_level"], "messages")
                    self.assertEqual(trusts[0]["remote_group_id"], remote_group)
                    self.assertEqual(len(registrations), 1)
                    self.assertEqual(registrations[0]["status"], "active")

                registration_id = approved["registration_id"]
                idempotency_key = "gbs_" + ("a" * 32)
                queued = enqueue_remote_send(
                    group_id="requester-group",
                    registration_id=registration_id,
                    idempotency_key=idempotency_key,
                    payload={"text": "real loopback", "format": "markdown", "priority": "attention"},
                    home=requester_home,
                )
                self.assertTrue(queued["queued"])
                sweep = sweep_remote_outbox(home=requester_home, local_endpoint=requester_endpoint)
                self.assertEqual(sweep, {"attempted": 1, "sent": 1, "retrying": 0, "failed": 0})
                first = remote_delivery_status(
                    group_id="requester-group",
                    registration_id=registration_id,
                    idempotency_key=idempotency_key,
                    home=requester_home,
                )["receipt"]
                replay = enqueue_remote_send(
                    group_id="requester-group",
                    registration_id=registration_id,
                    idempotency_key=idempotency_key,
                    payload={"text": "real loopback", "format": "markdown", "priority": "attention"},
                    home=requester_home,
                )
                self.assertTrue(replay["replayed"])
                self.assertEqual(sweep_remote_outbox(home=requester_home), {
                    "attempted": 0,
                    "sent": 0,
                    "retrying": 0,
                    "failed": 0,
                })

            self.assertEqual(first, replay["receipt"])
            self.assertEqual(first["status"], "sent")
            self.assertRegex(first["remote_event_id"], r"^gbs_[0-9a-f]{32}$")
            matching = [
                json.loads(line)
                for line in ledger_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("id") == first["remote_event_id"]
            ]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["by"], "system")
            self.assertEqual(matching[0]["data"]["to"], ["user"])
            self.assertEqual(matching[0]["data"]["source_platform"], "group_bridge_session")

    def test_canonical_paths_are_exact_and_public_wire_is_unwrapped(self) -> None:
        from no1.daemon.group_bridge.pairing_transport import PairingTransportError, canonical_receive_endpoint
        from no1.daemon.group_bridge.remote_outbox_worker import default_local_endpoint

        self.assertTrue(default_local_endpoint().endswith("/api/group-bridge/session/receive"))
        self.assertEqual(
            canonical_receive_endpoint("http://127.0.0.1:8848/api/group-bridge/session/receive"),
            "http://127.0.0.1:8848/api/group-bridge/session/receive",
        )
        for value in (
            "http://127.0.0.1:8848/api/group-bridge/session",
            "http://127.0.0.1:8848/api/group-bridge/session/receive/",
            "http://127.0.0.1:8848/api/group-bridge/session/receive?x=1",
        ):
            with self.subTest(value=value), self.assertRaises(PairingTransportError):
                canonical_receive_endpoint(value)

    def test_response_loss_replays_request_and_rejected_status_stays_secret_free(self) -> None:
        from no1.daemon.group_bridge import pairing_transport
        from no1.daemon.group_bridge.pairing_transport import (
            PairingTransportError,
            create_pairing_connection,
            receive_remote_pairing_request,
            submit_remote_pairing,
            sync_remote_pairing,
        )
        from no1.kernel.group_bridge.credentials import get_group_bridge_credential
        from no1.kernel.group_bridge.pairing import list_pairing_requests, list_trusts, reject_pairing_request
        from no1.kernel.group_bridge.pairing_outbound import get_pairing_outbound
        from no1.kernel.group_bridge.registration import list_registrations

        with tempfile.TemporaryDirectory() as requester_td, tempfile.TemporaryDirectory() as issuer_td:
            requester_home = Path(requester_td)
            issuer_home = Path(issuer_td)
            issuer_endpoint = "http://127.0.0.1:39192/api/group-bridge/session/receive"
            requester_endpoint = "http://127.0.0.1:39193/api/group-bridge/session/receive"
            connection = create_pairing_connection(
                group_id="issuer-group",
                local_endpoint=issuer_endpoint,
                remote_group_id="requester-group",
                home=issuer_home,
            )

            def lose_response(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
                self.assertTrue(endpoint.endswith("/api/group-bridge/pairing/remote/requests"))
                receive_remote_pairing_request(body, local_endpoint=issuer_endpoint, home=issuer_home)
                raise PairingTransportError("transport_error", "lost response", retriable=True)

            with self.assertRaises(PairingTransportError):
                submit_remote_pairing(
                    connection,
                    local_group_id="requester-group",
                    local_group_title="Requester",
                    requester_endpoint=requester_endpoint,
                    claim=self._claim(requester_home, "requester-group", "requester-user"),
                    home=requester_home,
                    http_post=lose_response,
                )
            request = list_pairing_requests(group_id="issuer-group", home=issuer_home)[0]
            reject_pairing_request(
                request["request_id"],
                claim=self._claim(issuer_home, "issuer-group", "issuer-user"),
                reason="declined",
                home=issuer_home,
            )

            def replay(endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
                self.assertTrue(endpoint.endswith("/api/group-bridge/pairing/remote/requests"))
                return receive_remote_pairing_request(body, local_endpoint=issuer_endpoint, home=issuer_home)

            outbound_path = requester_home / "state/group_bridge/pairing_outbounds.json"
            outbound_id = next(iter(json.loads(outbound_path.read_text())["outbounds"]))
            with patch.object(
                pairing_transport,
                "delete_group_bridge_credential",
                side_effect=RuntimeError("cleanup interrupted"),
            ):
                with self.assertRaisesRegex(RuntimeError, "cleanup interrupted"):
                    sync_remote_pairing(
                        outbound_id,
                        local_group_id="requester-group",
                        claim=self._claim(requester_home, "requester-group", "requester-user-2"),
                        home=requester_home,
                        http_post=replay,
                    )
            terminal = get_pairing_outbound(outbound_id, home=requester_home, strict=True)
            self.assertIsNotNone(terminal)
            assert terminal is not None
            self.assertEqual(terminal["status"], "rejected")
            self.assertIsNotNone(get_group_bridge_credential(terminal["credential_ref"], home=requester_home))

            result = sync_remote_pairing(
                outbound_id,
                local_group_id="requester-group",
                claim=self._claim(requester_home, "requester-group", "requester-user-3"),
                home=requester_home,
                http_post=lambda *_args: self.fail("terminal cleanup replay must not perform network I/O"),
            )
            self.assertEqual(result["status"], "rejected")
            self.assertIsNone(get_group_bridge_credential(terminal["credential_ref"], home=requester_home))
            self.assertNotIn("credential_ref", result)
            self.assertNotIn(connection["pairing_code"], outbound_path.read_text())
            self.assertEqual(list_trusts(group_id="requester-group", home=requester_home), [])
            self.assertEqual(list_registrations(home=requester_home), [])

    def test_tampered_connection_and_wrong_group_sync_fail_before_network(self) -> None:
        from no1.daemon.group_bridge.pairing_transport import (
            PairingTransportError,
            create_pairing_connection,
            submit_remote_pairing,
            sync_remote_pairing,
        )

        with tempfile.TemporaryDirectory() as requester_td, tempfile.TemporaryDirectory() as issuer_td:
            requester_home = Path(requester_td)
            issuer_home = Path(issuer_td)
            endpoint = "http://127.0.0.1:39194/api/group-bridge/session/receive"
            connection = create_pairing_connection(
                group_id="issuer-group",
                local_endpoint=endpoint,
                home=issuer_home,
            )
            tampered = {**connection, "issuer_group_id": "forged-group"}
            with self.assertRaisesRegex(PairingTransportError, "signature"):
                submit_remote_pairing(
                    tampered,
                    local_group_id="requester-group",
                    local_group_title="Requester",
                    requester_endpoint="http://127.0.0.1:39195/api/group-bridge/session/receive",
                    claim=self._claim(requester_home, "requester-group", "requester-user"),
                    home=requester_home,
                    http_post=lambda *_args: self.fail("must not perform network I/O"),
                )
            self.assertFalse((requester_home / "state/group_bridge/pairing_outbounds.json").exists())

            def unavailable(*_args: Any) -> dict[str, Any]:
                raise PairingTransportError("transport_error", "offline", retriable=True)

            with self.assertRaises(PairingTransportError):
                submit_remote_pairing(
                    connection,
                    local_group_id="requester-group",
                    local_group_title="Requester",
                    requester_endpoint="http://127.0.0.1:39195/api/group-bridge/session/receive",
                    claim=self._claim(requester_home, "requester-group", "requester-user-2"),
                    home=requester_home,
                    http_post=unavailable,
                )
            path = requester_home / "state/group_bridge/pairing_outbounds.json"
            outbound_id = next(iter(json.loads(path.read_text())["outbounds"]))
            with self.assertRaisesRegex(PairingTransportError, "not authorized"):
                sync_remote_pairing(
                    outbound_id,
                    local_group_id="forged-group",
                    claim=self._claim(requester_home, "forged-group", "forged-user"),
                    home=requester_home,
                    http_post=lambda *_args: self.fail("must not perform network I/O"),
                )

    def test_secret_then_reserve_crash_reuses_one_opaque_credential(self) -> None:
        from no1.daemon.group_bridge import pairing_transport
        from no1.kernel.group_bridge.credentials import list_group_bridge_credentials

        with tempfile.TemporaryDirectory() as requester_td, tempfile.TemporaryDirectory() as issuer_td:
            requester_home = Path(requester_td)
            issuer_home = Path(issuer_td)
            connection = pairing_transport.create_pairing_connection(
                group_id="issuer-group",
                local_endpoint="http://127.0.0.1:39196/api/group-bridge/session/receive",
                home=issuer_home,
            )
            submit_args = {
                "local_group_id": "requester-group",
                "local_group_title": "Requester",
                "requester_endpoint": "http://127.0.0.1:39197/api/group-bridge/session/receive",
                "home": requester_home,
            }
            with patch.object(pairing_transport, "reserve_pairing_outbound", side_effect=RuntimeError("crash")):
                with self.assertRaisesRegex(RuntimeError, "crash"):
                    pairing_transport.submit_remote_pairing(
                        connection,
                        claim=self._claim(requester_home, "requester-group", "requester-user"),
                        **submit_args,
                    )

            def offline(*_args: Any) -> dict[str, Any]:
                raise pairing_transport.PairingTransportError("transport_error", "offline", retriable=True)

            with self.assertRaises(pairing_transport.PairingTransportError):
                pairing_transport.submit_remote_pairing(
                    connection,
                    claim=self._claim(requester_home, "requester-group", "requester-user-2"),
                    http_post=offline,
                    **submit_args,
                )
            credentials = list_group_bridge_credentials(home=requester_home)
            self.assertEqual(len(credentials), 1)
            self.assertNotIn("token", credentials[0])
            self.assertNotIn(connection["pairing_code"], str(credentials))

    def test_corrupt_outbound_store_fails_closed(self) -> None:
        from no1.daemon.group_bridge.pairing_transport import PairingOutboundStoreError, sync_remote_pairing

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "state/group_bridge/pairing_outbounds.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"version":1,"outbounds":{"duplicate":1,"duplicate":2}}\n', encoding="utf-8")
            with self.assertRaises(PairingOutboundStoreError):
                sync_remote_pairing(
                    "pout_" + "a" * 16,
                    local_group_id="local-group",
                    claim=self._claim(home, "local-group", "member"),
                    home=home,
                )

    def test_daemon_pairing_ops_are_closed_claim_backed_and_public_envelope_only(self) -> None:
        from no1.daemon.group_bridge import ops
        from no1.kernel.access_tokens import create_access_token

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CCCC_HOME": td}, clear=False):
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            connection_args = {
                "group_id": "local-group",
                "expected_remote_group_id": "remote-group",
                "expected_remote_peer_id": "",
                "multiaddrs": [],
                "ttl_seconds": 600,
                "access_token": token,
            }
            with patch.object(ops, "create_pairing_connection", return_value={"kind": "pairing_connection"}) as create:
                response = ops.try_handle_group_bridge_op(
                    "group_bridge_management_pairing_connection",
                    connection_args,
                    dispatch_send=Mock(),
                )
            self.assertTrue(response.ok)
            self.assertEqual(create.call_args.kwargs["group_id"], "local-group")
            self.assertTrue(create.call_args.kwargs["local_endpoint"].endswith("/session/receive"))

            denied = ops.try_handle_group_bridge_op(
                "group_bridge_management_pairing_connection",
                {**connection_args, "access_token": ""},
                dispatch_send=Mock(),
            )
            extra = ops.try_handle_group_bridge_op(
                "group_bridge_management_pairing_connection",
                {**connection_args, "secret": "forbidden"},
                dispatch_send=Mock(),
            )
            self.assertEqual(denied.error.code, "permission_denied")
            self.assertEqual(extra.error.code, "invalid_request")

            envelope = {"strict": "wire"}
            with patch.object(ops, "receive_remote_pairing_request", return_value={"status": "pending"}) as receive:
                public = ops.try_handle_group_bridge_op(
                    "group_bridge_pairing_remote_request",
                    {"envelope": envelope},
                    dispatch_send=Mock(),
                )
            self.assertTrue(public.ok)
            self.assertEqual(public.result, {"status": {"status": "pending"}})
            self.assertEqual(receive.call_args.args, (envelope,))
            self.assertEqual(set(receive.call_args.kwargs), {"local_endpoint"})
            public_extra = ops.try_handle_group_bridge_op(
                "group_bridge_pairing_remote_request",
                {"envelope": envelope, "group_id": "forged"},
                dispatch_send=Mock(),
            )
            self.assertEqual(public_extra.error.code, "invalid_request")


if __name__ == "__main__":
    unittest.main()
