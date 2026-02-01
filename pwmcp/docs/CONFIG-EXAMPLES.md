# Configuration Examples

## 1) Local (direct ports, no TLS)

```dotenv
REGISTRY=ghcr.io
NAMESPACE=volkb79-2
IMAGE_NAME=pwmcp-server
VERSION=latest
BUILD_DATE=20260126

WS_EXTERNAL_PORT=3000
MCP_EXTERNAL_PORT=8765
HEALTH_EXTERNAL_PORT=8081

WS_PORT=3000
MCP_PORT=8765
HEALTH_PORT=8081
HEALTH_ENABLED=true
HEALTH_SOCKET_TIMEOUT=2
SELFTEST_URL=https://www.google.de
SELFTEST_TIMEOUT=5

AUTH_REQUIRED=true
ACCESS_TOKEN=replace-with-strong-token
MCP_ALLOWED_HOSTS=localhost,127.0.0.1
MCP_ALLOWED_ORIGINS=http://localhost,http://127.0.0.1
MCP_SERVER_NAME=pwmcp-server

PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_BROWSER=chromium
WS_MAX_SESSIONS=10
WS_SESSION_TIMEOUT=3600

SSL_ENABLED=false
SSL_CERT_PATH=/certs/server.crt
SSL_KEY_PATH=/certs/server.key

WORKSPACES_DIR=./vol-workspaces
CERTS_DIR=./certs
```

Run:

```bash
docker compose -f docker-compose.manual.yml up -d
```

## 2) Public with Reverse Proxy (TLS)

```dotenv
REGISTRY=ghcr.io
NAMESPACE=volkb79-2
IMAGE_NAME=pwmcp-server
VERSION=latest
BUILD_DATE=20260126

WS_EXTERNAL_PORT=3000
MCP_EXTERNAL_PORT=8765
HEALTH_EXTERNAL_PORT=8081

WS_PORT=3000
MCP_PORT=8765
HEALTH_PORT=8081
HEALTH_ENABLED=true
HEALTH_SOCKET_TIMEOUT=2
SELFTEST_URL=https://www.google.de
SELFTEST_TIMEOUT=5

AUTH_REQUIRED=true
ACCESS_TOKEN=replace-with-strong-token
MCP_ALLOWED_HOSTS=playwright.example.com
MCP_ALLOWED_ORIGINS=https://playwright.example.com
MCP_SERVER_NAME=pwmcp-server

PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_BROWSER=chromium
WS_MAX_SESSIONS=20
WS_SESSION_TIMEOUT=3600

SSL_ENABLED=false
SSL_CERT_PATH=/certs/server.crt
SSL_KEY_PATH=/certs/server.key

PUBLIC_FQDN=playwright.example.com
LETSENCRYPT_DIR=/etc/letsencrypt
REVERSE_PROXY_HTTP_PORT=80
REVERSE_PROXY_HTTPS_PORT=443

WORKSPACES_DIR=./vol-workspaces
CERTS_DIR=./certs
```

Run:

```bash
docker compose -f docker-compose.manual.yml --profile proxy up -d
```

## 3) Direct TLS (wss/https)

```dotenv
REGISTRY=ghcr.io
NAMESPACE=volkb79-2
IMAGE_NAME=pwmcp-server
VERSION=latest
BUILD_DATE=20260126

WS_EXTERNAL_PORT=3000
MCP_EXTERNAL_PORT=8765
HEALTH_EXTERNAL_PORT=8081

WS_PORT=3000
MCP_PORT=8765
HEALTH_PORT=8081
HEALTH_ENABLED=true
HEALTH_SOCKET_TIMEOUT=2
SELFTEST_URL=https://www.google.de
SELFTEST_TIMEOUT=5

AUTH_REQUIRED=true
ACCESS_TOKEN=replace-with-strong-token
MCP_ALLOWED_HOSTS=playwright.example.com
MCP_ALLOWED_ORIGINS=https://playwright.example.com
MCP_SERVER_NAME=pwmcp-server

PLAYWRIGHT_HEADLESS=true
PLAYWRIGHT_BROWSER=chromium
WS_MAX_SESSIONS=10
WS_SESSION_TIMEOUT=3600

SSL_ENABLED=true
SSL_CERT_PATH=/certs/server.crt
SSL_KEY_PATH=/certs/server.key

WORKSPACES_DIR=./vol-workspaces
CERTS_DIR=./certs
```

Run:

```bash
docker compose -f docker-compose.manual.yml up -d
```
