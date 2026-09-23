#!/bin/bash
set -e

echo "====================================="
echo "Building Voice2Text Tauri Client"
echo "Target: Windows (x86_64)"
echo "====================================="

# Check if we're in the right directory
if [ ! -f "package.json" ]; then
    echo "ERROR: package.json not found. Are you in the tauri-client directory?"
    exit 1
fi

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
cp target/x86_64-pc-windows-gnu/release/*.exe /app/target/release/ 2>/dev/null || \
    echo "Warning: No .exe files found in expected location"

# Copy bundles if they exist
if [ -d "target/x86_64-pc-windows-gnu/release/bundle" ]; then
    cp -r target/x86_64-pc-windows-gnu/release/bundle /app/target/release/
fi

echo "====================================="
echo "Build complete!"
echo "Output: /app/target/release/"
ls -lh /app/target/release/ 2>/dev/null || echo "Output directory is empty"
echo "====================================="
