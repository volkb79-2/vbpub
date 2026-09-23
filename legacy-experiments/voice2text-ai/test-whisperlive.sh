#!/bin/bash
# Test WhisperLive - Both Batch and Streaming Modes
# 
# This script demonstrates testing WhisperLive service in both modes:
# 1. Batch transcription (REST API)
# 2. Streaming transcription (WebSocket)

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Configuration
HOST="${1:-localhost}"
PORT="${2:-80}"

log_info "WhisperLive Hybrid Test Suite"
log_info "Host: $HOST"
log_info "Port: $PORT"
echo ""

# Check if service is running
log_info "Checking if WhisperLive service is running..."
if curl -sf "http://${HOST}:${PORT}/health" > /dev/null 2>&1; then
    log_success "Service is running"
else
    log_error "Service is not running at http://${HOST}:${PORT}"
    log_info "Start with: bash ./deploy-streaming.sh"
    exit 1
fi

# Run comprehensive tests
log_info "Running comprehensive test suite..."
echo ""

cd "$(dirname "$0")/whisper-live"

if python3 test-whisperlive-comprehensive.py "$HOST" "$PORT"; then
    log_success "All WhisperLive tests passed!"
    echo ""
    log_info "✓ Batch transcription (REST API) works"
    log_info "✓ Streaming transcription (WebSocket) works"
    log_info "✓ Health check works"
    log_info "✓ API contracts verified"
else
    log_error "Some tests failed"
    exit 1
fi

echo ""
log_info "Next steps:"
log_info "1. Test via reverse proxy: python3 test-whisperlive-comprehensive.py your-domain.com 9443"
log_info "2. Run full contract suite: python3 ../test-contracts.py"
log_info "3. Integrate with your client application"
