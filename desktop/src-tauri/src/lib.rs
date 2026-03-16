mod bootstrap;
mod config;
mod deep_link;
mod keychain;
mod notification;
mod sse_client;
mod state_aggregator;
mod token_store;
mod window_manager;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use config::DesktopConfig;
use deep_link::LaunchPayload;
use state_aggregator::{PanelDetails, StateAggregator};
use tauri::{AppHandle, Emitter, Listener, Manager, State};
use tauri_plugin_autostart::MacosLauncher;
use window_manager::WindowManager;

const WAITING_FOR_WEB_LAUNCH_MESSAGE: &str = "请从 Web UI 点击 Launch 启动";

#[derive(Clone)]
struct RuntimeState {
    config: Arc<Mutex<DesktopConfig>>,
    window_manager: Arc<Mutex<WindowManager>>,
    bootstrap_started: Arc<AtomicBool>,
}

impl RuntimeState {
    fn new(config: DesktopConfig) -> Self {
        Self {
            config: Arc::new(Mutex::new(config)),
            window_manager: Arc::new(Mutex::new(WindowManager::new())),
            bootstrap_started: Arc::new(AtomicBool::new(false)),
        }
    }

    fn current_config(&self) -> DesktopConfig {
        self.config
            .lock()
            .expect("desktop runtime config mutex poisoned")
            .clone()
    }

    fn mark_bootstrap_started(&self) -> bool {
        !self.bootstrap_started.swap(true, Ordering::SeqCst)
    }
}

// ---------------------------------------------------------------------------
// IPC commands
// ---------------------------------------------------------------------------

/// Open the CCCC Web UI in the default browser.
///
/// Resolves daemon_url, auth_token, and group_id from the calling window's
/// connection info so the browser opens with authentication pre-filled.
#[tauri::command]
fn open_cccc_web(window: tauri::WebviewWindow, state: State<'_, RuntimeState>) -> Result<(), String> {
    let label = window.label().to_string();
    let (daemon_url, token, group_id) = state
        .window_manager
        .lock()
        .ok()
        .and_then(|wm| wm.connection_info_for_label(&label))
        .unwrap_or_else(|| {
            let config = state.current_config();
            (config.api_base_url(), String::new(), String::new())
        });

    let base = daemon_url.trim_end_matches('/');
    let mut url = url::Url::parse(&format!("{}/ui/", base))
        .map_err(|e| format!("invalid daemon URL: {}", e))?;

    if !token.is_empty() {
        url.query_pairs_mut().append_pair("token", &token);
    }
    if !group_id.is_empty() {
        url.query_pairs_mut().append_pair("group_id", &group_id);
    }

    open::that(url.as_str()).map_err(|e| e.to_string())
}

#[tauri::command]
fn toggle_panel_window(window: tauri::WebviewWindow, state: State<'_, RuntimeState>) -> Result<(), String> {
    let label = window.label().to_string();
    let group_id = label
        .strip_prefix("pet-")
        .or_else(|| label.strip_prefix("panel-"))
        .ok_or_else(|| format!("unsupported window label {}", label))?;

    let mut manager = state
        .window_manager
        .lock()
        .map_err(|_| "window manager mutex poisoned".to_string())?;
    manager.toggle_panel_window(window.app_handle(), group_id)
}

#[tauri::command]
fn close_pet_window(window: tauri::WebviewWindow, state: State<'_, RuntimeState>) -> Result<(), String> {
    let label = window.label().to_string();
    let group_id = label
        .strip_prefix("pet-")
        .ok_or_else(|| format!("unsupported pet window label {}", label))?;

    let manager = state
        .window_manager
        .lock()
        .map_err(|_| "window manager mutex poisoned".to_string())?;
    manager.hide_pet_window_ui(window.app_handle(), group_id);
    Ok(())
}

#[tauri::command]
fn resize_panel_window(
    window: tauri::WebviewWindow,
    state: State<'_, RuntimeState>,
    width: f64,
    height: f64,
) -> Result<(), String> {
    let label = window.label().to_string();
    let group_id = label
        .strip_prefix("panel-")
        .ok_or_else(|| format!("unsupported panel label {}", label))?;

    let manager = state
        .window_manager
        .lock()
        .map_err(|_| "window manager mutex poisoned".to_string())?;
    manager.resize_panel_window(window.app_handle(), group_id, width, height)
}

#[tauri::command]
fn get_panel_data(
    window: tauri::WebviewWindow,
    state: State<'_, RuntimeState>,
) -> Result<Option<PanelDetails>, String> {
    let label = window.label().to_string();
    let group_id = label
        .strip_prefix("panel-")
        .or_else(|| label.strip_prefix("pet-"))
        .ok_or_else(|| format!("unsupported panel label {}", label))?;

    let manager = state
        .window_manager
        .lock()
        .map_err(|_| "window manager mutex poisoned".to_string())?;
    Ok(manager.latest_panel_details_for_group(group_id))
}


// ---------------------------------------------------------------------------
// Application lifecycle
// ---------------------------------------------------------------------------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default();

    // single-instance must be registered first
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(
            |app, args, _cwd| {
                // On Windows/Linux, deep-link URLs arrive via args in the second instance
                for arg in &args {
                    if let Some(payload) = deep_link::parse_launch_url(arg) {
                        if let Some(state) = app.try_state::<RuntimeState>() {
                            handle_launch_payload(app, &state, payload);
                        }
                    }
                }
            },
        ));
    }

    builder
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            Some(vec!["--started-at-login"]),
        ))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            open_cccc_web,
            toggle_panel_window,
            close_pet_window,
            resize_panel_window,
            get_panel_data,
        ])
        .setup(|app| {
            let config = DesktopConfig::load(&app.handle()).unwrap_or_default();
            let state = RuntimeState::new(config.clone());
            app.manage(state.clone());

            // Emit initial empty state
            emit_empty_state(&app.handle(), "initializing");

            // Register deep-link scheme (needed for dev on Linux/Windows)
            #[cfg(any(target_os = "linux", all(debug_assertions, windows)))]
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let _ = app.deep_link().register_all();
            }

            // Listen for deep-link events (macOS)
            {
                use tauri_plugin_deep_link::DeepLinkExt;
                let app_handle = app.handle().clone();
                app.deep_link().on_open_url(move |event| {
                    for url in event.urls() {
                        if let Some(payload) = deep_link::parse_launch_url(url.as_str()) {
                            if let Some(state) = app_handle.try_state::<RuntimeState>() {
                                handle_launch_payload(&app_handle, &state, payload);
                            }
                        }
                    }
                });
            }

            // Listen for group-stopped events from SSE runtime
            {
                let state_clone = state.clone();
                let app_handle = app.handle().clone();
                app.listen("group-stopped", move |event| {
                    if let Ok(group_id) = serde_json::from_str::<String>(event.payload()) {
                        if let Ok(mut wm) = state_clone.window_manager.lock() {
                            wm.remove_connection(&app_handle, &group_id);
                        }
                    }
                });
            }

            // Listen for desktop pet disable — hide UI only, keep SSE watcher alive
            {
                let state_clone = state.clone();
                let app_handle = app.handle().clone();
                app.listen("desktop-pet-disabled", move |event| {
                    if let Ok(group_id) = serde_json::from_str::<String>(event.payload()) {
                        if let Ok(wm) = state_clone.window_manager.lock() {
                            wm.hide_pet_window_ui(&app_handle, &group_id);
                        }
                    }
                });
            }

            // Listen for desktop pet enable — reopen UI window from existing runtime
            {
                let state_clone = state.clone();
                let app_handle = app.handle().clone();
                app.listen("desktop-pet-enabled", move |event| {
                    if let Ok(group_id) = serde_json::from_str::<String>(event.payload()) {
                        if let Ok(mut wm) = state_clone.window_manager.lock() {
                            wm.reopen_pet_window(&app_handle, &group_id);
                        }
                    }
                });
            }

            // Check if first instance was launched via deep-link argv
            // (e.g. app was not running, OS passes URL as CLI argument)
            for arg in std::env::args() {
                if let Some(payload) = deep_link::parse_launch_url(&arg) {
                    handle_launch_payload(&app.handle(), &state, payload);
                }
            }

            // Start bootstrap (restores last launch state or shows empty)
            // Skip if argv already handled a launch payload
            if state.window_manager.lock().map_or(true, |wm| !wm.has_connections()) {
                run_bootstrap_flow(app.handle().clone(), state);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Handle an incoming deep-link launch payload.
///
/// This is the unified entry point called from both the deep-link event listener
/// (macOS) and the single-instance callback (Windows/Linux).
fn handle_launch_payload(app: &AppHandle, state: &RuntimeState, payload: LaunchPayload) {
    // Hide static pet window
    if let Some(static_pet) = app.get_webview_window("pet") {
        let _ = static_pet.hide();
    }

    // Save last launch state for autostart recovery
    let last_launch = keychain::LastLaunchState {
        daemon_url: payload.daemon_url.clone(),
        token: payload.token.clone(),
        group_id: payload.group_id.clone(),
        group_title: payload.group_title.clone(),
    };
    let _ = keychain::save_last_launch(&last_launch);

    // Upsert into window manager
    let conn = payload.to_connection();
    if let Ok(mut wm) = state.window_manager.lock() {
        wm.upsert_connection(app, conn);
    }
}

/// Kick off the bootstrap flow — restore from last launch state or local discovery.
///
/// Priority: deep-link argv (handled before this) > keychain cache > local access_tokens.yaml > waiting
fn run_bootstrap_flow(app: AppHandle, state: RuntimeState) {
    if !state.mark_bootstrap_started() {
        return;
    }

    // 1. Try to restore from last launch state (autostart recovery).
    //    Skip keychain restore when local token discovery is available — local
    //    bootstrap finds ALL pet-enabled groups, while keychain only stores
    //    the single group from the last deep-link launch.
    if let Ok(Some(last_launch)) = keychain::load_last_launch() {
        if token_store::discover_local_token().is_none() {
            // Remote mode: no local tokens, use keychain to restore
            let payload = LaunchPayload {
                daemon_url: last_launch.daemon_url,
                token: last_launch.token,
                group_id: last_launch.group_id,
                group_title: last_launch.group_title,
            };
            handle_launch_payload(&app, &state, payload);
            return;
        }
        // Local mode: skip keychain, fall through to bootstrap_local()
    }

    // 2. Local fallback: discover token from access_tokens.yaml, query daemon for pet-enabled groups
    //    This is async so we spawn a task.
    let config = state.current_config();
    tauri::async_runtime::spawn(async move {
        emit_empty_state(&app, "discovering local groups...");

        match bootstrap::bootstrap_local(&config).await {
            Ok(connections) => {
                if let Some(static_pet) = app.get_webview_window("pet") {
                    let _ = static_pet.hide();
                }
                if let Ok(mut wm) = state.window_manager.lock() {
                    wm.sync_windows(&app, connections);
                }
            }
            Err(e) => {
                eprintln!("[bootstrap] failed: {}", e);

                // The static pet window starts with `visible: false` (tauri.conf.json),
                // so we must show() it before emitting the error state — otherwise
                // the user sees the app "disappear".
                if let Some(static_pet) = app.get_webview_window("pet") {
                    let _ = static_pet.show();
                }

                let message = if bootstrap::should_prompt_web_launch(&e) {
                    WAITING_FOR_WEB_LAUNCH_MESSAGE.to_string()
                } else {
                    e.to_string()
                };
                emit_empty_state(&app, &message);
            }
        }
    });
}

/// Emit an empty/connecting state to the frontend.
fn emit_empty_state(app: &AppHandle, message: &str) {
    let aggregator = StateAggregator::new("CCCC Desktop Pet".to_string());
    let payload = aggregator.payload(false, message);
    let _ = app.emit("cat-state-changed", payload.clone());
    let _ = app.emit("panel-data", payload.details.clone());
}
