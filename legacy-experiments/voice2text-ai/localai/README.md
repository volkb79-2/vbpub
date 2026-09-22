# LocalAI Multi-Model LLM Service

**Part of**: Voice2Text-AI Stack  
**API**: OpenAI-compatible

---

## 📋 Overview

LocalAI provides OpenAI-compatible API with support for multiple LLM models. This implementation includes:

- **3 Pre-configured Models**: TinyLlama (669MB), Phi-2 (1.5GB), Mistral-7B (4.4GB)
- **Model Switching**: Switch between models via API calls
- **CPU Optimized**: Configured for efficient CPU inference
- **GGUF Format**: Q4_K_M quantization for optimal memory usage

---

## 🚀 Quick Start

### 1. Deploy LocalAI

```bash
cd /workspaces/dstdns/legacy-experiments/voice2text-ai/localai

# Download models and start service
python3 ../../../scripts/ciu/ciu.py
```

**Expected Output**:
```
[INFO] LocalAI Model Download Hook
[INFO] Found 3 model(s) to download
[INFO] tinyllama-1.1b: Downloading from https://huggingface.co/...
[SUCCESS] tinyllama-1.1b: Downloaded (669.0 MB)
[INFO] phi-2: Downloading from https://huggingface.co/...
[SUCCESS] phi-2: Downloaded (1560.0 MB)
[INFO] mistral-7b-instruct: Downloading from https://huggingface.co/...
[SUCCESS] mistral-7b-instruct: Downloaded (4370.0 MB)
[SUCCESS] Model configurations created
```

### 2. Verify Service

```bash
# Check health
curl http://localhost:8080/readyz

# List available models
curl http://localhost:8080/v1/models

# Expected response:
{
  "object": "list",
  "data": [
    {"id": "tinyllama-1.1b", ...},
    {"id": "phi-2", ...},
    {"id": "mistral-7b-instruct", ...}
  ]
}
```

---

## 🎯 Model Profiles

### TinyLlama 1.1B (Default)
```toml
[localai.models.tinyllama]
name = "tinyllama-1.1b"
size_mb = 669
description = "Fast inference, basic quality"
recommended_for = "real-time chat, quick responses"
```

**Performance**:
- Inference speed: ~1.5-2s per response
- Memory usage: ~700-800MB
- Quality: Basic but acceptable for chat

**Use case**: Real-time chat, quick prompt optimization

### Phi-2 (2.7B)
```toml
[localai.models.phi2]
name = "phi-2"
size_mb = 1560
description = "Balanced quality/speed"
recommended_for = "document processing, general use"
```

**Performance**:
- Inference speed: ~3.5-4s per response
- Memory usage: ~1.8-2GB
- Quality: Significantly better than TinyLlama

**Use case**: Document processing, general text improvement

### Mistral 7B Instruct
```toml
[localai.models.mistral]
name = "mistral-7b-instruct"
size_mb = 4370
description = "Best quality, slower"
recommended_for = "offline batch, quality-critical tasks"
```

**Performance**:
- Inference speed: ~9-10s per response
- Memory usage: ~5-6GB
- Quality: Best available for CPU inference

**Use case**: Offline batch processing, quality-critical work

---

## 🔧 API Usage

### Chat Completion (OpenAI-compatible)

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tinyllama-1.1b",
    "messages": [
      {"role": "user", "content": "Fix grammar: hello world its a test"}
    ],
    "temperature": 0.7,
    "max_tokens": 100
  }'
```

**Response**:
```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1702200000,
  "model": "tinyllama-1.1b",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Hello world! It's a test."
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "total_tokens": 18
  }
}
```

### Switch Models

```bash
# Use different model in request
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "phi-2",
    "messages": [{"role": "user", "content": "Your prompt here"}]
  }'
```

---

## 🔗 Reverse Proxy Integration

LocalAI is accessible through the Voice2Text reverse proxy:

```bash
# Via reverse proxy (HTTPS)
curl https://your-domain.com:9443/llm/v1/models

# Direct access (development)
curl http://localhost:8080/v1/models
```

**Nginx Configuration** (`reverse-proxy/nginx.conf.j2`):
```nginx
location /llm/ {
    proxy_pass http://localai:8080/;
    proxy_set_header Host $host;
    proxy_connect_timeout 300s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
}
```

---

## 📊 Performance Benchmarking

### Test Script

```bash
#!/bin/bash
# test-localai-performance.sh

MODELS=("tinyllama-1.1b" "phi-2" "mistral-7b-instruct")
PROMPT="Fix grammar: hello world its a test"

for model in "${MODELS[@]}"; do
  echo "Testing $model..."
  
  start=$(date +%s%N)
  response=$(curl -s http://localhost:8080/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{
      \"model\": \"$model\",
      \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT\"}]
    }")
  end=$(date +%s%N)
  
  elapsed=$(( (end - start) / 1000000 ))
  echo "  Response time: ${elapsed}ms"
  echo "  Result: $(echo "$response" | jq -r '.choices[0].message.content')"
  echo ""
done
```

**Expected Results**:
```
Testing tinyllama-1.1b...
  Response time: 1850ms
  Result: Hello world! It's a test.

Testing phi-2...
  Response time: 3720ms
  Result: Hello world! It's a test.

Testing mistral-7b-instruct...
  Response time: 9150ms
  Result: Hello, world! It's a test.
```

---

## 🛠️ Configuration

### Environment Variables

Configured in `ciu.defaults.toml.j2`:

```toml
[env]
LOCALAI_THREADS = "6"           # Number of CPU threads
LOCALAI_CONTEXT_SIZE = "2048"   # Context window size
LOCALAI_MODELS_PATH = "/models" # Models directory
LOCALAI_DEBUG = "false"         # Debug logging
LOCALAI_CORS = "true"           # Enable CORS
LOCALAI_CORS_ALLOW_ORIGINS = "*"
```

### Model Configuration

Each model gets a YAML configuration file (auto-generated):

```yaml
name: tinyllama-1.1b
backend: llama
parameters:
  model: tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
  temperature: 0.7
  top_k: 40
  top_p: 0.9
  max_tokens: 2048
  context_size: 2048
  threads: 6
  batch_size: 512
  f16: false
  gpu_layers: 0
```

---

## 🐛 Troubleshooting

### Model Download Fails

```bash
# Check internet connectivity
curl -I https://huggingface.co/

# Download models manually
cd vol-localai-models
curl -L -O https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
```

### Model Not Loading

```bash
# Check model file exists
ls -lh vol-localai-models/

# Check LocalAI logs
docker logs voice2text-prod-localai --tail 50

# Verify model config
cat vol-localai-models/tinyllama-1.1b.yaml
```

### Slow Inference

```bash
# Increase threads in ciu.defaults.toml.j2
[localai]
threads = 8  # Increase from 6

# Reduce context size
context_size = 1024  # Reduce from 2048

# Use smaller model
# Switch to tinyllama-1.1b instead of mistral-7b-instruct
```

---

## 📈 Memory Requirements

| Model | Download Size | Memory Usage | Total |
|-------|---------------|--------------|-------|
| TinyLlama 1.1B | 669MB | ~800MB | ~1.5GB |
| Phi-2 | 1.5GB | ~2GB | ~3.5GB |
| Mistral 7B | 4.4GB | ~5GB | ~9.4GB |

**Recommendation**: Download all 3 models (~6.5GB disk space) but only load one at a time in memory.

---

## 🔗 Related Documentation

- **Memory Analysis**: `../MEMORY-AND-PERFORMANCE-ANALYSIS.md`
- **Implementation Status**: `../IMPLEMENTATION-STATUS.md`
- **Main README**: `../README.md`
- **Reverse Proxy**: `../reverse-proxy/README.md`

---

## ✅ Next Steps

1. **Test with speech-to-copilot**: Integrate LocalAI as LLM backend
2. **Benchmark all models**: Run performance tests
3. **Optimize prompts**: Test different prompt templates
4. **Production tuning**: Adjust threads, context size based on load

**Status**: Ready for integration testing ✨
