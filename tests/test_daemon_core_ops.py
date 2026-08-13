import os
import tempfile
import unittest


class TestDaemonCoreOps(unittest.TestCase):
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

    def _call(self, op: str, args: dict):
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def test_ping_and_shutdown(self) -> None:
        _, cleanup = self._with_home()
        try:
            ping, should_stop = self._call("ping", {})
            self.assertTrue(ping.ok, getattr(ping, "error", None))
            self.assertFalse(should_stop)
            result = ping.result if isinstance(ping.result, dict) else {}
            self.assertIsInstance(result, dict)
            assert isinstance(result, dict)
            capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
            self.assertEqual(bool(capabilities.get("events_stream")), True)
            self.assertEqual(bool(capabilities.get("remote_access")), True)

            shutdown, should_stop = self._call("shutdown", {})
            self.assertTrue(shutdown.ok, getattr(shutdown, "error", None))
            self.assertTrue(should_stop)
        finally:
            cleanup()

    def test_observability_update_permissions_and_roundtrip(self) -> None:
        _, cleanup = self._with_home()
        try:
            denied, _ = self._call("observability_update", {"by": "peer1", "patch": {"developer_mode": True}})
            self.assertFalse(denied.ok)
            self.assertEqual(str(getattr(denied, "error", None).code), "permission_denied")

            update, _ = self._call(
                "observability_update",
                {
                    "by": "user",
                    "patch": {
                        "developer_mode": True,
                        "logger_levels": {
                            "asyncio": "warning",
                            "no1.daemon.group_space_ops": "debug",
                            "": "info",
                            "httpx": "bogus",
                        },
                        "runtime_visibility": {
                            "peer_runtime": "hidden",
                            "assistant_runtime": "hidden",
                        },
                        "terminal_ui": {
                            "color_scheme": "light",
                        },
                    },
                },
            )
            self.assertTrue(update.ok, getattr(update, "error", None))

            get, _ = self._call("observability_get", {})
            self.assertTrue(get.ok, getattr(get, "error", None))
            obs = (get.result or {}).get("observability") if isinstance(get.result, dict) else {}
            self.assertIsInstance(obs, dict)
            assert isinstance(obs, dict)
            self.assertEqual(bool(obs.get("developer_mode")), True)
            self.assertEqual(
                obs.get("logger_levels"),
                {
                    "asyncio": "WARNING",
                    "no1.daemon.group_space_ops": "DEBUG",
                },
            )
            runtime_visibility = obs.get("runtime_visibility") if isinstance(obs.get("runtime_visibility"), dict) else {}
            self.assertEqual(str(runtime_visibility.get("peer_runtime") or ""), "hidden")
            self.assertEqual(str(runtime_visibility.get("assistant_runtime") or ""), "hidden")
            terminal_ui = obs.get("terminal_ui") if isinstance(obs.get("terminal_ui"), dict) else {}
            self.assertEqual(str(terminal_ui.get("color_scheme") or ""), "light")
        finally:
            cleanup()

    def test_observability_terminal_color_scheme_defaults_and_rejects_invalid_values(self) -> None:
        from no1.kernel.settings import (
            get_observability_settings,
            save_settings,
            update_observability_settings,
        )

        _, cleanup = self._with_home()
        try:
            save_settings({"observability": {"terminal_ui": {"scrollback_lines": 9000}}})
            terminal_ui = get_observability_settings().get("terminal_ui") or {}
            self.assertEqual(terminal_ui.get("color_scheme"), "dark")

            updated = update_observability_settings({"terminal_ui": {"color_scheme": "light"}})
            self.assertEqual((updated.get("terminal_ui") or {}).get("color_scheme"), "light")

            invalid = update_observability_settings({"terminal_ui": {"color_scheme": "sepia"}})
            self.assertEqual((invalid.get("terminal_ui") or {}).get("color_scheme"), "light")
        finally:
            cleanup()

    def test_observability_legacy_pet_runtime_is_read_once_but_never_exposed_or_written(self) -> None:
        from no1.kernel.settings import (
            get_observability_settings,
            load_settings,
            save_settings,
            update_observability_settings,
        )

        _, cleanup = self._with_home()
        try:
            save_settings(
                {
                    "observability": {
                        "runtime_visibility": {
                            "peer_runtime": "visible",
                            "pet_runtime": "visible",
                        }
                    }
                }
            )

            migrated = get_observability_settings()
            runtime_visibility = migrated.get("runtime_visibility") or {}
            self.assertEqual(runtime_visibility.get("assistant_runtime"), "visible")
            self.assertNotIn("pet_runtime", runtime_visibility)

            updated = update_observability_settings(
                {"runtime_visibility": {"pet_runtime": "hidden"}}
            )
            updated_visibility = updated.get("runtime_visibility") or {}
            self.assertEqual(updated_visibility.get("assistant_runtime"), "visible")
            self.assertNotIn("pet_runtime", updated_visibility)

            stored = load_settings().get("observability") or {}
            stored_visibility = stored.get("runtime_visibility") or {}
            self.assertEqual(stored_visibility.get("assistant_runtime"), "visible")
            self.assertNotIn("pet_runtime", stored_visibility)
        finally:
            cleanup()

    def test_observability_cache_is_scoped_to_current_home(self) -> None:
        _, cleanup_first = self._with_home()
        try:
            update, _ = self._call("observability_update", {"by": "user", "patch": {"developer_mode": True}})
            self.assertTrue(update.ok, getattr(update, "error", None))
        finally:
            cleanup_first()

        _, cleanup_second = self._with_home()
        try:
            get, _ = self._call("observability_get", {})
            self.assertTrue(get.ok, getattr(get, "error", None))
            obs = (get.result or {}).get("observability") if isinstance(get.result, dict) else {}
            self.assertIsInstance(obs, dict)
            assert isinstance(obs, dict)
            self.assertEqual(bool(obs.get("developer_mode")), False)
        finally:
            cleanup_second()

    def test_branding_update_permissions_and_roundtrip(self) -> None:
        _, cleanup = self._with_home()
        try:
            denied, _ = self._call("branding_update", {"by": "peer1", "patch": {"product_name": "Acme"}})
            self.assertFalse(denied.ok)
            self.assertEqual(str(getattr(denied, "error", None).code), "permission_denied")

            update, _ = self._call("branding_update", {"by": "user", "patch": {"product_name": "Acme Console"}})
            self.assertTrue(update.ok, getattr(update, "error", None))

            get, _ = self._call("branding_get", {})
            self.assertTrue(get.ok, getattr(get, "error", None))
            branding = (get.result or {}).get("branding") if isinstance(get.result, dict) else {}
            self.assertIsInstance(branding, dict)
            assert isinstance(branding, dict)
            self.assertEqual(str(branding.get("product_name") or ""), "Acme Console")
        finally:
            cleanup()

    def test_try_handle_unknown_daemon_core_op_returns_none(self) -> None:
        from no1.daemon.ops.daemon_core_ops import try_handle_daemon_core_op

        self.assertIsNone(
            try_handle_daemon_core_op(
                "not_core",
                {},
                version="x",
                pid_provider=lambda: 1,
                now_iso=lambda: "now",
                get_observability=lambda: {},
                update_observability_settings=lambda patch: patch,
                apply_observability_settings=lambda _obs: None,
                get_web_branding=lambda: {},
                update_web_branding_settings=lambda patch: patch,
            )
        )


if __name__ == "__main__":
    unittest.main()
