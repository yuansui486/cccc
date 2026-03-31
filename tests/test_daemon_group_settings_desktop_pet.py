import os
import tempfile
import unittest


class TestDaemonGroupSettingsDesktopPet(unittest.TestCase):
    def setUp(self) -> None:
        self._old_home = os.environ.get("CCCC_HOME")
        self._td = tempfile.TemporaryDirectory()
        os.environ["CCCC_HOME"] = self._td.name

    def tearDown(self) -> None:
        self._td.cleanup()
        if self._old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = self._old_home

    def _create_group(self) -> str:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {"op": "group_create", "args": {"title": "pet-test", "topic": "", "by": "user"}}
            )
        )
        self.assertTrue(resp.ok, getattr(resp, "error", None))
        return str((resp.result or {}).get("group_id") or "").strip()

    def _add_foreman(self, group_id: str) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "actor_add",
                    "args": {
                        "group_id": group_id,
                        "actor_id": "lead",
                        "title": "Lead",
                        "runtime": "codex",
                        "runner": "headless",
                        "by": "user",
                    },
                }
            )
        )
        self.assertTrue(resp.ok, getattr(resp, "error", None))

    def test_desktop_pet_enabled_defaults_to_false(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        group_id = self._create_group()

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {"group_id": group_id, "by": "user", "patch": {"default_send_to": "foreman"}},
                }
            )
        )
        self.assertTrue(resp.ok)
        settings = (resp.result or {}).get("settings", {})
        self.assertFalse(settings.get("desktop_pet_enabled"))

    def test_desktop_pet_enabled_requires_foreman_actor(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.group import load_group

        group_id = self._create_group()

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {"group_id": group_id, "by": "user", "patch": {"desktop_pet_enabled": True}},
                }
            )
        )
        self.assertFalse(resp.ok)
        self.assertEqual(getattr(resp.error, "code", ""), "group_settings_update_failed")
        self.assertIn("foreman", str(getattr(resp.error, "message", "")))
        self.assertEqual(((getattr(resp.error, "details", {}) or {}).get("reason") or ""), "desktop_pet_requires_enabled_foreman")
        group = load_group(group_id)
        assert group is not None
        features = group.doc.get("features") if isinstance(group.doc.get("features"), dict) else {}
        self.assertFalse(bool(features.get("desktop_pet_enabled")))

    def test_desktop_pet_enabled_can_be_set_to_true(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.pet_actor import PET_ACTOR_ID, get_pet_actor
        from cccc.kernel.group import load_group

        group_id = self._create_group()
        self._add_foreman(group_id)

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {"group_id": group_id, "by": "user", "patch": {"desktop_pet_enabled": True}},
                }
            )
        )
        self.assertTrue(resp.ok)
        settings = (resp.result or {}).get("settings", {})
        self.assertTrue(settings.get("desktop_pet_enabled"))
        event = (resp.result or {}).get("event", {})
        self.assertEqual(event.get("kind"), "group.settings_update")
        self.assertTrue(((event.get("data") or {}).get("patch") or {}).get("desktop_pet_enabled"))
        group = load_group(group_id)
        assert group is not None
        pet_actor = get_pet_actor(group)
        self.assertIsNotNone(pet_actor)
        self.assertEqual(str((pet_actor or {}).get("id") or ""), PET_ACTOR_ID)

    def test_enabling_desktop_pet_bootstraps_profile_refresh_from_existing_user_history(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.daemon.pet.profile_refresh import PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE
        from cccc.kernel.group import load_group
        from cccc.kernel.inbox import iter_events

        group_id = self._create_group()
        self._add_foreman(group_id)
        attach_resp, _ = handle_request(
            DaemonRequest.model_validate(
                {"op": "attach", "args": {"group_id": group_id, "path": ".", "by": "user"}}
            )
        )
        self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))
        start_resp, _ = handle_request(
            DaemonRequest.model_validate(
                {"op": "group_start", "args": {"group_id": group_id, "by": "user"}}
            )
        )
        self.assertTrue(start_resp.ok, getattr(start_resp, "error", None))

        for idx in range(PET_PROFILE_BOOTSTRAP_MIN_ELIGIBLE):
            send_resp, _ = handle_request(
                DaemonRequest.model_validate(
                    {
                        "op": "send",
                        "args": {
                            "group_id": group_id,
                            "by": "user",
                            "text": f"bootstrap sample {idx}",
                            "to": ["@foreman"],
                        },
                    }
                )
            )
            self.assertTrue(send_resp.ok, getattr(send_resp, "error", None))

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {"group_id": group_id, "by": "user", "patch": {"desktop_pet_enabled": True}},
                }
            )
        )
        self.assertTrue(resp.ok, getattr(resp, "error", None))

        group = load_group(group_id)
        assert group is not None
        profile_refresh_notifies = [
            ev
            for ev in iter_events(group.ledger_path)
            if str(ev.get("kind") or "").strip() == "system.notify"
            and str(((ev.get("data") or {}).get("title") or "")).strip() == "Pet profile refresh requested"
        ]
        self.assertEqual(len(profile_refresh_notifies), 1)

    def test_desktop_pet_enabled_can_be_toggled_back_to_false(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        group_id = self._create_group()
        self._add_foreman(group_id)

        handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {"group_id": group_id, "by": "user", "patch": {"desktop_pet_enabled": True}},
                }
            )
        )

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {"group_id": group_id, "by": "user", "patch": {"desktop_pet_enabled": False}},
                }
            )
        )
        self.assertTrue(resp.ok)
        settings = (resp.result or {}).get("settings", {})
        self.assertFalse(settings.get("desktop_pet_enabled"))
        event = (resp.result or {}).get("event", {})
        self.assertEqual(event.get("kind"), "group.settings_update")
        self.assertIs(((event.get("data") or {}).get("patch") or {}).get("desktop_pet_enabled"), False)

    def test_desktop_pet_enabled_tolerates_dirty_value(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.group import load_group

        group_id = self._create_group()

        group = load_group(group_id)
        assert group is not None
        group.doc["features"] = {"desktop_pet_enabled": "garbage", "panorama_enabled": True}
        group.save()

        resp, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {"group_id": group_id, "by": "user", "patch": {"default_send_to": "foreman"}},
                }
            )
        )
        self.assertTrue(resp.ok)
        settings = (resp.result or {}).get("settings", {})
        self.assertFalse(settings.get("desktop_pet_enabled"))
        self.assertTrue(settings.get("panorama_enabled"))


if __name__ == "__main__":
    unittest.main()
