# Voice2Text Client Configuration Guide

## Overview

The Voice2Text desktop client supports persistent configuration with the following features:
- **Configurable API endpoint** - Connect to any Voice2Text server
- **Batch vs Streaming modes** - Choose your transcription workflow
- **Persistent settings** - Configuration saved between sessions
- **Runtime configuration** - No restart needed after changing settings

## Configuration Location

Settings are stored in OS-specific directories:

- **Windows**: `%APPDATA%\voice2text-client\settings.json`
- **Linux**: `~/.config/voice2text-client/settings.json`
- **macOS**: `~/Library/Application Support/voice2text-client/settings.json`

## Configuration Options

### API Server URL
**Field**: `apiUrl`  
**Type**: String  
**Default**: `https://localhost:8443`

The URL of your Voice2Text AI reverse proxy. Examples:
```
https://yourdomain.com:9443
https://voice.example.com
http://localhost:8080  (for local development)
```

**Important**: Include the protocol (https://) and port number if non-standard.

### Transcription Mode
**Field**: `transcriptionMode`  
**Type**: `batch` | `streaming`  
**Default**: `batch`

#### Batch Mode (📦)
- Record complete audio before transcription
- Send entire recording to `/api/transcribe` endpoint
- Better accuracy for longer recordings
- Lower network bandwidth during recording
- Recommended for most use cases

#### Streaming Mode (⚡)
- Real-time transcription via WebSocket
- Get partial results as you speak
- Connects to `/ws` endpoint
- Requires low-latency connection
- Best for live dictation

### Language
**Field**: `language`  
**Type**: String  
**Default**: `en`

Whisper model language code:
- `en` - English
- `de` - German
- `es` - Spanish
- `fr` - French
- `it` - Italian
- `pt` - Portuguese
- `auto` - Auto-detect (may be slower)

### Enable Context-Aware Transcription
**Field**: `enableContext`  
**Type**: Boolean  
**Default**: `true`

When enabled, uses previous transcriptions to improve accuracy for:
- Technical terminology
- Proper nouns and names
- Domain-specific vocabulary
- Consistent spelling/formatting

### Enable Audio Enhancement
**Field**: `enableEnhancement`  
**Type**: Boolean  
**Default**: `true`

Applies pre-processing to improve quality:
- Noise reduction
- Audio normalization
- Volume leveling
- Silence trimming

**Note**: May add slight latency (~100ms)

### Auto-Send on Stop Recording
**Field**: `autoSendOnStop`  
**Type**: Boolean  
**Default**: `true`

- `true`: Automatically transcribe when you stop recording
- `false`: Requires manual "Send" button click after recording

Useful to disable if you want to review/edit recording before sending.

### Trust Self-Signed Certificates
**Field**: `trustSelfSigned`  
**Type**: Boolean  
**Default**: `true`

⚠️ **Security Warning**: Only enable for development/testing!

- `true`: Accept self-signed SSL certificates
- `false`: Require valid CA-signed certificates

**Production**: Always use `false` with proper SSL certificates.

## Configuration via UI

Access settings via the ⚙️ Settings button in the main UI:

1. Click **Show Settings**
2. Modify desired options
3. Click **💾 Save Settings**
4. Settings persist across app restarts

### Test Connection
Click **🔌 Test Connection** to verify:
- Server is reachable
- SSL certificate is valid (if applicable)
- API endpoint responds correctly

## Example Configurations

### Local Development
```json
{
  "apiUrl": "http://localhost:8080",
  "transcriptionMode": "batch",
  "language": "en",
  "enableContext": true,
  "enableEnhancement": false,
  "autoSendOnStop": true,
  "trustSelfSigned": true
}
```

### Production Reverse Proxy
```json
{
  "apiUrl": "https://voice.company.com:8443",
  "transcriptionMode": "batch",
  "language": "en",
  "enableContext": true,
  "enableEnhancement": true,
  "autoSendOnStop": true,
  "trustSelfSigned": false
}
```

### Real-Time Streaming Setup
```json
{
  "apiUrl": "https://voice.company.com:8443",
  "transcriptionMode": "streaming",
  "language": "auto",
  "enableContext": true,
  "enableEnhancement": true,
  "autoSendOnStop": true,
  "trustSelfSigned": false
}
```

### German Language Support
```json
{
  "apiUrl": "https://voice.company.com:8443",
  "transcriptionMode": "batch",
  "language": "de",
  "enableContext": true,
  "enableEnhancement": true,
  "autoSendOnStop": true,
  "trustSelfSigned": false
}
```

## Troubleshooting

### Can't Connect to Server
1. Verify `apiUrl` is correct (include port)
2. Check server is running: `curl https://your-server:port/api/health`
3. Test with `trustSelfSigned: true` if using self-signed certs
4. Check firewall rules allow connections

### Settings Not Persisting
1. Check app has write permissions to config directory
2. Look for errors in console (Dev Tools: Ctrl+Shift+I)
3. Try "Reset to Defaults" then reconfigure

### Streaming Mode Not Working
1. Verify server supports WebSocket (WhisperLive service)
2. Check reverse proxy forwards `/ws` endpoint correctly
3. Ensure low-latency network connection
4. Try batch mode as fallback

### Audio Quality Issues
- Enable `enableEnhancement` for better quality
- Check microphone settings in OS
- Test with different `language` settings
- Reduce background noise

## Advanced: Manual Configuration

You can manually edit `settings.json` (app must be closed):

```bash
# Windows
notepad %APPDATA%\voice2text-client\settings.json

# Linux
nano ~/.config/voice2text-client/settings.json

# macOS
nano ~/Library/Application\ Support/voice2text-client/settings.json
```

Restart the app after manual edits.

## Configuration API (For Developers)

The Tauri backend exposes these commands:

```typescript
// Get current settings
const settings = await invoke<AppSettings>('get_settings');

// Save settings
await invoke('save_settings_cmd', { settings });

// Reset to defaults
const defaults = await invoke<AppSettings>('reset_settings');

// Test API connection
await invoke('check_api_health', { apiUrl: 'https://...' });
```

## Security Best Practices

1. **Use HTTPS in production** - Unencrypted audio is a privacy risk
2. **Disable trustSelfSigned** - Only for dev/test environments
3. **Keep apiUrl private** - May contain authentication tokens
4. **Regular updates** - Keep client software up to date
5. **Verify server identity** - Check SSL certificate matches domain

## Support

For issues or questions:
- Check server logs for connection errors
- Verify reverse proxy configuration
- Test with default settings
- See main README.md for deployment guides
