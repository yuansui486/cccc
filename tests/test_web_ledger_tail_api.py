import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class TestWebLedgerTailApi(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def _client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def _call(self, op: str, args: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def test_ledger_tail_can_filter_chat_messages(self) -> None:
        _, cleanup = self._with_home()
        try:
            create, _ = self._call("group_create", {"title": "ledger-tail", "topic": "", "by": "user"})
            self.assertTrue(create.ok, getattr(create, "error", None))
            group_id = str((create.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "headless",
                    "by": "user",
                },
            )
            self.assertTrue(add.ok, getattr(add, "error", None))

            send, _ = self._call("send", {"group_id": group_id, "by": "user", "to": ["peer1"], "text": "hello"})
            self.assertTrue(send.ok, getattr(send, "error", None))

            with self._client() as client:
                resp = client.get(
                    f"/api/v1/groups/{group_id}/ledger/tail?kind=chat&lines=50&with_read_status=true&with_ack_status=true&with_obligation_status=true"
                )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")), body)
            result = body.get("result") or {}
            events = result.get("events") or []
            self.assertIsInstance(events, list)
            self.assertGreaterEqual(len(events), 1)
            self.assertEqual(result.get("has_more"), False)
            self.assertEqual(int(result.get("count") or 0), len(events))
            self.assertTrue(all(str(event.get("kind") or "") == "chat.message" for event in events))
        finally:
            cleanup()

    def test_ledger_tail_accepts_limit_alias(self) -> None:
        _, cleanup = self._with_home()
        try:
            create, _ = self._call("group_create", {"title": "ledger-tail-limit", "topic": "", "by": "user"})
            self.assertTrue(create.ok, getattr(create, "error", None))
            group_id = str((create.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "headless",
                    "by": "user",
                },
            )
            self.assertTrue(add.ok, getattr(add, "error", None))

            for idx in range(3):
                send, _ = self._call(
                    "send",
                    {"group_id": group_id, "by": "user", "to": ["peer1"], "text": f"msg-{idx}"},
                )
                self.assertTrue(send.ok, getattr(send, "error", None))

            with self._client() as client:
                resp = client.get(f"/api/v1/groups/{group_id}/ledger/tail?kind=chat&limit=2")

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")), body)
            result = body.get("result") or {}
            events = result.get("events") or []
            self.assertEqual(len(events), 2)
            self.assertEqual(result.get("has_more"), True)
            self.assertEqual(int(result.get("count") or 0), 2)
        finally:
            cleanup()

    def test_ledger_tail_kind_chat_reads_last_chat_messages_not_last_raw_lines(self) -> None:
        _, cleanup = self._with_home()
        try:
            create, _ = self._call("group_create", {"title": "ledger-tail-chat-tail", "topic": "", "by": "user"})
            self.assertTrue(create.ok, getattr(create, "error", None))
            group_id = str((create.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "headless",
                    "by": "user",
                },
            )
            self.assertTrue(add.ok, getattr(add, "error", None))

            for idx in range(3):
                send, _ = self._call(
                    "send",
                    {"group_id": group_id, "by": "user", "to": ["peer1"], "text": f"chat-{idx}"},
                )
                self.assertTrue(send.ok, getattr(send, "error", None))
                notify, _ = self._call(
                    "system_notify",
                    {
                        "group_id": group_id,
                        "by": "system",
                        "kind": "info",
                        "priority": "normal",
                        "title": f"notify-{idx}",
                        "message": f"notify-{idx}",
                        "target_actor_id": "peer1",
                        "requires_ack": False,
                    },
                )
                self.assertTrue(notify.ok, getattr(notify, "error", None))

            with self._client() as client:
                resp = client.get(f"/api/v1/groups/{group_id}/ledger/tail?kind=chat&limit=2")

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")), body)
            result = body.get("result") or {}
            events = result.get("events") or []
            self.assertEqual([((event.get("data") or {}).get("text")) for event in events], ["chat-1", "chat-2"])
            self.assertEqual(result.get("has_more"), True)
            self.assertEqual(int(result.get("count") or 0), 2)
        finally:
            cleanup()

    def test_ledger_tail_kind_chat_limit_zero_returns_empty_result(self) -> None:
        _, cleanup = self._with_home()
        try:
            create, _ = self._call("group_create", {"title": "ledger-tail-zero", "topic": "", "by": "user"})
            self.assertTrue(create.ok, getattr(create, "error", None))
            group_id = str((create.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            add, _ = self._call(
                "actor_add",
                {
                    "group_id": group_id,
                    "actor_id": "peer1",
                    "title": "Peer 1",
                    "runtime": "codex",
                    "runner": "headless",
                    "by": "user",
                },
            )
            self.assertTrue(add.ok, getattr(add, "error", None))

            send, _ = self._call("send", {"group_id": group_id, "by": "user", "to": ["peer1"], "text": "hello"})
            self.assertTrue(send.ok, getattr(send, "error", None))

            with self._client() as client:
                resp = client.get(f"/api/v1/groups/{group_id}/ledger/tail?kind=chat&limit=0")

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")), body)
            result = body.get("result") or {}
            self.assertEqual(result.get("events"), [])
            self.assertEqual(result.get("has_more"), False)
            self.assertEqual(int(result.get("count") or 0), 0)
        finally:
            cleanup()
