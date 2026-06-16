# tls-edge

tls-edge provides TLS termination as a shared host service: one hardened
Traefik v3 container terminates HTTPS (ports 443 + 8443; port 80 off by
default) for all Docker Compose stacks on a host.  Consumers join the shared
`ingress_public` network and declare four labels — TLS certificates are handled
entirely by the edge, including automatic issuance and zero-downtime renewal via
Let's Encrypt.  A docker-socket-proxy (POST denied) shields the Docker API from
Traefik.

---

## Install

On any Linux host with Docker and git already installed:

```bash
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/tls-edge/get.sh | sudo bash
```

This installs tls-edge to `/opt/tls-edge-src` and places a `tls-edge` command
at `/usr/local/bin/tls-edge`.  When it finishes, run:

```bash
tls-edge install
```

### Pin a specific version

```bash
TLS_EDGE_VERSION=tls-edge-v0.1.0 \
  curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/tls-edge/get.sh | sudo bash
```

### Update to a newer release

```bash
tls-edge update
```

Fetches the latest `tls-edge-v*` tag, checks out the new version, and
reinstalls the CLI wrapper.  Your site-specific config
(`ciu-stack/ciu.toml.j2`, `edge-proxy/.env`) is preserved across updates.

### Releases

Releases are git tags in the `vbpub` monorepo following the convention
`tls-edge-v<semver>` (e.g. `tls-edge-v0.1.0`).  No pre-built artifacts are
required — the installer clones only the `tls-edge/` subtree directly from the
tagged commit.

---

## Repository layout

```
tls-edge/
  ciu-stack/                  ← templates + source of truth + ciu v2 deploy package
    ciu.defaults.toml.j2      ← full configuration contract with commentary
    ciu.compose.yml.j2        ← Compose template (Traefik + docker-socket-proxy)
    traefik.yml.j2            ← Traefik static config template
    conf.d/                   ← dynamic config templates (hot-reloaded by Traefik)
      options.yml             ← TLS options: sniStrict, cipher suites
      middlewares.yml         ← secure-headers middleware (HSTS etc.)
      certs.yml.j2            ← static cert store (acme-tls: empty; static/dev: populated)

  edge-proxy/                 ← pre-rendered default-mode artifacts
    docker-compose.yml        ← *** do not edit rendered copies — edit templates ***
    traefik.yml               ← *** and re-render via scripts/render.sh or ciu     ***
    conf.d/                   ← rendered dynamic config
    .env.example              ← acme-dns secret variable names

  scripts/
    render.sh                 ← standalone renderer (wraps render_standalone.py)
    render_standalone.py      ← Jinja2+TOML render engine; DooD-aware path handling
    install.sh                ← interactive guided installer (see Quickstart B)
    verify.sh                 ← post-install verification checks
    dev-certs.sh              ← self-signed CA + cert generation for dev mode
    gen-guard-secret.sh       ← generate bcrypt/APR1 hash for Pattern D basicAuth guard
    update-rendered.sh        ← update edge-proxy/ from templates (CI helper)

  consumer-examples/          ← ready-to-copy example stacks
    pattern-a-public-only/
    pattern-b-public-and-private/
    pattern-c-public-and-ops-sidecar/
    pattern-d-guarded/          ← basicAuth per-route guard (labels only; no edge changes)

  ARCHITECTURE.md             ← design rationale, tradeoffs, rejected alternatives
  CONSUMER_GUIDE.md           ← onboarding guide for service teams
  KNOWN_ISSUES.md             ← current limitations and roadmap
```

---

## Quickstart A — plain `docker compose` (no tooling required)

Suitable for sysadmins who want to inspect the rendered defaults and start
without installing any tooling.

**Defaults**: ACME TLS-ALPN-01 challenge, port 80 off, Let's Encrypt
production CA.  Setting an ACME contact email is strongly recommended (avoids
rate-limit notices from Let's Encrypt); see the note below.

```bash
# 1. Point your DNS A/AAAA record at this host.

# 2. Start the stack.
cd tls-edge/edge-proxy
docker compose up -d

# 3. Verify.
bash ../scripts/verify.sh

# 4. Onboard your services — see CONSUMER_GUIDE.md.
```

To set an ACME email without re-rendering, edit
`edge-proxy/traefik.yml` directly (static config — requires container
restart).  For a persistent change, set `tls_edge.acme.email` in
`ciu-stack/ciu.toml.j2` and re-render with `scripts/render.sh`.

---

## Quickstart B — guided installer

`scripts/install.sh` is an interactive script that:

1. Detects the host FQDN via reverse DNS lookup.
2. Probes DNS capabilities: wildcard record present? A/AAAA vs host IPs? Dangling AAAA?
3. Reports which TLS modes are possible given the DNS state.
4. Picks and configures the TLS mode, prompts for any required secrets.
5. Renders the templates into `edge-proxy/`.
6. Starts the stack and runs `scripts/verify.sh`.

```bash
# Interactive (recommended for first-time setup):
bash scripts/install.sh

# With specific mode and staging CA (safe for experimentation):
bash scripts/install.sh --mode acme-tls --staging
```

---

## Quickstart C — wildcard certificate via manual DNS TXT

For operators who want a wildcard cert (`*.example.com`) but do **not** have
programmatic access to their DNS zone (no API key).  Uses
`certbot --manual --preferred-challenges dns` for initial issuance; the
installer guides you through the DNS TXT record step interactively.

```bash
# Let the installer guide you:
bash scripts/install.sh --mode static --domain example.com
# → when prompted for "wildcard?", answer: y
# → certbot will display a TXT record value; add it to your DNS zone
# → press Enter in certbot once the record is live; cert is issued
```

Or run certbot manually first, then install:

```bash
# 1. Issue the wildcard cert (interactive — certbot pauses for your DNS update).
certbot certonly \
  --manual --preferred-challenges dns \
  --email ops@example.com --agree-tos --no-eff-email \
  -d example.com -d '*.example.com'
# certbot will output something like:
#   Please deploy a DNS TXT record under the name:
#   _acme-challenge.example.com
#   with the following value: AbCdEf123...
#   (add the record, then press Enter)
# Cert saved to: /etc/letsencrypt/live/example.com/

# 2. Configure tls-edge for static mode.
bash scripts/install.sh --mode static --domain example.com --render-only

# 3. Start.
cd edge-proxy && docker compose up -d
```

**Renewal** — every ~60-90 days, re-run the certbot command above and add the
new TXT record value when prompted.  The deploy hook auto-reloads Traefik:

```bash
# Check expiry before it becomes urgent:
certbot certificates

# Re-issue (interactive — prompts for a new TXT record):
certbot certonly \
  --manual --preferred-challenges dns \
  --email ops@example.com --agree-tos --no-eff-email \
  -d example.com -d '*.example.com'

# Traefik reloads automatically via the deploy hook; no restart needed.
# If the hook is not installed: docker restart --time 5 edge-traefik
```

> **No DNS API access?**  This is the right path.  If you later gain API
> access to your DNS provider, switch to `acme-dns` mode for fully automated
> renewals (no manual TXT records ever again).

---

## Quickstart D — dev / devcontainer

For local development, devcontainers, or hosts without public DNS.  Generates
a self-signed local CA and certificate using `openssl` (or `mkcert` if
installed).  Works under Docker-outside-of-Docker via a named volume for certs.

```bash
# Automated:
bash scripts/install.sh --mode dev --domain test.localdomain

# Or generate certs manually and start:
bash scripts/dev-certs.sh --domain test.localdomain
cd edge-proxy && docker compose up -d
```

Browsers will show a security warning unless the generated CA certificate is
imported into the browser/OS trust store.  `mkcert -install` handles this
automatically if mkcert is available.

---

## Quickstart E — ciu v2

For teams using [ciu](../ciu/) for multi-stack deployment management.

1. Copy `ciu-stack/` into your ciu-managed infrastructure repository (e.g.
   `infra/tls-edge/`).
2. Create a gitignored `ciu.toml.j2` in the same directory with your
   overrides.
3. Run `ciu` in the stack directory.

**Minimal override (`ciu-stack/ciu.toml.j2`):**

```toml
[tls_edge.acme]
email = "ops@example.com"

[tls_edge.tls]
mode = "acme-tls"   # or acme-dns / static / dev
```

**acme-dns with Cloudflare (DNS-01, wildcard-capable):**

```toml
[tls_edge.tls]
mode = "acme-dns"

[tls_edge.acme.dns]
provider    = "cloudflare"
wildcard_main = "example.com"   # issues *.example.com + example.com

[tls_edge.acme.dns.environment]
CF_DNS_API_TOKEN_FILE = "/run/secrets/dns_api_token"

[tls_edge.secrets]
dns_api_token = "ASK_EXTERNAL:CF_DNS_API_TOKEN"
```

ciu resolves `ASK_EXTERNAL:CF_DNS_API_TOKEN` from the operator-supplied
environment (or Vault), injects it as a Docker secret, and leaks-scans the
rendered output before starting anything.

---

## Why use ciu instead of plain compose

Plain `docker compose up -d` works and is fully supported — ciu is a wrapper,
not a requirement.  When the host grows beyond a single stack, ciu provides
capabilities that plain Compose cannot express:

**Secrets management.**  Plain `.env` files store credentials as readable text
on disk.  ciu's secret directives (`ASK_VAULT:`, `ASK_EXTERNAL:`, `GEN_*`,
`DERIVE:`) retrieve or generate credentials at deploy time and inject them as
Docker secrets (`/run/secrets/<name>` inside the container), never writing them
to rendered files.  The rendered output is leak-scanned before any container
starts.

**Validated workspace environment.**  ciu checks that required environment
variables are set, that named TLS certificate files exist _and_ are readable by
the Docker group, and that the host satisfies all preconditions before rendering
or starting anything.  With plain compose, a missing file causes a silent TLS
failure after the container starts.

**Devcontainer / DooD path handling.**  When the repo is mounted inside a
devcontainer and Docker runs outside it (Docker-outside-of-Docker), logical repo
paths and physical host paths diverge.  ciu translates paths transparently;
the standalone renderer requires manual `--base-dir` overrides.

**Multi-stack orchestration.**  `ciu-deploy` runs stacks in numbered phases with
health gates between them: deploy tls-edge in phase 1, verify it healthy, then
deploy application stacks in phase 2.  Plain compose has no native equivalent
for cross-stack sequencing.

**Consistent naming + profiles.**  ciu derives container names from
`<project>-<environment>-<service>` automatically and supports per-host
profiles to activate different configuration sets.

**Clean lifecycle.**  `ciu --reset` tears down containers, volumes, rendered
files, and optionally secrets in a single command.  `docker compose down -v`
destroys only what that single compose file created and leaves rendered files
on disk.

---

## TLS mode reference

| Mode | Challenge | Port 80 needed | Wildcard | Renewal | Typical use |
|---|---|---|---|---|---|
| `acme-tls` | TLS-ALPN-01 | No | No | Automatic (Traefik) | Default; per-subdomain certs, no manual steps |
| `acme-http` | HTTP-01 | Yes | No | Automatic (Traefik) | When ALPN is blocked; port 80 must be open |
| `acme-dns` | DNS-01 | No | Yes | Automatic (Traefik + lego) | Wildcard cert; requires DNS provider API key |
| `static` | n/a | No | Yes | Manual certbot or hook | Manual wildcard TXT (no DNS API), or corporate certs |
| `dev` | n/a | No | Yes | n/a (397-day cert) | Self-signed CA (openssl) or mkcert; DooD-safe |

**Wildcard without DNS API** (`static` mode + `certbot --manual`): issue a
`*.example.com` cert interactively by adding a `_acme-challenge` TXT record
when certbot prompts.  No DNS API key needed.  Requires manual action every
~60 days.  See Quickstart C above.

**Port 80 note**: port 80 is off by default.  Enable it by setting
`tls_edge.ports.expose_http = true` in your override and re-rendering.  This
also activates the HTTP→HTTPS redirect entrypoint (required for `acme-http`).

**Static mode note**: both `live/<domain>/` and `archive/<domain>/` must be
mounted.  Certbot uses relative symlinks between these two directories; mounting
only `live/` causes silent TLS failure inside the container.

---

## Verification

`scripts/verify.sh` runs a post-install checklist.  Categories checked:

| Check | What it verifies |
|---|---|
| Stack health | Both containers report healthy (`docker inspect`) |
| Port binding | 443 and 8443 are bound on the expected interface |
| TLS handshake | `openssl s_client` against the host FQDN; cert chain valid |
| ACME volume | `acme.json` exists and is mode 0600 |
| Network | `ingress_public` network exists and Traefik is attached |
| DNS | Host FQDN resolves to the host's public IP |
| Port 80 | Reports whether HTTP→HTTPS redirect is active or port is closed |

Run at any time:

```bash
bash scripts/verify.sh
```

---

## Cutting a release

Releases are git tags in the `vbpub` monorepo — no Docker builds, no upload
artifacts.  The `scripts/release.sh` script handles the mechanics.

### Standalone (simplest)

```bash
bash tls-edge/scripts/release.sh 0.2.0
# → updates VERSION, commits, creates annotated tag tls-edge-v0.2.0
# → prints the push command when done

git push origin main tls-edge-v0.2.0
```

Accepts an optional leading `v` (`0.2.0` and `v0.2.0` both work).

### Via release-runner (consistent with other vbpub projects)

```bash
echo "TLS_EDGE_VERSION=0.2.0" > tls-edge/.release-vars
python3 release-runner.py --project tls-edge
# → runs scripts/release.sh, same outcome as above

git push origin main tls-edge-v0.2.0
```

`.release-vars` is gitignored; remove it after the release.

### What happens on `git push`

`get.sh` resolves the latest release via `git ls-remote --tags` on the public
repo.  Once the tag is visible on GitHub, `tls-edge update` on any installed
host will offer the new version.

---

## Further reading

- **ARCHITECTURE.md** — full design rationale: network model, TLS strategy,
  proxy selection, socket security, container hardening, renewal mechanisms,
  future options.
- **CONSUMER_GUIDE.md** — step-by-step onboarding for service teams: label
  reference, consumer patterns (A/B/C), verification commands.
- **KNOWN_ISSUES.md** — current limitations, workarounds, and roadmap items.
