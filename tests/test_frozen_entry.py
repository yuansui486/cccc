from __future__ import annotations

import unittest
from unittest.mock import patch


class TestFrozenEntry(unittest.TestCase):
    def test_normal_invocation_delegates_to_cli(self) -> None:
        from no1 import frozen_entry

        with patch.object(frozen_entry, "_run_cli", return_value=0) as cli_main:
            rc = frozen_entry.main(["version"])

        self.assertEqual(rc, 0)
        cli_main.assert_called_once_with(["version"])

    def test_internal_module_dispatch_calls_main_with_remainder(self) -> None:
        from no1 import frozen_entry

        class FakeModule:
            @staticmethod
            def main(argv):
                return 7 if argv == ["run"] else 8

        with patch.object(frozen_entry.importlib, "import_module", return_value=FakeModule):
            rc = frozen_entry.main(["--internal-module", "no1.daemon_main", "--", "run"])

        self.assertEqual(rc, 7)

    def test_internal_im_package_dispatch_imports_main_module(self) -> None:
        from no1 import frozen_entry

        class FakeModule:
            @staticmethod
            def main():
                return 0

        with patch.object(frozen_entry.importlib, "import_module", return_value=FakeModule) as import_module:
            rc = frozen_entry.main(["--internal-module", "no1.ports.im", "--", "g_test", "wecom"])

        self.assertEqual(rc, 0)
        import_module.assert_called_once_with("no1.ports.im.__main__")

    def test_internal_dispatch_rejects_unknown_modules(self) -> None:
        from no1 import frozen_entry

        rc = frozen_entry.main(["--internal-module", "os", "--"])

        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
