# Deployment

## Prerequisites

- [ciu](https://github.com/volkb79-2/vbpub/tree/main/ciu) installed
- Docker with Buildx plugin

## Internal Mode (Dev / CI — Default)

The unified container (`pwmcp`) runs on the project Docker network with plain HTTP and no auth. Sibling containers reach all services by **container name** — never via `localhost`.

### Network joining

PWMCP **joins the project network it is placed in**, controlled by `deploy.network_name` (a ciu variable). When pwmcp is deployed as a sub-stack of a parent project, that project passes its own network name via `deploy.network_name`, so the pwmcp container lands on the parent project's shared network alongside all other services.

The container name becomes `<project>-<env>-pwmcp` and is reachable from sibling containers using either the **short service alias** or the **full container name**:

```
ws://pwmcp:3000/                          # short alias (compose service name)
ws://<project>-<env>-pwmcp:3000/          # full container name
http://pwmcp:8931/mcp                     # @playwright/mcp short alias
http://<project>-<env>-pwmcp:8931/mcp     # @playwright/mcp full container name
http://pwmcp:8932/mcp                     # chrome-devtools-mcp short alias
http://<project>-<env>-pwmcp:8932/mcp     # chrome-devtools-mcp full container name
http://pwmcp:8933/mcp                     # lighthouse-mcp short alias
http://<project>-<env>-pwmcp:8933/mcp     # lighthouse-mcp full container name
```

When pwmcp is run standalone (not as a sub-stack), `deploy.project_name` defaults to `pwmcp` and the standard `pwmcp-dev` network is used.

```bash
cd pwmcp

# First time: generate .env.ciu from ciu.defaults.toml.j2
ciu --generate-env -d .

# Start the stack
ciu -d .
```

The unified container comes up as `<project>-<env>-pwmcp` on the project network, serving all ports.

Run-server policy is config-driven:

```toml
[pwmcp.run_server]
default_lease_s = 1800
max_lease_s = 7200
max_clients = 2
idle_recycle_s = 30
```

Raise `max_lease_s` only for an intentional long-running suite. A client may
request any shorter value. The idle recycle restarts the run-server process
group after its final client disconnects and is the automatic orphan-Chromium
remedy.

To expose ports to the host for local debugging, set `pwmcp.unified.expose = true` in `ciu.toml.j2` (overrides file) and re-run `ciu -d .`. Exposed ports bind to `127.0.0.1` only (3000, 8931, 8932, 8933).

### `PWMCP_MCP_ALLOWED_HOSTS` and the HTTP 403 on container-name access

The stream-only 8931 gateway has DNS-rebinding protection: it rejects any
request whose `Host` header does not match an allowed host before forwarding to
the loopback `@playwright/mcp` backend. The backend is never directly exposed;
the gateway is the only listener on the container's 8931 interface.

The ciu template fixes this by injecting `PWMCP_MCP_ALLOWED_HOSTS` into the container environment with the two ciu-derived host:port values:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

The gateway reads `PWMCP_MCP_ALLOWED_HOSTS` and applies the allowlist before
forwarding to the backend.

This is the secure approach: the allowlist is pinned to the known internal names rather than using `*` (which disables the check entirely). The network boundary already restricts who can reach the port; `PWMCP_MCP_ALLOWED_HOSTS` pins which `Host` header value the server honours.

If you need to add further aliases (e.g. a custom DNS name or `*` as a last resort), set `extra_args` in `ciu.toml.j2`:

```toml
[pwmcp.unified]
extra_args = "my-custom-alias:8931"
```

## External Mode (TLS via tls-edge)

External mode fronts the unified service with a running [tls-edge](https://github.com/volkb79-2/vbpub/tree/main/tls-edge) (Traefik) on the `ingress_public` network.

### Requirements

- tls-edge deployed and the `ingress_public` Docker network exists
- DNS records for `unified_host` pointing to the Traefik host
- A basicAuth htpasswd hash for the access guard:
  ```bash
  htpasswd -nbB pwmcp <secret>
  ```

### Configuration

In your `ciu.toml.j2` (override file in this directory):

```toml
[pwmcp.external]
enabled = true
unified_host = "pw.example.com"      # single host for both endpoints
guard_enabled = true
guard_user = "pwmcp"
guard_htpasswd = "$2y$05$..."         # htpasswd -nbB output
```

Then redeploy:

```bash
ciu --generate-env -d .
ciu -d .
```

External endpoints (all served from one hostname):
- Playwright connect: `wss://pw.example.com/` (WebSocket route to port 3000)
- @playwright/mcp: `https://pw.example.com/mcp` (HTTP route to port 8931)
- chrome-devtools-mcp: `https://pw.example.com/devtools/mcp` (HTTP route to port 8932)
- lighthouse-mcp: `https://pw.example.com/lighthouse/mcp` (HTTP route to port 8933)

### Access Guard

The basicAuth guard is applied per-route at Traefik level. The consumer includes credentials in the request:
- For Playwright `connect()`: pass auth via the URL or `extra_http_headers` option in the `connect()` call
- For MCP HTTP clients: standard HTTP Basic Auth header

## Image Build & Push

The unified `pwmcp` image must be built before deploying.

```bash
# Build both the PyPI-compatible and npm-latest tracks locally
python3 build-push.py --build

# Push to GHCR
GITHUB_USERNAME=<user> GITHUB_PUSH_PAT=<token> python3 build-push.py --push
```

`build-push.py` reads `[env]` from `cmru.toml`, verifies that the named builder
uses the configured `remote` driver and exact managed Unix socket, and passes
that builder explicitly to Bake. The host setup owns the BuildKit service,
cache, and resource limits; a project release must not create a private
`docker-container` worker or invent a second set of limits.

Verify the managed service before a direct build with:

```bash
docker buildx inspect mdt-managed --bootstrap
docker inspect mdt-buildkitd --format '{{.State.Status}} {{.HostConfig.CgroupParent}}'
systemctl status mdt-buildkitd.service
systemd-cgtop dev-buildkitd.slice
```

The Docker CLI and `dockerd` remain separate coordination processes. The
expensive executor work is charged to the host-managed `mdt-buildkitd` service,
so a private `buildx_buildkit_*` container is not expected to exist. If the
socket or builder verification fails, repair host setup before attempting the
release; the wrapper refuses to fall back to an ungoverned builder.

Or invoke the same wrapper through the release runner:

```bash
cmru build pwmcp
cmru publish pwmcp
```

The bake file reads `PLAYWRIGHT_VERSION`, `PLAYWRIGHT_DISTRO`,
`PLAYWRIGHT_IMAGE_DIGEST`, and `PWMCP_VERSION` from the prepared environment;
defaults match `ciu.defaults.toml.j2`, the Dockerfile, and `cmru.vars`.

## Upgrading the Playwright Version

The resolver validates the committed release projection by default without
contacting upstreams:

```bash
cd pwmcp
python3 scripts/resolve-playwright-version.py --check
```

To deliberately refresh the projection, use the explicit upstream operation.
It applies the configured temporary age window, updates
`ciu.defaults.toml.j2` (`unified.image.tag`), `ciu.toml.j2`, `docker-bake.hcl`,
and the Dockerfile's Playwright manifest digest. Then complete the release:

```bash
# Explicit upstream refresh (normally owned by CMRU FEAT-03):
python3 scripts/resolve-playwright-version.py --refresh

# Complete isolated release from the repository root:
cmru release pwmcp
```

For build-only inspection use `cmru build pwmcp`; for an already-reviewed
candidate's low-level publication step use `cmru publish pwmcp`. A release
commits and pushes the selected inputs before publishing. Publication must
stop if that source push cannot fast-forward; otherwise GitHub could create
the immutable release tag from an older remote tree.

Development consumers should follow `pwmcp-latest/latest.json`, verify its
bundle checksum, and rebuild their test-only layer from the bundled `client/`
and `pwmcp.contract.json`. They do not edit a Playwright pin manually.
Production deployments may deliberately retain a versioned release/digest
when reproducibility matters more than tracking latest.

## Bundle Verification

Every published bundle has a `.sha256` sidecar in the same release:

```bash
VERSION="1.62.0-r4"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz.sha256" \
  -o "pwmcp-${VERSION}.tar.xz.sha256"
sha256sum -c "pwmcp-${VERSION}.tar.xz.sha256"
```

The SHA256 digest is also embedded in the release notes for a quick manual check.

"Latest" is resolved by scanning `pwmcp-v*` releases for the highest semver. The thin `pwmcp-latest` release contains only `latest.json` — a JSON pointer to the current versioned release — with no bundle duplication.


## P03: Shared Browser Mode Vars

New `[pwmcp.unified]` ciu vars (all opt-in; defaults preserve pre-P03
behavior byte-for-byte):

| Var | Default | Notes |
|---|---|---|
| `browser_mode` | `"per-session"` | `"shared"` opts into one persistent Chromium; any other value is a fatal entrypoint error |
| `admin_port` | `8939` | Shared-mode admin API; internal network only, never published, never routed through Traefik |
| `browser_max_idle_s` | `0` | Shared-mode idle recycle; `0` disables it; must be a non-negative integer or the entrypoint fails fatally |

See `docs/USAGE.md` for the consumer-facing summary and `docs/ARCHITECTURE.md`
for the mode-plumbing/mechanism details.
