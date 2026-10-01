# Voice2Text AI - Complete Testing Guide

## Overview

This guide walks through testing the complete Voice2Text AI toolchain with all services performing their functions as designed.

## Prerequisites

Ensure all services are running:
```bash
cd legacy-experiments/voice2text-ai
./start-all-stacks.sh
```

Wait for all services to be healthy (~30 seconds):
```bash
# Check status
docker compose ps

# All services should show "healthy" status
```

## Test 1: Individual Service Health Checks

### Test Each Service is Responding

```bash
# 1. Reverse Proxy Health
curl -k https://localhost:8443/health
# Expected: "healthy"

# 2. Speech-to-Copilot API Health
curl -k https://localhost:8443/api/health | jq
# Expected: JSON with status, services, demo_mode

# 3. Whisper Service (via reverse proxy)
curl -k https://localhost:8443/whisper/docs
# Expected: HTML Swagger UI page

# 4. LLM Service (via reverse proxy)
curl -k https://localhost:8443/llm/health
# Expected: "ok" or JSON health status
```

### Expected Results

All endpoints should return HTTP 200 OK. If any fail:
- Check docker logs: `docker logs <container-name>`
- Verify network: `docker network inspect voice2text-network`
- Check service health: `docker ps --filter health=unhealthy`

## Test 2: Service-to-Service Communication

### Internal Network Communication

Verify services can communicate internally via the Docker network:

```bash
# From speech-to-copilot to whisper-service
docker exec speech-to-copilot-api curl -s http://whisper-service:9000/docs | head -5
# Expected: HTML content (Swagger UI)

# From speech-to-copilot to openai-shim
docker exec speech-to-copilot-api curl -s http://openai-shim:8300/health
# Expected: "ok" or health status

# DNS resolution test
docker exec speech-to-copilot-api ping -c 1 whisper-service
docker exec speech-to-copilot-api ping -c 1 openai-shim
# Expected: successful ping responses
```

### Expected Results

- All services should be reachable by hostname
- HTTP requests should return valid responses
- No connection refused or timeout errors

## Test 3: Audio Transcription (Demo Mode)

### Test Without External Services

With demo mode enabled (default):

```bash
# Test transcription endpoint
curl -k -X POST https://localhost:8443/api/transcribe \
  -H "Content-Type: application/json" \
  -d '{
    "audio_data": "fake_base64_audio_data",
    "format": "wav",
    "enable_context": true,
    "enable_enhancement": true
  }' | jq

# Expected output:
{
  "text": "def example_function():\n    pass",
  "context_used": ["Python", "FastAPI", "async"],
  "enhanced": true,
  "demo_mode": true,
  "timestamp": 1234567890.123
}
```

### Verify Demo Mode is Active

```bash
curl -k https://localhost:8443/api/health | jq '.demo_mode'
# Expected: true
```

## Test 4: Audio Transcription (Real Whisper)

### Disable Demo Mode

Edit `speech-to-copilot/compose.config.sample.toml`:
```toml
[api]
demo_mode = false
```

Restart:
```bash
cd speech-to-copilot
python3 ../../scripts/ciu/ciu.py
```

### Test with Real Audio File

```bash
# Upload test audio file to Whisper via reverse proxy
curl -k -X POST https://localhost:8443/whisper/asr \
  -F "audio_file=@whisper-trans/test.wav" \
  -F "task=transcribe" \
  -F "language=en" \
  -F "output=json" | jq

# Expected output:
{
  "text": "This is a test audio file...",
  "segments": [...],
  "language": "en"
}
```

### Test via Speech-to-Copilot API

```bash
# Upload via main API (goes through orchestration)
curl -k -X POST https://localhost:8443/api/transcribe \
  -F "audio_file=@whisper-trans/test.wav" | jq

# Expected output:
{
  "text": "Transcribed and enhanced text...",
  "original_text": "Raw transcription...",
  "enhanced": true,
  "demo_mode": false,
  "processing_time_ms": 1234
}
```

## Test 5: LLM Enhancement

### Test LLM Service Directly

```bash
# Test OpenAI-compatible endpoint
curl -k -X POST https://localhost:8443/llm/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [
      {"role": "user", "content": "def calc sum a b return a plus b"}
    ],
    "max_tokens": 200
  }' | jq

# Expected output:
{
  "choices": [{
    "message": {
      "content": "def calculate_sum(a, b):\n    return a + b"
    }
  }],
  ...
}
```

### Test Enhancement Through Main API

```bash
# Send transcription for enhancement
curl -k -X POST https://localhost:8443/api/enhance \
  -H "Content-Type: application/json" \
  -d '{
    "text": "def calc sum a b return a plus b",
    "context": "Python code",
    "style": "code_enhancement"
  }' | jq

# Expected output:
{
  "original_text": "def calc sum a b return a plus b",
  "enhanced_text": "def calculate_sum(a, b):\n    return a + b",
  "changes": ["Fixed function name", "Added proper syntax"],
  "confidence": 0.95
}
```

## Test 6: WebSocket Streaming

### Real-Time Transcription Stream

```bash
# Install wscat if needed
npm install -g wscat

# Connect to WebSocket endpoint
wscat -c wss://localhost:8443/ws/audio --no-check

# After connecting, the client should:
# 1. Send audio chunks (base64 encoded)
# 2. Receive partial transcription updates
# 3. Receive final transcription when complete
```

### Using Python WebSocket Demo

```bash
cd speech-to-copilot/api/scripts

# Run WebSocket demo
python3 ws_demo.py wss://localhost:8443/ws/audio

# Expected output:
Connected to WebSocket
Sending audio chunk 1/10...
Received: {"type": "partial", "text": "def calc"}
Sending audio chunk 2/10...
Received: {"type": "partial", "text": "def calculate_"}
...
Received: {"type": "final", "text": "def calculate_sum(a, b):\n    return a + b"}
Connection closed
```

## Test 7: TLS/HTTPS Setup

### Verify HTTPS Configuration

```bash
# Test SSL/TLS connection
openssl s_client -connect localhost:8443 -showcerts | grep -A 2 "Certificate chain"

# Expected output: Certificate chain details
```

### Test Certificate Trust

```bash
# Test with curl (should work with -k flag)
curl -k -I https://localhost:8443/
# Expected: HTTP/1.1 200 OK

# Test without -k flag (requires trusted certificate)
curl -I https://localhost:8443/
# Expected: Works if CA certificate is installed, fails if not
```

### Browser Microphone Access Test

1. Open browser and navigate to: `https://localhost:8443/`

2. Accept/trust the self-signed certificate warning

3. Open browser console (F12)

4. Test microphone access:
   ```javascript
   navigator.mediaDevices.getUserMedia({ audio: true })
     .then(stream => console.log('✓ Microphone access granted', stream))
     .catch(err => console.error('✗ Microphone access denied:', err))
   ```

5. Expected result:
   - ✅ Microphone permission prompt appears
   - ✅ After granting permission, stream object is returned
   - ✅ No "NotSupportedError" or "NotAllowedError"

## Test 8: Repository Context Scanning

### Test Vocabulary Extraction

```bash
# Test repository scanner (if implemented)
curl -k -X POST https://localhost:8443/api/scan-repository \
  -H "Content-Type: application/json" \
  -d '{
    "repository_path": "/workspace",
    "languages": ["python", "javascript"]
  }' | jq

# Expected output:
{
  "vocabulary": {
    "functions": ["calculate_sum", "fetch_data", ...],
    "classes": ["UserModel", "APIClient", ...],
    "imports": ["FastAPI", "PostgreSQL", ...]
  },
  "file_count": 42,
  "term_count": 156
}
```

## Test 9: Self-Correction Feature

### Test Correction Detection

```bash
# Send transcription with self-correction
curl -k -X POST https://localhost:8443/api/transcribe \
  -H "Content-Type: application/json" \
  -d '{
    "text": "connect to MySQL... I misspoke, I meant PostgreSQL",
    "enable_self_correction": true
  }' | jq

# Expected output:
{
  "original_text": "connect to MySQL",
  "corrected_text": "connect to PostgreSQL",
  "correction_detected": true,
  "correction_phrase": "I misspoke"
}
```

## Test 10: Performance Benchmark

### Measure End-to-End Latency

```bash
#!/bin/bash
# benchmark.sh

echo "Testing transcription latency..."

for i in {1..5}; do
  echo "Test $i..."
  time curl -k -X POST https://localhost:8443/api/transcribe \
    -F "audio_file=@whisper-trans/test.wav" \
    -o /dev/null -s
done

# Expected output: timing for each request
# Typical: 2-5 seconds per request (CPU-only)
```

### Monitor Resource Usage

```bash
# Monitor during load test
docker stats --no-stream

# Expected output:
# whisper-service: 50-80% CPU, 4-8 GB RAM
# llm-webui: 30-50% CPU, 2-4 GB RAM
# speech-to-copilot-api: 5-10% CPU, 500 MB RAM
# reverse-proxy: 1-2% CPU, 50 MB RAM
```

## Test 11: Error Handling

### Test Invalid Requests

```bash
# 1. Invalid audio format
curl -k -X POST https://localhost:8443/api/transcribe \
  -F "audio_file=@invalid.txt" 
# Expected: HTTP 400 Bad Request, error message

# 2. Missing required fields
curl -k -X POST https://localhost:8443/api/transcribe \
  -H "Content-Type: application/json" \
  -d '{}'
# Expected: HTTP 422 Unprocessable Entity, validation errors

# 3. Service unavailable
docker stop whisper-service
curl -k -X POST https://localhost:8443/api/transcribe \
  -F "audio_file=@whisper-trans/test.wav"
# Expected: HTTP 503 Service Unavailable or graceful degradation
docker start whisper-service
```

### Test Graceful Degradation

With demo mode as fallback:
```bash
# Stop Whisper service
docker stop whisper-service

# Request should fall back to demo mode
curl -k https://localhost:8443/api/health | jq '.services.whisper'
# Expected: "unavailable" or "demo"

# Restart service
docker start whisper-service
```

## Test 12: Complete Integration Test

### Full End-to-End Workflow

```bash
#!/bin/bash
# integration-test.sh

set -e  # Exit on error

echo "=== Voice2Text AI Integration Test ==="
echo ""

# 1. Health checks
echo "1. Checking service health..."
curl -k -s https://localhost:8443/health | grep -q "healthy" && echo "✓ Reverse proxy healthy"
curl -k -s https://localhost:8443/api/health | jq -e '.status == "healthy"' && echo "✓ API healthy"
curl -k -s https://localhost:8443/whisper/docs | grep -q "Swagger" && echo "✓ Whisper healthy"
curl -k -s https://localhost:8443/llm/health | grep -q "ok" && echo "✓ LLM healthy"
echo ""

# 2. Service communication
echo "2. Testing service communication..."
docker exec speech-to-copilot-api curl -s http://whisper-service:9000/docs > /dev/null && echo "✓ API → Whisper"
docker exec speech-to-copilot-api curl -s http://openai-shim:8300/health > /dev/null && echo "✓ API → LLM"
echo ""

# 3. Transcription
echo "3. Testing audio transcription..."
RESULT=$(curl -k -s -X POST https://localhost:8443/whisper/asr \
  -F "audio_file=@whisper-trans/test.wav" \
  -F "output=json")
echo "$RESULT" | jq -e '.text' > /dev/null && echo "✓ Transcription successful"
echo ""

# 4. Enhancement
echo "4. Testing LLM enhancement..."
ENHANCED=$(curl -k -s -X POST https://localhost:8443/llm/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "test"}], "max_tokens": 50}')
echo "$ENHANCED" | jq -e '.choices[0].message.content' > /dev/null && echo "✓ Enhancement successful"
echo ""

# 5. TLS/HTTPS
echo "5. Testing TLS/HTTPS..."
openssl s_client -connect localhost:8443 -showcerts < /dev/null 2>/dev/null | grep -q "BEGIN CERTIFICATE" && echo "✓ TLS certificate valid"
echo ""

echo "=== All Tests Passed! ==="
```

Make executable and run:
```bash
chmod +x integration-test.sh
./integration-test.sh
```

## Test Results Checklist

Mark each test as passed ✅ or failed ❌:

- [ ] Individual service health checks
- [ ] Service-to-service internal communication
- [ ] Audio transcription (demo mode)
- [ ] Audio transcription (real Whisper)
- [ ] LLM enhancement
- [ ] WebSocket streaming
- [ ] TLS/HTTPS setup
- [ ] Browser microphone access
- [ ] Repository context scanning
- [ ] Self-correction feature
- [ ] Performance benchmarks
- [ ] Error handling
- [ ] Complete integration test

## Troubleshooting Failed Tests

### Service Not Responding

```bash
# Check if service is running
docker ps | grep <service-name>

# Check logs
docker logs <service-name> --tail 50

# Restart service
docker restart <service-name>
```

### Network Communication Issues

```bash
# Verify network exists
docker network inspect voice2text-network

# Check if service is on network
docker network inspect voice2text-network | jq '.[].Containers'

# Reconnect service to network
docker network connect voice2text-network <service-name>
```

### TLS Certificate Issues

```bash
# Check if certificates exist
docker run --rm -v voice2text-proxy-certs:/certs alpine ls -la /certs

# Regenerate certificates
docker exec voice2text-reverse-proxy sh /docker-entrypoint.d/90-generate-certs.sh

# Restart reverse proxy
docker restart voice2text-reverse-proxy
```

## Continuous Testing

### Automated CI/CD Testing

Create `.github/workflows/voice2text-test.yml`:

```yaml
name: Voice2Text AI Tests

on: [push, pull_request]

jobs:
  integration-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Start Services
        run: |
          cd legacy-experiments/voice2text-ai
          ./start-all-stacks.sh
      
      - name: Wait for Services
        run: sleep 60
      
      - name: Run Integration Tests
        run: |
          cd legacy-experiments/voice2text-ai
          ./integration-test.sh
      
      - name: Stop Services
        if: always()
        run: |
          cd legacy-experiments/voice2text-ai
          ./stop-all-stacks.sh
```

### Monitoring in Production

```bash
# Set up health check monitoring
watch -n 10 'curl -k -s https://localhost:8443/api/health | jq'

# Log aggregation
docker compose logs -f --tail=100 > voice2text.log &

# Resource monitoring
docker stats --no-stream >> resource-usage.log
```

## Summary

This testing guide covers:
- ✅ Individual service validation
- ✅ Inter-service communication
- ✅ Audio transcription pipeline
- ✅ LLM enhancement
- ✅ WebSocket streaming
- ✅ TLS/HTTPS security
- ✅ Browser microphone access
- ✅ Error handling
- ✅ Performance benchmarks
- ✅ Complete integration testing

**All services should perform their intended functions when these tests pass.**
