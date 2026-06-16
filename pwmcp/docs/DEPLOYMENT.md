# Deployment

## Prerequisites

- [ciu](https://github.com/volkb79-2/vbpub/tree/main/ciu) installed
- Docker with Buildx plugin

## Internal Mode (Dev / CI — Default)

Both services run on the project Docker network with plain HTTP and no auth.  
Sibling containers reach services by **container name** — never via `localhost`.

### Network joining

pwmcp **joins the project network it is placed in**, controlled by `deploy.network_name` (a ciu variable). When pwmcp is deployed as a sub-stack of a parent project, that project passes its own network name via `deploy.network_name`, so all pwmcp containers land on the parent project's shared network alongside the rest of the project's services.

Container names in that case become `<project>-<env>-pwmcp-playwright` and `<project>-<env>-pwmcp-mcp`, and they are reachable from sibling containers using either the **short service alias** or the **full container name**:

```
ws://pwmcp-playwright:3000/           # short alias (service name in compose)
ws://<project>-<env>-pwmcp-playwright:3000/  # full container name
http://pwmcp-mcp:8931/mcp             # short alias
http://<project>-<env>-pwmcp-mcp:8931/mcp   # full container name
```

When pwmcp is run standalone (not as a sub-stack), `deploy.project_name` defaults to `pwmcp` and the standard `pwmcp-dev` network is used.

```bash
cd pwmcp

# First time: generate .env.ciu from ciu.defaults.toml.j2
ciu --generate-env -d .

# Start the stack
ciu -d .
```

Services come up as:
- `<project>-<env>-pwmcp-playwright` on port 3000 (project network only)
- `<project>-<env>-pwmcp-mcp` on port 8931 (project network only)

To expose a port to the host for local debugging, set `pwmcp.playwright_server.expose = true` (or `pwmcp.playwright_mcp.expose = true`) in `ciu.toml.j2` (overrides file) and re-run `ciu -d .`. Exposed ports bind to `127.0.0.1` only.

### `--allowed-hosts` and the HTTP 403 on container-name access

The `@playwright/mcp` server (`pwmcp-mcp`) has built-in DNS-rebinding protection: it rejects any request whose `Host` header does not match an allowed host. When bound to `0.0.0.0`, the server's default allowed host is `"0.0.0.0"` — which does **not** match `pwmcp-mcp:8931`, the `Host` header a sibling container sends when it reaches the service by name. Without an explicit allowlist this produces **HTTP 403** for every internal caller, silently breaking internal mode.

The ciu template fixes this by passing `--allowed-hosts` with the two ciu-derived host:port values for the container:

```
--allowed-hosts pwmcp-mcp:8931,<project>-<env>-pwmcp-mcp:8931
```

This is the secure approach: the allowlist is pinned to the known internal names rather than using `*` (which disables the check entirely). The network boundary already restricts who can reach the port; `--allowed-hosts` pins which `Host` header value the server honours.

If you need to add further aliases (e.g. a custom DNS name or `*` as a last resort), append them via `extra_args` in your `ciu.toml.j2` override:

```toml
[pwmcp.playwright_mcp]
extra_args = "--allowed-hosts my-custom-alias:8931"
```

## External Mode (TLS via tls-edge)

External mode fronts both services with a running [tls-edge](https://github.com/volkb79-2/vbpub/tree/main/tls-edge) (Traefik) on the `ingress_public` network.

### Requirements

- tls-edge deployed and the `ingress_public` Docker network exists
- DNS records for `server_host` and `mcp_host` pointing to the Traefik host
- A basicAuth htpasswd hash for the access guard (generate once):
  ```bash
  htpasswd -nbB pwmcp <secret>
  ```

### Configuration

In your `ciu.toml.j2` (override file in this directory):

```toml
[pwmcp.external]
enabled = true
server_host = "pw.example.com"       # playwright-server (wss connect)
mcp_host = "pw-mcp.example.com"      # playwright-mcp (MCP surface)
guard_enabled = true
guard_user = "pwmcp"
guard_htpasswd = "$2y$05$..."         # htpasswd -nbB output
```

Then redeploy:

```bash
ciu --generate-env -d .
ciu -d .
```

External endpoints:
- Playwright connect: `wss://pw.example.com/` (consumers use this as the WebSocket URL)
- MCP: `https://pw-mcp.example.com/mcp`

### Access Guard

The basicAuth guard is applied per-route at Traefik level. The consumer includes credentials in the request:
- For Playwright `connect()`: pass auth via the URL or the `extra_http_headers` option in the Playwright `connect()` call
- For MCP HTTP clients: standard HTTP Basic Auth header

## Image Build & Push

The `pwmcp-playwright` image must be built and pushed before deploying. The `pwmcp-mcp` service pulls from `mcr.microsoft.com` directly.

```bash
# Build locally
docker buildx bake all --load

# Push to GHCR
GITHUB_USERNAME=<user> GITHUB_PUSH_PAT=<token> docker buildx bake all --push
```

Or via the ciu build runner (uses `build-push.toml`):

```bash
ciu-build -d . build-images
ciu-build -d . push-images
```

The bake file reads `PLAYWRIGHT_VERSION` and `PLAYWRIGHT_DISTRO` from environment; defaults match `ciu.defaults.toml.j2`.

## Upgrading the Playwright Version

Run the resolve script to auto-detect the latest npm version, update config files,
and compute the next release number:

```bash
cd pwmcp
python3 scripts/resolve-playwright-version.py
```

The script updates `ciu.defaults.toml.j2`, `ciu.toml.j2`, and `docker-bake.hcl` with the
new `playwright_version` and `image.tag` (e.g., `1.61.0-r1`). Then complete the release:

```bash
# Build and push the new image + bundle via the release orchestrator:
python3 ../release-runner.py --project pwmcp --step build
python3 ../release-runner.py --project pwmcp --step push

# After publish-bundle.py runs, create the git tag it prints:
git tag -a pwmcp-v1.61.0-r1 -m "pwmcp 1.61.0-r1"
git push origin pwmcp-v1.61.0-r1
```

Notify consumers: they must update `pip install playwright==<new-version>` to match the
new `playwright_version` in their extracted bundle's `ciu.defaults.toml.j2`.
Then redeploy: `ciu --generate-env -d . && ciu -d .`
