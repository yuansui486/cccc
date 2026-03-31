import time
import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile
import json

from cccc.daemon.pet import review_scheduler


class TestPetReviewScheduler(unittest.TestCase):
    def tearDown(self) -> None:
        review_scheduler.cancel_pet_review("g-test")

    def test_chat_messages_are_debounced_into_single_review(self) -> None:
        emitted: list[tuple[str, set[str], str]] = []

        def _capture(group_id: str, reasons: set[str], source_event_id: str) -> None:
            emitted.append((group_id, set(reasons), source_event_id))

        with patch.object(review_scheduler, "_can_review_now", return_value=True), patch.object(
            review_scheduler,
            "_emit_pet_review",
            side_effect=_capture,
        ), patch.object(review_scheduler, "PET_REVIEW_DEBOUNCE_SECONDS", 0.05), patch.object(
            review_scheduler,
            "PET_REVIEW_MIN_INTERVAL_SECONDS",
            0.01,
        ), patch.object(review_scheduler, "PET_REVIEW_MAX_DELAY_SECONDS", 0.2):
            review_scheduler.request_pet_review("g-test", reason="chat_message", source_event_id="evt-1")
            review_scheduler.request_pet_review("g-test", reason="chat_message", source_event_id="evt-2")
            review_scheduler.request_pet_review("g-test", reason="chat_reply", source_event_id="evt-3")
            time.sleep(0.12)

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], "g-test")
        self.assertEqual(emitted[0][1], {"chat_message", "chat_reply"})
        self.assertEqual(emitted[0][2], "evt-3")

    def test_immediate_review_bypasses_debounce(self) -> None:
        emitted: list[float] = []

        def _capture(group_id: str, reasons: set[str], source_event_id: str) -> None:
            del group_id, reasons, source_event_id
            emitted.append(time.monotonic())

        with patch.object(review_scheduler, "_can_review_now", return_value=True), patch.object(
            review_scheduler,
            "_emit_pet_review",
            side_effect=_capture,
        ), patch.object(review_scheduler, "PET_REVIEW_DEBOUNCE_SECONDS", 0.2), patch.object(
            review_scheduler,
            "PET_REVIEW_MIN_INTERVAL_SECONDS",
            0.01,
        ), patch.object(review_scheduler, "PET_REVIEW_MAX_DELAY_SECONDS", 0.2):
            started_at = time.monotonic()
            review_scheduler.request_pet_review(
                "g-test",
                reason="actor_stop",
                source_event_id="evt-stop",
                immediate=True,
            )
            time.sleep(0.06)

        self.assertEqual(len(emitted), 1)
        self.assertLess(emitted[0] - started_at, 0.15)

    def test_manual_review_allows_idle_group(self) -> None:
        fake_group = object()
        with patch.object(review_scheduler, "load_group", return_value=fake_group), patch.object(
            review_scheduler,
            "is_desktop_pet_enabled",
            return_value=True,
        ), patch.object(review_scheduler, "get_group_state", return_value="idle"), patch.object(
            review_scheduler,
            "get_pet_actor",
            return_value={"id": "pet-peer", "enabled": True},
        ), patch.object(review_scheduler, "emit_system_notify") as emit_notify:
            accepted = review_scheduler.request_manual_pet_review(
                "g-test",
                reason="bubble_click",
                source_event_id="evt-idle",
            )

        self.assertTrue(accepted)
        emit_notify.assert_called_once()

    def test_manual_review_rejects_paused_group(self) -> None:
        fake_group = object()
        with patch.object(review_scheduler, "load_group", return_value=fake_group), patch.object(
            review_scheduler,
            "is_desktop_pet_enabled",
            return_value=True,
        ), patch.object(review_scheduler, "get_group_state", return_value="paused"), patch.object(
            review_scheduler,
            "get_pet_actor",
            return_value={"id": "pet-peer", "enabled": True},
        ), patch.object(review_scheduler, "emit_system_notify") as emit_notify:
            accepted = review_scheduler.request_manual_pet_review(
                "g-test",
                reason="bubble_click",
                source_event_id="evt-paused",
            )

        self.assertFalse(accepted)
        emit_notify.assert_not_called()

    def test_pending_review_is_persisted_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            review_scheduler,
            "ensure_home",
            return_value=Path(tmp),
        ), patch.object(review_scheduler, "_can_review_now", return_value=True), patch.object(
            review_scheduler,
            "PET_REVIEW_DEBOUNCE_SECONDS",
            60.0,
        ), patch.object(review_scheduler, "PET_REVIEW_MIN_INTERVAL_SECONDS", 0.01), patch.object(
            review_scheduler,
            "PET_REVIEW_MAX_DELAY_SECONDS",
            60.0,
        ):
            review_scheduler.request_pet_review("g-test", reason="chat_message", source_event_id="evt-1")
            pending_path = Path(tmp) / "groups" / "g-test" / "state" / "pet_review_pending.json"
            self.assertTrue(pending_path.exists())
            payload = json.loads(pending_path.read_text(encoding="utf-8"))
            due_at_wall = float(payload.get("due_at_wall") or 0.0)
            self.assertGreater(due_at_wall, time.time() + 30.0)

        review_scheduler.cancel_pet_review("g-test")

    def test_recover_pending_review_replays_after_restart(self) -> None:
        emitted: list[tuple[str, set[str], str]] = []

        def _capture(group_id: str, reasons: set[str], source_event_id: str) -> None:
            emitted.append((group_id, set(reasons), source_event_id))

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            review_scheduler,
            "ensure_home",
            return_value=Path(tmp),
        ), patch.object(review_scheduler, "_can_review_now", return_value=True), patch.object(
            review_scheduler,
            "_emit_pet_review",
            side_effect=_capture,
        ), patch.object(review_scheduler, "PET_REVIEW_DEBOUNCE_SECONDS", 0.05), patch.object(
            review_scheduler,
            "PET_REVIEW_MIN_INTERVAL_SECONDS",
            0.01,
        ), patch.object(review_scheduler, "PET_REVIEW_MAX_DELAY_SECONDS", 0.2):
            review_scheduler.request_pet_review("g-test", reason="chat_message", source_event_id="evt-9")
            review_scheduler.cancel_pet_review("g-test")
            pending_path = Path(tmp) / "groups" / "g-test" / "state" / "pet_review_pending.json"
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.write_text(
                '{"schema":1,"group_id":"g-test","dirty_since_wall":1,"last_dispatched_wall":0,"due_at_wall":0,"reasons":["chat_message"],"source_event_id":"evt-9"}',
                encoding="utf-8",
            )
            review_scheduler.recover_pending_pet_reviews()
            time.sleep(0.08)

        self.assertEqual(emitted, [("g-test", {"chat_message"}, "evt-9")])

    def test_due_review_keeps_pending_when_group_temporarily_cannot_review(self) -> None:
        emitted: list[tuple[str, set[str], str]] = []
        can_review = {"value": False}

        def _capture(group_id: str, reasons: set[str], source_event_id: str) -> None:
            emitted.append((group_id, set(reasons), source_event_id))

        def _can_review_now(_: str) -> bool:
            return bool(can_review["value"])

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            review_scheduler,
            "ensure_home",
            return_value=Path(tmp),
        ), patch.object(review_scheduler, "_can_review_now", side_effect=_can_review_now), patch.object(
            review_scheduler,
            "_emit_pet_review",
            side_effect=_capture,
        ), patch.object(review_scheduler, "PET_REVIEW_DEBOUNCE_SECONDS", 0.02), patch.object(
            review_scheduler,
            "PET_REVIEW_MIN_INTERVAL_SECONDS",
            0.01,
        ), patch.object(review_scheduler, "PET_REVIEW_MAX_DELAY_SECONDS", 0.2):
            pending_path = Path(tmp) / "groups" / "g-test" / "state" / "pet_review_pending.json"
            pending_path.parent.mkdir(parents=True, exist_ok=True)
            pending_path.write_text(
                '{"schema":1,"group_id":"g-test","dirty_since_wall":1,"last_dispatched_wall":0,"due_at_wall":0,"reasons":["chat_message"],"source_event_id":"evt-9"}',
                encoding="utf-8",
            )
            review_scheduler.recover_pending_pet_reviews()
            self.assertEqual(emitted, [])
            self.assertTrue(pending_path.exists())

            can_review["value"] = True
            review_scheduler.request_pet_review("g-test", reason="group_state_changed", source_event_id="evt-10", immediate=True)
            time.sleep(0.08)

        self.assertEqual(emitted, [("g-test", {"chat_message", "group_state_changed"}, "evt-10")])


if __name__ == "__main__":
    unittest.main()
