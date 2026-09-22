// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio;
mod api;
mod config;

use std::sync::Mutex;
use tauri::{Manager, State};
use audio::AudioRecorder;
use api::ApiClient;
use config::{AppSettings, load_settings, save_settings};

// Global state for audio recorder
struct AppState {
    recorder: Mutex<AudioRecorder>,
    api_client: Mutex<Option<ApiClient>>,
    api_url: Mutex<String>,
    settings: Mutex<AppSettings>,
}

#[tauri::command]
fn start_recording(state: State<AppState>) -> Result<String, String> {
    let mut recorder = state.recorder.lock().unwrap();
    recorder.start_recording()?;
    Ok("Recording started".to_string())
}

#[tauri::command]
fn stop_recording(state: State<AppState>) -> Result<String, String> {
    let mut recorder = state.recorder.lock().unwrap();
    let audio_data = recorder.stop_recording()?;
    Ok(audio_data)
}

#[tauri::command]
fn is_recording(state: State<AppState>) -> Result<bool, String> {
    let recorder = state.recorder.lock().unwrap();
    Ok(recorder.is_recording())
}

#[tauri::command]
async fn transcribe_audio(
    audio_data: String,
    api_url: String,
    enable_context: bool,
    enable_enhancement: bool,
    state: State<'_, AppState>,
) -> Result<String, String> {
    // Update or create API client if URL changed
    {
        let mut stored_url = state.api_url.lock().unwrap();
        let mut api_client = state.api_client.lock().unwrap();
        if api_client.is_none() || *stored_url != api_url {
            *api_client = Some(ApiClient::new(api_url.clone()));
            *stored_url = api_url.clone();
        }
    }

    // Get the client and make request
    let api_client = state.api_client.lock().unwrap();
    if let Some(client) = api_client.as_ref() {
        let response = client
            .transcribe(audio_data, enable_context, enable_enhancement)
            .await?;
        
        Ok(serde_json::to_string(&response)
            .map_err(|e| format!("Failed to serialize response: {}", e))?)
    } else {
        Err("API client not initialized".to_string())
    }
}

#[tauri::command]
async fn check_api_health(api_url: String) -> Result<String, String> {
    let client = ApiClient::new(api_url);
    client.health_check().await?;
    Ok("API is healthy".to_string())
}

#[tauri::command]
fn get_settings(state: State<AppState>) -> Result<AppSettings, String> {
    let settings = state.settings.lock().unwrap();
    Ok(settings.clone())
}

#[tauri::command]
fn save_settings_cmd(settings: AppSettings, state: State<AppState>) -> Result<String, String> {
    save_settings(&settings)?;
    
    // Update in-memory settings
    let mut app_settings = state.settings.lock().unwrap();
    *app_settings = settings;
    
    Ok("Settings saved successfully".to_string())
}

#[tauri::command]
fn reset_settings(state: State<AppState>) -> Result<AppSettings, String> {
    let default_settings = AppSettings::default();
    save_settings(&default_settings)?;
    
    // Update in-memory settings
    let mut app_settings = state.settings.lock().unwrap();
    *app_settings = default_settings.clone();
    
    Ok(default_settings)
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            // Load settings from disk
            let settings = load_settings().unwrap_or_else(|e| {
                eprintln!("Failed to load settings, using defaults: {}", e);
                AppSettings::default()
            });
            
            // Initialize global state
            app.manage(AppState {
                recorder: Mutex::new(AudioRecorder::new()),
                api_client: Mutex::new(None),
                api_url: Mutex::new(String::new()),
                settings: Mutex::new(settings),
            });

            // Setup global shortcuts
            let handle = app.handle();
            
            // Register Ctrl+Shift+R for start/stop recording
            #[cfg(desktop)]
            {
                use tauri::GlobalShortcutManager;
                let mut shortcuts = handle.global_shortcut_manager();
                
                let window = handle.get_window("main").unwrap();
                shortcuts.register("Ctrl+Shift+R", move || {
                    println!("Global shortcut triggered: Ctrl+Shift+R");
                    // Emit event to frontend to toggle recording
                    window.emit("toggle-recording", ()).unwrap();
                }).unwrap_or_else(|e| {
                    eprintln!("Failed to register global shortcut: {}", e);
                });
            }
            
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_recording,
            stop_recording,
            is_recording,
            transcribe_audio,
            check_api_health,
            get_settings,
            save_settings_cmd,
            reset_settings
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
