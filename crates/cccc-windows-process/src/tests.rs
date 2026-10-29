use super::*;
use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

#[test]
fn desktop_policy_inherits_environment_without_changing_global_test_state() {
    const PROBE: &str = "CCCC_TEST_DESKTOP_POLICY_CHILD";
    if std::env::var_os(PROBE).is_some() {
        let mut command = Command::new("cmd");
        assert_eq!(background_creation_flags(&command, 0), 0x0800_0000);
        command.env("ONECOLLEAGUE_DESKTOP", "0");
        assert_eq!(background_creation_flags(&command, 0), 0);
        command.env_remove("ONECOLLEAGUE_DESKTOP");
        assert_eq!(background_creation_flags(&command, 0), 0);
        return;
    }
    let mut command = Command::new(std::env::current_exe().expect("test executable"));
    command
        .args([
            "--exact",
            "tests::desktop_policy_inherits_environment_without_changing_global_test_state",
        ])
        .env(PROBE, "1")
        .env("ONECOLLEAGUE_DESKTOP", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let output = spawn_suspended(&mut command, 0)
        .expect("spawn policy fixture")
        .resume()
        .expect("resume policy fixture")
        .wait_with_output()
        .expect("policy fixture output");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stdout)
    );
}

#[test]
fn desktop_cmd_shim_has_no_visible_console_and_preserves_utf8_stdio() {
    let mut command = Command::new("cmd.exe");
    command
        .args(["/D", "/C", "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            "[Console]::InputEncoding = [Text.UTF8Encoding]::new(); [Console]::OutputEncoding = [Text.UTF8Encoding]::new(); Add-Type 'using System.Runtime.InteropServices; public class ConsoleProbe { [DllImport(\"kernel32.dll\")] public static extern System.IntPtr GetConsoleWindow(); [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(System.IntPtr hwnd); }'; if ([ConsoleProbe]::IsWindowVisible([ConsoleProbe]::GetConsoleWindow())) { exit 91 }; $text = [Console]::In.ReadLine(); [Console]::Out.WriteLine($text)"])
        .env("ONECOLLEAGUE_DESKTOP", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = spawn_suspended(&mut command, 0)
        .expect("suspended desktop shim")
        .resume()
        .expect("resume desktop shim");
    use std::io::Write;
    child
        .stdin
        .take()
        .expect("stdin")
        .write_all("stdio-preserved-自检\n".as_bytes())
        .expect("write stdin");
    let output = child.wait_with_output().expect("read shim output");
    assert!(
        output.status.success(),
        "console was allocated or shim failed ({:?}): {} / {}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("stdio-preserved-自检"));
}

#[test]
fn child_cannot_execute_before_resume_and_drop_cleans_up_suspended_launch() {
    let mut command = Command::new("cmd");
    command.args(["/C", "echo resumed"]).stdout(Stdio::piped());
    let mut suspended = spawn_suspended(&mut command, 0x0800_0000).expect("spawn suspended child");
    // Before resume the child cannot emit its very first output instruction.
    let stdout = suspended
        .take_stdout_for_test()
        .expect("complete take stdout for test in fixture");
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        reader.read_line(&mut line).expect("read child readiness");
        let _ = tx.send(line);
    });
    assert!(matches!(
        rx.recv_timeout(Duration::from_millis(100)),
        Err(mpsc::RecvTimeoutError::Timeout)
    ));
    let mut child = suspended.resume().expect("resume suspended child");
    assert_eq!(
        rx.recv_timeout(Duration::from_secs(5))
            .expect("receive before test deadline")
            .trim(),
        "resumed"
    );
    assert!(child.wait().expect("reap child process").success());

    let mut command = Command::new("cmd");
    command
        .args(["/C", "echo must-not-run"])
        .stdout(Stdio::piped());
    let mut suspended = spawn_suspended(&mut command, 0).expect("spawn suspended child");
    let stdout = suspended
        .take_stdout_for_test()
        .expect("complete take stdout for test in fixture");
    drop(suspended);
    let mut output = String::new();
    use std::io::Read;
    BufReader::new(stdout)
        .read_to_string(&mut output)
        .expect("read fixture contents");
    assert!(
        output.is_empty(),
        "aborted suspended launch executed child code"
    );
}
