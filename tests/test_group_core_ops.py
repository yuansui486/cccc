import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import errno
import shutil


class TestGroupCoreOps(unittest.TestCase):
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

    def test_group_update_and_detach_scope_behaviors(self) -> None:
        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "g1", "topic": "old", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            update_resp, _ = self._call(
                "group_update",
                {"group_id": group_id, "by": "user", "patch": {"title": "new-title", "topic": "new-topic"}},
            )
            self.assertTrue(update_resp.ok, getattr(update_resp, "error", None))
            group_doc = (update_resp.result or {}).get("group") if isinstance(update_resp.result, dict) else {}
            self.assertIsInstance(group_doc, dict)
            assert isinstance(group_doc, dict)
            self.assertEqual(str(group_doc.get("title") or ""), "new-title")
            self.assertEqual(str(group_doc.get("topic") or ""), "new-topic")

            bad_update_resp, _ = self._call(
                "group_update",
                {"group_id": group_id, "by": "user", "patch": {"unknown_key": 1}},
            )
            self.assertFalse(bad_update_resp.ok)
            self.assertEqual((bad_update_resp.error.code if bad_update_resp.error else ""), "invalid_patch")

            with tempfile.TemporaryDirectory(prefix="cccc_scope_") as scope_dir_raw:
                scope_dir = Path(scope_dir_raw)
                attach_resp, _ = self._call(
                    "attach",
                    {"group_id": group_id, "path": str(scope_dir), "by": "user"},
                )
                self.assertTrue(attach_resp.ok, getattr(attach_resp, "error", None))
                scope_key = str((attach_resp.result or {}).get("scope_key") or "").strip()
                self.assertTrue(scope_key)

                use_resp, _ = self._call(
                    "group_use",
                    {"group_id": group_id, "path": str(scope_dir), "by": "user"},
                )
                self.assertTrue(use_resp.ok, getattr(use_resp, "error", None))
                self.assertEqual(str((use_resp.result or {}).get("active_scope_key") or ""), scope_key)

                detach_resp, _ = self._call(
                    "group_detach_scope",
                    {"group_id": group_id, "scope_key": scope_key, "by": "user"},
                )
                self.assertTrue(detach_resp.ok, getattr(detach_resp, "error", None))
                self.assertEqual(str((detach_resp.result or {}).get("group_id") or ""), group_id)

                show_resp, _ = self._call("group_show", {"group_id": group_id})
                self.assertTrue(show_resp.ok, getattr(show_resp, "error", None))
                show_group = (show_resp.result or {}).get("group") if isinstance(show_resp.result, dict) else {}
                self.assertIsInstance(show_group, dict)
                assert isinstance(show_group, dict)
                scopes = show_group.get("scopes") if isinstance(show_group.get("scopes"), list) else []
                self.assertEqual(len(scopes), 0)
        finally:
            cleanup()

    def test_group_preamble_get_set_and_reset(self) -> None:
        from no1.kernel.prompt_files import DEFAULT_PREAMBLE_BODY

        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call(
                "group_create", {"title": "preamble", "topic": "", "by": "user"}
            )
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            initial, _ = self._call("group_preamble_get", {"group_id": group_id})
            self.assertTrue(initial.ok, getattr(initial, "error", None))
            self.assertEqual((initial.result or {}).get("source"), "builtin")
            self.assertFalse(bool((initial.result or {}).get("overridden")))
            self.assertEqual(
                (initial.result or {}).get("content"),
                str(DEFAULT_PREAMBLE_BODY or "").strip(),
            )

            custom = "Showrunner startup boundary.\nWait for the targeted mission.\n"
            updated, _ = self._call(
                "group_preamble_set",
                {"group_id": group_id, "content": custom, "by": "user"},
            )
            self.assertTrue(updated.ok, getattr(updated, "error", None))
            self.assertEqual((updated.result or {}).get("source"), "home")
            self.assertTrue(bool((updated.result or {}).get("overridden")))
            self.assertTrue(bool((updated.result or {}).get("changed")))
            self.assertEqual((updated.result or {}).get("content"), custom)

            with patch("no1.daemon.group.group_ops.write_group_prompt_file") as write_prompt:
                unchanged, _ = self._call(
                    "group_preamble_set",
                    {"group_id": group_id, "content": custom, "by": "user"},
                )
            self.assertTrue(unchanged.ok, getattr(unchanged, "error", None))
            self.assertFalse(bool((unchanged.result or {}).get("changed")))
            write_prompt.assert_not_called()

            oversized, _ = self._call(
                "group_preamble_set",
                {"group_id": group_id, "content": "x" * (512 * 1024 + 1), "by": "user"},
            )
            self.assertFalse(oversized.ok)
            self.assertEqual(
                getattr(oversized.error, "code", ""), "group_preamble_set_failed"
            )
            self.assertIn("524288 UTF-8 bytes", getattr(oversized.error, "message", ""))

            after_rejected, _ = self._call("group_preamble_get", {"group_id": group_id})
            self.assertTrue(after_rejected.ok, getattr(after_rejected, "error", None))
            self.assertEqual((after_rejected.result or {}).get("content"), custom)

            oversized_utf8, _ = self._call(
                "group_preamble_set",
                {"group_id": group_id, "content": "\u754c" * (512 * 1024 // 3 + 1), "by": "user"},
            )
            self.assertFalse(oversized_utf8.ok)
            self.assertEqual(
                getattr(oversized_utf8.error, "code", ""), "group_preamble_set_failed"
            )

            denied, _ = self._call(
                "group_preamble_set",
                {"group_id": group_id, "content": "denied", "by": "unknown-peer"},
            )
            self.assertFalse(denied.ok)
            self.assertEqual(getattr(denied.error, "code", ""), "group_preamble_set_failed")

            invalid, _ = self._call(
                "group_preamble_set",
                {"group_id": group_id, "content": "  ", "by": "user"},
            )
            self.assertFalse(invalid.ok)
            self.assertEqual(getattr(invalid.error, "code", ""), "invalid_content")

            unconfirmed, _ = self._call(
                "group_preamble_reset",
                {"group_id": group_id, "confirm": "wrong", "by": "user"},
            )
            self.assertFalse(unconfirmed.ok)
            self.assertEqual(getattr(unconfirmed.error, "code", ""), "confirm_required")

            reset, _ = self._call(
                "group_preamble_reset",
                {"group_id": group_id, "confirm": "preamble", "by": "user"},
            )
            self.assertTrue(reset.ok, getattr(reset, "error", None))
            self.assertEqual((reset.result or {}).get("source"), "builtin")
            self.assertFalse(bool((reset.result or {}).get("overridden")))
            self.assertTrue(bool((reset.result or {}).get("changed")))
            self.assertEqual(
                (reset.result or {}).get("content"),
                str(DEFAULT_PREAMBLE_BODY or "").strip(),
            )

            reset_again, _ = self._call(
                "group_preamble_reset",
                {"group_id": group_id, "confirm": "preamble", "by": "user"},
            )
            self.assertTrue(reset_again.ok, getattr(reset_again, "error", None))
            self.assertFalse(bool((reset_again.result or {}).get("changed")))

            denied_reset, _ = self._call(
                "group_preamble_reset",
                {"group_id": group_id, "confirm": "preamble", "by": "unknown-peer"},
            )
            self.assertFalse(denied_reset.ok)
            self.assertEqual(
                getattr(denied_reset.error, "code", ""), "group_preamble_reset_failed"
            )
        finally:
            cleanup()

    def test_group_use_rejects_exact_cccc_home_as_workspace_scope(self) -> None:
        home, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "g1", "topic": "", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            use_resp, _ = self._call("group_use", {"group_id": group_id, "path": home, "by": "user"})
            self.assertFalse(use_resp.ok)
            self.assertEqual(getattr(use_resp.error, "code", ""), "invalid_scope_path")
        finally:
            cleanup()

    def test_group_delete_clears_active_and_removes_group(self) -> None:
        from no1.kernel.active import load_active, set_active_group_id
        from no1.kernel.group import load_group

        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "delete-me", "topic": "", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            set_active_group_id(group_id)
            self.assertEqual(str(load_active().get("active_group_id") or ""), group_id)

            delete_resp, _ = self._call("group_delete", {"group_id": group_id, "by": "user"})
            self.assertTrue(delete_resp.ok, getattr(delete_resp, "error", None))
            self.assertEqual(str((delete_resp.result or {}).get("group_id") or ""), group_id)

            self.assertIsNone(load_group(group_id))
            self.assertEqual(str(load_active().get("active_group_id") or ""), "")

            show_resp, _ = self._call("group_show", {"group_id": group_id})
            self.assertFalse(show_resp.ok)
            self.assertEqual((show_resp.error.code if show_resp.error else ""), "group_not_found")
        finally:
            cleanup()

    def test_group_delete_tolerates_transient_directory_not_empty(self) -> None:
        from no1.kernel.group import load_group

        _, cleanup = self._with_home()
        try:
            create_resp, _ = self._call("group_create", {"title": "delete-race", "topic": "", "by": "user"})
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            group_id = str((create_resp.result or {}).get("group_id") or "").strip()
            self.assertTrue(group_id)

            real_rmtree = shutil.rmtree
            injected = {"raised": False}

            def _flaky_rmtree(path, *args, **kwargs):
                name = Path(path).name
                if name == group_id and not injected["raised"]:
                    injected["raised"] = True
                    raise OSError(errno.ENOTEMPTY, "Directory not empty")
                return real_rmtree(path, *args, **kwargs)

            with patch("no1.kernel.group.shutil.rmtree", side_effect=_flaky_rmtree):
                delete_resp, _ = self._call("group_delete", {"group_id": group_id, "by": "user"})

            self.assertTrue(injected["raised"])
            self.assertTrue(delete_resp.ok, getattr(delete_resp, "error", None))
            self.assertIsNone(load_group(group_id))
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
