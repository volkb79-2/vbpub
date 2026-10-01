# Voice2Text AI - Integrated Speech Recognition & Enhancement

> **Updated Architecture (Dec 2025)**: Now using modern CIU standards with shared 
> global config, Jinja2 templates, and unified reverse proxy for TLS access.
> 
> **Standalone Project**: This is a self-contained project with its own `ciu-global.defaults.toml.j2` -
> NOT integrated with the main DST-DNS global config.

A comprehensive speech-to-text pipeline with AI-powered post-processing, designed for developer workflows and GitHub Copilot integration.

## 🏗️ Architecture Overview

> **📊 Detailed Architecture**: See [ARCHITECTURE.md](ARCHITECTURE.md) for comprehensive diagrams including:
> - System architecture with all components
> - Network topology and communication protocols
> - Container boundaries and Docker network
> - Persistence volumes and data flow
> - Swimlane diagram for user voice processing flow

This system consists of four integrated stacks communicating via a shared Docker network:

```
┌──────────────────────────────────────────────────────────────┐
│  reverse-proxy (External HTTPS Access)                       │
│  - Single TLS endpoint for all services                      │
│  - HTTPS on port 8443                                        │
│  - Routes to all backend services                           │
└──────────────┬───────────────────────────────────────────────┘
               │ voice2text-network (internal HTTP)
               ├─────────────┬─────────────┬──────────────────
               ▼             ▼             ▼
┌────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│  speech-to-copilot │  │  whisper-trans       │  │  oobabooga-llm       │
│  - FastAPI API     │  │  - Whisper ASR API   │  │  - LLM enhancement   │
│  - WebSocket       │  │  - Faster-Whisper    │  │  - Grammar correction│
│  - Repository scan │  │  - CPU optimized     │  │  - OpenAI-compatible │
│  - Redis cache     │  │  - Word timestamps   │  │  - GGUF models       │
└────────────────────┘  └──────────────────────┘  └──────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

```bash
# Ensure Docker and Docker Compose are installed
docker --version
docker compose version

# Recommended: TLS certificates for HTTPS (optional - self-signed fallback available)
# See docs/0-prerequisites.md for Let's Encrypt setup
```

### Start All Stacks

```bash
cd legacy-experiments/voice2text-ai

# Start all four stacks in the correct order
./start-all-stacks.sh
```

This will:
1. Create shared `voice2text-prod-network` for inter-stack communication (from global config)
2. Start **whisper-trans** (transcription service)
3. Start **oobabooga-llm** (LLM enhancement service)
4. Start **speech-to-copilot** (API orchestration)
5. Start **reverse-proxy** (external HTTPS access)

### Access Points

> **Note**: Examples use `localhost` for local development. For remote access, replace `localhost` with your server's FQDN or IP address (e.g., `https://your-server.example.com:8443/` or `https://192.168.1.100:8443/`).

**Via Reverse Proxy (Recommended):**
- **Service Landing Page**: `https://localhost:8443/` (or `https://<your-host>:8443/`)
- **Speech-to-Copilot API**: https://localhost:8443/api/
- **API Documentation**: https://localhost:8443/docs/
- **Whisper API**: https://localhost:8443/whisper/
- **LLM Service**: https://localhost:8443/llm/

**Direct Access (if ports exposed):**
- Speech-to-Copilot API: http://localhost:8000/health
- Whisper API Docs: http://localhost:9000/docs
- LLM Shim: http://localhost:8300/health

### Stop All Stacks

```bash
./stop-all-stacks.sh
```

## 📦 Individual Stacks

All stacks use the standardized `CIU` workflow with Jinja2 templates and TOML configuration.

### Configuration Structure

```
voice2text-ai/
├── ciu-global.defaults.toml.j2    # Shared config for all stacks (project-level)
├── ciu-global.toml                # Generated active config (gitignored)
├── start-all-stacks.sh            # Start all stacks in correct order
├── stop-all-stacks.sh             # Stop all stacks
├── whisper-trans/
│   ├── ciu.defaults.toml.j2       # Stack-specific overrides
│   ├── docker-compose.yml.j2      # Jinja2 template
│   └── CIU -> ../../../scripts/ciu/ciu.py
├── oobabooga-llm/
│   └── ...
├── speech-to-copilot/
│   └── ...
└── reverse-proxy/
    └── ...
```

### whisper-trans

Whisper transcription service for speech-to-text conversion.

**Features:**
- Faster-Whisper engine for CPU-optimized performance
- Multiple model sizes (base, medium, large-v3)
- VAD filtering for noise reduction
- Word timestamps for precise timing
- Internal HTTP API (accessed via reverse proxy for HTTPS)

**Configuration:**
- Edit `whisper-trans/ciu.defaults.toml.j2`
- Uses Jinja2 template: `docker-compose.yml.j2`
- Model storage: `./vol-whisper-cache/`
- Audio temp: `./vol-whisper-audio-temp/`

**Standalone start:**
```bash
cd whisper-trans
python3 ../scripts/ciu/ciu.py --root-folder .. -d .
```

See: [whisper-trans/README.md](whisper-trans/README.md)

### oobabooga-llm

Local LLM service for text post-processing and enhancement.

**Features:**
- CPU-only operation (no GPU required)
- GGUF model support (TinyLlama, Phi-2, Mistral-7B)
- OpenAI-compatible API endpoint
- Grammar and punctuation correction
- Context-aware refinement

**Configuration:**
- Edit `oobabooga-llm/ciu.defaults.toml.j2`
- Uses Jinja2 template: `docker-compose.yml.j2`
- Model storage: `./vol-oobabooga-models/`
- Cache: `./vol-oobabooga-cache/`

**Standalone start:**
```bash
cd oobabooga-llm
python3 ../scripts/ciu/ciu.py --root-folder .. -d .
```

See: [oobabooga-llm/README.md](oobabooga-llm/README.md)

### speech-to-copilot

Web interface and API orchestration layer.

**Features:**
- FastAPI backend with WebSocket support
- Demo mode for testing without external services
- Repository context scanning for technical terms
- Real-time transcription streaming
- Redis caching

**Configuration:**
- Edit `speech-to-copilot/ciu.defaults.toml.j2`
- Uses Jinja2 template: `docker-compose.yml.j2`
- Set `demo_mode = false` to use real whisper/oobabooga services
- Logs: `./vol-speech-api-logs/`

**Standalone start:**
```bash
cd speech-to-copilot
python3 ../scripts/ciu/ciu.py --root-folder .. -d .
```

See: [speech-to-copilot/README.md](speech-to-copilot/README.md)

### reverse-proxy

HTTPS reverse proxy for secure external access to all services.

**Features:**
- Single HTTPS endpoint (port 8443)
- TLS/SSL termination
- Routes to all backend services
- Self-signed certificate fallback
- Service landing page

**Configuration:**
- Edit `reverse-proxy/ciu.defaults.toml.j2`
- Uses Jinja2 template: `docker-compose.yml.j2`
- nginx.conf rendered from nginx.conf.j2 template
- Logs: `./vol-reverse-proxy-logs/`

**Standalone start:**
```bash
cd reverse-proxy
python3 ../scripts/ciu/ciu.py --root-folder .. -d .
```

## 🔧 Configuration

All stacks use the standardized `CIU` workflow with TOML configuration:

```bash
# Ensure workspace environment is generated (one-time per machine)
bash ../env-workspace-setup-generate.sh

# Start a stack from its directory
cd <stack-directory>
python3 ../scripts/ciu/ciu.py --root-folder .. -d .
```

### Shared Network

All stacks connect to the network configured in `.env.ciu` (`DOCKER_NETWORK_INTERNAL`) for internal HTTP communication:

```yaml
networks:
  voice2text-network:
    external: true
    name: ${DOCKER_NETWORK_INTERNAL}
```

Create the network manually if needed (use the value from `.env.ciu`):
```bash
docker network create <DOCKER_NETWORK_INTERNAL>
```

### Configuration Pattern

Each stack follows the same structure:

```toml
# compose.config.sample.toml
[metadata]
project_name = "stack-name"
env_tag = "prod"

[global]
project_name = "Voice2Text-AI"
environment = "prod"

[global.labels]
prefix = "de.vxxu.volkb79"

[infrastructure]
public_fqdn = "auto-detected"  # Via reverse DNS lookup
public_tls_key_pem = "/etc/letsencrypt/live/.../privkey.pem"
public_tls_crt_pem = "/etc/letsencrypt/live/.../fullchain.pem"

[service_name]
# Service-specific configuration
port = 8000
hostdir_logs = "./vol-service-logs"  # Auto-created with correct permissions

[health]
interval = "10s"
timeout = "5s"
retries = 3
start_period = "20s"

[hooks]
pre_compose = []   # Scripts to run before docker compose up
post_compose = []  # Scripts to run after services start

[env]
UID = "$(id -u)"
GID = "$(id -g)"
```

## 🔐 TLS/HTTPS Setup

The reverse proxy handles all TLS/HTTPS termination for external access.

### Option 1: Let's Encrypt (Recommended for Production)

If Let's Encrypt certificates exist, they will be automatically mounted:

```bash
# Auto-detect domain via reverse DNS
PUBLIC_IP=$(curl -s https://api.ipify.org/)
CERT_DOMAIN=$(dig +short -x "$PUBLIC_IP" | sed 's/\.$//')

# Get certificate
certbot certonly --standalone -d "$CERT_DOMAIN"

# Certificates will be automatically detected at:
# /etc/letsencrypt/live/$CERT_DOMAIN/privkey.pem
# /etc/letsencrypt/live/$CERT_DOMAIN/fullchain.pem
```

Grant Docker access to certificates:
```bash
# Add docker group read access
sudo chgrp -R docker /etc/letsencrypt/archive /etc/letsencrypt/live
sudo chmod 750 /etc/letsencrypt/archive /etc/letsencrypt/live
sudo chmod 640 /etc/letsencrypt/archive/$CERT_DOMAIN/privkey*.pem
```

### Option 2: Self-Signed Certificates (Automatic Fallback)

If no Let's Encrypt certificates are found, the reverse proxy automatically generates self-signed certificates on first start.

**To trust the generated CA certificate:**

```bash
# Extract CA certificate from Docker volume
docker run --rm -v voice2text-proxy-certs:/certs alpine cat /certs/ca-cert.pem > ca-cert.pem

# Install on Ubuntu/Debian
sudo cp ca-cert.pem /usr/local/share/ca-certificates/voice2text-ca.crt
sudo update-ca-certificates

# Install on macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ca-cert.pem

# Install in browser (Firefox)
# Settings → Privacy & Security → Certificates → View Certificates → Import
```

### Reverse Proxy Configuration

Edit `reverse-proxy/compose.config.sample.toml`:

```toml
[infrastructure]
# Auto-detected via reverse DNS, or set manually
public_fqdn = "your-domain.example.com"
public_tls_key_pem = "/etc/letsencrypt/live/${PUBLIC_FQDN}/privkey.pem"
public_tls_crt_pem = "/etc/letsencrypt/live/${PUBLIC_FQDN}/fullchain.pem"

[proxy]
external_port = 8443  # External HTTPS port
http_port = 8080      # HTTP redirect port
```

## 🎤 Audio Recording

### Browser-Based Recording (Current)

The webapp uses browser MediaRecorder API:
- **Requires HTTPS** for microphone access (browser security requirement)
- Works in Chrome, Firefox, Edge
- WebSocket streaming for real-time transcription

### Why Microphone Recording May Fail

**1. CORS Issues**

CORS (Cross-Origin Resource Sharing) is **not the issue** for microphone access. Microphone permission is a browser security feature, not a CORS issue.

**2. Missing TLS/HTTPS Setup (Main Cause)**

Browsers **require HTTPS** for microphone access for security reasons. If you're accessing via HTTP, the microphone will be blocked.

**Solution:**
- Use the reverse proxy: `https://localhost:8443/`
- Trust the self-signed certificate (see TLS setup above)
- Or use Let's Encrypt certificates for production

**Testing:**
```bash
# ❌ This will fail (HTTP)
curl http://localhost:8000/

# ✅ This will work (HTTPS via reverse proxy)
curl -k https://localhost:8443/api/
```

### Browser Security Requirements

Modern browsers require:
1. **HTTPS connection** OR localhost
2. **User permission** (prompt on first access)
3. **Secure context** (no mixed content)

### Testing Microphone Access

1. **Check Browser Console** (F12)
   ```javascript
   // Should work on HTTPS
   navigator.mediaDevices.getUserMedia({ audio: true })
     .then(stream => console.log('✓ Microphone access granted'))
     .catch(err => console.error('✗ Microphone access denied:', err))
   ```

2. **Expected Errors on HTTP:**
   ```
   NotAllowedError: Permission denied
   NotSupportedError: Only secure origins are allowed
   ```

3. **Access via HTTPS:**
   ```bash
   # Use reverse proxy
   https://localhost:8443/
   ```

### Alternative Recording Methods

If browser recording doesn't work, see:
- **[AUDIO-RECORDING-ALTERNATIVES.md](AUDIO-RECORDING-ALTERNATIVES.md)** - Comprehensive guide to alternatives
- **Desktop apps** (Tauri, Electron, Flutter)
- **VSCode extension** (planned)
- **CLI tools** (sox, ffmpeg)
- **Mobile apps** (React Native, Flutter)

### Recommended Approach: Desktop App or VSCode Extension

For best results outside the browser:
- **Desktop App**: Use Tauri (3-5 MB, native performance) - see [WINDOWS-CLIENT-OPTIONS.md](WINDOWS-CLIENT-OPTIONS.md)
- **VSCode Extension**: Direct IDE integration, no browser required
- **CLI Tool**: Simple scriptable interface

## 🐛 Troubleshooting

### Services Not Starting

```bash
# Check logs for each stack
cd <stack-directory>
docker compose logs -f

# Check network connectivity
docker network inspect voice2text-network
```

### Audio Not Recording

1. **Check HTTPS**: Microphone requires HTTPS (not HTTP)
   - ✅ Use: `https://localhost:8443/`
   - ❌ Not: `http://localhost:8000/`

2. **Trust Certificate**: Install self-signed CA certificate
   ```bash
   docker run --rm -v voice2text-proxy-certs:/certs alpine cat /certs/ca-cert.pem > ca-cert.pem
   # Then install in your system/browser
   ```

3. **Browser Permissions**: Allow microphone access when prompted

4. **Check Console**: Open browser DevTools → Console for errors

5. **Try Different Browser**: Chrome/Edge generally have best support

### Transcription Not Working

```bash
# Test whisper-trans directly via reverse proxy
curl -k -X POST -F "audio_file=@test.wav" https://localhost:8443/whisper/asr

# Check if services can communicate internally
docker exec speech-to-copilot-api curl http://whisper-service:9000/docs
docker exec speech-to-copilot-api curl http://openai-shim:8300/health
```

### TLS Certificate Issues

```bash
# Verify certificate paths
ls -la /etc/letsencrypt/live/YOUR_DOMAIN/

# Check certificate permissions
sudo chmod 640 /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem
sudo chgrp docker /etc/letsencrypt/live/YOUR_DOMAIN/privkey.pem

# View reverse proxy logs
docker logs voice2text-reverse-proxy
```

### Service Communication Errors

```bash
# Verify all services are on the same network
docker network inspect voice2text-network

# Check if service names resolve
docker exec speech-to-copilot-api ping -c 1 whisper-service
docker exec speech-to-copilot-api ping -c 1 openai-shim

# Check service health
curl -k https://localhost:8443/api/health | jq
```

## 📊 Monitoring

### Service Health

```bash
# Check all services
docker compose ps

# View logs
docker compose logs -f [service-name]

# Check resource usage
docker stats
```

### API Health Checks

```bash
# Whisper-trans
curl http://localhost:9000/docs

# Oobabooga (if running)
curl http://localhost:8300/health

# Speech-to-Copilot
curl http://localhost:8000/health | jq
```

## 🔄 Updates & Maintenance

### Update Docker Images

```bash
# Pull latest images
cd <stack-directory>
docker compose pull

# Restart services
docker compose down
docker compose up -d
```

### Clean Up

```bash
# Stop all stacks
./stop-all-stacks.sh

# Remove volumes (WARNING: deletes data)
cd whisper-trans && docker compose down -v
cd ../oobabooga-llm && docker compose down -v
cd ../speech-to-copilot && docker compose down -v

# Remove network
docker network rm voice2text-network
```

## 📝 Development

### Demo Mode

Test without external services using demo mode:

**What is Demo Mode?**
- Simulates transcription without calling Whisper
- Simulates enhancement without calling LLM
- Returns predictable dummy responses
- Perfect for development, testing, and CI/CD

**Enable Demo Mode:**
```bash
cd speech-to-copilot
# Edit compose.config.sample.toml: set demo_mode = true
python3 ../../scripts/ciu/ciu.py
```

**Test Demo Mode:**
```bash
# Check health (should show demo_mode: true)
curl -s http://localhost:8000/health | jq

# Test transcription endpoint
curl -s -X POST http://localhost:8000/api/transcribe \
  -H "Content-Type: application/json" \
  -d '{"audio_data": "fake_audio", "format": "wav"}' | jq
```

**Full Documentation:** [DEMO-MODE-EXPLAINED.md](DEMO-MODE-EXPLAINED.md)

### Project-Specific Vocabulary & Technical Terms

**Yes, you can customize Whisper for better IT/technical term recognition!**

Multiple approaches available:
1. **Post-Processing Correction** (Recommended)
   - Repository scanner extracts technical terms from codebase
   - Phonetic matching fixes misheard terms
   - Vocabulary file for custom terms

2. **Whisper Initial Prompt**
   - Guide Whisper with expected technical terms
   - Limited to ~150 words but effective

3. **Fine-Tuned Model** (Advanced)
   - Train custom Whisper model on your domain
   - Requires 10+ hours of labeled audio

**Example Configuration:**
```toml
# speech-to-copilot/compose.config.sample.toml
[api]
enable_vocabulary_correction = true
vocabulary_source = "hybrid"  # repository + file
vocabulary_file_path = "/workspace/tech-vocab.txt"
enable_phonetic_matching = true
phonetic_algorithm = "metaphone"
```

**Full Documentation:** [WHISPER-CUSTOMIZATION.md](WHISPER-CUSTOMIZATION.md)

### Self-Correction During Dictation

**Handling Misspeaking:**

When you speak: *"connect to MySQL... I misspoke, I meant PostgreSQL"*

The system can:
1. Detect correction phrases: "I misspoke", "I meant", "correction"
2. Extract corrected term: "PostgreSQL"
3. Replace previous term in transcript
4. Update vocabulary for future use

**Enable Self-Correction:**
```toml
[api]
enable_self_correction = true
correction_phrases = ["I misspoke", "I meant", "correction", "actually"]
correction_window = 30  # seconds to look back
```

**Works with Streaming:**
- Buffers last 30 seconds of text
- Detects correction in real-time
- Sends diff update to client via WebSocket

**Full Documentation:** [WHISPER-CUSTOMIZATION.md#error-correction-self-adjustment](WHISPER-CUSTOMIZATION.md#error-correction-self-adjustment)

### Adding New Features

1. Modify service code in respective directories
2. Rebuild if needed: `docker compose build`
3. Restart: `docker compose up -d`

### Windows Desktop Client

**Want a standalone Windows application?**

Top 3 framework recommendations:
1. **🥇 Tauri** (Rust + Web) - 3-5 MB, best performance
2. **🥈 Flutter** (Dart) - 15-25 MB, beautiful UI
3. **🥉 Python + PyQt6** - 40-60 MB, rapid development

**Cross-Compilation:**
All three can be built on Linux to produce Windows .exe files using Docker containers.

**Full Documentation:** [WINDOWS-CLIENT-OPTIONS.md](WINDOWS-CLIENT-OPTIONS.md)

## 🧪 Testing

### Full Toolchain Test

Test the complete pipeline with all services:

```bash
# 1. Start all services
./start-all-stacks.sh

# 2. Wait for services to be healthy
sleep 30

# 3. Test via reverse proxy (HTTPS)
# Upload test audio file
curl -k -X POST https://localhost:8443/api/transcribe \
  -F "audio_file=@whisper-trans/test.wav" \
  -o transcription_result.json

# Check result
cat transcription_result.json | jq

# 4. Test WebSocket streaming (requires ws_demo.py)
cd speech-to-copilot/api/scripts
python3 ws_demo.py wss://localhost:8443/ws/audio

# 5. Test individual services
curl -k https://localhost:8443/whisper/docs  # Whisper API docs
curl -k https://localhost:8443/llm/health    # LLM health check
curl -k https://localhost:8443/api/health    # Main API health
```

### Service-to-Service Communication

Verify internal HTTP communication:

```bash
# From speech-to-copilot to whisper-service
docker exec speech-to-copilot-api \
  curl -X POST http://whisper-service:9000/asr \
  -F "audio_file=@/tmp/test.wav"

# From speech-to-copilot to LLM
docker exec speech-to-copilot-api \
  curl http://openai-shim:8300/v1/models
```

### TLS/HTTPS Validation

Test TLS setup for browser microphone access:

```bash
# 1. Check certificate
openssl s_client -connect localhost:8443 -showcerts

# 2. Test HTTPS access
curl -k https://localhost:8443/

# 3. In browser, navigate to:
https://localhost:8443/

# 4. Check browser console (F12):
# - Should see no mixed content warnings
# - Should be able to request microphone permission
```

### Streaming Transcription Test

Test real-time audio streaming:

```bash
# 1. Record audio with streaming
cd speech-to-copilot

# 2. Use WebSocket demo script
python3 api/scripts/ws_demo.py

# Expected output:
# Connected to WebSocket
# Sending audio chunk 1...
# Received partial: "def calculate"
# Sending audio chunk 2...
# Received partial: "def calculate_sum"
# ...
# Received final: "def calculate_sum(a, b):\n    return a + b"
```

### Automated Testing

```bash
# Run integration tests (if available)
cd speech-to-copilot
python3 integration-test.sh

# Or use test scripts
./test-all-services.sh
```

## 📊 Monitoring

### Service Health

```bash
# Check all services status
docker compose ps

# View logs
docker compose logs -f [service-name]

# Check resource usage
docker stats
```

### API Health Checks

```bash
# All services via reverse proxy
curl -k https://localhost:8443/api/health | jq
curl -k https://localhost:8443/whisper/docs
curl -k https://localhost:8443/llm/health

# Direct access (if ports exposed)
curl http://localhost:8000/health | jq
curl http://localhost:9000/docs
curl http://localhost:8300/health
```

## 🔗 Integration

### With External Hosts

The webapp can run on an external host and securely access services via the reverse proxy:

```bash
# Configure client to use reverse proxy
VOICE2TEXT_API_URL=https://server.example.com:8443/api/
```

### VSCode Extension

Use the REST API from VSCode extension:

```typescript
// POST audio to transcription endpoint via reverse proxy
const response = await fetch('https://your-server:8443/api/transcribe', {
  method: 'POST',
  headers: { 
    'Content-Type': 'application/json',
    'Authorization': 'Bearer your-token'  // if auth enabled
  },
  body: JSON.stringify({
    audio_data: base64Audio,
    format: 'wav',
    enable_context: true,
    enable_enhancement: true
  })
});
```

## 📚 Further Reading

### Project Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - Comprehensive architecture diagrams and system design
- **[DEMO-MODE-EXPLAINED.md](DEMO-MODE-EXPLAINED.md)** - Complete guide to demo mode functionality
- **[WHISPER-CUSTOMIZATION.md](WHISPER-CUSTOMIZATION.md)** - Customize Whisper for technical terms and self-correction
- **[AUDIO-RECORDING-ALTERNATIVES.md](AUDIO-RECORDING-ALTERNATIVES.md)** - Alternative audio capture methods
- **[WINDOWS-CLIENT-OPTIONS.md](WINDOWS-CLIENT-OPTIONS.md)** - Windows desktop client frameworks and build strategies

### External Documentation

- [Whisper ASR Documentation](https://github.com/openai/whisper)
- [Oobabooga Text Generation WebUI](https://github.com/oobabooga/text-generation-webui)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
- [Deployment Guide](../../docs/DEPLOYMENT-GUIDE.md)

## 🤝 Contributing

1. Make changes in feature branches
2. Test with `./start-all-stacks.sh`
3. Update documentation
4. Submit pull request

## 📄 License

See main repository LICENSE file.
