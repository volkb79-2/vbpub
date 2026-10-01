use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use tauri::api::path::config_dir;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppSettings {
    pub api_url: String,
    pub transcription_mode: String, // "batch" or "streaming"
    pub language: String,
    pub enable_context: bool,
    pub enable_enhancement: bool,
    pub auto_send_on_stop: bool,
    pub trust_self_signed: bool,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            api_url: "https://localhost:8443".to_string(),
            transcription_mode: "batch".to_string(),
            language: "en".to_string(),
            enable_context: true,
            enable_enhancement: true,
            auto_send_on_stop: true,
            trust_self_signed: true,
        }
    }
}

pub fn get_config_path() -> Result<PathBuf, String> {
    let config_dir = config_dir()
        .ok_or_else(|| "Could not find config directory".to_string())?;
    
    let app_config_dir = config_dir.join("voice2text-client");
    if !app_config_dir.exists() {
        fs::create_dir_all(&app_config_dir)
            .map_err(|e| format!("Failed to create config directory: {}", e))?;
    }
    
    Ok(app_config_dir.join("settings.json"))
}

pub fn load_settings() -> Result<AppSettings, String> {
    let config_path = get_config_path()?;
    
    if !config_path.exists() {
        return Ok(AppSettings::default());
    }
    
    let contents = fs::read_to_string(&config_path)
        .map_err(|e| format!("Failed to read config file: {}", e))?;
    
    serde_json::from_str(&contents)
        .map_err(|e| format!("Failed to parse config file: {}", e))
}

pub fn save_settings(settings: &AppSettings) -> Result<(), String> {
    let config_path = get_config_path()?;
    
    let json = serde_json::to_string_pretty(settings)
        .map_err(|e| format!("Failed to serialize settings: {}", e))?;
    
    fs::write(&config_path, json)
        .map_err(|e| format!("Failed to write config file: {}", e))?;
    
    Ok(())
}
