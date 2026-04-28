from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class TestWindowsSupportDiagnostics(unittest.TestCase):
    def test_platform_support_reports_missing_pywinpty(self) -> None:
        from cccc.runners import platform_support

        missing = ModuleNotFoundError("No module named 'winpty'")
        missing.name = "winpty"
        with patch.object(platform_support.os, "name", "nt"), patch.object(
            platform_support.importlib,
            "import_module",
            side_effect=missing,
        ):
            details = platform_support.pty_support_details()

        self.assertFalse(bool(details.get("supported")))
        self.assertEqual(str(details.get("code") or ""), "pywinpty_missing")
        self.assertIn("pywinpty", str(details.get("message") or ""))
        hints = details.get("hints") if isinstance(details.get("hints"), list) else []
        self.assertTrue(any("pip install pywinpty" in str(item) for item in hints))

    def test_platform_support_matches_real_import_path(self) -> None:
        from cccc.runners import platform_support

        with patch.object(platform_support.os, "name", "nt"), patch.object(
            platform_support.importlib,
            "import_module",
            return_value=SimpleNamespace(PtyProcess=object()),
        ):
            details = platform_support.pty_support_details()
            pty_process = platform_support.load_winpty_process_class()

        self.assertTrue(bool(details.get("supported")))
        self.assertEqual(str(details.get("code") or ""), "")
        self.assertIsNotNone(pty_process)

    def test_platform_support_reports_import_failure(self) -> None:
        from cccc.runners import platform_support

        with patch.object(platform_support.os, "name", "nt"), patch.object(
            platform_support.importlib,
            "import_module",
            side_effect=RuntimeError("native import failed"),
        ):
            details = platform_support.pty_support_details()

        self.assertFalse(bool(details.get("supported")))
        self.assertEqual(str(details.get("code") or ""), "winpty_import_failed")
        self.assertIn("native import failed", str(details.get("message") or ""))

    def test_actor_runtime_returns_explicit_windows_pty_error(self) -> None:
        from cccc.daemon.actors import actor_runtime_ops

        group = SimpleNamespace(
            group_id="g1",
            doc={"active_scope_key": "scope1"},
            save=lambda: None,
            ledger_path="ledger.jsonl",
        )
        actor = {
            "id": "peer1",
            "default_scope_key": "scope1",
            "runner": "pty",
            "runtime": "codex",
            "command": ["codex"],
            "env": {},
        }

        with patch.object(actor_runtime_ops, "find_actor", return_value=actor), patch.object(
            actor_runtime_ops.pty_runner,
            "PTY_SUPPORTED",
            False,
            create=False,
        ), patch.object(
            actor_runtime_ops,
            "pty_support_error_message",
            return_value="Windows PTY backend unavailable: install pywinpty to enable ConPTY actors.",
        ):
            result = actor_runtime_ops.start_actor_process(
                group,
                "peer1",
                command=["codex"],
                env={},
                runner="pty",
                runtime="codex",
                by="user",
                find_scope_url=lambda _group, _scope_key: ".",
                effective_runner_kind=lambda runner: runner,
                merge_actor_env_with_private=lambda _gid, _aid, env: dict(env),
                normalize_runtime_command=lambda _runtime, command: list(command),
                ensure_mcp_installed=lambda _runtime, _cwd, **_kwargs: True,
                inject_actor_context_env=lambda env, _gid, _aid: dict(env),
                prepare_pty_env=lambda env: dict(env),
                pty_backlog_bytes=lambda: 1024,
                write_headless_state=lambda _gid, _aid: None,
                write_pty_state=lambda _gid, _aid, _pid: None,
                clear_preamble_sent=lambda _group, _aid: None,
                throttle_reset_actor=lambda _gid, _aid: None,
                supported_runtimes=("codex",),
            )

        self.assertFalse(bool(result.get("success")))
        self.assertIn("pywinpty", str(result.get("error") or ""))

    def test_actor_runtime_returns_explicit_runtime_unavailable_error(self) -> None:
        from cccc.daemon.actors import actor_runtime_ops

        group = SimpleNamespace(
            group_id="g1",
            doc={"active_scope_key": "scope1"},
            save=lambda: None,
            ledger_path="ledger.jsonl",
        )
        actor = {
            "id": "peer1",
            "default_scope_key": "scope1",
            "runner": "pty",
            "runtime": "codex",
            "command": ["codex"],
            "env": {},
        }

        with patch.object(actor_runtime_ops, "find_actor", return_value=actor), patch.object(
            actor_runtime_ops,
            "runtime_start_preflight_error",
            return_value="runtime unavailable: Codex CLI is not installed or not in PATH",
        ), patch.object(actor_runtime_ops.pty_runner, "PTY_SUPPORTED", True, create=False):
            result = actor_runtime_ops.start_actor_process(
                group,
                "peer1",
                command=["codex"],
                env={},
                runner="pty",
                runtime="codex",
                by="user",
                find_scope_url=lambda _group, _scope_key: ".",
                effective_runner_kind=lambda runner: runner,
                merge_actor_env_with_private=lambda _gid, _aid, env: dict(env),
                normalize_runtime_command=lambda _runtime, command: list(command),
                ensure_mcp_installed=lambda _runtime, _cwd, **_kwargs: True,
                inject_actor_context_env=lambda env, _gid, _aid: dict(env),
                prepare_pty_env=lambda env: dict(env),
                pty_backlog_bytes=lambda: 1024,
                write_headless_state=lambda _gid, _aid: None,
                write_pty_state=lambda _gid, _aid, _pid: None,
                clear_preamble_sent=lambda _group, _aid: None,
                throttle_reset_actor=lambda _gid, _aid: None,
                supported_runtimes=("codex",),
            )

        self.assertFalse(bool(result.get("success")))
        self.assertIn("not in PATH", str(result.get("error") or ""))

    def test_codex_actor_start_injects_skill_package_overlay_codex_home(self) -> None:
        from cccc.daemon.actors import actor_runtime_ops
        from cccc.daemon.ops import capability_ops as ops

        old_home = os.environ.get("CCCC_HOME")
        old_codex_home = os.environ.get("CODEX_HOME")
        try:
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                source_codex = Path(td) / "source-codex"
                source_codex.mkdir()
                (source_codex / "auth.json").write_text('{"ok":true}\n', encoding="utf-8")
                (source_codex / "config.toml").write_text("[mcp_servers]\n", encoding="utf-8")
                os.environ["CODEX_HOME"] = str(source_codex)

                skill_root = Path(td) / "installed-skill"
                skill_root.mkdir()
                (skill_root / "SKILL.md").write_text("Use mounted package.\n", encoding="utf-8")
                state_path = Path(td) / "state" / "capabilities" / "skill_packages" / "install_state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps(
                        {
                            "v": 1,
                            "packages": {
                                "skill:onecolleague:demo": {
                                    "capability_id": "skill:onecolleague:demo",
                                    "skill_slug": "demo",
                                    "package_sha256": "a" * 64,
                                    "extracted_path": str(skill_root),
                                    "state": "installed",
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                catalog_path, catalog_doc = ops._load_catalog_doc()
                catalog_doc["records"]["skill:onecolleague:demo"] = {
                    "capability_id": "skill:onecolleague:demo",
                    "kind": "skill",
                    "name": "demo",
                    "source_id": "onecolleague_skill_library",
                    "qualification_status": "qualified",
                    "enable_supported": True,
                    "capsule_text": "Use mounted package.",
                    "install_mode": "codex_skill_package",
                    "install_spec": {
                        "package_url": "http://skills.local/demo.zip",
                        "package_sha256": "a" * 64,
                        "package_size": 10,
                        "package_format": "zip",
                        "skill_slug": "demo",
                    },
                }
                ops._save_catalog_doc(catalog_path, catalog_doc)

                group = SimpleNamespace(
                    group_id="g1",
                    doc={
                        "active_scope_key": "scope1",
                        "actors": [
                            {
                                "id": "peer1",
                                "default_scope_key": "scope1",
                                "runner": "headless",
                                "runtime": "codex",
                                "command": ["codex"],
                                "env": {},
                                "capability_autoload": ["skill:onecolleague:demo"],
                            }
                        ],
                    },
                    save=lambda: None,
                    ledger_path=str(Path(td) / "ledger.jsonl"),
                )
                captured: dict[str, object] = {}

                def _start_actor(**kwargs: object) -> None:
                    captured.update(kwargs)

                with patch.object(actor_runtime_ops.codex_app_supervisor, "start_actor", side_effect=_start_actor), patch.object(
                    actor_runtime_ops,
                    "append_event",
                    return_value={"id": "evt1"},
                ), patch.object(actor_runtime_ops, "publish_event", create=True), patch.object(
                    actor_runtime_ops,
                    "request_pet_review",
                ):
                    result = actor_runtime_ops.start_actor_process(
                        group,
                        "peer1",
                        command=["codex"],
                        env={},
                        runner="headless",
                        runtime="codex",
                        by="user",
                        find_scope_url=lambda _group, _scope_key: td,
                        effective_runner_kind=lambda runner: runner,
                        merge_actor_env_with_private=lambda _gid, _aid, env: dict(env),
                        normalize_runtime_command=lambda _runtime, command: list(command),
                        ensure_mcp_installed=lambda _runtime, _cwd, **_kwargs: True,
                        inject_actor_context_env=lambda env, gid, aid: {**dict(env), "CCCC_GROUP_ID": gid, "CCCC_ACTOR_ID": aid},
                        prepare_pty_env=lambda env: dict(env),
                        pty_backlog_bytes=lambda: 1024,
                        write_headless_state=lambda _gid, _aid: None,
                        write_pty_state=lambda _gid, _aid, _pid: None,
                        clear_preamble_sent=lambda _group, _aid: None,
                        throttle_reset_actor=lambda _gid, _aid: None,
                        supported_runtimes=("codex",),
                    )

                self.assertTrue(bool(result.get("success")), result)
                env = captured.get("env") if isinstance(captured.get("env"), dict) else {}
                overlay = Path(str(env.get("CODEX_HOME") or ""))
                self.assertTrue((overlay / "skills" / "demo" / "SKILL.md").is_file())
                self.assertFalse((source_codex / "skills" / "demo").exists())
        finally:
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home
            if old_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_codex_home

    def test_windows_pty_does_not_fallback_to_spawn_without_env(self) -> None:
        from cccc.runners import pty_win

        with tempfile.TemporaryDirectory() as td:
            spawn_calls: list[dict[str, object]] = []

            def _spawn(_cmdline: str, **kwargs: object) -> object:
                spawn_calls.append(dict(kwargs))
                raise TypeError("spawn signature mismatch")

            fake_proc = SimpleNamespace(spawn=_spawn)
            with patch.object(pty_win, "PTY_SUPPORTED", True), patch.object(pty_win, "_WINPTY_PROCESS", fake_proc):
                with self.assertRaisesRegex(RuntimeError, "environment forwarding"):
                    pty_win.PtySession(
                        group_id="g1",
                        actor_id="peer1",
                        cwd=Path(td),
                        command=["codex"],
                        env={"CCCC_HOME": td, "CCCC_GROUP_ID": "g1", "CCCC_ACTOR_ID": "peer1"},
                    )

            self.assertEqual(len(spawn_calls), 2)
            self.assertTrue(all("env" in call for call in spawn_calls))

    def test_windows_pty_stop_uses_tree_termination(self) -> None:
        from cccc.runners import pty_win

        session = object.__new__(pty_win.PtySession)
        session._running = True
        session._proc = SimpleNamespace(
            pid=4321,
            isalive=lambda: False,
            exitstatus=0,
            terminate=lambda *args, **kwargs: None,
            kill=lambda *args, **kwargs: None,
            close=lambda *args, **kwargs: None,
        )
        session._notify_wake = lambda: None
        session._thread = SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None)
        session._reader_thread = SimpleNamespace(is_alive=lambda: False, join=lambda timeout=None: None)

        with patch.object(pty_win, "terminate_pid", return_value=True) as mock_terminate:
            session.stop()

        mock_terminate.assert_called_once_with(4321, timeout_s=1.0, include_group=True, force=True)

    def test_codex_windows_command_still_gets_env_inherit_flag(self) -> None:
        from cccc.daemon import server as daemon_server

        cmd = daemon_server._normalize_runtime_command("codex", [r"C:\Tools\codex.cmd", "--search"])

        self.assertEqual(cmd[0], r"C:\Tools\codex.cmd")
        self.assertEqual(cmd[1:3], ["-c", "shell_environment_policy.inherit=all"])
        self.assertEqual(cmd[3:], ["--search"])


if __name__ == "__main__":
    unittest.main()
