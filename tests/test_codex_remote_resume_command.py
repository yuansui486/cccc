import pytest

from no1.daemon.codex_app_sessions import _codex_remote_tui_command


@pytest.mark.parametrize("permission_args", [
    ["--dangerously-bypass-approvals-and-sandbox"],
    ["--yolo"], ["--full-auto"], ["--approve-for-me"],
    ["-a", "never"], ["--ask-for-approval", "on-request"],
    ["--ask-for-approval=never"],
    ["-s", "workspace-write"], ["--sandbox", "read-only"],
    ["--sandbox=danger-full-access"],
    ["--add-dir", "C:/extra directory"], ["--add-dir=C:/extra"],
])
def test_remote_resume_inherits_permissions_without_losing_following_options(permission_args):
    base = ["codex", *permission_args, "-c", "shell_environment_policy.inherit=all",
            "-m", "gpt-5.4", "--search", "--no-alt-screen", "prompt not to forward"]
    expected = ["codex", "-c", "shell_environment_policy.inherit=all", "-m", "gpt-5.4",
                "--search", "--no-alt-screen"]
    assert _codex_remote_tui_command(base, "ws://127.0.0.1:1234", resume_thread_id="thread-id") == [
        *expected, "resume", "thread-id", "--remote", "ws://127.0.0.1:1234",
    ]
    assert _codex_remote_tui_command(base, "ws://127.0.0.1:1234") == [
        "codex", *permission_args, *expected[1:], "--remote", "ws://127.0.0.1:1234",
    ]
