import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO


class TestCliImWecom(unittest.TestCase):
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

    def test_im_set_wecom_cli_writes_bot_id_and_secret(self) -> None:
        from cccc.cli.main import main
        from cccc.kernel.group import create_group, load_group
        from cccc.kernel.registry import load_registry

        _, cleanup = self._with_home()
        try:
            group = create_group(load_registry(), title="cli-wecom", topic="")

            out = StringIO()
            with redirect_stdout(out):
                rc = main([
                    "im",
                    "set",
                    "wecom",
                    "--group",
                    group.group_id,
                    "--wecom-bot-id",
                    "corp123",
                    "--wecom-secret",
                    "sec456",
                ])

            self.assertEqual(rc, 0)
            self.assertIn('"ok": true', out.getvalue())

            saved = load_group(group.group_id)
            self.assertIsNotNone(saved)
            im = saved.doc.get("im") if saved is not None else {}
            self.assertIsInstance(im, dict)
            self.assertEqual(im.get("platform"), "wecom")
            self.assertEqual(im.get("wecom_bot_id"), "corp123")
            self.assertEqual(im.get("wecom_secret"), "sec456")
            self.assertNotIn("wecom_agent_id", im)
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
