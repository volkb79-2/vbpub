# Voice2Text AI - Deployment Status

**Date**: December 11, 2025  

## Successfully Deployed Services

All services running on Docker network `voice2text-prod-network` and accessible via HTTPS:

| Service | Container | Status | Internal Port | Purpose |
|---------|-----------|--------|---------------|---------|
| **Redis** | voice2text-redis | ✅ Healthy | 6379 | Message broker |
| **LocalAI** | voice2text-prod-localai | ✅ Healthy | 80 | AI inference engine |
| **Speech API** | voice2text-api | ✅ Healthy | 80 | Main API service |
| **Whisper** | voice2text-whisper-service | ✅ Healthy | 9000 | Speech transcription |
| **Reverse Proxy** | voice2text-reverse-proxy | ⚠️  Running | 443/80 | HTTPS gateway |

## External Access (Production)

**Base URL**: `https://gstammtisch.dchive.de:9443/`

### Working Endpoints:
- ✅ **Health Check**: `/health` - System status
- ✅ **Whisper API**: `/whisper/docs` - Speech transcription service
- ✅ **LocalAI**: `/llm/readyz` - AI inference health
- ✅ **API Docs**: `/docs/` - API documentation

### Service Connectivity Tests:
```bash
# Internal connectivity (all passing)
API → Redis: ✓ Connected
API → Whisper: ✓ Connected  
API → LocalAI: ✓ Connected

# External HTTPS access (all passing)
Health endpoint: ✓
Whisper docs: ✓
LLM readyz: ✓
```

## Architecture Changes

### Port Standardization (Completed)
All services now use standard ports internally:
- **Custom HTTP services**: Port 80 (API, LocalAI)
- **Whisper**: Port 9000 (image-defined)
- **External access**: Port 9443 (HTTPS via reverse proxy)

### Configuration Updates
- ✅ Removed oobabooga-llm stack (replaced with LocalAI)
- ✅ Fixed OpenAI shim references in nginx config
- ✅ Standardized health check endpoints
- ✅ Configured Let's Encrypt TLS certificates

## Windows Client Build

### Cross-Compilation Setup
Docker image created for building Windows executables on Linux:
- **Image**: `tauri-win-build:latest`
- **Target**: x86_64-pc-windows-gnu
- **Toolchain**: Rust + MinGW-w64 + Tauri CLI

### Build Instructions
```bash
cd /workspaces/dstdns/legacy-experiments/voice2text-ai/tauri-client

# Build Windows executable
docker run --rm tauri-win-build:latest

# Extract executable from container
docker run --rm -v $(pwd)/dist:/output tauri-win-build:latest \
  cp /app/src-tauri/target/x86_64-pc-windows-gnu/release/*.exe /output/
```

### Known Issue
Current build hangs at "Installing Node.js dependencies" - likely npm network/cache issue.

**Workaround**: Run build interactively with shell access:
```bash
docker run --rm -it --entrypoint /bin/bash tauri-win-build:latest
# Inside container:
cd /app
npm install --verbose
npm run build
cd src-tauri
cargo tauri build --target x86_64-pc-windows-gnu
```

## Testing Commands

### Start Stack
```bash
cd /workspaces/dstdns/legacy-experiments/voice2text-ai
for dir in speech-to-copilot localai whisper-trans reverse-proxy; do
  cd $dir && python3 /workspaces/dstdns/scripts/ciu/ciu.py && cd ..
done
```

### Stop Stack
```bash
docker stop $(docker ps --filter "name=voice2text" -q)
docker rm $(docker ps -a --filter "name=voice2text" -q)
```

### Test Endpoints
```bash
# Health check
curl -k https://gstammtisch.dchive.de:9443/health

# Whisper API docs
curl -k https://gstammtisch.dchive.de:9443/whisper/docs

# LocalAI status
curl -k https://gstammtisch.dchive.de:9443/llm/readyz
```

## Next Steps

1. ✅ **COMPLETE**: Deploy LocalAI stack
2. ✅ **COMPLETE**: Configure reverse proxy for all services
3. ✅ **COMPLETE**: Test external HTTPS access
4. ⏳ **IN PROGRESS**: Build Windows client executable
5. ⏳ **TODO**: Package and distribute Windows client
6. ⏳ **TODO**: Create user documentation

## Files Modified

- `reverse-proxy/etc-nginx/nginx.conf` - Updated service routes
- `speech-to-copilot/docker-compose.yml.j2` - Fixed health check port
- `whisper-trans/docker-compose.yml.j2` - Confirmed health check (port 9000)
- `localai/docker-compose.yml.j2` - Health check on port 80
- `tauri-client/Dockerfile.windows-build` - Created for cross-compilation

## Resources

- **Production URL**: https://gstammtisch.dchive.de:9443/
- **Docker Network**: voice2text-prod-network
- **TLS Certificates**: Let's Encrypt (auto-renewed)
- **Build Logs**: `/tmp/build-output.log`

---
**Deployment Team**: Automated via CIU  
**Last Updated**: December 11, 2025
