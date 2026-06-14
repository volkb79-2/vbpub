# Known Issues & Roadmap

---

## Known limitations

### acme-data volume loss triggers certificate re-issuance

Running `docker compose down -v` or `ciu --reset` destroys the `acme-data`
named volume, which contains `acme.json` — Traefik's ACME account key and all
issued certificates.  On the next start, Traefik re-issues certificates from
scratch.  Let's Encrypt enforces a rate limit of approximately five duplicate
certificates per registered domain per week; hitting this limit blocks new
issuance for up to a week.

Workaround: avoid `-v` on the edge-proxy stack.  Before any destructive
operation, back up the volume:
```bash
docker run --rm -v acme-data:/data -v "$(pwd)":/backup alpine \
  tar czf /backup/acme-data.tar.gz -C /data .
```
ciu users who run into this regularly can migrate `acme-data` to a ciu hostdir
(spec S6.7a), which survives `ciu --reset` and can be backed up with the
host filesystem.

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

### Port-based routing is single-slot per entrypoint

The `websecure-alt` entrypoint (port 8443) can route one `Host()` + entrypoint
combination per service.  A second service that needs its own external port
cannot share `websecure-alt` with a different `Host()` rule and must get a new
entrypoint — which requires adding it to `traefik.yml` (static config) and to
`docker-compose.yml` ports, meaning a re-render and a container restart.

Workaround: use `PathPrefix` sharing on `websecure-alt` to fit two services
behind the same entrypoint, provided paths do not conflict.

Roadmap: pre-provision additional `websecure-alt2` / `websecure-alt3`
entrypoints so that up to three port-routed services can be deployed without a
re-render.

---

### Port 80 off means no HTTP→HTTPS redirect

By default, port 80 is not bound.  Users who type `http://` URLs get a
connection refused rather than an automatic redirect to HTTPS.

Workaround: enable port 80 by setting `tls_edge.ports.expose_http = true` in
your override and re-rendering (`scripts/render.sh`).  This is required anyway
for `acme-http` (HTTP-01 challenge) mode.  Once a browser has visited the site
over HTTPS and received the HSTS header, subsequent HTTP visits are upgraded
client-side without the redirect entrypoint.

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

### Additional pre-provisioned alt entrypoints

Pre-define `websecure-alt2` (port 8444) and `websecure-alt3` (port 8445) in
the default rendered configuration so that up to three port-routed services can
be onboarded without requiring a re-render and container restart to add new
static entrypoints.

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
