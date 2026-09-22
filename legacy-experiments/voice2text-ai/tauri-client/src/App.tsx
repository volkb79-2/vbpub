import { useState, useEffect, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/tauri';
import { listen } from '@tauri-apps/api/event';
import { AppSettings, DEFAULT_SETTINGS } from './config';
import Recorder from './components/Recorder';
import Transcription from './components/Transcription';
import Settings from './components/Settings';

interface TranscribeResponse {
  text: string;
  confidence?: number;
  duration?: number;
}

function App() {
  const [isRecording, setIsRecording] = useState(false);
  const [transcription, setTranscription] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);

  // Load settings on mount
  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const loaded = await invoke<AppSettings>('get_settings');
      setSettings(loaded);
    } catch (err) {
      console.error('Failed to load settings:', err);
    }
  };

  // Reload settings when returning from settings screen
  useEffect(() => {
    if (!showSettings) {
      loadSettings();
    }
  }, [showSettings]);

  const startRecording = useCallback(async () => {
    try {
      setError(null);
      await invoke('start_recording');
      setIsRecording(true);
    } catch (err) {
      setError(`Failed to start recording: ${err}`);
    }
  }, []);

  const stopRecording = useCallback(async () => {
    try {
      setIsTranscribing(true);
      
      // Stop recording and get audio data
      const audioData = await invoke<string>('stop_recording');
      setIsRecording(false);
      
      // Only transcribe if auto-send is enabled or we're in streaming mode
      if (!settings.autoSendOnStop && settings.transcriptionMode === 'batch') {
        setIsTranscribing(false);
        return;
      }
      
      // Send audio to API for transcription
      const resultJson = await invoke<string>('transcribe_audio', {
        audioData,
        apiUrl: settings.apiUrl,
        enableContext: settings.enableContext,
        enableEnhancement: settings.enableEnhancement,
      });
      
      const result: TranscribeResponse = JSON.parse(resultJson);
      setTranscription(result.text || 'No transcription available');
      
      if (result.confidence !== undefined) {
        console.log(`Transcription confidence: ${result.confidence}`);
      }
    } catch (err) {
      setError(`Failed to transcribe: ${err}`);
      setIsRecording(false);
    } finally {
      setIsTranscribing(false);
    }
  }, [settings]);

  useEffect(() => {
    // Listen for global hotkey toggle event from Rust backend
    const unlisten = listen('toggle-recording', () => {
      if (isRecording) {
        stopRecording();
      } else {
        startRecording();
      }
    });

    return () => {
      unlisten.then(fn => fn());
    };
  }, [isRecording, startRecording, stopRecording]);

  const checkApiConnection = async () => {
    try {
      await invoke('check_api_health', { apiUrl: settings.apiUrl });
      setError(null);
      alert('API connection successful!');
    } catch (err) {
      setError(`API connection failed: ${err}`);
    }
  };

  return (
    <div className="app">
      <header>
        <h1>Voice2Text AI Client</h1>
        <div>
          <button onClick={checkApiConnection} style={{ marginRight: '0.5rem' }}>
            🔌 Test Connection
          </button>
          <button onClick={() => setShowSettings(!showSettings)}>
            {showSettings ? 'Hide' : 'Show'} Settings
          </button>
        </div>
      </header>

      <main>
        {error && <div className="error">{error}</div>}
        
        {isTranscribing && (
          <div className="info">
            Transcribing audio...
          </div>
        )}
        
        {showSettings ? (
          <Settings />
        ) : (
          <>
            <Recorder
              isRecording={isRecording}
              onStart={startRecording}
              onStop={stopRecording}
              disabled={isTranscribing}
            />
            <Transcription text={transcription} />
          </>
        )}
      </main>

      <footer>
        <p>
          Connected to: {settings.apiUrl}
          {' | '}
          Mode: {settings.transcriptionMode === 'batch' ? '📦 Batch' : '⚡ Streaming'}
        </p>
        <p style={{ fontSize: '0.8rem', color: '#888' }}>
          Hotkey: Ctrl+Shift+R to toggle recording
        </p>
      </footer>
    </div>
  );
}

export default App;
