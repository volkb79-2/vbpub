# Oobabooga CPU-Only LLM System

A self-contained, CPU-only implementation of [oobabooga text-generation-webui](https://github.com/oobabooga/text-generation-webui) with OpenAI-compatible API endpoints.

## 🚀 Project Status

| Component | Status | Description |
|-----------|--------|-------------|
| 🟢 Infrastructure | Complete | Docker Compose, environment management |
| 🟢 CUDA Resolution | **SOLVED** | CPU-only dependencies working |
| 🟢 Oobabooga API | Running | text-generation-webui server on port 5000 |
| 🟡 OpenAI Shim | Running* | OpenAI-compatible proxy on port 8300 |
| 🟢 Model Storage | Ready | TinyLlama 638MB GGUF model |
| ⚠️ Model Loading | **Known Limitation** | See below |

*\*Shows unhealthy due to model loading dependency*

---

## 📖 Quick Start

```bash
# Start services
cd legacy-experiments/voice2text-ai/oobabooga-llm
python3 ../../../scripts/ciu/ciu.py

# Check status
docker compose ps

# Test APIs
curl http://localhost:5000/v1/models      # Oobabooga API
curl http://localhost:8300/health         # OpenAI Shim
```

---

## 🏗️ Architecture

```
Client Request
     ↓  
🟢 Reverse Proxy :8443 (voice2text-ai/reverse-proxy)
     ↓
🟡 OpenAI Shim :8300 (FastAPI proxy)
     ↓  
🟢 Oobabooga API :5000 (text-generation-webui)
     ↓
🟢 Model Files (TinyLlama 638MB GGUF)
```

**Services**:
- `llm-webui` - Oobabooga text-generation-webui (port 5000)
- `openai-shim` - OpenAI-compatible API wrapper (port 8300)
- `model-loader` - One-shot model loading service

---

## ✅ Major Achievement: CUDA Dependencies Resolved

The primary technical challenge was eliminating CUDA library dependencies that prevented CPU-only operation. This has been **completely solved**.

### The Problem
Even with `--cpu` flags, default oobabooga installation includes GPU-optimized packages that require CUDA:
```bash
# Error without fix:
libcuda.so.1: cannot open shared object file: No such file or directory
```

### The Solution (in Dockerfile)
```dockerfile
# 1. Install CPU-only PyTorch FIRST
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 2. Install CPU-only llama-cpp-python from dedicated wheel index
pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

# 3. Install other requirements (may reinstall GPU packages)
pip install -r requirements.txt || true

# 4. Force reinstall CPU-only llama-cpp-python to override
pip uninstall -y llama-cpp-binaries
pip install --force-reinstall llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

**Result**: ✅ All containers start successfully, no CUDA errors

---

## ⚠️ Known Limitation: Model Loading

### Issue
Oobabooga's model loading system has a hardcoded dependency on `llama_cpp_binaries` module that we intentionally removed for CPU-only operation.

### Root Cause
```python
# From oobabooga's modules/llama_cpp_server.py:
import llama_cpp_binaries  # ← This import fails with CPU-only setup
```

### Workarounds

**Option A: Custom CPU Loader** (Recommended)
Create a patched loader in `modules/llama_cpp_direct.py` to directly use llama-cpp-python without the binaries module.

**Option B: Alternative Stack**
Replace oobabooga with llama-cpp-python's HTTP server directly:
- Use `python -m llama_cpp.server` 
- Add FastAPI wrapper for OpenAI compatibility
- Simpler, more reliable for CPU-only use cases

---

## 📁 File Structure

| File | Purpose |
|------|---------|
| `Dockerfile` | CPU-only oobabooga build (Python 3.13) |
| `docker-compose.yml.j2` | Service orchestration template |
| `ciu.defaults.toml.j2` | Service configuration |
| `shim/` | OpenAI-compatible API adapter (FastAPI) |
| `config/system-prompt.txt` | Base LLM system prompt |
| `load-model.py` | Model loading helper script |
| `test-llm.sh` | Health & function tests |

---

## ⚙️ Configuration

Key settings in `ciu.defaults.toml.j2`:

| Variable | Default | Description |
|----------|---------|-------------|
| `llm.model_name` | TinyLlama GGUF | Model filename |
| `llm.model_threads` | 6 | CPU threads for inference |
| `llm.model_context_length` | 2048 | Context window size |
| `llm.resource_memory_limit` | 10G | Container memory limit |
| `llm.resource_cpu_limit` | 6 | Container CPU limit |

---

## 📦 Model Selection

Models are stored in `./vol-oobabooga-models/`.

| Model | Size | Quality | Notes |
|-------|------|---------|-------|
| TinyLlama-1.1B-Q4_K_M | 638MB | Basic | Fast, low memory |
| Phi-2 Q4 | ~1.5GB | Better | Microsoft Phi-2 quantized |
| Mistral-7B-Q4 | ~4GB | Good | Slower, better quality |

---

## 🔌 OpenAI-Compatible API (via Shim)

The shim exposes `/v1/chat/completions`:

```json
{
  "model": "gpt-3.5-turbo",
  "messages": [
    {"role": "user", "content": "Fix this text: teh quick brown fox"}
  ],
  "max_tokens": 128
}
```

---

## 🔧 Resource Tuning (≤6 cores / 10GB RAM)

| Component | Strategy |
|-----------|----------|
| Model weights | Use quantized models (Q4_K_M, Q4_0) |
| Threads | `MODEL_THREADS=6` or fewer |
| Context length | 512-2048 tokens |
| Max tokens | ≤256 for short responses |

---

## 🔗 Integration with Whisper

For voice-to-text post-processing, point to the shim endpoint:

```bash
curl -X POST http://openai-shim:8300/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Fix: teh quick brown fox"}]
  }'
```

---

## 🧪 Testing

```bash
./test-llm.sh
```

---

## 📋 Troubleshooting

| Symptom | Solution |
|---------|----------|
| High latency | Lower context length, reduce threads |
| OOM kill | Use smaller model, enable swap/zram |
| Shim 502 | Check llm-webui logs, verify model path |
| CUDA errors | Rebuild image, verify CPU-only packages |

---

## 📚 References

- [oobabooga/text-generation-webui](https://github.com/oobabooga/text-generation-webui)
- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python)
- [CPU wheel index](https://abetlen.github.io/llama-cpp-python/whl/cpu)
- [TheBloke GGUF models](https://huggingface.co/TheBloke)
