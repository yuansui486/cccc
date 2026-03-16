//! Multi-window runtime registry.
//!
//! Manages pet windows keyed by `group_id`. Each window has its own
//! SSE runtime (StateAggregator + DesktopApiClient). Windows are created
//! and destroyed dynamically based on bootstrap results.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use tauri::async_runtime::JoinHandle;
use tauri::{AppHandle, Emitter, Manager, Position, Size, WebviewUrl, WebviewWindowBuilder};

use crate::bootstrap::ResolvedConnection;
use crate::notification::NotificationManager;
use crate::sse_client::{ConnectionInfo, DesktopApiClient, StreamSignal};
use crate::state_aggregator::{CatStatePayload, PanelDetails, StateAggregator};

const MARGIN: f64 = 16.0;
const PET_SIZE: f64 = 96.0;
const PET_GAP: f64 = 8.0;
const PANEL_GAP: f64 = 8.0;
const PANEL_WIDTH: f64 = 240.0;
const PANEL_MIN_HEIGHT: f64 = 96.0;

/// Per-window runtime state.
struct WindowRuntime {
    task_handle: JoinHandle<()>,
    daemon_url: String,
    auth_token: String,
    team_label: String,
}

/// Registry managing all pet windows and their runtimes.
pub struct WindowManager {
    windows: HashMap<String, WindowRuntime>,
    notifier: NotificationManager,
    latest_payloads: Arc<Mutex<HashMap<String, CatStatePayload>>>,
}

impl WindowManager {
    pub fn new() -> Self {
        Self {
            windows: HashMap::new(),
            notifier: NotificationManager::new(),
            latest_payloads: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Returns true if there are any active connections.
    pub fn has_connections(&self) -> bool {
        !self.windows.is_empty()
    }

    /// Returns true if a runtime exists for the given group (regardless of window state).
    pub fn has_runtime(&self, group_id: &str) -> bool {
        self.windows.contains_key(group_id)
    }

    /// Synchronize windows to match the given connections.
    ///
    /// - New groups → create window + start runtime
    /// - Removed groups → stop runtime + close window
    /// - Existing groups → keep as-is
    pub fn sync_windows(&mut self, app: &AppHandle, connections: Vec<ResolvedConnection>) {
        let new_ids: std::collections::HashSet<String> =
            connections.iter().map(|c| c.group_id.clone()).collect();
        let old_ids: Vec<String> = self.windows.keys().cloned().collect();

        // Remove windows for groups no longer present
        for id in &old_ids {
            if !new_ids.contains(id) {
                self.remove_connection(app, id);
            }
        }

        // Add windows for new groups
        for conn in connections {
            if !self.windows.contains_key(&conn.group_id) {
                self.ensure_pet_window(app, conn);
            }
        }

        // Re-layout all windows
        self.layout_windows(app);
    }

    /// Create a pet window and start its SSE runtime.
    fn ensure_pet_window(&mut self, app: &AppHandle, conn: ResolvedConnection) {
        let group_id = conn.group_id.clone();

        if !self.ensure_pet_window_ui(app, &group_id, &conn.team_label()) {
            return;
        }

        // Start SSE runtime for this group
        let task_handle = spawn_group_runtime(
            app.clone(),
            conn.clone(),
            self.notifier_ref(),
            self.latest_payloads_ref(),
        );

        let daemon_url = conn.daemon_url.clone();
        let auth_token = conn.auth_token.clone();
        let team_label = conn.team_label();
        self.windows.insert(
            group_id,
            WindowRuntime {
                task_handle,
                daemon_url,
                auth_token,
                team_label,
            },
        );
    }

    /// Create the pet window UI only (no runtime start).
    ///
    /// Returns `true` if the window exists (already present or just created).
    fn ensure_pet_window_ui(&self, app: &AppHandle, group_id: &str, title: &str) -> bool {
        let label = window_label(group_id);
        if app.get_webview_window(&label).is_some() {
            return true;
        }

        let result = WebviewWindowBuilder::new(app, &label, WebviewUrl::App("index.html".into()))
            .title(title)
            .inner_size(PET_SIZE, PET_SIZE)
            .decorations(false)
            .transparent(true)
            .shadow(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .visible_on_all_workspaces(true)
            .visible(true)
            .accept_first_mouse(true)
            .build();

        match result {
            Ok(_) => true,
            Err(e) => {
                eprintln!("failed to create pet window for {}: {}", group_id, e);
                false
            }
        }
    }

    /// Fully remove a group: stop runtime, close all windows, remove from registry.
    pub fn remove_connection(&mut self, app: &AppHandle, group_id: &str) {
        if let Some(runtime) = self.windows.remove(group_id) {
            runtime.task_handle.abort();
        }
        self.close_panel_window(app, group_id);
        self.clear_cached_payload(group_id);

        let label = window_label(group_id);
        if let Some(window) = app.get_webview_window(&label) {
            let _ = window.close();
        }
    }

    /// Close only UI windows (pet + panel), keeping the SSE runtime alive.
    ///
    /// Used when the desktop pet setting is toggled OFF — the watcher must
    /// stay alive to detect when the setting is toggled back ON.
    pub fn hide_pet_window_ui(&self, app: &AppHandle, group_id: &str) {
        self.close_panel_window(app, group_id);
        // NOTE: do NOT clear cached payload here — the SSE runtime keeps
        // running and reopen_pet_window() needs the cache for first-frame
        // rendering. Cache is only cleared in remove_connection().

        let label = window_label(group_id);
        if let Some(window) = app.get_webview_window(&label) {
            let _ = window.close();
        }
    }

    /// Layout all pet windows anchored to the bottom-right, tiling left.
    ///
    /// Keys are sorted so window positions remain stable across relayouts.
    fn layout_windows(&self, app: &AppHandle) {
        let mut labels: Vec<String> = self
            .windows
            .keys()
            .map(|id| window_label(id))
            .collect();
        labels.sort();

        for (index, label) in labels.iter().enumerate() {
            if let Some(window) = app.get_webview_window(label) {
                let _ = anchor_bottom_right_indexed(&window, PET_SIZE, PET_SIZE, index);
            }
        }
    }

    /// Re-create the pet window for an existing runtime (e.g. after toggle ON).
    ///
    /// The SSE watcher must already be in the registry. Only the UI window is
    /// rebuilt; the runtime is left untouched.
    pub fn reopen_pet_window(&mut self, app: &AppHandle, group_id: &str) {
        let Some(runtime) = self.windows.get(group_id) else {
            eprintln!("reopen_pet_window: no runtime for {}", group_id);
            return;
        };
        let title = runtime.team_label.clone();
        self.ensure_pet_window_ui(app, group_id, &title);
        self.layout_windows(app);

        // Emit the latest cached payload so the new window renders immediately
        // instead of staying blank until the next SSE event arrives.
        if let Some(payload) = self.latest_payload_for_group(group_id) {
            emit_group_payload(app, group_id, &payload);
        }
    }

    /// Add or update a single connection.
    ///
    /// If the group already exists, its runtime is restarted with the new connection.
    /// If it's new, a window + runtime is created. Layout is refreshed afterwards.
    pub fn upsert_connection(&mut self, app: &AppHandle, conn: ResolvedConnection) {
        let group_id = conn.group_id.clone();

        // If group already exists, tear down old runtime first
        if self.windows.contains_key(&group_id) {
            self.remove_connection(app, &group_id);
        }

        self.ensure_pet_window(app, conn);
        self.layout_windows(app);
    }

    /// Look up connection info (daemon_url, auth_token, group_id) by window label.
    pub fn connection_info_for_label(&self, label: &str) -> Option<(String, String, String)> {
        let group_id = group_id_from_any_label(label)?;
        let rt = self.windows.get(group_id)?;
        Some((rt.daemon_url.clone(), rt.auth_token.clone(), group_id.to_string()))
    }

    pub fn toggle_panel_window(&mut self, app: &AppHandle, group_id: &str) -> Result<(), String> {
        let label = panel_window_label(group_id);
        if let Some(window) = app.get_webview_window(&label) {
            return window.close().map_err(|e| e.to_string());
        }

        let runtime = self
            .windows
            .get(group_id)
            .ok_or_else(|| format!("missing pet runtime for group {}", group_id))?;

        let window = WebviewWindowBuilder::new(app, &label, WebviewUrl::App("panel.html".into()))
            .title(format!("CCCC Panel — {}", runtime.team_label))
            .inner_size(PANEL_WIDTH, PANEL_MIN_HEIGHT)
            .decorations(false)
            .transparent(true)
            .shadow(false)
            .always_on_top(true)
            .skip_taskbar(true)
            .visible_on_all_workspaces(true)
            .resizable(false)
            .visible(true)
            .focused(false)
            .build()
            .map_err(|e| e.to_string())?;

        anchor_panel_to_pet(app, group_id, &window, PANEL_WIDTH, PANEL_MIN_HEIGHT)
            .map_err(|e| e.to_string())?;

        if let Some(payload) = self.latest_payload_for_group(group_id) {
            let tagged = serde_json::json!({
                "teamName": payload.details.team_name,
                "agents": payload.details.agents,
                "actionItems": payload.details.action_items,
                "connection": payload.details.connection,
                "windowLabel": label,
            });
            let _ = app.emit_to(&label, "panel-data", tagged);
        }

        Ok(())
    }

    pub fn resize_panel_window(
        &self,
        app: &AppHandle,
        group_id: &str,
        width: f64,
        height: f64,
    ) -> Result<(), String> {
        let label = panel_window_label(group_id);
        let window = app
            .get_webview_window(&label)
            .ok_or_else(|| format!("panel window not found for group {}", group_id))?;

        let clamped_width = width.max(180.0);
        let clamped_height = height.max(PANEL_MIN_HEIGHT);
        window
            .set_size(Size::Logical(tauri::LogicalSize::new(clamped_width, clamped_height)))
            .map_err(|e| e.to_string())?;
        anchor_panel_to_pet(app, group_id, &window, clamped_width, clamped_height)
            .map_err(|e| e.to_string())
    }

    /// Clone the shared notifier so each runtime shares the same dedup state.
    fn notifier_ref(&self) -> NotificationManager {
        self.notifier.clone()
    }

    fn latest_payloads_ref(&self) -> Arc<Mutex<HashMap<String, CatStatePayload>>> {
        self.latest_payloads.clone()
    }

    fn latest_payload_for_group(&self, group_id: &str) -> Option<CatStatePayload> {
        self.latest_payloads
            .lock()
            .ok()
            .and_then(|cache| cache.get(group_id).cloned())
    }

    pub fn latest_panel_details_for_group(&self, group_id: &str) -> Option<PanelDetails> {
        self.latest_payload_for_group(group_id).map(|payload| payload.details)
    }

    fn clear_cached_payload(&self, group_id: &str) {
        if let Ok(mut cache) = self.latest_payloads.lock() {
            cache.remove(group_id);
        }
    }

    fn close_panel_window(&self, app: &AppHandle, group_id: &str) {
        let label = panel_window_label(group_id);
        if let Some(window) = app.get_webview_window(&label) {
            let _ = window.close();
        }
    }
}

/// Compute window label from group_id.
fn window_label(group_id: &str) -> String {
    format!("pet-{}", group_id)
}

fn panel_window_label(group_id: &str) -> String {
    format!("panel-{}", group_id)
}

/// Extract group_id from a window label (inverse of `window_label`).
fn group_id_from_label(label: &str) -> Option<&str> {
    label.strip_prefix("pet-")
}

fn group_id_from_panel_label(label: &str) -> Option<&str> {
    label.strip_prefix("panel-")
}

fn group_id_from_any_label(label: &str) -> Option<&str> {
    group_id_from_label(label).or_else(|| group_id_from_panel_label(label))
}

/// Anchor a window to bottom-right, offset left by `index` positions.
pub fn anchor_bottom_right_indexed(
    window: &tauri::WebviewWindow,
    win_w: f64,
    win_h: f64,
    index: usize,
) -> Result<(), Box<dyn std::error::Error>> {
    if let Some(monitor) = window.current_monitor()? {
        let screen_size = monitor.size();
        let screen_pos = monitor.position();
        let scale = monitor.scale_factor();

        let offset = index as f64 * (win_w + PET_GAP);
        let x = (screen_size.width as f64 / scale) - win_w - MARGIN - offset
            + (screen_pos.x as f64 / scale);
        let y =
            (screen_size.height as f64 / scale) - win_h - MARGIN + (screen_pos.y as f64 / scale);

        window.set_position(tauri::Position::Logical(tauri::LogicalPosition::new(x, y)))?;
    }
    Ok(())
}

fn anchor_panel_to_pet(
    app: &AppHandle,
    group_id: &str,
    panel_window: &tauri::WebviewWindow,
    width: f64,
    height: f64,
) -> Result<(), Box<dyn std::error::Error>> {
    let pet_window = app
        .get_webview_window(&window_label(group_id))
        .ok_or_else(|| format!("pet window missing for group {}", group_id))?;
    let monitor = pet_window.current_monitor()?.ok_or("missing monitor")?;
    let scale = monitor.scale_factor();
    let pet_pos = pet_window.outer_position()?;

    let x = (pet_pos.x as f64 / scale) + PET_SIZE - width;
    let y = (pet_pos.y as f64 / scale) - height - PANEL_GAP;
    panel_window.set_position(Position::Logical(tauri::LogicalPosition::new(x, y)))?;
    Ok(())
}

/// Spawn the SSE runtime for a single group connection.
fn spawn_group_runtime(
    app: AppHandle,
    conn: ResolvedConnection,
    notifier: NotificationManager,
    latest_payloads: Arc<Mutex<HashMap<String, CatStatePayload>>>,
) -> JoinHandle<()> {
    tauri::async_runtime::spawn(async move {
        let info = ConnectionInfo::from_resolved(&conn);

        let Ok(api) = DesktopApiClient::new(info) else {
            return;
        };

        let mut aggregator = StateAggregator::new(conn.team_label());
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();

        let stream_api = api.clone();
        tauri::async_runtime::spawn(async move {
            stream_api.run_stream_loop(tx).await;
        });

        let group_id = conn.group_id.clone();
        while let Some(signal) = rx.recv().await {
            match signal {
                StreamSignal::Connected => {
                    if let Ok(Some(title)) = api.fetch_group_title().await {
                        aggregator.set_team_name(title);
                    }
                    if let Ok(state) = api.fetch_group_state().await {
                        aggregator.set_group_state(state);
                    }
                    if let Ok(context) = api.fetch_context().await {
                        aggregator.replace_context(context);
                    }
                    if let Ok(events) = api.fetch_recent_obligations(120).await {
                        aggregator.reconcile_obligations(&events);
                    }
                    let payload = aggregator.payload(true, "connected");
                    cache_group_payload(&latest_payloads, &group_id, &payload);
                    notifier.check_and_notify(&app, &group_id, &payload);
                    emit_group_payload(&app, &group_id, &payload);

                    // First-frame guard: if desktop_pet was disabled before we
                    // connected, hide the window immediately so a stale deep-link
                    // launch doesn't leave an orphan window.
                    if let Ok(false) = api.fetch_desktop_pet_enabled().await {
                        let _ = app.emit("desktop-pet-disabled", group_id.clone());
                    }
                }
                StreamSignal::ContextChanged => {
                    if let Ok(context) = api.fetch_context().await {
                        aggregator.replace_context(context);
                        let payload = aggregator.payload(true, "connected");
                        cache_group_payload(&latest_payloads, &group_id, &payload);
                        notifier.check_and_notify(&app, &group_id, &payload);
                        emit_group_payload(&app, &group_id, &payload);
                    }
                }
                StreamSignal::ObligationChanged => {
                    if let Ok(events) = api.fetch_recent_obligations(120).await {
                        aggregator.reconcile_obligations(&events);
                        let payload = aggregator.payload(true, "connected");
                        cache_group_payload(&latest_payloads, &group_id, &payload);
                        notifier.check_and_notify(&app, &group_id, &payload);
                        emit_group_payload(&app, &group_id, &payload);
                    }
                }
                StreamSignal::GroupStateChanged => {
                    if let Ok(state) = api.fetch_group_state().await {
                        aggregator.set_group_state(state);
                        let payload = aggregator.payload(true, "connected");
                        cache_group_payload(&latest_payloads, &group_id, &payload);
                        emit_group_payload(&app, &group_id, &payload);
                    }
                }
                StreamSignal::GroupStopped => {
                    let _ = app.emit("group-stopped", group_id.clone());
                    break;
                }
                StreamSignal::DesktopPetSettingChanged => {
                    match api.fetch_desktop_pet_enabled().await {
                        Ok(false) => {
                            // Hide UI but keep this watcher alive so we can
                            // detect when the setting is toggled back ON.
                            let _ = app.emit("desktop-pet-disabled", group_id.clone());
                        }
                        Ok(true) => {
                            // Setting toggled ON — rebuild window if it was closed.
                            let pet_label = format!("pet-{}", group_id);
                            if app.get_webview_window(&pet_label).is_none() {
                                let _ = app.emit("desktop-pet-enabled", group_id.clone());
                            }
                        }
                        Err(error) => {
                            // GET failed — keep current state as-is.
                            // The next SSE event or reconnect will retry.
                            eprintln!(
                                "failed to fetch desktop_pet_enabled for {}: {}; keeping current state",
                                group_id, error
                            );
                        }
                    }
                }
                StreamSignal::Reconnecting { reason, delay } => {
                    let message = format!("reconnecting in {}s: {}", delay.as_secs(), reason);
                    let payload = aggregator.payload(false, message);
                    cache_group_payload(&latest_payloads, &group_id, &payload);
                    emit_group_payload(&app, &group_id, &payload);
                }
            }
        }
    })
}

/// Emit payload events scoped to a specific group's pet window.
///
/// Uses `emit_to()` instead of `emit()` because Tauri v2's `Emitter::emit()`
/// broadcasts to ALL windows regardless of which object it's called on.
fn emit_group_payload(app: &AppHandle, group_id: &str, payload: &CatStatePayload) {
    let pet = window_label(group_id);
    let panel = panel_window_label(group_id);

    // Build tagged payloads with windowLabel so each frontend can filter
    // events that belong to its own window — a reliable cross-version
    // approach that doesn't depend on emit_to target matching.
    let cat_payload = serde_json::json!({
        "state": payload.state,
        "details": payload.details,
        "windowLabel": pet,
    });
    let pet_panel_payload = serde_json::json!({
        "teamName": payload.details.team_name,
        "agents": payload.details.agents,
        "actionItems": payload.details.action_items,
        "connection": payload.details.connection,
        "windowLabel": pet,
    });
    let panel_payload = serde_json::json!({
        "teamName": payload.details.team_name,
        "agents": payload.details.agents,
        "actionItems": payload.details.action_items,
        "connection": payload.details.connection,
        "windowLabel": panel,
    });

    if app.get_webview_window(&pet).is_some() {
        let _ = app.emit_to(&pet, "cat-state-changed", cat_payload);
        let _ = app.emit_to(&pet, "panel-data", pet_panel_payload);
    }
    if app.get_webview_window(&panel).is_some() {
        let _ = app.emit_to(&panel, "panel-data", panel_payload);
    }
}

fn cache_group_payload(
    cache: &Arc<Mutex<HashMap<String, CatStatePayload>>>,
    group_id: &str,
    payload: &CatStatePayload,
) {
    if let Ok(mut inner) = cache.lock() {
        inner.insert(group_id.to_string(), payload.clone());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_label_format() {
        assert_eq!(window_label("g_abc123"), "pet-g_abc123");
        assert_eq!(panel_window_label("g_abc123"), "panel-g_abc123");
        assert_eq!(window_label(""), "pet-");
    }

    #[test]
    fn group_id_from_label_roundtrip() {
        assert_eq!(group_id_from_label("pet-g_abc123"), Some("g_abc123"));
        assert_eq!(group_id_from_panel_label("panel-g_abc123"), Some("g_abc123"));
        assert_eq!(group_id_from_any_label("panel-g_abc123"), Some("g_abc123"));
        assert_eq!(group_id_from_label("pet-"), Some(""));
        assert_eq!(group_id_from_label("other-label"), None);
        assert_eq!(group_id_from_label(""), None);
    }

    #[test]
    fn new_manager_has_no_connections() {
        let wm = WindowManager::new();
        assert!(!wm.has_connections());
        assert!(!wm.has_runtime("g_abc123"));
    }

    #[test]
    fn resolved_connection_team_label() {
        let conn = ResolvedConnection {
            group_id: "g_1".to_string(),
            group_title: "My Team".to_string(),
            daemon_url: "http://localhost:8848".to_string(),
            auth_token: "tok".to_string(),
        };
        assert_eq!(conn.team_label(), "My Team");

        let conn2 = ResolvedConnection {
            group_id: "g_2".to_string(),
            group_title: "".to_string(),
            daemon_url: "http://localhost:8848".to_string(),
            auth_token: "".to_string(),
        };
        assert_eq!(conn2.team_label(), "g_2");
    }
}
