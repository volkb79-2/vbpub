# Deployment

## Build (Local)

This project uses Buildx Bake.

```bash
cd /workspaces/vbpub/pwmcp
./build-images.py
```

## Push (GHCR)

```bash
cd /workspaces/vbpub/pwmcp
cp .env.sample .env
# Fill GITHUB_USERNAME and GITHUB_PUSH_PAT in vbpub/.env or local .env
./push-images.py
```

## Stack Bundle (CIU + Manual)

The release script creates a portable stack bundle that includes:
- `ciu.defaults.toml.j2`
- `ciu-global.defaults.toml.j2`
- `docker-compose.yml.j2`
- `docker-compose.manual.yml`
- `.env.sample`
- `docs/`
- `reverse-proxy/`
- `client/` (wheel files for offline installs)

Bundle output:
- `dist/pwmcp-stack-bundle-<version>.tar.gz`

Distribution:
- Publish the versioned bundle asset to:
	- `pwmcp-stack-bundle-<version>`
	- `pwmcp-stack-bundle-latest`

## CIU (Recommended)

This repository is a standalone CIU project: it ships its own
`ciu-global.defaults.toml.j2`, stack `ciu.defaults.toml.j2`, and
`docker-compose.yml.j2`, so a user can run CIU directly without any
external orchestration or shared repo configuration.

```bash
cd /workspaces/vbpub/pwmcp
ciu --generate-env -d .
ciu -d .
```

Full deployment flow example (external checks):

```bash
ciu-deploy --update-cert-permission --generate-env --stop --build --deploy --healthcheck external --selftest external
```

Why this order:
- `--update-cert-permission` ensures TLS certs are readable before compose (skip if certs are not provisioned yet).
- `--generate-env` ensures `.env.ciu` exists and is current.
- `--stop` + `--build` ensures a clean, fresh image deployment.
- `--deploy` starts the stack.
- `--healthcheck external` validates reverse-proxy routes.
- `--selftest external` validates egress and external connectivity.

## Docker Compose (Manual)

### Direct Ports (WS + MCP)

```bash
cp .env.sample .env
# Edit ACCESS_TOKEN and PUBLIC_FQDN

docker compose -f docker-compose.manual.yml up -d
```

Endpoints:
- WS: `ws://<host>:${WS_EXTERNAL_PORT}`
- MCP: `http://<host>:${MCP_EXTERNAL_PORT}/mcp`
- Health: `http://<host>:${HEALTH_EXTERNAL_PORT}/health`

### Reverse Proxy (TLS)

```bash
cp .env.sample .env
# Set PUBLIC_FQDN and LETSENCRYPT_DIR

docker compose -f docker-compose.manual.yml --profile proxy up -d
```

Endpoints:
- WS: `wss://PUBLIC_FQDN/ws`
- MCP: `https://PUBLIC_FQDN/mcp`

## Ports
- WebSocket server: `WS_PORT` (default 3000)
- MCP server: `MCP_PORT` (default 8765)
- Health server: `HEALTH_PORT` (default 8081)
- Proxy TLS: `REVERSE_PROXY_HTTPS_PORT` (default 443)

## Volumes
- `WORKSPACES_DIR` -> `/workspaces`
- `CERTS_DIR` -> `/certs`

Artifacts are stored under `/workspaces/artifacts` (controlled by `WS_ARTIFACT_ROOT`).

## Runtime User (CIU)

To align with VS Code devcontainers while staying compatible with hosted CI/CD
environments, the PWMCP stack exposes the runtime user via CIU config:

- `pwmcp_server.runtime.container_user`
- `pwmcp_server.runtime.container_uid`
- `pwmcp_server.runtime.container_gid`

Default values are set to the `vscode` user (UID/GID 1000). Override these
values in your active CIU config when running in GitHub-hosted or other
non-`vscode` environments.

## Health Checks
- `/health` and `/ready` perform WS/MCP socket checks.
- `/selftest` performs an external GET (default https://www.google.de).

## TLS Requirements (CIU)

CIU supports `ciu.require_certs` (default: false) to fail fast when certificates
are required. When enabled, CIU validates that the Let’s Encrypt files exist
and are readable for `DOCKER_GID` before starting the reverse proxy.

`ciu.require_fqdn` (default: false) controls whether `.env.ciu` generation must resolve
`PUBLIC_FQDN`. Disable it when reverse DNS is unreliable.

For this project, both are set to true in [ciu-global.defaults.toml.j2](vbpub/pwmcp/ciu-global.defaults.toml.j2).

This repo also sets `ciu.standalone_root = true`, which requires running CIU
from the project root (regenerate `.env.ciu` in that directory if you move the repo).

## Publish & Push Options

### GHCR (Recommended for GitHub orgs)
Pros: integrated auth, supports private/public images, good CI/CD fit.
Cons: GitHub token management required, rate limits for anonymous pulls.

### Docker Hub
Pros: widest ecosystem compatibility, familiar UX.
Cons: stricter rate limits, public naming collisions, org paid plans for private.

### Private Registry (self-hosted)
Pros: full control, on‑prem compliance, no external rate limits.
Cons: ops overhead, TLS/credentials management.

### Air‑gapped / offline
Pros: no registry dependency; ships as tarball.
Cons: manual distribution and load (`docker save`/`docker load`).
