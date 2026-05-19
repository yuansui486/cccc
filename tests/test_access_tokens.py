import os
import tempfile
import unittest
from pathlib import Path


class TestAccessTokens(unittest.TestCase):
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

        return Path(td), cleanup

    def test_create_lookup_list_delete_access_token(self) -> None:
        from no1.kernel.access_tokens import (
            create_access_token,
            delete_access_token,
            list_access_tokens,
            lookup_access_token,
        )

        _, cleanup = self._with_home()
        try:
            created = create_access_token("user-a", allowed_groups=["g1", "g1", "g2"], is_admin=False)
            token = str(created.get("token") or "")

            self.assertTrue(token.startswith("acc_"))
            self.assertEqual(str(created.get("user_id") or ""), "user-a")
            self.assertEqual(created.get("allowed_groups"), ["g1", "g2"])

            looked_up = lookup_access_token(token)
            self.assertIsNotNone(looked_up)
            assert looked_up is not None
            self.assertEqual(str(looked_up.get("user_id") or ""), "user-a")
            self.assertEqual(looked_up.get("allowed_groups"), ["g1", "g2"])

            listed = list_access_tokens()
            self.assertEqual(len(listed), 1)
            self.assertEqual(str(listed[0].get("token") or ""), token)

            self.assertTrue(delete_access_token(token))
            self.assertIsNone(lookup_access_token(token))
            self.assertEqual(list_access_tokens(), [])
        finally:
            cleanup()

    def test_load_access_tokens_tolerates_invalid_yaml(self) -> None:
        from no1.kernel.access_tokens import load_access_tokens

        home, cleanup = self._with_home()
        try:
            (home / "access_tokens.yaml").write_text("tokens: [", encoding="utf-8")
            self.assertEqual(load_access_tokens(), {})
        finally:
            cleanup()

    def test_create_access_token_requires_user_id(self) -> None:
        from no1.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            with self.assertRaises(ValueError):
                create_access_token("")
        finally:
            cleanup()
