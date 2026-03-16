//! Connection types and local bootstrap for pet window runtimes.

use anyhow::{anyhow, Context, Result};
use reqwest::header::AUTHORIZATION;
use serde::Deserialize;

use crate::config::DesktopConfig;
use crate::token_store;

const ERR_NO_USABLE_TOKEN: &str = "no usable token in access_tokens.yaml";
const ERR_NO_ENABLED_GROUPS: &str = "no groups have desktop_pet_enabled; enable it in group settings";

/// A fully resolved connection ready to spawn a pet-window runtime.
#[derive(Debug, Clone)]
pub struct ResolvedConnection {
    pub group_id: String,
    pub group_title: String,
    pub daemon_url: String,
    pub auth_token: String,
}

impl ResolvedConnection {
    pub fn api_base_url(&self) -> String {
        self.daemon_url.trim_end_matches('/').to_string()
    }

    pub fn team_label(&self) -> String {
        if self.group_title.is_empty() {
            self.group_id.clone()
        } else {
            self.group_title.clone()
        }
    }
}

// ---------------------------------------------------------------------------
// Local bootstrap: access_tokens.yaml → groups API → desktop_pet_enabled
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct ApiEnvelope<T> {
    ok: bool,
    result: T,
}

#[derive(Debug, Deserialize)]
struct GroupsResult {
    #[serde(default)]
    groups: Vec<GroupEntry>,
}

#[derive(Debug, Deserialize)]
struct GroupEntry {
    group_id: String,
    #[serde(default)]
    title: String,
}

#[derive(Debug, Deserialize)]
struct SettingsResult {
    #[serde(default)]
    settings: SettingsDoc,
}

#[derive(Debug, Default, Deserialize)]
struct SettingsDoc {
    #[serde(default)]
    desktop_pet_enabled: Option<bool>,
}

/// Attempt local bootstrap: discover token from `access_tokens.yaml`,
/// query daemon for groups with `desktop_pet_enabled`, return connections.
pub async fn bootstrap_local(config: &DesktopConfig) -> Result<Vec<ResolvedConnection>> {
    let discovered = token_store::discover_local_token()
        .ok_or_else(|| anyhow!(ERR_NO_USABLE_TOKEN))?;

    let daemon_url = config.daemon_url.trim_end_matches('/').to_string();
    let token = &discovered.token;

    let client = reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(5))
        .build()
        .context("failed to build HTTP client")?;

    // Fetch all groups
    let groups_url = format!("{}/api/v1/groups", daemon_url);
    let resp: ApiEnvelope<GroupsResult> = client
        .get(&groups_url)
        .header(AUTHORIZATION, format!("Bearer {}", token))
        .send()
        .await
        .context("failed to fetch groups")?
        .json()
        .await
        .context("failed to parse groups response")?;

    if !resp.ok {
        return Err(anyhow!("groups endpoint returned ok=false"));
    }

    let groups = resp.result.groups;

    // Check each group's settings for desktop_pet_enabled
    let mut pet_enabled = Vec::new();
    for group in &groups {
        let settings_url = format!(
            "{}/api/v1/groups/{}/settings",
            daemon_url, group.group_id
        );
        let settings_resp: Result<ApiEnvelope<SettingsResult>, _> = async {
            client
                .get(&settings_url)
                .header(AUTHORIZATION, format!("Bearer {}", token))
                .send()
                .await?
                .json()
                .await
        }
        .await;

        let enabled = match settings_resp {
            Ok(resp) if resp.ok => {
                resp.result.settings.desktop_pet_enabled.unwrap_or(false)
            }
            _ => false,
        };

        if enabled {
            pet_enabled.push(group);
        }
    }

    resolve_enabled_connections(&pet_enabled, &daemon_url, token)
}

/// Build connections from groups that have `desktop_pet_enabled`.
/// Returns `ERR_NO_ENABLED_GROUPS` if the list is empty.
fn resolve_enabled_connections(
    enabled_groups: &[&GroupEntry],
    daemon_url: &str,
    token: &str,
) -> Result<Vec<ResolvedConnection>> {
    if enabled_groups.is_empty() {
        return Err(anyhow!(ERR_NO_ENABLED_GROUPS));
    }

    let connections = enabled_groups
        .iter()
        .map(|group| ResolvedConnection {
            group_id: group.group_id.clone(),
            group_title: if group.title.is_empty() {
                group.group_id.clone()
            } else {
                group.title.clone()
            },
            daemon_url: daemon_url.to_string(),
            auth_token: token.to_string(),
        })
        .collect();

    Ok(connections)
}

pub fn should_prompt_web_launch(error: &anyhow::Error) -> bool {
    matches!(error.to_string().as_str(), ERR_NO_USABLE_TOKEN | ERR_NO_ENABLED_GROUPS)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_base_url_trims_trailing_slash() {
        let conn = ResolvedConnection {
            group_id: "g_1".into(),
            group_title: "".into(),
            daemon_url: "http://localhost:8848/".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.api_base_url(), "http://localhost:8848");
    }

    #[test]
    fn api_base_url_handles_no_trailing_slash() {
        let conn = ResolvedConnection {
            group_id: "g_1".into(),
            group_title: "".into(),
            daemon_url: "http://localhost:8848".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.api_base_url(), "http://localhost:8848");
    }

    #[test]
    fn team_label_falls_back_to_group_id() {
        let conn = ResolvedConnection {
            group_id: "g_abc".into(),
            group_title: "".into(),
            daemon_url: "".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.team_label(), "g_abc");
    }

    #[test]
    fn team_label_uses_title_when_present() {
        let conn = ResolvedConnection {
            group_id: "g_abc".into(),
            group_title: "My Team".into(),
            daemon_url: "".into(),
            auth_token: "".into(),
        };
        assert_eq!(conn.team_label(), "My Team");
    }

    #[test]
    fn launch_prompt_errors_are_classified() {
        assert!(should_prompt_web_launch(&anyhow!(ERR_NO_USABLE_TOKEN)));
        assert!(should_prompt_web_launch(&anyhow!(ERR_NO_ENABLED_GROUPS)));
    }

    #[test]
    fn non_launch_errors_are_not_classified_as_launch_prompt() {
        assert!(!should_prompt_web_launch(&anyhow!("failed to fetch groups")));
        assert!(!should_prompt_web_launch(&anyhow!("no eligible groups found (none running)")));
    }

    /// Regression T237: multiple groups exist but none have desktop_pet_enabled
    /// → resolve_enabled_connections returns ERR_NO_ENABLED_GROUPS, 0 connections.
    /// Dev mode must NOT bypass this check.
    #[test]
    fn no_enabled_groups_returns_error_with_zero_connections() {
        // Simulate: 3 groups fetched from daemon, all with desktop_pet_enabled=false
        // → the enabled list passed to resolve is empty
        let enabled: Vec<&GroupEntry> = vec![];
        let result = resolve_enabled_connections(&enabled, "http://localhost:8848", "tok");

        assert!(result.is_err());
        let err = result.unwrap_err();
        assert_eq!(err.to_string(), ERR_NO_ENABLED_GROUPS);
        assert!(should_prompt_web_launch(&err));
    }

    /// When some groups are enabled, resolve_enabled_connections returns only those.
    #[test]
    fn only_enabled_groups_become_connections() {
        let g1 = GroupEntry { group_id: "g_1".into(), title: "Team A".into() };
        let g2 = GroupEntry { group_id: "g_2".into(), title: "Team B".into() };
        // Only g1 has desktop_pet_enabled=true
        let enabled: Vec<&GroupEntry> = vec![&g1];
        let result = resolve_enabled_connections(&enabled, "http://localhost:8848", "tok");

        let conns = result.unwrap();
        assert_eq!(conns.len(), 1);
        assert_eq!(conns[0].group_id, "g_1");
        assert_eq!(conns[0].group_title, "Team A");
        // g2 is not included even though it exists
        assert!(!conns.iter().any(|c| c.group_id == g2.group_id));
    }
}
