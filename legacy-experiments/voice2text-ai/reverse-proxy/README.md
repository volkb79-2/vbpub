# Voice2Text AI - Reverse Proxy

This reverse proxy provides a single HTTPS entry point for all Voice2Text AI services.

## Features

- **Single HTTPS Endpoint**: Port 9443 for all services
- **TLS Termination**: Handles SSL/TLS for all backend services
- **Service Routing**: Routes requests to appropriate backend services
- **Self-Signed Fallback**: Automatically generates certificates if Let's Encrypt not available
- **Internal HTTP**: Backend services communicate via HTTP on internal network

## Configuration

Edit `ciu.defaults.toml.j2`:

```toml
[deploy.env.shared]
# Populated by .env.ciu
PUBLIC_FQDN = "$PUBLIC_FQDN"
PUBLIC_TLS_KEY_PEM = "$PUBLIC_TLS_KEY_PEM"
PUBLIC_TLS_CRT_PEM = "$PUBLIC_TLS_CRT_PEM"

[services.reverse_proxy]
external_https_port = 9443

[proxy.routes.api]
path = "/api"
backend_service = "speech_api"
strip_path = false
```

## Deployment

### Start Standalone

```bash
cd reverse-proxy
python3 ../scripts/ciu/ciu.py --root-folder .. -d .
```

### Or Start with All Services

```bash
cd ..
./start-all-stacks.sh
```

## Access Points

Once running:
- **Service Landing Page**: https://localhost:9443/
- **Speech-to-Copilot API**: https://localhost:9443/api/
- **API Documentation**: https://localhost:9443/docs/
- **Whisper API**: https://localhost:9443/whisper/
- **LLM Service**: https://localhost:9443/llm/
- **WebSocket**: wss://localhost:9443/ws/audio

## TLS Certificates

### Option 1: Let's Encrypt (Recommended)

If certificates exist at `/etc/letsencrypt/live/`, they will be automatically mounted.

```bash
# Get certificate
certbot certonly --standalone -d your-domain.com

# Grant Docker access
sudo chgrp -R docker /etc/letsencrypt/archive /etc/letsencrypt/live
sudo chmod 750 /etc/letsencrypt/archive /etc/letsencrypt/live
```

### Option 2: Self-Signed (Automatic)

If no Let's Encrypt certificates found, self-signed certificates are automatically generated on first start.

**Trust the CA certificate:**

```bash
# Extract CA certificate
docker run --rm -v voice2text-proxy-certs:/certs alpine cat /certs/ca-cert.pem > ca-cert.pem

# Install on system
sudo cp ca-cert.pem /usr/local/share/ca-certificates/voice2text-ca.crt
sudo update-ca-certificates

# Or import in browser
# Firefox: Settings → Privacy & Security → Certificates → Import
```

## nginx Configuration

The nginx configuration is generated from `nginx.conf.j2` template:

```bash
# Render manually
./render_nginx_conf.py

# Output: nginx.conf
```

Template variables come from `compose.config.sample.toml`.

## Health Check

```bash
# Test reverse proxy
curl -k https://localhost:9443/health

# Test routing to backend services
curl -k https://localhost:9443/api/health
curl -k https://localhost:9443/whisper/docs
curl -k https://localhost:9443/llm/health
```

## Troubleshooting

### Port Already in Use

Change the port in `compose.config.sample.toml`:

```toml
[proxy]
external_port = 9443  # Use different port
```

### Certificate Errors

Check certificate generation:

```bash
# View logs
docker logs voice2text-reverse-proxy

# Manually generate certificates
docker exec voice2text-reverse-proxy sh /docker-entrypoint.d/90-generate-certs.sh

# Check certificates
docker run --rm -v voice2text-proxy-certs:/certs alpine ls -la /certs
```

### Backend Service Not Found

If a backend service returns 502 Bad Gateway:

```bash
# Ensure service is running
docker ps | grep whisper-service
docker ps | grep openai-shim
docker ps | grep speech-to-copilot-api

# Check if services are on the same network
docker network inspect voice2text-network
```

## Architecture

```
Internet (Port 8443)
       │
       ▼
[Reverse Proxy] (nginx:alpine)
   HTTPS ↓ HTTP (internal)
       ├──→ speech-to-copilot-api:8000
       ├──→ whisper-service:9000
       └──→ openai-shim:8300
```

## Security

- **TLS 1.2/1.3**: Modern TLS protocols only
- **Non-Root User**: Runs as non-root for security
- **High Ports**: Uses port 8443 (no root required)
- **Internal HTTP**: Backend services don't need TLS
- **Network Isolation**: Services communicate on isolated Docker network
