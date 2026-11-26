# Known Issues & Roadmap

---

## Known limitations

### acme-data volume loss triggers certificate re-issuance (`store = "volume"`)

This limitation applies only with `[tls_edge.acme] store = "volume"`. Running
`docker compose down -v`, `ciu --reset`, or `ciu clean` may destroy the
instance-owned `acme-data` named volume, which contains `acme.json` — Traefik's
ACME account key and all issued certificates. On the next start, Traefik
re-issues certificates from scratch. Let's Encrypt enforces a rate limit of
approximately five duplicate certificates per registered domain per week;
hitting this limit blocks new issuance for up to a week.

Workaround: use the hostdir storage created for new root-run instances, or
avoid `-v` on the tls-edge instance. Before any destructive operation on a
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
`env_file` injection in a generated standalone trial.

---

### TE-13 — rootless Docker ownership of `vol-traefik-acme`

Under rootless Docker the S6.5 helper's `chown 0:0` of `ciu-stack/vol-traefik-acme`
maps to the user's uid, so the directory a rootless run creates is not the
`0:0 0700` directory a rootful run expects. CIU's S6.3 check
(`ciu/src/ciu/engine.py:899-914`) compares the existing dir against the
declared owner and may then refuse it on the next run (the ciu S6.3 refusal of
the existing directory).

Known issue, not a verified defect: no rootless host was available, and 4.0.0
was not checked for this before release. The main controller waived the check
on 2026-11-26 (the P-edge-11b 4.0.0 acceptance did not test rootless Docker),
and no host is known to have failed this way. Workaround if it happens: remove
the directory and let the next run create it, or run the instance as the user
that owns it.

Source: P-edge-04b fix review (2026-11-24); moved here from the Roadmap after
4.0.0 (P-edge-12).

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

---

### Single Traefik instance is a single point of failure

The tls-edge instance runs one Traefik container.  If it crashes or is
restarting, all public services on the host are unreachable for the duration.

Mitigation: `restart: unless-stopped` ensures Traefik is automatically
restarted after transient crashes; the typical recovery time is under five
seconds.  For higher availability, Traefik HA requires Docker Swarm or
Kubernetes mode, both of which are out of scope for a single-host compose
deployment.

---

### py314 bundle: gates only

The release publishes one bundle per interpreter. The `py314` bundle of 4.0.0
passed the release gates but was never installed on a host: no host in the
estate runs python 3.14, so `get.py install --variant py314` on a real
installation is unverified. Use `--variant py313` (see INSTALL section 3) until
a py314 host exercises it.

---

## Roadmap

### TE-16 — a volume-store instance still creates an unused `vol-traefik-acme` (planned)

A volume-store instance (an adopted external ACME volume, or the default
volume) still makes ciu create `ciu-stack/vol-traefik-acme` through the S6.5
root helper, although nothing mounts it: the compose template mounts the
external volume instead. On a non-root host this means the root helper and
`CGROUP_PARENT_DEV_INTERACTIVE` are required for a directory that is never used
(P-edge-11b report section 5, P1). Fix: have the stack declare no hostdir volume
when `store = "volume"`, so a non-root run needs no root helper; then drop the
known-limitation caveat about it and the INSTALL section 9 note.

Source: P-edge-11b 4.0.0 acceptance report section 5 (2026-11-26).

### TE-08 — tests exercise the wrong renderer; coverage rule

Remaining: rebase the CLI tests on the CLI boundary and remove the
private-helper, source-grep and config-literal oracles in P-edge-04. P-edge-03
covers the standalone/ciu renderer oracles and D10's coverage-exclusion policy.

Source: `.controller-state/design-review-2026-11-24/tls-edge.md` M5, M6

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

Remaining middleware work, from the same review that created this entry:
ready-to-enable definitions in `conf.d/` that consumers reference by name in
their router labels without writing their own YAML — rate limiting
(`rateLimit`), IP allowlisting (`ipAllowList`), and a BasicAuth-protected
dashboard endpoint. They ship disabled and are enabled through the schema
rather than by editing a synced file (an edited synced file stops `update`).

### Traefik Prometheus metrics on an internal entrypoint

Expose Traefik's built-in Prometheus metrics endpoint on a loopback-bound or
`traefik_internal`-bound entrypoint so that a scraper on the same host can
collect request rates, error rates, and TLS handshake counts without publishing
a metrics port to `ingress_public`.

### Instance packaging follow-ups (filed 2026-11-04)

- Drop the installer adapter's `git init` once ciu runs in a non-git standalone root (ciu CIU-163).
- Live migration of the hand-made deployment: after the Calls cut-over described in
  `docs/INSTALL.md` §8.

### ciu dev-mode integration: generate dev certs via a pre-compose hook

Wire `scripts/dev-certs.sh` into a ciu `pre_compose` hook so that running
`ciu` in dev mode automatically generates the self-signed CA and certificate
into the certs volume before Traefik starts.  Currently, dev cert generation
is a manual step separate from the ciu deploy flow.
