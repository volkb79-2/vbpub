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

## Client install and upgrade

The client is **one wheel, `pwmcp-client`**, with two faces: the **`pwmcp` CLI** (console script
`pwmcp_client.cli:main`: `contract`, `versions`, `doctor`, `inspect`, `ocr`, `skills`) and the
**`pwmcp_client` Python library** (`BrowserLease`, `load_contract`, `PwmcpContract`, `PwmcpError`,
`PwmcpUnavailable`, `VersionMismatch`). Installing the wheel gives both.

**Versioning: 1:1 with the server release.** The wheel version is generated at release time from the
server release (`client/src/pwmcp_client/VERSION`, one mapping helper in `pwmcp_client/release.py`):
`1.64.1-r2` becomes `1.64.1.post2` (PEP 440 reads `-rN` as the post-release segment, so
`packaging.version.Version("1.64.1-r2") == Version("1.64.1.post2")`). Two wheels with different
content therefore never share a version, and `pip` can tell and upgrade. Roll the client out together
with the server it talks to. `pwmcp --version` prints the exact network-free `pwmcp 1.64.1.post2`
line the shared CLI contract requires; **`pwmcp versions`** adds the server:
`pwmcp 1.64.1.post2 (server 1.64.1-r2)` or `(server unreachable)`.

**Compatibility policy** (what `pwmcp.contract.json` guarantees). The contract's
`playwright.protocol` (`<major>.<minor>` of Playwright) is the wire-protocol guarantee: a client
whose pinned `playwright` is the server's Playwright version drives the server's browser. The
r-number only versions pwmcp's own helpers and contract fields. `pwmcp doctor` has a `skew` check:

| client vs server | doctor | meaning |
|---|---|---|
| same release | OK | nothing to do |
| different Playwright major.minor | FAIL | hard incompatibility (the protocol line differs: `playwright.protocol` is `<major>.<minor>`) |
| same major.minor, different patch or `-rN` | WARN | works; upgrade the older side when convenient |

### Client only (a server runs elsewhere)

**Platform limit.** The release wheels are built for one interpreter and platform: **CPython 3.14 on Linux
x86_64** (`greenlet` is a cp314 wheel and `playwright` a manylinux x86_64 wheel). The installer and the
`pip` path below fail on any other Python or architecture; per-interpreter client wheels are tracked as PWMCP-36.

For a consumer that only talks to a pwmcp server (no docker, no ciu, no compose, no stack tree),
use the release asset `pwmcp-client-install.py`. It downloads the release bundle, verifies its SHA-256,
extracts only `manifest.json` and `wheels/`, verifies every wheel against the manifest, builds a private
venv with `pip install --no-index --require-hashes` (pwmcp-client plus its exact pinned
dependencies, including that release's Playwright) and links `~/.local/bin/pwmcp`:

```bash
base=https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v1.64.1-r2
curl -fsSLO "$base/pwmcp-client-install.py"; curl -fsSL "$base/pwmcp-client-install.py.sha256"   # check it
python3 pwmcp-client-install.py --version 1.64.1-r2      # private repo: --github-token-file FILE
pwmcp skills install && pwmcp doctor --contract http://pwmcp-host:3000/contract
```

Upgrade = run it again with the new release (releases live side by side under
`~/.local/share/pwmcp-client/releases/`; `current` and the launcher switch atomically), then
`pwmcp skills install`. `--bundle FILE` installs from a local bundle (offline).

Release assets per `pwmcp-v<release>` (all published with every release): `pwmcp-v<release>.tar.xz`
(+ `.sha256`; the stack bundle, which also contains `wheels/`), `get.py` (+ `.sha256`; full stack installer),
`pwmcp-client-install.py` (+ `.sha256`), `pwmcp_client-<version>-py3-none-any.whl` (+ `.sha256`; the client
wheel: CLI `pwmcp` and library `pwmcp_client` are the same wheel), the pinned dependency wheels
(`cli_extended-*`, `playwright-*`, `greenlet-*`, `pyee-*`, `typing_extensions-*`; cli-extended is not on PyPI)
and `pwmcp-client-requirements.txt`, a hash-pinned requirements file whose lines are
`name @ <asset URL> --hash=sha256:...`. Plain `pip` install, one go, hashes enforced:

```bash
rel=1.64.1-r3   # the server's release; the wheel version is 1.64.1.post3
curl -fsSLO https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v$rel/pwmcp-client-requirements.txt
python3 -m venv ~/.venvs/pwmcp && ~/.venvs/pwmcp/bin/pip install --require-hashes -r pwmcp-client-requirements.txt
~/.venvs/pwmcp/bin/pwmcp versions --contract http://pwmcp-host:3000/contract
```

(Private repository: the plain `pip` URLs above need public assets; with a private repository use
`pwmcp-client-install.py --github-token-file FILE`, which downloads through the GitHub API.)
`pwmcp-client-install.py` is the equivalent of a `get.py --client-only`: `get.py` is generated by
`cmru get-py`, so the flag is tracked in the cmru backlog and this script serves the same assets meanwhile.

### Deploying a release to the devcontainer instance (`/srv/pwmcp-instance`)

Operator procedure (do not run it from a release job; the instance is live). The instance was installed with
`--scope user` and `XDG_DATA_HOME=/srv`, which is what makes the user scope land on `/srv/pwmcp-instance`
(system scope needs root, and as root `get.py` refuses the group-writable instance directory owned by uid 1000).
`bin/` holds only the `pwmcp` launcher, so use the release's `get.py` (an `update`, not an `install`: install is the
fresh-install verb). Replace `$rel`:

```bash
rel=1.64.1-r3
cd /tmp && base=https://github.com/volkb79-2/vbpub/releases/download/pwmcp-v$rel
curl -fsSLO $base/get.py && curl -fsSL $base/get.py.sha256 && sha256sum get.py     # compare
XDG_DATA_HOME=/srv python3 get.py update --scope user --version pwmcp-v$rel
#   installs releases/pwmcp-v$rel-<hash> beside the old one, switches `current` atomically, records the previous
#   release in state.json; the sparse ciu.toml.j2 must NOT pin the image tag
readlink /srv/pwmcp-instance/current      # -> releases/pwmcp-v$rel-<hash>
cd /srv/pwmcp-instance && ciu up          # recreates only the changed container (pwmcp-cmqemn-pwmcp, new image tag)
pwmcp skills install                      # refresh packaged skills
pwmcp version && pwmcp contract && pwmcp doctor     # release/contract agree; doctor: skew OK, skills current
```

Order matters: the `pwmcp` launcher (client) swaps with `current` at the `update` step, before `ciu up` recreates
the container, so `pwmcp doctor` reports a skew mismatch for those seconds; it clears once the new container
answers. Downtime is the container recreate only. **Rollback:**

```bash
XDG_DATA_HOME=/srv python3 get.py rollback --scope user     # back to the release recorded in state.json
cd /srv/pwmcp-instance && ciu up && pwmcp skills install && pwmcp doctor
```

Do not repoint `current` with `ln -sfn`: `state.json` would drift from the symlink. (`rollback --version TAG`
selects a specific recorded release, e.g. `pwmcp-v1.64.0-r4`.)

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

## Bot protection (PWMCP-32)

Many sites answer an automated browser with a challenge or block page (Cloudflare
"Just a moment...", a CAPTCHA wall, an Akamai "Access Denied"). `pwmcp inspect` used to
return that page like any other content, so callers had to guess they were blocked. It now
says so.

**What `inspect` does.** After the page loads (or a wait ends) it checks the latest
main-frame document's status and headers, the page title, the body text and the frames it
embeds against a small signature table (`client/src/pwmcp_client/blocked.py`). On a match:

- exit code **4** (distinct from 1 = failure, 2 = usage, 3 = not installed);
- stderr: `[ERROR] blocked by bot protection (Cloudflare managed challenge); page content below
  is the challenge page, not the target (HTTP 403, signals: ...)`, plus a hint;
- stdout: that same sentence, a blank line, then the challenge page's text (at most 4000
  characters), so no consumer can mistake it for the target. `--output` is NOT written;
- `--json`: one object `{"blocked": true, "provider", "kind", "signals", "status",
  "final_url", "waited_ms", "message", "content"}` (plus `user_agent*`/`storage_state` fields when
  an override or session is active). A non-blocked `--json` result keeps its old shape, so the
  absence of `blocked` with exit 0 means "not recognised as bot protection";
- a `--wait-for` (or `--wait-until`) that ends while the page is still a challenge is reported as
  blocked with the time waited (`waited_ms`, and `still blocked after waiting N s` on stderr), not
  as a generic timeout. A blocked attempt is never retried by `--retries`.

**Signatures** (the tables are data; extend them with a row plus a fixture test):

| Kind | Signals | Counts as blocked |
|---|---|---|
| Definitive | `cf-mitigated: challenge`; a challenge title (`Just a moment...`, `Attention Required! \| Cloudflare`, `Pardon Our Interruption`, ...); challenge text (`Performing security verification`, `Checking your browser before accessing`, `Sorry, you have been blocked`, `DDoS protection by Cloudflare`, `Press & Hold to confirm you are human`, `verify you are human`, ...) on a page of at most 6000 characters of text | on its own |
| Hint | provider headers (`server: cloudflare`, `cf-ray`, `server: AkamaiGHost`, `x-datadome`, `x-iinfo`, ...) | only with HTTP 403, 429 or 503 |
| Hint | a captcha/challenge frame (`challenges.cloudflare.com`, `captcha-delivery.com`, `captcha.px-cdn.net`, `arkoselabs.com`, reCAPTCHA, hCaptcha) | with HTTP 403/429/503, or on a near-empty page (600 characters or less) |

A plain 403 with no provider marker is not called bot protection (the existing
`page diagnostics` line still reports the status), and neither is a normal login page that
embeds a reCAPTCHA or an article that quotes "Just a moment...". Providers are detected
only as DETECTION signatures: nothing here solves, bypasses or hides from them.

**Limits.** Detection is a heuristic. A page that is not recognised is "not detected as
blocked", not proof that it is the complete target. `inspect` reports the state when it
loads the page: without `--wait-for` a self-resolving interstitial that would clear in a
few seconds is reported as blocked, so pass `--wait-for SELECTOR` for content you expect.
`pwmcp ocr URL --selector ...` does not check for bot protection.

**Human in the loop (opt-in).** The legitimate way past a protection is a person. A HUMAN
operator opens the site in their own browser, passes the check, and exports the session as a
Playwright storage state:

```bash
# on the operator's machine (headed browser; solve the check by hand, then close it)
playwright codegen --save-storage=geekbench.state.json https://browser.geekbench.com/
chmod 600 geekbench.state.json
# copy it to where pwmcp runs (scp keeps the mode), then:
pwmcp inspect https://browser.geekbench.com/v7/cpu/1 --storage-state geekbench.state.json
```

(Any other way of producing a Playwright `{"cookies": [...], "origins": [...]}` file from your
own browser session works equally; a headed/VNC browser session run by a human is fine.)
The file holds session cookies, so it is a secret:

- `inspect` refuses a file that is group- or other-accessible (it must be mode 0600), is not a
  regular file, is not a storage state, or holds nothing for the URL's host;
- only the cookies and origins that belong to the URL's host are handed to the browser (a
  `.example.com` cookie applies to `www.example.com` but never to `notexample.com`), so one file
  can hold several sites without leaking one to another;
- values are never printed or logged: stderr says `storage state: N cookies and M origins applied`
  and `--json` carries only those counts. Do not commit the file or paste it into a ticket;
- the context is closed after the call, so nothing is stored in the browser service.

Limits: a clearance cookie (for example Cloudflare's `cf_clearance`) usually expires after
minutes to hours and may be bound to the IP address and user agent that earned it, so a state
exported on one machine can be rejected from another. When the supplied session is itself
challenged, the result is `blocked` again with a hint saying so; the human exports a fresh one.
Using a session you are not entitled to use is out of scope.

**Explicit non-goals.** No challenge solver, no CAPTCHA-solving service, no stealth or
anti-detection patch, no fingerprint spoofing, and no dependency that does any of that. The
`--user-agent` presets stay an identity string (see above); a site that blocks automation is
asking you to ask its owner for an API, a data feed, or permission.

## OCR sidecar (PWMCP-11 phase 1, opt-in, off by default)

**What it is.** A second, STATELESS container in the same ciu stack that turns an image
into text: you POST a PNG/JPEG, it runs native Tesseract 5 and returns the recognised
text plus a confidence (0-100) and a box for every line and word. It exists because some
consoles are one `<canvas>` (for example a VNC web console), so Playwright snapshots carry
no text and an agent could only read screenshots visually. It returns **TEXT AND
CONFIDENCE, not decisions**: deciding what a screen means (and what to do about it)
stays with the caller.

**Non-goal of this phase.** No watching, no waiting for text, no sending keys. Those are
phases 2 and 3 of PWMCP-11 and do not exist yet.

**Enable it.** Nothing changes unless you do (default `enabled = false`, and a default render
of the compose file is byte-identical to before). In `ciu.toml.j2`:

```toml
[pwmcp.ocr]
enabled = true            # renders the second service
# name = "pwmcp-ocr"      # service alias on the stack network (the only way to reach it)
# port = 8941             # 8939 admin API, 8940 run-server admin, 8941 OCR
# engines = "tesseract"   # advertised in /contract
# max_bytes = 8388608     # request body limit (8 MiB)
# max_dim = 4096          # max image width/height in pixels
# max_scaled_pixels = 24000000   # max width*height after scale (24 MP)
# body_deadline_s = 10    # total time to receive a request body
# timeout_s = 20          # per-request recognition timeout
# mem_limit = "512m"      # narrowing caps; ciu governance owns cgroup_parent
# cpus = "1.0"
```

The URL the contract advertises (`ocr_url`, for example `http://pwmcp-ocr:8941`) is a docker-DNS
alias that resolves only on the stack network; a CLI running anywhere else must pass `--sidecar-url`
or set `PWMCP_OCR_URL` to an address it can reach (the sidecar publishes no host port by default).

The sidecar is reachable only on the stack network (no published port, no Traefik route, in
either deployment mode), runs as uid 1000 with all capabilities dropped, a read-only root
filesystem and a small tmpfs, needs no outbound network, and runs under `nice`. The
pwmcp container learns its URL as `PWMCP_OCR_URL` (only rendered when enabled), and the
gateway `/contract` advertises it as `ocr.url` / `ocr.engines`; servers without the feature
simply lack the block and old clients ignore it.

**HTTP API** (`http://<alias>:<port>`):

| Endpoint | Purpose |
|---|---|
| `GET /health` | `200 {"status":"ok"}` when Tesseract is usable, else `503` |
| `GET /v1/engines` | engine names, availability, versions, installed languages |
| `POST /v1/ocr` | body = the PNG/JPEG bytes (`Content-Length` required); query below |

Query parameters of `POST /v1/ocr` (unknown or repeated ones are a 400): `engine`
(`tesseract` default; `glyphgrid` is registered but answers `501
{"error":"engine not implemented","code":"engine_unavailable"}` until phase 2), `lang`
(default `eng`; must be installed), `psm` (3, 4, **6** default = uniform block, suited to
consoles, 7, 11, 13), `scale` (integer 1-8, default 1; upscales before recognition because
Tesseract wants glyphs of roughly 30 px or more; boxes are reported in ORIGINAL pixels),
`whitelist` (printable ASCII, at most 128 characters, passed as one argv element, never
through a shell), `min_conf` (0-100, NO default: words BELOW it are flagged, never removed) and
`drop_low_conf` (`0`/`1`, default 0, needs `min_conf`: the explicit opt-in to really remove the flagged
words). The response:

```json
{"engine":"tesseract","engine_version":"5.x","lang":"eng","psm":6,"width":1280,"height":800,
 "elapsed_ms":210,"text":"...",
 "lines":[{"text":"...","conf":93.1,"conf_mean":93.1,"conf_min":88.0,"low_conf_words":null,"box":[x,y,w,h]}],
 "words":[{"text":"...","conf":95.0,"box":[x,y,w,h],"line":0,"low_conf":null}],
 "min_conf_applied":null,"low_conf_words":null,"unscored_words":0,"drop_low_conf":false,"dropped_words":0}
```

> **Confidence is NOT calibrated: do not treat "unflagged" or "high" as "correct".** Tesseract read a garbled
> TrueNAS installer dialog at a mean confidence of 91%, and misread digits (`10.0.2.15` as `16.0.2.15`,
> `9)` as `3)`). `low_conf` is a hint, never a safety check. Missing or fuzzy-matched text means UNKNOWN.
> Anything that will later trigger a keystroke needs an EXACT match of a known screen signature (or the
> glyph-grid engine, phase 2), never merely "unflagged". Without `min_conf` nothing is assessed at all.

**The result is complete; doubt is flagged, not deleted.** `text`, `lines[].text` and `words[]` always
contain every recognised word whatever its confidence. `min_conf` only sets the threshold: words below it
get `low_conf: true`, each line reports `conf_mean` (unweighted; `conf` is an alias kept for
compatibility), `conf_min` (the weakest word, so a high mean cannot hide a doubtful token such as the
`[y/N]` of `Continue? [y/N]`) and `low_conf_words`, and the top level reports `low_conf_words` (total),
`min_conf_applied`, `drop_low_conf` and `dropped_words`. Callers that match on this text (phases 2 and 3
will) must look at the flags. WITHOUT a `min_conf` nothing was assessed, so `low_conf`, per-line `low_conf_words`, the top-level
`low_conf_words` and `min_conf_applied` are JSON `null` (not `false`/`0`, which would read as "confident").
A word the engine returned without any confidence (`unscored_words` counts them) has `conf: null`, is kept,
and counts as flagged once a threshold is given. `pwmcp ocr --min-conf N` prints a warning when any word is
flagged, and when the sidecar predates this contract (it may then have dropped words silently:
`completeness_unknown` in the library result).

Errors are `{"error":"...","code":"..."}` with a 4xx/5xx status and never echo image bytes:
400 bad parameters, 411 no `Content-Length`, 413 body or image too large, 415 not a decodable
PNG/JPEG, 429 `busy` (ONE recognition runs at a time; retry), 501 engine not implemented or
Tesseract missing, 504 timeout, 500 engine failure (a fixed message; the engine's own diagnostics are logged server-side only). Every error the HTTP layer itself produces (bad request line, 414, 431, an unsupported method) uses the same JSON shape. Transparent PNGs are composited onto white. `GET /health` is 200 only when the Tesseract binary AND the default language's traineddata are present. A frame with no recognisable text is a
valid `200` with empty `text`.

**Limits** (all configurable): 8 MiB body, 4096x4096 pixels (checked on the image header
before any pixel is decoded), 8192 px per side after `scale` (400 `invalid_scale`) and 24 megapixels after `scale` (413
`scaled_image_too_large`), 10 s TOTAL to receive the body (408 `request_timeout`), 10 s TOTAL to receive the request
line and headers (408 `header_timeout`), 16 concurrent connections (further ones get a JSON 503 `overloaded`), 20 s per request,
one concurrent recognition, 512 MiB of memory.

**Agent workflow.** Take the screenshot with the MCP `browser_take_screenshot` tool, save it
to a file, then recognise it:

```bash
pwmcp ocr --image console.png                       # recognised text only
pwmcp ocr --image console.png --format tsv --min-conf 60 --scale 2
cat console.png | pwmcp ocr --image - --json        # full result with confidences and boxes
pwmcp ocr "$CONSOLE_URL" --selector canvas --scale 2 --wait-ms 1000   # capture + recognise
```

The URL form opens the page in a fresh context exactly like `inspect` (server-level user
agent included), waits for the element to be visible, captures it, recognises it and closes
the context. The element is captured with Playwright's compositor screenshot (works for 2D
and WebGL canvases, never trips over a tainted canvas) so the image is the element as
DISPLAYED, not the canvas's intrinsic bitmap; use `--scale` for small text. `--region
x,y,w,h` limits the capture to a rectangle of the element. The sidecar URL defaults to
`$PWMCP_OCR_URL`, else the `ocr_url` the contract advertises; `--sidecar-url` overrides both.
If none is known, `pwmcp ocr` exits 2 with a message naming `ocr.enabled`. From Python:
`pwmcp_client.ocr.recognize(image_bytes, sidecar_url=..., psm=6, scale=2)` returns an
`OcrResult`.

**Gate dependency.** The registered pwmcp gate lanes judge `containers/ocr/ocr_service.py` at 100% line and
branch coverage and run every test that needs an optional dependency. The lanes therefore DEPEND ON THE
TESTER IMAGE CARRYING THE `pwmcp-client` `test` extra (pytest, pillow, websockets, jinja2, pyyaml, declared in
`client/pyproject.toml` by the tester-unified change that lands separately on main, which also rebuilds the
image) plus `node` and `sh`. Operator policy (2026-10-27): a test that needs an optional module or binary
FAILS when it is missing, with a message naming it and the `test` extra; skipping is the opt-in. One helper
(`tests/_testdeps.py`, used by every site in `tests/` and `client/tests/`) implements it, and a test forbids
bare `pytest.importorskip` / `skipif(... is None)`. On a developer machine without a dependency, set
`PWMCP_ALLOW_MISSING_TEST_DEPS=1` to skip those tests cleanly; the gate lanes set nothing, so they cannot
skip. The one deliberate exception is the real-Tesseract test: `tesseract` legitimately exists only in the
sidecar image, so it skips with an explicit reason, and fails instead when `PWMCP_REQUIRE_TESSERACT=1` (set it
only in a lane that runs inside the OCR image; none exists today, the gate runs in the tester image).
The same module's `optional_local_data` helper handles optional local fixtures: it skips only when the
loader reports that no data was selected, while invalid selected data remains an error. The console
corpus tests use this helper with `PWMCP_REQUIRE_CONSOLE_CORPUS=1` to make an absent local corpus fail.
The gate image carries this declared test dependency set. The judged Python targets are the client package
(`__init__`, `cli`, `contract`, `diagnostics`, `ocr`, `session`, `user_agent`), `build-push.py`,
`scripts/resolve-playwright-version.py` and `containers/ocr/ocr_service.py`. The Node code
(`containers/pwmcp/ua-override/index.js`, the run-server and MCP gateways, the admin server) is outside the
Python judge entirely and is exercised only by the subprocess tests.

**OCR sidecar image (`1.64.0-r4`).** The `1.64.0-r4` release publishes the `pwmcp-ocr` image
alongside the main pwmcp image. It remains opt-in: `pwmcp.ocr.enabled` defaults to false. With the
defaults, enabling `pwmcp.ocr` selects that published image. For local development or changes to the
sidecar image, build it from `pwmcp/` and use the coordinate rendered by your config:

```bash
docker build -f containers/ocr/Dockerfile -t ghcr.io/volkb79-2/pwmcp-ocr:1.64.0-r4 .
```

Compose renders `image: <registry>/<namespace>/<name>:<tag>` from `[pwmcp.ocr.image]` with
`pull_policy: missing`, so an image already present under that exact name is used and never pulled.
To use a different name instead, set `registry`, `namespace`, `name` and `tag` under `[pwmcp.ocr.image]`
in your `ciu.toml.j2` (together with `enabled = true` under `[pwmcp.ocr]`).

**Release verification.** The post-release smoke for `1.64.0-r4` verifies the image's health,
confirms `/v1/engines` reports Tesseract available (and `glyphgrid` unavailable), and checks that a
generated PNG request returns recognized text and confidence. It does not assess OCR accuracy on
real console frames; the gateway `/contract` reports release `1.64.0-r4`.

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

**Two images, one release.** The `1.64.0-r4` release builds and pushes both
`ghcr.io/volkb79-2/pwmcp:<tag>` and the OCR sidecar `ghcr.io/volkb79-2/pwmcp-ocr:<tag>` (and their
`latest`) with the same release coordinate (`1.64.0-r4` for this release) in one `docker buildx bake all`;
the bundle's `ciu.defaults.toml.j2` pins both
(`[pwmcp.unified.image] tag` and `[pwmcp.ocr.image] tag`, kept equal by
`scripts/resolve-playwright-version.py`), so a consumer that enables `pwmcp.ocr.enabled` gets the sidecar
built with that release. The sidecar's `debian:trixie-slim` base is pinned by manifest digest like the
Playwright base: `--refresh` re-resolves it and `--check` refuses a tag-only or inconsistent pin.
`pwmcp.contract.json` records the release and Playwright version but carries no image coordinates; the
resolver updates its version fields alongside the image pins. `bake all --push` builds and pushes the two targets CONCURRENTLY, so no order is guaranteed. A PARTIAL push
looks like this: one image (and its `latest`) is in GHCR and the other is missing or stale, the step fails
and no GitHub release or git tag has been made yet. Recovery: fix the cause (token scope, network, builder)
and re-run the push step of the SAME prepared transaction; pushing is idempotent for identical content. Do
not publish the bundle until `docker buildx imagetools inspect` shows BOTH `pwmcp:<tag>` and
`pwmcp-ocr:<tag>`; if a half-pushed tag must be abandoned, the operator deletes the stray package version
in the GHCR UI. (A version-bump release cut by mistake is the recovery that went wrong on 2026-09-08.)

**First release of the OCR image: checklist.** The first push creates a GHCR package that has never existed.
1. *Before the release:* confirm the base digest is current with
   `docker buildx imagetools inspect debian:trixie-slim` and compare it with `DEBIAN_IMAGE_DIGEST` in
   `containers/ocr/Dockerfile`; run `python3 scripts/resolve-playwright-version.py --refresh` if it differs
   (no age window is applied to this rolling base, by design, decided 2026-10-27). A release transaction
   PREPARED before the `DEBIAN_RELEASE` / `DEBIAN_IMAGE_DIGEST` keys existed fails closed on the new
   `cmru.vars` keys: re-run the prepare phase instead of editing `cmru.vars`.
2. *Token:* confirm the release token has `write:packages` for a NEW package under the account
   (PWMCP-01 notes the PAT once lacked it).
3. *After the push:* expect `pwmcp-ocr` to be created PRIVATE and possibly not yet linked to the repository
   (the `org.opencontainers.image.source` label in `containers/ocr/Dockerfile` is what links it). Make it
   public once in the GHCR UI (Your packages -> pwmcp-ocr -> Package settings -> Change visibility);
   it persists for all later pushes.
4. *If the visibility sync hard-fails after BOTH images were pushed* (cmru KI-97: a brand-new package can be
   "not visible yet" for longer than the 6x5 s retry): the images are already published, so do NOT redo the
   release. Re-run ONLY the sync step (`GHCR_PACKAGE_NAMES=pwmcp,pwmcp-ocr` and the release identity
   exported, then the visibility part of `build-push.py`, or set the package public in the UI). Never use
   `cmru release --resume` without `--set-version`: that mistake produced the wrong v1.0.0 release of
   2026-09-08 (see the nyxloom-cmru-adoption incident record).

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
- [GlyphGrid development tools](tools/glyphgrid/README.md) — local font atlases,
  reference-checked reads, and deterministic synthetic images; offline tooling
  only, with the service engine still HTTP 501. See the [design rationale](docs/DESIGN-GUIDE.md#glyphgrid-development-tooling)
  and [pasteable examples](docs/CONSUMERS.md#glyphgrid-development-tooling).

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

### Local console corpus

The repository keeps selected public, anonymous console screenshots as curated
fixtures. The full 242-frame TrueNAS capture stays in a local directory and is
never published. Set `PWMCP_CONSOLE_CORPUS_DIR` to an absolute path containing
`frames/` to let the two full-corpus checks verify each frame and the pinned,
deterministic tar digest. With no path, those checks skip with an explicit
reason; `PWMCP_REQUIRE_CONSOLE_CORPUS=1` makes a missing corpus fail during a
focused run. The registered gate lanes do not include or require the full
capture. See the [design rationale](docs/DESIGN-GUIDE.md#local-console-corpus)
and [local verification example](docs/CONSUMERS.md#local-console-corpus).

### CLI diagnostics

The `pwmcp` client is registered on the shared cli-extended grammar (`contract`,
`versions`, `doctor`, `inspect`, `ocr`, `skills`; see [`docs/CLI-SPEC.md`](docs/CLI-SPEC.md)). Help starts with
`PWMCP <version> — client utility`; a usage error prints its `[ERROR]` diagnostic first,
then the identity-headed usage, and exits 2. The version has one source,
`client/src/pwmcp_client/VERSION` (generated from the server release at release time, see
[Client install and upgrade](#client-install-and-upgrade)), which is also the wheel's metadata version; the CLI
refuses to run if installed metadata and that file disagree (no literal fallback).
`pwmcp --version` prints exactly one `pwmcp <version>` line on stdout, exits 0, and writes
nothing to stderr. `pwmcp contract` output is unchanged; `pwmcp doctor` now reports named
checks (`contract`, `playwright`, `skew`, `skills`). The client wheel depends on the `cli-extended`
wheel, which is published to GitHub Releases only; install it first (see
[`docs/USAGE.md`](docs/USAGE.md)). The release-step scripts (`build-push.py`,
`scripts/*.py`) use the same installed library and accept `--dry-run`.
