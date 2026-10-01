# Windows Desktop Client for Voice2Text AI

## Overview

This document outlines the top 3 framework candidates for building a Windows desktop application to interact with Voice2Text AI services, including cross-compilation strategies for building on Linux.

## Requirements Analysis

A Windows client application should:
- ✅ Record audio from microphone
- ✅ Stream or upload audio to Voice2Text API
- ✅ Display real-time transcription
- ✅ Insert text into active applications
- ✅ System tray integration
- ✅ Global hotkeys
- ✅ Native Windows look and feel
- ✅ Small distribution size (<50 MB)
- ✅ Installable without admin rights (optional)

## Top 3 Framework Candidates

### 🥇 #1: Tauri (Rust + Web Technologies)

#### Why Tauri?

**Pros:**
- ✅ **Smallest Binary**: 3-5 MB (vs Electron's 50+ MB)
- ✅ **Native Performance**: Rust backend, no Node.js overhead
- ✅ **Cross-Platform**: Windows, macOS, Linux from same codebase
- ✅ **Modern UI**: Use React, Vue, Svelte for UI
- ✅ **System Integration**: Native APIs for audio, clipboard, hotkeys
- ✅ **Secure**: Rust memory safety, sandboxed WebView
- ✅ **Auto-Updates**: Built-in update mechanism

**Cons:**
- ❌ **Learning Curve**: Requires Rust knowledge (but starter templates available)
- ❌ **Ecosystem**: Smaller than Electron (but growing fast)
- ❌ **Maturity**: Younger project (v1.0 released 2022)

#### Technology Stack

```rust
// Backend (Rust)
#[tauri::command]
async fn record_audio() -> Result<Vec<u8>, String> {
    // Use cpal crate for audio recording
    let audio_data = capture_microphone().await?;
    Ok(audio_data)
}

#[tauri::command]
async fn send_to_api(audio: Vec<u8>) -> Result<String, String> {
    // HTTP client to Voice2Text API
    let response = reqwest::post("https://server/api/transcribe")
        .body(audio)
        .send()
        .await?;
    Ok(response.text().await?)
}
```

```typescript
// Frontend (TypeScript + React)
import { invoke } from '@tauri-apps/api/tauri'

async function startRecording() {
  const audioData = await invoke('record_audio')
  const transcription = await invoke('send_to_api', { audio: audioData })
  insertText(transcription)
}
```

#### Cross-Compilation from Linux

**Yes, you can build Windows .exe on Linux!**

```dockerfile
# Dockerfile for Tauri Windows build on Linux
FROM ubuntu:22.04

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    libwebkit2gtk-4.0-dev \
    build-essential \
    libssl-dev \
    libgtk-3-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    mingw-w64

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Add Windows target
RUN rustup target add x86_64-pc-windows-gnu

# Install Node.js (for frontend)
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs

# Install Tauri CLI
RUN cargo install tauri-cli

# Build script
WORKDIR /app
COPY . .
RUN npm install
RUN cargo tauri build --target x86_64-pc-windows-gnu
```

**Build Command:**
```bash
# Build in container
docker build -t voice2text-windows-builder .
docker run -v $(pwd)/dist:/app/target voice2text-windows-builder

# Output: dist/voice2text-ai.exe
```

#### Distribution Size

- **Base Application**: ~3-5 MB
- **With Dependencies**: ~8-10 MB
- **Installer**: ~15 MB (NSIS installer)

#### Example Project Structure

```
voice2text-windows/
├── src-tauri/
│   ├── Cargo.toml
│   ├── src/
│   │   ├── main.rs          # Rust backend
│   │   ├── audio.rs         # Audio recording
│   │   ├── api.rs           # API client
│   │   └── hotkeys.rs       # Global hotkeys
│   └── tauri.conf.json      # App configuration
├── src/
│   ├── App.tsx              # React frontend
│   ├── components/
│   │   ├── Recorder.tsx
│   │   ├── Transcription.tsx
│   │   └── Settings.tsx
│   └── main.tsx
├── package.json
└── Dockerfile               # Linux build container
```

---

### 🥈 #2: Flutter (Dart)

#### Why Flutter?

**Pros:**
- ✅ **Native Compilation**: Compiles to native Windows binary
- ✅ **Beautiful UI**: Material Design and custom widgets
- ✅ **Single Codebase**: Windows, macOS, Linux, Mobile, Web
- ✅ **Fast Development**: Hot reload during development
- ✅ **Growing Ecosystem**: Many packages available
- ✅ **Google Backing**: Well-supported and maintained

**Cons:**
- ❌ **Large Binary**: 15-25 MB compressed
- ❌ **System Integration**: Limited compared to native
- ❌ **Desktop Maturity**: Mobile-first, desktop support newer
- ❌ **Native APIs**: Some Windows features require platform channels

#### Technology Stack

```dart
// Main application
import 'package:flutter/material.dart';
import 'package:record/record.dart';
import 'package:http/http.dart' as http;

class VoiceRecorderApp extends StatefulWidget {
  @override
  _VoiceRecorderAppState createState() => _VoiceRecorderAppState();
}

class _VoiceRecorderAppState extends State<VoiceRecorderApp> {
  final _recorder = Record();
  String _transcription = '';

  Future<void> startRecording() async {
    if (await _recorder.hasPermission()) {
      await _recorder.start(
        path: 'audio.wav',
        encoder: AudioEncoder.wav,
      );
    }
  }

  Future<void> stopAndTranscribe() async {
    final path = await _recorder.stop();
    final audioBytes = await File(path!).readAsBytes();
    
    // Send to API
    final response = await http.post(
      Uri.parse('https://server/api/transcribe'),
      body: audioBytes,
    );
    
    setState(() {
      _transcription = response.body;
    });
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      home: Scaffold(
        appBar: AppBar(title: Text('Voice2Text AI')),
        body: Column(
          children: [
            ElevatedButton(
              onPressed: startRecording,
              child: Text('Start Recording'),
            ),
            ElevatedButton(
              onPressed: stopAndTranscribe,
              child: Text('Stop & Transcribe'),
            ),
            Text(_transcription),
          ],
        ),
      ),
    );
  }
}
```

#### Cross-Compilation from Linux

**Yes, Flutter supports Windows build on Linux!**

```dockerfile
# Dockerfile for Flutter Windows build on Linux
FROM ubuntu:22.04

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    unzip \
    xz-utils \
    zip \
    clang \
    cmake \
    ninja-build \
    pkg-config \
    libgtk-3-dev

# Install Flutter
RUN git clone https://github.com/flutter/flutter.git -b stable /opt/flutter
ENV PATH="/opt/flutter/bin:${PATH}"

# Enable Windows desktop
RUN flutter config --enable-windows-desktop

# Pre-download dependencies
RUN flutter doctor

WORKDIR /app
COPY . .

# Build Windows app
RUN flutter build windows
```

**Build Command:**
```bash
docker build -t voice2text-flutter-builder .
docker run -v $(pwd)/build/windows:/output voice2text-flutter-builder

# Output: build/windows/runner/Release/voice2text_ai.exe
```

#### Distribution Size

- **Base Application**: ~15 MB
- **With Assets**: ~20-25 MB
- **Installer**: ~30 MB (Inno Setup)

---

### 🥉 #3: Python + PyQt6 (Compiled with PyInstaller)

#### Why Python + PyQt6?

**Pros:**
- ✅ **Python**: Easy development, rich ecosystem
- ✅ **Native Look**: Qt provides native Windows widgets
- ✅ **Rapid Development**: Quick prototyping and testing
- ✅ **System Integration**: Easy access to Windows APIs via pywin32
- ✅ **Audio Libraries**: Excellent Python audio libraries (pyaudio, sounddevice)
- ✅ **Familiar**: Python is widely known

**Cons:**
- ❌ **Large Distribution**: 30-50 MB (includes Python runtime)
- ❌ **Startup Time**: Slower than native apps
- ❌ **Packaging Complexity**: PyInstaller can be tricky
- ❌ **Performance**: Not as fast as compiled languages

#### Technology Stack

```python
# main.py
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QTextEdit
from PyQt6.QtCore import QThread, pyqtSignal
import sounddevice as sd
import numpy as np
import requests
from scipy.io.wavfile import write

class RecorderThread(QThread):
    finished = pyqtSignal(str)
    
    def __init__(self, duration=10):
        super().__init__()
        self.duration = duration
        self.sample_rate = 16000
    
    def run(self):
        # Record audio
        audio = sd.rec(
            int(self.duration * self.sample_rate),
            samplerate=self.sample_rate,
            channels=1
        )
        sd.wait()
        
        # Save to file
        write('temp.wav', self.sample_rate, audio)
        
        # Send to API
        with open('temp.wav', 'rb') as f:
            response = requests.post(
                'https://server/api/transcribe',
                files={'audio_file': f}
            )
        
        self.finished.emit(response.json()['text'])

class VoiceRecorderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Voice2Text AI')
        self.setGeometry(100, 100, 600, 400)
        
        # UI components
        self.record_btn = QPushButton('Record', self)
        self.record_btn.clicked.connect(self.start_recording)
        
        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        
        # Layout
        self.setup_ui()
    
    def start_recording(self):
        self.record_btn.setEnabled(False)
        self.recorder = RecorderThread()
        self.recorder.finished.connect(self.on_transcription)
        self.recorder.start()
    
    def on_transcription(self, text):
        self.text_edit.append(text)
        self.record_btn.setEnabled(True)

if __name__ == '__main__':
    app = QApplication([])
    window = VoiceRecorderApp()
    window.show()
    app.exec()
```

#### Cross-Compilation from Linux

**Yes, but requires Wine or cross-compilation tools**

```dockerfile
# Dockerfile for PyQt6 Windows build on Linux
FROM ubuntu:22.04

# Install Wine for Windows build
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y wine wine32 wine64 winetricks

# Install Python in Wine
RUN wine python-3.11.0-amd64.exe /quiet InstallAllUsers=1 PrependPath=1

# Install dependencies in Wine Python
RUN wine pip install PyQt6 sounddevice requests pyinstaller

WORKDIR /app
COPY . .

# Build with PyInstaller
RUN wine pyinstaller \
    --onefile \
    --windowed \
    --name voice2text-ai \
    main.py
```

**Alternative: Build on Windows in Docker**

```bash
# Use Windows container (requires Windows host or Windows Docker)
docker run -it --rm \
    -v $(pwd):/app \
    mcr.microsoft.com/windows/servercore:ltsc2022 \
    powershell -Command "
    pip install PyQt6 sounddevice requests pyinstaller;
    pyinstaller --onefile --windowed main.py
    "
```

#### Distribution Size

- **Single File**: 40-60 MB (with PyInstaller --onefile)
- **Directory**: 100-150 MB (with all dependencies)
- **Installer**: 60-80 MB (with Inno Setup)

---

## Comparison Matrix

| Feature | Tauri | Flutter | Python + PyQt6 |
|---------|-------|---------|----------------|
| **Binary Size** | 3-5 MB | 15-25 MB | 40-60 MB |
| **Performance** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **Development Speed** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Learning Curve** | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Cross-Platform** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **System Integration** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Linux Build Support** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **UI Quality** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **Startup Time** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| **Packaging Ease** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |

## Recommendation

### 🏆 Best Choice: **Tauri**

**Reasons:**
1. **Smallest Distribution**: Critical for easy deployment
2. **Best Performance**: Rust backend ensures responsiveness
3. **Native Feel**: Uses platform WebView, feels native
4. **Excellent Linux Build**: Clean cross-compilation support
5. **Future-Proof**: Modern architecture, active development
6. **Security**: Rust memory safety + sandboxed frontend

**Trade-offs:**
- Requires learning Rust basics (but lots of examples available)
- Frontend can use familiar web technologies (React/Vue/Svelte)

### Implementation Roadmap

#### Phase 1: Proof of Concept (1-2 weeks)
```bash
# Use Tauri template
npm create tauri-app@latest voice2text-windows
cd voice2text-windows

# Add audio recording (Rust)
cargo add cpal

# Add HTTP client (Rust)
cargo add reqwest tokio

# Implement basic recording + API call
# Build and test
cargo tauri dev
```

#### Phase 2: Core Features (2-3 weeks)
- Audio recording with visual feedback
- WebSocket streaming to API
- Real-time transcription display
- Basic settings (API endpoint, hotkeys)

#### Phase 3: Windows Integration (1-2 weeks)
- System tray icon
- Global hotkeys
- Text insertion into active app
- Auto-start with Windows

#### Phase 4: Polish & Distribution (1 week)
- Installer creation (NSIS)
- Auto-update mechanism
- Error handling and logging
- Documentation

**Total Estimated Time: 5-8 weeks**

## Linux Build Example

Complete example for building Tauri app on Linux:

```bash
# 1. Clone repository
git clone https://github.com/yourorg/voice2text-windows
cd voice2text-windows

# 2. Build in Docker
docker build -t voice2text-builder -f Dockerfile.windows .

# 3. Run build
docker run --rm -v $(pwd)/dist:/app/target/release voice2text-builder

# 4. Output
ls dist/
# voice2text-ai.exe
# voice2text-ai-setup.exe (installer)
```

## Alternative: VSCode Extension Instead?

Before building a Windows app, consider:

**Pros of VSCode Extension:**
- ✅ Users already have VSCode installed
- ✅ Better IDE integration
- ✅ Easier distribution (VSCode Marketplace)
- ✅ Cross-platform automatically
- ✅ TypeScript development (familiar to most devs)

**Pros of Windows App:**
- ✅ Works outside VSCode
- ✅ System-wide hotkeys
- ✅ Can insert text into any application
- ✅ Simpler for non-developers

**Recommendation**: Build **both**:
1. Start with VSCode extension (faster, developer audience)
2. Add Windows app later for broader audience

## Summary

| Candidate | Best For | Build on Linux | Recommended |
|-----------|----------|----------------|-------------|
| **Tauri** | Performance, small size, modern stack | ⭐⭐⭐⭐⭐ Yes | ✅ **YES** |
| **Flutter** | Beautiful UI, rapid development | ⭐⭐⭐⭐ Yes | ⚠️ Alternative |
| **Python + PyQt6** | Quick prototyping, Python ecosystem | ⭐⭐⭐ Possible | ⚠️ Prototype only |

**Winner: Tauri** - Best balance of size, performance, and cross-compilation support for building Windows .exe on Linux.
