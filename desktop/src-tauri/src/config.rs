use std::env;
use std::fs;
use std::path::PathBuf;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

const CONFIG_FILE_NAME: &str = "desktop-pet.json";
const DEFAULT_DAEMON_URL: &str = "http://127.0.0.1:8848";

/// Startup mode for the desktop pet application.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum StartupMode {
    /// Local daemon discovered via CCCC_HOME / access_tokens.yaml.
    Local,
    /// Remote daemon with user-provided URL + access token (stored in keychain).
    Remote,
}

impl Default for StartupMode {
    fn default() -> Self {
        Self::Local
    }
}

/// Persisted desktop-pet configuration.
///
/// After the multi-window refactor only `daemon_url` and `last_mode` are used.
/// Legacy fields (`group_id`, `auth_token`) are kept for deserialization compat
/// but will be removed once the full bootstrap pipeline is in place.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct DesktopConfig {
    pub daemon_url: String,
    #[serde(default)]
    pub last_mode: StartupMode,
    // --- legacy fields (kept for backward compat during migration) ---
    #[serde(default)]
    pub group_id: String,
    #[serde(default)]
    pub auth_token: String,
}

impl Default for DesktopConfig {
    fn default() -> Self {
        Self {
            daemon_url: normalize_daemon_url(env::var("CCCC_DESKTOP_DAEMON_URL").ok()),
            last_mode: StartupMode::default(),
            group_id: env::var("CCCC_DESKTOP_GROUP_ID")
                .ok()
                .map(|value| value.trim().to_string())
                .unwrap_or_default(),
            auth_token: env::var("CCCC_DESKTOP_AUTH_TOKEN")
                .ok()
                .map(|value| value.trim().to_string())
                .unwrap_or_default(),
        }
    }
}

impl DesktopConfig {
    pub fn load(app: &AppHandle) -> Result<Self> {
        let path = config_path(app)?;
        if !path.exists() {
            return Ok(Self::default());
        }

        let raw = fs::read_to_string(&path)
            .with_context(|| format!("failed to read desktop config {}", path.display()))?;
        let mut config: Self =
            serde_json::from_str(&raw).context("failed to parse desktop config json")?;
        config.daemon_url = normalize_daemon_url(Some(config.daemon_url));
        config.group_id = config.group_id.trim().to_string();
        config.auth_token = config.auth_token.trim().to_string();
        Ok(config)
    }

    pub fn save(&self, app: &AppHandle) -> Result<()> {
        let path = config_path(app)?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("failed to create config dir {}", parent.display()))?;
        }
        let raw =
            serde_json::to_string_pretty(self).context("failed to serialize desktop config")?;
        fs::write(&path, raw)
            .with_context(|| format!("failed to write desktop config {}", path.display()))
    }

    pub fn is_ready(&self) -> bool {
        !self.daemon_url.is_empty() && !self.group_id.is_empty()
    }

    pub fn api_base_url(&self) -> String {
        self.daemon_url.trim_end_matches('/').to_string()
    }

}

fn normalize_daemon_url(value: Option<String>) -> String {
    let raw = value
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .unwrap_or_else(|| DEFAULT_DAEMON_URL.to_string());
    raw.trim_end_matches('/').to_string()
}

fn config_path(app: &AppHandle) -> Result<PathBuf> {
    let dir = app
        .path()
        .app_config_dir()
        .context("failed to resolve app config dir")?;
    Ok(dir.join(CONFIG_FILE_NAME))
}

#[cfg(test)]
mod tests {
    use super::{normalize_daemon_url, DesktopConfig, StartupMode, DEFAULT_DAEMON_URL};

    #[test]
    fn normalizes_trailing_slash() {
        assert_eq!(
            normalize_daemon_url(Some("http://127.0.0.1:8848/".to_string())),
            "http://127.0.0.1:8848"
        );
    }

    #[test]
    fn default_config_uses_fallback_url() {
        let config = DesktopConfig {
            daemon_url: normalize_daemon_url(None),
            last_mode: StartupMode::Local,
            group_id: String::new(),
            auth_token: String::new(),
        };
        assert_eq!(config.daemon_url, DEFAULT_DAEMON_URL);
        assert!(!config.is_ready());
    }

    #[test]
    fn default_mode_is_local() {
        let config = DesktopConfig::default();
        assert_eq!(config.last_mode, StartupMode::Local);
    }

    #[test]
    fn deserializes_legacy_format_without_mode() {
        let json = r#"{"daemonUrl":"http://example.com:8848","groupId":"g_1","authToken":"tok"}"#;
        let config: DesktopConfig = serde_json::from_str(json).unwrap();
        assert_eq!(config.daemon_url, "http://example.com:8848");
        assert_eq!(config.group_id, "g_1");
        assert_eq!(config.last_mode, StartupMode::Local); // default
    }
}
