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

`@playwright/mcp` has built-in DNS-rebinding protection: it rejects any request whose `Host` header does not match an allowed host. When bound to `0.0.0.0`, the server's default allowed host is `"0.0.0.0"` — which does **not** match `pwmcp:8931`, the `Host` header a sibling container sends when it reaches the service by name. Without an explicit allowlist this produces **HTTP 403** for every internal caller, silently breaking internal mode.

The ciu template fixes this by injecting `PWMCP_MCP_ALLOWED_HOSTS` into the container environment with the two ciu-derived host:port values:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

`supervisord.conf` passes `--allowed-hosts %(ENV_PWMCP_MCP_ALLOWED_HOSTS)s` to `playwright-mcp`.

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

`build-push.py` reads `[builder]` from `build-push.toml`, creates or repairs the
named `docker-container` BuildKit builder, verifies the applied Docker limits,
and passes that builder explicitly to Bake. The defaults permit 4 GiB RAM,
12 GiB combined RAM+swap (therefore up to 8 GiB swap), and four CPUs. Edit the
TOML when the build needs different limits; do not bypass the wrapper for a
release.

The Docker CLI and `dockerd` remain in `system.slice`, while the expensive
executor work is charged to the `buildx_buildkit_<builder>0` container's cgroup.
Accordingly, some `dockerd` CPU in `top` is normal coordination, registry, and
snapshotter work. Attribute the actual build with:

```bash
docker stats buildx_buildkit_pwmcp-governed-v10
docker inspect buildx_buildkit_pwmcp-governed-v10 \
  --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.CpuQuota}}/{{.HostConfig.CpuPeriod}}'
systemd-cgtop system.slice
```

Or invoke the same wrapper through the release runner:

```bash
./cmru.build.sh --project pwmcp
./cmru.publish.sh --project pwmcp
```

The bake file reads `PLAYWRIGHT_VERSION`, `PLAYWRIGHT_DISTRO`, and `PWMCP_VERSION` from environment; defaults match `ciu.defaults.toml.j2` and `cmru.vars`.

## Upgrading the Playwright Version

Run the resolve script to auto-detect the latest npm version, update config files, and compute the next release number:

```bash
cd pwmcp
python3 scripts/resolve-playwright-version.py
```

The script updates `ciu.defaults.toml.j2` (`unified.image.tag`), `ciu.toml.j2`, and `docker-bake.hcl`. Then complete the release:

```bash
# Build and push the new image + bundle via cmru (run from the repo root):
./cmru.build.sh   --project pwmcp
./cmru.publish.sh --project pwmcp

# Or perform the complete delegated release in one operation:
./cmru.release.sh --project pwmcp
```

The one-shot release commits and pushes resolver-updated PWMCP inputs before
publishing. Publication must stop if that source push cannot fast-forward;
otherwise GitHub would create the immutable release tag from an older remote
tree.

Development consumers should follow `pwmcp-latest/latest.json`, verify its
bundle checksum, and rebuild their test-only layer from the bundled `client/`
and `pwmcp.contract.json`. They do not edit a Playwright pin manually.
Production deployments may deliberately retain a versioned release/digest
when reproducibility matters more than tracking latest.

## Bundle Verification

Every published bundle has a `.sha256` sidecar in the same release:

```bash
VERSION="1.61.0-r2"
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
