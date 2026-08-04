from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestGroupBridgeManagementDaemon(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self._env = patch.dict(
            os.environ,
            {"ONECOLLEAGUE_HOME": str(self.home), "CCCC_HOME": str(self.home)},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._td.cleanup)
        from no1.kernel.access_tokens import create_access_token

        self.token = str(create_access_token("web-user", allowed_groups=["local-group"], home=self.home)["token"])

    def _op(self, op: str, args: dict[str, object]):
        from no1.daemon.group_bridge.ops import try_handle_group_bridge_op

        return try_handle_group_bridge_op(op, args, dispatch_send=Mock())

    def _args(self, **extra: object) -> dict[str, object]:
        return {"group_id": "local-group", "access_token": self.token, **extra}

    def test_identity_and_registration_projection_are_secret_free(self) -> None:
        from no1.kernel.group_bridge.registration import upsert_registration

        upsert_registration(
            "local-group",
            "https://remote.example.test/session",
            transport="group_bridge_session",
            remote_group_id="remote-group",
            remote_peer_id="remote-peer",
            credential_ref="sec_test_ref",
            home=self.home,
            _approved_by_pairing=True,
        )
        identity = self._op("group_bridge_management_identity", self._args())
        self.assertTrue(identity.ok)
        projection = identity.result["identity"]
        self.assertIn("peer_id", projection)
        self.assertIn("public_key", projection)
        self.assertNotIn("private_key", str(projection))

        registrations = self._op("group_bridge_management_registrations", self._args())
        self.assertTrue(registrations.ok)
        record = registrations.result["registrations"][0]
        self.assertNotIn("credential_ref", record)
        self.assertNotIn("sec_test_ref", str(registrations))

    def test_invite_request_list_and_reject_are_claim_backed(self) -> None:
        from no1.kernel.group_bridge.pairing import create_pairing_request

        invite = self._op(
            "group_bridge_management_pairing_invite",
            self._args(
                expected_remote_group_id="remote-group",
                expected_remote_peer_id="remote-peer",
                multiaddrs=[],
                ttl_seconds=600,
            ),
        )
        self.assertTrue(invite.ok)
        pairing_code = invite.result["invite"]["pairing_code"]
        request = create_pairing_request(
            pairing_code,
            client_nonce="nonce_" + "a" * 24,
            requester_group_id="remote-group",
            requester_group_title="Remote",
            requester_peer_id="remote-peer",
            requester_endpoint="https://remote.example.test/session",
            requester_multiaddrs=[],
            home=self.home,
        )
        listed = self._op("group_bridge_management_pairing_requests", self._args())
        self.assertTrue(listed.ok)
        self.assertEqual(listed.result["requests"][0]["request_id"], request["request_id"])

        rejected = self._op(
            "group_bridge_management_pairing_reject",
            self._args(request_id=request["request_id"], reason="not approved"),
        )
        self.assertTrue(rejected.ok)
        self.assertEqual(rejected.result["request"]["status"], "rejected")

    def test_management_requires_token_and_closed_arguments(self) -> None:
        missing = self._op("group_bridge_management_trusts", {"group_id": "local-group", "access_token": ""})
        self.assertFalse(missing.ok)
        self.assertEqual(missing.error.code, "permission_denied")
        extra = self._op(
            "group_bridge_management_trusts",
            self._args(unexpected="nope"),
        )
        self.assertFalse(extra.ok)
        self.assertEqual(extra.error.code, "invalid_request")
        wrong_group = self._op("group_bridge_management_trusts", {"group_id": "other-group", "access_token": self.token})
        self.assertFalse(wrong_group.ok)
        self.assertEqual(wrong_group.error.code, "permission_denied")


class TestGroupBridgeManagementWeb(unittest.TestCase):
    def _client(self, calls: list[dict[str, object]], home: Path | None = None) -> TestClient:
        from no1.ports.web.routes.group_bridge import create_routers
        from no1.ports.web.schemas import RouteContext

        async def daemon(request: dict[str, object]) -> dict[str, object]:
            calls.append(request)
            return {"ok": True, "result": {"identity": {"peer_id": "peer"}}}

        ctx = RouteContext(
            home=home or Path(tempfile.gettempdir()),
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
        for router in create_routers(ctx):
            app.include_router(router)
        return TestClient(app)

    def test_management_read_routes_project_locally_without_token(self) -> None:
        calls: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as td:
            client = self._client(calls, Path(td))
            response = client.get("/api/group-bridge/identity", params={"group_id": "local-group"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("identity", response.json()["result"])

            response = client.get("/api/group-bridge/registrations", params={"group_id": "local-group"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["result"]["registrations"], [])

            response = client.get("/api/group-bridge/trusts", params={"group_id": "local-group"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["result"]["trusts"], [])

            response = client.get("/api/group-bridge/pairing/requests", params={"group_id": "local-group"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["result"]["requests"], [])

        self.assertEqual(calls, [])

    def test_management_routes_delegate_writes_to_daemon(self) -> None:
        calls: list[dict[str, object]] = []
        client = self._client(calls)
        response = client.post(
            "/api/group-bridge/pairing/invites",
            json={
                "group_id": "local-group",
                "expected_remote_group_id": "remote-group",
                "expected_remote_peer_id": "remote-peer",
                "multiaddrs": [],
                "ttl_seconds": 600,
            },
            params={"token": "opaque-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["op"], "group_bridge_management_pairing_invite")
        self.assertEqual(calls[0]["args"], {
            "group_id": "local-group",
            "expected_remote_group_id": "remote-group",
            "expected_remote_peer_id": "remote-peer",
            "multiaddrs": [],
            "ttl_seconds": 600,
            "access_token": "opaque-token",
        })

    def test_management_routes_require_group_scope_when_tokens_are_enabled(self) -> None:
        calls: list[dict[str, object]] = []
        client = self._client(calls)
        with patch("no1.ports.web.schemas._tokens_enabled", return_value=True), patch(
            "no1.ports.web.schemas.get_principal",
            return_value=type("Principal", (), {"kind": "user", "allowed_groups": ("allowed-group",), "is_admin": False})(),
        ):
            response = client.get("/api/group-bridge/trusts", params={"group_id": "forbidden-group"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
