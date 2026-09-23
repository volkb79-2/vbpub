#!/bin/bash
# Contract tests running from within the Docker network
# Tests services without needing port exposure to localhost

set -e

echo "============================================================"
echo "Voice2Text AI - Contract Testing (Docker Network)"
echo "============================================================"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

test_count=0
pass_count=0
fail_count=0

test_service() {
    local name="$1"
    local url="$2"
    local expected_pattern="$3"
    
    ((test_count++))
    echo -n "[$test_count] Testing $name... "
    
    if output=$(docker exec voice2text-api curl -s --max-time 10 "$url" 2>&1); then
        if [[ -z "$expected_pattern" ]] || echo "$output" | grep -q "$expected_pattern"; then
            echo -e "${GREEN}✓ PASS${NC}"
            ((pass_count++))
            return 0
        else
            echo -e "${RED}✗ FAIL${NC} (unexpected content)"
            echo "Expected pattern: $expected_pattern"
            echo "Got: $(echo "$output" | head -c 100)..."
            ((fail_count++))
            return 1
        fi
    else
        echo -e "${RED}✗ FAIL${NC} (connection error)"
        ((fail_count++))
        return 1
    fi
}

echo ""
echo "[Phase 1] Health Check Contracts"
echo "------------------------------------------------------------"

# Whisper service health
test_service "Whisper health endpoint" "http://voice2text-whisper-service:9000/" "Faster Whisper"

# LLM service health
test_service "LLM models API" "http://voice2text-llm-webui:5000/v1/models" "object"

# OpenAI shim health
test_service "OpenAI shim health" "http://voice2text-openai-shim:8300/health" "ok"

# API service health
test_service "API service health" "http://voice2text-api:8000/health" "healthy"

echo ""
echo "[Phase 2] Service Contract Tests"
echo "------------------------------------------------------------"

# Test Whisper ASR contract with minimal WAV
echo -n "[$((test_count + 1))] Testing Whisper ASR contract... "
((test_count++))

# Create minimal WAV file
docker exec voice2text-api python3 -c "
import wave, struct
with wave.open('/tmp/test.wav', 'w') as f:
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(16000)
    for _ in range(1600):  # 0.1 second
        f.writeframes(struct.pack('<h', 0))
"

# Test ASR endpoint
if docker exec voice2text-api sh -c '
    curl -s -X POST "http://voice2text-whisper-service:9000/asr" \
        -F "audio_file=@/tmp/test.wav" \
        -F "language=en" | grep -q "text"
'; then
    echo -e "${GREEN}✓ PASS${NC}"
    ((pass_count++))
else
    echo -e "${RED}✗ FAIL${NC}"
    ((fail_count++))
fi

# Test LLM chat completions contract
echo -n "[$((test_count + 1))] Testing LLM chat completions contract... "
((test_count++))

if docker exec voice2text-api sh -c '
    curl -s -X POST "http://voice2text-llm-webui:5000/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"default\", \"messages\": [{\"role\": \"user\", \"content\": \"hello\"}], \"max_tokens\": 10}" \
        | grep -q "choices"
'; then
    echo -e "${GREEN}✓ PASS${NC}"
    ((pass_count++))
else
    echo -e "${RED}✗ FAIL${NC}"
    ((fail_count++))
fi

echo ""
echo "============================================================"
echo "Test Results:"
echo "  Total:  $test_count"
echo "  Passed: $pass_count"
echo "  Failed: $fail_count"
echo "============================================================"

if [ $fail_count -eq 0 ]; then
    echo -e "${GREEN}✓ ALL CONTRACT TESTS PASSED${NC}"
    exit 0
else
    echo -e "${RED}✗ $fail_count TEST(S) FAILED${NC}"
    exit 1
fi
