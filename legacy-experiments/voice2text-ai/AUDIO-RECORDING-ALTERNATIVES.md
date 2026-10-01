# Audio Recording Alternatives for Speech-to-Copilot

This document explores alternatives to browser-based audio recording for the Speech-to-Copilot system.

## Current Approach: Browser MediaRecorder API

### Pros ✅
- No installation required
- Works across platforms (Windows, macOS, Linux)
- Integrated with web UI
- Real-time streaming via WebSocket

### Cons ❌
- **Requires HTTPS**: Security restrictions for microphone access
- **Browser compatibility**: Not all browsers fully support MediaRecorder
- **Permission prompts**: Users must grant microphone access each session
- **Audio quality**: Limited by browser implementation
- **Latency**: WebSocket overhead for real-time streaming

## Alternative 1: Desktop Application

### Native Desktop App (Electron/Tauri)

**Description**: Build a standalone desktop application that captures audio and sends to API.

**Technology Stack:**
- **Electron** (JavaScript/Node.js) - cross-platform, easy development
- **Tauri** (Rust/JavaScript) - lightweight, better performance
- **Native APIs**: Direct OS audio capture

**Pros:**
- ✅ No HTTPS requirement (local network or custom protocol)
- ✅ Better audio quality control
- ✅ System tray integration
- ✅ Persistent permissions
- ✅ Global hotkeys (e.g., Ctrl+Shift+R to record)
- ✅ Works offline with local queue

**Cons:**
- ❌ Requires installation
- ❌ Maintenance for multiple platforms
- ❌ Larger download size

**Example Implementation (Electron):**
```javascript
const { desktopCapturer } = require('electron');
const recorder = require('node-record-lpcm16');

// Record audio from microphone
const audioStream = recorder.record({
  sampleRate: 16000,
  channels: 1,
  audioType: 'wav'
});

audioStream.on('data', (chunk) => {
  // Send to API
  sendToTranscriptionAPI(chunk);
});
```

## Alternative 2: VSCode Extension

### Direct IDE Integration

**Description**: VSCode extension with native audio capture and Copilot integration.

**Features:**
- Direct integration with development workflow
- Audio capture using VSCode's extension APIs
- Send transcription directly to editor
- No separate window/browser needed

**Pros:**
- ✅ Seamless workflow integration
- ✅ Context-aware (knows current file/project)
- ✅ Can interact directly with Copilot
- ✅ No additional app to run
- ✅ Settings synced with VSCode

**Cons:**
- ❌ VSCode-specific (no other editor support)
- ❌ Extension API limitations for audio
- ❌ Requires extension approval/publishing

**Example API Integration:**
```typescript
// VSCode extension manifest
{
  "contributes": {
    "commands": [
      {
        "command": "speech-to-copilot.startRecording",
        "title": "Start Voice Recording"
      }
    ],
    "keybindings": [
      {
        "command": "speech-to-copilot.startRecording",
        "key": "ctrl+shift+r"
      }
    ]
  }
}

// Extension code
async function recordAndTranscribe() {
  // Use Node.js audio capture (sox, ffmpeg, etc.)
  const audio = await captureAudio();
  const text = await transcribe(audio);
  
  // Insert into editor
  const editor = vscode.window.activeTextEditor;
  if (editor) {
    editor.edit(editBuilder => {
      editBuilder.insert(editor.selection.active, text);
    });
  }
}
```

## Alternative 3: CLI Tool with File Upload

### Command-Line Recording Tool

**Description**: Simple CLI tool that records audio and uploads to API.

**Pros:**
- ✅ Minimal dependencies
- ✅ Scriptable/automatable
- ✅ Works over SSH
- ✅ Easy to integrate with other tools

**Cons:**
- ❌ No GUI
- ❌ Manual operation
- ❌ No real-time feedback

**Example Implementation:**
```bash
#!/bin/bash
# record-and-transcribe.sh

# Record 10 seconds of audio
echo "Recording... Speak now!"
sox -d -r 16000 -c 1 -t wav /tmp/recording.wav trim 0 10

# Upload to API
curl -X POST https://your-server/api/transcribe \
  -F "audio_file=@/tmp/recording.wav" \
  -H "Authorization: Bearer $API_TOKEN" | jq -r '.text'

# Clean up
rm /tmp/recording.wav
```

**Usage:**
```bash
# Record and get text
./record-and-transcribe.sh > output.txt

# In editor workflow
vim `./record-and-transcribe.sh`
```

## Alternative 4: Mobile App

### Smartphone as Microphone

**Description**: Mobile app that captures audio and sends to server.

**Pros:**
- ✅ Better microphone quality (often better than laptop mics)
- ✅ Portable/convenient
- ✅ Background recording
- ✅ Push notifications for results

**Cons:**
- ❌ Separate device required
- ❌ Network latency
- ❌ Battery consumption
- ❌ Development complexity (iOS + Android)

**Technology:**
- React Native - cross-platform
- Flutter - good performance
- Native (Swift/Kotlin) - best quality

## Alternative 5: System Audio Capture

### OBS Studio / Virtual Audio Cable

**Description**: Use existing audio tools to capture and route audio.

**Setup:**
1. Install OBS Studio or Virtual Audio Cable
2. Configure audio routing
3. Record to file or stream
4. Upload to transcription API

**Pros:**
- ✅ Professional audio tools
- ✅ Advanced audio processing (filters, effects)
- ✅ No custom development needed
- ✅ Works with any audio source

**Cons:**
- ❌ Complex setup
- ❌ Manual file management
- ❌ Not automated
- ❌ Requires technical knowledge

## Alternative 6: Hardware Button / Foot Pedal

### Physical Trigger for Recording

**Description**: USB device (button/pedal) that triggers recording.

**Use Case:**
- Hands-free operation
- Continuous coding workflow
- Accessibility

**Implementation:**
```python
# Python script to listen for USB HID device
import usb.core
import usb.util

def on_button_press():
    # Start/stop recording
    toggle_recording()

# Listen for HID button events
device = usb.core.find(idVendor=0x1234, idProduct=0x5678)
while True:
    data = device.read(0x81, 64, timeout=1000)
    if data[0] == 1:  # Button pressed
        on_button_press()
```

## Alternative 7: Discord/Slack Bot

### Chat-Based Voice Messages

**Description**: Send voice messages via Discord/Slack, bot transcribes and responds.

**Pros:**
- ✅ Familiar interface
- ✅ Mobile + desktop
- ✅ Collaboration features
- ✅ Built-in audio handling

**Cons:**
- ❌ Third-party dependency
- ❌ Privacy concerns
- ❌ API rate limits
- ❌ Not direct integration

## Recommended Approach: Multi-Modal Strategy

### Hybrid Solution

Support multiple input methods:

1. **Primary**: Browser-based (current) - easiest to start
2. **Power Users**: VSCode extension - best integration
3. **Offline**: Desktop app - best quality/control
4. **Quick**: CLI tool - automation/scripting
5. **Mobile**: App for on-the-go

### Implementation Priority

**Phase 1** (Current):
- ✅ Browser MediaRecorder API
- ✅ Echo test for debugging

**Phase 2** (Next):
- [ ] VSCode extension (highest ROI for developers)
- [ ] Desktop app (Electron/Tauri)

**Phase 3** (Future):
- [ ] CLI tool
- [ ] Mobile app
- [ ] Hardware trigger support

## Technical Considerations

### Audio Format Requirements

For transcription service (Whisper):
- **Sample Rate**: 16000 Hz (preferred) or 44100 Hz
- **Channels**: Mono (1 channel)
- **Format**: WAV, MP3, M4A, WebM
- **Bitrate**: 128 kbps or higher

### API Design

REST API endpoint for file upload:
```http
POST /api/transcribe
Content-Type: multipart/form-data

audio_file: <binary>
format: wav|mp3|m4a|webm
language: auto|en|es|fr|...
enable_timestamps: true|false
```

WebSocket for streaming:
```javascript
// Connect to WebSocket
const ws = new WebSocket('wss://server/ws/audio');

// Send audio chunks
ws.send(audioChunk);

// Receive partial transcriptions
ws.onmessage = (event) => {
  const result = JSON.parse(event.data);
  console.log('Partial:', result.text);
};
```

### Security Considerations

1. **Authentication**: API tokens for all alternatives
2. **Encryption**: TLS/HTTPS for all communication
3. **Privacy**: Option to disable cloud storage
4. **Rate Limiting**: Prevent abuse
5. **Audio Validation**: Check file size/duration

## Testing Audio Capture

Use the provided echo test:
```bash
# Serve the test page
cd speech-to-copilot
python3 -m http.server 8080

# Open in browser (requires HTTPS for microphone)
# Use local HTTPS proxy or tunnel
```

## Conclusion

**Recommended Path Forward:**

1. **Keep browser approach** - works for most users
2. **Develop VSCode extension** - best developer experience
3. **Provide CLI tool** - for automation/power users
4. **Consider desktop app** - if demand is high

Each alternative has trade-offs. The best approach depends on:
- Target audience (developers, general users)
- Development resources available
- Required features (real-time, offline, mobile)
- Integration needs (editor, chat, automation)

## Resources

- [MediaRecorder API](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder)
- [VSCode Extension API](https://code.visualstudio.com/api)
- [Electron Documentation](https://www.electronjs.org/docs)
- [Tauri Documentation](https://tauri.app/v1/guides/)
- [Whisper Audio Requirements](https://github.com/openai/whisper#available-models-and-languages)
