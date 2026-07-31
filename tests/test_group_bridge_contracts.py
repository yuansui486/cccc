import unittest


class TestGroupBridgeContracts(unittest.TestCase):
    def test_access_defaults_to_messages(self) -> None:
        from no1.contracts.v1.group_bridge import (
            DEFAULT_GROUP_BRIDGE_ACCESS_LEVEL,
            GROUP_BRIDGE_ACCESS_LEVELS,
            RegistrationRecord,
        )

        self.assertEqual(DEFAULT_GROUP_BRIDGE_ACCESS_LEVEL, "messages")
        self.assertEqual(GROUP_BRIDGE_ACCESS_LEVELS, ("messages", "read", "full"))
        self.assertNotIn("access_level", RegistrationRecord.model_fields)

    def test_payload_defaults_and_forbids_extra(self) -> None:
        from no1.contracts.v1.group_bridge import RemoteSendPayload

        payload = RemoteSendPayload(text="hi")
        self.assertEqual(payload.priority, "normal")
        self.assertFalse(payload.reply_required)
        self.assertEqual(payload.to, [])
        self.assertEqual(payload.refs, [])
        self.assertEqual(payload.attachments, [])
        with self.assertRaises(Exception):
            RemoteSendPayload(text="hi", bogus=1)

    def test_persisted_models_have_no_raw_secret_fields(self) -> None:
        from no1.contracts.v1.group_bridge import RegistrationRecord, RemoteSendReceipt

        for model in (RegistrationRecord, RemoteSendReceipt):
            self.assertNotIn("token", model.model_fields)
            self.assertNotIn("credential", model.model_fields)
        self.assertIn("credential_ref", RegistrationRecord.model_fields)
        self.assertIn("request_fingerprint", RemoteSendReceipt.model_fields)

    def test_receipt_and_error_roundtrip(self) -> None:
        from no1.contracts.v1.group_bridge import RemoteSendError, RemoteSendReceipt

        receipt = RemoteSendReceipt(
            status="failed",
            registration_id="reg_1",
            idempotency_key="k1",
            error=RemoteSendError(code="transport_error", message="public", retriable=True),
        ).model_dump()
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["error"]["code"], "transport_error")
        self.assertTrue(receipt["error"]["retriable"])

    def test_envelope_wraps_payload(self) -> None:
        from no1.contracts.v1.group_bridge import RemoteSendEnvelope, RemoteSendPayload

        envelope = RemoteSendEnvelope(
            src_group_id="g1",
            registration_id="reg_1",
            idempotency_key="k1",
            payload=RemoteSendPayload(text="hi"),
        )
        self.assertEqual(envelope.payload.text, "hi")


if __name__ == "__main__":
    unittest.main()
