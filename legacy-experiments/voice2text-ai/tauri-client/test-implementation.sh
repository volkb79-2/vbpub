#!/bin/bash
# Test script to verify the tauri-client implementation

set -e

echo "========================================"
echo "Tauri Client Implementation Test"
echo "========================================"
echo ""

# Change to tauri-client directory
cd "$(dirname "$0")"

echo "1. Testing frontend dependencies..."
if [ ! -d "node_modules" ]; then
    echo "   Installing npm packages..."
    npm install
else
    echo "   ✓ node_modules exists"
fi

echo ""
echo "2. Testing frontend build..."
npm run build
if [ -f "dist/index.html" ]; then
    echo "   ✓ Frontend built successfully"
else
    echo "   ✗ Frontend build failed"
    exit 1
fi

echo ""
echo "3. Verifying Rust code structure..."
if [ -f "src-tauri/src/audio.rs" ] && [ -f "src-tauri/src/api.rs" ]; then
    echo "   ✓ Audio and API modules present"
else
    echo "   ✗ Missing implementation modules"
    exit 1
fi

echo ""
echo "4. Checking build configuration..."
if [ -f "Dockerfile.windows" ] && [ -f "build.sh" ]; then
    echo "   ✓ Docker build files present"
else
    echo "   ✗ Missing Docker build files"
    exit 1
fi

echo ""
echo "========================================"
echo "✓ All implementation checks passed!"
echo "========================================"
echo ""
echo "To build Windows executable, run:"
echo "  docker build -t voice2text-windows-builder -f Dockerfile.windows ."
echo "  docker run --rm -v \$(pwd):/app -v \$(pwd)/dist:/app/target/release voice2text-windows-builder"
echo ""
echo "Note: Build artifacts will be placed in dist/ directory"
echo "      Output: dist/voice2text-client.exe (~3-5 MB)"
echo ""
