#!/bin/bash
# Test LocalAI deployment and model loading
set -euo pipefail

echo "=== LocalAI Deployment Test ==="
echo ""

# Test 1: Check if service is running
echo "[TEST 1] Checking if LocalAI service is running..."
if docker ps --filter "name=localai" --format "{{.Names}}" | grep -q "localai"; then
    echo "✅ LocalAI container is running"
else
    echo "❌ LocalAI container is NOT running"
    exit 1
fi

# Test 2: Health check
echo ""
echo "[TEST 2] Health check..."
if curl -f -s http://localhost:8090/readyz > /dev/null 2>&1; then
    echo "✅ LocalAI is healthy"
else
    echo "❌ LocalAI health check failed"
    exit 1
fi

# Test 3: List models
echo ""
echo "[TEST 3] Listing available models..."
response=$(curl -s http://localhost:8090/v1/models)
echo "Response: $response"

if echo "$response" | jq -e '.data | length > 0' > /dev/null 2>&1; then
    model_count=$(echo "$response" | jq -r '.data | length')
    echo "✅ Found $model_count model(s)"
    echo "$response" | jq -r '.data[].id' | while read -r model; do
        echo "   - $model"
    done
else
    echo "❌ No models found"
    exit 1
fi

# Test 4: Test chat completion with TinyLlama
echo ""
echo "[TEST 4] Testing chat completion with tinyllama-1.1b..."
prompt="Fix grammar: hello world its a test"
echo "Prompt: $prompt"

start_time=$(date +%s%N)
chat_response=$(curl -s http://localhost:8090/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"tinyllama-1.1b\",
    \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}],
    \"temperature\": 0.7,
    \"max_tokens\": 100
  }")
end_time=$(date +%s%N)

elapsed=$(( (end_time - start_time) / 1000000 ))
echo "Response time: ${elapsed}ms"

if echo "$chat_response" | jq -e '.choices[0].message.content' > /dev/null 2>&1; then
    result=$(echo "$chat_response" | jq -r '.choices[0].message.content')
    echo "✅ Chat completion successful"
    echo "Result: $result"
else
    echo "❌ Chat completion failed"
    echo "Response: $chat_response"
    exit 1
fi

# Test 5: Verify reverse proxy integration
echo ""
echo "[TEST 5] Testing reverse proxy access..."
if curl -f -s http://localhost/localai/readyz > /dev/null 2>&1; then
    echo "✅ LocalAI accessible via reverse proxy"
else
    echo "⚠️  Reverse proxy not configured or not running (this is OK if testing LocalAI standalone)"
fi

echo ""
echo "=== All Tests Passed! ✨ ==="
echo ""
echo "Next steps:"
echo "  1. Test other models: curl http://localhost:8080/v1/chat/completions -d '{\"model\": \"phi-2\", ...}'"
echo "  2. Run performance benchmark: ./test-localai-performance.sh"
echo "  3. Integrate with speech-to-copilot API"
