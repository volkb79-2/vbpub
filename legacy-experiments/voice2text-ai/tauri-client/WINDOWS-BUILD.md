# Building Voice2Text Client for Windows

## Overview

This guide covers building the Voice2Text desktop client as a Windows executable (.exe) and installer (.msi) using cross-compilation from Linux.

## Prerequisites

### On Linux Build Machine
- Docker installed
- 8GB+ RAM recommended
- 10GB+ free disk space

### On Windows Target Machine
- Windows 10/11 (64-bit)
- No additional dependencies needed (self-contained)

## Build Methods

### Method 1: Docker Build (Recommended)

This is the easiest method - all dependencies are containerized:

```bash
cd /workspaces/dstdns/legacy-experiments/voice2text-ai/tauri-client

# Build the Docker image (one-time setup)
docker build -t tauri-windows-builder -f Dockerfile.windows-build .

# Run the build
./build-windows.sh
```

The script will:
1. Install Node.js dependencies
2. Build the React frontend
3. Cross-compile Rust code for Windows
4. Create Windows installers (.msi and .exe)

**Build Output Location**:
```
src-tauri/target/x86_64-pc-windows-gnu/release/
├── voice-2-text-client.exe          # Portable executable
└── bundle/
    ├── msi/
    │   └── Voice2Text Client_1.0.0_x64_en-US.msi
    └── nsis/
        └── Voice2Text Client_1.0.0_x64-setup.exe
```

### Method 2: Manual Build in Container

For debugging or customization:

```bash
# Start the builder container
docker run -it --name tauri-builder -v "$(pwd):/app" tauri-windows-builder bash

# Inside container
cd /app
npm install
npm run build
cd src-tauri
cargo tauri build --target x86_64-pc-windows-gnu

# Exit and extract files
exit
docker cp tauri-builder:/app/src-tauri/target/x86_64-pc-windows-gnu/release/bundle ./dist
```

## Build Configuration

### Dockerfile.windows-build

Key components:
- **Base**: Ubuntu 22.04
- **Rust**: Latest stable + x86_64-pc-windows-gnu target
- **Cross-compiler**: MinGW-w64
- **Node.js**: v18.x
- **Tauri CLI**: v1.5+

### Cargo Configuration

The build uses MinGW for Windows cross-compilation:

```toml
# Auto-configured in container
[target.x86_64-pc-windows-gnu]
linker = "x86_64-w64-mingw32-gcc"
ar = "x86_64-w64-mingw32-ar"
```

## Installer Types

### MSI Installer (Recommended for Enterprise)
- **File**: `Voice2Text Client_1.0.0_x64_en-US.msi`
- **Size**: ~15-20 MB
- Windows Installer format
- Better for corporate deployments
- Supports silent install: `msiexec /i installer.msi /quiet`
- Can be deployed via Group Policy

### NSIS Installer (Recommended for Individual Users)
- **File**: `Voice2Text Client_1.0.0_x64-setup.exe`
- **Size**: ~15-20 MB
- Modern installer UI
- Allows custom install location
- Creates Start Menu shortcuts
- Offers desktop shortcut option

### Portable Executable
- **File**: `voice-2-text-client.exe`
- **Size**: ~10 MB
- No installation required
- Run directly from USB drive
- No admin privileges needed
- Settings stored in %APPDATA%

## Installation on Windows

### Using MSI Installer

**GUI Install**:
1. Double-click `.msi` file
2. Follow installation wizard
3. Click "Finish"

**Silent Install** (Administrator CMD):
```cmd
msiexec /i "Voice2Text Client_1.0.0_x64_en-US.msi" /quiet /norestart
```

**Uninstall**:
```cmd
msiexec /x "Voice2Text Client_1.0.0_x64_en-US.msi" /quiet
```

### Using NSIS Installer

1. Double-click `-setup.exe` file
2. Choose installation directory
3. Select components (Desktop shortcut, Start Menu)
4. Click "Install"

**Silent Install**:
```cmd
"Voice2Text Client_1.0.0_x64-setup.exe" /S
```

### Using Portable Executable

1. Copy `voice-2-text-client.exe` to desired location
2. Double-click to run
3. Settings stored in `%APPDATA%\voice2text-client\`

## First Launch Configuration

After installation, configure on first launch:

1. **Launch Application**
   - Start Menu: "Voice2Text Client"
   - Or run executable directly

2. **Click "Show Settings"**

3. **Configure API Server**
   ```
   API Server URL: https://your-reverse-proxy.com:9443
   ```

4. **Choose Transcription Mode**
   - Batch: For complete recordings (default)
   - Streaming: For real-time transcription

5. **Click "Test Connection"**
   - Verify server connectivity
   - Check SSL certificate acceptance

6. **Save Settings**

7. **Start Using**
   - Press Record button or Ctrl+Shift+R
   - Speak into microphone
   - Stop recording to transcribe

## Deployment Scenarios

### Scenario 1: Corporate Deployment via GPO

```powershell
# Deploy via Group Policy
# 1. Copy MSI to network share: \\server\software\voice2text\
# 2. Create GPO: Computer Configuration → Software Installation
# 3. Add MSI package
# 4. Deployment mode: Assigned

# Pre-configure settings via registry:
reg add "HKCU\Software\voice2text-client" /v ApiUrl /t REG_SZ /d "https://voice.company.com:8443" /f
reg add "HKCU\Software\voice2text-client" /v TranscriptionMode /t REG_SZ /d "batch" /f
```

### Scenario 2: USB Portable Deployment

```
USB Drive Structure:
E:\
├── voice2text\
│   ├── voice-2-text-client.exe
│   └── README.txt
└── config\
    └── settings.json (optional pre-config)
```

Users can run directly from USB without installation.

### Scenario 3: Silent Mass Deployment

```batch
@echo off
REM Automated deployment script

REM 1. Silent install
msiexec /i "\\server\share\Voice2Text Client.msi" /quiet /norestart

REM 2. Wait for installation
timeout /t 30

REM 3. Configure settings
mkdir "%APPDATA%\voice2text-client"
copy "\\server\share\config\settings.json" "%APPDATA%\voice2text-client\" /Y

REM 4. Create desktop shortcut
copy "%ProgramFiles%\Voice2Text Client\Voice2Text Client.lnk" "%USERPROFILE%\Desktop\" /Y

echo Deployment complete
```

## Customization

### Branding

Edit `src-tauri/tauri.conf.json` before building:

```json
{
  "package": {
    "productName": "Your Company Voice2Text",
    "version": "1.0.0"
  },
  "bundle": {
    "identifier": "com.yourcompany.voice2text",
    "icon": ["icons/custom-icon.ico"]
  }
}
```

### Default Configuration

Pre-configure settings in `src/config.ts`:

```typescript
export const DEFAULT_SETTINGS: AppSettings = {
  apiUrl: 'https://voice.yourcompany.com:8443',
  transcriptionMode: 'batch',
  language: 'en',
  enableContext: true,
  enableEnhancement: true,
  autoSendOnStop: true,
  trustSelfSigned: false, // false for production
};
```

### Build Customization

Modify `Dockerfile.windows-build` for:
- Different Rust version
- Additional system dependencies
- Custom build flags
- Code signing certificates

## Troubleshooting

### Build Issues

**"MinGW not found"**:
```bash
# Rebuild Docker image
docker build --no-cache -t tauri-windows-builder -f Dockerfile.windows-build .
```

**"npm install fails"**:
```bash
# Clear npm cache
docker run --rm -v "$(pwd):/app" tauri-windows-builder bash -c "cd /app && rm -rf node_modules package-lock.json && npm install"
```

**"Cargo build fails"**:
```bash
# Check Rust target
docker run --rm tauri-windows-builder rustup target list | grep windows-gnu
```

### Runtime Issues on Windows

**"VCRUNTIME140.dll missing"**:
- Install Visual C++ Redistributable
- Or bundle DLL with installer

**"Application won't start"**:
- Check Windows Event Viewer
- Run from CMD to see error messages
- Verify .NET Framework 4.8+ installed

**"Can't save settings"**:
- Check folder permissions: `%APPDATA%\voice2text-client\`
- Run as administrator once
- Check antivirus isn't blocking

**"Certificate errors"**:
- Enable "Trust Self-Signed Certificates" in settings
- Or install proper SSL certificate on server

## Build Optimization

### Reduce Binary Size

Add to `src-tauri/Cargo.toml`:

```toml
[profile.release]
opt-level = "z"     # Optimize for size
lto = true          # Link-time optimization
codegen-units = 1   # Better optimization
panic = "abort"     # Smaller binary
strip = true        # Remove debug symbols
```

**Result**: ~30% smaller executable

### Build Time Optimization

```bash
# Use cargo cache
docker run --rm -v cargo-cache:/root/.cargo/registry tauri-windows-builder

# Parallel builds
docker run --rm -e CARGO_BUILD_JOBS=4 tauri-windows-builder
```

## Security Considerations

### Code Signing (Recommended for Production)

```bash
# Sign executable on Windows machine
signtool sign /f certificate.pfx /p password /t http://timestamp.server /v voice-2-text-client.exe
```

Benefits:
- No SmartScreen warnings
- User trust
- Authenticity verification

### Update Mechanism

Consider implementing auto-updates:

1. Enable in `tauri.conf.json`:
```json
"updater": {
  "active": true,
  "endpoints": ["https://updates.yourserver.com/{{target}}/{{current_version}}"],
  "pubkey": "YOUR_PUBLIC_KEY"
}
```

2. Host update manifests
3. Sign updates with private key

## Distribution

### Internal Distribution
- Corporate file share
- Intranet download page
- Software deployment tools (SCCM, Intune)

### Public Distribution
- GitHub Releases (if open source)
- Company website download
- Microsoft Store (requires certification)

### Licensing
- Add license check in `src-tauri/src/main.rs`
- Hardware-based licensing
- Time-based trial licenses
- Network license server

## Support

For build issues:
- Check build logs in Docker container
- Verify all dependencies installed
- Test on clean Windows VM
- See CONFIGURATION.md for runtime config

For Windows deployment issues:
- Check Windows Event Viewer
- Test on target Windows version
- Verify antivirus whitelist
- Check network connectivity to server
