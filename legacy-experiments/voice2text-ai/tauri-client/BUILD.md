# Building Tauri Windows Client on Linux

This guide explains how to build the Voice2Text AI Tauri Windows client (`.exe`) on a Linux machine using Docker containers.

**This is the RECOMMENDED approach for building Windows executables**, especially when:
- Working in a VSCode dev container
- Using Ubuntu 24.04+ (which has webkit2gtk version incompatibilities)
- Building in CI/CD pipelines
- You want reproducible, consistent builds

## Why Cross-Compile?

Building a Windows executable on Linux allows:

- ✅ CI/CD integration on Linux build servers
- ✅ Consistent build environment via Docker
- ✅ No need for Windows VM or dual-boot
- ✅ Reproducible builds
- ✅ Avoids system dependency conflicts (especially webkit2gtk on newer Ubuntu)
- ✅ Works in VSCode dev containers

## Prerequisites

- Docker installed and running
- 8GB+ RAM available for Docker
- 10GB+ free disk space
- Linux host (Ubuntu 22.04+ recommended)

## Build Methods

### Method 1: MinGW Cross-Compilation (Recommended)

Uses MinGW-w64 to cross-compile Rust to Windows.

#### 1. Create Dockerfile

File: `Dockerfile.windows`

```dockerfile
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    build-essential \
    mingw-w64 \
    pkg-config \
    libssl-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Add Windows target for Rust
RUN rustup target add x86_64-pc-windows-gnu

# Install Node.js 18.x
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs

# Install Tauri CLI
RUN cargo install tauri-cli --version "^1.5"

# Configure cargo for MinGW cross-compilation
RUN mkdir -p /root/.cargo && \
    echo '[target.x86_64-pc-windows-gnu]' >> /root/.cargo/config.toml && \
    echo 'linker = "x86_64-w64-mingw32-gcc"' >> /root/.cargo/config.toml && \
    echo 'ar = "x86_64-w64-mingw32-ar"' >> /root/.cargo/config.toml

# Set working directory
WORKDIR /app

# Build script
COPY build.sh /build.sh
RUN chmod +x /build.sh

ENTRYPOINT ["/build.sh"]
```

#### 2. Create Build Script

File: `build.sh`

```bash
#!/bin/bash
set -e

echo "====================================="
echo "Building Voice2Text Tauri Client"
echo "Target: Windows (x86_64)"
echo "====================================="

# Install frontend dependencies
echo "Installing Node.js dependencies..."
npm install

# Build frontend
echo "Building frontend..."
npm run build

# Build Tauri application for Windows
echo "Building Tauri application..."
cd src-tauri
cargo tauri build --target x86_64-pc-windows-gnu

# Copy artifacts to output directory
echo "Copying build artifacts..."
mkdir -p /app/target/release
cp target/x86_64-pc-windows-gnu/release/*.exe /app/target/release/ || true
cp -r target/x86_64-pc-windows-gnu/release/bundle /app/target/release/ || true

echo "====================================="
echo "Build complete!"
echo "Output: /app/target/release/"
ls -lh /app/target/release/*.exe
echo "====================================="
```

#### 3. Build Docker Image

```bash
cd tauri-client
docker build -t voice2text-windows-builder -f Dockerfile.windows .
```

Build time: ~15-30 minutes (first time, then cached)

#### 4. Run Build

```bash
# Run build and copy output to local dist/ directory
docker run --rm -v $(pwd):/app -v $(pwd)/dist:/app/target/release voice2text-windows-builder

# Output files in dist/:
# - voice2text-client.exe (main executable)
# - bundle/nsis/ (installer if configured)
```

#### 5. Verify Output

```bash
ls -lh dist/
# voice2text-client.exe (~3-5 MB)

file dist/voice2text-client.exe
# dist/voice2text-client.exe: PE32+ executable (GUI) x86-64, for MS Windows
```

---

### Method 2: Wine + Native Windows Tools (Alternative)

Uses Wine to run native Windows build tools.

**Pros:**
- More compatible with some Rust crates
- Uses official Windows toolchain

**Cons:**
- Slower build times
- More complex setup
- Larger Docker image

#### Dockerfile (Wine-based)

```dockerfile
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# Enable 32-bit architecture for Wine
RUN dpkg --add-architecture i386

# Install Wine and dependencies
RUN apt-get update && apt-get install -y \
    wine64 \
    wine32 \
    winetricks \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Initialize Wine prefix
ENV WINEPREFIX=/root/.wine
RUN wine64 wineboot --init

# Download and install Visual C++ Build Tools (for Rust MSVC target)
# Note: This is a simplified example; actual installation may require more steps
RUN wget https://aka.ms/vs/17/release/vs_buildtools.exe -O /tmp/vs_buildtools.exe && \
    wine64 /tmp/vs_buildtools.exe --quiet --wait --norestart \
    --add Microsoft.VisualStudio.Workload.VCTools \
    --includeRecommended || true

# Install Rust via rustup-init.exe
RUN wget https://static.rust-lang.org/rustup/dist/x86_64-pc-windows-msvc/rustup-init.exe -O /tmp/rustup-init.exe && \
    wine64 /tmp/rustup-init.exe -y

# Install Node.js in Wine
RUN wget https://nodejs.org/dist/v18.18.0/node-v18.18.0-x64.msi -O /tmp/node.msi && \
    wine64 msiexec /i /tmp/node.msi /qn || true

WORKDIR /app

# Build command
CMD ["bash", "-c", "wine64 npm install && wine64 cargo tauri build"]
```

**Note**: Wine-based builds are more complex and may have compatibility issues. MinGW cross-compilation (Method 1) is recommended.

---

## Configuration for Cross-Compilation

### Tauri Configuration

Ensure `src-tauri/tauri.conf.json` has correct bundle settings:

```json
{
  "build": {
    "beforeBuildCommand": "npm run build",
    "beforeDevCommand": "npm run dev",
    "devPath": "http://localhost:5173",
    "distDir": "../dist"
  },
  "package": {
    "productName": "Voice2Text Client",
    "version": "1.0.0"
  },
  "tauri": {
    "bundle": {
      "active": true,
      "targets": ["nsis"],
      "identifier": "com.dstdns.voice2text",
      "icon": [
        "icons/icon.ico"
      ],
      "windows": {
        "certificateThumbprint": null,
        "digestAlgorithm": "sha256",
        "timestampUrl": ""
      }
    }
  }
}
```

### Cargo.toml Dependencies

Ensure Windows-compatible dependencies in `src-tauri/Cargo.toml`:

```toml
[dependencies]
tauri = { version = "1.5", features = ["shell-open"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
reqwest = { version = "0.11", features = ["json", "rustls-tls"] }
tokio = { version = "1", features = ["full"] }

# Audio recording
cpal = "0.15"
hound = "3.5"

# Windows-specific
[target.'cfg(windows)'.dependencies]
windows = { version = "0.51", features = [
    "Win32_System_Console",
    "Win32_Foundation",
    "Win32_UI_WindowsAndMessaging",
]}
```

---

## Troubleshooting

### Issue: "linker not found"

**Error:**
```
error: linker `x86_64-w64-mingw32-gcc` not found
```

**Solution:**
Ensure MinGW is installed in Docker image:
```dockerfile
RUN apt-get install -y mingw-w64
```

### Issue: "WebView2 not found"

**Context**: WebView2 is required on Windows at runtime, not build time.

**Solution for end users**:
Include WebView2 bootstrapper in installer or document requirement.

### Issue: OpenSSL linking errors

**Error:**
```
error: failed to run custom build command for `openssl-sys`
```

**Solution 1**: Use `rustls` instead of OpenSSL:
```toml
reqwest = { version = "0.11", features = ["json", "rustls-tls"], default-features = false }
```

**Solution 2**: Install OpenSSL development packages:
```dockerfile
RUN apt-get install -y libssl-dev pkg-config
```

### Issue: Large executable size

**Problem**: Executable is 20+ MB

**Solution**: Enable optimizations and strip symbols

In `src-tauri/Cargo.toml`:
```toml
[profile.release]
opt-level = "z"     # Optimize for size
lto = true          # Link-time optimization
codegen-units = 1   # Better optimization
strip = true        # Strip symbols
```

Then optionally use UPX:
```bash
upx --best --lzma dist/voice2text-client.exe
```

Result: ~3-5 MB

### Issue: Runtime dependencies missing

**Problem**: Application requires DLLs not included

**Solution**: Use static linking where possible

In `.cargo/config.toml`:
```toml
[target.x86_64-pc-windows-gnu]
rustflags = ["-C", "target-feature=+crt-static"]
```

---

## CI/CD Integration

### GitHub Actions Example

`.github/workflows/build-windows.yml`:

```yaml
name: Build Windows Client

on:
  push:
    branches: [ main ]
    paths:
      - 'legacy-experiments/voice2text-ai/tauri-client/**'
  pull_request:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Build Docker image
      run: |
        cd legacy-experiments/voice2text-ai/tauri-client
        docker build -t voice2text-windows-builder -f Dockerfile.windows .
    
    - name: Build Windows executable
      run: |
        cd legacy-experiments/voice2text-ai/tauri-client
        docker run --rm -v $(pwd):/app -v $(pwd)/dist:/app/target/release voice2text-windows-builder
    
    - name: Upload artifact
      uses: actions/upload-artifact@v3
      with:
        name: voice2text-client-windows
        path: legacy-experiments/voice2text-ai/tauri-client/dist/*.exe
    
    - name: Create Release (on tag)
      if: startsWith(github.ref, 'refs/tags/')
      uses: softprops/action-gh-release@v1
      with:
        files: legacy-experiments/voice2text-ai/tauri-client/dist/*.exe
```

---

## Performance Tips

### Incremental Builds

Use Docker volumes to cache dependencies:

```bash
# Create volume for cargo registry
docker volume create cargo-registry

# Run build with cache
docker run --rm \
  -v $(pwd):/app \
  -v cargo-registry:/root/.cargo/registry \
  -v $(pwd)/dist:/app/target/release \
  voice2text-windows-builder
```

Subsequent builds: ~2-5 minutes (vs. 15-30 minutes first time)

### Multi-stage Build

Optimize Docker image size:

```dockerfile
# Stage 1: Build
FROM ubuntu:22.04 as builder
# ... install dependencies and build ...

# Stage 2: Extract artifacts
FROM scratch as export
COPY --from=builder /app/target/release/*.exe /
```

---

## Testing the Build

### On Linux (with Wine)

Test the built executable on Linux using Wine:

```bash
# Install Wine
sudo apt-get install wine64

# Run Windows executable
wine64 dist/voice2text-client.exe
```

**Note**: Some features may not work correctly in Wine (e.g., system tray, global hotkeys).

### On Windows

Transfer `voice2text-client.exe` to Windows machine and test:

1. Copy executable to Windows
2. Double-click to run
3. Verify all features work:
   - Audio recording
   - API connection
   - Transcription display
   - System tray
   - Global hotkeys

---

## References

- [Tauri Cross-Compilation Guide](https://tauri.app/v1/guides/building/cross-platform)
- [Rust Cross-Compilation](https://rust-lang.github.io/rustup/cross-compilation.html)
- [MinGW-w64 Project](http://mingw-w64.org/)
- [Wine Project](https://www.winehq.org/)

---

## Summary

**Recommended Approach**: MinGW cross-compilation (Method 1)

**Build Command**:
```bash
docker build -t voice2text-windows-builder -f Dockerfile.windows .
docker run --rm -v $(pwd):/app -v $(pwd)/dist:/app/target/release voice2text-windows-builder
```

**Output**: `dist/voice2text-client.exe` (~3-5 MB)

**Build Time**: 
- First build: 15-30 minutes
- Subsequent: 2-5 minutes (with cache)
