use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
struct TranscribeRequest {
    audio_data: String,
    format: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    enable_context: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    enable_enhancement: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct TranscribeResponse {
    pub text: String,
    #[serde(default)]
    pub confidence: f32,
    #[serde(default)]
    pub duration: f32,
}

#[derive(Debug, Deserialize)]
struct ErrorResponse {
    detail: String,
}

pub struct ApiClient {
    base_url: String,
    client: reqwest::Client,
}

impl ApiClient {
    pub fn new(base_url: String) -> Self {
        let client = reqwest::Client::builder()
            .danger_accept_invalid_certs(true) // For self-signed certificates
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());

        Self { base_url, client }
    }

    /// Transcribe base64-encoded audio data
    pub async fn transcribe(
        &self,
        audio_data: String,
        enable_context: bool,
        enable_enhancement: bool,
    ) -> Result<TranscribeResponse, String> {
        let url = format!("{}/api/transcribe", self.base_url);
        
        let request = TranscribeRequest {
            audio_data,
            format: "wav".to_string(),
            enable_context: Some(enable_context),
            enable_enhancement: Some(enable_enhancement),
        };

        let response = self
            .client
            .post(&url)
            .json(&request)
            .send()
            .await
            .map_err(|e| format!("Failed to send request: {}", e))?;

        if response.status().is_success() {
            response
                .json::<TranscribeResponse>()
                .await
                .map_err(|e| format!("Failed to parse response: {}", e))
        } else {
            let status = response.status();
            match response.json::<ErrorResponse>().await {
                Ok(err) => Err(format!("API error ({}): {}", status, err.detail)),
                Err(_) => Err(format!("API error: {}", status)),
            }
        }
    }

    /// Check API health
    pub async fn health_check(&self) -> Result<(), String> {
        let url = format!("{}/api/health", self.base_url);
        
        let response = self
            .client
            .get(&url)
            .send()
            .await
            .map_err(|e| format!("Failed to connect to API: {}", e))?;

        if response.status().is_success() {
            Ok(())
        } else {
            Err(format!("API health check failed: {}", response.status()))
        }
    }
}
