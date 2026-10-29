use std::ffi::OsStr;
use std::process::Command;

/// Build a non-interactive helper command. Desktop Windows launches do not
/// allocate console windows; ordinary CLI and non-Windows behavior is unchanged.
/// Helper policy uses the current process environment at construction time.
/// OwnedProcessTree applies this policy separately at spawn time, including
/// child environment overrides, without changing its suspended/Job ordering.
pub fn background_command(program: impl AsRef<OsStr>) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(cccc_windows_process::background_creation_flags(&command, 0));
    }
    // Keep the same binding on every platform, without conditional public APIs.
    #[cfg(not(windows))]
    let _ = &mut command;
    command
}
