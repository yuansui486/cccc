import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestAsyncFirstDelivery(unittest.TestCase):
    def _group(self):
        return SimpleNamespace(group_id="g-test", doc={})

    def test_openclaw_first_delivery_submits_preamble_and_message_as_one_turn(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        message = delivery.PendingMessage(
            event_id="e1",
            by="user",
            to=["peer1"],
            text="hello first",
        )
        submitted: list[str] = []
        order: list[str] = []
        worker_done = threading.Event()
        attempt = {"attempt_id": "attempt-1"}

        def submit(_group, *, text: str, **_kwargs):
            order.append("submit")
            submitted.append(text)
            return delivery.PtySubmitOutcome(True, False, "accepted")

        with patch(
            "no1.daemon.messaging.delivery.render_system_prompt",
            return_value="SYSTEM PROMPT",
        ), patch(
            "no1.daemon.messaging.delivery.begin_turn_delivery_attempt",
            return_value=attempt,
        ), patch(
            "no1.daemon.messaging.delivery.append_turn_completion_receipt",
            side_effect=lambda text, _attempt: f"COMPLETION RECEIPT\n{text}",
        ), patch(
            "no1.daemon.messaging.delivery.append_turn_grant_receipt",
            side_effect=lambda text, _attempt: f"TURN GRANT RECEIPT\n{text}",
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text",
            side_effect=submit,
        ), patch(
            "no1.daemon.messaging.delivery.mark_preamble_sent",
            side_effect=lambda *_args: order.append("mark_preamble"),
        ), patch(
            "no1.daemon.messaging.delivery._finalize_delivery_success",
            side_effect=lambda *_args, **_kwargs: order.append("finalize"),
        ), patch(
            "no1.daemon.messaging.delivery._finish_delivery_chain",
            side_effect=lambda *_args, **_kwargs: worker_done.set(),
        ):
            delivery._start_async_first_delivery(
                group,
                actor_id="peer1",
                messages=[message],
                deliverable=[message],
                requeue=[],
                message_text="hello first",
                chat_total=1,
                actor={"id": "peer1", "runtime": "openclaw", "runner": "pty"},
                experience_decision=unittest.mock.Mock(),
            )

            self.assertTrue(worker_done.wait(1.0))

        self.assertEqual(len(submitted), 1)
        self.assertIn("SYSTEM PROMPT", submitted[0])
        self.assertIn("COMPLETION RECEIPT", submitted[0])
        self.assertIn("TURN GRANT RECEIPT", submitted[0])
        self.assertIn("hello first", submitted[0])
        self.assertEqual(order, ["submit", "mark_preamble", "finalize"])

    def test_openclaw_retryable_first_delivery_requeues_complete_batch(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        message = delivery.PendingMessage(
            event_id="e1",
            by="user",
            to=["peer1"],
            text="hello first",
        )
        attempt = {"attempt_id": "attempt-1"}
        failed = threading.Event()
        requeued = threading.Event()
        worker_done = threading.Event()

        with patch(
            "no1.daemon.messaging.delivery.render_system_prompt",
            return_value="SYSTEM PROMPT",
        ), patch(
            "no1.daemon.messaging.delivery.begin_turn_delivery_attempt",
            return_value=attempt,
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text",
            return_value=delivery.PtySubmitOutcome(False, True, "pre_write_failed", "not running"),
        ), patch(
            "no1.daemon.messaging.delivery.fail_turn_delivery_attempt",
            side_effect=lambda *_args, **_kwargs: failed.set(),
        ), patch.object(
            delivery.THROTTLE,
            "requeue_front",
            side_effect=lambda _gid, _aid, messages: requeued.set() if messages == [message] else None,
        ), patch(
            "no1.daemon.messaging.delivery._finish_delivery_chain",
            side_effect=lambda *_args, **_kwargs: worker_done.set(),
        ), patch(
            "no1.daemon.messaging.delivery.mark_preamble_sent",
        ) as mark_preamble, patch(
            "no1.daemon.messaging.delivery._finalize_delivery_success",
        ) as finalize:
            delivery._start_async_first_delivery(
                group,
                actor_id="peer1",
                messages=[message],
                deliverable=[message],
                requeue=[],
                message_text="hello first",
                chat_total=1,
                actor={"id": "peer1", "runtime": "openclaw", "runner": "pty"},
                experience_decision=unittest.mock.Mock(),
            )

            self.assertTrue(worker_done.wait(1.0))

        self.assertTrue(failed.is_set())
        self.assertTrue(requeued.is_set())
        mark_preamble.assert_not_called()
        finalize.assert_not_called()

    def test_openclaw_accepted_first_delivery_never_requeues_sent_messages(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        delivered = delivery.PendingMessage(event_id="e1", by="user", to=["peer1"], text="hello")
        blocked = delivery.PendingMessage(event_id="e2", by="peer2", to=["peer1"], text="later")
        attempt = {"attempt_id": "attempt-1", "generation": 1}
        requeued: list[list[delivery.PendingMessage]] = []
        worker_done = threading.Event()

        with patch(
            "no1.daemon.messaging.delivery.render_system_prompt",
            return_value="SYSTEM PROMPT",
        ), patch(
            "no1.daemon.messaging.delivery.begin_turn_delivery_attempt",
            return_value=attempt,
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text",
            return_value=delivery.PtySubmitOutcome(True, False, "accepted"),
        ), patch(
            "no1.daemon.messaging.delivery.mark_preamble_sent",
        ), patch(
            "no1.daemon.messaging.delivery._finalize_delivery_success",
            side_effect=RuntimeError("ledger unavailable after PTY acceptance"),
        ), patch(
            "no1.daemon.messaging.delivery.terminalize_uncertain_delivery_attempt",
            side_effect=OSError("state file locked"),
        ), patch(
            "no1.daemon.messaging.delivery.append_event",
        ), patch(
            "no1.daemon.messaging.delivery.fail_turn_delivery_attempt",
        ) as fail_attempt, patch.object(
            delivery.THROTTLE,
            "requeue_front",
            side_effect=lambda _gid, _aid, batch: requeued.append(list(batch)),
        ), patch.object(
            delivery.THROTTLE,
            "end_delivery",
            side_effect=lambda *_args: worker_done.set(),
        ):
            delivery._start_async_first_delivery(
                group,
                actor_id="peer1",
                messages=[delivered, blocked],
                deliverable=[delivered],
                requeue=[blocked],
                message_text="hello",
                chat_total=1,
                actor={"id": "peer1", "runtime": "openclaw", "runner": "pty"},
                experience_decision=unittest.mock.Mock(),
            )

            self.assertTrue(worker_done.wait(1.0))

        self.assertEqual(requeued, [[blocked]])
        fail_attempt.assert_not_called()

    def test_first_flush_returns_without_waiting_for_preamble_submit(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        preamble_state = {"sent": False}
        prompt_started = threading.Event()
        message_sent = threading.Event()

        def fake_submit(
            _group,
            *,
            actor_id: str,
            text: str,
            file_fallback: bool = False,
            wait_for_submit: bool = False,
            detailed_result: bool = False,
        ):
            self.assertEqual(actor_id, "peer1")
            self.assertTrue(wait_for_submit)
            self.assertTrue(detailed_result)
            if text == "SYSTEM PROMPT":
                prompt_started.set()
                time.sleep(0.2)
            else:
                self.assertIn("hello first", text)
                message_sent.set()
            return delivery.PtySubmitOutcome(True, False, "accepted")

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.find_actor", return_value={"id": "peer1", "runner": "pty"}
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.render_system_prompt", return_value="SYSTEM PROMPT"
        ), patch(
            "no1.daemon.messaging.delivery.is_preamble_sent", side_effect=lambda _group, _aid: bool(preamble_state["sent"])
        ), patch(
            "no1.daemon.messaging.delivery.mark_preamble_sent", side_effect=lambda _group, _aid: preamble_state.__setitem__("sent", True)
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text", side_effect=fake_submit
        ), patch(
            "no1.daemon.messaging.delivery.commit_experience_reminder", return_value=None
        ), patch.object(
            delivery, "PREAMBLE_TO_MESSAGE_DELAY_SECONDS", 0.0
        ):
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello first",
                ts="2026-03-23T00:00:00Z",
            )

            started = time.monotonic()
            result = delivery.flush_pending_messages(group, actor_id="peer1")
            elapsed = time.monotonic() - started

            self.assertTrue(result)
            self.assertLess(elapsed, 0.1)
            self.assertTrue(prompt_started.wait(0.2))
            self.assertTrue(message_sent.wait(1.0))

    def test_openclaw_first_flush_submits_preamble_and_message_as_one_turn(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        submitted = threading.Event()
        submitted_text: list[str] = []

        def fake_submit(
            _group,
            *,
            actor_id: str,
            text: str,
            file_fallback: bool = False,
            wait_for_submit: bool = False,
            detailed_result: bool = False,
        ):
            self.assertEqual(actor_id, "peer1")
            self.assertTrue(wait_for_submit)
            self.assertTrue(detailed_result)
            submitted_text.append(text)
            submitted.set()
            return delivery.PtySubmitOutcome(True, False, "accepted")

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.find_actor",
            return_value={"id": "peer1", "runner": "pty", "runtime": "openclaw"},
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.render_system_prompt", return_value="SYSTEM PROMPT"
        ), patch("no1.daemon.messaging.delivery.is_preamble_sent", return_value=False), patch(
            "no1.daemon.messaging.delivery.mark_preamble_sent"
        ) as mark_preamble, patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text", side_effect=fake_submit
        ), patch(
            "no1.daemon.messaging.delivery.begin_turn_delivery_attempt", return_value={"attempt_id": "attempt-a"}
        ), patch(
            "no1.daemon.messaging.delivery.append_turn_completion_receipt", side_effect=lambda text, _attempt: text
        ), patch(
            "no1.daemon.messaging.delivery.append_turn_grant_receipt", side_effect=lambda text, _attempt: text
        ), patch(
            "no1.daemon.messaging.delivery._finalize_delivery_success"
        ) as finalize:
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello OpenClaw",
                ts="2026-03-23T00:00:00Z",
            )

            self.assertTrue(delivery.flush_pending_messages(group, actor_id="peer1"))
            self.assertTrue(submitted.wait(1.0))

        self.assertEqual(len(submitted_text), 1)
        self.assertIn("SYSTEM PROMPT", submitted_text[0])
        self.assertIn("hello OpenClaw", submitted_text[0])
        mark_preamble.assert_called_once_with(group, "peer1")
        finalize.assert_called_once()

    def test_async_first_delivery_serializes_followup_flushes(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        preamble_state = {"sent": False}
        prompt_started = threading.Event()
        release_prompt = threading.Event()
        second_sent = threading.Event()
        sent_messages: list[str] = []

        def fake_submit(
            _group,
            *,
            actor_id: str,
            text: str,
            file_fallback: bool = False,
            wait_for_submit: bool = False,
            detailed_result: bool = False,
        ):
            self.assertEqual(actor_id, "peer1")
            self.assertTrue(wait_for_submit)
            self.assertTrue(detailed_result)
            if text == "SYSTEM PROMPT":
                prompt_started.set()
                self.assertTrue(release_prompt.wait(1.0))
            else:
                sent_messages.append(text)
                if "hello second" in text:
                    second_sent.set()
            return delivery.PtySubmitOutcome(True, False, "accepted")

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.find_actor", return_value={"id": "peer1", "runner": "pty"}
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.render_system_prompt", return_value="SYSTEM PROMPT"
        ), patch(
            "no1.daemon.messaging.delivery.is_preamble_sent", side_effect=lambda _group, _aid: bool(preamble_state["sent"])
        ), patch(
            "no1.daemon.messaging.delivery.mark_preamble_sent", side_effect=lambda _group, _aid: preamble_state.__setitem__("sent", True)
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text", side_effect=fake_submit
        ), patch(
            "no1.daemon.messaging.delivery.commit_experience_reminder", return_value=None
        ), patch.object(
            delivery, "PREAMBLE_TO_MESSAGE_DELAY_SECONDS", 0.0
        ):
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello first",
                ts="2026-03-23T00:00:00Z",
            )
            self.assertTrue(delivery.flush_pending_messages(group, actor_id="peer1"))
            self.assertTrue(prompt_started.wait(0.2))

            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e2",
                by="user",
                to=["@all"],
                text="hello second",
                ts="2026-03-23T00:00:01Z",
            )
            self.assertFalse(delivery.flush_pending_messages(group, actor_id="peer1"))
            self.assertFalse(second_sent.is_set())

            release_prompt.set()
            self.assertTrue(second_sent.wait(1.0))
            self.assertGreaterEqual(len(sent_messages), 2)
            self.assertIn("hello first", sent_messages[0])
            self.assertIn("hello second", sent_messages[1])

    def test_async_first_delivery_requeues_messages_when_worker_raises(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        preamble_state = {"sent": False}
        prompt_started = threading.Event()

        def fake_submit(
            _group,
            *,
            actor_id: str,
            text: str,
            file_fallback: bool = False,
            wait_for_submit: bool = False,
            detailed_result: bool = False,
        ):
            self.assertEqual(actor_id, "peer1")
            self.assertTrue(wait_for_submit)
            self.assertTrue(detailed_result)
            if text == "SYSTEM PROMPT":
                prompt_started.set()
            return delivery.PtySubmitOutcome(True, False, "accepted")

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.find_actor", return_value={"id": "peer1", "runner": "pty"}
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.render_system_prompt", return_value="SYSTEM PROMPT"
        ), patch(
            "no1.daemon.messaging.delivery.is_preamble_sent", side_effect=lambda _group, _aid: bool(preamble_state["sent"])
        ), patch(
            "no1.daemon.messaging.delivery.mark_preamble_sent", side_effect=RuntimeError("boom during mark_preamble_sent")
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times", return_value=(None, None)
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text", side_effect=fake_submit
        ), patch.object(
            delivery, "PREAMBLE_TO_MESSAGE_DELAY_SECONDS", 0.0
        ):
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello first",
                ts="2026-03-23T00:00:00Z",
            )
            self.assertTrue(delivery.flush_pending_messages(group, actor_id="peer1"))
            self.assertTrue(prompt_started.wait(0.2))

            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if delivery.THROTTLE.has_pending("g-test", "peer1"):
                    break
                time.sleep(0.01)

            self.assertTrue(delivery.THROTTLE.has_pending("g-test", "peer1"))
            reacquired = False
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if delivery.THROTTLE.try_begin_delivery("g-test", "peer1"):
                    reacquired = True
                    break
                time.sleep(0.01)

            self.assertTrue(reacquired)
            delivery.THROTTLE.end_delivery("g-test", "peer1")

    def test_first_flush_waits_for_bracketed_paste_before_preamble(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        submit_mock = unittest.mock.Mock(return_value=True)
        now = time.monotonic()

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.find_actor", return_value={"id": "peer1", "runner": "pty"}
        ), patch("no1.daemon.messaging.delivery.should_deliver_message", return_value=True), patch(
            "no1.daemon.messaging.delivery.render_system_prompt", return_value="SYSTEM PROMPT"
        ), patch(
            "no1.daemon.messaging.delivery.is_preamble_sent", return_value=False
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.startup_times",
            return_value=(now - 1.0, now - 2.0),
        ), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.bracketed_paste_status",
            return_value=(False, None),
        ), patch(
            "no1.daemon.messaging.delivery.pty_submit_text", submit_mock
        ):
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello first",
                ts="2026-03-23T00:00:00Z",
            )

            self.assertFalse(delivery.flush_pending_messages(group, actor_id="peer1"))
            self.assertTrue(delivery.THROTTLE.has_pending("g-test", "peer1"))
            submit_mock.assert_not_called()

    def test_request_flush_retries_until_delivery_window_opens(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        delivered = threading.Event()
        flush_calls = {"count": 0}

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.get_group_state", return_value="active"
        ), patch.object(
            delivery, "ASYNC_FLUSH_POLL_SECONDS", 0.01
        ), patch.object(
            delivery, "ASYNC_FLUSH_MAX_WAIT_SECONDS", 0.2
        ):
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello first",
                ts="2026-03-23T00:00:00Z",
            )

            original_next_retry_delay = delivery.THROTTLE.next_retry_delay

            def fake_flush(_group, *, actor_id: str) -> bool:
                self.assertEqual(actor_id, "peer1")
                flush_calls["count"] += 1
                if flush_calls["count"] == 1:
                    pending = delivery.THROTTLE.take_pending("g-test", "peer1")
                    delivery.THROTTLE.requeue_front("g-test", "peer1", pending)
                    return False
                pending = delivery.THROTTLE.take_pending("g-test", "peer1")
                self.assertEqual(len(pending), 1)
                delivery.THROTTLE.mark_delivered("g-test", "peer1")
                delivered.set()
                return True

            with patch(
                "no1.daemon.messaging.delivery.flush_pending_messages", side_effect=fake_flush
            ), patch.object(
                delivery.THROTTLE, "next_retry_delay", side_effect=lambda gid, aid, interval: (
                    0.0 if flush_calls["count"] == 0 else original_next_retry_delay(gid, aid, interval)
                ),
            ):
                self.assertTrue(delivery.request_flush_pending_messages(group, actor_id="peer1"))
                self.assertTrue(delivered.wait(1.0))

            self.assertGreaterEqual(flush_calls["count"], 2)

    def test_request_flush_stops_when_actor_is_not_running(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        flush_called = threading.Event()

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=False
        ), patch(
            "no1.daemon.messaging.delivery.get_group_state", return_value="active"
        ), patch.object(
            delivery, "ASYNC_FLUSH_POLL_SECONDS", 0.01
        ), patch.object(
            delivery, "ASYNC_FLUSH_MAX_WAIT_SECONDS", 0.2
        ):
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello first",
                ts="2026-03-23T00:00:00Z",
            )

            def fake_flush(_group, *, actor_id: str) -> bool:
                self.assertEqual(actor_id, "peer1")
                flush_called.set()
                return False

            with patch("no1.daemon.messaging.delivery.flush_pending_messages", side_effect=fake_flush):
                self.assertTrue(delivery.request_flush_pending_messages(group, actor_id="peer1"))
                self.assertTrue(flush_called.wait(1.0))
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    with delivery._ASYNC_FLUSH_LOCK:
                        if ("g-test", "peer1") not in delivery._ASYNC_FLUSH_IN_FLIGHT:
                            break
                    time.sleep(0.01)

            with delivery._ASYNC_FLUSH_LOCK:
                self.assertNotIn(("g-test", "peer1"), delivery._ASYNC_FLUSH_IN_FLIGHT)
            self.assertTrue(delivery.THROTTLE.has_pending("g-test", "peer1"))

    def test_request_flush_stops_when_group_is_paused(self) -> None:
        from no1.daemon.messaging import delivery

        group = self._group()
        flush_called = threading.Event()

        with patch.object(delivery, "THROTTLE", delivery.DeliveryThrottle()), patch(
            "no1.daemon.messaging.delivery.pty_runner.SUPERVISOR.actor_running", return_value=True
        ), patch(
            "no1.daemon.messaging.delivery.get_group_state", return_value="paused"
        ), patch.object(
            delivery, "ASYNC_FLUSH_POLL_SECONDS", 0.01
        ), patch.object(
            delivery, "ASYNC_FLUSH_MAX_WAIT_SECONDS", 0.2
        ):
            delivery.queue_chat_message(
                group,
                actor_id="peer1",
                event_id="e1",
                by="user",
                to=["@all"],
                text="hello first",
                ts="2026-03-23T00:00:00Z",
            )

            def fake_flush(_group, *, actor_id: str) -> bool:
                self.assertEqual(actor_id, "peer1")
                flush_called.set()
                return False

            with patch("no1.daemon.messaging.delivery.flush_pending_messages", side_effect=fake_flush):
                self.assertTrue(delivery.request_flush_pending_messages(group, actor_id="peer1"))
                self.assertTrue(flush_called.wait(1.0))
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    with delivery._ASYNC_FLUSH_LOCK:
                        if ("g-test", "peer1") not in delivery._ASYNC_FLUSH_IN_FLIGHT:
                            break
                    time.sleep(0.01)

            with delivery._ASYNC_FLUSH_LOCK:
                self.assertNotIn(("g-test", "peer1"), delivery._ASYNC_FLUSH_IN_FLIGHT)
            self.assertTrue(delivery.THROTTLE.has_pending("g-test", "peer1"))


if __name__ == "__main__":
    unittest.main()
