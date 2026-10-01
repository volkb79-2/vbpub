#!/bin/bash
# Windows Tauri Client Build with Persistent Logging

set -e

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BUILD_NAME="tauri-build-${TIMESTAMP}"
LOG_FILE="build-${TIMESTAMP}.log"

echo "========================================="
echo "Windows Tauri Build with Persistent Logs"
echo "========================================="
echo "Container: ${BUILD_NAME}"
echo "Log file: ${LOG_FILE}"
echo ""

# Create dist directory
mkdir -p ./dist

# Run build with journald logging + tee for local file
echo "Starting build (this may take 10-15 minutes)..."
echo "Logs will be saved to:"
echo "  1. ${LOG_FILE} (local file)"
echo "  2. journalctl CONTAINER_NAME=${BUILD_NAME} (systemd journal)"
echo ""

docker run --rm \
  --name "${BUILD_NAME}" \
  --log-driver=journald \
  --log-opt tag="tauri-windows-build" \
  tauri-win-build:latest 2>&1 | tee "${LOG_FILE}"

BUILD_EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo "========================================="
if [ $BUILD_EXIT_CODE -eq 0 ]; then
    echo "✅ Build completed successfully!"
    echo ""
    echo "Logs saved to:"
    echo "  - ${LOG_FILE}"
    echo "  - journalctl CONTAINER_NAME=${BUILD_NAME}"
    echo ""
    echo "Note: Container exited, checking for artifacts..."
    echo "Since container used --rm, artifacts were lost."
    echo "See BUILD-ANALYSIS.md for artifact extraction strategy."
else
    echo "❌ Build failed with exit code: ${BUILD_EXIT_CODE}"
    echo ""
    echo "Check logs:"
    echo "  cat ${LOG_FILE}"
    echo "  journalctl CONTAINER_NAME=${BUILD_NAME}"
fi
echo "========================================="

exit $BUILD_EXIT_CODE
