import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class _FakeGroup:
    def __init__(self, group_id: str, root: Path) -> None:
        self.group_id = group_id
        self.path = root
        self.doc = {"title": "demo", "state": "active", "actors": []}

    @property
    def ledger_path(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        path = self.path / "ledger.jsonl"
        if not path.exists():
            path.write_text("", encoding="utf-8")
        return path


class TestPetProfileRefresh(unittest.TestCase):
    def tearDown(self) -> None:
        from cccc.daemon.pet import assistive_jobs

        assistive_jobs.cancel_job("g-demo", assistive_jobs.JOB_KIND_PET_PROFILE_REFRESH)

    def test_record_user_chat_message_skips_paste_noise(self) -> None:
        from cccc.daemon.pet import profile_refresh

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            group = _FakeGroup("g-demo", root / "groups" / "g-demo")
            noisy = "https://a.example/x\nhttps://b.example/y\nhttps://c.example/z\n" + ("/tmp/demo/path\n" * 6)
            with patch.object(profile_refresh, "ensure_home", return_value=root), patch.object(
                profile_refresh.assistive_jobs,
                "ensure_home",
                return_value=root,
            ), patch.object(
                profile_refresh,
                "load_group",
                return_value=group,
            ), patch.object(profile_refresh, "maybe_request_pet_profile_refresh", return_value=False):
                result = profile_refresh.record_user_chat_message(
                    group.group_id,
                    event_id="evt-1",
                    ts="2026-03-29T10:00:00Z",
                    text=noisy,
                )

                state = profile_refresh._load_state(group.group_id)

        self.assertFalse(result["eligible"])
        self.assertEqual(int(state.get("eligible_total") or 0), 0)
        self.assertEqual(state.get("samples"), [])

    def test_bootstrap_threshold_dispatches_profile_refresh(self) -> None:
        from cccc.daemon.pet import profile_refresh

        emitted = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            group = _FakeGroup("g-demo", root / "groups" / "g-demo")
            with patch.object(profile_refresh, "ensure_home", return_value=root), patch.object(
                profile_refresh.assistive_jobs,
                "ensure_home",
                return_value=root,
            ), patch.object(
                profile_refresh,
                "load_group",
                return_value=group,
            ), patch.object(profile_refresh, "is_desktop_pet_enabled", return_value=True), patch.object(
                profile_refresh,
                "get_group_state",
                return_value="active",
            ), patch.object(
                profile_refresh,
                "get_pet_actor",
                return_value={"id": "pet-peer", "enabled": True},
            ), patch.object(profile_refresh, "emit_system_notify", side_effect=lambda grp, by, notify: emitted.append(notify)):
                for idx in range(profile_refresh.PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE - 1):
                    result = profile_refresh.record_user_chat_message(
                        group.group_id,
                        event_id=f"evt-{idx}",
                        ts=f"2026-03-29T10:00:{idx:02d}Z",
                        text=f"short direct user message {idx}",
                    )
                    self.assertTrue(result["eligible"])
                self.assertEqual(len(emitted), 0)

                result = profile_refresh.record_user_chat_message(
                    group.group_id,
                    event_id="evt-boot",
                    ts="2026-03-29T10:01:00Z",
                    text="Please ask foreman to verify the fix path first.",
                )
                state = profile_refresh._load_state(group.group_id)

        self.assertTrue(result["eligible"])
        self.assertTrue(result["requested"])
        self.assertEqual(len(emitted), 1)
        notify = emitted[0]
        self.assertEqual(str(notify.title), "Pet profile refresh requested")
        self.assertEqual(str((notify.context or {}).get("kind") or ""), "pet_profile_refresh")
        self.assertEqual(str((notify.context or {}).get("reason") or ""), "new_user_messages")
        self.assertLessEqual(
            int((notify.context or {}).get("sample_packet_size") or 0),
            profile_refresh.PET_PROFILE_REFRESH_PACKET_SIZE,
        )
        self.assertEqual(
            int(state.get("last_requested_eligible_total") or 0),
            profile_refresh.PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE,
        )

    def test_refresh_requires_twenty_new_eligible_messages_after_apply(self) -> None:
        from cccc.daemon.pet import profile_refresh

        emitted = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            group = _FakeGroup("g-demo", root / "groups" / "g-demo")
            with patch.object(profile_refresh, "ensure_home", return_value=root), patch.object(
                profile_refresh.assistive_jobs,
                "ensure_home",
                return_value=root,
            ), patch.object(
                profile_refresh,
                "load_group",
                return_value=group,
            ), patch.object(profile_refresh, "is_desktop_pet_enabled", return_value=True), patch.object(
                profile_refresh,
                "get_group_state",
                return_value="active",
            ), patch.object(
                profile_refresh,
                "get_pet_actor",
                return_value={"id": "pet-peer", "enabled": True},
            ), patch.object(profile_refresh, "PET_PROFILE_REFRESH_COOLDOWN_SECONDS", 0), patch.object(
                profile_refresh,
                "emit_system_notify",
                side_effect=lambda grp, by, notify: emitted.append(notify),
            ):
                for idx in range(profile_refresh.PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE):
                    profile_refresh.record_user_chat_message(
                        group.group_id,
                        event_id=f"evt-init-{idx}",
                        ts=f"2026-03-29T10:00:{idx:02d}Z",
                        text=f"bootstrap sample {idx}",
                    )

                profile_refresh.mark_pet_profile_refresh_applied(
                    group.group_id,
                    actor_id="pet-peer",
                    user_model="direct, brief, action-oriented",
                )

                initial_emits = len(emitted)
                for idx in range(profile_refresh.PET_PROFILE_REFRESH_DELTA - 1):
                    result = profile_refresh.record_user_chat_message(
                        group.group_id,
                        event_id=f"evt-next-{idx}",
                        ts=f"2026-03-29T11:00:{idx:02d}Z",
                        text=f"follow-up sample {idx}",
                    )
                    self.assertTrue(result["eligible"])

                self.assertEqual(len(emitted), initial_emits)

                result = profile_refresh.record_user_chat_message(
                    group.group_id,
                    event_id="evt-next-trigger",
                    ts="2026-03-29T11:05:00Z",
                    text="Please keep the reply concise and go straight to the next move.",
                )
                state = profile_refresh._load_state(group.group_id)

        self.assertTrue(result["requested"])
        self.assertEqual(len(emitted), initial_emits + 1)
        self.assertEqual(
            int(state.get("last_requested_eligible_total") or 0),
            profile_refresh.PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE + profile_refresh.PET_PROFILE_REFRESH_DELTA,
        )

    def test_mark_applied_advances_refresh_watermark(self) -> None:
        from cccc.daemon.pet import profile_refresh

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            group = _FakeGroup("g-demo", root / "groups" / "g-demo")
            with patch.object(profile_refresh, "ensure_home", return_value=root), patch.object(
                profile_refresh.assistive_jobs,
                "ensure_home",
                return_value=root,
            ), patch.object(
                profile_refresh,
                "load_group",
                return_value=group,
            ), patch.object(profile_refresh, "is_desktop_pet_enabled", return_value=True), patch.object(
                profile_refresh,
                "get_group_state",
                return_value="active",
            ), patch.object(
                profile_refresh,
                "get_pet_actor",
                return_value={"id": "pet-peer", "enabled": True},
            ), patch.object(profile_refresh, "emit_system_notify", return_value=None):
                for idx in range(profile_refresh.PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE):
                    profile_refresh.record_user_chat_message(
                        group.group_id,
                        event_id=f"evt-{idx}",
                        ts=f"2026-03-29T10:00:{idx:02d}Z",
                        text=f"bootstrap sample {idx}",
                    )
                profile_refresh.mark_pet_profile_refresh_applied(
                    group.group_id,
                    actor_id="pet-peer",
                    user_model="direct, brief",
                )
                state = profile_refresh._load_state(group.group_id)

        self.assertEqual(
            int(state.get("last_refresh_eligible_total") or 0),
            int(state.get("last_requested_eligible_total") or 0),
        )
        self.assertTrue(str(state.get("last_refresh_at") or "").strip())
        self.assertTrue(str(state.get("last_applied_user_model_hash") or "").strip())


if __name__ == "__main__":
    unittest.main()
