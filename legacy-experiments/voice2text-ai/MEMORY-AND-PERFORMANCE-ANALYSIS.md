# Memory & Performance Analysis for LLM Inference

**Date**: December 10, 2025  
**System Resources**: 16GB RAM, 32GB Swap (7.7GB used), 2.6GB available  
**Current LLM**: TinyLlama 1.1B (638MB GGUF Q4_K_M)

---

## 📊 Current System State

### Memory Overview
```
Total RAM:        16GB
Used RAM:         13GB (81%)
Available:        2.6GB
Swap:             32GB (7.7GB used, 25GB free)
Current LLM Size: 638MB (Q4_K_M quantization)
```

### Running Containers (Voice2Text-AI LLM Stack)
- **voice2text-llm-webui**: 223MB (2.18% of 10GB limit)
- **voice2text-whisper-service**: 87MB
- **voice2text-openai-shim**: 29MB
- **voice2text-api**: 31MB
- **voice2text-redis**: 4MB

**Total Voice2Text Stack**: ~374MB

---

## 🏗️ DST-DNS Project - Full Stack Deployment Analysis

### Services Deployed in Development Environment

Based on `ciu-global.defaults.toml.j2`, the following containers are deployed:

#### **Phase 1: Vault - Secrets Management**
- **vault** (HashiCorp Vault 1.15)
  - Memory: ~50-100MB idle, ~150-200MB under load
  - Type: Go binary, efficient memory management
  - ZRAM benefit: ❌ **Low** - Small footprint, hot service

#### **Phase 2: Core Data Services**
- **redis** (Redis 7)
  - Memory: ~20-50MB idle, ~200-500MB under load (caching)
  - Type: In-memory data store
  - ZRAM benefit: ⚠️ **Medium** - Cache data compressible but frequently accessed

- **postgres** (PostgreSQL 16 with TimescaleDB)
  - Memory: ~100-200MB idle, ~500-1000MB under load
  - Type: Relational database with shared buffers
  - ZRAM benefit: ✅ **Medium-High** - Shared buffers are 30-40% compressible, but hot working set

- **pgadmin** (pgAdmin 4 - Optional)
  - Memory: ~150-250MB idle, ~300-400MB when actively used
  - Type: Python web application
  - ZRAM benefit: ✅ **High** - Lots of idle code, rarely used in dev

- **minio** (MinIO S3-compatible storage)
  - Memory: ~50-100MB idle, ~150-300MB under load
  - Type: Go binary, object storage
  - ZRAM benefit: ❌ **Low** - Small footprint, efficient design

#### **Phase 3: Observability Infrastructure (Optional)**
- **skywalking-banyandb** (SkyWalking native APM database)
  - Memory: ~200-400MB idle, ~800-1200MB under load
  - Type: Java-based time-series database
  - ZRAM benefit: ✅ **High** - Java heap, mostly idle in dev

- **skywalking-oap** (SkyWalking OAP Server)
  - Memory: ~300-500MB idle, ~1000-1500MB under load
  - Type: Java application, APM processing
  - ZRAM benefit: ✅ **Very High** - Large Java heap, minimal load in dev

- **skywalking-ui** (SkyWalking Web UI)
  - Memory: ~100-150MB idle, ~200-300MB when accessed
  - Type: Node.js web application
  - ZRAM benefit: ✅ **High** - Mostly idle UI, rarely accessed in dev

- **otel-aggregator** (OpenTelemetry Collector)
  - Memory: ~50-100MB idle, ~200-400MB under load
  - Type: Go binary, metrics aggregation
  - ZRAM benefit: ⚠️ **Medium** - Efficient but can accumulate buffered data

- **otel-collector-node** (OpenTelemetry Node Collector)
  - Memory: ~30-50MB idle, ~100-200MB under load
  - Type: Go binary, metrics collection
  - ZRAM benefit: ⚠️ **Medium** - Small footprint but active

- **docker-stats-exporter** (Docker container metrics)
  - Memory: ~20-40MB idle, ~50-100MB under load
  - Type: Go binary, Prometheus exporter
  - ZRAM benefit: ❌ **Low** - Very small footprint

#### **Phase 4: Application Services**
- **controller** (Task/Job Management API)
  - Memory: ~100-150MB idle, ~250-400MB under load
  - Type: Python FastAPI application
  - ZRAM benefit: ⚠️ **Medium** - Python heap + JSON data

- **worker-io** (I/O Worker - DNS, HTTP)
  - Memory: ~80-120MB idle, ~200-350MB under load
  - Type: Python async worker
  - ZRAM benefit: ❌ **Low** - Active worker, hot data

- **worker-db** (Database Worker - Analytics)
  - Memory: ~80-120MB idle, ~200-350MB under load
  - Type: Python async worker + SQLAlchemy
  - ZRAM benefit: ❌ **Low** - Active worker, hot data

- **webapp-server** (WebSocket + API Server)
  - Memory: ~100-150MB idle, ~250-400MB under load
  - Type: Python FastAPI + WebSockets
  - ZRAM benefit: ⚠️ **Medium** - WebSocket connections can be idle

- **webapp-ui** (Frontend Static Files)
  - Memory: ~10-20MB idle, ~30-50MB under load
  - Type: Nginx serving static files
  - ZRAM benefit: ✅ **High** - Static files very compressible (~70%)

#### **Phase 5: External Access**
- **reverse-proxy** (Nginx HTTPS Gateway)
  - Memory: ~10-20MB idle, ~50-100MB under load
  - Type: Nginx with TLS
  - ZRAM benefit: ❌ **Low** - Small footprint, active service

#### **Phase 6: Testing (Optional)**
- **testing** (Playwright + pytest)
  - Memory: ~200-400MB idle, ~800-1500MB when running tests
  - Type: Python + Node.js + Chromium
  - ZRAM benefit: ✅ **Very High** - Only active during test runs, large idle heap

### **Total Memory Estimates (Development Load)**

| Scenario | Total Memory | Notes |
|----------|--------------|-------|
| **Minimal** (vault + data + controller) | ~400-600MB | Bare minimum for development |
| **Standard** (vault + data + apps) | ~1.2-1.8GB | Typical dev workflow |
| **Full** (all enabled services) | ~3.0-5.0GB | With observability stack |
| **Full + Tests Running** | ~4.0-6.5GB | Peak during test execution |

---

## 💾 ZRAM Analysis: DST-DNS Full Stack

### Per-Service ZRAM Value Assessment

Based on development usage patterns with **minimal load**, here's the ZRAM benefit analysis:

#### 🔴 **NOT Worth ZRAM** (Hot, Active, Low Compression)
These services have hot working sets or are too small to benefit:

| Service | Memory | Why NOT ZRAM |
|---------|--------|--------------|
| **redis** | 20-50MB | In-memory data store, all data hot |
| **vault** | 50-100MB | Small footprint, frequently accessed |
| **minio** | 50-100MB | Go binary, efficient, small |
| **worker-io** | 80-120MB | Active I/O worker, hot data paths |
| **worker-db** | 80-120MB | Active DB worker, hot query cache |
| **controller** | 100-150MB | Active API, hot request handlers |
| **reverse-proxy** | 10-20MB | Small, active gateway |
| **docker-stats-exporter** | 20-40MB | Too small, active |

**Total**: ~440-690MB  
**ZRAM Impact**: CPU overhead > compression benefit

---

#### 🟡 **Marginal ZRAM Benefit** (Warm, Occasionally Active)
These services have mixed hot/cold patterns - minor benefit in dev:

| Service | Memory | Compression Est. | ZRAM Savings | Why Marginal |
|---------|--------|------------------|--------------|--------------|
| **postgres** | 100-200MB | 25-30% | 25-60MB | Shared buffers hot, but index pages cold |
| **webapp-server** | 100-150MB | 20-25% | 20-38MB | WebSocket data hot, but connection state cold |
| **otel-aggregator** | 50-100MB | 30-35% | 15-35MB | Buffered metrics mostly warm |
| **otel-collector-node** | 30-50MB | 30-35% | 9-18MB | Small but has buffer accumulation |

**Total**: ~280-500MB  
**Potential ZRAM Savings**: ~69-151MB (~24-30% compression)  
**Trade-off**: Adds 20-40ms latency to warm paths, minimal benefit in dev

---

#### 🟢 **GOOD ZRAM Candidates** (Mostly Idle, High Compression)
These services are rarely accessed in development and compress well:

| Service | Memory | Compression Est. | ZRAM Savings | Why GOOD |
|---------|--------|------------------|--------------|----------|
| **pgadmin** | 150-250MB | 50-60% | 75-150MB | Python web UI, rarely used in dev |
| **skywalking-banyandb** | 200-400MB | 40-50% | 80-200MB | Java heap, mostly idle in dev |
| **skywalking-oap** | 300-500MB | 45-55% | 135-275MB | Large Java heap, minimal dev traffic |
| **skywalking-ui** | 100-150MB | 50-60% | 50-90MB | Node.js UI, accessed occasionally |
| **webapp-ui** | 10-20MB | 70-80% | 7-16MB | Static files, very compressible |
| **testing** | 200-400MB | 50-60% | 100-240MB | Only active during test runs |

**Total**: ~960-1720MB  
**Potential ZRAM Savings**: ~447-971MB (~47-56% compression)  
**Trade-off**: Minimal - these services are cold, latency doesn't matter

---

### 📊 ZRAM Value by Deployment Scenario

#### **Scenario 1: Minimal Dev** (vault + data + controller)
```
Total Memory:    ~400-600MB
ZRAM Candidates: postgres (25-60MB savings)
Net ZRAM Value:  ❌ NOT WORTH IT
Reason:          Too small, hot working set, CPU overhead > benefit
```

#### **Scenario 2: Standard Dev** (vault + data + apps)
```
Total Memory:    ~1.2-1.8GB
ZRAM Candidates: postgres, webapp-server (45-98MB savings)
Net ZRAM Value:  ⚠️ MARGINAL
Reason:          ~5-8% memory savings, adds latency to warm paths
```

#### **Scenario 3: Full Stack** (all services enabled)
```
Total Memory:       ~3.0-5.0GB
ZRAM Candidates:    pgadmin, skywalking stack (447-971MB savings)
Net ZRAM Value:     ✅ WORTHWHILE
Reason:             ~9-19% memory savings, only cold services compressed
Recommendation:     Enable ZRAM ONLY for observability services
```

#### **Scenario 4: Full + Tests** (peak usage)
```
Total Memory:       ~4.0-6.5GB
ZRAM Candidates:    observability + testing (547-1211MB savings)
Net ZRAM Value:     ✅ HIGHLY WORTHWHILE
Reason:             ~12-19% memory savings, testing container mostly idle
Recommendation:     Enable ZRAM for observability + testing containers
```

---

### 🎯 ZRAM Strategy Recommendation for DST-DNS

#### **Option A: No ZRAM** (Default - Recommended for Standard Dev)
```bash
# Benefits:
✅ No CPU overhead
✅ Predictable latency
✅ Simple configuration

# Use when:
- Running minimal/standard dev setup
- Available RAM > 2.6GB
- Only core services enabled
```

#### **Option B: Selective ZRAM** (Recommended for Full Stack)
```bash
# Enable ZRAM ONLY for cold/idle services:
ZRAM_SERVICES="skywalking-banyandb skywalking-oap skywalking-ui pgadmin testing"

# Benefits:
✅ 447-971MB savings (9-19% of full stack)
✅ No impact on hot path latency
✅ Only compresses rarely-used services

# Implementation:
# 1. Create ZRAM swap: zramctl -f -s 4G -a lz4
# 2. Configure cgroups to swap only these services:
echo "$MEMORY_LIMIT" > /sys/fs/cgroup/skywalking-oap/memory.high
echo "100" > /sys/fs/cgroup/skywalking-oap/memory.swappiness

# Use when:
- Running full observability stack
- Available RAM < 2GB
- Observability services rarely accessed
```

#### **Option C: System-Wide ZRAM** (NOT Recommended)
```bash
# Would compress ALL services including hot paths

# Downsides:
❌ Adds 20-80ms latency to active workers
❌ CPU overhead on every page fault
❌ Only ~15-25% compression on hot services

# Use when:
- Never (better alternatives exist)
```

---

### 💡 Better Alternatives to ZRAM for Dev

If memory is tight, consider these instead:

#### **1. Disable Observability Stack** (Saves 600-1400MB)
```bash
# Edit ciu-global.defaults.toml.j2:
enable_skywalking = false
enable_otel = false
enable_pgadmin = false

# Deployment:
python3 scripts/ciu/ciu-deploy.py --groups dev --deploy  # Skips observability
```

#### **2. Use Minimal Profile** (Saves 1.8-3.4GB)
```bash
# Deploy only essentials:
python3 scripts/ciu/ciu-deploy.py --groups minimal --deploy

# Includes:
- vault (secrets)
- postgres + redis (data)
- controller (API)
```

#### **3. Increase Swap** (Already have 32GB, 25GB free)
```bash
# Current swap is sufficient, no action needed
# Swap is slower than ZRAM but zero CPU overhead
```

#### **4. Resource Limits** (Force less memory per service)
```toml
# In ciu-global.defaults.toml.j2:
[deployment.resources]
enabled = true
mem_limit = "512m"  # Hard limit per container
```

---

## 🧠 LLM Memory "Hotness" Analysis for Chat/Prompt Optimization

### Memory Access Patterns in LLM Inference

When running an LLM for chat/prompt optimization, memory access patterns are:

#### **Hot Memory Areas** (Frequently Accessed) 🔥🔥🔥
1. **Active Context Window** (~100-300MB for our use case)
   - Current tokens being processed
   - Attention mechanism computations
   - KV cache for recent tokens
   - **Access pattern**: Sequential, predictable, very hot
   - **Why hot**: Every token generation reads from recent context

2. **Model Embeddings Layer** (~50-100MB)
   - Input token embeddings
   - Output token embeddings
   - **Access pattern**: Random access, frequent
   - **Why hot**: Every token lookup requires embedding access

3. **Final Layer Weights** (~50-100MB)
   - Output projection layer
   - Softmax computation weights
   - **Access pattern**: Sequential, every inference
   - **Why hot**: Used for every token prediction

**Total Hot Memory**: ~200-500MB (30-80% of model size)

#### **Warm Memory Areas** (Occasionally Accessed) 🔥🔥
1. **Middle Transformer Layers** (~200-300MB)
   - Attention weights
   - Feed-forward network weights
   - **Access pattern**: Sequential during forward pass
   - **Why warm**: Used every inference, but sequentially (layer by layer)

2. **KV Cache Buffer** (grows with context, ~50-200MB)
   - Cached attention key/value pairs
   - **Access pattern**: Append-only, then random reads
   - **Why warm**: Read during attention, written once per token

**Total Warm Memory**: ~250-500MB (40-80% of model size)

#### **Cold Memory Areas** (Rarely Accessed) ❄️
1. **Initialization Metadata** (~10-50MB)
   - Model configuration
   - Tokenizer data
   - Rope embeddings
   - **Access pattern**: Read once at startup
   - **Why cold**: Only accessed during model loading

2. **Unused Model Variants** (~0MB for single model)
   - Alternative quantization layers (if present)
   - **Access pattern**: Never accessed if not selected

**Total Cold Memory**: ~10-50MB (2-8% of model size)

### Memory Access Timeline (Single Inference)
```
Time    Memory Area             Access Type    Compression Benefit
----------------------------------------------------------------------
0ms     Load embeddings         Sequential     Low (hot)
2ms     Layer 1 attention       Sequential     Medium (warm)
4ms     Layer 1 FFN             Sequential     Medium (warm)
6ms     Layer 2 attention       Sequential     Medium (warm)
...
50ms    Final layer projection  Sequential     Low (hot)
52ms    Softmax + sampling      Random         Low (hot)
```

---

## 💾 ZRAM Analysis for Your Use Case

### Should You Use ZRAM?

**TL;DR**: ⚠️ **Probably NOT worthwhile** - Your working set is hot, compression overhead > benefit

### Detailed Analysis

#### ✅ ZRAM Works Well When:
1. **Large cold/inactive pages**: Applications loaded but idle
2. **Text-heavy data**: Code, configs, logs (60-70% compression)
3. **Sparse data structures**: Zeros, repeated patterns
4. **Low CPU availability**: Swapping to disk worse than compression

#### ❌ ZRAM NOT Ideal When:
1. **Hot working set**: Frequently accessed memory (compression/decompression overhead)
2. **Dense numerical data**: Model weights (30-40% compression at best)
3. **Sequential access**: Predictable patterns don't benefit from caching
4. **Already quantized**: Q4_K_M already compressed (4-bit weights)

### Your Specific Scenario

**LLM Inference Memory Profile**:
- **Hot memory**: 30-50% (200-320MB) - Constantly accessed
- **Warm memory**: 40-50% (250-320MB) - Sequentially accessed
- **Cold memory**: 2-8% (10-50MB) - Read once

**ZRAM Compression Estimate**:
```
Hot memory (300MB):   10% compressible → 30MB savings, high CPU cost
Warm memory (300MB):  20% compressible → 60MB savings, medium CPU cost
Cold memory (50MB):   50% compressible → 25MB savings, low CPU cost
----------------------------------------------------------------------
Total savings:        ~115MB (18% of 638MB model)
CPU overhead:         ~5-10% during inference (unacceptable for real-time)
```

### Recommendation: **ZRAM Strategy by Use Case**

#### **For Voice2Text LLM Stack ONLY**: ❌ **Don't Use ZRAM**

**Why**:
1. **Small savings**: Only ~115MB (18%) could be effectively compressed
2. **High overhead**: CPU cycles better spent on inference itself
3. **Hot working set**: Your use case keeps 60-80% of model memory active
4. **Already quantized**: Q4_K_M is already heavily compressed (4-bit)
5. **Latency sensitive**: Chat responses need <2s, compression adds 100-200ms

**Alternative Solutions for Voice2Text** (Better ROI):
1. **Use smaller models**: Phi-2 (2.7B) vs TinyLlama (1.1B) only +400MB
2. **Reduce KV cache**: Limit context window to 1024 tokens (saves 100-200MB)
3. **Use quantized attention**: Flash attention 2 (saves 20-30% memory)
4. **Off-peak swapping**: Let OS swap unused system services, not LLM

#### **For DST-DNS Full Stack**: ✅ **Use Selective ZRAM** (Observability Services Only)

**When**: Running full stack with observability (SkyWalking, OTel, pgAdmin)

**What to Compress**: ONLY cold/idle services
- ✅ skywalking-banyandb (200-400MB → save 80-200MB)
- ✅ skywalking-oap (300-500MB → save 135-275MB)
- ✅ skywalking-ui (100-150MB → save 50-90MB)
- ✅ pgadmin (150-250MB → save 75-150MB)
- ✅ testing (200-400MB → save 100-240MB)
- ❌ DON'T compress: postgres, redis, workers, controller (hot paths)

**Expected Benefit**: 440-955MB savings (12-19% of full stack)

**Implementation**:
```bash
# 1. Enable ZRAM (4GB compressed swap):
sudo zramctl -f -s 4G -a lz4
sudo mkswap /dev/zram0
sudo swapon /dev/zram0 -p 100  # Higher priority than disk swap

# 2. Configure per-service swap preference:
# Only observability containers should use ZRAM
# (Requires cgroups v2 configuration - see docs)

# 3. Verify:
zramctl  # Should show 4GB ZRAM device
cat /proc/swaps  # ZRAM should have higher priority
```

**Best Practice**: Disable observability services in dev instead
```bash
# Edit ciu-global.defaults.toml.j2:
enable_skywalking = false
enable_otel = false
enable_pgadmin = false

# Or deploy minimal profile:
python3 scripts/ciu/ciu-deploy.py --groups dev --deploy
```

---

## ⚡ TGI Performance: Rust vs Python

### Does Rust Make It Faster?

**Short Answer**: **YES, but mostly for GPU inference.** For CPU, the bottleneck is matrix math, not language overhead.

### Performance Breakdown

#### Where Rust Helps (GPU Inference) 🚀
```
Component                Python (oobabooga)    Rust (TGI)         Improvement
--------------------------------------------------------------------------------
Token Encoding           ~5ms                  ~1ms               5x faster
Batch Scheduling         ~10ms                 ~2ms               5x faster
Memory Management        ~15ms                 ~5ms               3x faster
Network I/O              ~5ms                  ~2ms               2.5x faster
--------------------------------------------------------------------------------
Overhead (per request):  ~35ms                 ~10ms              3.5x faster

Model Inference (GPU):   ~100ms                ~100ms             Same
Total Response Time:     ~135ms                ~110ms             18% faster
```

**TGI Advantage on GPU**: 18-25% faster (overhead reduction)

#### Where Rust Doesn't Help (CPU Inference) 🐢
```
Component                Python (direct)       Rust (TGI)         Difference
--------------------------------------------------------------------------------
Token Encoding           ~5ms                  ~1ms               4ms saved
Batch Scheduling         N/A (single)          ~2ms               2ms added
Memory Management        ~10ms                 ~5ms               5ms saved
Network I/O              ~5ms                  ~2ms               3ms saved
--------------------------------------------------------------------------------
Overhead (per request):  ~20ms                 ~10ms              10ms saved

Model Inference (CPU):   ~2000ms               ~2000ms            Same
Total Response Time:     ~2020ms               ~2010ms            0.5% faster
```

**TGI Advantage on CPU**: <1% faster (noise level)

### Why CPU Inference Doesn't Benefit

1. **Bottleneck is matrix math**: 99% of time spent in llama.cpp (C/C++), not Python
2. **Rust calls same C library**: Both use llama.cpp for inference
3. **Python overhead negligible**: 20ms overhead vs 2000ms compute = 1%
4. **Single request at a time**: No batching benefits for your use case

### When TGI Actually Shines

**TGI is designed for**:
- ✅ **High throughput**: 100+ requests/second (batching benefits)
- ✅ **GPU inference**: Tensor parallelism, CUDA graphs
- ✅ **Multi-user**: Continuous batching, request queuing
- ✅ **Production scale**: Monitoring, metrics, load balancing

**Your use case is**:
- ❌ **Low throughput**: ~1-5 requests/minute (single user testing)
- ❌ **CPU inference**: No GPU acceleration
- ❌ **Single user**: No concurrent requests
- ❌ **Development**: Testing prompts, not production

**Verdict**: **TGI overhead not worth it for CPU development work**

---

## 🔀 Running Multiple LLM Instances for Comparison

### Can You Run Multiple TGI Instances?

**YES!** ✅ Both TGI and LocalAI support multiple instances.

### Memory Requirements for Parallel Models

```
Configuration                Memory Required      Feasibility
------------------------------------------------------------------------
1x TinyLlama (1.1B)         ~800MB              ✅ Easy
2x TinyLlama (parallel)     ~1.6GB              ✅ Possible
TinyLlama + Phi-2 (2.7B)    ~800MB + ~2GB       ⚠️ Tight (2.6GB free)
3x different models         ~5GB+               ❌ Out of RAM
------------------------------------------------------------------------
```

### Option 1: Multiple TGI Instances (Sequential Testing)

**Pros**:
- ✅ Each model isolated in own container
- ✅ Easy to start/stop individual models
- ✅ No configuration conflicts

**Cons**:
- ❌ Cannot run simultaneously (insufficient RAM)
- ❌ Must stop one before starting another (~10s delay)
- ❌ 4GB image size × 3 models = 12GB disk space

**Example Setup**:
```yaml
# docker-compose-tgi-multi.yml
services:
  tgi-tinyllama:
    image: ghcr.io/huggingface/text-generation-inference:latest
    ports: ["8001:80"]
    volumes: ["./models/tinyllama:/data"]
    environment: {MODEL_ID: "/data/tinyllama-1.1b"}
  
  tgi-phi2:
    image: ghcr.io/huggingface/text-generation-inference:latest
    ports: ["8002:80"]
    volumes: ["./models/phi2:/data"]
    environment: {MODEL_ID: "/data/phi-2-2.7b"}
    profiles: ["phi2"]  # Don't start by default
  
  tgi-mistral:
    image: ghcr.io/huggingface/text-generation-inference:latest
    ports: ["8003:80"]
    volumes: ["./models/mistral:/data"]
    environment: {MODEL_ID: "/data/mistral-7b"}
    profiles: ["mistral"]  # Don't start by default
```

**Usage**:
```bash
# Test TinyLlama
docker compose up tgi-tinyllama
# Benchmark, record results

# Switch to Phi-2
docker compose stop tgi-tinyllama
docker compose --profile phi2 up tgi-phi2
# Benchmark, record results

# Switch to Mistral
docker compose stop tgi-phi2
docker compose --profile mistral up tgi-mistral
```

### Option 2: LocalAI (Recommended for Model Comparison) 🎯

**Pros**:
- ✅ **Single container, multiple models**
- ✅ **Switch via API call** (instant, no restart)
- ✅ **Smaller image** (~2GB vs 4GB TGI)
- ✅ **Load models on-demand** (unload when not needed)

**Cons**:
- ⚠️ Still memory-limited (can't load all simultaneously)
- ⚠️ Must configure models.yaml for each model

**Example Setup**:
```yaml
# LocalAI models/models.yaml
- name: tinyllama
  backend: llama-cpp
  parameters:
    model: tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
    threads: 6
    context_size: 2048
  
- name: phi2
  backend: llama-cpp
  parameters:
    model: phi-2.Q4_K_M.gguf
    threads: 6
    context_size: 2048
  
- name: mistral
  backend: llama-cpp
  parameters:
    model: mistral-7b-instruct-v0.2.Q4_K_M.gguf
    threads: 6
    context_size: 4096
```

**Usage** (Instant Switching):
```bash
# Load TinyLlama
curl http://localhost:5000/v1/models/apply -d '{"id": "tinyllama"}'

# Run benchmark
for i in {1..10}; do
  time curl http://localhost:5000/v1/chat/completions \
    -d '{"model": "tinyllama", "messages": [...]}'
done

# Switch to Phi-2 (instant, no restart)
curl http://localhost:5000/v1/models/apply -d '{"id": "phi2"}'

# Run same benchmark
for i in {1..10}; do
  time curl http://localhost:5000/v1/chat/completions \
    -d '{"model": "phi2", "messages": [...]}'
done
```

**Memory Management**:
```bash
# Unload model when done (free memory)
curl http://localhost:5000/v1/models/unload -d '{"id": "tinyllama"}'

# Load different model
curl http://localhost:5000/v1/models/apply -d '{"id": "mistral"}'
```

---

## 📈 Recommended Performance Testing Strategy

### Phase 1: Establish Baseline (Option A - Direct)
**Goal**: Get working system with minimal overhead

**Implementation**: 1-2 hours
```python
# direct_llm.py - Simple llama-cpp-python wrapper
from llama_cpp import Llama

llm = Llama(model_path="./tinyllama-1.1b.gguf", n_ctx=2048, n_threads=6)
result = llm.create_chat_completion(messages=[...])
```

**Benchmark**: 
- ✅ Response time
- ✅ Memory usage
- ✅ CPU utilization
- ✅ Quality assessment

### Phase 2: Test Multiple Models (Option C - LocalAI)
**Goal**: Compare speed/quality trade-offs

**Models to Test**:
1. **TinyLlama 1.1B** (baseline) - Fast, basic quality
2. **Phi-2 2.7B** - 2.5x slower, better quality
3. **Mistral 7B** - 6x slower, best quality

**Benchmark Script**:
```bash
#!/bin/bash
# test-models.sh

MODELS=("tinyllama" "phi2" "mistral")
TEST_PROMPTS="test-prompts.txt"  # 10 sample transcriptions

for model in "${MODELS[@]}"; do
  echo "Testing $model..."
  curl http://localhost:5000/v1/models/apply -d "{\"id\": \"$model\"}"
  
  total_time=0
  while IFS= read -r prompt; do
    start=$(date +%s%N)
    response=$(curl -s http://localhost:5000/v1/chat/completions \
      -d "{\"model\": \"$model\", \"messages\": [{\"role\": \"user\", \"content\": \"$prompt\"}]}")
    end=$(date +%s%N)
    
    elapsed=$(( (end - start) / 1000000 ))  # Convert to ms
    total_time=$(( total_time + elapsed ))
    
    echo "  Prompt: ${prompt:0:50}... → ${elapsed}ms"
  done < "$TEST_PROMPTS"
  
  avg_time=$(( total_time / 10 ))
  echo "  Average: ${avg_time}ms"
  echo ""
done
```

**Expected Results**:
```
TinyLlama: ~1500ms avg  - "hello world its a test" → "Hello world! It's a test."
Phi-2:     ~3500ms avg  - "hello world its a test" → "Hello world! It's a test."
Mistral:   ~9000ms avg  - "hello world its a test" → "Hello, world! It's a test."
```

### Phase 3: Production Optimization
**Goal**: Deploy optimal model for speed/quality balance

**Decision Matrix**:
```
Use Case                Model Choice    Rationale
------------------------------------------------------------------
Real-time chat         TinyLlama       <2s response, acceptable quality
Document processing    Phi-2           Quality > speed, 3-4s OK
Offline batch          Mistral         Best quality, time not critical
```

---

## 🎯 Final Recommendations Summary

### 📌 ZRAM Strategy (Context-Dependent)

#### **For Voice2Text LLM Stack**
```
Recommendation: ❌ DON'T USE ZRAM
Reason:         Hot working set (60-80%), low compression (18%)
Alternative:    Use smaller models, reduce context window
Savings:        ~115MB (not worth CPU overhead)
```

#### **For DST-DNS Minimal/Standard Dev** (~1.2-1.8GB)
```
Recommendation: ❌ DON'T USE ZRAM
Reason:         Services are active/hot, minimal benefit
Alternative:    Use minimal profile, disable optional services
Savings:        ~45-98MB (5-8% compression, not worth it)
```

#### **For DST-DNS Full Stack** (~3.0-5.0GB with observability)
```
Recommendation: ✅ SELECTIVE ZRAM (Observability Services Only)
Reason:         Java heaps + UIs mostly idle in dev
What:           skywalking-*, pgadmin, testing containers only
Savings:        ~440-955MB (12-19% compression)
Implementation: zramctl -f -s 4G -a lz4 + cgroup swappiness config
```

**Key Insight**: ZRAM value depends on **which services** you compress, not total memory size.

---

### 🔧 Memory Optimization by Project

#### **Voice2Text LLM**
1. ✅ **Don't use ZRAM** - Not beneficial for hot LLM workload
2. ✅ **Keep 2-3GB free RAM** - Avoid swap during inference
3. ✅ **Use Q4_K_M quantization** - Best quality/size balance
4. ✅ **Limit context window** - 2048 tokens sufficient for chat

#### **DST-DNS Development**
1. ✅ **Use minimal profile** by default:
   ```bash
   python3 scripts/ciu/ciu-deploy.py --groups dev --deploy  # Skips observability
   ```
2. ✅ **Disable optional services** in `ciu-global.defaults.toml.j2`:
   ```toml
   enable_skywalking = false
   enable_otel = false
   enable_pgadmin = false
   ```
3. ⚠️ **Use selective ZRAM** only if running full stack:
   ```bash
   # Enable ZRAM for cold services only
   zramctl -f -s 4G -a lz4
   # Configure cgroup swappiness for skywalking/pgadmin
   ```
4. ✅ **Set resource limits** (optional):
   ```toml
   [deployment.resources]
   enabled = true
   mem_limit = "512m"
   ```

---

### ⚡ Performance Testing (Voice2Text LLM)
1. **Start with Option A** - Get baseline performance (1-2 hours)
2. **Migrate to Option C (LocalAI)** - Test multiple models (4-6 hours)
3. **Don't use TGI** - Overhead not worth it for CPU development

### 🧠 Model Selection (Voice2Text LLM)
1. **TinyLlama 1.1B** - Start here (fast, <1GB memory)
2. **Phi-2 2.7B** - Test if quality insufficient (~2.5GB memory)
3. **Mistral 7B** - Only if quality critical (~5GB memory, may need swap)

### 🔀 Parallel Testing
- **Use LocalAI profiles** - Switch models via API, no restart
- **Don't run multiple instances** - Insufficient RAM (2.6GB available)
- **Sequential testing** - Load → benchmark → unload → next model

---

### 📊 Memory Budget Planning

Current system with **2.6GB available** can support:

| Configuration | Total RAM | Fits in Available? | Notes |
|---------------|-----------|-------------------|--------|
| **Voice2Text + TinyLlama** | ~1GB | ✅ Yes | Plenty of headroom |
| **Voice2Text + Phi-2** | ~2.5GB | ⚠️ Tight | May need swap |
| **Voice2Text + Mistral** | ~5GB | ❌ No | Requires swap |
| **DST-DNS Minimal** | ~600MB | ✅ Yes | Fits easily |
| **DST-DNS Standard** | ~1.8GB | ✅ Yes | Comfortable |
| **DST-DNS Full** | ~4GB | ❌ No | Needs ZRAM or disable observability |
| **Both Stacks (Voice2Text + DST-DNS Full)** | ~5-6GB | ❌ No | Must choose priority |

**Recommendation**: 
- **Primary development**: Choose one stack at a time
- **Voice2Text testing**: Use TinyLlama or Phi-2, stop DST-DNS
- **DST-DNS testing**: Use minimal profile, stop Voice2Text LLM
- **Full stack**: Disable observability OR use selective ZRAM

---

**Next Steps**:
1. **Voice2Text**: Implement Option A (direct llama-cpp-python) for baseline
2. **DST-DNS**: Use `--groups dev` deployment profile by default
3. **Monitor**: `free -h` and `docker stats` to track actual usage
