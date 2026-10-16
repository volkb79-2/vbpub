# Usage

## Version coupling — read this first

The Playwright wire protocol is **version-strict**: the client library version you `pip install`
must exactly match the Playwright version baked into the running `pwmcp` image.
A version mismatch causes protocol errors at connect time.

**Preferred GitHub-only client installation:** verify the release bundle using
`pwmcp-latest/latest.json`, extract `bundle/client` and
`bundle/pwmcp.contract.json`, then install the bundled source and exact
contract version:

The client depends on the **`cli-extended`** wheel (`cli-extended>=0.3.0`, the shared CLI
layer under the `pwmcp` command). It is published to GitHub Releases only, never PyPI: the
bare name is unclaimed there, so install it first from its release (URL and sha256 from
`cli-extended-latest/latest.json`, verified like the bundle), then the client. pip finds the
requirement already satisfied and never queries an index for it:

```bash
# 1. the released cli-extended wheel, sha256-verified, installed without any index
python -m pip install --no-index --no-deps ./cli_extended-<version>-py3-none-any.whl
# 2. the bundled client and the exact Playwright version from the contract
PW_VER=$(python -c 'import json; print(json.load(open("pwmcp.contract.json"))["playwright"]["python"])')
pip install "playwright==$PW_VER" ./client
```

A wheelhouse works too: `pip install --no-index --find-links ./wheelhouse ./client` (the
wheelhouse then holds the cli-extended wheel and Playwright's own dependencies). Without the
library the client install fails with `cli-extended` named as the missing requirement.

Do **not** run `playwright install` — browser binaries belong in the pwmcp container,
not in your project.

## Install a standalone CIU instance

For an independent instance at `/srv/pwmcp-instance`, create the install root
owned by the user who will run CIU, then use the generated CMRU installer. It verifies
the versioned release bundle and its SHA-256 sidecar, then installs the bundled
client and exact Playwright wheel offline:

```bash
sudo install -d -m 0755 -o "$USER" -g "$(id -gn)" /srv/pwmcp-instance
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/pwmcp/get.py \
  | XDG_DATA_HOME=/srv python3 - install --scope user
export PATH="/srv/pwmcp-instance/bin:$PATH"
cd /srv/pwmcp-instance
ciu up
```

For an image that owns `/srv`, the same user-scope command works without the
ownership setup line. Keep `/srv/pwmcp-instance/bin` on `PATH` in later shells
to use the `pwmcp` launcher. To update, replace `install` with `update`, add
the launcher directory to `PATH`, and run `ciu up` again.

The standalone root is independent of a consuming project's CIU root. The
internal network remains unauthenticated, matching PWMCP's existing internal
policy. CIU attaches the current devcontainer to that network; agents there can
use `http://pwmcp:8931/mcp`, while Python clients and the `pwmcp inspect`
command use `ws://pwmcp:3000/`.

## Inspect a webpage with the client CLI

The release installs a `pwmcp` command alongside the Python client library. It
opens pages in PWMCP's remote browser, so the devcontainer needs no local
browser binaries:

```bash
pwmcp inspect https://example.com
pwmcp inspect https://example.com --format snapshot --output page.yml
pwmcp inspect https://example.com --format screenshot --output page.png --full-page
pwmcp inspect https://example.com --wait-for '#content' --wait-until domcontentloaded
pwmcp inspect https://nas.example --ignore-https-errors
```

`--format` accepts `text`, `snapshot`, or `screenshot`. `--wait-until` accepts
`commit`, `domcontentloaded`, `load`, or `networkidle`. Text and snapshots go to
stdout unless `--output PATH` is given; screenshots require `--output` and a
`.png`, `.jpg`, or `.jpeg` suffix. `--wait-for SELECTOR` waits for a visible
CSS selector, and `--timeout-ms` controls navigation and selector waits.
`--ignore-https-errors` opts into accepting invalid HTTPS certificates for all
HTTPS requests in this one-shot browser context, including redirects and page
resources. Use it only for a trusted target with a known certificate problem;
it does not change PWMCP service defaults. See the
[design rationale](DESIGN-GUIDE.md#certificate-errors-during-inspection).

## Unified Image: Three Services, One Hostname

The unified image exposes three endpoints from the single service alias `pwmcp`:

| Endpoint | URL | Purpose |
|---|---|---|
| Playwright run-server | `ws://pwmcp:3000/` | Full Playwright API via `connect()` |
| @playwright/mcp | `http://pwmcp:8931/mcp` | MCP-compatible AI clients |
| chrome-devtools-mcp | `http://pwmcp:8932/mcp` | CDP performance tracing, DevTools insights |
| lighthouse-mcp | `http://pwmcp:8933/mcp` | Lighthouse audit scores + opportunities |

## Playwright `connect()` — Test Suites

Use `pwmcp_client.BrowserLease` for Python gates. It validates the live
contract, supplies lease headers, and owns both browser and driver cleanup:

```python
from pwmcp_client import BrowserLease

with BrowserLease.connect(lease_seconds=1800, label="consumer-gate") as lease:
    page = lease.browser.new_page()
    page.goto("https://example.com")
```

Configuration: `PWMCP_CONTRACT_URL`, `DSTDNS_PWMCP_WS`,
`PWMCP_LEASE_SECONDS`, and `PWMCP_SESSION_LABEL`. Requested leases are clamped
to `[pwmcp.run_server].max_lease_s`; operators can deliberately raise that
ceiling for a long suite. Do not use `chromium.launch()`, run `playwright
install`, or leave `sync_playwright().start()` unmanaged.

The internal run-server admin surface provides `GET /sessions`,
`POST /sessions/close`, and `POST /run-server/restart` on port 8940 by default.
It is never host-published or routed through tls-edge. After the final client
disconnects, the run-server process group is recycled after
`idle_recycle_s`, removing leaked Chromium descendants.

The `pwmcp` service exposes the native Playwright remote server protocol on port 3000. Test suites connect to it with `chromium.connect()` (or `firefox.connect()`, `webkit.connect()`) and get the **full Playwright API**.

### Install Requirement

The `playwright` Python (or JS) package version **must match** the pinned `pwmcp.playwright_version` in `ciu.defaults.toml.j2` (currently `1.62.0`). Mismatched versions cause protocol errors.

```bash
pip install playwright==1.62.0
```

### Python Example

```python
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        # container-name addressing on the shared Docker network
        browser = await p.chromium.connect("ws://pwmcp:3000/")
        page = await browser.new_page()
        await page.goto("https://example.com")
        screenshot = await page.screenshot()
        await browser.close()

asyncio.run(main())
```

### Environment Variable Pattern

Pass the WebSocket URL via environment variable so it works in both local dev and CI without code changes:

```bash
# In docker-compose or CI env:
PLAYWRIGHT_SERVER_WS=ws://pwmcp:3000/
```

```python
import os
from playwright.async_api import async_playwright

async def main():
    ws_url = os.environ["PLAYWRIGHT_SERVER_WS"]
    async with async_playwright() as p:
        browser = await p.chromium.connect(ws_url)
        # ... tests ...
        await browser.close()
```

### External Mode

In external mode the URL becomes a `wss://` URL with basicAuth credentials:

```python
browser = await p.chromium.connect(
    "wss://pw.example.com/",
    headers={"Authorization": "Basic <base64(user:secret)>"},
)
```

## MCP — AI Clients

The `pwmcp` service provides an MCP-compatible streamable-HTTP interface for AI clients such as VS Code Copilot at port 8931.

### Endpoint

- Internal: `http://pwmcp:8931/mcp`
- External (tls-edge): `https://<unified_host>/mcp`

<!-- DEPRECATED and disabled: the legacy HTTP+SSE `/sse` endpoint is not part of
the PWMCP contract. Use the streamable-HTTP `/mcp` endpoint. -->

### `PWMCP_MCP_ALLOWED_HOSTS` and DNS-rebinding protection

The 8931 stream-only gateway implements DNS-rebinding protection: every
incoming request is checked against an allowlist of permitted `Host` header
values before it reaches the loopback `@playwright/mcp` backend. The backend's
default allowlist contains only its loopback bind address, which would not
match the `Host: pwmcp:8931` header sent by a sibling container accessing the
service by name.

The ciu template resolves this by injecting `PWMCP_MCP_ALLOWED_HOSTS` with both ciu-derived names for the container:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

The gateway reads `PWMCP_MCP_ALLOWED_HOSTS` and pins the allowlist to known
internal names rather than using `*` (which disables the check). The Docker
network boundary remains the second access-control layer.

To add extra allowed hosts (e.g. a custom DNS alias), set `extra_args` in `ciu.toml.j2`:

```toml
[pwmcp.unified]
extra_args = "my-alias:8931"
```

To disable the check entirely (not recommended; use only if you control the network):

```toml
[pwmcp.unified]
extra_args = "*"
```

### VS Code MCP Configuration (internal)

```json
{
  "mcp": {
    "servers": {
      "playwright": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      },
      "chrome-devtools": {
        "type": "http",
        "url": "http://pwmcp:8932/mcp"
      },
      "lighthouse": {
        "type": "http",
        "url": "http://pwmcp:8933/mcp"
      }
    }
  }
}
```

This works when VS Code is running inside a devcontainer on the same Docker network as `pwmcp`.

### VS Code MCP Configuration (external with guard)

```json
{
  "mcp": {
    "servers": {
      "playwright": {
        "type": "http",
        "url": "https://pw.example.com/mcp",
        "headers": {
          "Authorization": "Basic <base64(pwmcp:secret)>"
        }
      },
      "chrome-devtools": {
        "type": "http",
        "url": "https://pw.example.com/devtools/mcp",
        "headers": {
          "Authorization": "Basic <base64(pwmcp:secret)>"
        }
      },
      "lighthouse": {
        "type": "http",
        "url": "https://pw.example.com/lighthouse/mcp",
        "headers": {
          "Authorization": "Basic <base64(pwmcp:secret)>"
        }
      }
    }
  }
}
```

### MCP Browser Options

The default browser is `chromium` (the only browser available in the unified image via `--executable-path`). The `browser` setting in `pwmcp.unified` controls the browser argument passed to `playwright-mcp`. To change browser type or add capabilities, set in `ciu.toml.j2`:

```toml
[pwmcp.unified]
browser = "chromium"
extra_args = "my-alias:8931"   # comma-separated extra allowed-hosts
```

For `--caps` or other playwright-mcp flags, they cannot be passed directly via the current `extra_args` field (which only controls allowed-hosts extensions). For advanced configuration, override the supervisord.conf by mounting a custom one.

## Multiple Consumers

All services support multiple simultaneous consumers:
- `run-server` (port 3000): bounded by configurable `max_clients`; each accepted connection has an absolute lease
- `@playwright/mcp` (port 8931): the MCP server handles concurrent MCP clients
- `chrome-devtools-mcp` (port 8932): served via mcp-proxy, which handles concurrent MCP clients; all sessions share a single chrome-devtools-mcp process (stdio child)
- `lighthouse-mcp` (port 8933): served via mcp-proxy; each audit launches a fresh browser instance (per-audit launch, not shared)

No per-consumer authentication exists in internal mode — the network boundary is the control.

## Consumer Integration (external projects)

External projects consume pwmcp by downloading the versioned bundle from GitHub Releases.
No Docker build is needed — the image is on GHCR.

### Resolving the latest version

Scan `pwmcp-v*` releases for the highest semver, or check `pwmcp-latest/latest.json` for the thin redirect:

```bash
# latest.json contains: version, tag, asset name, sha256, download URL
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-latest/latest.json"
```

### Initial setup

```bash
# Pin a specific release:
VERSION="1.63.0-r3"
mkdir -p services/pwmcp
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz" \
  -o "pwmcp-${VERSION}.tar.xz"

# Verify the bundle (sidecar lives in the same release):
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz.sha256" \
  -o "pwmcp-${VERSION}.tar.xz.sha256"
sha256sum -c "pwmcp-${VERSION}.tar.xz.sha256"

tar -xJf "pwmcp-${VERSION}.tar.xz" --strip-components=1 -C services/pwmcp

# Read the required Playwright version from the bundle (wire protocol pin):
PW_VER=$(grep playwright_version services/pwmcp/ciu.defaults.toml.j2 | grep -oP '"\K[^"]+')
pip install playwright==${PW_VER}   # must match exactly — do not omit the pin

# Start the stack (installs ciu if needed: pip install ciu):
cd services/pwmcp && ciu --generate-env -d . && ciu -d .
```

The unified container comes up as `pwmcp-pwmcp` (default project name `pwmcp`) on the `pwmcp` Docker network, serving port 3000 (WS) and port 8931 (MCP).

### Staying up-to-date

Download a newer bundle, verify it, re-read `playwright_version`, reinstall the pinned client, redeploy:

```bash
VERSION="1.63.0-r3"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz" \
  -o "pwmcp-${VERSION}.tar.xz"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-${VERSION}.tar.xz.sha256" \
  -o "pwmcp-${VERSION}.tar.xz.sha256"
sha256sum -c "pwmcp-${VERSION}.tar.xz.sha256"
tar -xJf "pwmcp-${VERSION}.tar.xz" --strip-components=1 -C services/pwmcp
PW_VER=$(grep playwright_version services/pwmcp/ciu.defaults.toml.j2 | grep -oP '"\K[^"]+')
pip install playwright==${PW_VER}
ciu -d services/pwmcp
```

### Connecting from consumer containers

Add the consumer service to the `pwmcp` Docker network so it can reach the service by container name:

```yaml
services:
  my-test-runner:
    environment:
      PLAYWRIGHT_SERVER_WS: ws://pwmcp:3000/
      MCP_URL: http://pwmcp:8931/mcp
    networks:
      - pwmcp   # join the pwmcp stack's network (never use localhost)

networks:
  pwmcp:
    external: true   # owned by the pwmcp stack; must be running first
```


## P03: Shared Persistent Browser Mode (Opt-In)

By default pwmcp runs in `browser_mode = "per-session"`: `@playwright/mcp`
and `chrome-devtools-mcp` each launch their own Chromium, fully isolated
from each other. This is unchanged and remains the default.

Set `browser_mode = "shared"` in `ciu.toml.j2` when you need the
**cross-tool workflow**: drive a page with Playwright MCP tools, then
profile that SAME page with DevTools MCP tools, in the same browser. Shared
mode also pools browser memory under concurrent use instead of paying for a
Chromium process per session.

```toml
[pwmcp.unified]
browser_mode = "shared"
admin_port = 8939          # internal-network-only admin API (see below)
browser_max_idle_s = 0     # 0 = disabled; >0 recycles the browser after
                            # this many seconds with zero open pages
```

Choose `shared` when: you need the cross-tool proof (navigate via
Playwright, trace via DevTools, same page) or you are running many
concurrent short MCP sessions and want to pool browser memory.

Stay on `per-session` (default) when: you want the strongest per-consumer
isolation, or you don't need the cross-tool workflow — it is the simpler,
longer-proven path.

### Admin endpoint

Shared mode exposes a small admin HTTP API on `admin_port` (default 8939),
internal Docker network only — reach it from a sibling container as
`http://pwmcp:8939/...` (never routed through Traefik, even in external
mode):

```bash
# Clear all browser state (cookies/storage/pages) without restarting the process:
curl -X POST http://pwmcp:8939/browser/reset

# Hard-restart the shared Chromium (e.g. after a suspected leak):
curl -X POST http://pwmcp:8939/browser/restart

# Liveness + open-page count + uptime:
curl http://pwmcp:8939/browser/health
```

The standalone `pwmcp` CLI is built on the shared cli-extended grammar (full reference and
reviewed semantics: [CLI-SPEC.md](CLI-SPEC.md)):

```bash
pwmcp --version                       # one line: pwmcp <version>
pwmcp contract [--json]               # the server contract as JSON
pwmcp doctor [--check NAME] [--json]  # named checks: contract, playwright, skills
pwmcp skills install                  # install the packaged pwmcp-fetch agent skill
```

Help starts with `PWMCP <version> — client utility`. A usage error prints the `[ERROR]`
diagnostic first, then the identity-headed usage, and exits 2; an unreachable or malformed
contract is a one-line `[ERROR]` with exit 1 (never a traceback; add `--traceback` for the
stack of an unexpected failure). `pwmcp doctor` reports one `[OK]`/`[WARN]`/`[FAIL]` line per
check instead of the former single `ok release=... playwright=... ws=...` line; the same
facts are in the `contract` and `playwright` check summaries and in `--json`.
