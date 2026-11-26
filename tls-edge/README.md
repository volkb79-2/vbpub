# tls-edge

tls-edge provides TLS termination as a shared host service: one hardened
Traefik v3 container handles HTTPS (port 443, one host name per service; port 80
for the redirect and ACME HTTP-01) for Docker Compose stacks on a host. HTTP/2
and HTTP/1.1 use the shared TCP listener; HTTP/3 over QUIC/UDP is an optional
instance-wide feature. Raw TCP+UDP media (for example Jitsi JVB or Mattermost
Calls) is published directly by the application, outside Traefik.
Consumers join the shared `ingress_public` network and declare labels — TLS
certificates are handled by the edge, including automatic issuance and
zero-downtime renewal via Let's Encrypt. A docker-socket-proxy (POST denied)
shields the Docker API from Traefik. See the
[direct-media design guide](docs/DESIGN-GUIDE.md) and
[consumer instructions](docs/CONSUMERS.md), plus the [transport and instance
configuration guide](docs/TRANSPORTS-AND-CONFIGURATION.md) for HTTP/2, HTTP/3,
QUIC, server events, and where operators configure the shared endpoint. The
repository's [Jitsi Meet deployment](https://github.com/volkb79-2/vbpub/blob/main/voice-screen-share/jitsi-server/README.md)
uses this same default `websecure` route; its consumer suite checks it against
the tls-edge renderer.

---

## Install

On a Linux host with Docker, `git`, and `python3` 3.13 or 3.14 (x86_64):

```bash
TAG='tls-edge-vX.Y.Z' # replace with the CLI-enabled release tag
curl -fsSLO "https://raw.githubusercontent.com/volkb79-2/vbpub/${TAG}/tls-edge/get.py"
sudo python3 get.py install --version "$TAG" --variant py313 # py314 on a 3.14 host
sudo /srv/tls-edge-instance/bin/tls-edge configure
cd /srv/tls-edge-instance/stack
sudo ./ciu check && sudo ./ciu up --dry-run && sudo ./ciu up
```

`get.py` downloads the release bundle, **verifies its SHA256 and manifest**, builds
a private venv from the bundled `ciu`, the `tls-edge` CLI and dependency wheels
(offline, hash locked). Configure the site after installation with
`tls-edge configure`, which can also read a TOML answers file. The instance lives in
`/srv/tls-edge-instance/stack`, a stable path that keeps the same ciu identity
across updates.  Install, certificate configuration, adding a consumer, update,
rollback and migrating a hand-made deployment are all in
**[docs/INSTALL.md](docs/INSTALL.md)**; label-by-label consumer examples are in
[consumer-examples/README.md](consumer-examples/README.md).

### Update

`bin/tls-edge-installer update` targets the system instance by default, accepts
`--scope user` for a user instance, and reuses the installed interpreter variant
unless `--variant` overrides it.

```bash
sudo /srv/tls-edge-instance/bin/tls-edge-installer update --variant py313
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

Your site config (`ciu-stack/ciu.toml.j2`) and ciu's state (`.ciu/`) are never
overwritten; `rollback` re-points to the previous release.

### Releases

Releases are immutable GitHub Releases in the `vbpub` monorepo tagged
`tls-edge-v<semver>` (e.g. `tls-edge-v2.1.0`).  Each release carries one bundle
per Python interpreter (the platform wheels are interpreter specific):
- `tls-edge-v<ver>-py313.tar.xz`, `tls-edge-v<ver>-py314.tar.xz` — the bundles
- a `.sha256` sidecar for each

"Latest" is resolved by scanning `tls-edge-v*` releases and picking the
highest semver — no separate "latest" release is used as the source of truth.

---

## Repository layout

```
tls-edge/
  src/tls_edge_cli/            ← installed `tls-edge configure/show/validate` CLI
  pyproject.toml                ← CLI wheel and optional interactive extra
  requirements-runtime.txt      ← CLI runtime dependency set
  ciu-stack/                  ← schema, shipped defaults, templates, ciu v2 deploy package
    ciu.defaults.toml.j2      ← full configuration contract with commentary
    ciu.compose.yml.j2        ← Compose template (Traefik + docker-socket-proxy)
    traefik.yml.j2            ← Traefik static config template
    conf.d/                   ← dynamic config templates (hot-reloaded by Traefik)
      options.yml             ← TLS options: sniStrict, cipher suites
      middlewares.yml         ← secure-headers middleware (HSTS etc.)
      certs.yml.j2            ← static cert store (acme-tls: empty; static/dev: populated)

  scripts/
    verify.sh                 ← post-install verification checks
    dev-certs.sh              ← self-signed CA + cert generation for dev mode
    gen-guard-secret.sh       ← generate bcrypt/APR1 hash for Pattern D basicAuth guard
    certbot-deploy-hook.sh    ← restart Traefik after a certbot renewal
    verify-external.sh        ← live HTTP/2 + HTTP/3 acceptance probe

  tools/                      ← maintainer-only; never copied into a bundle
    render.sh                 ← standalone renderer wrapper
    render_standalone.py      ← Jinja2+TOML render engine; DooD-aware paths
    update-rendered.sh        ← regenerate ignored edge-proxy/ trial output
    bundle_wheels.py          ← generated requirements + the one first-party wheel fetcher

  consumer-examples/          ← ready-to-copy example stacks
    pattern-a-public-only/
    pattern-d-guarded/          ← basicAuth per-route guard (labels only; no edge changes)
    pattern-e-media-udp/        ← HTTPS host + application-published TCP and UDP media
    README.md                   ← label-by-label explanation

  ARCHITECTURE.md             ← design rationale, tradeoffs, rejected alternatives
  CONSUMER_GUIDE.md           ← onboarding guide for service teams
  KNOWN_ISSUES_TODO_BACKLOG.md             ← current limitations and roadmap
```

---

## Quickstart A — trial, generated

The standalone tree is a generated trial artifact, not a supported deployment:

**The tls-edge-specific defaults** (ACME mode, port 80, ca_server, public port
set): the schema and every shipped value are in
[`ciu-stack/ciu.defaults.toml.j2`](ciu-stack/ciu.defaults.toml.j2), and
`tls-edge show` prints what a configured instance actually uses.

Setting an ACME contact email is strongly recommended (Let's Encrypt uses it to
warn the account before a rate limit is hit); [`INSTALL` §3](docs/INSTALL.md#3-install)
shows where it goes (`email` under `[tls_edge.acme]` in the answers file).

```bash
# 1. Point your DNS A/AAAA record at this host.

# 2. Generate the trial tree.
cd tls-edge
python3 tools/render_standalone.py --out "$PWD/edge-proxy" --defaults-only

# 3. Start the trial stack.
cd edge-proxy
docker compose up -d

# 4. Verify the trial (--no-canary: 4.0.0's canary orders a real certificate for
#    verify.<domain>; see docs/INSTALL.md section 3 and the backlog's known limitations).
bash ../scripts/verify.sh --no-canary

# 5. Onboard your services — see CONSUMER_GUIDE.md.
```

`edge-proxy/` is gitignored and may be deleted or regenerated at any time. For
the supported get.py + ciu deployment, follow
[INSTALL §3](docs/INSTALL.md#3-install).

---

## Quickstart B — get.py + ciu instance

[Install and configure the supported instance](docs/INSTALL.md#3-install).

---

## Quickstart C — wildcard certificate via manual DNS TXT

[Create the wildcard certificate with manual DNS TXT](CONSUMER_GUIDE.md#static-mode--wildcard-cert-via-manual-dns-txt-no-dns-api-required).

---

## Quickstart D — dev / devcontainer

[Configure dev mode and generate local certificates](docs/INSTALL.md#4-configure-listeners-and-certificates).

---

## Quickstart E — ciu v2

[Run the supported CIU stack from its instance](docs/INSTALL.md#3-install).

[Configure DNS-01 credentials through CIU](docs/INSTALL.md#4-configure-listeners-and-certificates).

---

## Why the supported path uses ciu

The get.py instance runs CIU from a stable stack root. CIU provides capabilities
that a generated trial tree cannot express:

**Secrets management.**  Plain `.env` files store credentials as readable text
on disk.  ciu's secret directives (`ASK_VAULT:`, `ASK_EXTERNAL:`, `GEN_*`,
`DERIVE:`) retrieve or generate credentials at deploy time and inject them as
Docker secrets (`/run/secrets/<name>` inside the container), never writing them
to rendered files.  The rendered output is leak-scanned before any container
starts.

**Validated workspace environment.**  ciu checks that required environment
variables are set, that named TLS certificate files exist _and_ are readable by
the Docker group, and that the host satisfies all preconditions before rendering
or starting anything.

**Devcontainer / DooD path handling.**  When the repo is mounted inside a
devcontainer and Docker runs outside it (Docker-outside-of-Docker), logical repo
paths and physical host paths diverge.  ciu translates paths transparently;
the standalone renderer handles the trial case separately.

**Multi-stack orchestration.**  `ciu-deploy` runs stacks in numbered phases with
health gates between them: deploy tls-edge in phase 1, verify it healthy, then
deploy application stacks in phase 2.

**Consistent naming + profiles.**  ciu derives container names from
`<project>-<environment>-<service>` automatically and supports per-host
profiles to activate different configuration sets.

**Clean lifecycle.**  `ciu --reset` tears down containers, volumes, rendered
files, and optionally secrets in a single command.  `docker compose down -v`
destroys only what that single compose file created and leaves rendered files
on disk.

---

## TLS mode reference

`tls-edge show` reports the mode and the port plan of a configured instance; that
is the listener requirement the CLI validates for a mode,
`src/tls_edge_cli/config.py:492-495`.
The mode list and their defaults are in
[`ciu-stack/ciu.defaults.toml.j2`](ciu-stack/ciu.defaults.toml.j2).  The table
below covers the operational choice only (which challenge each mode uses, and
how renewal happens); per-mode instructions are in
[INSTALL §4](docs/INSTALL.md#4-configure-listeners-and-certificates).

| Mode | Challenge | Renewal | Typical use |
|---|---|---|---|
| `acme-tls` | TLS-ALPN-01 | Automatic (Traefik) | Default; per-subdomain certs, no manual steps |
| `acme-http` | HTTP-01 | Automatic (Traefik) | When ALPN is blocked; needs the HTTP listener |
| `acme-dns` | DNS-01 | Automatic (Traefik + lego) | Wildcard cert; requires DNS provider API key |
| `static` | n/a | Manual certbot or hook | Manual wildcard TXT (no DNS API), or corporate certs |
| `dev` | n/a | n/a (397-day cert) | Self-signed CA (openssl) or mkcert; DooD-safe |

**Wildcard without DNS API** (`static` mode + `certbot --manual`): issue a
`*.example.com` cert interactively by adding a `_acme-challenge` TXT record
when certbot prompts.  No DNS API key needed.  Requires manual action every
~60 days.  See [the manual DNS-TXT procedure](CONSUMER_GUIDE.md#static-mode--wildcard-cert-via-manual-dns-txt-no-dns-api-required).

**Port 80 note**: whether the instance serves HTTP is an override value
(`[tls_edge.ports] expose_http`), so `tls-edge show` answers it for a live
instance.  `acme-http` needs it on; every other mode works with it off.

**Static mode note**: both `live/<domain>/` and `archive/<domain>/` must be
mounted.  Certbot uses relative symlinks between these two directories; mounting
only `live/` causes silent TLS failure inside the container.

---

## Verification

`scripts/verify.sh` runs a post-install checklist. Strictly, it checks, in order:

| Check | What it verifies |
|---|---|
| 1. ciu configuration | The bundled `<root>/ciu check` passes (SKIPs when the ciu launcher is absent) |
| 2. compose | `docker compose config -q` accepts the rendered stack |
| 3. health | Both containers report `healthy` (`docker inspect`) |
| 4. networks | `ingress_public` exists; `traefik_internal` exists and is internal |
| 5. socket proxy | A GET is allowed and a POST is refused at `dockerproxy:2375` |
| 6. redirect | With `expose_http = true`: the HTTP entrypoint answers 301/308 |
| 7. TLS | A certificate is served for the domain and it is not Traefik's default self-signed |
| 8. ACME/static | The ACME volume holds a non-empty `acme.json` at mode 0600, or the static cert file is readable |
| 9. canary | A labelled `whoami` container is routed through `websecure` |
| 10. access log | Traefik's recent log lines are JSON carrying `RouterName` |
| 11. ops isolation | With `--ops-check`: an ops endpoint is unreachable from the given network |

Run at any time, with `--no-canary` until 4.0.1 (4.0.0's canary orders a real certificate for
`verify.<domain>`, and its checks 5 and 9 and the exit status are known-bad; see
[`KNOWN_ISSUES_TODO_BACKLOG.md`](KNOWN_ISSUES_TODO_BACKLOG.md#known-limitations)):

```bash
bash scripts/verify.sh --no-canary
```

---

## Cutting a release

tls-edge's `cmru.toml` calls `cmru handler bundle-build` for both interpreter
variants. `bundle.toml` allowlists the operator files and vendors the exact
third-party closure generated from the ciu + cli-extended + tls-edge-cli
declarations under the estate's 14-day upload buffer. The three first-party
wheels come through one stage module. The maintainer scripts are not part of
the instance bundle.
The build step derives `TLS_EDGE_VERSION` from the committed `VERSION` file,
clears stale `dist/` output first and requires the version variable to be set.

The build retains `dist/VERSION` beside the tarball. CMRU uses that retained
copy when publishing a completed local build, so publication reads the version
from the exact build record after its worktree has been removed.

CMRU resolves credentials from `GITHUB_PUSH_PAT`/`GITHUB_TOKEN` or this project's
ignored `cmru.secret.toml [github].token`; a committed credential is rejected.

CMRU's `xztar` writer sorts members, normalises ownership and modes, and uses
`SOURCE_DATE_EPOCH`; two builds with identical inputs and epoch are byte-identical.

### Release command

```bash
cmru release tls-edge --set-version 0.2.0
# → isolated CMRU transaction: gate, commits VERSION + CHANGES, tags, builds,
#   publishes, and synchronises main, with the root audit log
```

This is the only release entry point: tls-edge keeps no release script of its own.

### What happens after publish

`get.py` resolves the latest release by querying the GitHub Releases API and
picking the highest-semver `tls-edge-v*` tag.  Once the release is published,
`bin/tls-edge-installer update` on any installed host downloads and verifies
the new bundle automatically.

### Artifact filename scheme

```
tls-edge-v<semver>-<variant>.tar.xz         ← bundle per interpreter (py313, py314)
tls-edge-v<semver>-<variant>.tar.xz.sha256  ← SHA256 sidecar (sha256sum -c format)
```

Example for version 2.1.0:
```
tls-edge-v2.1.0-py313.tar.xz
tls-edge-v2.1.0-py313.tar.xz.sha256
```

The tarball contains cmru's single staging directory as its top-level directory,
`bundle/`, so `tar -xJf … --strip-components=1` still extracts cleanly to any
target path.

---

## Further reading

- **ARCHITECTURE.md** — full design rationale: network model, TLS strategy,
  proxy selection, socket security, container hardening, renewal mechanisms,
  future options.
- **docs/DESIGN-GUIDE.md** — rationale for application-published TCP/UDP media.
- **docs/CONSUMERS.md** — HTTP routing plus direct application media publication
  and the firewall ports each path needs.
- **CONSUMER_GUIDE.md** — step-by-step onboarding for service teams: label
  reference, consumer patterns (A/B/C), verification commands.
- **KNOWN_ISSUES_TODO_BACKLOG.md** — current limitations, workarounds, and roadmap items.
