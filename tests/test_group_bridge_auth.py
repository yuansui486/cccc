import tempfile
import unittest
from pathlib import Path


class TestGroupBridgeAuth(unittest.TestCase):
    def test_admin_all_vs_non_admin_empty_none(self) -> None:
        from no1.kernel.group_bridge.auth import can_access_group

        self.assertTrue(can_access_group({"is_admin": True, "allowed_groups": []}, "any-group"))
        self.assertFalse(can_access_group({"is_admin": False, "allowed_groups": []}, "g1"))
        self.assertFalse(can_access_group({}, "g1"))

    def test_explicit_allow_list_is_normalized(self) -> None:
        from no1.kernel.group_bridge.auth import allowed_group_ids, can_access_group

        entry = {"is_admin": False, "allowed_groups": [" g1 ", "g1", "", "g2"]}
        self.assertEqual(allowed_group_ids(entry), ["g1", "g2"])
        self.assertTrue(can_access_group(entry, "g1"))
        self.assertFalse(can_access_group(entry, "g3"))

    def test_authorize_token_group_reuses_access_token_scope(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.auth import authorize_token_group

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("user", allowed_groups=["g1"], home=home)["token"]
            self.assertTrue(authorize_token_group(token, "g1", home)["allowed"])
            self.assertFalse(authorize_token_group(token, "g2", home)["allowed"])
            unknown = authorize_token_group("acc_not_known", "g1", home)
            self.assertEqual(unknown, {"allowed": False, "is_admin": False, "reason": "unknown_token"})

    def test_admin_token_authorizes_every_group(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.auth import authorize_token_group

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("admin", is_admin=True, home=home)["token"]
            decision = authorize_token_group(token, "unlisted", home)
            self.assertTrue(decision["allowed"])
            self.assertTrue(decision["is_admin"])


if __name__ == "__main__":
    unittest.main()
