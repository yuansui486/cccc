//! Deep-link URL parsing and launch payload handling.
//!
//! The desktop pet is launched via `cccc-pet://launch?daemon_url=...&token=...&group_id=...`.
//! This module provides a pure parsing function and the unified entry point
//! for handling incoming launch payloads.

use url::Url;

use crate::bootstrap::ResolvedConnection;

/// Parsed launch payload from a deep-link URL.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchPayload {
    pub daemon_url: String,
    pub token: String,
    pub group_id: String,
    pub group_title: String,
}

/// Parse a `cccc-pet://launch?...` URL into a `LaunchPayload`.
///
/// Returns `None` if the URL is malformed or missing required fields.
pub fn parse_launch_url(raw: &str) -> Option<LaunchPayload> {
    let url = Url::parse(raw).ok()?;

    if url.scheme() != "cccc-pet" {
        return None;
    }
    if url.host_str() != Some("launch") {
        return None;
    }

    let mut daemon_url = String::new();
    let mut token = String::new();
    let mut group_id = String::new();
    let mut group_title = String::new();

    for (key, value) in url.query_pairs() {
        match key.as_ref() {
            "daemon_url" => daemon_url = value.into_owned(),
            "token" => token = value.into_owned(),
            "group_id" => group_id = value.into_owned(),
            "group_title" => group_title = value.into_owned(),
            _ => {}
        }
    }

    // Validate and normalize
    let payload = validate_payload(LaunchPayload {
        daemon_url,
        token,
        group_id,
        group_title,
    })?;

    Some(payload)
}

/// Validate and normalize a launch payload.
/// Returns `None` if required fields are empty after trimming.
fn validate_payload(mut payload: LaunchPayload) -> Option<LaunchPayload> {
    payload.daemon_url = payload
        .daemon_url
        .trim()
        .trim_end_matches('/')
        .to_string();
    payload.token = payload.token.trim().to_string();
    payload.group_id = payload.group_id.trim().to_string();
    payload.group_title = payload.group_title.trim().to_string();

    if payload.daemon_url.is_empty() || payload.group_id.is_empty() {
        return None;
    }

    Some(payload)
}

impl LaunchPayload {
    /// Convert to a `ResolvedConnection` for the window manager.
    pub fn to_connection(&self) -> ResolvedConnection {
        ResolvedConnection {
            group_id: self.group_id.clone(),
            group_title: if self.group_title.is_empty() {
                self.group_id.clone()
            } else {
                self.group_title.clone()
            },
            daemon_url: self.daemon_url.clone(),
            auth_token: self.token.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_valid_launch_url() {
        let url = "cccc-pet://launch?daemon_url=http%3A%2F%2Flocalhost%3A8848&token=tok123&group_id=g_abc&group_title=My%20Team";
        let payload = parse_launch_url(url).unwrap();
        assert_eq!(payload.daemon_url, "http://localhost:8848");
        assert_eq!(payload.token, "tok123");
        assert_eq!(payload.group_id, "g_abc");
        assert_eq!(payload.group_title, "My Team");
    }

    #[test]
    fn trims_trailing_slash_from_daemon_url() {
        let url = "cccc-pet://launch?daemon_url=http%3A%2F%2Flocalhost%3A8848%2F&token=t&group_id=g_1";
        let payload = parse_launch_url(url).unwrap();
        assert_eq!(payload.daemon_url, "http://localhost:8848");
    }

    #[test]
    fn rejects_missing_daemon_url() {
        let url = "cccc-pet://launch?token=t&group_id=g_1";
        assert!(parse_launch_url(url).is_none());
    }

    #[test]
    fn rejects_missing_group_id() {
        let url = "cccc-pet://launch?daemon_url=http%3A%2F%2Flocalhost&token=t";
        assert!(parse_launch_url(url).is_none());
    }

    #[test]
    fn rejects_wrong_scheme() {
        let url = "https://launch?daemon_url=http%3A%2F%2Flocalhost&token=t&group_id=g_1";
        assert!(parse_launch_url(url).is_none());
    }

    #[test]
    fn rejects_wrong_host() {
        let url = "cccc-pet://settings?daemon_url=http%3A%2F%2Flocalhost&token=t&group_id=g_1";
        assert!(parse_launch_url(url).is_none());
    }

    #[test]
    fn allows_empty_token() {
        let url = "cccc-pet://launch?daemon_url=http%3A%2F%2Flocalhost&token=&group_id=g_1";
        let payload = parse_launch_url(url).unwrap();
        assert_eq!(payload.token, "");
    }

    #[test]
    fn allows_missing_group_title() {
        let url = "cccc-pet://launch?daemon_url=http%3A%2F%2Flocalhost&token=t&group_id=g_1";
        let payload = parse_launch_url(url).unwrap();
        assert_eq!(payload.group_title, "");
    }

    #[test]
    fn to_connection_uses_group_id_as_fallback_title() {
        let payload = LaunchPayload {
            daemon_url: "http://localhost".into(),
            token: "t".into(),
            group_id: "g_abc".into(),
            group_title: "".into(),
        };
        let conn = payload.to_connection();
        assert_eq!(conn.group_title, "g_abc");
    }

    #[test]
    fn to_connection_uses_group_title_when_present() {
        let payload = LaunchPayload {
            daemon_url: "http://localhost".into(),
            token: "t".into(),
            group_id: "g_abc".into(),
            group_title: "My Team".into(),
        };
        let conn = payload.to_connection();
        assert_eq!(conn.group_title, "My Team");
    }

    #[test]
    fn rejects_whitespace_only_fields() {
        let url = "cccc-pet://launch?daemon_url=%20%20&token=t&group_id=g_1";
        assert!(parse_launch_url(url).is_none());
    }
}
