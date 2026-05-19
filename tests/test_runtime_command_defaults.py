import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestRuntimeCommandDefaults(unittest.TestCase):
    def test_kimi_runtime_uses_yolo_flags_for_launch(self) -> None:
        from no1.kernel.runtime import get_runtime_command_with_flags

        self.assertEqual(get_runtime_command_with_flags("kimi"), ["kimi", "--yolo"])
        self.assertEqual(get_runtime_command_with_flags("hermes"), ["hermes", "--tui", "--yolo"])

    def test_onecolleague_mcp_stdio_command_prefers_unresolved_venv_entrypoint(self) -> None:
        from no1.kernel.runtime import get_onecolleague_mcp_stdio_command

        with tempfile.TemporaryDirectory() as td:
            venv = Path(td) / ".venv"
            bin_dir = venv / "bin"
            bin_dir.mkdir(parents=True)
            python = bin_dir / "python"
            onecolleague = bin_dir / "onecolleague"
            python.write_text("", encoding="utf-8")
            onecolleague.write_text("", encoding="utf-8")
            with patch("no1.kernel.runtime.sys.platform", "linux"), patch(
                "no1.kernel.runtime.sys.executable",
                str(python),
            ), patch("no1.kernel.runtime.sys.prefix", str(venv)), patch(
                "no1.kernel.runtime.shutil.which",
                return_value=None,
            ):
                self.assertEqual(get_onecolleague_mcp_stdio_command(), [str(onecolleague.resolve()), "mcp"])


if __name__ == "__main__":
    unittest.main()
