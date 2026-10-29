use std::ffi::OsStr;
use std::process::Command;
use windows_sys::Win32::System::Threading::{
    CREATE_NEW_CONSOLE, CREATE_NO_WINDOW, DETACHED_PROCESS,
};

/// Add desktop background policy to the caller's complete creation flags.
/// Explicit child environment settings take precedence over inherited settings.
/// Intentional detached/new-console launches retain their existing semantics.
pub fn background_creation_flags(command: &Command, creation_flags: u32) -> u32 {
    let desktop = command
        .get_envs()
        .find(|(key, _)| {
            key.to_string_lossy()
                .eq_ignore_ascii_case("ONECOLLEAGUE_DESKTOP")
        })
        .map(|(_, value)| value.map(ToOwned::to_owned))
        .unwrap_or_else(|| std::env::var_os("ONECOLLEAGUE_DESKTOP"));
    if desktop.as_deref() == Some(OsStr::new("1"))
        && creation_flags & (CREATE_NEW_CONSOLE | DETACHED_PROCESS) == 0
    {
        creation_flags | CREATE_NO_WINDOW
    } else {
        creation_flags
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn desktop_policy_preserves_explicit_flags_and_child_environment() {
        let mut command = Command::new("cmd");
        command.env("ONECOLLEAGUE_DESKTOP", "1");
        const PROCESS_GROUP: u32 = 0x200;
        assert_eq!(
            background_creation_flags(&command, PROCESS_GROUP),
            PROCESS_GROUP | CREATE_NO_WINDOW
        );
        for flags in [
            DETACHED_PROCESS | PROCESS_GROUP,
            CREATE_NEW_CONSOLE | PROCESS_GROUP,
        ] {
            assert_eq!(background_creation_flags(&command, flags), flags);
        }
        for value in ["0", "true", "1 "] {
            command.env("ONECOLLEAGUE_DESKTOP", value);
            assert_eq!(
                background_creation_flags(&command, PROCESS_GROUP),
                PROCESS_GROUP
            );
        }
        command.env_remove("ONECOLLEAGUE_DESKTOP");
        assert_eq!(background_creation_flags(&command, 0), 0);
    }
}
