import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, call, patch

from no1.daemon.mcp_install import ensure_mcp_installed, is_mcp_installed, prepare_runtime_mcp_env
from no1.kernel.runtime import get_onecolleague_mcp_stdio_command


class TestMcpInstall(unittest.TestCase):
    def test_prepare_runtime_mcp_env_marks_claude_as_host_managed(self) -> None:
        prepared = prepare_runtime_mcp_env(
            "claude",
            {
                "ANTHROPIC_BASE_URL": "https://peer.example/claude",
                "ANTHROPIC_AUTH_TOKEN": "actor-token",
            },
        )

        self.assertEqual(prepared["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"], "1")
        self.assertEqual(prepared["ANTHROPIC_BASE_URL"], "https://peer.example/claude")
        self.assertEqual(prepared["ANTHROPIC_AUTH_TOKEN"], "actor-token")

    def test_prepare_runtime_mcp_env_injects_selected_kimi_model(self) -> None:
        prepared = prepare_runtime_mcp_env(
            "kimi",
            {"KIMI_MODEL_NAME": "old-model", "KIMI_API_KEY": "secret"},
            runtime_options={"selected_model": "kimi-k2.6"},
        )
        self.assertEqual(prepared["KIMI_MODEL_NAME"], "kimi-k2.6")
        self.assertEqual(prepared["KIMI_API_KEY"], "secret")

    def test_prepare_runtime_mcp_env_injects_selected_hermes_model(self) -> None:
        with patch("no1.daemon.mcp_install.hermes_prebuilt_tui_dir", return_value=None):
            prepared = prepare_runtime_mcp_env(
                "hermes",
                {"ONECOLLEAGUE_API_KEY": "secret"},
                runtime_options={"selected_model": "deepseek-v4-pro"},
            )
        self.assertEqual(prepared["HERMES_SELECTED_MODEL"], "deepseek-v4-pro")
        self.assertEqual(prepared["ONECOLLEAGUE_API_KEY"], "secret")

    def test_prepare_runtime_mcp_env_injects_prebuilt_hermes_tui(self) -> None:
        tui_dir = Path("/opt/hermes/ui-tui")
        with patch("no1.daemon.mcp_install.hermes_prebuilt_tui_dir", return_value=tui_dir) as discover:
            prepared = prepare_runtime_mcp_env(
                "hermes",
                {"ONECOLLEAGUE_API_KEY": "secret"},
                command=["hermes", "--tui", "--yolo"],
            )

        self.assertEqual(prepared["HERMES_TUI_DIR"], str(tui_dir))
        discover.assert_called_once_with(["hermes", "--tui", "--yolo"])

    def test_prepare_runtime_mcp_env_preserves_explicit_hermes_tui(self) -> None:
        with patch("no1.daemon.mcp_install.hermes_prebuilt_tui_dir") as discover:
            prepared = prepare_runtime_mcp_env(
                "hermes",
                {"HERMES_TUI_DIR": "/custom/tui"},
                command=["hermes", "--tui", "--yolo"],
            )

        self.assertEqual(prepared["HERMES_TUI_DIR"], "/custom/tui")
        discover.assert_not_called()

    def test_is_mcp_installed_unknown_runtime_false(self) -> None:
        self.assertFalse(is_mcp_installed("unknown-runtime"))

    def test_ensure_mcp_installed_skips_non_auto_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
                ok = ensure_mcp_installed("unknown-runtime", cwd, auto_mcp_runtimes=("claude", "codex"))
                self.assertTrue(ok)
                mock_run.assert_not_called()

    def test_prepare_runtime_mcp_env_opencode_injects_inline_config(self) -> None:
        env = {
            "ONECOLLEAGUE_HOME": "/tmp/onecolleague-home",
            "CCCC_HOME": "/tmp/onecolleague-home",
            "ONECOLLEAGUE_GROUP_ID": "g_123",
            "ONECOLLEAGUE_ACTOR_ID": "peer1",
            "OPENCODE_CONFIG_CONTENT": json.dumps({"mcp": {"other": {"type": "local", "command": ["other"]}}}),
        }
        with patch(
            "no1.daemon.opencode_provider.load_opencode_model_catalog",
            return_value=[
                {"model": "gpt-5.4", "locked": True},
                {"model": "deepseek-v4-pro", "locked": False},
            ],
        ), patch(
            "no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command", return_value=["/abs/onecolleague", "mcp"]
        ):
            prepared = prepare_runtime_mcp_env(
                "opencode",
                env,
                command=["opencode", "-m", "onecolleague/deepseek-v4-pro"],
                runtime_options={"opencode": {"default_variant": "max"}},
            )
        doc = json.loads(prepared["OPENCODE_CONFIG_CONTENT"])
        self.assertEqual(doc["mcp"]["other"]["command"], ["other"])
        self.assertEqual(doc["mcp"]["onecolleague"]["command"], ["/abs/onecolleague", "mcp"])
        self.assertEqual(doc["mcp"]["onecolleague"]["environment"]["ONECOLLEAGUE_GROUP_ID"], "g_123")
        provider = doc["provider"]["onecolleague"]
        self.assertEqual(provider["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(provider["options"]["baseURL"], "https://peer.shierkeji.com/v1")
        self.assertEqual(provider["options"]["apiKey"], "{env:ONECOLLEAGUE_API_KEY}")
        self.assertTrue(provider["models"])
        self.assertEqual(
            list(provider["models"]["deepseek-v4-pro"]["variants"]),
            ["none", "low", "medium", "high", "max"],
        )
        self.assertTrue(provider["models"]["deepseek-v4-pro"]["variants"]["medium"]["disabled"])
        self.assertEqual(doc["agent"]["build"]["model"], "onecolleague/deepseek-v4-pro")
        self.assertEqual(doc["agent"]["build"]["variant"], "max")
        with patch("no1.daemon.mcp_install.get_onecolleague_mcp_stdio_command", return_value=["/abs/onecolleague", "mcp"]):
            self.assertTrue(is_mcp_installed("opencode", env=prepared))

    def test_ensure_mcp_installed_opencode_uses_prepared_env_without_cli(self) -> None:
        with patch("no1.daemon.opencode_provider.load_opencode_model_catalog", return_value=[{"model": "gpt-5.4", "locked": True}]):
            env = prepare_runtime_mcp_env("opencode", {"ONECOLLEAGUE_HOME": "/tmp/home"})
        with tempfile.TemporaryDirectory() as td, patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
            self.assertTrue(ensure_mcp_installed("opencode", Path(td), auto_mcp_runtimes=("opencode",), env=env))
            mock_run.assert_not_called()

    def test_ensure_mcp_installed_opencode_missing_inline_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch("no1.daemon.mcp_install.subprocess.run") as mock_run:
            self.assertFalse(ensure_mcp_installed("opencode", Path(td), auto_mcp_runtimes=("opencode",), env={}))
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
                        encoding="utf-8",
                        errors="replace",
                        env=ANY,
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
                                env=ANY,
                                timeout=10,
                            ),
                            call(
                                ["claude", "mcp", "get", "cccc"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
                                timeout=10,
                            ),
                            call(
                                ["claude", "mcp", "remove", "onecolleague", "-s", "user"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["claude", "mcp", "add", "-s", "user", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["claude", "mcp", "get", "onecolleague"],
                                capture_output=True,
                                text=False,
                                env=ANY,
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
                                encoding="utf-8",
                                errors="replace",
                                timeout=10,
                                env=ANY,
                            ),
                            call(
                                ["codex", "mcp", "add", "onecolleague", "--", "/abs/onecolleague", "mcp"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                cwd=str(cwd),
                                timeout=30,
                                env=ANY,
                            ),
                        ],
                    )
                    for run_call in mock_run.call_args_list:
                        run_env = run_call.kwargs["env"]
                        self.assertEqual(run_env["CODEX_HOME"], env["CODEX_HOME"])
                        self.assertEqual(run_env["OPENAI_API_KEY"], env["OPENAI_API_KEY"])
                        self.assertEqual(run_env["PYTHONUTF8"], "1")
                        self.assertEqual(run_env["PYTHONIOENCODING"], "utf-8")

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
                provider_env=env,
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
                provider_env=env,
            )

    def test_ensure_mcp_installed_hermes_surfaces_prepare_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td)
            with patch("no1.daemon.mcp_install._runtime_mcp_state", return_value="missing"), patch(
                "no1.daemon.mcp_install.prepare_hermes_runtime",
                return_value={
                    "ok": False,
                    "error": {"code": "hermes_cli_missing", "message": "Hermes CLI is not installed or not in PATH"},
                },
            ):
                with self.assertRaisesRegex(RuntimeError, "hermes_cli_missing"):
                    ensure_mcp_installed("hermes", cwd, auto_mcp_runtimes=("hermes",), env={})

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
            encoding="utf-8",
            errors="replace",
            env=ANY,
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
                        encoding="utf-8",
                        errors="replace",
                        env=ANY,
                        timeout=10,
                    ),
                    call(
                        [r"C:\Tools\codex.cmd", "mcp", "add", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=ANY,
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
                        encoding="utf-8",
                        errors="replace",
                        env=ANY,
                        timeout=10,
                    ),
                    call(
                        ["codex", "mcp", "remove", "cccc"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=ANY,
                        cwd=str(cwd),
                        timeout=30,
                    ),
                    call(
                        ["codex", "mcp", "add", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=ANY,
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
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
                                timeout=10,
                            ),
                            call(
                                ["codex", "mcp", "get", "cccc"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
                                timeout=10,
                            ),
                            call(
                                ["codex", "mcp", "remove", "onecolleague"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["codex", "mcp", "add", "onecolleague", "--", "C:\\OneColleague\\onecolleague.exe", "mcp"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
                                cwd=str(cwd),
                                timeout=30,
                            ),
                            call(
                                ["codex", "mcp", "get", "onecolleague"],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                env=ANY,
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
        ), patch("no1.kernel.runtime.sys.prefix", "C:\\Python312"), patch(
            "no1.kernel.runtime.Path.exists",
            return_value=False,
        ), patch("no1.kernel.runtime.shutil.which", return_value=None):
            self.assertEqual(
                get_onecolleague_mcp_stdio_command(),
                ["C:\\Python312\\python.exe", "-m", "no1.ports.mcp.main"],
            )


if __name__ == "__main__":
    unittest.main()
