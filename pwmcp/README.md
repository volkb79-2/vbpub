# PWMCP — Playwright-as-a-Service

PWMCP is a thin, hardened [ciu](https://github.com/volkb79-2/vbpub/tree/main/ciu) packaging of the **official Microsoft Playwright unified image**. It provides browser automation as a service so consuming projects never install a browser into their own devcontainer or CI runner.

## Unified Image

The unified image `ghcr.io/volkb79-2/pwmcp` bundles the Playwright `run-server`, `@playwright/mcp`, `chrome-devtools-mcp`, and `lighthouse-mcp` into a **single container** under a **single hostname** `pwmcp`:

| Endpoint | Port | Purpose |
|---|---|---|
| `ws://pwmcp:3000/` | 3000 | Native Playwright `connect()` — full API, test suites |
| `http://pwmcp:8931/mcp` | 8931 | `@playwright/mcp` (Streamable HTTP) — AI clients (VS Code Copilot, etc.) |
| `http://pwmcp:8932/mcp` | 8932 | `chrome-devtools-mcp` (CDP profiling via mcp-proxy) — performance tracing |
| `http://pwmcp:8933/mcp` | 8933 | `lighthouse-mcp` (Lighthouse audit server via mcp-proxy) — audit scores + opportunities |

All services are managed by `supervisord` (PID 1), which reaps children and forwards SIGTERM on shutdown.

## Deployment Modes

**internal** (default): the container joins the project Docker network with plain HTTP and no authentication. Sibling containers (test runner, devcontainer) reach both endpoints by **container name** on the shared network — never via `localhost`:

```
ws://pwmcp:3000/              — Playwright connect() endpoint
http://pwmcp:8931/mcp         — @playwright/mcp endpoint
http://pwmcp:8932/mcp         — chrome-devtools-mcp (CDP profiling)
http://pwmcp:8933/mcp         — lighthouse-mcp (Lighthouse audits)
```

The network boundary is the access control. This is the correct mode for dev and CI.

**external** (`external.enabled = true`): front with a running [tls-edge](https://github.com/volkb79-2/vbpub/tree/main/tls-edge) (Traefik). Set `pwmcp.external.unified_host` to a domain name; each port gets a separate TLS route. Routes are protected by a per-route basicAuth guard. See `ciu.defaults.toml.j2` for the `[pwmcp.external]` settings.

## Quick Start

```bash
cd pwmcp
ciu --generate-env -d .
ciu -d .
```

Services come up as one container: `<project>-<env>-pwmcp` on the project network.

## Standalone instance for MDT and other devcontainers

PWMCP publishes a CMRU `get.py` installer and a versioned, checksum-verified
bundle. With CIU installed, create the install root owned by the user who will
run CIU, install the current release there, then start it from that root:

The install user needs Docker access and Python 3.11 or newer with `venv` and
`ensurepip` available (`python3-venv` on Debian).

```bash
sudo install -d -m 0755 -o "$USER" -g "$(id -gn)" /srv/pwmcp-instance
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/pwmcp/get.py \
  | XDG_DATA_HOME=/srv python3 - install --scope user
export PATH="/srv/pwmcp-instance/bin:$PATH"
cd /srv/pwmcp-instance
# First run only: record this devcontainer's CIU identity and network.
ciu env generate
ciu up
```

To update the installed release and apply its Compose configuration:

```bash
set -o pipefail
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/pwmcp/get.py \
  | XDG_DATA_HOME=/srv python3 - update --scope user
export PATH="/srv/pwmcp-instance/bin:$PATH"
cd /srv/pwmcp-instance
ciu up
```

The installer keeps the generated CIU identity and the instance's sparse
`ciu.toml.j2` override across updates.

The first `ciu env generate` records the devcontainer's CIU identity and
internal network; later updates keep those generated facts. `ciu up` starts the
root's PWMCP stack on that shared network. The internal deployment keeps
PWMCP's existing policy: no authentication and no host-published ports. Its
MCP endpoint is `http://pwmcp:8931/mcp` and its Playwright endpoint is
`ws://pwmcp:3000/`. See the [consumer commands](docs/CONSUMERS.md) and the
[standalone design](docs/DESIGN-GUIDE.md#standalone-ciu-root-and-installer).
The Compose stack uses the exact version-pinned image from the local cache
when available and pulls it on a cold host.

The bundled `pwmcp` CLI can inspect a remote page without installing browser
binaries in the devcontainer:

```bash
pwmcp inspect https://example.com --format text
pwmcp inspect https://example.com --format snapshot --output page.yml
pwmcp inspect https://example.com --format screenshot --output page.png --full-page
pwmcp inspect https://self-signed.example --ignore-https-errors
pwmcp inspect https://example.com/app \
  --wait-until networkidle --wait-for '#content'
```

For pages that render content after the initial navigation event, use
`--wait-until networkidle` and `--wait-for` a visible, page-specific content
selector. The selector may match several elements; the CLI waits for the first
matching visible element before reading the page.

The `pwmcp-fetch` agent skill is installed with `pwmcp skills install --harness
all`. Consumer environments must make the `pwmcp` command available on the
agent process's `PATH`; installing the skill does not install the CLI or set
PATH. MCP consumers must also configure an endpoint reachable from the agent.
See [skill and agent setup](docs/CONSUMERS.md#install-and-use-the-agent-skill).

Use `--ignore-https-errors` only for a trusted target with a known invalid
certificate. It applies to HTTPS requests in that one inspection context.
See [CLI usage and output choices](docs/CONSUMERS.md#inspect-a-webpage) and the
[certificate-bypass design](docs/DESIGN-GUIDE.md#certificate-errors-during-inspection).
In the MDT devcontainer install, the stable launcher is
`/srv/pwmcp-instance/bin/pwmcp`; it follows updates through the `current`
release link. A `~/.local/bin/pwmcp` symlink exposes it on the default user's
existing PATH, and the absolute launcher path also works when an agent shell
does not inherit PATH.

## Consumer Connection

### Playwright `connect()` (test suites)

```python
from pwmcp_client import BrowserLease

with BrowserLease.connect(label="my-test-suite") as lease:
    page = lease.browser.new_page()
    page.goto("https://example.com")
```

The GitHub release bundle contains `client/` and `pwmcp.contract.json`. A
consumer verifies the URL and SHA-256 from `pwmcp-latest/latest.json`, installs
the bundled client source, and installs the exact `playwright.python` version
declared by the contract. This companion client is intentionally not on PyPI.

Port 3000 is a policy gateway. Its defaults are two concurrent clients, a
30-minute lease, a two-hour maximum, and a 30-second idle recycle. Override
them in `[pwmcp.run_server]`. A client may request a lease, but it cannot exceed
the server-side ceiling. The gateway accepts only CIU-derived service names,
consumer aliases, and the configured external hostname; it rewrites `Host` and
`Origin` to the loopback run-server before forwarding. This does not change the
internal policy: anyone on the internal network can use the service without
authentication. See the [design guide](docs/DESIGN-GUIDE.md#run-server-host-validation)
for why the proxy validates and rewrites these headers.

### MCP

#### `@playwright/mcp` (port 8931)

Add to your MCP client configuration (e.g. VS Code `settings.json`):

```json
{
  "mcp": {
    "servers": {
      "pwmcp": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      }
    }
  }
}
```

In external mode the URL becomes `https://<unified_host>/mcp`.

Port 8931 exposes only Streamable HTTP at `/mcp`; the legacy `/sse` route is
disabled. The stream-only gateway enforces the configured
`PWMCP_MCP_ALLOWED_HOSTS` values before forwarding to the loopback Playwright
MCP backend.

#### `chrome-devtools-mcp` (port 8932)

For performance profiling, CDP tracing, and CPU/network throttling emulation, add a second MCP server entry:

```json
{
  "mcp": {
    "servers": {
      "pwmcp": {
        "type": "http",
        "url": "http://pwmcp:8931/mcp"
      },
      "chrome-devtools": {
        "type": "http",
        "url": "http://pwmcp:8932/mcp"
      }
    }
  }
}
```

In external mode the URL becomes `https://<unified_host>/devtools/mcp` (the `/devtools` prefix is stripped by Traefik before forwarding to the backend).

Note: `chrome-devtools-mcp` is served via `mcp-proxy` (stdio→streamable-HTTP proxy). Unlike `@playwright/mcp`, it does not have native `--allowed-hosts` DNS-rebinding protection. In internal mode the Docker network boundary is the access control; see [SECURITY.md](docs/SECURITY.md) for details.

#### `lighthouse-mcp` (port 8933)

For Lighthouse performance/accessibility/SEO/best-practices audits, add a third MCP server entry:

```json
{
  "mcp": {
    "servers": {
      "pwmcp": {
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

In external mode the URL becomes `https://<unified_host>/lighthouse/mcp` (the `/lighthouse` prefix is stripped by Traefik).

Note: `lighthouse-mcp` is served via `mcp-proxy`, same as `chrome-devtools-mcp`. It has no `--allowed-hosts` enforcement.

## `PWMCP_MCP_ALLOWED_HOSTS` Environment Variable

`@playwright/mcp` has DNS-rebinding protection: it rejects requests whose `Host` header does not match an allowlist. The ciu compose template injects `PWMCP_MCP_ALLOWED_HOSTS` at container start with the two ciu-derived container names:

```
PWMCP_MCP_ALLOWED_HOSTS=pwmcp:8931,<project>-<env>-pwmcp:8931
```

The image default is `localhost:8931,127.0.0.1:8931` for standalone usage. Override via `extra_args` in `ciu.toml.j2` to add further hosts.

## Shared Persistent Browser Mode (P03, opt-in)

By default, `@playwright/mcp` and `chrome-devtools-mcp` each launch their
own Chromium (`browser_mode = "per-session"`, unchanged from before). Set
`browser_mode = "shared"` (in `ciu.toml.j2`) to run ONE persistent headless
Chromium that both attach to over CDP — enabling the cross-tool workflow
(drive a page with Playwright tools, then profile that SAME page with
DevTools tools) and pooling browser memory under concurrent use. See
[docs/USAGE.md](docs/USAGE.md#p03-shared-persistent-browser-mode-opt-in) for
configuration and the admin endpoint (`POST /browser/reset`,
`POST /browser/restart`, `GET /browser/health`), and
[docs/SECURITY.md](docs/SECURITY.md#p03-shared-browser-mode--risk-posture)
for the risk posture.

## Overridable User Agent (PWMCP-09, opt-in, off by default)

Some sites refuse the managed browser because it presents itself as
`HeadlessChrome` (for example `Mozilla/5.0 (X11; Linux x86_64) ... HeadlessChrome/156.0.0.0 ...`);
Anubis-protected pages answer "Access Denied". The operator decided (2026-10-27) to
support a configurable user agent as an explicit, recorded, opt-in feature. Nothing
changes unless it is configured.

**Set it** with the ciu/config key `user_agent` (in `ciu.toml.j2`, next to
`browser_mode`; default `"default"`). ciu exports it to the container as
`PWMCP_USER_AGENT`, and the entrypoint validates it and applies it when the browser is
launched. An invalid value is a fatal entrypoint error. The active value is logged once
at container start.

| Value | User agent presented |
|---|---|
| `default` (or unset) | no override: the browser's own identity, exactly as before |
| `edge-windows` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0` |
| `chrome-windows` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36` |
| `firefox-windows` | `Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0` |
| anything else | a CUSTOM raw string: printable ASCII, 1-512 characters, no CR/LF, no leading or trailing space, and it must contain a space, `/` or `(` as a real UA does. A value that case-insensitively equals a preset name (`Edge-Windows`) or lacks all three (`chrome`, `edge-window`) is rejected as a probable typo, naming the valid presets |

The table lives in exactly one file, `client/src/pwmcp_client/user_agent.py`
(stdlib only). The client imports it and the image carries the same file as
`/opt/pwmcp/pwmcp_user_agent.py`, which the entrypoint runs; there is no second copy.

**What is overridden.** The user-agent string, and the client-hint brand metadata that
matches it (`Sec-CH-UA`, `navigator.userAgentData.brands`, platform), because Chromium's
`--user-agent` alone leaves `HeadlessChrome` in the hints:

- `per-session` mode: `playwright-mcp --user-agent` and
  `chrome-devtools-mcp --chrome-arg=--user-agent=...` (via `containers/pwmcp/pwmcp-ua-args.sh`,
  which adds the flag only when an override is configured). `playwright-mcp` derives the
  hints from the UA.
- `shared` mode: the shared Chromium is launched with `--user-agent=...`, and the
  `ua-override` program (`containers/pwmcp/ua-override/index.js`) attaches over CDP to the
  loopback endpoint and calls `Emulation.setUserAgentOverride` with the matching
  `userAgentMetadata` on every page, existing and new.
- `pwmcp inspect` / the Python client (the run-server path): the service publishes the
  setting in `/contract` (`user_agent.value`, `user_agent.source`); `pwmcp inspect`
  creates its context with that UA and sets the same CDP override with metadata.
- The Firefox preset sends an EMPTY client-hint brand list (Firefox sends none). Whether
  Chromium then omits `Sec-CH-UA` entirely, rather than sending an empty one, is unverified.
- A custom string with no recognisable `Chrome/x.y.z.w` or `Firefox/N` token overrides the
  UA string only; `pwmcp inspect` warns that client-hint metadata was not set.
- `lighthouse-mcp` launches its own per-audit browser and is NOT overridden.

**Scope of the `/contract` setting.** The server-level value published in `/contract`
applies to `pwmcp inspect` only. A direct Playwright `connect()` client keeps the browser
default (it creates its own context and must set its own `user_agent`), even though the
container log reports the override as active.

**Client-hint edge cases.**
- The Firefox preset sends an empty client-hint brand list (Firefox sends none); whether
  Chromium then omits `Sec-CH-UA` entirely is unverified.
- A UA containing `OPR/` (Opera) gets Chrome brands (`Google Chrome`), not Opera's.
- A custom UA whose `Chrome/` token has fewer than four version parts (for example
  `Chrome/156`) gets no client-hint metadata, only the UA string.
- In shared mode, pages created while Chromium is restarting may lack the hints until
  `ua-override` reattaches (it retries every second).
- In shared mode the `ua-override` program exists only when an override is set: the
  entrypoint appends `supervisord.ua-override.part` to a `/tmp` copy of the shared config.
  With no override the process list is exactly what it was before.

**What is not overridden, on purpose.** No `navigator.webdriver` patching, no plugin or
canvas spoofing, no other fingerprint surface. This is an identity string and its matching
hints, not an anti-detection layer.

**Transparency.** With no override (source `default`) `pwmcp inspect` behaves exactly as
before: same stdout, same bare `--json` string, nothing on stderr. Whenever an override is
ACTIVE (`preset:<name>` or `custom`, server-level or `--user-agent`), the result says so,
so spoofed identity is never mistaken for the default: one
`[INFO] user agent: ... (source: ...)` line on stderr that `--quiet` cannot silence, and
with `--json` the output becomes the object `{"content": ..., "user_agent": ...,
"user_agent_source": ...}` (with `--output` it prints
`{"output", "format", "user_agent", "user_agent_source"}`); in text mode stdout stays exactly
the page content. `user_agent` is what the page itself reported (`navigator.userAgent`),
falling back to the configured value. In shared mode the admin API
`GET /browser/health` additionally returns `userAgent` and `userAgentSource`
(`null` / `default` when not overridden).

**Per call.** `pwmcp inspect URL --user-agent <preset|string>` presents that identity for
one inspection only, overriding the server-level setting; `--user-agent default` presents
the browser's own. The inspection runs in a fresh browser context that is closed
afterwards, which is the restore. It is validated before connecting.

**Responsibility.** Use this for pages you are entitled to read. It does not solve
interactive challenges (proof-of-work or CAPTCHA pages), and it does not make
reading a page that its owner has explicitly closed to automation acceptable. Whether a
given Anubis deployment lifts its block under these presets is not verified (see
`KNOWN_ISSUES_TODO_BACKLOG.md` PWMCP-09).

## KSM Opt-In (default on)

Every process in the container opts into Kernel Samepage Merging (KSM) by
default via `prctl(PR_SET_MEMORY_MERGE)`, so the host kernel's `ksmd` can
dedupe identical memory pages (e.g. shared Chromium/Node code pages) across
this container and any other opted-in process on the host — including other
pwmcp instances — without the consumer needing to wire up ciu's KSM
governance overlay.

Mechanism: `containers/pwmcp/ksm-optin.c` (vendored from
`dstdns/tools/ksm-optin/ksm-optin.c`) is a tiny `LD_PRELOAD` shim whose
constructor calls `prctl(PR_SET_MEMORY_MERGE, 1, ...)`. It is compiled from
source in a throwaway Dockerfile build stage (`ksm-optin-builder`) so the
final image never carries a compiler toolchain. `entrypoint.sh` sets
`LD_PRELOAD=/opt/ksm/ksm-optin.so` before `exec supervisord`; since
supervisord is PID 1 and its children inherit the environment, `run-server`,
the gateway, and every MCP program (`mcp`, `devtools-mcp`, `lighthouse-mcp`,
and — in shared browser mode — `chromium`/`admin-server`) all opt in
automatically, with no per-program wiring.

Requires kernel >= 6.4 with KSM enabled on the host. If the host kernel is
older or `CONFIG_KSM` is off, the shim's `prctl` call fails, it logs a
warning to stderr, and the process starts normally — enabling this by
default is safe even on hosts where KSM isn't available.

Toggle via `PWMCP_KSM_OPTIN` (default `1`). Set to `0` to disable:

```yaml
environment:
  PWMCP_KSM_OPTIN: "0"
```

## Package Version Pins

### Playwright Version

`pwmcp.playwright_version` in `ciu.defaults.toml.j2` is the single source of truth. It pins:
- the base image tag and immutable multi-arch manifest digest (`mcr.microsoft.com/playwright:v<version>-<distro>@<digest>`)
- the playwright JS package baked into the image
- the version consumers must `pip install playwright==<version>`

The committed release projection records the Playwright version jointly
available from npm, PyPI, and Microsoft Container Registry for the configured
distribution. Release preparation validates that projection without silently
selecting a newly published dependency; explicit upstream refresh belongs to
the version-policy workflow. Until CMRU owns that selection, an explicit
`--refresh` requires the npm and PyPI releases to be at least two days old by
default; the age window can be overridden for a deliberate refresh. See the
[version selection rationale](docs/DESIGN-GUIDE.md#upstream-version-age-window).

The selected MCR manifest digest is committed in the Dockerfile and
`docker-bake.hcl` and is also written to the transaction-local `cmru.vars`.
The resolver refreshes the digest from the exact MCR tag whenever it refreshes
the version, and `--check` refuses a missing or inconsistent digest.

### npm Package Pins

The following npm packages are pinned via `docker-bake.hcl` ARGs (with matching defaults in the `Dockerfile`):

| Package | Pin | Source |
|---|---|---|
| `@playwright/mcp` | `0.0.80` | MCP Streamable HTTP server for AI clients |
| `chrome-devtools-mcp` | `1.8.0` | CDP-based performance tracing and DevTools insights |
| `mcp-proxy` | `6.7.14` | stdio→streamable-HTTP proxy for chrome-devtools-mcp and lighthouse-mcp |
| `lighthouse` | `13.4.1` | Node API for programmatic Lighthouse audits |

`@playwright/mcp@0.0.80` bundles a Playwright 1.63 prerelease core; PWMCP
passes the executable path from the selected official base image explicitly.
`chrome-devtools-mcp@1.8.0` is checked against the bundled Chromium through
the live container smoke lane.
`chrome-devtools-mcp` requires Node `^20.19.0 || ^22.12.0 || >=23` (verify compatibility when upgrading the base image).

Lighthouse is used by the vendored in-repo lighthouse-mcp server at `containers/pwmcp/lighthouse-mcp/`.
It provides `lighthouse_audit()` and `lighthouse_metrics()` MCP tools returning bounded JSON results
(capped at 100 KB / 10 opportunities) — never the raw LHR (hundreds of KB).

## Browser Isolation Hardening

Applied in both modes:

- Run as UID/GID 1000 (non-root user shipped by the official base image)
- `cap_drop: ALL`
- `no-new-privileges: true`
- `shm_size: 2gb` (Chromium requires shared memory)

## Release Model

Versioned bundles are published to GitHub Releases under the `pwmcp-v<version>` tag:

```
pwmcp-v1.63.0-r4
  pwmcp-v1.63.0-r4.tar.xz          ← the deployment bundle
  pwmcp-v1.63.0-r4.tar.xz.sha256   ← sha256sum-verifiable sidecar
```

Releases up to and including `1.64.0-r2` carry the bundle without the `v`
(`pwmcp-<version>.tar.xz`). The installer (`get.py`) requests only the `v`-prefixed name, so it
cannot install those releases (PWMCP-08); pin a later release.

The release notes embed the SHA256 digest. Verify any downloaded bundle with:

```bash
sha256sum -c pwmcp-v1.63.0-r4.tar.xz.sha256
```

"Latest" is resolved programmatically by scanning `pwmcp-v*` releases and picking the highest semver — this works in a monorepo where GitHub's repo-global "Latest" badge cannot be per-project. The thin `pwmcp-latest` release contains only `latest.json` (a JSON redirect pointing at the versioned release); it does **not** duplicate the heavy bundle asset. Development consumers use that stable manifest URL to rebuild automatically; production consumers may choose an immutable version/digest.

### Downloading a specific version

```bash
VERSION="1.63.0-r4"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-v${VERSION}.tar.xz" \
  -o "pwmcp-v${VERSION}.tar.xz"
curl -fsSL "https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v${VERSION}/pwmcp-v${VERSION}.tar.xz.sha256" \
  -o "pwmcp-v${VERSION}.tar.xz.sha256"
sha256sum -c "pwmcp-v${VERSION}.tar.xz.sha256"
tar -xJf "pwmcp-v${VERSION}.tar.xz"
```

## Build

```bash
# Build unified image (local load)
docker buildx bake pwmcp --load

# Push to GHCR
docker buildx bake pwmcp --push
```

Or use the ciu build runner:

```bash
ciu-build -d . build-images
ciu-build -d . push-images
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — unified image design and chromium path resolution
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — internal and external deploy procedures
- [docs/SECURITY.md](docs/SECURITY.md) — browser isolation rationale and hardening
- [docs/USAGE.md](docs/USAGE.md) — consumer connect() and MCP usage details
- [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) — why standalone roots and client bundles use these boundaries
- [docs/CONSUMERS.md](docs/CONSUMERS.md) — installation, updates, CLI inspection, MCP, and release gates

## Testing

`run-gate` is the canonical test entrypoint — `run-gate --list`
discovers the declared lanes; definitions live in `run-gate.toml`.
See [`../run-gate-project/CONSUMERS.md`](../run-gate-project/CONSUMERS.md).

The release gate is `run-gate r1`. It runs the resolver, image-builder
contract, and client tests through source-backed Assay in `tester-unified`,
with branch coverage recorded in `coverage.json`. The slower mutation lane is
`run-gate r2`; it is resumable and may be run separately before a release.
PWMCP has no HTTP/OpenAPI contract, so Hypothesis is used for the pure upstream
version-intersection invariant and Schemathesis is not applicable.

### CLI diagnostics

The `pwmcp` client is registered on the shared cli-extended grammar (`contract`,
`doctor`, `inspect`, `skills`; see [`docs/CLI-SPEC.md`](docs/CLI-SPEC.md)). Help starts with
`PWMCP <version> — client utility`; a usage error prints its `[ERROR]` diagnostic first,
then the identity-headed usage, and exits 2. The version has one source,
`client/src/pwmcp_client/VERSION`, which is also the wheel's metadata version; the CLI
refuses to run if installed metadata and that file disagree (no literal fallback).
`pwmcp --version` prints exactly one `pwmcp <version>` line on stdout, exits 0, and writes
nothing to stderr. `pwmcp contract` output is unchanged; `pwmcp doctor` now reports named
checks (`contract`, `playwright`, `skills`). The client wheel depends on the `cli-extended`
wheel, which is published to GitHub Releases only; install it first (see
[`docs/USAGE.md`](docs/USAGE.md)). The release-step scripts (`build-push.py`,
`scripts/*.py`) use the same installed library and accept `--dry-run`.
