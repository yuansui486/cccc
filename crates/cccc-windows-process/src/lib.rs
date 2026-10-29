#![deny(unsafe_code)]

#[cfg(windows)]
mod desktop;
#[cfg(windows)]
pub use desktop::background_creation_flags;

#[cfg(windows)]
#[allow(unsafe_code)]
mod suspended;
#[cfg(windows)]
pub use suspended::{SuspendedChild, spawn_suspended};

#[cfg(all(test, windows))]
mod tests;
