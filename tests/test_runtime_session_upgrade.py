import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class TestRuntimeSessionUpgrade(unittest.TestCase):
    def setUp(self) -> None:
        self._home_vars = ("ONECOLLEAGUE_HOME", "CCCC_HOME")
        self._old_homes = {name: os.environ.get(name) for name in self._home_vars}
        self._temp = tempfile.TemporaryDirectory()
        for name in self._home_vars:
            os.environ[name] = self._temp.name

    def tearDown(self) -> None:
        for name, old_home in self._old_homes.items():
            if old_home is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_home
        self._temp.cleanup()

    def test_grok_managed_session_first_start_and_resume(self) -> None:
        from no1.daemon.runtime_session_ops import (
            prepare_initial_pty_session_command,
            prepare_pty_resume_command,
        )

        cwd = Path(self._temp.name) / "repo"
        cwd.mkdir()
        session_id = "019d6d3e-b066-7dc0-bd42-ed621a7ddccc"
        base_command = ["grok", "--always-approve"]
        with patch("no1.daemon.runtime_session_ops.uuid.uuid4", return_value=session_id):
            command, doc = prepare_initial_pty_session_command(
                group_id="g1",
                actor_id="peer1",
                runtime="grok",
                cwd=cwd,
                base_command=base_command,
                env={},
                max_backlog_bytes=1000,
            )

        self.assertEqual(command, ["grok", "--session-id", session_id, "--always-approve"])
        self.assertEqual((doc or {}).get("captured_from"), "grok_generated_session_id")
        command, doc = prepare_pty_resume_command(
            group_id="g1",
            actor_id="peer1",
            runtime="grok",
            cwd=cwd,
            base_command=base_command,
        )
        self.assertEqual(command, ["grok", "--resume", session_id, "--always-approve"])
        self.assertIsNotNone(doc)

    def test_grok_user_session_control_and_subcommand_are_preserved(self) -> None:
        from no1.daemon.runtime_session_ops import prepare_initial_pty_session_command

        cwd = Path(self._temp.name) / "repo"
        cwd.mkdir()
        for actor_id, base_command in (
            ("resume", ["grok", "--resume=existing"]),
            ("short", ["grok", "-s12345678"]),
            ("subcommand", ["grok", "sessions", "list"]),
        ):
            command, doc = prepare_initial_pty_session_command(
                group_id="g1",
                actor_id=actor_id,
                runtime="grok",
                cwd=cwd,
                base_command=base_command,
                env={},
                max_backlog_bytes=1000,
            )
            self.assertEqual(command, base_command)
            self.assertIsNone(doc)

    def test_auth_failure_only_marks_matching_session_identity(self) -> None:
        from no1.daemon.runtime_session_ops import (
            mark_runtime_session_auth_failed,
            read_runtime_session,
            record_codex_app_thread_runtime_session,
        )

        cwd = Path(self._temp.name) / "repo"
        cwd.mkdir()
        command = ["codex", "app-server", "--listen", "ws://127.0.0.1:12345"]
        record_codex_app_thread_runtime_session(
            group_id="g1",
            actor_id="peer1",
            cwd=cwd,
            command=command,
            provider_thread_id="thread-fresh",
            runner="pty",
            captured_from="app_server_thread_start",
        )

        self.assertEqual(
            mark_runtime_session_auth_failed(
                group_id="g1",
                actor_id="peer1",
                error="401 Unauthorized",
                expected_command=command,
                expected_provider_thread_id="thread-stale",
            ),
            {},
        )
        self.assertEqual(read_runtime_session("g1", "peer1").get("status"), "usable")

        marked = mark_runtime_session_auth_failed(
            group_id="g1",
            actor_id="peer1",
            error="401 Unauthorized",
            expected_command=command,
            expected_provider_thread_id="thread-fresh",
        )
        self.assertEqual(marked.get("status"), "auth_failed")
        self.assertFalse(bool(marked.get("resume_eligible")))

    def test_codex_stderr_401_marks_only_current_runtime_session(self) -> None:
        from no1.daemon import codex_app_sessions

        session = codex_app_sessions.CodexAppSession(
            group_id="g1",
            actor_id="peer1",
            cwd=Path(self._temp.name),
            env={},
        )
        session._runtime_command = ["codex", "-c", "mcp_servers.windows-mcp.enabled=false", "app-server"]
        session._session_state.thread_id = "thread-current"
        session._proc = SimpleNamespace(
            stderr=["responses websocket failed: 401 Unauthorized\n"],
        )

        with patch.object(codex_app_sessions, "mark_runtime_session_auth_failed") as mark_failed:
            session._stderr_loop()

        mark_failed.assert_called_once_with(
            group_id="g1",
            actor_id="peer1",
            error="responses websocket failed: 401 Unauthorized",
            expected_command=session._runtime_command,
            expected_provider_thread_id="thread-current",
            require_provider_thread_id_match=True,
        )

    def test_codex_app_identity_ignores_injected_windows_mcp_isolation(self) -> None:
        from no1.computer_control.isolation import codex_windows_mcp_disable_args
        from no1.daemon.runtime_session_ops import runtime_session_command_fingerprint

        recorded = ["codex", "app-server", "--listen", "ws://127.0.0.1:12345"]
        codex_home = Path(self._temp.name) / "codex"
        codex_home.mkdir()
        (codex_home / "config.toml").write_text(
            """
[mcp_servers.custom_alias]
command = "windows-mcp"

[mcp_servers."quoted.alias"]
command = "windows_mcp"
""".strip(),
            encoding="utf-8",
        )
        with patch("no1.computer_control.isolation._windows_mcp_supported_platform", return_value=False):
            disable_args = codex_windows_mcp_disable_args({"CODEX_HOME": str(codex_home)})
        self.assertEqual(
            disable_args,
            [
                "-c",
                "mcp_servers.custom_alias.enabled=false",
                "-c",
                'mcp_servers."quoted.alias".enabled=false',
            ],
        )

        for option, value in zip(disable_args[::2], disable_args[1::2]):
            launched = [
                "codex",
                option,
                value,
                "app-server",
                "--listen",
                "ws://127.0.0.1:54321",
            ]
            self.assertEqual(
                runtime_session_command_fingerprint(recorded),
                runtime_session_command_fingerprint(launched),
            )

        inline = [
            "codex",
            f"--config={disable_args[3]}",
            "app-server",
            "--listen=ws://127.0.0.1:54321",
        ]
        self.assertEqual(
            runtime_session_command_fingerprint(recorded),
            runtime_session_command_fingerprint(inline),
        )

    def test_codex_app_identity_keeps_provider_config(self) -> None:
        from no1.daemon.runtime_session_ops import runtime_session_command_fingerprint

        default = ["codex", "app-server", "--listen", "stdio://"]
        provider = [
            "codex",
            "-c",
            "model_provider=local",
            "app-server",
            "--listen",
            "stdio://",
        ]

        self.assertNotEqual(
            runtime_session_command_fingerprint(default),
            runtime_session_command_fingerprint(provider),
        )

        for config in (
            "mcp_servers.windows-mcp.enabled=true",
            "mcp_servers.windows-mcp.command=local-tool",
            "feature.enabled=false",
        ):
            configured = ["codex", "-c", config, "app-server", "--listen", "stdio://"]
            self.assertNotEqual(
                runtime_session_command_fingerprint(default),
                runtime_session_command_fingerprint(configured),
            )


if __name__ == "__main__":
    unittest.main()
