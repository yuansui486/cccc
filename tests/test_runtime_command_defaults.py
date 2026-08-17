import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch


class TestRuntimeCommandDefaults(unittest.TestCase):
    def test_kimi_runtime_uses_yolo_flags_for_launch(self) -> None:
        from no1.kernel.runtime import get_runtime_command_with_flags

        self.assertEqual(get_runtime_command_with_flags("kimi"), ["kimi", "--yolo"])
        self.assertEqual(get_runtime_command_with_flags("hermes"), ["hermes", "--tui", "--yolo"])
        self.assertEqual(get_runtime_command_with_flags("opencode"), ["opencode", "--auto"])
        self.assertEqual(get_runtime_command_with_flags("openclaw"), ["openclaw", "tui"])

    def test_openclaw_detection_resolves_the_executable_without_tui_arguments(self) -> None:
        from no1.kernel.runtime import detect_runtime

        with patch("no1.kernel.runtime.find_subprocess_executable", return_value=r"C:\Tools\openclaw.cmd") as find:
            runtime = detect_runtime("openclaw")

        find.assert_called_once_with("openclaw")
        self.assertTrue(runtime.available)
        self.assertEqual(runtime.path, r"C:\Tools\openclaw.cmd")

    def test_direct_opencode_commands_force_auto_mode(self) -> None:
        from no1.kernel.runtime import ensure_opencode_auto_command

        cases = (
            ([], ["opencode", "--auto"]),
            (["opencode"], ["opencode", "--auto"]),
            (["opencode", "-m", "onecolleague/gpt-5.4"], ["opencode", "--auto", "-m", "onecolleague/gpt-5.4"]),
            ([r"C:\Tools\opencode.cmd", "--no-auto", "--model=onecolleague/gpt-5.5"], [r"C:\Tools\opencode.cmd", "--auto", "--model=onecolleague/gpt-5.5"]),
            (["/usr/local/bin/opencode", "--auto=false", "run", "task"], ["/usr/local/bin/opencode", "--auto", "run", "task"]),
            (["opencode.exe", "--auto", "--auto=true", "--mini"], ["opencode.exe", "--auto", "--mini"]),
        )
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(ensure_opencode_auto_command(command), expected)

    def test_opencode_wrapper_commands_are_not_rewritten(self) -> None:
        from no1.kernel.runtime import ensure_opencode_auto_command

        self.assertEqual(
            ensure_opencode_auto_command(["npx", "opencode", "--model", "onecolleague/gpt-5.4"]),
            ["npx", "opencode", "--model", "onecolleague/gpt-5.4"],
        )

    def test_daemon_launch_normalizer_applies_opencode_auto_mode(self) -> None:
        from no1.daemon import server as daemon_server

        self.assertEqual(
            daemon_server._normalize_runtime_command(
                "opencode",
                ["opencode", "-m", "onecolleague/gpt-5.4"],
            ),
            ["opencode", "--auto", "-m", "onecolleague/gpt-5.4"],
        )

    def test_daemon_launch_normalizer_applies_hermes_provider_and_model(self) -> None:
        from no1.daemon import server as daemon_server

        command = daemon_server._normalize_runtime_command(
            "hermes",
            ["hermes", "--tui", "--yolo"],
            env={"HERMES_SELECTED_MODEL": "deepseek-v4-pro"},
        )
        self.assertIn(Path(command[0]).name.lower(), {"hermes", "hermes.exe", "hermes.cmd", "hermes.bat"})
        self.assertEqual(
            command[1:],
            [
                "--tui",
                "--yolo",
                "--provider",
                "custom:onecolleague",
                "--model",
                "deepseek-v4-pro",
            ],
        )

    def test_windows_hermes_install_dir_is_discovered_without_path_refresh(self) -> None:
        from no1.util import process

        with patch.object(process.os, "name", "nt"), patch.object(process.Path, "home", return_value=Path(r"C:\Users\tester")), patch.dict(
            process.os.environ,
            {"LOCALAPPDATA": r"C:\Users\tester\AppData\Local", "APPDATA": r"C:\Users\tester\AppData\Roaming"},
            clear=False,
        ), patch.object(process.Path, "exists", return_value=False):
            candidates = process._iter_windows_user_bin_dirs()

        self.assertIn(Path(r"C:\Users\tester\AppData\Local\hermes\hermes-agent\venv\Scripts"), candidates)

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

    def test_onecolleague_mcp_stdio_command_uses_current_exe_when_frozen(self) -> None:
        from no1.kernel.runtime import get_onecolleague_mcp_stdio_command

        with patch("no1.kernel.runtime.is_frozen_executable", return_value=True), patch(
            "no1.kernel.runtime.current_frozen_executable",
            return_value=r"C:\OneColleague\onecolleague.exe",
        ), patch(
            "no1.kernel.runtime.sys.executable",
            r"C:\OneColleague\onecolleague.exe",
        ):
            self.assertEqual(get_onecolleague_mcp_stdio_command(), [r"C:\OneColleague\onecolleague.exe", "mcp"])


if __name__ == "__main__":
    unittest.main()
