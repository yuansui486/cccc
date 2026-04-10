import os
import tempfile
import unittest
from unittest.mock import patch


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

    def test_enabling_desktop_pet_inherits_foreman_profile_private_env(self) -> None:
        from pathlib import Path

        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request
        from cccc.kernel.group import load_group
        from cccc.kernel.pet_actor import PET_ACTOR_ID, get_pet_actor

        group_id = self._create_group()

        attach_resp, _ = handle_request(
            DaemonRequest.model_validate(
                {"op": "attach", "args": {"group_id": group_id, "path": ".", "by": "user"}}
            )
        )
        self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))

        profile_upsert, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "actor_profile_upsert",
                    "args": {
                        "by": "user",
                        "caller_id": "user-a",
                        "is_admin": False,
                        "profile": {
                            "id": "pet-profile",
                            "name": "Pet Profile",
                            "scope": "user",
                            "owner_id": "user-a",
                            "runtime": "custom",
                            "runner": "headless",
                            "command": [],
                            "submit": "newline",
                        },
                    },
                }
            )
        )
        self.assertTrue(profile_upsert.ok, getattr(profile_upsert, "error", None))

        secret_update, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "actor_profile_secret_update",
                    "args": {
                        "by": "user",
                        "profile_id": "pet-profile",
                        "profile_scope": "user",
                        "profile_owner": "user-a",
                        "caller_id": "user-a",
                        "is_admin": False,
                        "set": {"API_KEY": "pet-secret"},
                    },
                }
            )
        )
        self.assertTrue(secret_update.ok, getattr(secret_update, "error", None))

        add_foreman, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "actor_add",
                    "args": {
                        "group_id": group_id,
                        "actor_id": "lead",
                        "runtime": "codex",
                        "runner": "headless",
                        "profile_id": "pet-profile",
                        "profile_scope": "user",
                        "profile_owner": "user-a",
                        "caller_id": "user-a",
                        "is_admin": False,
                        "by": "user",
                    },
                }
            )
        )
        self.assertTrue(add_foreman.ok, getattr(add_foreman, "error", None))

        group = load_group(group_id)
        assert group is not None
        group.doc["running"] = True
        group.save()

        captured: list[dict[str, object]] = []

        def _fake_headless_start_actor(*, group_id: str, actor_id: str, cwd: Path, env: dict[str, str]):
            captured.append(
                {
                    "group_id": group_id,
                    "actor_id": actor_id,
                    "cwd": cwd,
                    "env": dict(env),
                }
            )

            class _Session:
                pass

            return _Session()

        with patch("cccc.daemon.actors.actor_runtime_ops.headless_runner.SUPERVISOR.start_actor", side_effect=_fake_headless_start_actor):
            enable_resp, _ = handle_request(
                DaemonRequest.model_validate(
                    {
                        "op": "group_settings_update",
                        "args": {"group_id": group_id, "by": "user", "patch": {"desktop_pet_enabled": True}},
                    }
                )
            )

        self.assertTrue(enable_resp.ok, getattr(enable_resp, "error", None))
        pet_launches = [item for item in captured if item.get("actor_id") == PET_ACTOR_ID]
        self.assertEqual(len(pet_launches), 1)
        pet_env = pet_launches[0].get("env")
        self.assertIsInstance(pet_env, dict)
        assert isinstance(pet_env, dict)
        self.assertEqual(pet_env.get("API_KEY"), "pet-secret")

        private_keys_resp, _ = handle_request(
            DaemonRequest.model_validate(
                {"op": "actor_env_private_keys", "args": {"group_id": group_id, "actor_id": PET_ACTOR_ID, "by": "user"}}
            )
        )
        self.assertTrue(private_keys_resp.ok, getattr(private_keys_resp, "error", None))
        self.assertEqual(set(private_keys_resp.result.get("keys") or []), {"API_KEY"})

        group = load_group(group_id)
        assert group is not None
        pet_actor = get_pet_actor(group)
        self.assertIsNotNone(pet_actor)
        assert pet_actor is not None
        self.assertEqual(str(pet_actor.get("runtime") or ""), "custom")
        self.assertEqual(str(pet_actor.get("submit") or ""), "newline")
        self.assertEqual(dict(pet_actor.get("env") or {}), {})

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
