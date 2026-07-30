import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestMcpCodeMode(unittest.TestCase):
    def setUp(self) -> None:
        from no1.ports.mcp import server as mcp_server

        denied = set(mcp_server._WEB_MODEL_HARD_DENIED_TOOLS)
        self._engine_authorization_patches = [
            patch.object(mcp_server, "_WEB_MODEL_HARD_DENIED_TOOLS", frozenset()),
            patch.object(
                mcp_server,
                "_WEB_MODEL_PEER_ADVERTISED_TOOL_NAMES",
                frozenset(set(mcp_server._WEB_MODEL_PEER_ADVERTISED_TOOL_NAMES) | denied),
            ),
            patch.object(
                mcp_server,
                "_WEB_MODEL_FOREMAN_ADVERTISED_TOOL_NAMES",
                frozenset(set(mcp_server._WEB_MODEL_FOREMAN_ADVERTISED_TOOL_NAMES) | denied),
            ),
            patch.object(
                mcp_server,
                "_WEB_MODEL_PEER_ALLOWED_TOOL_NAMES",
                frozenset(set(mcp_server._WEB_MODEL_PEER_ALLOWED_TOOL_NAMES) | denied),
            ),
        ]
        for authorization_patch in self._engine_authorization_patches:
            authorization_patch.start()
            self.addCleanup(authorization_patch.stop)

    def _with_home_and_group(self):
        from no1.kernel.actors import add_actor
        from no1.kernel.group import attach_scope_to_group, create_group
        from no1.kernel.registry import load_registry
        from no1.kernel.scope import detect_scope

        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        home = Path(td) / "home"
        workspace = Path(td) / "repo"
        home.mkdir()
        workspace.mkdir()
        os.environ["CCCC_HOME"] = str(home)
        reg = load_registry()
        group = create_group(reg, title="code-mode", topic="")
        group = attach_scope_to_group(reg, group, detect_scope(workspace), set_active=True)
        add_actor(group, actor_id="foreman1", title="Foreman", runtime="codex", runner="headless")
        add_actor(group, actor_id="peer1", title="ChatGPT Web Model", runtime="web_model", runner="headless")

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return home, workspace, group, cleanup

    def test_code_exec_orchestrates_repo_patch_shell_git_and_message_tools(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, workspace, group, cleanup = self._with_home_and_group()
        try:
            (workspace / "app.txt").write_text("one\ntwo\n", encoding="utf-8")
            import subprocess

            subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True, text=True)
            subprocess.run(["git", "add", "app.txt"], cwd=workspace, check=True, capture_output=True, text=True)
            subprocess.run(
                ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            )
            source = r'''
const before = await tools.onecolleague_repo({ action: "read", path: "app.txt" });
const patch = [
  "*** Begin Patch",
  "*** Update File: app.txt",
  "@@",
  " one",
  "-two",
  "+TWO",
  "*** End Patch",
  "",
].join("\n");
const applied = await tools.onecolleague_apply_patch({ patch });
const exec = await tools.onecolleague_exec_command({
  command: "printf exec-start; sleep 0.1; printf exec-done",
  yield_time_ms: 10,
});
let execOutput = exec.output || "";
if (exec.running && exec.session_id) {
  const poll = await tools.onecolleague_write_stdin({ session_id: exec.session_id, yield_time_ms: 300 });
  execOutput += poll.output || "";
}
const diff = await tools.onecolleague_git({ action: "diff" });
const sent = await tools.onecolleague_message_send({ text: "code mode report", to: ["user"] });
text(JSON.stringify({
  before: before.content,
  applied: applied.applied,
  execOutput,
  diff: diff.stdout,
  sent: sent.ok,
}));
'''

            def _fake_daemon(req, **_kwargs):
                if req.get("op") == "send":
                    return {"ok": True, "event_id": "ev_code_mode"}
                return {"ok": True}

            from no1.ports.mcp.handlers import onecolleague_messaging

            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"), patch.object(
                onecolleague_messaging, "_call_daemon_or_raise", side_effect=_fake_daemon
            ):
                out = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})

            self.assertEqual(out.get("status"), "completed")
            self.assertFalse(out.get("running"))
            self.assertIn('"before":"one\\ntwo\\n"', str(out.get("output") or ""))
            self.assertIn('"applied":true', str(out.get("output") or ""))
            self.assertIn("exec-done", str(out.get("output") or ""))
            self.assertIn("+TWO", str(out.get("output") or ""))
            self.assertEqual((workspace / "app.txt").read_text(encoding="utf-8"), "one\nTWO\n")
        finally:
            cleanup()

    def test_exec_command_and_write_stdin_preserve_zero_yield(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"), patch.object(
                mcp_server,
                "exec_command_tool",
                return_value={"running": True, "session_id": "exec-1"},
            ) as exec_tool, patch.object(
                mcp_server,
                "write_stdin_tool",
                return_value={"running": True, "session_id": "exec-1"},
            ) as stdin_tool:
                mcp_server.handle_tool_call("onecolleague_exec_command", {"command": "sleep 1", "yield_time_ms": 0})
                mcp_server.handle_tool_call("onecolleague_write_stdin", {"session_id": "exec-1", "yield_time_ms": 0})

            self.assertEqual(exec_tool.call_args.kwargs.get("yield_time_ms"), 0)
            self.assertEqual(stdin_tool.call_args.kwargs.get("yield_time_ms"), 0)
        finally:
            cleanup()

    def test_code_exec_yields_and_code_wait_resumes(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            source = r'''
text("phase-1");
yield_control();
await new Promise((resolve) => setTimeout(resolve, 100));
text("phase-2");
'''
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                first = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})
                self.assertEqual(first.get("status"), "running")
                cell_id = str(first.get("cell_id") or "")
                self.assertTrue(cell_id)
                second = mcp_server.handle_tool_call(
                    "onecolleague_code_wait",
                    {"cell_id": cell_id, "yield_time_ms": 1000},
                )

            self.assertEqual(second.get("status"), "completed")
            self.assertIn("phase-2", str(second.get("output") or ""))
        finally:
            cleanup()

    def test_code_wait_can_terminate_running_cell(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            source = r'''
text("before-wait");
await new Promise(() => {});
'''
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                first = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 50})
                self.assertEqual(first.get("status"), "running")
                cell_id = str(first.get("cell_id") or "")
                self.assertTrue(cell_id)
                terminated = mcp_server.handle_tool_call("onecolleague_code_wait", {"cell_id": cell_id, "terminate": True})
                missing = mcp_server.handle_tool_call("onecolleague_code_wait", {"cell_id": cell_id, "yield_time_ms": 1})

            self.assertEqual(terminated.get("status"), "terminated")
            self.assertEqual(missing.get("status"), "missing")
        finally:
            cleanup()

    def test_code_wait_is_bound_to_creating_actor(self) -> None:
        from no1.kernel.actors import add_actor
        from no1.kernel.group import load_group
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            add_actor(group, actor_id="peer2", title="Second Web Model", runtime="web_model", runner="headless")
            source = r'''
text("peer1-secret-output");
await new Promise(() => {});
'''
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                first = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 50})
                self.assertEqual(first.get("status"), "running")
                cell_id = str(first.get("cell_id") or "")
                self.assertTrue(cell_id)

            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer2"):
                blocked_wait = mcp_server.handle_tool_call("onecolleague_code_wait", {"cell_id": cell_id, "yield_time_ms": 1})
                blocked_terminate = mcp_server.handle_tool_call(
                    "onecolleague_code_wait",
                    {"cell_id": cell_id, "terminate": True},
                )

            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                still_running = mcp_server.handle_tool_call("onecolleague_code_wait", {"cell_id": cell_id, "yield_time_ms": 1})
                terminated = mcp_server.handle_tool_call("onecolleague_code_wait", {"cell_id": cell_id, "terminate": True})

            self.assertEqual(load_group(group.group_id).group_id, group.group_id)
            self.assertEqual(blocked_wait.get("status"), "missing")
            self.assertNotIn("peer1-secret-output", str(blocked_wait.get("output") or ""))
            self.assertEqual(blocked_terminate.get("status"), "missing")
            self.assertEqual(still_running.get("status"), "running")
            self.assertEqual(terminated.get("status"), "terminated")
        finally:
            cleanup()

    def test_code_mode_store_and_load_survive_between_cells(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                first = mcp_server.handle_tool_call(
                    "onecolleague_code_exec",
                    {"source": 'store("answer", { value: 42 }); text("stored");', "yield_time_ms": 5000},
                )
                second = mcp_server.handle_tool_call(
                    "onecolleague_code_exec",
                    {"source": 'text(JSON.stringify(load("answer")));', "yield_time_ms": 5000},
                )
            self.assertEqual(first.get("status"), "completed")
            self.assertEqual(second.get("status"), "completed")
            self.assertEqual(str(second.get("output") or "").strip(), '{"value":42}')
        finally:
            cleanup()

    def test_code_mode_engine_fixture_keeps_execution_fallbacks_visible(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            expected = {
                "onecolleague_code_exec",
                "onecolleague_code_wait",
                "onecolleague_repo",
                "onecolleague_apply_patch",
                "onecolleague_exec_command",
                "onecolleague_write_stdin",
                "onecolleague_git",
                "onecolleague_message_send",
            }
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                names = {str(spec.get("name") or "") for spec in mcp_server.list_tools_for_caller()}
            self.assertTrue(expected.issubset(names), expected - names)
        finally:
            cleanup()

    def test_code_mode_env_kill_switch_hides_and_blocks_code_tools(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            with patch.dict(os.environ, {"ONECOLLEAGUE_WEB_MODEL_CODE_MODE": "0"}), runtime_context_override(
                home=str(home), group_id=group.group_id, actor_id="peer1"
            ):
                names = {str(spec.get("name") or "") for spec in mcp_server.list_tools_for_caller()}
                self.assertNotIn("onecolleague_code_exec", names)
                self.assertNotIn("onecolleague_code_wait", names)
                self.assertIn("onecolleague_repo", names)
                with self.assertRaises(mcp_server.MCPError) as cm:
                    mcp_server.handle_tool_call("onecolleague_code_exec", {"source": "text('blocked')"})
            self.assertEqual(cm.exception.code, "code_mode_disabled")
        finally:
            cleanup()

    def test_code_mode_hides_web_model_foreman_tools_from_peer(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            source = r'''
text(JSON.stringify({
  has_actor: Object.prototype.hasOwnProperty.call(tools, "onecolleague_actor"),
  has_shell: Object.prototype.hasOwnProperty.call(tools, "onecolleague_shell")
}));
'''
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                out = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})
            self.assertEqual(out.get("status"), "completed")
            self.assertEqual(json.loads(str(out.get("output") or "{}")), {"has_actor": False, "has_shell": True})
        finally:
            cleanup()

    def test_code_mode_rejects_recursive_exec(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            source = 'text(String(ALL_TOOLS.some((tool) => tool.raw_name === "onecolleague_code_exec")));'
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                out = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})
            self.assertEqual(out.get("status"), "completed")
            self.assertEqual(str(out.get("output") or "").strip(), "false")
        finally:
            cleanup()

    def test_code_mode_exposes_tool_help_and_common_work_loops(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            source = r'''
const help = tool_help("repo");
const full = tool_help("repo", { detail: "schema" });
const turn = tool_help("turn_complete");
const finish = tool_help("finish_turn");
const messageHelp = tool_help("message");
const names = tool_names("turn_complete");
const finishNames = tool_names("finish_turn");
const repoNames = tool_names("repo");
const messageNames = tool_names("message");
const files = list_tools("file");
text(JSON.stringify({
  hasRepo: help.tools.some((tool) => tool.raw_name === "onecolleague_repo"),
  compactByDefault: help.tools.every((tool) => tool.summary && !tool.description),
  fullHasSchemaText: full.tools.some((tool) => tool.description && tool.description.includes("inputSchema")),
  turnCompleteAlias: names[0] === "onecolleague_runtime_complete_turn",
  finishCurated: finishNames.slice(0, 4).join(",") === "onecolleague_message_reply,onecolleague_message_send,onecolleague_agent_state,onecolleague_runtime_complete_turn",
  finishNoRepoNoise: !finishNames.includes("onecolleague_apply_patch"),
  finishToolsNarrow: finish.tools.slice(0, 4).every((tool) => ["onecolleague_message_reply", "onecolleague_message_send", "onecolleague_agent_state", "onecolleague_runtime_complete_turn"].includes(tool.raw_name)),
  repoRanking: repoNames.slice(0, 4).join(",") === "onecolleague_repo,onecolleague_repo_edit,onecolleague_apply_patch,onecolleague_git",
  messageRanking: messageNames.slice(0, 2).join(",") === "onecolleague_message_reply,onecolleague_message_send",
  trackedSendBounded: messageHelp.notes.some((note) => note.includes("durable delegation")),
  finishLoopAlias: turn.common_work_loops.some((loop) => loop.name === "finish_turn"),
  fileListCompact: files.some((tool) => tool.raw_name === "onecolleague_file" && tool.summary),
  hasPatchLoop: COMMON_WORK_LOOPS.some((loop) => loop.name === "patch_safely"),
  usage: help.usage.includes("tools.<name>"),
}));
'''
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                out = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})
            self.assertEqual(out.get("status"), "completed")
            payload = str(out.get("output") or "")
            self.assertIn('"hasRepo":true', payload)
            self.assertIn('"compactByDefault":true', payload)
            self.assertIn('"fullHasSchemaText":true', payload)
            self.assertIn('"turnCompleteAlias":true', payload)
            self.assertIn('"finishCurated":true', payload)
            self.assertIn('"finishNoRepoNoise":true', payload)
            self.assertIn('"finishToolsNarrow":true', payload)
            self.assertIn('"repoRanking":true', payload)
            self.assertIn('"messageRanking":true', payload)
            self.assertIn('"trackedSendBounded":true', payload)
            self.assertIn('"finishLoopAlias":true', payload)
            self.assertIn('"fileListCompact":true', payload)
            self.assertIn('"hasPatchLoop":true', payload)
            self.assertIn('"usage":true', payload)
        finally:
            cleanup()

    def test_code_mode_nested_errors_include_recommended_action(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, workspace, group, cleanup = self._with_home_and_group()
        try:
            (workspace / "app.txt").write_text("alpha\nbeta\n", encoding="utf-8")
            source = r'''
try {
  await tools.onecolleague_repo_edit({ action: "replace", path: "app.txt", old_text: "missing", new_text: "x" });
  text("unexpected");
} catch (err) {
  text(String(err.message || err));
}
'''
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                out = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})
            self.assertEqual(out.get("status"), "completed")
            output = str(out.get("output") or "")
            self.assertIn("old_text_not_found", output)
            self.assertIn("recommended_action", output)
            self.assertIn("onecolleague_repo(action='read')", output)
        finally:
            cleanup()

    def test_code_mode_keeps_node_host_apis_out_of_sandbox(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            source = "text([typeof console, typeof require, typeof process, typeof fetch, typeof WebSocket].join(','));"
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                out = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})
            self.assertEqual(out.get("status"), "completed")
            self.assertEqual(str(out.get("output") or "").strip(), "undefined,undefined,undefined,undefined,undefined")
        finally:
            cleanup()

    def test_code_mode_blocks_constructor_escape_to_node_process(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            source = r'''
const attempts = [
  ["global", () => this.constructor.constructor("return process")()],
  ["text", () => text.constructor.constructor("return process")()],
  ["tool", () => tools.onecolleague_repo.constructor.constructor("return process")()],
  ["object", () => ({}).constructor.constructor("return process")()],
];
const results = [];
for (const [name, fn] of attempts) {
  try {
    const escaped = fn();
    results.push(`${name}:${String(Boolean(escaped && escaped.env && escaped.env.HOME))}`);
  } catch (err) {
    results.push(`${name}:blocked`);
  }
}
try {
  const escaped = load("missing").constructor.constructor("return process")();
  results.push(`load:${String(Boolean(escaped && escaped.env && escaped.env.HOME))}`);
} catch (err) {
  results.push("load:blocked");
}
text(results.join(","));
'''
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                out = mcp_server.handle_tool_call("onecolleague_code_exec", {"source": source, "yield_time_ms": 5000})
            self.assertEqual(out.get("status"), "completed")
            self.assertEqual(
                str(out.get("output") or "").strip(),
                "global:blocked,text:blocked,tool:blocked,object:blocked,load:blocked",
            )
        finally:
            cleanup()

    def test_code_mode_rejects_require_and_import_source(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, _workspace, group, cleanup = self._with_home_and_group()
        try:
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id="peer1"):
                with self.assertRaises(mcp_server.MCPError) as require_cm:
                    mcp_server.handle_tool_call("onecolleague_code_exec", {"source": "const fs = require('node:fs');"})
                with self.assertRaises(mcp_server.MCPError) as import_cm:
                    mcp_server.handle_tool_call("onecolleague_code_exec", {"source": "import('node:fs')"})
                ok = mcp_server.handle_tool_call(
                    "onecolleague_code_exec",
                    {"source": "const important = 1; text(String(important));", "yield_time_ms": 5000},
                )
            self.assertEqual(require_cm.exception.code, "unsupported_js")
            self.assertEqual(import_cm.exception.code, "unsupported_js")
            self.assertEqual(str(ok.get("output") or "").strip(), "1")
        finally:
            cleanup()

    def test_code_exec_requires_web_model_actor(self) -> None:
        from no1.ports.mcp import server as mcp_server

        class _FakeGroup:
            pass

        with patch.object(mcp_server, "_resolve_group_id", return_value="g_test"), patch.object(
            mcp_server, "_resolve_self_actor_id", return_value="peer1"
        ), patch.object(mcp_server, "load_group", return_value=_FakeGroup()), patch.object(
            mcp_server, "find_actor", return_value={"id": "peer1", "runtime": "codex", "runner": "headless"}
        ):
            with self.assertRaises(mcp_server.MCPError) as cm:
                mcp_server.handle_tool_call("onecolleague_code_exec", {"source": "text('blocked')"})
        self.assertEqual(cm.exception.code, "invalid_actor_runtime")


class TestWebModelLocalExecutionDenied(unittest.TestCase):
    execution_tools = {
        "onecolleague_shell",
        "onecolleague_exec_command",
        "onecolleague_write_stdin",
        "onecolleague_code_exec",
        "onecolleague_code_wait",
        "onecolleague_git",
    }
    capability_tools = {
        "onecolleague_capability_enable",
        "onecolleague_capability_install",
        "onecolleague_capability_use",
    }
    hard_denied_tools = execution_tools | capability_tools

    def _with_web_model_actor(self, *, role: str):
        from no1.kernel.actors import add_actor
        from no1.kernel.group import attach_scope_to_group, create_group
        from no1.kernel.registry import load_registry
        from no1.kernel.scope import detect_scope

        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = Path(td_ctx.__enter__())
        home = td / "home"
        workspace = td / "repo"
        home.mkdir()
        workspace.mkdir()
        os.environ["CCCC_HOME"] = str(home)
        group = create_group(load_registry(), title=f"web-model-{role}", topic="")
        group = attach_scope_to_group(load_registry(), group, detect_scope(workspace), set_active=True)
        if role == "peer":
            add_actor(group, actor_id="lead", title="Foreman", runtime="codex", runner="headless")
            actor_id = "web-peer"
        else:
            actor_id = "web-foreman"
        add_actor(group, actor_id=actor_id, title="Web Model", runtime="web_model", runner="headless")

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return home, group, actor_id, cleanup

    def test_peer_and_foreman_lists_hide_execution_tools_under_dynamic_and_full_profiles(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        for role in ("peer", "foreman"):
            home, group, actor_id, cleanup = self._with_web_model_actor(role=role)
            try:
                dynamic_tools = [
                    {
                        "name": name,
                        "description": "injected",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                    for name in sorted(self.hard_denied_tools)
                ]
                for profile in ("", "full"):
                    with self.subTest(role=role, profile=profile or "default"), patch.dict(
                        os.environ,
                        {"CCCC_MCP_TOOL_PROFILE": profile},
                        clear=False,
                    ), patch.object(
                        mcp_server,
                        "_call_daemon_or_raise",
                        return_value={"visible_tools": sorted(self.hard_denied_tools), "dynamic_tools": dynamic_tools},
                    ), runtime_context_override(home=str(home), group_id=group.group_id, actor_id=actor_id):
                        names = {str(spec.get("name") or "") for spec in mcp_server.list_tools_for_caller()}
                    self.assertTrue(self.hard_denied_tools.isdisjoint(names), self.hard_denied_tools.intersection(names))
                    self.assertTrue({"onecolleague_capability_search", "onecolleague_capability_state"}.issubset(names))
                    self.assertTrue(
                        {"onecolleague_repo", "onecolleague_repo_edit", "onecolleague_apply_patch"}.issubset(names)
                    )
            finally:
                cleanup()

    def test_peer_and_foreman_reject_direct_legacy_and_dynamic_execution_calls(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override
        from no1.ports.mcp.toolspecs import legacy_mcp_tool_name

        for role in ("peer", "foreman"):
            home, group, actor_id, cleanup = self._with_web_model_actor(role=role)
            try:
                for canonical_name in sorted(self.execution_tools):
                    for requested_name in (canonical_name, legacy_mcp_tool_name(canonical_name)):
                        with self.subTest(role=role, tool=requested_name), patch.object(
                            mcp_server,
                            "_call_daemon_or_raise",
                        ) as daemon_call, runtime_context_override(
                            home=str(home), group_id=group.group_id, actor_id=actor_id
                        ):
                            with self.assertRaises(mcp_server.MCPError) as caught:
                                mcp_server.handle_tool_call(requested_name, {})
                        self.assertEqual(caught.exception.code, "permission_denied")
                        daemon_call.assert_not_called()
            finally:
                cleanup()

    def test_foreman_nested_and_external_capability_use_cannot_bypass_execution_deny(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        home, group, actor_id, cleanup = self._with_web_model_actor(role="foreman")
        try:
            with runtime_context_override(home=str(home), group_id=group.group_id, actor_id=actor_id):
                for tool_name in sorted(self.execution_tools):
                    with self.subTest(path="nested_scope", tool=tool_name), mcp_server.capability_use_nested_builtin_call_scope():
                        with self.assertRaises(mcp_server.MCPError) as nested_caught:
                            mcp_server.handle_tool_call(tool_name, {})
                    self.assertEqual(nested_caught.exception.code, "permission_denied")

                    with self.subTest(path="external_capability", tool=tool_name), patch.object(
                        mcp_server,
                        "_call_daemon_or_raise",
                    ) as daemon_call:
                        with self.assertRaises(mcp_server.MCPError) as capability_caught:
                            mcp_server.handle_tool_call(
                                "onecolleague_capability_use",
                                {
                                    "capability_id": "mcp:injected-exec",
                                    "tool_name": tool_name,
                                    "tool_arguments": {},
                                },
                            )
                    self.assertEqual(capability_caught.exception.code, "permission_denied")
                    daemon_call.assert_not_called()
        finally:
            cleanup()

    def test_dynamic_external_mcp_tool_cannot_bypass_capability_meta_tool_deny(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        dynamic_tool_name = "onecolleague_ext_deadbeef_process_runner"
        for role in ("peer", "foreman"):
            home, group, actor_id, cleanup = self._with_web_model_actor(role=role)
            try:
                for meta_tool in sorted(self.capability_tools):
                    arguments = {
                        "actor_id": actor_id,
                        "capability_id": "mcp:external-runner",
                        "tool_name": dynamic_tool_name,
                        "tool_arguments": {"command": "ignored"},
                        "target": "mcp:external-runner",
                    }
                    with self.subTest(role=role, tool=meta_tool), patch.object(
                        mcp_server,
                        "_call_daemon_or_raise",
                    ) as daemon_call, runtime_context_override(
                        home=str(home), group_id=group.group_id, actor_id=actor_id
                    ):
                        with self.assertRaises(mcp_server.MCPError) as caught:
                            mcp_server.handle_tool_call(meta_tool, arguments)
                    self.assertEqual(caught.exception.code, "permission_denied")
                    daemon_call.assert_not_called()
            finally:
                cleanup()

    def test_unknown_dynamic_tool_name_is_denied_before_daemon_fallback(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        dynamic_tool_name = "onecolleague_ext_deadbeef_process_runner"
        for role in ("peer", "foreman"):
            home, group, actor_id, cleanup = self._with_web_model_actor(role=role)
            try:
                with self.subTest(role=role), patch.object(
                    mcp_server,
                    "_call_daemon_or_raise",
                ) as daemon_call, runtime_context_override(
                    home=str(home), group_id=group.group_id, actor_id=actor_id
                ):
                    with self.assertRaises(mcp_server.MCPError) as caught:
                        mcp_server.handle_tool_call(dynamic_tool_name, {"command": "ignored"})
                self.assertEqual(caught.exception.code, "permission_denied")
                daemon_call.assert_not_called()
            finally:
                cleanup()

    def test_advertised_repo_context_and_message_builtins_remain_authorized(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        preserved = {
            "onecolleague_repo",
            "onecolleague_context_get",
            "onecolleague_message_send",
        }
        for role in ("peer", "foreman"):
            home, group, actor_id, cleanup = self._with_web_model_actor(role=role)
            try:
                with runtime_context_override(home=str(home), group_id=group.group_id, actor_id=actor_id):
                    for tool_name in sorted(preserved):
                        with self.subTest(role=role, tool=tool_name):
                            mcp_server._authorize_web_model_builtin_tool_call(tool_name)
            finally:
                cleanup()


if __name__ == "__main__":
    unittest.main()
