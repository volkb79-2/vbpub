#!/bin/bash
set -e

echo "=========================================="
echo "Building Voice2Text Tauri Client for Windows"
echo "=========================================="
echo ""

# Check if running in Docker
if [ -f /.dockerenv ]; then
    echo "✓ Running in Docker container"
else
    echo "Building using Docker container..."
    docker build -t tauri-windows-builder -f Dockerfile.windows-build .
    docker run --rm -v "$(pwd)/src-tauri/target:/app/src-tauri/target" tauri-windows-builder
    exit 0
fi

# We're in Docker, proceed with build
cd /app

echo "[1/5] Installing Node.js dependencies..."
npm install

echo ""
echo "[2/5] Building frontend (Vite + React)..."
npm run build

echo ""
echo "[3/5] Configuring Rust for Windows target..."
rustup target add x86_64-pc-windows-gnu

echo ""
echo "[4/5] Building Tauri application for Windows..."
cd src-tauri

# Build for Windows target
cargo tauri build --target x86_64-pc-windows-gnu

echo ""
echo "[5/5] Build Complete!"
echo ""
echo "=========================================="
echo "Build artifacts location:"
echo "=========================================="

# List the built executables
if [ -d "target/x86_64-pc-windows-gnu/release" ]; then
    echo ""
    echo "✓ Windows executables (.exe):"
    find target/x86_64-pc-windows-gnu/release -maxdepth 1 -name "*.exe" -type f -exec ls -lh {} \;
    echo ""
fi

if [ -d "target/x86_64-pc-windows-gnu/release/bundle" ]; then
    echo "✓ Windows installers:"
    find target/x86_64-pc-windows-gnu/release/bundle -name "*.msi" -o -name "*.exe" | while read file; do
        ls -lh "$file"
    done
    echo ""
fi

echo "=========================================="
echo "To extract files from Docker:"
echo "docker cp <container-id>:/app/src-tauri/target/x86_64-pc-windows-gnu/release/bundle ./dist"
echo "=========================================="
