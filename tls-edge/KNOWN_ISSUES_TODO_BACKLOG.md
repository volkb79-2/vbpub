# Known Issues & Roadmap

---

## Known limitations

### Mattermost Calls media requires explicit edge opt-in

**Resolved 2026-11-21:** the generic tls-edge template keeps raw TCP/UDP media
ports closed by default. When hosting a Mattermost Calls consumer, configure
the edge with `[tls_edge.calls] enabled = true` and `public_port = 8443`; the Mattermost
project's own default media port remains 8443. This is an intentional split
between safe generic edge defaults and an application-specific listener.

`tests/test_calls_defaults_agree.py` pins the shared port numbers while allowing
the edge listener to remain disabled until an operator opts in. A live Calls
acceptance check still belongs in the migration checklist.

### acme-data volume loss triggers certificate re-issuance (`store = "volume"`)

This limitation applies only with `[tls_edge.acme] store = "volume"`. Running
`docker compose down -v`, `ciu --reset`, or `ciu clean` may destroy the
instance-owned `acme-data` named volume, which contains `acme.json` — Traefik's
ACME account key and all issued certificates. On the next start, Traefik
re-issues certificates from scratch. Let's Encrypt enforces a rate limit of
approximately five duplicate certificates per registered domain per week;
hitting this limit blocks new issuance for up to a week.

Workaround: use the hostdir storage created for new root-run instances, or
avoid `-v` on the edge-proxy stack. Before any destructive operation on a
volume-backed edge, back up the volume:
```bash
docker run --rm -v acme-data:/data -v "$(pwd)":/backup alpine \
  tar czf /backup/acme-data.tar.gz -C /data .
```
To move an existing instance to `<instance root>/shared/acme`, follow INSTALL
section 6. The hostdir survives `ciu --reset`, `down -v`, and `ciu clean`, and
can be backed up with the host filesystem.
An adopted external volume is still removable by `ciu clean` (see INSTALL §6).

---

### `sniStrict` rejects SNI-less clients

`conf.d/options.yml` enables `sniStrict: true` by default so that Traefik
refuses connections that do not present a valid SNI hostname.  This protects
against certificate fingerprinting via bare-IP requests but rejects legitimate
SNI-less clients, including some load-balancer health probes, very old TLS
stacks (pre-TLS 1.1), and `curl` invocations against the bare IP address.

Workaround: set `sniStrict: false` in `conf.d/options.yml`.  Traefik
hot-reloads the file via inotify; no container restart is required.

---

### One media pair per edge (Calls)

The Calls TCP and UDP entrypoints (8443) can be attached by one consumer only: a second consumer would
share the public port.  There is no alternate HTTPS port; every HTTPS service needs its own host name.

---

### Port 80 is on by default

Port 80 carries the permanent HTTP→HTTPS redirect and ACME HTTP-01. A host
that must not serve it sets `tls_edge.ports.expose_http = false` with
`tls-edge configure`; `acme-http` mode then cannot work.

---

### Manual-TXT wildcard renewal requires a DNS record on every renewal

The combination of `tls.mode = "static"` + `certbot --manual` and a wildcard
certificate requires creating a `_acme-challenge` TXT record by hand on every
renewal cycle, which occurs approximately every 60 days.  Missing the window
causes certificate expiry.

Workaround: switch to `tls.mode = "acme-dns"` with an API-capable DNS
provider (Cloudflare, Hetzner, deSEC, Route 53, and many others supported via
lego), or use CNAME delegation to point `_acme-challenge.<yourdomain>` to a
zone you can update programmatically (e.g. deSEC or a self-hosted acme-dns
instance).  Both approaches automate renewal without manual DNS intervention.

---

### lego `*_FILE` env support varies by DNS provider

Traefik uses lego for DNS-01 challenge automation.  The convention of reading
credentials from a file path in a `<VAR>_FILE` environment variable (e.g.
`CF_DNS_API_TOKEN_FILE`) is supported by Cloudflare but not uniformly
implemented across all lego providers.

For providers that do not support `*_FILE`, ciu users have two options: use the
`expose_env` escape hatch to inject the plaintext variable directly (this is
discouraged as it surfaces the secret in rendered files and container
environment inspection), or run in standalone `.env` mode by placing the
credential in the gitignored `edge-proxy/.env` file and relying on Compose
`env_file` injection.

---

### `ingress_public` east-west visibility

All containers that join `ingress_public` can attempt TCP connections to each
other, regardless of which service Traefik routes to which hostname.  Traefik
routes only the ports declared via labels; it does not firewall east-west traffic
at the network level.  A compromised container on `ingress_public` can
port-scan or connect to any other container on that network.

Workaround: keep `ingress_public` membership minimal — attach only the
public-facing service container of each stack, never databases, caches, or
workers.  Backend containers must join only the stack's private internal
network.

Roadmap: add a `DOCKER-USER` iptables enforcement helper (see Roadmap below)
for kernel-level east-west isolation between containers on the shared ingress
network.

---

### Single Traefik instance is a single point of failure

The edge-proxy stack runs one Traefik container.  If it crashes or is
restarting, all public services on the host are unreachable for the duration.

Mitigation: `restart: unless-stopped` ensures Traefik is automatically
restarted after transient crashes; the typical recovery time is under five
seconds.  For higher availability, Traefik HA requires Docker Swarm or
Kubernetes mode, both of which are out of scope for a single-host compose
deployment.

---

## Roadmap

### TE-03 — four deployment paths

The README offers plain compose on the committed `edge-proxy/`,
`scripts/install.sh` (a stale third override writer without HTTP/3 or Calls
fields), manual wildcard, dev and a ciu checkout, and the CLI accepts only an
instance root. Decided: the get.py instance with ciu is the one supported path;
`install.sh` is deleted; the CLI takes a stack dir (the directory containing
`ciu-stack/`, `consumer.env` optional) so it serves a checkout too;
`edge-proxy/` is no longer committed but generated by
`render_standalone.py --out DIR` as a trial artifact.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` H4

### TE-04 — certbot hook inert on the instance

On a ciu instance `scripts/certbot-deploy-hook.sh` rewrites
`stack/edge-proxy/conf.d/certs.yml`, which Traefik does not mount, so `static`
mode keeps serving the old certificate, and the rewritten synced file makes the
next `get.py update` refuse. Decided: on the instance the hook restarts the
Traefik container resolved as `verify.sh` does (about 3 s, documented) and never
writes synced files.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` H5

### TE-05 — hand-rolled bundle build

`scripts/build-artifact.sh`, `scripts/write-bundle-manifest.py` and most of
`scripts/stage-wheels.py` re-implement cmru's bundle builder, produce
non-reproducible tarballs and ship maintainer scripts into every instance.
Decided: `bundle.toml` plus `cmru handler bundle-build` (cmru KI-126); the
local builders go, the unlisted-wheel refusal stays, one GitHub fetcher, no
maintainer scripts in the bundle, and one equivalence build is the proof.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` H6

### TE-06 — three release-notes sources

`TLS_EDGE_RELEASE_NOTES` in `cmru.toml` carries version-specific prose,
released CHANGES sections were edited after their tags, and INSTALL.md keeps
release history. Decided: the headline is version-agnostic (install and verify
only); version prose lives only in pending notes before the tag; released
CHANGES sections are immutable from now on (existing post-tag edits stay);
INSTALL.md carries no release history; bundled versions come from the
manifest (cmru KI-197).

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` M2

### TE-08 — tests exercise the wrong renderer; coverage rule

Remaining: rebase the CLI tests on the CLI boundary and remove the
private-helper, source-grep and config-literal oracles in P-edge-04. P-edge-03
covers the standalone/ciu renderer oracles and D10's coverage-exclusion policy.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` M5, M6

### TE-09 — Calls media through Traefik

The Calls preset (`[tls_edge.calls]`, `mattermost-calls-*` entrypoints, port
8443, its CLI questions and agreement tests) puts an app-specific UDP media
proxy into the generic edge, while Jitsi JVB publishes its media directly.
Decided 2026-11-24: Mattermost publishes Calls media directly; the next major
release deletes the preset, and the schema 2 `migrate()` drops
`calls.enabled = false` silently and refuses `calls.enabled = true` with a
pointer to the Mattermost stack. The live cut-over is one window: the edge
drops its 8443 listeners and Mattermost publishes 8443 itself.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` M7

### TE-11 — docs restate facts

Ports, keys and defaults are restated across many documents. ARCHITECTURE has
two socket-proxy allowlists and claims a single source of truth, and this
backlog holds resolved and out-of-scope plans. Decided: facts live in
`ciu.defaults.toml.j2` and `tls-edge show`, with docs linking there;
ARCHITECTURE keeps rationale only. The backlog keeps open items only; closed
ones go one line each to `archive/2026-Q4.md`. The DOCKER-USER helper, multiple Traefik
instances and log aggregation are dropped; the middleware library folds into
FEAT-01, and mkcert moves to the mdt-cockpit backlog.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` L1

### TE-12 — small duplication

`requirements-test.txt` duplicates the pyproject `test` extra,
`scripts/release.sh` duplicates `cmru release`, and the override `ciu.toml.j2`
is parsed by the CLI as plain TOML but rendered by ciu as Jinja. Decided: one
test-dependency list (the pyproject extra), `cmru release` only, and the
override documented and enforced as plain TOML.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` L4

### TE-13 — rootless Docker ownership of vol-traefik-acme

Question: Under rootless Docker, the S6.5 helper's `chown 0:0` of
`vol-traefik-acme` maps to the user's uid; ciu's S6.3 check
(`ciu/src/ciu/engine.py:899-914`) may then reject the existing dir on the next
run. Unverified; check on a rootless host before 4.0.0.

Source: P-edge-04b fix review (2026-11-24).

### FEAT-01 — bearer-token authentication middleware for routed services

Provide an optional Traefik `forwardAuth` integration for services that need
`Authorization: Bearer <token>` validation instead of, or in addition to, the
existing per-route BasicAuth guard. The verifier must receive the original
request headers, return only an allow/deny decision plus bounded identity
headers, and never expose token values in Traefik access logs. The feature
needs a small reference verifier contract, secret delivery guidance, and an
acceptance test covering allowed, missing, invalid, and expired tokens.

PWMCP currently remains on TLS plus BasicAuth. Its `mcp-proxy`-backed routes
also have a native `X-API-Key` option, but that is a per-proxy mechanism rather
than estate-wide bearer authentication. This feature is the planned shared
edge solution; it is deliberately not enabled by PWMCP until tls-edge ships
the verifier integration.

### Middleware template library

Add a set of ready-to-enable middleware definitions in `conf.d/` that consumers
can reference by name in their router labels without writing their own YAML:
rate limiting (`rateLimit`), IP allowlisting (`ipAllowList`), and a
BasicAuth-protected dashboard endpoint.  Each middleware would ship disabled
and be enabled by a single line change in `conf.d/middlewares.yml`, which
Traefik hot-reloads.

### Traefik Prometheus metrics on an internal entrypoint

Expose Traefik's built-in Prometheus metrics endpoint on a loopback-bound or
`traefik_internal`-bound entrypoint so that a scraper on the same host can
collect request rates, error rates, and TLS handshake counts without publishing
a metrics port to `ingress_public`.

### `DOCKER-USER` iptables enforcement helper

Provide a script or compose pre-hook that installs `DOCKER-USER` iptables rules
to restrict east-west traffic on `ingress_public` at the kernel level.  This
would enforce isolation independently of application bind-address configuration,
closing the gap described in the east-west limitation above.  Rules would target
network CIDRs rather than container IPs to survive container restarts.

### Multiple Traefik instances per trust class

Define a second edge-proxy variant (`edge-proxy-internal` or similar) attached
to a separate ingress network with different routing policies — for example,
mTLS enforcement for internal API services while the primary edge handles
public-facing services.  Requires either additional host ports or an upstream
L4 load balancer to distribute by SNI.

### Centralised log aggregation

Document and provide configuration for shipping Traefik's JSON access log to a
log aggregation stack (Loki + Grafana as the lightweight default, with Elastic
Stack and Splunk as alternatives).  The access log already includes timestamp,
router name, service name, upstream address, HTTP status, and latency —
sufficient for a searchable, persistent audit trail.

### Instance packaging follow-ups (filed 2026-11-04)

- Drop the installer adapter's `git init` once ciu runs in a non-git standalone root (ciu CIU-163).
- Live migration of the hand-made deployment: after TE-04 and TE-09 (see TE-09 for the Calls cut-over).

### ciu dev-mode integration: generate dev certs via a pre-compose hook

Wire `scripts/dev-certs.sh` into a ciu `pre_compose` hook so that running
`ciu` in dev mode automatically generates the self-signed CA and certificate
into the certs volume before Traefik starts.  Currently, dev cert generation
is a manual step separate from the ciu deploy flow.

### mkcert in the base dev image (tooling; separate repo)

Consider adding `mkcert` to the `modern-debian-tools-python-debug` base image
so that devcontainer-based dev mode gets a trusted local CA certificate
installed into the system and browser trust stores automatically, eliminating
the manual CA import step.  This is a change to the tooling image repository,
not to tls-edge itself.
