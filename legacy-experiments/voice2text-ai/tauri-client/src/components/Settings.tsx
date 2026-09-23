import React, { useState, useEffect } from 'react';
import { invoke } from '@tauri-apps/api/tauri';
import { AppSettings, DEFAULT_SETTINGS } from '../config';

const Settings: React.FC = () => {
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const loaded = await invoke<AppSettings>('get_settings');
      setSettings(loaded);
    } catch (err) {
      console.error('Failed to load settings:', err);
      setSaveStatus(`Failed to load settings: ${err}`);
    } finally {
      setLoading(false);
    }
  };

  const saveSettings = async () => {
    try {
      setSaveStatus('Saving...');
      await invoke('save_settings_cmd', { settings });
      setSaveStatus('✓ Settings saved successfully!');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      setSaveStatus(`Failed to save settings: ${err}`);
    }
  };

  const resetSettings = async () => {
    if (!window.confirm('Reset all settings to defaults?')) return;
    
    try {
      const defaultSettings = await invoke<AppSettings>('reset_settings');
      setSettings(defaultSettings);
      setSaveStatus('✓ Settings reset to defaults');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      setSaveStatus(`Failed to reset settings: ${err}`);
    }
  };

  const testConnection = async () => {
    try {
      setSaveStatus('Testing connection...');
      await invoke('check_api_health', { apiUrl: settings.apiUrl });
      setSaveStatus('✓ Connection successful!');
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      setSaveStatus(`Connection failed: ${err}`);
    }
  };

  if (loading) {
    return <div className="settings">Loading settings...</div>;
  }

  return (
    <div className="settings">
      <h2>⚙️ Settings</h2>
      
      {saveStatus && (
        <div className={saveStatus.includes('Failed') || saveStatus.includes('failed') ? 'error' : 'info'}>
          {saveStatus}
        </div>
      )}

      <div className="field">
        <label htmlFor="apiUrl">🌐 API Server URL</label>
        <input
          id="apiUrl"
          type="text"
          value={settings.apiUrl}
          onChange={(e) => setSettings({ ...settings, apiUrl: e.target.value })}
          placeholder="https://your-server.example.com:8443"
        />
        <small style={{ color: '#888', marginTop: '0.5rem', display: 'block' }}>
          Your Voice2Text AI reverse proxy URL (e.g., https://yourdomain.com:9443)
        </small>
        <button onClick={testConnection} style={{ marginTop: '0.5rem', fontSize: '0.9rem' }}>
          🔌 Test Connection
        </button>
      </div>

      <div className="field">
        <label htmlFor="transcriptionMode">🎤 Transcription Mode</label>
        <select
          id="transcriptionMode"
          value={settings.transcriptionMode}
          onChange={(e) => setSettings({ ...settings, transcriptionMode: e.target.value as 'batch' | 'streaming' })}
        >
          <option value="batch">Batch (Send entire recording)</option>
          <option value="streaming">Streaming (Real-time transcription)</option>
        </select>
        <small style={{ color: '#888', marginTop: '0.5rem', display: 'block' }}>
          {settings.transcriptionMode === 'batch' 
            ? '📦 Batch: Record complete audio, then transcribe (recommended for accuracy)'
            : '⚡ Streaming: Get real-time transcription as you speak (requires WebSocket support)'}
        </small>
      </div>

      <div className="field">
        <label htmlFor="language">🌍 Language</label>
        <select
          id="language"
          value={settings.language}
          onChange={(e) => setSettings({ ...settings, language: e.target.value })}
        >
          <option value="en">English</option>
          <option value="de">German (Deutsch)</option>
          <option value="es">Spanish (Español)</option>
          <option value="fr">French (Français)</option>
          <option value="it">Italian (Italiano)</option>
          <option value="pt">Portuguese (Português)</option>
          <option value="auto">Auto-detect</option>
        </select>
      </div>

      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={settings.enableContext}
            onChange={(e) => setSettings({ ...settings, enableContext: e.target.checked })}
          />
          {' '}Enable Context-Aware Transcription
        </label>
        <small style={{ color: '#888', marginTop: '0.5rem', display: 'block' }}>
          Uses previous transcriptions to improve accuracy
        </small>
      </div>

      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={settings.enableEnhancement}
            onChange={(e) => setSettings({ ...settings, enableEnhancement: e.target.checked })}
          />
          {' '}Enable Audio Enhancement
        </label>
        <small style={{ color: '#888', marginTop: '0.5rem', display: 'block' }}>
          Apply noise reduction and audio normalization
        </small>
      </div>

      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={settings.autoSendOnStop}
            onChange={(e) => setSettings({ ...settings, autoSendOnStop: e.target.checked })}
          />
          {' '}Auto-Send on Stop Recording
        </label>
        <small style={{ color: '#888', marginTop: '0.5rem', display: 'block' }}>
          Automatically transcribe when you stop recording
        </small>
      </div>

      <div className="field">
        <label>
          <input
            type="checkbox"
            checked={settings.trustSelfSigned}
            onChange={(e) => setSettings({ ...settings, trustSelfSigned: e.target.checked })}
          />
          {' '}Trust Self-Signed Certificates
        </label>
        <small style={{ color: '#888', marginTop: '0.5rem', display: 'block' }}>
          ⚠️ Only enable for development/testing servers
        </small>
      </div>

      <div className="field" style={{ marginTop: '1.5rem' }}>
        <label>Audio Configuration</label>
        <input type="text" value="Sample Rate: 16000 Hz" disabled />
        <input type="text" value="Channels: Mono (1)" disabled style={{ marginTop: '0.5rem' }} />
      </div>

      <div style={{ marginTop: '2rem', display: 'flex', gap: '1rem' }}>
        <button onClick={saveSettings} style={{ flex: 1 }}>
          💾 Save Settings
        </button>
        <button onClick={resetSettings} style={{ flex: 1, background: '#666' }}>
          🔄 Reset to Defaults
        </button>
      </div>
      
      <div style={{ marginTop: '2rem', padding: '1rem', background: '#2d2d2d', borderRadius: '4px' }}>
        <h3>ℹ️ About</h3>
        <p>Voice2Text AI Client v1.0.0</p>
        <p style={{ fontSize: '0.9rem', color: '#888', marginTop: '0.5rem' }}>
          A lightweight desktop client for Voice2Text AI services.<br />
          Built with Tauri and React.<br />
          <br />
          <strong>Hotkey:</strong> Ctrl+Shift+R to toggle recording
        </p>
      </div>
    </div>
  );
};

export default Settings;
