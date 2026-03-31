from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TestServerRequestQueueRouting(unittest.TestCase):
    def test_message_ops_use_fast_queue_when_group_not_idle(self) -> None:
        from cccc.daemon.server import _request_queue_for

        fast_queue = object()
        slow_queue = object()
        req = SimpleNamespace(op="reply", args={"group_id": "g1"})

        with patch("cccc.daemon.server.load_group", return_value=object()), patch(
            "cccc.daemon.server.get_group_state", return_value="active"
        ):
            selected = _request_queue_for(req, fast_queue=fast_queue, slow_queue=slow_queue)

        self.assertIs(selected, fast_queue)
        self.assertEqual(req.args.get("__group_state_at_accept"), "active")

    def test_message_ops_use_fast_queue_even_when_group_idle(self) -> None:
        from cccc.daemon.server import _request_queue_for

        fast_queue = object()
        slow_queue = object()
        req = SimpleNamespace(op="send", args={"group_id": "g1"})

        with patch("cccc.daemon.server.load_group", return_value=object()), patch(
            "cccc.daemon.server.get_group_state", return_value="idle"
        ):
            selected = _request_queue_for(req, fast_queue=fast_queue, slow_queue=slow_queue)

        self.assertIs(selected, fast_queue)
        self.assertEqual(req.args.get("__group_state_at_accept"), "idle")

    def test_non_message_ops_use_slow_queue(self) -> None:
        from cccc.daemon.server import _request_queue_for

        fast_queue = object()
        slow_queue = object()
        req = SimpleNamespace(op="context_get", args={"group_id": "g1"})

        selected = _request_queue_for(req, fast_queue=fast_queue, slow_queue=slow_queue)

        self.assertIs(selected, slow_queue)
