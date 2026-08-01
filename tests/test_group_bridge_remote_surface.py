from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestGroupBridgeRemoteSurface(unittest.TestCase):
    def _web_client(self, calls: list[dict]) -> TestClient:
        from no1.ports.web.routes.group_bridge import create_routers
        from no1.ports.web.schemas import RouteContext

        async def daemon(req):
            calls.append(req)
            return {
                "ok": True,
                "result": {
                    "receipt": {
                        "status": "queued",
                        "registration_id": "reg_1",
                        "idempotency_key": "gbs_" + "a" * 32,
                    }
                },
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

    def test_web_remote_send_and_status_delegate_closed_daemon_ops(self) -> None:
        calls: list[dict] = []
        client = self._web_client(calls)
        key = "gbs_" + "a" * 32
        send = client.post(
            "/api/group-bridge/remote/send",
            json={
                "group_id": "g_local",
                "registration_id": "reg_1",
                "idempotency_key": key,
                "payload": {"text": "hello", "format": "markdown", "priority": "attention"},
            },
        )
        self.assertEqual(send.status_code, 200)
        self.assertEqual(calls[0]["op"], "remote_send")
        self.assertEqual(set(calls[0]["args"]), {"group_id", "registration_id", "idempotency_key", "payload"})
        self.assertNotIn("attachments", calls[0]["args"]["payload"])

        status = client.post(
            "/api/group-bridge/remote/status",
            json={"group_id": "g_local", "registration_id": "reg_1", "idempotency_key": key},
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(calls[1]["op"], "remote_delivery_status")
        self.assertEqual(set(calls[1]["args"]), {"group_id", "registration_id", "idempotency_key"})

    def test_web_remote_rejects_expansion_and_malformed_identity_before_daemon(self) -> None:
        calls: list[dict] = []
        client = self._web_client(calls)
        body = {
            "group_id": "g_local",
            "registration_id": "reg_1",
            "idempotency_key": "gbs_" + "a" * 32,
            "payload": {"text": "hello", "attachments": []},
        }
        for changed in (
            {**body, "extra": True},
            {**body, "idempotency_key": "bad"},
            {**body, "payload": {"text": "hello", "to": ["user"]}},
        ):
            with self.subTest(changed=changed):
                response = client.post("/api/group-bridge/remote/send", json=changed)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(calls, [])

    def test_web_remote_requires_authenticated_user_with_group_access(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.ports.web.app import create_app

        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"CCCC_HOME": td}, clear=False):
            token = str(create_access_token("web-user", allowed_groups=["g_allowed"], is_admin=False).get("token") or "")
            calls: list[dict] = []

            def fake_call_daemon(req, **_kwargs):
                calls.append(req)
                return {"ok": True, "result": {"receipt": {"status": "queued"}}}

            body = {
                "group_id": "g_allowed",
                "registration_id": "reg_1",
                "idempotency_key": "gbs_" + "a" * 32,
                "payload": {"text": "hello"},
            }
            with patch("no1.ports.web.app.call_daemon", side_effect=fake_call_daemon):
                client = TestClient(create_app())
                self.assertEqual(client.post("/api/group-bridge/remote/send", json=body).status_code, 401)
                denied = client.post(
                    "/api/group-bridge/remote/send",
                    headers={"Authorization": f"Bearer {token}"},
                    json={**body, "group_id": "g_denied"},
                )
                self.assertEqual(denied.status_code, 403)
                allowed = client.post(
                    "/api/group-bridge/remote/send",
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                )
                self.assertEqual(allowed.status_code, 200)
            self.assertEqual([item["op"] for item in calls], ["remote_send"])

    def test_mcp_remote_tools_delegate_only_from_local_runtime(self) -> None:
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        captured: list[dict] = []

        def fake_call_daemon(req, **_kwargs):
            captured.append(req)
            return {"ok": True, "result": {"receipt": {"status": "queued"}}}

        key = "gbs_" + "b" * 32
        args = {
            "group_id": "g_local",
            "actor_id": "actor_local",
            "registration_id": "reg_1",
            "idempotency_key": key,
            "payload": {"text": "hello", "reply_required": True},
        }
        with tempfile.TemporaryDirectory() as td, patch.object(mcp_common, "call_daemon", side_effect=fake_call_daemon):
            with mcp_common.runtime_context_override(
                home=td, group_id="g_local", actor_id="actor_local", source="local_mcp"
            ):
                result = mcp_server.handle_tool_call("onecolleague_group_bridge_remote_send", args)
                self.assertEqual(result["receipt"]["status"], "queued")
                status = mcp_server.handle_tool_call(
                    "onecolleague_group_bridge_remote_delivery_status",
                    {key: value for key, value in args.items() if key != "payload"},
                )
                self.assertEqual(status["receipt"]["status"], "queued")
        self.assertEqual([item["op"] for item in captured], ["remote_send", "remote_delivery_status"])
        self.assertNotIn("payload", captured[1]["args"])

        with tempfile.TemporaryDirectory() as td, patch.object(
            mcp_common, "call_daemon", side_effect=AssertionError("must not delegate")
        ):
            with mcp_common.runtime_context_override(
                home=td, group_id="g_local", actor_id="actor_local", source="group_bridge"
            ):
                with self.assertRaises(mcp_common.MCPError) as denied:
                    mcp_server.handle_tool_call("onecolleague_group_bridge_remote_send", args)
                self.assertEqual(denied.exception.code, "permission_denied")

    def test_mcp_remote_tools_require_bound_identity_and_reject_forged_fields(self) -> None:
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        args = {
            "group_id": "g_local",
            "actor_id": "actor_local",
            "registration_id": "reg_1",
            "idempotency_key": "gbs_" + "c" * 32,
            "payload": {"text": "hello"},
        }
        with tempfile.TemporaryDirectory() as td, patch.object(
            mcp_common, "call_daemon", side_effect=AssertionError("must not delegate")
        ):
            cases = (
                ("", "actor_local", "missing_group_id"),
                ("g_local", "", "missing_actor_id"),
                ("", "", "missing_group_id"),
            )
            for runtime_group, runtime_actor, code in cases:
                with self.subTest(runtime_group=runtime_group, runtime_actor=runtime_actor):
                    with mcp_common.runtime_context_override(
                        home=td,
                        group_id=runtime_group,
                        actor_id=runtime_actor,
                        source="local_mcp",
                    ):
                        with self.assertRaises(mcp_common.MCPError) as error:
                            mcp_server.handle_tool_call("onecolleague_group_bridge_remote_send", args)
                        self.assertEqual(error.exception.code, code)

            with mcp_common.runtime_context_override(
                home=td, group_id="g_local", actor_id="actor_local", source="local_mcp"
            ):
                for tool_name, forged in (
                    (
                        "onecolleague_group_bridge_remote_send",
                        {**args, "group_id": "g_forged"},
                    ),
                    (
                        "onecolleague_group_bridge_remote_send",
                        {**args, "actor_id": "actor_forged"},
                    ),
                    (
                        "onecolleague_group_bridge_remote_delivery_status",
                        {key: value for key, value in args.items() if key != "payload"} | {"group_id": "g_forged"},
                    ),
                    (
                        "onecolleague_group_bridge_remote_delivery_status",
                        {key: value for key, value in args.items() if key != "payload"} | {"actor_id": "actor_forged"},
                    ),
                ):
                    with self.subTest(tool_name=tool_name, forged=forged):
                        with self.assertRaises(mcp_common.MCPError) as error:
                            mcp_server.handle_tool_call(tool_name, forged)
                        self.assertIn(error.exception.code, {"group_id_mismatch", "actor_id_mismatch"})

    def test_mcp_session_send_requires_bound_identity_before_daemon(self) -> None:
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
            for runtime_group, runtime_actor, code in (
                ("", "actor_local", "missing_group_id"),
                ("g_local", "", "missing_actor_id"),
                ("", "", "missing_group_id"),
            ):
                with self.subTest(runtime_group=runtime_group, runtime_actor=runtime_actor):
                    with mcp_common.runtime_context_override(
                        home=td, group_id=runtime_group, actor_id=runtime_actor, source="local_mcp"
                    ):
                        with self.assertRaises(mcp_common.MCPError) as error:
                            mcp_server.handle_tool_call("onecolleague_group_bridge_session_send", args)
                        self.assertEqual(error.exception.code, code)

            with mcp_common.runtime_context_override(
                home=td, group_id="g_local", actor_id="actor_local", source="local_mcp"
            ):
                for forged in (
                    {**args, "group_id": "g_forged"},
                    {**args, "actor_id": "actor_forged"},
                ):
                    with self.subTest(forged=forged):
                        with self.assertRaises(mcp_common.MCPError) as error:
                            mcp_server.handle_tool_call("onecolleague_group_bridge_session_send", forged)
                        self.assertIn(error.exception.code, {"group_id_mismatch", "actor_id_mismatch"})

            with mcp_common.runtime_context_override(
                home=td, group_id="g_local", actor_id="actor_local", source="group_bridge"
            ):
                with self.assertRaises(mcp_common.MCPError) as error:
                    mcp_server.handle_tool_call("onecolleague_group_bridge_session_send", args)
                self.assertEqual(error.exception.code, "permission_denied")

    def test_remote_tools_are_only_visible_to_local_mcp(self) -> None:
        from no1.ports.mcp import common as mcp_common
        from no1.ports.mcp import server as mcp_server

        def fake_call_daemon(req, **_kwargs):
            if req.get("op") == "capability_state":
                return {"ok": True, "result": {"visible_tools": []}}
            return {"ok": True, "result": {}}

        with tempfile.TemporaryDirectory() as td, patch.object(mcp_common, "call_daemon", side_effect=fake_call_daemon):
            with mcp_common.runtime_context_override(home=td, group_id="g", actor_id="a", source="local_mcp"):
                local = {item["name"] for item in mcp_server.list_tools_for_caller()}
            with mcp_common.runtime_context_override(home=td, group_id="g", actor_id="a", source="group_bridge"):
                remote = {item["name"] for item in mcp_server.list_tools_for_caller()}
        self.assertIn("onecolleague_group_bridge_remote_send", local)
        self.assertIn("onecolleague_group_bridge_remote_delivery_status", local)
        self.assertNotIn("onecolleague_group_bridge_remote_send", remote)
        self.assertNotIn("onecolleague_group_bridge_remote_delivery_status", remote)


if __name__ == "__main__":
    unittest.main()
