import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cccc.kernel.pet_task_proposals import (
    build_task_proposal_candidates,
    build_task_proposal_summary_lines,
)


def _task(
    task_id: str,
    *,
    title: str,
    status: str = "planned",
    assignee: str = "",
    blocked_by: list[str] | None = None,
    waiting_on: str = "none",
    handoff_to: str = "",
):
    return SimpleNamespace(
        id=task_id,
        title=title,
        status=SimpleNamespace(value=status),
        assignee=assignee,
        blocked_by=list(blocked_by or []),
        waiting_on=SimpleNamespace(value=waiting_on),
        handoff_to=handoff_to,
    )


class TestPetTaskProposals(unittest.TestCase):
    def test_build_task_proposal_candidates_keeps_only_highest_priority_item(self) -> None:
        tasks = [
            _task("T1", title="Need user scope", status="active", waiting_on="user"),
            _task("T2", title="Review handoff", status="active", handoff_to="foreman"),
            _task("T3", title="Blocked on peer output", status="active", blocked_by=["T9"]),
            _task("T4", title="Backlog cleanup", status="planned"),
            _task("T5", title="Already done", status="done", waiting_on="user"),
        ]

        proposals = build_task_proposal_candidates(tasks)

        self.assertEqual(len(proposals), 1)
        self.assertEqual([item["reason"] for item in proposals], ["waiting_user"])
        self.assertEqual(proposals[0]["action"]["task_id"], "T1")

    def test_waiting_user_candidate_prefers_move_from_planned_to_active(self) -> None:
        proposal = build_task_proposal_candidates(
            [_task("T10", title="Need product answer", status="planned", waiting_on="user")]
        )[0]

        self.assertEqual(proposal["reason"], "waiting_user")
        self.assertEqual(proposal["action"]["operation"], "move")
        self.assertEqual(proposal["action"]["status"], "active")

    def test_handoff_and_blocked_candidates_keep_expected_actions(self) -> None:
        proposals = build_task_proposal_candidates(
            [
                _task("T20", title="Take over runtime fix", status="active", handoff_to="peer-debugger"),
                _task("T21", title="Need external API back", status="active", waiting_on="external"),
            ],
            limit=2,
        )

        self.assertEqual(proposals[0]["action"]["operation"], "handoff")
        self.assertEqual(proposals[0]["action"]["assignee"], "peer-debugger")
        self.assertEqual(proposals[1]["action"]["operation"], "update")
        self.assertEqual(proposals[1]["reason"], "blocked")

    def test_build_task_proposal_summary_lines_defaults_to_single_item(self) -> None:
        lines = build_task_proposal_summary_lines(
            [
                _task("T1", title="Need user scope", status="active", waiting_on="user"),
                _task("T2", title="Review handoff", status="active", handoff_to="foreman"),
                _task("T3", title="Blocked on peer output", status="active", blocked_by=["T9"]),
            ],
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("T1:Need user scope", lines[0])
        self.assertIn("waiting on the user", lines[0])

    def test_build_task_proposal_candidates_follow_proposal_ready_focus(self) -> None:
        proposals = build_task_proposal_candidates(
            [
                _task("T1", title="Need user scope", status="active", waiting_on="user"),
                _task("T2", title="Blocked on peer output", status="active", blocked_by=["T9"]),
            ],
            signal_payload={
                "proposal_ready": {
                    "ready": True,
                    "focus": "blocked",
                }
            },
        )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["reason"], "blocked")
        self.assertEqual(proposals[0]["action"]["task_id"], "T2")

    def test_build_task_proposal_candidates_suppresses_recent_user_echo_for_same_task(self) -> None:
        with patch("cccc.kernel.pet_task_proposals.utc_now_iso", return_value="2026-03-28T00:12:00Z"):
            proposals = build_task_proposal_candidates(
                [_task("T192", title="Bridge 出站链路 stream_emit daemon op", status="active", blocked_by=["T191"])],
                recent_chat_events=[
                    {
                        "id": "evt-1",
                        "kind": "chat.message",
                        "by": "user",
                        "ts": "2026-03-28T00:05:00Z",
                        "data": {
                            "to": ["@foreman"],
                            "text": 'Pet task proposal: please use cccc_task to update this task (task_id=T192, title="Bridge 出站链路 stream_emit daemon op").',
                        },
                    }
                ],
            )

        self.assertEqual(proposals, [])

    def test_build_task_proposal_candidates_keeps_plain_discussion_about_same_task(self) -> None:
        with patch("cccc.kernel.pet_task_proposals.utc_now_iso", return_value="2026-03-28T00:12:00Z"):
            proposals = build_task_proposal_candidates(
                [_task("T192", title="Bridge 出站链路 stream_emit daemon op", status="active", blocked_by=["T191"])],
                recent_chat_events=[
                    {
                        "id": "evt-1",
                        "kind": "chat.message",
                        "by": "user",
                        "ts": "2026-03-28T00:05:00Z",
                        "data": {
                            "to": ["@foreman"],
                            "text": "先协调 T192 的解阻路径，确认 Bridge 出站链路 stream_emit daemon op 还缺哪条运行态证据。",
                        },
                    }
                ],
            )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["action"]["task_id"], "T192")

    def test_build_task_proposal_candidates_keeps_other_task_when_recent_echo_is_for_different_task(self) -> None:
        with patch("cccc.kernel.pet_task_proposals.utc_now_iso", return_value="2026-03-28T00:12:00Z"):
            proposals = build_task_proposal_candidates(
                [
                    _task("T192", title="Bridge 出站链路 stream_emit daemon op", status="active", blocked_by=["T191"]),
                    _task("T193", title="Repair pet duplicate reminders", status="active", blocked_by=["T192"]),
                ],
                recent_chat_events=[
                    {
                        "id": "evt-1",
                        "kind": "chat.message",
                        "by": "user",
                        "ts": "2026-03-28T00:05:00Z",
                        "data": {
                            "to": ["@foreman"],
                            "text": 'Pet task proposal: please use cccc_task to update this task (task_id=T192, title="Bridge 出站链路 stream_emit daemon op").',
                        },
                    }
                ],
                limit=2,
            )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["action"]["task_id"], "T193")

    def test_build_task_proposal_candidates_still_checks_recent_user_echo_after_non_user_chatter(self) -> None:
        with patch("cccc.kernel.pet_task_proposals.utc_now_iso", return_value="2026-03-28T00:12:00Z"):
            chatter = [
                {
                    "id": f"evt-chatter-{idx}",
                    "kind": "chat.message",
                    "by": "claude-1" if idx % 2 == 0 else "peer-debugger",
                    "ts": f"2026-03-28T00:{11 - (idx % 6):02d}:00Z",
                    "data": {
                        "to": ["@all"],
                        "text": f"status update {idx}",
                    },
                }
                for idx in range(13)
            ]
            proposals = build_task_proposal_candidates(
                [_task("T192", title="Bridge 出站链路 stream_emit daemon op", status="active", blocked_by=["T191"])],
                recent_chat_events=chatter
                + [
                    {
                        "id": "evt-user-1",
                        "kind": "chat.message",
                        "by": "user",
                        "ts": "2026-03-28T00:05:00Z",
                        "data": {
                            "to": ["@foreman"],
                            "text": 'Pet task proposal: please use cccc_task to update this task (task_id=T192, title="Bridge 出站链路 stream_emit daemon op").',
                        },
                    }
                ],
            )

        self.assertEqual(proposals, [])


if __name__ == "__main__":
    unittest.main()
