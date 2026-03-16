//! OS keychain operations for remote-mode access token storage.
//!
//! Uses the `keyring` crate for cross-platform secure credential storage
//! (macOS Keychain / Windows Credential Manager / Linux Secret Service).

use anyhow::{Context, Result};
use keyring::Entry;
use serde::{Deserialize, Serialize};

const SERVICE_NAME: &str = "com.cccc.desktop-pet";


const LAST_LAUNCH_KEY: &str = "last_launch";

/// Persisted state for autostart recovery.
///
/// When the pet is launched via deep-link, we save the connection info
/// so it can restore automatically on next cold start.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LastLaunchState {
    pub daemon_url: String,
    pub token: String,
    pub group_id: String,
    pub group_title: String,
}

/// Save last launch state to the keychain for autostart recovery.
pub fn save_last_launch(state: &LastLaunchState) -> Result<()> {
    let entry =
        Entry::new(SERVICE_NAME, LAST_LAUNCH_KEY).context("failed to create keychain entry")?;
    let json = serde_json::to_string(state).context("failed to serialize last launch state")?;
    entry
        .set_password(&json)
        .map_err(|e| anyhow::anyhow!("keychain save failed: {}", e))
}

/// Load last launch state from the keychain.
pub fn load_last_launch() -> Result<Option<LastLaunchState>> {
    let entry =
        Entry::new(SERVICE_NAME, LAST_LAUNCH_KEY).context("failed to create keychain entry")?;
    match entry.get_password() {
        Ok(json) => {
            let state: LastLaunchState =
                serde_json::from_str(&json).context("failed to parse last launch state")?;
            // Validate: must have daemon_url and group_id
            if state.daemon_url.trim().is_empty() || state.group_id.trim().is_empty() {
                Ok(None)
            } else {
                Ok(Some(state))
            }
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(anyhow::anyhow!("keychain load failed: {}", e)),
    }
}
