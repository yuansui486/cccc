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

    def test_desktop_pet_enabled_can_be_set_to_true(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        group_id = self._create_group()

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

    def test_desktop_pet_enabled_can_be_toggled_back_to_false(self) -> None:
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        group_id = self._create_group()

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
