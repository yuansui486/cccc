import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml  # type: ignore


class TestHermesRuntime(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        old_hermes_home = os.environ.get("HERMES_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td
        os.environ.pop("HERMES_HOME", None)

        def cleanup() -> None:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home
            if old_hermes_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_hermes_home
            td_ctx.__exit__(None, None, None)

        return Path(td), cleanup

    def _write_ready_config(self, config_path: Path, command: list[str]) -> None:
        self._write_ready_config_for_server(config_path, command, server_name="onecolleague")

    def _write_ready_config_for_server(self, config_path: Path, command: list[str], *, server_name: str) -> None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            yaml.safe_dump(
                {
                    "mcp_servers": {
                        server_name: {
                            "command": command[0],
                            "args": command[1:],
                            "env": {
                                "ONECOLLEAGUE_HOME": "${ONECOLLEAGUE_HOME}",
                                "ONECOLLEAGUE_GROUP_ID": "${ONECOLLEAGUE_GROUP_ID}",
                                "ONECOLLEAGUE_ACTOR_ID": "${ONECOLLEAGUE_ACTOR_ID}",
                                "CCCC_HOME": "${CCCC_HOME}",
                                "CCCC_GROUP_ID": "${CCCC_GROUP_ID}",
                                "CCCC_ACTOR_ID": "${CCCC_ACTOR_ID}",
                            },
                            "enabled": True,
                        }
                    }
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def test_status_defaults_to_user_hermes_home(self) -> None:
        from no1.kernel.hermes_runtime import hermes_runtime_status

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        try:
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ), patch(
                "no1.kernel.hermes_runtime._hermes_version",
                return_value="Hermes Agent v0.14.0",
            ):
                status = hermes_runtime_status(home=cccc_home)

            self.assertEqual(status["phase"], "phase1_pty_runtime_mvp")
            self.assertTrue(status["user_facing_actor_runtime_enabled"])
            self.assertFalse(status["setup_ready"])
            self.assertFalse(status["launch_ready"])
            self.assertEqual(status["hermes_home"], str(user_home / ".hermes"))
            self.assertEqual(status["profile"]["name"], "default")
            self.assertIn("profile_missing", status["issues"])
        finally:
            cleanup()

    def test_onecolleague_provider_merge_keeps_secrets_out_of_config(self) -> None:
        from no1.kernel.hermes_runtime import merge_hermes_onecolleague_provider

        config = {
            "model": {"provider": "xai-oauth", "default": "grok-code-fast-1"},
            "providers": {"other": {"base_url": "https://other.example/v1"}},
        }
        merged = merge_hermes_onecolleague_provider(
            config,
            env={
                "ONECOLLEAGUE_OPENCODE_BASE_URL": "https://peer.example/v1/",
                "ONECOLLEAGUE_API_KEY": "must-not-be-persisted",
            },
        )

        self.assertEqual(merged["model"], config["model"])
        self.assertEqual(merged["providers"]["other"], config["providers"]["other"])
        provider = merged["providers"]["onecolleague"]
        self.assertEqual(provider["base_url"], "https://peer.example/v1")
        self.assertEqual(provider["key_env"], "ONECOLLEAGUE_API_KEY")
        self.assertEqual(provider["api_mode"], "chat_completions")
        self.assertNotIn("must-not-be-persisted", yaml.safe_dump(merged))

    def test_provider_status_rejects_wrong_endpoint_or_transport(self) -> None:
        from no1.kernel.hermes_runtime import _inspect_onecolleague_provider

        status = _inspect_onecolleague_provider(
            {
                "providers": {
                    "onecolleague": {
                        "name": "OneColleague",
                        "base_url": "https://wrong.example/v1",
                        "key_env": "ONECOLLEAGUE_API_KEY",
                        "api_mode": "anthropic_messages",
                    }
                }
            }
        )
        self.assertEqual(status["status"], "missing")
        self.assertEqual(status["expected_api_mode"], "chat_completions")

    def test_launch_command_uses_per_actor_model_without_duplicates(self) -> None:
        from no1.kernel.hermes_runtime import normalize_hermes_launch_command

        command = normalize_hermes_launch_command(
            ["hermes", "--tui", "--yolo"],
            selected_model="deepseek-v4-pro",
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
        manual = normalize_hermes_launch_command(
            ["hermes", "--provider", "custom:manual", "--model", "manual-model", "--tui"],
            selected_model="ignored-model",
        )
        self.assertIn(Path(manual[0]).name.lower(), {"hermes", "hermes.exe", "hermes.cmd", "hermes.bat"})
        self.assertEqual(manual[1:], ["--provider", "custom:manual", "--model", "manual-model", "--tui"])

    def test_prebuilt_tui_dir_finds_bundle_from_hermes_venv(self) -> None:
        from no1.kernel.hermes_runtime import hermes_prebuilt_tui_dir

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "hermes-agent"
            executable = root / "venv" / "Scripts" / "hermes.exe"
            tui_dir = root / "ui-tui"
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            (tui_dir / "dist").mkdir(parents=True)
            (tui_dir / "dist" / "entry.js").write_text("", encoding="utf-8")
            (tui_dir / "node_modules").mkdir()
            (tui_dir / "node_modules" / "undici").mkdir()

            with patch("no1.kernel.hermes_runtime.find_subprocess_executable", return_value=str(executable)):
                self.assertEqual(
                    hermes_prebuilt_tui_dir(["hermes", "--tui", "--yolo"]),
                    tui_dir,
                )

    def test_prebuilt_tui_dir_requires_tui_flag_and_complete_bundle(self) -> None:
        from no1.kernel.hermes_runtime import hermes_prebuilt_tui_dir

        with tempfile.TemporaryDirectory() as td:
            executable = Path(td) / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
            executable.parent.mkdir(parents=True)
            executable.write_text("", encoding="utf-8")
            with patch("no1.kernel.hermes_runtime.find_subprocess_executable", return_value=str(executable)):
                self.assertIsNone(hermes_prebuilt_tui_dir(["hermes", "--yolo"]))
                self.assertIsNone(hermes_prebuilt_tui_dir(["hermes", "--tui", "--yolo"]))

    def test_status_accepts_existing_onecolleague_entrypoint_after_install_path_changes(self) -> None:
        from no1.kernel.hermes_runtime import hermes_runtime_status

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        installed = cccc_home / "packaged" / "onecolleague.exe"
        current = cccc_home / "source" / "onecolleague.exe"
        installed.parent.mkdir(parents=True)
        current.parent.mkdir(parents=True)
        installed.write_text("", encoding="utf-8")
        current.write_text("", encoding="utf-8")
        try:
            self._write_ready_config(user_home / ".hermes" / "config.yaml", [str(installed), "mcp"])
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=[str(current), "mcp"],
            ), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ):
                status = hermes_runtime_status(home=cccc_home, include_version=False)

            self.assertEqual(status["mcp"]["status"], "ready")
            self.assertTrue(status["mcp"]["command_matches"])
        finally:
            cleanup()

    def test_status_rejects_missing_alternate_onecolleague_entrypoint(self) -> None:
        from no1.kernel.hermes_runtime import hermes_runtime_status

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        current = cccc_home / "source" / "onecolleague.exe"
        current.parent.mkdir(parents=True)
        current.write_text("", encoding="utf-8")
        try:
            self._write_ready_config(
                user_home / ".hermes" / "config.yaml",
                [str(cccc_home / "missing" / "onecolleague.exe"), "mcp"],
            )
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=[str(current), "mcp"],
            ), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ):
                status = hermes_runtime_status(home=cccc_home, include_version=False)

            self.assertEqual(status["mcp"]["status"], "stale")
            self.assertFalse(status["mcp"]["command_matches"])
        finally:
            cleanup()

    def test_status_respects_explicit_hermes_home_env(self) -> None:
        from no1.kernel.hermes_runtime import hermes_runtime_status

        cccc_home, cleanup = self._with_home()
        explicit_home = cccc_home / "explicit-hermes"
        command = ["/abs/onecolleague", "mcp"]
        try:
            os.environ["HERMES_HOME"] = str(explicit_home)
            self._write_ready_config(explicit_home / "config.yaml", command)
            with patch("no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command", return_value=command), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ):
                status = hermes_runtime_status(home=cccc_home, include_version=False)

            self.assertEqual(status["hermes_home"], str(explicit_home))
            self.assertEqual(status["profile"]["dir"], str(explicit_home))
            self.assertEqual(status["profile"]["config_path"], str(explicit_home / "config.yaml"))
            self.assertEqual(status["mcp"]["status"], "ready")
        finally:
            cleanup()

    def test_status_requires_mcp_placeholders_not_static_actor_ids(self) -> None:
        from no1.kernel.hermes_runtime import hermes_runtime_status

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        command = ["/abs/onecolleague", "mcp"]
        config_path = user_home / ".hermes" / "config.yaml"
        try:
            self._write_ready_config(config_path, command)
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=command,
            ), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ):
                ready = hermes_runtime_status(home=cccc_home, include_version=False)
            self.assertEqual(ready["mcp"]["status"], "ready")

            doc = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            doc["mcp_servers"]["onecolleague"]["env"]["ONECOLLEAGUE_ACTOR_ID"] = "peer1"
            config_path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=command,
            ), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ):
                stale = hermes_runtime_status(home=cccc_home, include_version=False)
            self.assertEqual(stale["mcp"]["status"], "stale")
            self.assertFalse(stale["mcp"]["env_placeholders_match"])
        finally:
            cleanup()

    def test_prepare_uses_default_profile_mcp_add_with_confirmation(self) -> None:
        from no1.kernel import hermes_runtime

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        command = ["/abs/onecolleague", "mcp"]
        config_path = user_home / ".hermes" / "config.yaml"
        calls: list[tuple[list[str], str | None, str]] = []

        def fake_run(argv, *, hermes_home_path=None, cwd=None, timeout=60, input_text=None, extra_env=None):
            calls.append((list(argv), input_text, str(hermes_home_path or "")))
            if argv[:4] == ["hermes", "mcp", "add", "onecolleague"]:
                self.assertEqual(input_text, "Y\n")
                self.assertIsNone(hermes_home_path)
                self._write_ready_config(config_path, command)
            return Mock(returncode=0, stdout="", stderr="")

        try:
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=command,
            ), patch(
                "no1.kernel.hermes_runtime._hermes_mcp_sdk_status",
                return_value={"available": True, "python": "/usr/bin/python"},
            ), patch.object(hermes_runtime, "_run_hermes_cli", side_effect=fake_run):
                result = hermes_runtime.prepare_hermes_runtime(home=cccc_home, auto_enable_tools=True)

            self.assertTrue(result.get("ok"), result)
            self.assertEqual(
                calls[0][0],
                [
                    "hermes",
                    "mcp",
                    "add",
                    "onecolleague",
                    "--command",
                    "/abs/onecolleague",
                    "--env",
                    f"ONECOLLEAGUE_HOME={cccc_home}",
                    "ONECOLLEAGUE_GROUP_ID=g_probe",
                    "ONECOLLEAGUE_ACTOR_ID=hermes-probe",
                    f"CCCC_HOME={cccc_home}",
                    "CCCC_GROUP_ID=g_probe",
                    "CCCC_ACTOR_ID=hermes-probe",
                    "--args",
                    "mcp",
                ],
            )
            doc = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            env = doc["mcp_servers"]["onecolleague"]["env"]
            self.assertEqual(env["ONECOLLEAGUE_HOME"], "${ONECOLLEAGUE_HOME}")
            self.assertEqual(env["ONECOLLEAGUE_GROUP_ID"], "${ONECOLLEAGUE_GROUP_ID}")
            self.assertEqual(env["ONECOLLEAGUE_ACTOR_ID"], "${ONECOLLEAGUE_ACTOR_ID}")
            self.assertEqual(env["CCCC_HOME"], "${CCCC_HOME}")
            self.assertEqual(env["CCCC_GROUP_ID"], "${CCCC_GROUP_ID}")
            self.assertEqual(env["CCCC_ACTOR_ID"], "${CCCC_ACTOR_ID}")
        finally:
            cleanup()

    def test_prepare_skips_mcp_add_when_config_ready(self) -> None:
        from no1.kernel import hermes_runtime

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        command = ["/abs/onecolleague", "mcp"]
        try:
            self._write_ready_config(user_home / ".hermes" / "config.yaml", command)
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/usr/bin/hermes",
            ), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=command,
            ), patch(
                "no1.kernel.hermes_runtime._hermes_mcp_sdk_status",
                return_value={"available": True, "python": "/usr/bin/python"},
            ), patch.object(hermes_runtime, "_run_hermes_cli") as mock_run:
                result = hermes_runtime.prepare_hermes_runtime(home=cccc_home, auto_enable_tools=True)

            self.assertTrue(result.get("ok"), result)
            mock_run.assert_not_called()
        finally:
            cleanup()

    def test_prepare_installs_missing_sdk_without_readding_ready_mcp(self) -> None:
        from no1.kernel import hermes_runtime

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        command = ["/abs/onecolleague", "mcp"]
        sdk_states = iter(
            [
                {"available": False, "python": "/hermes/venv/python"},
                {"available": True, "python": "/hermes/venv/python"},
            ]
        )
        try:
            self._write_ready_config(user_home / ".hermes" / "config.yaml", command)
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/hermes/venv/bin/hermes",
            ), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=command,
            ), patch(
                "no1.kernel.hermes_runtime._hermes_mcp_sdk_status",
                side_effect=lambda *_args, **_kwargs: next(sdk_states),
            ), patch(
                "no1.kernel.hermes_runtime._install_hermes_mcp_sdk",
                return_value=(
                    ["uv", "pip", "install", "mcp==1.28.1"],
                    Mock(returncode=0, stdout="installed", stderr=""),
                ),
            ) as install, patch.object(hermes_runtime, "_run_hermes_cli") as mock_run:
                result = hermes_runtime.prepare_hermes_runtime(home=cccc_home, auto_enable_tools=True)

            self.assertTrue(result.get("ok"), result)
            install.assert_called_once()
            mock_run.assert_not_called()
            self.assertEqual([item["name"] for item in result["commands_run"]], ["mcp_sdk_install"])
        finally:
            cleanup()

    def test_mcp_test_reports_connection_failure_despite_zero_exit_code(self) -> None:
        from no1.kernel import hermes_runtime

        with patch.object(
            hermes_runtime,
            "_run_hermes_cli",
            return_value=Mock(returncode=0, stdout="Connection failed: MCP SDK missing", stderr=""),
        ):
            result = hermes_runtime.run_hermes_mcp_test()

        self.assertFalse(result["ok"])

    def test_prepare_installs_missing_hermes_mcp_sdk_before_discovery(self) -> None:
        from no1.kernel import hermes_runtime

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        command = ["/abs/onecolleague", "mcp"]
        config_path = user_home / ".hermes" / "config.yaml"
        sdk_states = iter(
            [
                {"available": False, "python": "/hermes/venv/python"},
                {"available": True, "python": "/hermes/venv/python"},
            ]
        )

        def fake_run(argv, **kwargs):
            if argv[:4] == ["hermes", "mcp", "add", "onecolleague"]:
                config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                config["mcp_servers"] = {
                    "onecolleague": {
                        "command": command[0],
                        "args": command[1:],
                        "env": dict(hermes_runtime.HERMES_MCP_ENV_PLACEHOLDERS),
                        "enabled": True,
                    }
                }
                config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
            return Mock(returncode=0, stdout="", stderr="")

        try:
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/hermes/venv/bin/hermes",
            ), patch(
                "no1.kernel.hermes_runtime.get_onecolleague_mcp_stdio_command",
                return_value=command,
            ), patch(
                "no1.kernel.hermes_runtime._hermes_mcp_sdk_status",
                side_effect=lambda *_args, **_kwargs: next(sdk_states),
            ), patch(
                "no1.kernel.hermes_runtime._install_hermes_mcp_sdk",
                return_value=(
                    ["uv", "pip", "install", "mcp==1.28.1"],
                    Mock(returncode=0, stdout="installed", stderr=""),
                ),
            ) as install, patch.object(hermes_runtime, "_run_hermes_cli", side_effect=fake_run):
                result = hermes_runtime.prepare_hermes_runtime(home=cccc_home, auto_enable_tools=True)

            self.assertTrue(result.get("ok"), result)
            install.assert_called_once()
            self.assertEqual(result["commands_run"][0]["name"], "mcp_sdk_install")
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            self.assertEqual(
                config["providers"]["onecolleague"]["base_url"],
                "https://peer.shierkeji.com/v1",
            )
        finally:
            cleanup()

    def test_prepare_keeps_provider_when_mcp_sdk_install_fails(self) -> None:
        from no1.kernel import hermes_runtime

        cccc_home, cleanup = self._with_home()
        user_home = cccc_home / "user"
        config_path = user_home / ".hermes" / "config.yaml"
        try:
            with patch("no1.kernel.hermes_runtime.Path.home", return_value=user_home), patch(
                "no1.kernel.hermes_runtime.find_subprocess_executable",
                return_value="/hermes/venv/bin/hermes",
            ), patch(
                "no1.kernel.hermes_runtime._hermes_mcp_sdk_status",
                return_value={"available": False, "python": "/hermes/venv/python"},
            ), patch(
                "no1.kernel.hermes_runtime._install_hermes_mcp_sdk",
                return_value=(
                    ["uv", "pip", "install", "mcp==1.28.1"],
                    Mock(returncode=1, stdout="", stderr="offline"),
                ),
            ):
                result = hermes_runtime.prepare_hermes_runtime(home=cccc_home, auto_enable_tools=True)

            self.assertFalse(result.get("ok"), result)
            self.assertEqual(result["error"]["code"], "hermes_mcp_sdk_install_failed")
            self.assertIn("offline", result["error"]["message"])
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            self.assertEqual(
                config["providers"]["onecolleague"]["base_url"],
                "https://peer.shierkeji.com/v1",
            )
        finally:
            cleanup()

    def test_placeholder_normalization_preserves_unrelated_comments(self) -> None:
        from no1.kernel.hermes_runtime import _normalize_mcp_config_placeholders

        cccc_home, cleanup = self._with_home()
        try:
            config_path = cccc_home / "user" / ".hermes" / "config.yaml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                (
                    "# keep header\n"
                    "model: ''\n"
                    "mcp_servers:\n"
                    "  onecolleague:\n"
                    "    command: /abs/onecolleague\n"
                    "    args:\n"
                    "    - mcp\n"
                    "    env:\n"
                    f"      ONECOLLEAGUE_HOME: {cccc_home}\n"
                    "      ONECOLLEAGUE_GROUP_ID: g_probe\n"
                    "      ONECOLLEAGUE_ACTOR_ID: hermes-probe\n"
                    f"      CCCC_HOME: {cccc_home}\n"
                    "      CCCC_GROUP_ID: g_probe\n"
                    "      CCCC_ACTOR_ID: hermes-probe\n"
                    "    enabled: true\n"
                    "# keep footer\n"
                ),
                encoding="utf-8",
            )

            _normalize_mcp_config_placeholders(config_path)

            text = config_path.read_text(encoding="utf-8")
            self.assertIn("# keep header", text)
            self.assertIn("# keep footer", text)
            self.assertIn("ONECOLLEAGUE_HOME: ${ONECOLLEAGUE_HOME}", text)
            self.assertIn("ONECOLLEAGUE_GROUP_ID: ${ONECOLLEAGUE_GROUP_ID}", text)
            self.assertIn("ONECOLLEAGUE_ACTOR_ID: ${ONECOLLEAGUE_ACTOR_ID}", text)
            self.assertIn("CCCC_HOME: ${CCCC_HOME}", text)
            self.assertIn("CCCC_GROUP_ID: ${CCCC_GROUP_ID}", text)
            self.assertIn("CCCC_ACTOR_ID: ${CCCC_ACTOR_ID}", text)
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
