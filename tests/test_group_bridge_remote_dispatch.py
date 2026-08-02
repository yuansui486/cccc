from __future__ import annotations

import copy
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml


class TestGroupBridgeRemoteDispatch(unittest.TestCase):
    def setUp(self) -> None:
        from no1.kernel.group_bridge.registration import upsert_registration

        self._td = tempfile.TemporaryDirectory()
        self.home = Path(self._td.name)
        self._env = patch.dict(
            os.environ,
            {"ONECOLLEAGUE_HOME": str(self.home), "CCCC_HOME": str(self.home)},
        )
        self._env.start()
        self.addCleanup(self._env.stop)
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

    def _args(
        self,
        *,
        key: str = "gbs_" + "a" * 32,
        text: str = "hello",
        insight: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "text": text,
            "format": "markdown",
            "priority": "attention",
            "reply_required": True,
        }
        if insight is not None:
            payload["insight"] = insight
        return {
            "group_id": "local-group",
            "registration_id": self.registration["registration_id"],
            "idempotency_key": key,
            "payload": payload,
        }

    def _op(self, op: str, args: dict[str, object]):
        from no1.daemon.group_bridge.ops import try_handle_group_bridge_op

        return try_handle_group_bridge_op(op, args, dispatch_send=Mock())

    def test_enqueue_replays_conflicts_and_status_is_public_only(self) -> None:
        from no1.kernel.group_bridge.receipts import get_queued_request

        first = self._op("remote_send", self._args())
        self.assertTrue(first.ok)
        self.assertTrue(first.result["queued"])
        self.assertFalse(first.result["replayed"])
        receipt = first.result["receipt"]
        self.assertEqual(receipt["status"], "queued")
        self.assertNotIn("payload", receipt)
        self.assertNotIn("sec_test_ref", str(first))

        replay = self._op("remote_send", self._args())
        self.assertTrue(replay.ok)
        self.assertFalse(replay.result["queued"])
        self.assertTrue(replay.result["replayed"])
        self.assertEqual(replay.result["receipt"], receipt)
        self.assertEqual(
            get_queued_request(self.registration["registration_id"], self._args()["idempotency_key"], self.home)["payload"]["text"],
            "hello",
        )

        status = self._op(
            "remote_delivery_status",
            {
                "group_id": "local-group",
                "registration_id": self.registration["registration_id"],
                "idempotency_key": self._args()["idempotency_key"],
            },
        )
        self.assertTrue(status.ok)
        self.assertEqual(status.result["receipt"], receipt)
        self.assertNotIn("payload", str(status.result))

        before = (self.home / "group_bridge_receipts.yaml").read_bytes()
        conflict = self._op("remote_send", self._args(text="different"))
        self.assertFalse(conflict.ok)
        self.assertEqual(conflict.error.code, "remote_receipt_conflict")
        self.assertEqual((self.home / "group_bridge_receipts.yaml").read_bytes(), before)

        insight_conflict = self._op(
            "remote_send",
            self._args(insight="The same transport key cannot replace the original perspective."),
        )
        self.assertFalse(insight_conflict.ok)
        self.assertEqual(insight_conflict.error.code, "remote_receipt_conflict")
        self.assertEqual((self.home / "group_bridge_receipts.yaml").read_bytes(), before)

    def test_payload_is_exact_session_message_and_args_are_closed(self) -> None:
        for field, value in (
            ("attachments", [{"name": "x"}]),
            ("refs", [{"id": "x"}]),
            ("to", ["user"]),
        ):
            with self.subTest(field=field):
                args = self._args()
                args["payload"] = {**args["payload"], field: value}  # type: ignore[dict-item]
                response = self._op("remote_send", args)
                self.assertFalse(response.ok)
                self.assertEqual(response.error.code, "invalid_request")

        signed_args = self._args(key="gbs_" + "e" * 32)
        signed_args["payload"] = {
            **signed_args["payload"],  # type: ignore[dict-item]
            "insight": "The remote sender remains part of the message fact.",
            "source_by": "remote-peer",
        }
        signed = self._op("remote_send", signed_args)
        self.assertTrue(signed.ok, signed.error)
        from no1.kernel.group_bridge.receipts import get_queued_request

        queued = get_queued_request(
            self.registration["registration_id"],
            signed_args["idempotency_key"],
            self.home,
        )
        self.assertEqual(queued["payload"]["source_by"], "remote-peer")
        self.assertEqual(
            queued["payload"]["insight"],
            "The remote sender remains part of the message fact.",
        )

        for args in (
            {key: value for key, value in self._args().items() if key != "payload"},
            {**self._args(), "extra": True},
            {**self._args(), "payload": {**self._args()["payload"], "reply_required": 1}},
        ):
            response = self._op("remote_send", args)
            self.assertFalse(response.ok)
            self.assertEqual(response.error.code, "invalid_request")

        self.assertIsNone(self._op("unknown", {}))

    def test_registration_and_store_fail_closed(self) -> None:
        wrong_group = self._args()
        wrong_group["group_id"] = "other-group"
        response = self._op("remote_send", wrong_group)
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "remote_group_mismatch")

        from no1.kernel.group_bridge.registration import upsert_registration

        revoked = upsert_registration(
            "local-group",
            "https://revoked.example.test/session",
            transport="group_bridge_session",
            remote_group_id="remote-group",
            remote_peer_id="remote-peer",
            status="revoked",
            home=self.home,
        )
        revoked_args = self._args(key="gbs_" + "b" * 32)
        revoked_args["registration_id"] = revoked["registration_id"]
        response = self._op("remote_send", revoked_args)
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "remote_registration_inactive")

        path = self.home / "group_bridge_receipts.yaml"
        path.write_bytes(b"receipts: [\n")
        before = path.read_bytes()
        response = self._op("remote_send", self._args(key="gbs_" + "c" * 32))
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "remote_receipt_store_corrupt")
        self.assertEqual(path.read_bytes(), before)

    def test_partial_private_request_is_rejected_without_write(self) -> None:
        self.assertTrue(self._op("remote_send", self._args()).ok)
        path = self.home / "group_bridge_receipts.yaml"
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        key = self.registration["registration_id"] + "::" + self._args()["idempotency_key"]
        raw["receipts"][key]["_queued_request"] = {"src_group_id": "local-group"}
        path.write_text(yaml.safe_dump(raw, sort_keys=True), encoding="utf-8")
        before = path.read_bytes()
        response = self._op("remote_send", self._args())
        self.assertFalse(response.ok)
        self.assertEqual(response.error.code, "remote_receipt_store_corrupt")
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_enqueue_has_one_creator_and_no_external_delivery(self) -> None:
        responses: list[object] = []
        barrier = threading.Barrier(8)

        def worker() -> None:
            barrier.wait()
            responses.append(self._op("remote_send", self._args(key="gbs_" + "d" * 32)))

        with patch("no1.daemon.group_bridge.session.send_group_bridge_session_message") as send, patch(
            "no1.daemon.messaging.chat_ops.append_event"
        ) as append:
            threads = [threading.Thread(target=worker) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            send.assert_not_called()
            append.assert_not_called()
        self.assertEqual(len(responses), 8)
        self.assertTrue(all(response.ok for response in responses))
        self.assertEqual(sum(response.result["queued"] for response in responses), 1)
        receipts = [response.result["receipt"] for response in responses if response.ok]
        self.assertEqual(len({receipt["request_fingerprint"] for receipt in receipts}), 1)


if __name__ == "__main__":
    unittest.main()
