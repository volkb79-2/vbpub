# Port Standardization Summary

**Date**: December 9, 2025  

## Changes Made

### 1. Whisper Service
- **Old**: Port 9000
- **New**: Port 80
- **Files Changed**:
  - `whisper-trans/docker-compose.yml`: 
    - `WHISPER_PORT=9000` → `WHISPER_PORT=80`
    - Health check updated to `localhost:80`

### 2. Speech-to-Copilot API
- **Old**: Port 8000
- **New**: Port 80
- **Files Changed**:
  - `speech-to-copilot/docker-compose.yml`:
    - `uvicorn --port 8000` → `uvicorn --port 80`
    - Health check updated to `localhost:80`
    - Service URLs updated:
      - `WHISPER_SERVICE_URL=http://whisper-service:80`
      - `LLM_SERVICE_URL=http://openai-shim:80`

### 3. OpenAI Shim
- **Old**: Port 8300
- **New**: Port 80
- **Files Changed**:
  - `oobabooga-llm/docker-compose.yml`: Health check updated to `localhost:80`
  - `oobabooga-llm/shim/Dockerfile`:
    - `EXPOSE 8300` → `EXPOSE 80`
    - `--port 8300` → `--port 80`

### 4. Nginx Reverse Proxy
- **Files Changed**: `reverse-proxy/etc-nginx/nginx.conf`
- Updated all proxy_pass directives:
  - `/api/` → `http://speech-to-copilot-api:80/`
  - `/docs/` → `http://speech-to-copilot-api:80/docs`
  - `/whisper/` → `http://whisper-service:80/`
  - `/llm/` → `http://openai-shim:80/`
  - `/ws/` → `http://speech-to-copilot-api:80/ws/`

## Unchanged Services

### LLM WebUI (oobabooga)
- **Port**: 5000 (unchanged)
- **Reason**: Internal service, not exposed via reverse proxy
- **Access**: Only accessed by shim (llm-webui:5000)

### Redis
- **Port**: 6379 (unchanged)
- **Reason**: Standard Redis port, not HTTP service

### Reverse Proxy (nginx)
- **External Ports**: 80 (HTTP), 443 (HTTPS) - unchanged
- **Reason**: Standard web ports for external access

## Architecture Benefits

### Before (Mixed Ports)
```
┌─────────────────────┐
│  Reverse Proxy      │ :80, :443
└─────────────────────┘
          │
    ┌─────┴─────┬─────────────┬──────────────┐
    │           │             │              │
┌───▼────┐  ┌───▼────┐  ┌────▼─────┐  ┌─────▼────┐
│  API   │  │Whisper │  │   Shim   │  │   Redis  │
│ :8000  │  │ :9000  │  │  :8300   │  │  :6379   │
└────────┘  └────────┘  └──────────┘  └──────────┘
   ⚠️ Inconsistent ports for HTTP services
```

### After (Standardized)
```
┌─────────────────────┐
│  Reverse Proxy      │ :80 (internal), :443 (external)
└─────────────────────┘
          │
    ┌─────┴─────┬─────────────┬──────────────┐
    │           │             │              │
┌───▼────┐  ┌───▼────┐  ┌────▼─────┐  ┌─────▼────┐
│  API   │  │Whisper │  │   Shim   │  │   Redis  │
│  :80   │  │  :80   │  │   :80    │  │  :6379   │
└────────┘  └────────┘  └──────────┘  └──────────┘
   ✅ All HTTP services use standard port 80
```

## Benefits

1. **Consistency**: All HTTP services use the same internal port (80)
2. **Industry Standard**: Port 80 is the default HTTP port (RFC 7230)
3. **Configuration Simplicity**: Easier to remember and document
4. **Container Communication**: No need to remember service-specific ports
5. **Best Practice Alignment**: Follows DST-DNS project conventions

## Testing Required

After applying these changes, test:

```bash
# 1. Rebuild services with port changes
cd /workspaces/dstdns/legacy-experiments/voice2text-ai
docker compose -f whisper-trans/docker-compose.yml build
docker compose -f speech-to-copilot/docker-compose.yml build
docker compose -f oobabooga-llm/docker-compose.yml build

# 2. Restart all stacks
./stop-all-stacks.sh --yes
./start-all-stacks.sh

# 3. Verify health checks
docker ps --filter network=voice2text-prod-network

# 4. Test external access via reverse proxy
curl -k https://localhost:9443/health
curl -k https://localhost:9443/api/health
curl -k https://localhost:9443/whisper/docs
curl -k https://localhost:9443/llm/health

# 5. Test transcription workflow
# (upload audio, verify LLM post-processing works)
```

## Related Documentation

- **Main Project**: `/workspaces/dstdns/.github/copilot-instructions.md` (Port Standards section)
- **Implementation Status**: `IMPLEMENTATION-STATUS.md` (Updated with detailed LLM option analysis)
- **DST-DNS Pattern**: All custom HTTP services use port 8080 (we use 80 for simplicity in this legacy project)
