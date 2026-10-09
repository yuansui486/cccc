from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from no1.runners import pty_win


@pytest.mark.parametrize("executable", [
    "codex", "codex.exe", "codex.cmd", r"C:\Program Files\Codex\codex.exe",
    "C:/Tools/codex.cmd", "CODEX.EXE",
])
@pytest.mark.parametrize("args", [
    [], ["-c", "shell_environment_policy.inherit=all", "--search"],
    ["resume", "thread-id", "--remote", "ws://127.0.0.1:1234"],
    ["--remote=ws://127.0.0.1:1234", "resume", "--last"],
    ["fork", "thread-id"], ["--", "--no-alt-screen"],
    ["-m", "exec", "resume", "thread-id"], ["explain this code"],
])
def test_inline_mode_covers_existing_interactive_commands_without_mutation(executable, args):
    command = [executable, *args]
    original = list(command)
    assert pty_win._codex_inline_command(command, runtime="codex") == [
        executable, "--no-alt-screen", *args,
    ]
    assert command == original


@pytest.mark.parametrize("command,runtime", [
    (["codex", "--no-alt-screen", "resume", "id"], "codex"),
    (["codex", "resume", "id", "--no-alt-screen"], "codex"),
    (["codex", "-c", "model=example", "exec", "prompt"], "codex"),
    (["codex", "app-server", "--listen", "ws://127.0.0.1:1234"], "codex"),
    (["codex", "review"], "codex"), (["codex", "--help"], "codex"),
    (["codex", "--version"], "codex"),
    (["npx", "codex", "resume", "id"], "codex"),
    (["cmd.exe", "/c", "codex"], "codex"),
    (["claude"], "claude"), (["codex"], "custom"), ([], "codex"),
])
def test_inline_mode_leaves_explicit_flags_and_other_commands_unchanged(command, runtime):
    assert pty_win._codex_inline_command(command, runtime=runtime) == command


def test_windows_spawn_applies_inline_mode_before_starting_threads(tmp_path):
    command = ["codex.cmd", "resume", "thread-id", "--remote", "ws://127.0.0.1:1234"]
    proc = Mock(pid=123)
    with patch.object(pty_win, "PTY_SUPPORTED", True), patch.object(
        pty_win, "_WINPTY_PROCESS"
    ) as backend, patch.object(pty_win.threading, "Thread"):
        backend.spawn.return_value = proc
        session = pty_win.PtySession(
            group_id="g", actor_id="a", cwd=Path(tmp_path), command=command,
            env={}, runtime="codex",
        )
        try:
            assert backend.spawn.call_args.args[0] == [command[0], "--no-alt-screen", *command[1:]]
            assert "--no-alt-screen" not in command
        finally:
            session._selector.close()
            session._wake_r.close()
            session._wake_w.close()
