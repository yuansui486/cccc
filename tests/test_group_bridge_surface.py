import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestGroupBridgeSurface(unittest.TestCase):
    def _wire_envelope(self):
        return {
            "version": 1,
            "kind": "message",
            "transport": "group_bridge_session",
            "nonce": "A" * 43,
            "issued_at": "2026-08-01T00:00:00Z",
            "source_group_id": "g_remote",
            "source_peer_id": "peer_remote",
            "source_public_key": ("A" * 43) + "=",
            "source_endpoint": "https://remote.example/api/group-bridge/session/receive",
            "target_group_id": "g_local",
            "target_peer_id": "peer_local",
            "target_endpoint": "https://local.example/api/group-bridge/session/receive",
            "payload": {"text": "hello"},
            "signature": ("A" * 86) + "==",
        }

    def _route_client(self, calls):
        from no1.ports.web.routes.group_bridge import create_routers
        from no1.ports.web.schemas import RouteContext

        async def daemon(req):
            calls.append(req)
            return {
                "ok": True,
                "result": {"session": {"status": "accepted"}, "receipt": {"status": "accepted"}},
            }

        ctx = RouteContext(
            home=Path(tempfile.gettempdir()),
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

    def test_web_send_delegates_closed_session_args(self):
        calls = []
        client = self._route_client(calls)
        response = client.post(
            "/api/group-bridge/session/send",
            json={
                "group_id": "g_local",
                "local_endpoint": "https://local.example/session",
                "remote_group_id": "g_remote",
                "remote_peer_id": "peer_remote",
                "remote_endpoint": "https://remote.example/session",
                "client_nonce": "A" * 43,
                "payload": {"text": "hello", "format": "plain"},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls[0]["op"], "user_group_bridge_session_send")
        self.assertEqual(
            set(calls[0]["args"]),
            {
                "group_id",
                "local_endpoint",
                "remote_group_id",
                "remote_peer_id",
                "remote_endpoint",
                "client_nonce",
                "payload",
                "by",
            },
        )
        self.assertEqual(calls[0]["args"]["by"], "user")
        self.assertEqual(calls[0]["args"]["payload"]["source_by"], "user")

    def test_web_receive_delegates_signed_envelope_without_local_writer(self):
        calls = []
        client = self._route_client(calls)
        response = client.post(
            "/api/group-bridge/session/receive",
            json=self._wire_envelope(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "accepted"})
        self.assertEqual(calls[0]["op"], "group_bridge_session_receive")
        self.assertEqual(set(calls[0]["args"]), {"group_id", "envelope"})
        self.assertEqual(calls[0]["args"]["group_id"], "g_local")
        self.assertNotIn("local_endpoint", calls[0]["args"])

    def test_web_rejects_extra_fields_before_daemon(self):
        calls = []
        client = self._route_client(calls)
        response = client.post(
            "/api/group-bridge/session/send",
            json={
                "group_id": "g_local",
                "local_endpoint": "https://local.example/session",
                "remote_group_id": "g_remote",
                "remote_peer_id": "peer_remote",
                "remote_endpoint": "",
                "client_nonce": "A" * 43,
                "payload": {"text": "hello"},
                "attachments": [],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_public_wire_reader_rejects_duplicate_compressed_and_oversized_body_before_daemon(self):
        calls = []
        client = self._route_client(calls)
        duplicate = client.post(
            "/api/group-bridge/session/receive",
            content=b'{"version":1,"version":1}',
            headers={"Content-Type": "application/json"},
        )
        compressed = client.post(
            "/api/group-bridge/pairing/remote/requests",
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
        oversized = client.post(
            "/api/group-bridge/pairing/remote/status",
            content=b"x" * 256_001,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(compressed.status_code, 400)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(calls, [])

    def test_create_app_keeps_receive_public_but_send_authenticated(self):
        from no1.kernel.access_tokens import create_access_token
        from no1.ports.web.app import create_app

        old_home = os.environ.get("CCCC_HOME")
        with tempfile.TemporaryDirectory() as td:
            os.environ["CCCC_HOME"] = td
            calls = []

            def fake_call_daemon(req, **_kwargs):
                calls.append(req)
                return {
                    "ok": True,
                    "result": {"session": {"status": "accepted"}, "receipt": {"status": "accepted"}},
                }

            token = str(create_access_token("web-user", allowed_groups=["g_local"], is_admin=False).get("token") or "")
            with patch("no1.ports.web.app.call_daemon", side_effect=fake_call_daemon):
                client = TestClient(create_app())
                receive = client.post(
                    "/api/group-bridge/session/receive",
                    json=self._wire_envelope(),
                )
                self.assertEqual(receive.status_code, 200)
                self.assertEqual(receive.json(), {"status": "accepted"})
                public_pairing = client.post("/api/group-bridge/pairing/remote/requests", json={})
                self.assertEqual(public_pairing.status_code, 422)

                send_body = {
                    "group_id": "g_local",
                    "local_endpoint": "https://local.example/session",
                    "remote_group_id": "g_remote",
                    "remote_peer_id": "peer_remote",
                    "remote_endpoint": "",
                    "client_nonce": "A" * 43,
                    "payload": {"text": "hello"},
                }
                anonymous_send = client.post("/api/group-bridge/session/send", json=send_body)
                self.assertEqual(anonymous_send.status_code, 401)

                authenticated_send = client.post(
                    "/api/group-bridge/session/send",
                    headers={"Authorization": f"Bearer {token}"},
                    json=send_body,
                )
                self.assertEqual(authenticated_send.status_code, 200)
            self.assertEqual(
                [item["op"] for item in calls],
                ["group_bridge_session_receive", "user_group_bridge_session_send"],
            )
            self.assertEqual(calls[1]["args"]["by"], "user")
            self.assertEqual(calls[1]["args"]["payload"]["source_by"], "user")

        if old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = old_home

    def test_mcp_session_send_delegates_closed_args(self):
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        captured = {}

        def fake_call_daemon(req, **_kwargs):
            captured["req"] = req
            return {"ok": True, "result": {"session": {"status": "accepted"}}}

        with tempfile.TemporaryDirectory() as td, patch.dict(
            os.environ,
            {"CCCC_GROUP_ID": "", "CCCC_ACTOR_ID": ""},
            clear=False,
        ), patch.object(mcp_common, "call_daemon", side_effect=fake_call_daemon):
            with mcp_common.runtime_context_override(
                home=td,
                group_id="g_local",
                actor_id="actor_local",
                source="local_mcp",
            ):
                result = mcp_server.handle_tool_call(
                    "onecolleague_group_bridge_session_send",
                    {
                        "group_id": "g_local",
                        "actor_id": "actor_local",
                        "local_endpoint": "https://local.example/session",
                        "remote_group_id": "g_remote",
                        "remote_peer_id": "peer_remote",
                        "remote_endpoint": "",
                        "client_nonce": "A" * 43,
                        "payload": {"text": "hello"},
                    },
                )
        self.assertEqual(result["session"]["status"], "accepted")
        self.assertEqual(captured["req"]["op"], "actor_group_bridge_session_send")
        self.assertEqual(
            set(captured["req"]["args"]),
            {
                "group_id",
                "local_endpoint",
                "remote_group_id",
                "remote_peer_id",
                "remote_endpoint",
                "client_nonce",
                "payload",
                "by",
            },
        )
        self.assertEqual(captured["req"]["args"]["by"], "actor_local")
        self.assertEqual(captured["req"]["args"]["payload"]["source_by"], "actor_local")

    def test_mcp_rejects_non_local_source_and_extra_fields(self):
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        args = {
            "group_id": "g_local",
            "actor_id": "actor_local",
            "local_endpoint": "https://local.example/session",
            "remote_group_id": "g_remote",
            "remote_peer_id": "peer_remote",
            "remote_endpoint": "",
            "client_nonce": "A" * 43,
            "payload": {"text": "hello"},
        }
        with tempfile.TemporaryDirectory() as td, patch.object(
            mcp_common, "call_daemon", side_effect=AssertionError("must not delegate")
        ):
            with mcp_common.runtime_context_override(
                home=td,
                group_id="g_local",
                actor_id="actor_local",
                source="group_bridge",
            ):
                with self.assertRaises(mcp_common.MCPError) as denied:
                    mcp_server.handle_tool_call("onecolleague_group_bridge_session_send", args)
                self.assertEqual(denied.exception.code, "permission_denied")

            args["refs"] = []
            with mcp_common.runtime_context_override(
                home=td,
                group_id="g_local",
                actor_id="actor_local",
                source="local_mcp",
            ):
                with self.assertRaises(mcp_common.MCPError) as invalid:
                    mcp_server.handle_tool_call("onecolleague_group_bridge_session_send", args)
                self.assertEqual(invalid.exception.code, "invalid_request")

    def test_mcp_tool_is_local_messages_surface_only(self):
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        def fake_call_daemon(req, **_kwargs):
            if req.get("op") == "capability_state":
                return {"ok": True, "result": {"visible_tools": []}}
            return {"ok": True, "result": {}}

        with tempfile.TemporaryDirectory() as td, patch.object(mcp_common, "call_daemon", side_effect=fake_call_daemon):
            with mcp_common.runtime_context_override(
                home=td,
                group_id="g_local",
                actor_id="actor_local",
                source="local_mcp",
            ):
                names = {item["name"] for item in mcp_server.list_tools_for_caller()}
        self.assertIn("onecolleague_group_bridge_session_send", names)
        self.assertNotIn("cccc_remote_context", names)
        self.assertNotIn("cccc_remote_shell", names)
        self.assertNotIn("onecolleague_shell", names)


if __name__ == "__main__":
    unittest.main()
