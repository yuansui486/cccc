import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


class TestOpenClawRuntime(unittest.TestCase):
    def setUp(self) -> None:
        from no1.daemon import openclaw_runtime

        openclaw_runtime._GATEWAY_PROCESSES.clear()
        openclaw_runtime._GATEWAY_ACTORS.clear()
        openclaw_runtime._GATEWAY_ACTOR_ENVS.clear()
        for timer in openclaw_runtime._GATEWAY_IDLE_TIMERS.values():
            timer.cancel()
        openclaw_runtime._GATEWAY_IDLE_TIMERS.clear()
        openclaw_runtime._GATEWAY_PORT_RESERVATIONS.clear()
        openclaw_runtime._PREPARED.clear()
        openclaw_runtime._MODEL_CACHE.clear()
        openclaw_runtime._INITIALIZED_CONTEXTS.clear()

    def test_actor_mcp_and_session_names_are_stable_and_onecolleague_scoped(self) -> None:
        from no1.daemon.openclaw_runtime import (
            openclaw_agent_id,
            openclaw_mcp_server_name,
            openclaw_session_key,
        )

        agent_id = openclaw_agent_id("group-a", "actor-a")
        self.assertEqual(agent_id, openclaw_agent_id("group-a", "actor-a"))
        self.assertTrue(agent_id.startswith("onecolleague-"))
        self.assertLessEqual(len(agent_id), 30)
        self.assertEqual(openclaw_mcp_server_name("group-a", "actor-a"), agent_id)
        session = openclaw_session_key("group-a", "actor-a", Path("project-a"))
        self.assertTrue(session.startswith(f"agent:{agent_id}:onecolleague-"))
        self.assertNotEqual(session, openclaw_session_key("group-a", "actor-a", Path("project-b")))
        self.assertNotEqual(agent_id, openclaw_agent_id("group-a", "actor-b"))

    def test_background_openclaw_commands_use_windowless_process_flags(self) -> None:
        from no1.daemon import openclaw_runtime

        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(
            openclaw_runtime,
            "windowless_subprocess_popen_kwargs",
            return_value={"creationflags": 0x08000000},
        ), patch.object(openclaw_runtime.subprocess, "run", return_value=completed) as run:
            openclaw_runtime._run_cli(["openclaw"], ["config", "file"])

        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
        with patch.object(
            openclaw_runtime,
            "windowless_subprocess_popen_kwargs",
            return_value={"creationflags": 0x08000000},
        ), patch.object(
            openclaw_runtime,
            "supervised_process_popen_kwargs",
            return_value={"creationflags": 0x208},
        ):
            self.assertEqual(openclaw_runtime._gateway_popen_kwargs(), {"creationflags": 0x08000000})

    def test_switching_runtime_to_openclaw_replaces_an_incompatible_command(self) -> None:
        from no1.kernel.actors import update_actor

        group = SimpleNamespace(
            doc={
                "actors": [{
                    "id": "actor-a",
                    "runtime": "codex",
                    "runner": "pty",
                    "command": ["codex", "--search"],
                }],
            },
            save=Mock(),
        )

        actor = update_actor(group, "actor-a", {"runtime": "openclaw"})

        self.assertEqual(actor["runner"], "pty")
        self.assertEqual(actor["runtime_state_source"], "terminal")
        self.assertEqual(actor["command"], ["openclaw", "tui"])

    def test_switching_runtime_to_openclaw_preserves_an_explicit_command(self) -> None:
        from no1.kernel.actors import update_actor

        group = SimpleNamespace(
            doc={
                "actors": [{
                    "id": "actor-a",
                    "runtime": "codex",
                    "runner": "pty",
                    "command": ["codex"],
                }],
            },
            save=Mock(),
        )

        actor = update_actor(
            group,
            "actor-a",
            {"runtime": "openclaw", "command": ["openclaw", "--profile", "work", "tui"]},
        )

        self.assertEqual(actor["command"], ["openclaw", "--profile", "work", "tui"])

    def test_tui_command_rejects_overrides_and_forces_managed_connection(self) -> None:
        from no1.daemon.openclaw_runtime import build_openclaw_tui_command, openclaw_session_key

        for invalid in (
            ["openclaw", "tui", "--local"],
            ["openclaw", "chat"],
            ["openclaw", "--container", "sandbox", "tui"],
            ["openclaw", "--dev", "--profile", "work", "tui"],
            ["openclaw", "--profile", "work", "tui"],
            ["openclaw", "--dev", "tui"],
            ["openclaw", "--no-color", "tui"],
            ["openclaw", "--log-level", "debug", "tui"],
        ):
            with self.assertRaises(ValueError):
                build_openclaw_tui_command(invalid, group_id="group-a", actor_id="actor-a")
        command = build_openclaw_tui_command(
            ["openclaw", "terminal", "--thinking", "high", "--history-limit=50"],
            group_id="group-a",
            actor_id="actor-a",
            workspace=Path("project-a"),
        )
        self.assertEqual(command[:4], ["openclaw", "--log-level", "error", "tui"])
        self.assertNotIn("--url", command)
        self.assertNotIn("--token", command)
        self.assertNotIn("ws://127.0.0.1:24128", command)
        self.assertNotIn("managed-gateway-token", command)
        self.assertIn("--log-level", command)
        self.assertEqual(command[command.index("--log-level") + 1], "error")
        self.assertEqual(command[-1], openclaw_session_key("group-a", "actor-a", Path("project-a")))

    def test_windows_openclaw_cmd_resolves_to_direct_node_entry(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shim = root / "openclaw.cmd"
            node = root / "node.exe"
            entry = root / "node_modules" / "openclaw" / "openclaw.mjs"
            entry.parent.mkdir(parents=True)
            shim.write_text("@echo off\r\n", encoding="ascii")
            node.write_bytes(b"")
            entry.write_text("", encoding="ascii")
            with patch.object(openclaw_runtime.os, "name", "nt"), patch.object(
                openclaw_runtime,
                "resolve_subprocess_argv",
                return_value=[str(shim), "tui", "--session", "agent:test"],
            ):
                resolved = openclaw_runtime._resolve_openclaw_process_argv(
                    ["openclaw", "tui", "--session", "agent:test"]
                )

        self.assertEqual(resolved, [str(node.resolve()), str(entry.resolve()), "tui", "--session", "agent:test"])

    def test_strict_managed_config_read_preserves_malformed_file(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "openclaw.json"
            malformed = '{"agents": '
            config_path.write_text(malformed, encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "failed to read managed OpenClaw config"):
                openclaw_runtime._read_managed_config_strict({"OPENCLAW_CONFIG_PATH": str(config_path)})

            self.assertEqual(config_path.read_text(encoding="utf-8"), malformed)

    def test_model_catalog_reuses_opencode_models_and_is_cached_per_context(self) -> None:
        from no1.daemon import openclaw_runtime

        catalogs = [
            [{"model": "model-a", "locked": True}],
            [{"model": "model-b", "locked": False}],
        ]
        with patch.object(openclaw_runtime, "get_opencode_model_catalog", side_effect=catalogs) as get_catalog, patch.object(
            openclaw_runtime,
            "_run_cli",
        ) as run_cli:
            first = openclaw_runtime.list_openclaw_models(
                env={"OPENCLAW_CONFIG_PATH": "C:/profiles/a.json", "OPENCLAW_STATE_DIR": "C:/state/a"}
            )
            repeated = openclaw_runtime.list_openclaw_models(
                env={"OPENCLAW_CONFIG_PATH": "C:/profiles/a.json", "OPENCLAW_STATE_DIR": "C:/state/a"}
            )
            second = openclaw_runtime.list_openclaw_models(
                env={"OPENCLAW_CONFIG_PATH": "C:/profiles/b.json", "OPENCLAW_STATE_DIR": "C:/state/b"}
            )

        self.assertEqual(first, [{
            "key": "model-a",
            "name": "model-a",
            "input": "text",
            "contextWindow": 0,
            "tags": [],
            "available": True,
            "locked": True,
        }])
        self.assertEqual(repeated, first)
        self.assertEqual(second[0]["key"], "model-b")
        self.assertTrue(second[0]["available"])
        self.assertEqual(get_catalog.call_count, 2)
        run_cli.assert_not_called()

    def test_model_cache_isolated_by_onecolleague_auth_environment(self) -> None:
        from no1.daemon import openclaw_runtime

        catalogs = [
            [{"model": "model-a"}],
            [{"model": "model-b"}],
        ]
        common = {"OPENCLAW_CONFIG_PATH": "C:/profiles/a.json", "OPENCLAW_STATE_DIR": "C:/state/a"}
        with patch.object(openclaw_runtime, "get_opencode_model_catalog", side_effect=catalogs) as get_catalog, patch.object(
            openclaw_runtime,
            "_run_cli",
        ) as run_cli:
            first = openclaw_runtime.list_openclaw_model_ids(env={**common, "ONECOLLEAGUE_API_KEY": "key-a"})
            second = openclaw_runtime.list_openclaw_model_ids(env={**common, "ONECOLLEAGUE_API_KEY": "key-b"})

        self.assertEqual(first, ["model-a"])
        self.assertEqual(second, ["model-b"])
        self.assertEqual(get_catalog.call_count, 2)
        run_cli.assert_not_called()

    def test_environment_fingerprint_ignores_actor_only_values_but_tracks_config_dependencies(self) -> None:
        from no1.daemon import openclaw_runtime

        first = openclaw_runtime._environment_fingerprint({"ACTOR_ONLY": "first"})
        second = openclaw_runtime._environment_fingerprint({"ACTOR_ONLY": "second"})
        referenced_first = openclaw_runtime._environment_fingerprint(
            {"CUSTOM_SECRET": "first"},
            referenced_keys={"CUSTOM_SECRET"},
        )
        referenced_second = openclaw_runtime._environment_fingerprint(
            {"CUSTOM_SECRET": "second"},
            referenced_keys={"CUSTOM_SECRET"},
        )

        self.assertEqual(first, second)
        self.assertNotEqual(referenced_first, referenced_second)

    def test_model_resolution_qualifies_onecolleague_models_once(self) -> None:
        from no1.daemon import openclaw_runtime

        self.assertEqual(openclaw_runtime._resolve_model([], {}, "gpt-5.5"), "onecolleague/gpt-5.5")
        self.assertEqual(
            openclaw_runtime._resolve_model([], {}, "onecolleague/gpt-5.5"),
            "onecolleague/gpt-5.5",
        )
        self.assertEqual(
            openclaw_runtime._resolve_model([], {}, "vendor/model-with-slash"),
            "onecolleague/vendor/model-with-slash",
        )
        self.assertEqual(openclaw_runtime._resolve_model([], {}, ""), "")

    def test_managed_provider_preserves_other_providers_and_appends_custom_model(self) -> None:
        from no1.daemon import openclaw_runtime

        result = openclaw_runtime._merge_managed_openclaw_provider(
            {
                "models": {
                    "providers": {
                        "demo": {"baseUrl": "https://demo.invalid/v1"},
                        "onecolleague": {"headers": {"X-Stale": "value"}, "apiKey": "plaintext"},
                    },
                },
            },
            env={"ONECOLLEAGUE_OPENCODE_BASE_URL": "https://peer.example/v1"},
            selected_model="onecolleague/custom/model",
            model_ids=["gpt-5.5"],
        )

        providers = result["models"]["providers"]
        self.assertEqual(providers["demo"], {"baseUrl": "https://demo.invalid/v1"})
        self.assertEqual(providers["onecolleague"], {
            "baseUrl": "https://peer.example/v1",
            "api": "openai-completions",
            "apiKey": {"source": "env", "provider": "default", "id": "ONECOLLEAGUE_API_KEY"},
            "models": [
                {"id": "gpt-5.5", "name": "gpt-5.5", "input": ["text"]},
                {"id": "custom/model", "name": "custom/model", "input": ["text"]},
            ],
        })
        self.assertNotIn("plaintext", json.dumps(result))

    def test_managed_context_copies_user_config_and_disables_background_features(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source_config = root / "user-openclaw.json"
            source_config.write_text(
                """
                {
                  models: {
                    mode: 'replace',
                    providers: {
                      demo: { baseUrl: 'https://demo.invalid/v1' },
                      onecolleague: { apiKey: 'user-secret', headers: { 'X-Stale': 'value' } },
                    },
                  },
                  channels: { discord: { enabled: true } },
                  agents: {
                    list: [{
                      id: 'main',
                      default: true,
                      workspace: './user-workspace',
                      agentDir: './user-agent',
                    }],
                  },
                }
                """,
                encoding="utf-8",
            )
            (root / "user-state" / "skills").mkdir(parents=True)
            context = openclaw_runtime._ManagedContext(
                context_id="context-a",
                root=root / "managed",
                config_path=root / "managed" / "openclaw.json",
                state_dir=root / "managed" / "state",
                gateway_port=24128,
                gateway_token="managed-gateway-token",
                source_config_path=source_config,
                source_state_dir=root / "user-state",
                group_id="group-a",
                actor_id="actor-a",
                agent_id=openclaw_runtime.openclaw_agent_id("group-a", "actor-a"),
            )
            source_env = {
                "CCCC_HOME": str(root / "cccc-home"),
                "OPENCLAW_GATEWAY_PASSWORD": "user-password",
                "ONECOLLEAGUE_API_KEY": "actual-secret",
            }
            with patch.object(
                openclaw_runtime,
                "load_opencode_model_catalog",
                return_value=[{"model": "gpt-5.5", "locked": False}],
            ), patch.object(openclaw_runtime, "_publish_config") as publish_config:
                managed_env = openclaw_runtime._initialize_managed_context(
                    ["openclaw"],
                    source_env=source_env,
                    context=context,
                )

            self.assertEqual(managed_env["OPENCLAW_CONFIG_PATH"], str(context.config_path))
            self.assertEqual(managed_env["OPENCLAW_STATE_DIR"], str(context.state_dir))
            self.assertEqual(managed_env["OPENCLAW_GATEWAY_PORT"], "24128")
            self.assertEqual(managed_env["OPENCLAW_GATEWAY_TOKEN"], "managed-gateway-token")
            self.assertEqual(managed_env["OPENCLAW_GATEWAY_PASSWORD"], "")
            with patch.dict("os.environ", {"OPENCLAW_GATEWAY_PASSWORD": "global-password"}, clear=False):
                self.assertEqual(openclaw_runtime._openclaw_env(managed_env)["OPENCLAW_GATEWAY_PASSWORD"], "")
            payload = publish_config.call_args.args[1]
            self.assertTrue(payload["agents"]["defaults"]["skipBootstrap"])
            self.assertEqual(payload["agents"]["defaults"]["heartbeat"]["every"], "0m")
            self.assertEqual(
                payload["agents"]["list"],
                [{
                    "id": "main",
                    "default": True,
                    "agentDir": str(root / "user-state" / "agents" / "main" / "agent"),
                }],
            )
            self.assertEqual(payload["skills"]["load"]["extraDirs"], [str((root / "user-state" / "skills").resolve())])
            self.assertFalse(payload["cron"]["enabled"])
            self.assertFalse(payload["hooks"]["enabled"])
            self.assertNotIn("channels", payload)
            self.assertEqual(payload["gateway"]["bind"], "loopback")
            self.assertEqual(
                payload["gateway"]["auth"],
                {"mode": "token", "token": "${OPENCLAW_GATEWAY_TOKEN}"},
            )
            self.assertEqual(payload["models"]["mode"], "merge")
            self.assertEqual(payload["models"]["providers"]["demo"]["baseUrl"], "https://demo.invalid/v1")
            self.assertEqual(payload["models"]["providers"]["onecolleague"], {
                "baseUrl": "https://peer.shierkeji.com/v1",
                "api": "openai-completions",
                "apiKey": {"source": "env", "provider": "default", "id": "ONECOLLEAGUE_API_KEY"},
                "models": [{"id": "gpt-5.5", "name": "gpt-5.5", "input": ["text"]}],
            })
            self.assertNotIn("actual-secret", json.dumps(payload))
            self.assertNotIn("user-secret", json.dumps(payload))

    def test_prepare_provisions_actor_and_patches_session_model(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            context = openclaw_runtime._ManagedContext(
                context_id="context-a",
                root=root / "managed",
                config_path=root / "managed" / "openclaw.json",
                state_dir=root / "managed" / "state",
                gateway_port=24128,
                gateway_token="managed-gateway-token",
            )
            env = {
                "CCCC_HOME": str(root / "cccc-home"),
                "OPENCLAW_SELECTED_MODEL": "model-a",
            }
            with patch.object(openclaw_runtime, "_managed_context", return_value=context), patch.object(
                openclaw_runtime,
                "_initialize_managed_context",
                return_value={
                    **env,
                    "OPENCLAW_CONFIG_PATH": str(context.config_path),
                    "OPENCLAW_STATE_DIR": str(context.state_dir),
                    "OPENCLAW_GATEWAY_PORT": str(context.gateway_port),
                    "OPENCLAW_GATEWAY_TOKEN": context.gateway_token,
                },
            ), patch.object(openclaw_runtime, "_resolve_model", return_value="onecolleague/model-a"), patch.object(
                openclaw_runtime,
                "_skill_projection",
                return_value={
                    "root": "",
                    "managed_root": str(root / "skills"),
                    "selected_names": [],
                    "managed_names": [],
                    "fingerprint": "empty",
                },
            ), patch.object(
                openclaw_runtime,
                "_mcp_server_config",
                return_value={"command": "onecolleague", "args": ["mcp"]},
            ), patch.object(
                openclaw_runtime,
                "_read_managed_config_strict",
                return_value={
                    "agents": {"list": [{"id": "main", "default": True}]},
                    "mcp": {"servers": {}},
                    "skills": {"load": {"extraDirs": [str(root / "skills" / "old")] }},
                },
            ), patch.object(openclaw_runtime, "_publish_config") as publish_config, patch.object(
                openclaw_runtime,
                "_list_candidate_skill_names",
                return_value=[],
            ) as list_candidate_skills, patch.object(openclaw_runtime, "_gateway_ready", return_value=False), patch.object(
                openclaw_runtime,
                "_start_gateway",
            ) as start_gateway, patch.object(openclaw_runtime, "_patch_session_model") as patch_session:
                publish_config.side_effect = lambda _prefix, payload, **_kwargs: context.config_path.write_text(
                    json.dumps(payload),
                    encoding="utf-8",
                )
                command = openclaw_runtime.prepare_openclaw_actor_runtime(
                    group_id="group-a",
                    actor_id="actor-a",
                    cwd=root / "project",
                    command=["openclaw"],
                    env=env,
                )
                payload = publish_config.call_args.args[1]
                first_gateway_call = start_gateway.call_args
                openclaw_runtime._PREPARED.clear()
                openclaw_runtime._INITIALIZED_CONTEXTS.clear()
                publish_config.reset_mock()
                list_candidate_skills.reset_mock()
                start_gateway.reset_mock()
                patch_session.reset_mock()
                repeated = openclaw_runtime.prepare_openclaw_actor_runtime(
                    group_id="group-a",
                    actor_id="actor-a",
                    cwd=root / "project",
                    command=["openclaw"],
                    env=env,
                )

            self.assertEqual(env["OPENCLAW_CONFIG_PATH"], str(context.config_path))
            self.assertEqual(command[-1], openclaw_runtime.openclaw_session_key("group-a", "actor-a", root / "project"))
            self.assertEqual(repeated, command)
            self.assertFalse(first_gateway_call.kwargs["authenticated_ready"])
            publish_config.assert_not_called()
            list_candidate_skills.assert_not_called()
            managed_agent = next(item for item in payload["agents"]["list"] if item["id"].startswith("onecolleague-"))
            self.assertEqual(managed_agent["workspace"], str((root / "project").resolve()))
            self.assertEqual(managed_agent["agentDir"], str(context.root / "agents" / managed_agent["id"]))
            self.assertEqual(managed_agent["model"], "onecolleague/model-a")
            self.assertIn(
                {"id": "model-a", "name": "model-a", "input": ["text"]},
                payload["models"]["providers"]["onecolleague"]["models"],
            )
            self.assertEqual(payload["skills"]["load"]["extraDirs"], [])
            start_gateway.assert_called_once_with(
                ["openclaw"],
                env=env,
                cwd=context.root,
                group_id="group-a",
                actor_id="actor-a",
            )
            patch_session.assert_called_once_with(
                ["openclaw"],
                session_key=openclaw_runtime.openclaw_session_key("group-a", "actor-a", root / "project"),
                model="onecolleague/model-a",
                env=env,
            )

    def test_gateway_start_timeout_terminates_process_tree_and_clears_tracking(self) -> None:
        from no1.daemon import openclaw_runtime

        process = SimpleNamespace(pid=1234, poll=lambda: None, wait=lambda timeout: None, kill=lambda: None)
        with tempfile.TemporaryDirectory() as td, patch.object(
            openclaw_runtime,
            "_gateway_ready",
            return_value=False,
        ), patch.object(openclaw_runtime.subprocess, "Popen", return_value=process), patch.object(
            openclaw_runtime.time,
            "monotonic",
            side_effect=[0.0, 21.0],
        ), patch.object(openclaw_runtime, "terminate_pid", return_value=True) as terminate_pid:
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                openclaw_runtime._start_gateway(
                    ["openclaw"],
                    env={
                        "CCCC_HOME": td,
                        "OPENCLAW_CONFIG_PATH": str(Path(td) / "openclaw.json"),
                        "OPENCLAW_GATEWAY_PORT": "24128",
                        "OPENCLAW_GATEWAY_TOKEN": "token",
                    },
                    cwd=Path(td),
                    group_id="group-a",
                    actor_id="actor-a",
                )

        terminate_pid.assert_called_once_with(1234, timeout_s=5.0, include_group=True, force=True)
        self.assertEqual(openclaw_runtime._GATEWAY_PROCESSES, {})
        self.assertEqual(openclaw_runtime._GATEWAY_ACTORS, {})

    def test_gateway_termination_waits_after_kill_fallback(self) -> None:
        from no1.daemon import openclaw_runtime

        process = SimpleNamespace(
            pid=1234,
            poll=lambda: None,
            wait=Mock(side_effect=[subprocess.TimeoutExpired("openclaw", 1.0), None]),
            kill=Mock(),
        )
        with patch.object(openclaw_runtime, "terminate_pid", return_value=False):
            openclaw_runtime._terminate_gateway_process(process)

        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)

    def test_managed_context_reuses_persisted_gateway_token(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = {
                "CCCC_HOME": td,
                "OPENCLAW_CONFIG_PATH": str(home / "source" / "openclaw.json"),
                "OPENCLAW_STATE_DIR": str(home / "source-state"),
            }
            first = openclaw_runtime._managed_context(
                ["openclaw"],
                env,
                group_id="group-a",
                actor_id="actor-a",
            )
            first.config_path.parent.mkdir(parents=True, exist_ok=True)
            first.config_path.write_text(
                json.dumps({"gateway": {"auth": {"mode": "token", "token": first.gateway_token}}}),
                encoding="utf-8",
            )
            repeated = openclaw_runtime._managed_context(
                ["openclaw"],
                env,
                group_id="group-a",
                actor_id="actor-a",
            )
            other = openclaw_runtime._managed_context(
                ["openclaw"],
                env,
                group_id="group-a",
                actor_id="actor-b",
            )

        self.assertEqual(repeated.gateway_token, first.gateway_token)
        self.assertEqual(other.context_id, first.context_id)
        self.assertEqual(other.gateway_token, first.gateway_token)
        self.assertEqual(
            first.root,
            home / "runtime" / "openclaw" / "contexts" / first.context_id,
        )
        self.assertEqual(first.state_dir, first.root / "state")
        self.assertEqual(first.source_state_dir, home / "source-state")
        self.assertEqual(other.state_dir, other.root / "state")
        self.assertEqual(other.state_dir, first.state_dir)
        self.assertEqual(other.root, first.root)

    def test_managed_context_reserves_explicit_gateway_port_per_actor(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            env = {
                "CCCC_HOME": td,
                "OPENCLAW_CONFIG_PATH": str(home / "source" / "openclaw.json"),
                "OPENCLAW_STATE_DIR": str(home / "source-state"),
                "CCCC_OPENCLAW_GATEWAY_PORT": "62000",
            }
            with patch.object(openclaw_runtime, "_ports_available", return_value=True):
                first = openclaw_runtime._managed_context(
                    ["openclaw"],
                    env,
                    group_id="group-a",
                    actor_id="actor-a",
                )
                second = openclaw_runtime._managed_context(
                    ["openclaw"],
                    env,
                    group_id="group-a",
                    actor_id="actor-b",
                )

        self.assertEqual(first.gateway_port, 62000)
        self.assertEqual(second.gateway_port, first.gateway_port)

    def test_session_model_failure_stops_started_gateway(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            context = openclaw_runtime._ManagedContext(
                context_id="context-a",
                root=root / "managed",
                config_path=root / "managed" / "openclaw.json",
                state_dir=root / "managed" / "state",
                gateway_port=24128,
                gateway_token="managed-gateway-token",
            )
            env = {"CCCC_HOME": str(root / "cccc-home")}
            managed_env = {
                **env,
                "OPENCLAW_CONFIG_PATH": str(context.config_path),
                "OPENCLAW_STATE_DIR": str(context.state_dir),
                "OPENCLAW_GATEWAY_PORT": str(context.gateway_port),
                "OPENCLAW_GATEWAY_TOKEN": context.gateway_token,
            }
            with patch.object(openclaw_runtime, "_managed_context", return_value=context), patch.object(
                openclaw_runtime,
                "_initialize_managed_context",
                return_value=managed_env,
            ), patch.object(openclaw_runtime, "_resolve_model", return_value="onecolleague/model-a"), patch.object(
                openclaw_runtime,
                "_skill_projection",
                return_value={"root": "", "managed_root": "", "selected_names": [], "managed_names": []},
            ), patch.object(openclaw_runtime, "_configured_agents", return_value=[]), patch.object(
                openclaw_runtime,
                "_configured_mcp_servers",
                return_value={},
            ), patch.object(openclaw_runtime, "_configured_extra_skill_dirs", return_value=[]), patch.object(
                openclaw_runtime,
                "_mcp_server_config",
                return_value={"command": "onecolleague", "args": ["mcp"]},
            ), patch.object(openclaw_runtime, "_publish_config"), patch.object(
                openclaw_runtime,
                "_list_candidate_skill_names",
                return_value=[],
            ), patch.object(openclaw_runtime, "_gateway_ready", return_value=False), patch.object(
                openclaw_runtime,
                "_start_gateway",
            ), patch.object(
                openclaw_runtime,
                "_patch_session_model",
                side_effect=RuntimeError("session patch failed"),
            ), patch.object(openclaw_runtime, "stop_openclaw_actor_gateway") as stop_gateway:
                with self.assertRaisesRegex(RuntimeError, "session patch failed"):
                    openclaw_runtime.prepare_openclaw_actor_runtime(
                        group_id="group-a",
                        actor_id="actor-a",
                        cwd=root / "project",
                        command=["openclaw"],
                        env=env,
                    )

            stop_gateway.assert_called_once_with("group-a", "actor-a")

    def test_session_model_patch_uses_gateway_rpc(self) -> None:
        from no1.daemon import openclaw_runtime

        with patch.object(openclaw_runtime, "_gateway_rpc", return_value={}) as gateway_rpc:
            openclaw_runtime._patch_session_model(
                ["openclaw"],
                session_key="agent:onecolleague-a:onecolleague",
                model="onecolleague/model-a",
                env={},
            )

        gateway_rpc.assert_called_once_with(
            ["openclaw"],
            "sessions.patch",
            {"key": "agent:onecolleague-a:onecolleague", "model": "onecolleague/model-a"},
            env={},
            timeout=20.0,
        )

    def test_session_model_patch_does_not_mutate_session_store_when_rpc_fails(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            state_dir = Path(td)
            agent_id = "onecolleague-test"
            session_key = f"agent:{agent_id}:onecolleague-workspace"
            sessions_path = state_dir / "agents" / agent_id / "sessions" / "sessions.json"
            sessions_path.parent.mkdir(parents=True)
            sessions_path.write_text(json.dumps({session_key: {"label": "kept"}}), encoding="utf-8")
            with patch.object(
                openclaw_runtime,
                "_gateway_rpc",
                side_effect=RuntimeError("missing scope: operator.admin"),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to select OpenClaw model"):
                    openclaw_runtime._patch_session_model(
                        ["openclaw"],
                        session_key=session_key,
                        model="onecolleague/model-a",
                        env={"OPENCLAW_STATE_DIR": str(state_dir)},
                    )

            unchanged = json.loads(sessions_path.read_text(encoding="utf-8"))[session_key]
            self.assertEqual(unchanged, {"label": "kept"})

    def test_stopping_one_actor_keeps_shared_gateway_for_other_actor(self) -> None:
        from no1.daemon import openclaw_runtime

        key = ("openclaw", "OPENCLAW_CONFIG_PATH", "shared")
        process = Mock(pid=1234, poll=Mock(return_value=None))
        openclaw_runtime._GATEWAY_PROCESSES[key] = process
        openclaw_runtime._GATEWAY_ACTORS[("group-a", "actor-a")] = key
        openclaw_runtime._GATEWAY_ACTORS[("group-a", "actor-b")] = key
        openclaw_runtime._GATEWAY_ACTOR_ENVS[("group-a", "actor-a")] = {}
        openclaw_runtime._GATEWAY_ACTOR_ENVS[("group-a", "actor-b")] = {}

        with patch.object(openclaw_runtime, "_terminate_gateway_process") as terminate, patch.object(
            openclaw_runtime,
            "_terminate_owned_gateway",
        ) as terminate_owned:
            openclaw_runtime.stop_openclaw_actor_gateway("group-a", "actor-a")

        self.assertIs(openclaw_runtime._GATEWAY_PROCESSES[key], process)
        self.assertEqual(openclaw_runtime._GATEWAY_ACTORS[("group-a", "actor-b")], key)
        terminate.assert_not_called()
        terminate_owned.assert_not_called()

    def test_gateway_rpc_uses_official_plugin_sdk_backend_shared_auth(self) -> None:
        from no1.daemon import openclaw_runtime

        completed = subprocess.CompletedProcess([], 0, stdout='{"ok":true}', stderr="")
        env = {
            "OPENCLAW_GATEWAY_PORT": "24128",
            "OPENCLAW_GATEWAY_URL": "ws://127.0.0.1:24128",
            "OPENCLAW_GATEWAY_TOKEN": "gateway-token",
        }
        sdk_path = Path("openclaw") / "dist" / "plugin-sdk" / "gateway-runtime.js"
        with patch.object(
            openclaw_runtime,
            "_openclaw_gateway_rpc_runtime",
            return_value=("node", sdk_path),
        ) as resolve_runtime, patch.object(openclaw_runtime.subprocess, "run", return_value=completed) as run_node:
            result = openclaw_runtime._gateway_rpc(
                ["openclaw", "--profile", "work"],
                "sessions.patch",
                {"key": "agent:test:session", "model": "provider/model"},
                env=env,
                timeout=7.5,
            )

        self.assertEqual(result, {"ok": True})
        resolve_runtime.assert_called_once_with(["openclaw", "--profile", "work"], env)
        run_node.assert_called_once()
        argv = run_node.call_args.args[0]
        kwargs = run_node.call_args.kwargs
        self.assertEqual(argv[:3], ["node", "--input-type=module", "--eval"])
        self.assertIn('clientName: "gateway-client"', argv[3])
        self.assertIn('mode: "backend"', argv[3])
        self.assertIn('scopes: ["operator.admin"]', argv[3])
        self.assertNotIn("gateway-token", argv)
        self.assertEqual(
            json.loads(kwargs["input"]),
            {
                "sdkPath": str(sdk_path),
                "method": "sessions.patch",
                "params": {"key": "agent:test:session", "model": "provider/model"},
                "url": "ws://127.0.0.1:24128",
                "token": "gateway-token",
                "timeoutMs": 7500,
            },
        )
        self.assertEqual(kwargs["timeout"], 12.5)

    def test_gateway_rpc_runtime_resolves_sdk_and_sibling_node(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            install_root = Path(td)
            command = install_root / "openclaw.cmd"
            command.write_text("@echo off", encoding="utf-8")
            node = install_root / "node.exe"
            node.write_bytes(b"")
            sdk = install_root / "node_modules" / "openclaw" / "dist" / "plugin-sdk" / "gateway-runtime.js"
            sdk.parent.mkdir(parents=True)
            sdk.write_text("export {};", encoding="utf-8")
            with patch.object(openclaw_runtime.shutil, "which", return_value=str(command)):
                resolved_node, resolved_sdk = openclaw_runtime._openclaw_gateway_rpc_runtime(
                    ["openclaw"],
                    {"PATH": str(install_root)},
                )

        self.assertEqual(resolved_node, str(node))
        self.assertEqual(resolved_sdk, sdk.resolve())

    def test_daemon_cleanup_stops_all_openclaw_gateways(self) -> None:
        from no1.daemon.serve_ops import cleanup_after_stop

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stop_all = Mock()
            cleanup_after_stop(
                stop_event=threading.Event(),
                home=root,
                best_effort_killpg=lambda *_args, **_kwargs: None,
                im_stop_all=lambda *_args, **_kwargs: None,
                codex_stop_all=lambda: None,
                pty_stop_all=lambda: None,
                headless_stop_all=lambda: None,
                sock_path=root / "missing.sock",
                addr_path=root / "missing.addr",
                pid_path=root / "missing.pid",
                release_lockfile=lambda _handle: None,
                lock_handle=object(),
                openclaw_stop_all=stop_all,
            )

        stop_all.assert_called_once_with()

    def test_config_publish_rolls_back_when_gateway_does_not_confirm(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "openclaw.json"
            original = {"agents": {"list": [{"id": "onecolleague-old"}]}}
            config_path.write_text(json.dumps(original), encoding="utf-8")
            env = {"OPENCLAW_CONFIG_PATH": str(config_path)}
            validated = subprocess.CompletedProcess([], 0, stdout='{"valid":true}', stderr="")
            with patch.object(openclaw_runtime, "_run_cli", return_value=validated), patch.object(
                openclaw_runtime,
                "_owned_gateway_pid",
                return_value=1234,
            ), patch.object(
                openclaw_runtime,
                "_wait_for_gateway_config_hash",
                side_effect=RuntimeError("gateway did not reload"),
            ):
                with self.assertRaisesRegex(RuntimeError, "gateway did not reload"):
                    openclaw_runtime._publish_config(
                        ["openclaw"],
                        {"agents": {"list": [{"id": "onecolleague-new"}]}},
                        env=env,
                    )

            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), original)

    def test_source_config_loads_json5_includes_without_cli_redaction_or_stale_cache(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            included = root / "shared.json5"
            source_config = root / "openclaw.json"
            included.write_text(
                "{ models: { providers: { demo: { apiKey: 'secret-a' } } }, skills: { load: { extraDirs: ['./skills'] } } }",
                encoding="utf-8",
            )
            source_config.write_text(
                "{ $include: './shared.json5', models: { mode: 'merge' } }",
                encoding="utf-8",
            )

            with patch.object(openclaw_runtime, "_run_cli") as run_cli:
                first = openclaw_runtime._load_source_config(
                    ["openclaw"],
                    source_config=source_config,
                    env={},
                )
                included.write_text(
                    "{ models: { providers: { demo: { apiKey: 'secret-b' } } }, skills: { load: { extraDirs: ['./skills'] } } }",
                    encoding="utf-8",
                )
                second = openclaw_runtime._load_source_config(
                    ["openclaw"],
                    source_config=source_config,
                    env={},
                )

            self.assertEqual(first["models"]["providers"]["demo"]["apiKey"], "secret-a")
            self.assertEqual(second["models"]["providers"]["demo"]["apiKey"], "secret-b")
            projected = openclaw_runtime._project_source_config(first, source_dir=root, env={})
            self.assertEqual(projected["skills"]["load"]["extraDirs"], [str((root / "skills").resolve())])
            run_cli.assert_not_called()

    def test_source_config_include_arrays_concatenate_like_openclaw(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "first.json5").write_text(
                "{ skills: { load: { extraDirs: ['./skills-a'] } }, tools: { deny: ['browser'] } }",
                encoding="utf-8",
            )
            (root / "second.json5").write_text(
                "{ skills: { load: { extraDirs: ['./skills-b'] } }, tools: { deny: ['exec'] } }",
                encoding="utf-8",
            )
            source_config = root / "openclaw.json"
            source_config.write_text("{ $include: ['./first.json5', './second.json5'] }", encoding="utf-8")

            loaded = openclaw_runtime._load_source_config(
                ["openclaw"],
                source_config=source_config,
                env={},
            )

            self.assertEqual(loaded["skills"]["load"]["extraDirs"], ["./skills-a", "./skills-b"])
            self.assertEqual(loaded["tools"]["deny"], ["browser", "exec"])

    def test_source_config_include_merges_sibling_arrays_like_openclaw(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "base.json5").write_text(
                "{ tools: { deny: ['browser'] }, skills: { load: { extraDirs: ['./base'] } } }",
                encoding="utf-8",
            )
            source_config = root / "openclaw.json"
            source_config.write_text(
                "{ $include: './base.json5', tools: { deny: ['exec'] }, skills: { load: { extraDirs: ['./local'] } } }",
                encoding="utf-8",
            )

            loaded = openclaw_runtime._load_source_config(
                ["openclaw"],
                source_config=source_config,
                env={},
            )

            self.assertEqual(loaded["tools"]["deny"], ["browser", "exec"])
            self.assertEqual(loaded["skills"]["load"]["extraDirs"], ["./base", "./local"])

    def test_source_config_multiple_includes_concatenate_top_level_arrays(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "first.json5").write_text("['browser']", encoding="utf-8")
            (root / "second.json5").write_text("['exec']", encoding="utf-8")
            source_config = root / "openclaw.json"
            source_config.write_text(
                "{ tools: { deny: { $include: ['./first.json5', './second.json5'] } } }",
                encoding="utf-8",
            )

            loaded = openclaw_runtime._load_source_config(
                ["openclaw"],
                source_config=source_config,
                env={},
            )

            self.assertEqual(loaded["tools"]["deny"], ["browser", "exec"])

    def test_source_config_empty_include_array_matches_openclaw(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            source_config = Path(td) / "openclaw.json"
            source_config.write_text(
                "{ $include: [], models: { mode: 'merge' } }",
                encoding="utf-8",
            )

            loaded = openclaw_runtime._load_source_config(
                ["openclaw"],
                source_config=source_config,
                env={},
            )

            self.assertEqual(loaded, {"models": {"mode": "merge"}})

    def test_source_config_null_include_is_invalid(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            source_config = Path(td) / "openclaw.json"
            source_config.write_text("{ $include: null }", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, r"invalid \$include"):
                openclaw_runtime._load_source_config(
                    ["openclaw"],
                    source_config=source_config,
                    env={},
                )

    def test_include_merge_filters_prototype_keys_like_openclaw(self) -> None:
        from no1.daemon import openclaw_runtime

        merged = openclaw_runtime._include_deep_merge(
            {"safe": {"first": True}},
            {
                "safe": {"second": True},
                "__proto__": {"polluted": True},
                "prototype": {"polluted": True},
                "constructor": {"polluted": True},
            },
        )

        self.assertEqual(merged, {"safe": {"first": True, "second": True}})

    def test_source_config_rejects_oversized_include_file(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            included = root / "oversized.json5"
            included.write_text(
                "{" + (" " * openclaw_runtime._MAX_INCLUDE_FILE_BYTES) + "}",
                encoding="utf-8",
            )
            source_config = root / "openclaw.json"
            source_config.write_text("{ $include: './oversized.json5' }", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "failed security checks"):
                openclaw_runtime._load_source_config(
                    ["openclaw"],
                    source_config=source_config,
                    env={},
                )

    def test_config_projection_rewrites_only_openclaw_owned_path_fields(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            projected = openclaw_runtime._project_source_config(
                {
                    "skills": {"load": {"extraDirs": ["./skills"]}},
                    "plugins": {
                        "load": {"paths": ["./plugins"]},
                        "entries": {"policy": {"config": {"path": "./workspace-policy.json"}}},
                    },
                    "mcp": {"servers": {"docs": {"command": "node", "cwd": "./mcp"}}},
                },
                source_dir=root,
                env={},
            )

            self.assertEqual(projected["skills"]["load"]["extraDirs"], [str((root / "skills").resolve())])
            self.assertEqual(projected["plugins"]["load"]["paths"], [str((root / "plugins").resolve())])
            self.assertEqual(projected["mcp"]["servers"]["docs"]["cwd"], str((root / "mcp").resolve()))
            self.assertEqual(
                projected["plugins"]["entries"]["policy"]["config"]["path"],
                "./workspace-policy.json",
            )

    def test_managed_context_is_shared_by_source_context(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "user-home"
            source_config = home / ".openclaw" / "openclaw.json"
            env = {"CCCC_HOME": str(root / "cccc-home"), "HOME": str(home), "USERPROFILE": str(home)}
            with patch.object(openclaw_runtime, "_active_config_path", return_value=source_config):
                first = openclaw_runtime._managed_context(
                    ["openclaw"], {**env, "ACTOR_ONLY": "first"}, group_id="group-a", actor_id="actor-a"
                )
                second = openclaw_runtime._managed_context(
                    ["openclaw"], {**env, "ACTOR_ONLY": "second"}, group_id="group-a", actor_id="actor-b"
                )

            self.assertEqual(first.context_id, second.context_id)
            self.assertEqual(first.root, second.root)
            self.assertEqual(first.source_state_dir, (home / ".openclaw").resolve())
            self.assertEqual(second.source_state_dir, first.source_state_dir)
            self.assertEqual(first.state_dir, first.root / "state")
            self.assertEqual(second.state_dir, second.root / "state")
            self.assertEqual(second.state_dir, first.state_dir)

    def test_profile_command_selects_profile_source_state(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "user-home"
            source_config = home / ".openclaw-work" / "openclaw.json"
            env = {
                "CCCC_HOME": str(root / "cccc-home"),
                "HOME": str(home),
                "USERPROFILE": str(home),
                "OPENCLAW_PROFILE": "work",
            }
            with patch.object(openclaw_runtime, "_active_config_path", return_value=source_config):
                context = openclaw_runtime._managed_context(
                    ["openclaw"],
                    env,
                    group_id="group-a",
                    actor_id="actor-a",
                )

            self.assertEqual(context.source_state_dir, (home / ".openclaw-work").resolve())
            self.assertEqual(context.state_dir, context.root / "state")

    def test_dev_profile_environment_selects_dev_source_state(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "user-home"
            source_config = home / ".openclaw-dev" / "openclaw.json"
            env = {
                "CCCC_HOME": str(root / "cccc-home"),
                "HOME": str(home),
                "USERPROFILE": str(home),
                "OPENCLAW_PROFILE": "dev",
            }
            with patch.object(openclaw_runtime, "_active_config_path", return_value=source_config):
                context = openclaw_runtime._managed_context(
                    ["openclaw"],
                    env,
                    group_id="group-a",
                    actor_id="actor-a",
                )

            self.assertEqual(context.source_state_dir, (home / ".openclaw-dev").resolve())
            self.assertEqual(context.state_dir, context.root / "state")

    def test_stop_actor_finds_owned_gateway_after_daemon_restart(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            agent_id = openclaw_runtime.openclaw_agent_id("group-a", "actor-a")
            context_root = home / "runtime" / "openclaw" / "contexts" / "context-a"
            context_root.mkdir(parents=True)
            token = "owned-token"
            (context_root / "gateway-token").write_text(token, encoding="utf-8")
            (context_root / "openclaw.json").write_text(
                json.dumps({"agents": {"list": [{"id": agent_id}]}}),
                encoding="utf-8",
            )
            (context_root / "context.json").write_text(
                json.dumps({
                    "gateway_port": 24128,
                    "state_dir": str(context_root / "state"),
                }),
                encoding="utf-8",
            )
            ownership_path = context_root / "gateway-ownership.json"
            ownership_path.write_text(
                json.dumps({
                    "pid": 1234,
                    "process_created_at": 10.0,
                    "config_path": str(context_root / "openclaw.json"),
                    "port": 24128,
                    "token_fingerprint": openclaw_runtime._token_fingerprint(token),
                }),
                encoding="utf-8",
            )
            callbacks = []

            class _Timer:
                daemon = False

                def __init__(self, _seconds, callback):
                    callbacks.append(callback)

                def start(self):
                    return None

                def cancel(self):
                    return None

            with patch.object(openclaw_runtime, "ensure_home", return_value=home), patch.object(
                openclaw_runtime,
                "pid_is_alive",
                return_value=True,
            ), patch.object(openclaw_runtime, "_process_created_at", return_value=10.0), patch.object(
                openclaw_runtime,
                "terminate_pid",
                return_value=True,
            ) as terminate_pid, patch.object(openclaw_runtime.threading, "Timer", _Timer):
                openclaw_runtime.stop_openclaw_actor_gateway("group-a", "actor-a")
                self.assertEqual(len(callbacks), 1)
                callbacks[0]()

            terminate_pid.assert_called_once_with(1234, timeout_s=5.0, include_group=True, force=True)
            self.assertFalse(ownership_path.exists())

    def test_stop_all_scans_explicit_daemon_home_after_restart(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            context_root = home / "runtime" / "openclaw" / "actors" / "onecolleague-test" / "context-a"
            context_root.mkdir(parents=True)
            token = "owned-token"
            config_path = context_root / "openclaw.json"
            config_path.write_text("{}", encoding="utf-8")
            (context_root / "gateway-token").write_text(token, encoding="utf-8")
            (context_root / "context.json").write_text(
                json.dumps({"gateway_port": 24128, "state_dir": str(context_root / "state")}),
                encoding="utf-8",
            )
            ownership_path = context_root / "gateway-ownership.json"
            ownership_path.write_text(
                json.dumps({
                    "pid": 1234,
                    "process_created_at": 10.0,
                    "config_path": str(config_path),
                    "port": 24128,
                    "token_fingerprint": openclaw_runtime._token_fingerprint(token),
                }),
                encoding="utf-8",
            )
            with patch.object(openclaw_runtime, "pid_is_alive", return_value=True), patch.object(
                openclaw_runtime,
                "_process_created_at",
                return_value=10.0,
            ), patch.object(openclaw_runtime, "terminate_pid", return_value=True) as terminate_pid:
                openclaw_runtime.stop_all_openclaw_gateways(env={"CCCC_HOME": str(home)})

            terminate_pid.assert_called_once_with(1234, timeout_s=5.0, include_group=True, force=True)
            self.assertFalse(ownership_path.exists())

    def test_explicit_runtime_home_does_not_scan_the_default_home(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            default_home = root / "default"
            explicit_home = root / "explicit"
            with patch.object(openclaw_runtime, "ensure_home", return_value=default_home):
                homes = openclaw_runtime._known_runtime_homes({"CCCC_HOME": str(explicit_home)})

            self.assertEqual(homes, {explicit_home.resolve()})

    def test_remove_actor_deletes_only_actor_managed_state(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            source_state_root = home / "user-state"
            first_id = openclaw_runtime.openclaw_agent_id("group-a", "actor-a")
            second_id = openclaw_runtime.openclaw_agent_id("group-a", "actor-b")
            first_actor_root = home / "runtime" / "openclaw" / "actors" / first_id
            second_actor_root = home / "runtime" / "openclaw" / "actors" / second_id
            first_context_root = first_actor_root / "context-a"
            second_context_root = second_actor_root / "context-b"
            skill_digest = openclaw_runtime._stable_digest("group-a", "actor-a")
            skill_root = home / "runtime" / "openclaw" / "skills" / "actors" / skill_digest
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text("---\nname: managed\n---\n", encoding="utf-8")
            for context_root, agent_id, port in (
                (first_context_root, first_id, 24128),
                (second_context_root, second_id, 24256),
            ):
                (context_root / "state" / "agents" / agent_id / "sessions").mkdir(parents=True)
                (context_root / "context.json").write_text(
                    json.dumps({
                        "command": ["openclaw"],
                        "gateway_port": port,
                        "state_dir": str(context_root / "state"),
                        "source_state": str(source_state_root),
                    }),
                    encoding="utf-8",
                )
                (context_root / "openclaw.json").write_text(
                    json.dumps({"agents": {"list": [{"id": "main"}, {"id": agent_id}]}}),
                    encoding="utf-8",
                )
            (source_state_root / "agents" / first_id / "sessions").mkdir(parents=True)
            (source_state_root / "agents" / second_id / "sessions").mkdir(parents=True)

            openclaw_runtime.remove_openclaw_actor_runtime(
                "group-a",
                "actor-a",
                env={"ONECOLLEAGUE_HOME": str(home)},
            )

            self.assertFalse(first_actor_root.exists())
            self.assertTrue(second_actor_root.is_dir())
            self.assertTrue((second_context_root / "state" / "agents" / second_id).is_dir())
            self.assertTrue((source_state_root / "agents" / first_id).is_dir())
            self.assertTrue((source_state_root / "agents" / second_id).is_dir())
            self.assertFalse(skill_root.exists())

    def test_remove_last_shared_actor_cancels_gateway_idle_timer(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            context_root = home / "runtime" / "openclaw" / "contexts" / "context-a"
            context_root.mkdir(parents=True)
            agent_id = openclaw_runtime.openclaw_agent_id("group-a", "actor-a")
            (context_root / "gateway-token").write_text("test-token", encoding="utf-8")
            (context_root / "context.json").write_text(
                json.dumps(
                    {
                        "command": ["openclaw"],
                        "gateway_port": 24128,
                        "state_dir": str(context_root / "state"),
                    }
                ),
                encoding="utf-8",
            )
            (context_root / "openclaw.json").write_text(
                json.dumps({"agents": {"list": [{"id": "main"}, {"id": agent_id}]}}),
                encoding="utf-8",
            )
            managed_env = openclaw_runtime._managed_env_from_context_root(context_root)
            key = openclaw_runtime._gateway_context_key(["openclaw"], managed_env)
            openclaw_runtime._GATEWAY_ACTORS[("group-a", "actor-a")] = key
            openclaw_runtime._GATEWAY_ACTOR_ENVS[("group-a", "actor-a")] = managed_env
            normalized_root = os.path.normcase(os.path.abspath(str(context_root)))
            openclaw_runtime._INITIALIZED_CONTEXTS[normalized_root] = ("initialized",)
            openclaw_runtime._GATEWAY_PORT_RESERVATIONS[normalized_root] = 24128
            openclaw_runtime._CONTEXT_LOCKS[normalized_root] = threading.RLock()
            timer = Mock()

            with patch("no1.daemon.openclaw_runtime.threading.Timer", return_value=timer), patch.object(
                openclaw_runtime, "_terminate_owned_gateway"
            ):
                openclaw_runtime.remove_openclaw_actor_runtime(
                    "group-a",
                    "actor-a",
                    env={"ONECOLLEAGUE_HOME": str(home)},
                )

            timer.start.assert_called_once_with()
            timer.cancel.assert_called_once_with()
            self.assertNotIn(key, openclaw_runtime._GATEWAY_IDLE_TIMERS)
            self.assertNotIn(normalized_root, openclaw_runtime._INITIALIZED_CONTEXTS)
            self.assertNotIn(normalized_root, openclaw_runtime._GATEWAY_PORT_RESERVATIONS)
            self.assertNotIn(normalized_root, openclaw_runtime._CONTEXT_LOCKS)
            self.assertFalse(context_root.exists())

    def test_skill_projection_reconciles_removed_actor_skills(self) -> None:
        from no1.daemon.ops.capability_ops import _skill_packages

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            extracted = home / "package"
            extracted.mkdir()
            (extracted / "SKILL.md").write_text("---\nname: selected-skill\n---\nBody\n", encoding="utf-8")
            install_state = home / "install_state.json"
            install_state.write_text(
                json.dumps(
                    {
                        "packages": {
                            "skill:test": {
                                "extracted_path": str(extracted),
                                "skill_slug": "selected-skill",
                                "sha256": "abc123",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            group = SimpleNamespace(group_id="group-a", doc={"actors": [{"id": "actor-a"}]})
            admitted = {
                "admitted_capabilities": ["skill:test"],
                "admitted_records": {
                    "skill:test": {
                        "capability_id": "skill:test",
                        "kind": "skill",
                        "install_mode": "codex_skill_package",
                        "qualification_status": "qualified",
                    }
                },
            }
            with patch.object(_skill_packages, "ensure_home", return_value=home), patch.object(
                _skill_packages,
                "_skill_package_install_state_path",
                return_value=install_state,
            ), patch.object(_skill_packages, "resolve_current_admission", return_value=admitted):
                projection = _skill_packages.prepare_openclaw_skill_package_overlay_for_actor(group, "actor-a")

            root = Path(projection["root"])
            self.assertTrue((root / "selected-skill" / "SKILL.md").is_file())
            self.assertEqual(projection["selected_names"], ["onecolleague-37f351890c26fe42"])

            empty = {"admitted_capabilities": [], "admitted_records": {}}
            with patch.object(_skill_packages, "ensure_home", return_value=home), patch.object(
                _skill_packages,
                "_skill_package_install_state_path",
                return_value=install_state,
            ), patch.object(_skill_packages, "resolve_current_admission", return_value=empty):
                projection = _skill_packages.prepare_openclaw_skill_package_overlay_for_actor(group, "actor-a")

            self.assertEqual(projection["root"], "")
            self.assertFalse((root / "selected-skill").exists())

    def test_skill_projection_preserves_current_overlay_when_staging_fails(self) -> None:
        from no1.daemon.ops.capability_ops import _skill_packages

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            group = SimpleNamespace(group_id="group-a", doc={"actors": [{"id": "actor-a"}]})
            with patch.object(_skill_packages, "ensure_home", return_value=home):
                _, actor_root = _skill_packages._openclaw_actor_skill_root("group-a", "actor-a")
            existing_skill = actor_root / "skills" / "existing" / "SKILL.md"
            existing_skill.parent.mkdir(parents=True)
            existing_skill.write_text("---\nname: existing\n---\n", encoding="utf-8")
            install_state = home / "install_state.json"
            install_state.write_text(json.dumps({"packages": {"skill:test": {}}}), encoding="utf-8")
            admitted = {
                "admitted_capabilities": ["skill:test"],
                "admitted_records": {
                    "skill:test": {
                        "capability_id": "skill:test",
                        "kind": "skill",
                        "install_mode": "codex_skill_package",
                        "qualification_status": "qualified",
                    }
                },
            }
            with patch.object(_skill_packages, "ensure_home", return_value=home), patch.object(
                _skill_packages,
                "_skill_package_install_state_path",
                return_value=install_state,
            ), patch.object(_skill_packages, "resolve_current_admission", return_value=admitted):
                with self.assertRaisesRegex(ValueError, "not installed"):
                    _skill_packages.prepare_openclaw_skill_package_overlay_for_actor(group, "actor-a")

            self.assertTrue(existing_skill.is_file())

    def test_live_skill_refresh_preserves_base_skills_and_replaces_actor_overlay(self) -> None:
        from no1.daemon import openclaw_runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "context"
            root.mkdir()
            config_path = root / "openclaw.json"
            agent_id = openclaw_runtime.openclaw_agent_id("group-a", "actor-a")
            managed_root = Path(td) / "runtime" / "openclaw" / "skills" / "actors"
            actor_root = managed_root / hashlib.sha256(b"group-a\0actor-a").hexdigest()[:16]
            config_path.write_text(
                json.dumps({
                    "agents": {"list": [{"id": agent_id, "skills": ["base-skill", "onecolleague-old"]}]},
                    "skills": {"load": {"extraDirs": ["C:/base", str(actor_root / "old" / "skills"), "C:/other"]}},
                }),
                encoding="utf-8",
            )
            env = {
                "OPENCLAW_CONFIG_PATH": str(config_path),
                "OPENCLAW_GATEWAY_TOKEN": "token",
                "OPENCLAW_GATEWAY_PORT": "24128",
            }
            openclaw_runtime._GATEWAY_ACTOR_ENVS[("group-a", "actor-a")] = env
            published = {}
            with patch.object(openclaw_runtime, "_context_command", return_value=["openclaw"]), patch.object(
                openclaw_runtime, "_publish_config", side_effect=lambda prefix, candidate, env: published.update(candidate)
            ), patch("no1.kernel.group.load_group", return_value=SimpleNamespace(group_id="group-a")), patch(
                "no1.daemon.ops.capability_ops.prepare_openclaw_skill_package_overlay_for_actor",
                return_value={
                    "selected_names": ["onecolleague-new"],
                    "managed_root": str(managed_root),
                    "root": str(actor_root / "skills"),
                },
            ):
                result = openclaw_runtime.refresh_openclaw_actor_skill_projection("group-a", "actor-a")

            self.assertTrue(result["refreshed"])
            skills = published["agents"]["list"][0]["skills"]
            self.assertEqual(skills, ["base-skill", "onecolleague-new"])
            self.assertIn("C:/base", published["skills"]["load"]["extraDirs"])
            self.assertNotIn(str(actor_root / "old" / "skills"), published["skills"]["load"]["extraDirs"])
            self.assertIn(str(actor_root / "skills"), published["skills"]["load"]["extraDirs"])


if __name__ == "__main__":
    unittest.main()
