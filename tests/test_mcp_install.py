import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from no1.daemon.mcp_install import ensure_mcp_installed, is_mcp_installed
from no1.kernel.runtime import get_onecolleague_mcp_stdio_command


class TestMcpInstall(unittest.TestCase):
    def test_is_mcp_installed_unknown_runtime_false(self) -> None:
        self.assertFalse(is_mcp_installed("unknown-runtime"))

    def test_ensure_mcp_installed_skips_non_auto_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                ok = ensure_mcp_installed("unknown-runtime", cwd, auto_mcp_runtimes=("claude", "codex"))
                self.assertTrue(ok)
                mock_run.assert_not_called()

    def test_build_mcp_add_command_hermes_uses_safe_prepare_wrapper(self) -> None:
        from no1.daemon.mcp_install import build_mcp_add_command

        with patch("no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command", return_value=["/abs/onecolleague", "mcp"]):
            self.assertEqual(
                build_mcp_add_command("hermes"),
                ["onecolleague", "runtime", "hermes", "prepare", "--yes"],
            )

    def test_is_mcp_installed_kimi_reads_config_and_validates_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            config_path = home / ".kimi" / "mcp.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "onecolleague": {
                                "command": r"C:\OneColleague\onecolleague.exe",
                                "args": ["mcp"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch("no1.daemon.mcp_install.sys.platform", "win32"), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=[r"C:\OneColleague\onecolleague.exe", "mcp"],
            ), patch("no1.daemon.mcp_install.Path.home", return_value=home):
                self.assertTrue(is_mcp_installed("kimi"))

    def test_is_mcp_installed_droid_windows_rejects_backslash_stripped_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            config_path = home / ".factory" / "mcp.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "onecolleague": {
                                "type": "stdio",
                                "command": "C:OneColleagueonecolleague.exe",
                                "args": ["mcp"],
                                "disabled": False,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch("no1.daemon.mcp_install.sys.platform", "win32"), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=[r"C:\OneColleague\onecolleague.exe", "mcp"],
            ), patch("no1.daemon.mcp_install.Path.home", return_value=home):
                self.assertFalse(is_mcp_installed("droid"))

    def test_ensure_mcp_installed_kimi_adds_onecolleague_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install._runtime_mcp_state", side_effect=["missing", "ready"]), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=["/abs/onecolleague", "mcp"],
            ), patch("no1.daemon.mcp_install.resolve_subprocess_argv", side_effect=lambda argv: list(argv)):
                with patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.return_value.returncode = 0
                    ok = ensure_mcp_installed("kimi", cwd, auto_mcp_runtimes=("kimi",))
                    self.assertTrue(ok)
                    mock_run.assert_called_once_with(
                        ["kimi", "mcp", "add", "--transport", "stdio", "onecolleague", "--", "/abs/onecolleague", "mcp"],
                        capture_output=True,
                        text=True,
                        cwd=str(cwd),
                        timeout=30,
                    )

    def test_ensure_mcp_installed_gemini_verifies_against_actor_home_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            actor_home = Path(td) / "actor-home"
            env = {"HOME": str(actor_home)}

            def fake_run(argv, **kwargs):
                run_home = Path((kwargs.get("env") or {}).get("HOME") or "")
                self.assertEqual(run_home, actor_home)
                config_path = run_home / ".gemini" / "settings.json"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(
                    json.dumps({"mcpServers": {"onecolleague": {"command": "/abs/onecolleague", "args": ["mcp"]}}}),
                    encoding="utf-8",
                )
                return Mock(returncode=0, stdout="", stderr="")

            with patch("no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command", return_value=["/abs/onecolleague", "mcp"]), patch(
                "no1.daemon.mcp_install.resolve_subprocess_argv", side_effect=lambda argv: list(argv)
            ), patch("no1.daemon.mcp_install.subprocess.run", side_effect=fake_run):
                ok = ensure_mcp_installed("gemini", cwd, auto_mcp_runtimes=("gemini",), env=env)
                self.assertTrue(ok)
                config_path = actor_home / ".gemini" / "settings.json"
                self.assertTrue(config_path.exists())
                self.assertTrue(is_mcp_installed("gemini", env=env))

    def test_ensure_mcp_installed_kimi_verifies_against_actor_home_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            actor_home = Path(td) / "actor-home"
            env = {"HOME": str(actor_home)}

            def fake_run(argv, **kwargs):
                run_home = Path((kwargs.get("env") or {}).get("HOME") or "")
                self.assertEqual(run_home, actor_home)
                config_path = run_home / ".kimi" / "mcp.json"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(
                    json.dumps({"mcpServers": {"onecolleague": {"command": "/abs/onecolleague", "args": ["mcp"]}}}),
                    encoding="utf-8",
                )
                return Mock(returncode=0, stdout="", stderr="")

            with patch("no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command", return_value=["/abs/onecolleague", "mcp"]), patch(
                "no1.daemon.mcp_install.resolve_subprocess_argv", side_effect=lambda argv: list(argv)
            ), patch("no1.daemon.mcp_install.subprocess.run", side_effect=fake_run):
                ok = ensure_mcp_installed("kimi", cwd, auto_mcp_runtimes=("kimi",), env=env)
                self.assertTrue(ok)
                config_path = actor_home / ".kimi" / "mcp.json"
                self.assertTrue(config_path.exists())
                self.assertTrue(is_mcp_installed("kimi", env=env))

    def test_ensure_mcp_installed_claude_windows_repairs_stale_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install.sys.platform", "win32"), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=[r"C:\OneColleague\onecolleague.exe", "mcp"],
            ), patch(
                "no1.daemon.mcp_install.resolve_subprocess_argv",
                side_effect=lambda argv: list(argv),
            ):
                with patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.side_effect = [
                        Mock(
                            returncode=0,
                            stdout=(
                                "onecolleague:\n"
                                "  Scope: User config\n"
                                "  Type: stdio\n"
                                "  Command: C:\\Old\\onecolleague.exe\n"
                                "  Args: mcp\n"
                            ).encode(),
                        ),
                        Mock(returncode=1, stdout="", stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                        Mock(
                            returncode=0,
                            stdout=(
                                "onecolleague:\n"
                                "  Scope: User config\n"
                                "  Type: stdio\n"
                                "  Command: C:\\OneColleague\\onecolleague.exe\n"
                                "  Args: mcp\n"
                            ).encode(),
                        ),
                    ]
                    ok = ensure_mcp_installed("claude", cwd, auto_mcp_runtimes=("claude",))
                    self.assertTrue(ok)
                    self.assertEqual(
                        mock_run.call_args_list,
                        [
                            call(
                                ["claude", "mcp", "get", "onecolleague"],
                                capture_output=True,
                                text=False,
                                timeout=10,
                            ),
                            call(
                                ["claude", "mcp", "get", "cccc"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            ),
                            call(
                                ["claude", "mcp", "remove", "onecolleague", "-s", "user"],
                                capture_output=True,
                                text=True,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["claude", "mcp", "add", "-s", "user", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                                capture_output=True,
                                text=True,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["claude", "mcp", "get", "onecolleague"],
                                capture_output=True,
                                text=False,
                                timeout=10,
                            ),
                        ],
                    )

    def test_ensure_mcp_installed_codex_passes_explicit_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            env = {"CODEX_HOME": "/tmp/onecolleague-isolated-codex-home", "OPENAI_API_KEY": "sk-test"}
            with patch("no1.daemon.mcp_install._runtime_mcp_state", side_effect=["missing", "ready"]), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=["/abs/onecolleague", "mcp"],
            ), patch("no1.daemon.mcp_install.resolve_subprocess_argv", side_effect=lambda argv: list(argv)):
                with patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.side_effect = [
                        Mock(returncode=1, stdout="", stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                    ]
                    ok = ensure_mcp_installed("codex", cwd, auto_mcp_runtimes=("codex",), env=env)
                    self.assertTrue(ok)
                    self.assertEqual(
                        mock_run.call_args_list,
                        [
                            call(
                                ["codex", "mcp", "get", "cccc"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                                env={**os.environ, **env},
                            ),
                            call(
                                ["codex", "mcp", "add", "onecolleague", "--", "/abs/onecolleague", "mcp"],
                                capture_output=True,
                                text=True,
                                cwd=str(cwd),
                                timeout=30,
                                env={**os.environ, **env},
                            ),
                        ],
                    )

    def test_ensure_mcp_installed_hermes_prepares_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td) / "repo"
            cwd.mkdir()
            cccc_home = Path(td) / "cccc-home"
            env = {"CCCC_HOME": str(cccc_home)}
            calls = []

            def fake_state(runtime, *, env=None):
                calls.append(("state", runtime, dict(env or {})))
                return "missing" if len(calls) == 1 else "ready"

            with patch("no1.daemon.mcp_install._runtime_mcp_state", side_effect=fake_state), patch(
                "no1.daemon.mcp_install.prepare_hermes_runtime",
                return_value={"ok": True},
            ) as prepare:
                ok = ensure_mcp_installed("hermes", cwd, auto_mcp_runtimes=("hermes",), env=env)

            self.assertTrue(ok)
            prepare.assert_called_once_with(
                home=cccc_home.resolve(),
                cwd=cwd,
                auto_enable_tools=True,
                force_mcp=False,
                hermes_home_override=None,
            )

    def test_ensure_mcp_installed_hermes_respects_explicit_hermes_home(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td) / "repo"
            cwd.mkdir()
            cccc_home = Path(td) / "cccc-home"
            hermes_home = Path(td) / "hermes-home"
            env = {"CCCC_HOME": str(cccc_home), "HERMES_HOME": str(hermes_home)}
            calls = []

            def fake_state(runtime, *, env=None):
                calls.append(("state", runtime, dict(env or {})))
                return "missing" if len(calls) == 1 else "ready"

            with patch("no1.daemon.mcp_install._runtime_mcp_state", side_effect=fake_state), patch(
                "no1.daemon.mcp_install.prepare_hermes_runtime",
                return_value={"ok": True},
            ) as prepare:
                ok = ensure_mcp_installed("hermes", cwd, auto_mcp_runtimes=("hermes",), env=env)

            self.assertTrue(ok)
            prepare.assert_called_once_with(
                home=cccc_home.resolve(),
                cwd=cwd,
                auto_enable_tools=True,
                force_mcp=False,
                hermes_home_override=hermes_home,
            )

    def test_ensure_mcp_installed_returns_false_when_initial_probe_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)

            with patch(
                "no1.daemon.mcp_install._runtime_mcp_state",
                side_effect=subprocess.TimeoutExpired(cmd=["codex"], timeout=10),
            ):
                self.assertFalse(ensure_mcp_installed("codex", cwd, auto_mcp_runtimes=("codex",)))

    def test_is_mcp_installed_codex_uses_resolved_windows_cli_path(self) -> None:
        with patch("no1.daemon.mcp_install.sys.platform", "linux"), patch("no1.daemon.mcp_install.resolve_subprocess_argv", return_value=[r"C:\Tools\codex.cmd", "mcp", "get", "onecolleague"]), patch(
            "no1.daemon.mcp_install.subprocess.run"
        ) as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "cccc\n  enabled: true\n  transport: stdio\n"
            mock_run.return_value.stderr = ""

            self.assertTrue(is_mcp_installed("codex"))

        mock_run.assert_called_once_with(
            [r"C:\Tools\codex.cmd", "mcp", "get", "onecolleague"],
            capture_output=True,
            timeout=10,
            text=True,
        )

    def test_ensure_mcp_installed_codex_uses_resolved_windows_cli_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install._runtime_mcp_state", side_effect=["missing", "ready"]), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=["C:\\OneColleague\\onecolleague.exe", "mcp"],
            ), patch(
                "no1.daemon.mcp_install.resolve_subprocess_argv",
                side_effect=lambda argv: [r"C:\Tools\codex.cmd", *list(argv)[1:]],
            ), patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                mock_run.side_effect = [
                    Mock(returncode=1, stdout="", stderr=""),
                    Mock(returncode=0, stdout="", stderr=""),
                ]

                ok = ensure_mcp_installed("codex", cwd, auto_mcp_runtimes=("codex",))

            self.assertTrue(ok)
            self.assertEqual(
                mock_run.call_args_list,
                [
                    call(
                        [r"C:\Tools\codex.cmd", "mcp", "get", "cccc"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    ),
                    call(
                        [r"C:\Tools\codex.cmd", "mcp", "add", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                        capture_output=True,
                        text=True,
                        cwd=str(cwd),
                        timeout=30,
                    ),
                ],
            )

    def test_ensure_mcp_installed_codex_removes_legacy_cccc_server(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install._runtime_mcp_state", side_effect=["missing", "ready"]), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=["C:\\OneColleague\\onecolleague.exe", "mcp"],
            ), patch(
                "no1.daemon.mcp_install.resolve_subprocess_argv",
                side_effect=lambda argv: list(argv),
            ), patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                mock_run.side_effect = [
                    Mock(returncode=0, stdout="cccc\n  enabled: true\n", stderr=""),
                    Mock(returncode=0, stdout="", stderr=""),
                    Mock(returncode=0, stdout="", stderr=""),
                ]

                ok = ensure_mcp_installed("codex", cwd, auto_mcp_runtimes=("codex",))

            self.assertTrue(ok)
            self.assertEqual(
                mock_run.call_args_list,
                [
                    call(
                        ["codex", "mcp", "get", "cccc"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    ),
                    call(
                        ["codex", "mcp", "remove", "cccc"],
                        capture_output=True,
                        text=True,
                        cwd=str(cwd),
                        timeout=30,
                    ),
                    call(
                        ["codex", "mcp", "add", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                        capture_output=True,
                        text=True,
                        cwd=str(cwd),
                        timeout=30,
                    ),
                ],
            )

    def test_ensure_mcp_installed_codex_windows_repairs_stale_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install.sys.platform", "win32"), patch(
                "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command",
                return_value=["C:\\OneColleague\\onecolleague.exe", "mcp"],
            ), patch(
                "no1.daemon.mcp_install.resolve_subprocess_argv",
                side_effect=lambda argv: list(argv),
            ):
                with patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                    mock_run.side_effect = [
                        Mock(
                            returncode=0,
                            stdout=(
                                "onecolleague\n"
                                "  enabled: true\n"
                                "  transport: stdio\n"
                                "  command: C:\\Old\\onecolleague.exe\n"
                                "  args: mcp\n"
                            ),
                        ),
                        Mock(returncode=1, stdout="", stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                        Mock(returncode=0, stdout="", stderr=""),
                        Mock(
                            returncode=0,
                            stdout=(
                                "onecolleague\n"
                                "  enabled: true\n"
                                "  transport: stdio\n"
                                "  command: C:\\OneColleague\\onecolleague.exe\n"
                                "  args: mcp\n"
                            ),
                        ),
                    ]
                    ok = ensure_mcp_installed("codex", cwd, auto_mcp_runtimes=("codex",))
                    self.assertTrue(ok)
                    self.assertEqual(
                        mock_run.call_args_list,
                        [
                            call(
                                ["codex", "mcp", "get", "onecolleague"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            ),
                            call(
                                ["codex", "mcp", "get", "cccc"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            ),
                            call(
                                ["codex", "mcp", "remove", "onecolleague"],
                                capture_output=True,
                                text=True,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["codex", "mcp", "add", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                                capture_output=True,
                                text=True,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["codex", "mcp", "get", "onecolleague"],
                                capture_output=True,
                                text=True,
                                timeout=10,
                            ),
                        ],
                    )

    def test_get_onecolleague_mcp_stdio_command_prefers_onecolleague_sibling_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td)
            python_exe = bin_dir / "python.exe"
            onecolleague_exe = bin_dir / "onecolleague.exe"
            python_exe.write_text("", encoding="utf-8")
            onecolleague_exe.write_text("", encoding="utf-8")
            with patch("no1.kernel.runtime.sys.platform", "win32"), patch(
                "no1.kernel.runtime.sys.executable",
                str(python_exe),
            ), patch("no1.kernel.runtime.shutil.which", return_value=None):
                self.assertEqual(get_onecolleague_mcp_stdio_command(), [str(onecolleague_exe.resolve()), "mcp"])

    def test_get_onecolleague_mcp_stdio_command_falls_back_to_python_module(self) -> None:
        with patch("no1.kernel.runtime.sys.platform", "win32"), patch(
            "no1.kernel.runtime.sys.executable",
            "C:\\Python312\\python.exe",
        ), patch("no1.kernel.runtime.shutil.which", return_value=None):
            self.assertEqual(
                get_onecolleague_mcp_stdio_command(),
                ["C:\\Python312\\python.exe", "-m", "no1.ports.mcp.main"],
            )


if __name__ == "__main__":
    unittest.main()
