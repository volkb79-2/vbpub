# Voice2Text Desktop Client

A cross-platform desktop application for Voice2Text AI transcription services, built with Tauri, React, and TypeScript.

## Features

- 🎤 **Audio Recording** - High-quality audio capture with configurable settings
- ⚡ **Dual Transcription Modes**
  - **Batch Mode**: Record complete audio, then transcribe (best accuracy)
  - **Streaming Mode**: Real-time transcription via WebSocket
- 💾 **Persistent Configuration** - Settings saved between sessions
- 🌍 **Multi-Language Support** - English, German, Spanish, French, Italian, Portuguese, and auto-detect
- 🔧 **Configurable Server** - Connect to any Voice2Text API endpoint
- ⌨️ **Global Hotkeys** - Ctrl+Shift+R to toggle recording
- 📋 **Clipboard Integration** - Copy transcriptions with one click
- 🎨 **Modern UI** - Clean, responsive interface built with React
- 🔒 **Secure** - Rust backend with sandboxed permissions
- 📦 **Small Footprint** - ~10-20 MB installed size

## Platform Support

- ✅ **Windows** - Windows 10/11 (64-bit)
- ✅ **Linux** - Ubuntu 20.04+, Debian 11+, Fedora 35+ (via AppImage)
- ✅ **macOS** - macOS 10.15+ (Intel and Apple Silicon)

## Architecture

```
┌─────────────────────────────────────────────┐
│  Tauri Application                          │
│                                             │
│  ┌─────────────┐      ┌─────────────────┐  │
│  │  Frontend   │      │  Rust Backend   │  │
│  │  (React)    │◄────►│  - Audio Capture│  │
│  │  - UI       │      │  - HTTP Client  │  │
│  │  - Display  │      │  - Hotkeys      │  │
│  └─────────────┘      │  - Clipboard    │  │
│                       └─────────────────┘  │
└───────────────┬─────────────────────────────┘
                │
                │ HTTPS (8443)
                ▼
    ┌────────────────────────┐
    │  Voice2Text API        │
    │  (Reverse Proxy)       │
    │  - Transcription       │
    │  - Enhancement         │
    └────────────────────────┘
```

## Prerequisites

### For Building Windows Executable (Recommended Approach)

**Use Docker for cross-compilation from Linux** - See [BUILD.md](BUILD.md) for complete instructions.

This is the **recommended approach** for building the Windows `.exe` in a VSCode dev container or Linux environment, as it:
- ✅ Avoids system dependency conflicts
- ✅ Provides a reproducible build environment
- ✅ Works consistently across different Linux distributions
- ✅ Is the same method used in CI/CD

Quick build command:
```bash
# Build Docker image (one-time)
docker build -t voice2text-windows-builder -f Dockerfile.windows .

# Run build
docker run --rm -v $(pwd):/app -v $(pwd)/dist:/app/target/release voice2text-windows-builder

# Output: dist/voice2text-client.exe
```

### For Native Development on Windows

If developing directly on Windows:

- **Rust**: Install from [rustup.rs](https://rustup.rs/)
- **Node.js**: v18+ for frontend build
- **System Dependencies** (Windows):
  - Visual Studio Build Tools or Windows SDK
  - WebView2 (usually pre-installed on Windows 10/11)

### For Native Development on Linux (Not Recommended for Production Builds)

**Note**: Due to webkit2gtk version incompatibilities on newer Linux distributions (Ubuntu 24.04+), 
native Linux builds may fail. Use the Docker cross-compilation method above for production builds.

If you still want to try native Linux development for testing the frontend only:
- **Rust**: Install from [rustup.rs](https://rustup.rs/)
- **Node.js**: v18+
- **System Dependencies** (Ubuntu 20.04/22.04):
  ```bash
  sudo apt-get install -y \
    libglib2.0-dev \
    libgtk-3-dev \
    libsoup2.4-dev \
    libjavascriptcoregtk-4.0-dev \
    libwebkit2gtk-4.0-dev \
    libasound2-dev
  ```
  **Warning**: These packages may not be available on Ubuntu 24.04+.

## Quick Start

### Building for Windows (Recommended)

**Use Docker cross-compilation for reliable Windows builds:**

```bash
cd legacy-experiments/voice2text-ai/tauri-client

# Build Docker image (first time only, ~15-30 minutes)
docker build -t voice2text-windows-builder -f Dockerfile.windows .

# Run build (subsequent builds ~2-5 minutes with cache)
docker run --rm -v $(pwd):/app -v $(pwd)/dist:/app/target/release voice2text-windows-builder

# Output: dist/voice2text-client.exe (~3-5 MB)
# Note: Build artifacts are placed in the dist/ directory
```

See [BUILD.md](BUILD.md) for detailed cross-compilation instructions and troubleshooting.

### Testing the Frontend Only (Optional)

If you just want to test the UI without building the Rust backend:

```bash
# Install dependencies
npm install

# Build frontend
npm run build

# Preview (requires Vite)
npm run preview
```

### Verifying the Implementation

Run the test script to verify all components are in place:

```bash
# Make sure the script is executable (should already be set)
chmod +x test-implementation.sh

# Run the test
./test-implementation.sh
```

This will:
- Check that npm dependencies are installed
- Build the frontend and verify output
- Confirm Rust implementation modules exist
- Verify Docker build configuration is present
- Display the command to build the Windows executable

### Development on Windows (Native)

If developing directly on Windows:

```bash
# Install dependencies
npm install

# Run development server with hot reload
npm run tauri dev

# Build for production
npm run tauri build
```

## Project Structure

```
tauri-client/
├── src/                          # Frontend (React + TypeScript)
│   ├── App.tsx                   # Main application component
│   ├── components/
│   │   ├── Recorder.tsx          # Audio recording UI
│   │   ├── Transcription.tsx     # Transcription display
│   │   └── Settings.tsx          # Configuration panel
│   ├── config.ts                 # API configuration
│   ├── main.tsx                  # React entry point
│   └── styles/                   # CSS styles
├── src-tauri/                    # Rust backend
│   ├── src/
│   │   ├── main.rs               # Tauri application entry
│   │   ├── audio.rs              # Audio recording module
│   │   ├── api.rs                # HTTP API client
│   │   ├── hotkeys.rs            # Global hotkey handler
│   │   └── clipboard.rs          # Clipboard integration
│   ├── Cargo.toml                # Rust dependencies
│   ├── tauri.conf.json           # Tauri configuration
│   └── icons/                    # Application icons
├── package.json                  # Node.js dependencies
├── Dockerfile.windows            # Cross-compile from Linux
├── BUILD.md                      # Build instructions
└── README.md                     # This file
```

## Building on Linux (Cross-compilation)

### Using Docker

See [BUILD.md](BUILD.md) for complete Docker-based build instructions.

Quick build:

```bash
# Build Docker image
docker build -t voice2text-windows-builder -f Dockerfile.windows .

# Run build
docker run --rm -v $(pwd)/dist:/app/target voice2text-windows-builder

# Output: dist/voice2text-client.exe
```

## Configuration

### API Endpoint

The client needs to connect to your Voice2Text AI server. There are two ways to configure the API endpoint:

**Option 1: Environment Variable (Recommended for Development)**

Set the `VOICE2TEXT_API_URL` environment variable before building or running the application:

```bash
# Linux/macOS
export VOICE2TEXT_API_URL=https://your-server.example.com:8443

# Windows (PowerShell)
$env:VOICE2TEXT_API_URL="https://your-server.example.com:8443"

# Windows (Command Prompt)
set VOICE2TEXT_API_URL=https://your-server.example.com:8443
```

This environment variable is read during the build process and compiled into the application. If not set, it defaults to `https://localhost:8443`.

**Option 2: Edit Configuration File**

Alternatively, you can edit `src/config.ts` before building:

```typescript
export const API_CONFIG = {
  baseUrl: 'https://your-server.example.com:8443',  // Change this
  apiPath: '/api',
  wsPath: '/ws',
  trustSelfSigned: true,  // For development only
};
```

**Note**: The `VOICE2TEXT_API_URL` environment variable takes precedence over the hardcoded value in `config.ts`.

### Global Hotkeys

Default hotkeys (configurable in UI):

- **Ctrl+Shift+R**: Start/Stop Recording
- **Ctrl+Shift+V**: Paste Last Transcription
- **Ctrl+Shift+H**: Show/Hide Window

### System Tray

The application minimizes to system tray with these options:

- **Start Recording**: Begin voice capture
- **Stop Recording**: End and transcribe
- **Settings**: Open configuration panel
- **Quit**: Exit application

## Usage

1. **Launch Application**: Double-click `voice2text-client.exe`
2. **Configure Server**: Enter your Voice2Text API URL in Settings
3. **Trust Certificate** (if using self-signed):
   - Download CA certificate from server
   - Install in Windows Certificate Store
4. **Start Recording**: Click "Record" or use global hotkey
5. **Speak**: Your voice is captured and sent to server
6. **Get Transcription**: Text appears in real-time
7. **Insert Text**: Click "Insert" to paste into active application

## Features in Detail

### Audio Recording

- Uses Windows Core Audio API (via `cpal` Rust crate)
- 16kHz sample rate, mono channel
- Real-time level monitoring
- Automatic silence detection (VAD)

### Transcription

- WebSocket streaming for real-time results
- Fallback to HTTP POST for complete audio
- Caching for repeated phrases
- Context-aware technical term correction

### Text Insertion

- Clipboard integration (copy transcription)
- Simulate keyboard input (type transcription)
- Preserve formatting and special characters

### Global Hotkeys

- System-wide keyboard shortcuts
- Customizable key combinations
- Visual feedback in system tray

## Troubleshooting

### WebView2 Not Found

If you get WebView2 errors:

```bash
# Download and install WebView2 Runtime
# https://developer.microsoft.com/en-us/microsoft-edge/webview2/
```

### Certificate Trust Issues

For self-signed certificates:

1. Extract CA certificate from server:
   ```bash
   docker run --rm -v voice2text-proxy-certs:/certs alpine cat /certs/ca-cert.pem > ca-cert.pem
   ```

2. Install on Windows:
   - Double-click `ca-cert.pem`
   - Click "Install Certificate"
   - Select "Local Machine"
   - Place in "Trusted Root Certification Authorities"

### Microphone Not Working

1. Check Windows microphone permissions
2. Verify microphone is default device in Windows Sound settings
3. Check application permissions in Windows Privacy settings

### Connection Refused

1. Verify Voice2Text API is running
2. Check firewall rules allow port 8443
3. Ensure correct server URL in configuration

## Development

### Hot Reload

Development mode supports hot reload for frontend changes:

```bash
cargo tauri dev
```

Frontend changes reload automatically. Rust changes require restart.

### Debugging

Enable Rust logging:

```bash
RUST_LOG=debug cargo tauri dev
```

Enable frontend console:

Press `F12` in development mode to open DevTools.

### Testing

```bash
# Test Rust backend
cd src-tauri
cargo test

# Test frontend
npm test
```

## Distribution

### Installer

Tauri can create installers using NSIS (Windows):

```bash
cargo tauri build --bundles nsis

# Output: src-tauri/target/release/bundle/nsis/voice2text-client_1.0.0_x64-setup.exe
```

### Portable Executable

Single executable without installer:

```bash
cargo tauri build

# Output: src-tauri/target/release/voice2text-client.exe
```

Size: ~3-5 MB (compressed with UPX if needed)

## Roadmap

- [ ] Auto-update mechanism
- [ ] Multi-language support
- [ ] Voice activity detection (VAD) settings
- [ ] Custom vocabulary management
- [ ] Integration with popular IDEs (VS Code, IntelliJ)
- [ ] Dark/light theme toggle
- [ ] Audio waveform visualization
- [ ] Export transcription history

## Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature/my-feature`
3. Make changes and test thoroughly
4. Commit: `git commit -m "Add my feature"`
5. Push: `git push origin feature/my-feature`
6. Create Pull Request

## License

See main repository LICENSE file.

## Related Documentation

- [WINDOWS-CLIENT-OPTIONS.md](../WINDOWS-CLIENT-OPTIONS.md) - Framework comparison and rationale
- [ARCHITECTURE.md](../ARCHITECTURE.md) - Voice2Text-AI system architecture
- [README.md](../README.md) - Main Voice2Text-AI documentation
- [Tauri Documentation](https://tauri.app/v1/guides/)
