export interface AppSettings {
  apiUrl: string;
  transcriptionMode: 'batch' | 'streaming';
  language: string;
  enableContext: boolean;
  enableEnhancement: boolean;
  autoSendOnStop: boolean;
  trustSelfSigned: boolean;
}

export const DEFAULT_SETTINGS: AppSettings = {
  apiUrl: 'https://localhost:8443',
  transcriptionMode: 'batch',
  language: 'en',
  enableContext: true,
  enableEnhancement: true,
  autoSendOnStop: true,
  trustSelfSigned: true,
};

export const API_CONFIG = {
  batchPath: '/api/transcribe',
  streamingPath: '/ws',
  healthPath: '/api/health',
};

export const APP_CONFIG = {
  name: 'Voice2Text AI Client',
  version: '1.0.0',
  sampleRate: 16000,
  channels: 1,
  maxRecordingDuration: 300, // seconds
};
