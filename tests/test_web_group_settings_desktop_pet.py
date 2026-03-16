import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class TestWebGroupSettingsDesktopPet(unittest.TestCase):
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

    def _create_group(self):
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        group = create_group(reg, title="web-pet-test", topic="")
        return group.group_id

    def test_get_settings_desktop_pet_defaults_to_false(self) -> None:
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            app = create_app()
            client = TestClient(app)

            resp = client.get(f"/api/v1/groups/{group_id}/settings")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body.get("ok"))
            settings = (body.get("result") or {}).get("settings", {})
            self.assertFalse(settings.get("desktop_pet_enabled"))
            # panorama_enabled also defaults to false
            self.assertFalse(settings.get("panorama_enabled"))
        finally:
            cleanup()

    def test_get_settings_desktop_pet_enabled_after_manual_set(self) -> None:
        from cccc.kernel.group import load_group
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()

            loaded = load_group(group_id)
            assert loaded is not None
            loaded.doc["features"] = {"desktop_pet_enabled": True}
            loaded.save()

            app = create_app()
            client = TestClient(app)
            resp = client.get(f"/api/v1/groups/{group_id}/settings")
            self.assertEqual(resp.status_code, 200)
            settings = (resp.json().get("result") or {}).get("settings", {})
            self.assertTrue(settings.get("desktop_pet_enabled"))
        finally:
            cleanup()

    def test_get_settings_tolerates_dirty_desktop_pet_value(self) -> None:
        from cccc.kernel.group import load_group
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()

            loaded = load_group(group_id)
            assert loaded is not None
            loaded.doc["features"] = {"desktop_pet_enabled": "garbage", "panorama_enabled": True}
            loaded.save()

            app = create_app()
            client = TestClient(app)
            resp = client.get(f"/api/v1/groups/{group_id}/settings")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body.get("ok"))
            settings = (body.get("result") or {}).get("settings", {})
            self.assertFalse(settings.get("desktop_pet_enabled"))
            self.assertTrue(settings.get("panorama_enabled"))
        finally:
            cleanup()

    def test_schema_accepts_desktop_pet_enabled_field(self) -> None:
        """Verify GroupSettingsRequest schema includes desktop_pet_enabled."""
        from cccc.ports.web.schemas import GroupSettingsRequest

        req = GroupSettingsRequest(desktop_pet_enabled=True)
        self.assertTrue(req.desktop_pet_enabled)

        req2 = GroupSettingsRequest(desktop_pet_enabled=False)
        self.assertFalse(req2.desktop_pet_enabled)

        req3 = GroupSettingsRequest()
        self.assertIsNone(req3.desktop_pet_enabled)

    def test_launch_token_endpoint_returns_current_scoped_token(self) -> None:
        from cccc.kernel.access_tokens import create_access_token
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            token = str(create_access_token("member-user", is_admin=False, allowed_groups=[group_id]).get("token") or "")
            app = create_app()
            client = TestClient(app)

            resp = client.get(
                f"/api/v1/groups/{group_id}/desktop_pet/launch_token",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body.get("ok"))
            self.assertEqual(str((body.get("result") or {}).get("token") or ""), token)
        finally:
            cleanup()

    def test_launch_token_endpoint_allows_empty_token_when_no_access_tokens_exist(self) -> None:
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            app = create_app()
            client = TestClient(app)

            resp = client.get(f"/api/v1/groups/{group_id}/desktop_pet/launch_token")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body.get("ok"))
            self.assertEqual((body.get("result") or {}).get("token"), "")
        finally:
            cleanup()

    def test_launch_token_endpoint_respects_group_scope(self) -> None:
        from cccc.kernel.access_tokens import create_access_token
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            other_group_id = self._create_group()
            token = str(create_access_token("member-user", is_admin=False, allowed_groups=[group_id]).get("token") or "")
            app = create_app()
            client = TestClient(app)

            resp = client.get(
                f"/api/v1/groups/{other_group_id}/desktop_pet/launch_token",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(resp.status_code, 403)
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
