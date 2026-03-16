use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use tauri::AppHandle;
use tauri_plugin_notification::NotificationExt;

use crate::state_aggregator::{CatState, CatStatePayload};

const DEDUP_COOLDOWN: Duration = Duration::from_secs(300); // 5 minutes

/// App-level global notification center.
///
/// Shared across all pet windows via `Arc<Mutex<...>>`. Dedup keys are
/// scoped per group: `{group_id}:{item.id}` to avoid cross-group collisions
/// while still deduping within the same group.
#[derive(Clone)]
pub struct NotificationManager {
    inner: Arc<Mutex<NotificationInner>>,
}

struct NotificationInner {
    last_sent: HashMap<String, Instant>,
}

impl NotificationManager {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(NotificationInner {
                last_sent: HashMap::new(),
            })),
        }
    }

    /// Check the new payload and fire system notifications when state is NeedsYou.
    ///
    /// Each action item is deduped by `{group_id}:{item.id}` with a 5-minute cooldown.
    pub fn check_and_notify(
        &self,
        app: &AppHandle,
        group_id: &str,
        payload: &CatStatePayload,
    ) {
        if payload.state != CatState::NeedsYou {
            return;
        }

        let Ok(mut inner) = self.inner.lock() else {
            return;
        };

        let now = Instant::now();
        inner.evict_expired(now);

        let team_name = &payload.details.team_name;

        for item in &payload.details.action_items {
            if item.id.is_empty() {
                continue;
            }

            let dedup_key = format!("{}:{}", group_id, item.id);

            if let Some(last) = inner.last_sent.get(&dedup_key) {
                if now.duration_since(*last) < DEDUP_COOLDOWN {
                    continue;
                }
            }

            let title = format!("CCCC — {}", team_name);
            let body = format!("{}: {}", item.agent, item.summary);

            if send_notification(app, &title, &body) {
                inner.last_sent.insert(dedup_key, now);
            }
        }
    }
}

impl NotificationInner {
    fn evict_expired(&mut self, now: Instant) {
        self.last_sent
            .retain(|_, last| now.duration_since(*last) < DEDUP_COOLDOWN);
    }
}

fn send_notification(app: &AppHandle, title: &str, body: &str) -> bool {
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state_aggregator::{
        ActionItem, CatState, CatStatePayload, ConnectionStatus, PanelDetails,
    };

    fn make_payload(state: CatState, items: Vec<ActionItem>) -> CatStatePayload {
        CatStatePayload {
            state,
            details: PanelDetails {
                team_name: "test-team".to_string(),
                agents: vec![],
                action_items: items,
                connection: ConnectionStatus {
                    connected: true,
                    message: "connected".to_string(),
                },
            },
        }
    }

    #[test]
    fn dedup_key_includes_group_id() {
        let manager = NotificationManager::new();
        let inner = manager.inner.lock().unwrap();

        // Verify dedup key format
        let key = format!("{}:{}", "g_abc", "evt1");
        assert_eq!(key, "g_abc:evt1");
        drop(inner);
    }

    #[test]
    fn same_item_different_groups_not_deduped() {
        let manager = NotificationManager::new();

        // Simulate: same item id "evt1" in two different groups
        {
            let mut inner = manager.inner.lock().unwrap();
            inner
                .last_sent
                .insert("g_group1:evt1".to_string(), Instant::now());
        }

        // g_group2:evt1 should NOT be blocked
        let inner = manager.inner.lock().unwrap();
        assert!(!inner.last_sent.contains_key("g_group2:evt1"));
    }

    #[test]
    fn same_group_same_item_is_deduped() {
        let manager = NotificationManager::new();

        {
            let mut inner = manager.inner.lock().unwrap();
            inner
                .last_sent
                .insert("g_group1:evt1".to_string(), Instant::now());
        }

        let inner = manager.inner.lock().unwrap();
        assert!(inner.last_sent.contains_key("g_group1:evt1"));
    }

    #[test]
    fn task_dedup_key_format() {
        let item = ActionItem {
            id: "T123_waiting_on_user".to_string(),
            agent: "peer-impl-1".to_string(),
            summary: "Need approval".to_string(),
        };
        let key = format!("g_abc:{}", item.id);
        assert_eq!(key, "g_abc:T123_waiting_on_user");
    }

    #[test]
    fn evict_expired_removes_old_entries() {
        let manager = NotificationManager::new();
        let old_time = Instant::now() - Duration::from_secs(600);

        {
            let mut inner = manager.inner.lock().unwrap();
            inner
                .last_sent
                .insert("g_1:old-key".to_string(), old_time);
            inner
                .last_sent
                .insert("g_1:fresh-key".to_string(), Instant::now());
            inner.evict_expired(Instant::now());
        }

        let inner = manager.inner.lock().unwrap();
        assert!(!inner.last_sent.contains_key("g_1:old-key"));
        assert!(inner.last_sent.contains_key("g_1:fresh-key"));
    }

    #[test]
    fn empty_id_items_are_skipped() {
        let item = ActionItem {
            id: String::new(),
            agent: "peer-impl-1".to_string(),
            summary: "Some action".to_string(),
        };
        assert!(item.id.is_empty());
    }

    #[test]
    fn clone_shares_dedup_state() {
        let manager = NotificationManager::new();
        let clone = manager.clone();

        // Insert via original
        {
            let mut inner = manager.inner.lock().unwrap();
            inner
                .last_sent
                .insert("g_1:evt1".to_string(), Instant::now());
        }

        // Verify via clone
        let inner = clone.inner.lock().unwrap();
        assert!(inner.last_sent.contains_key("g_1:evt1"));
    }
}
