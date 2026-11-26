# TLS-as-a-Service: Architecture & Design Decisions

This document traces the full design process for the shared TLS termination
layer, recording every decision made, the alternatives that were considered,
the implications and requirements of each choice, and options that are deferred
for future implementation.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Docker Networking Fundamentals & Limitations](#2-docker-networking-fundamentals--limitations)
3. [Network Design](#3-network-design)
4. [TLS Strategy](#4-tls-strategy)
5. [Edge Proxy Selection](#5-edge-proxy-selection)
6. [Docker Socket Security](#6-docker-socket-security)
7. [Consumer Authorization](#7-consumer-authorization)
8. [Routing Strategy](#8-routing-strategy)
9. [Consumer Patterns](#9-consumer-patterns)
10. [Ops Endpoint Isolation](#10-ops-endpoint-isolation)
11. [Configuration Model & Rendering](#11-configuration-model--rendering)
12. [Container Hardening](#12-container-hardening)
13. [Certificate Renewal](#13-certificate-renewal)
14. [Port Collision Behaviour](#14-port-collision-behaviour)
15. [Verification & Audit](#15-verification--audit)
16. [Future Options](#16-future-options)
17. [Known Limitations & Tradeoffs](#17-known-limitations--tradeoffs)

---

## 1. Problem Statement

### Starting point

- A physical host runs multiple Docker Compose stacks.
- A host-level `certbot` manages a TLS certificate for a public FQDN.
- Some containers need TLS for public services.
- Historically: cert paths were manually volume-mounted into every container
  that needed them, and an Nginx instance ran on the host as a reverse proxy
  for services that did not offer TLS natively.

### Problems with the starting point

| Problem | Impact |
|---|---|
| Cert mounts duplicated across many compose files | Operational drift; missing mounts cause silent HTTP-only exposure |
| Host-native Nginx as reverse proxy | Higher blast radius on compromise; harder to version-control and isolate |
| No consistent TLS policy | Different stacks may use different cipher suites or cert handling |
| No audit trail for which service is publicly reachable | Operational opacity |

### Goals

1. Provide TLS termination as a shared host service that containers can use
   without requiring access to the host filesystem or proxy configuration.
2. Keep each compose stack's internal network segregated.
3. Support all three consumer patterns: public-only; public + private backend;
   public + isolated ops endpoint.
4. Support both subdomain-based and port-based routing discriminators.
5. Centralize cert handling and renewal.
6. Maintain an audit trail of what is routed where.

---

## 2. Docker Networking Fundamentals & Limitations

Understanding these constraints shaped every architectural decision.

### Port exposure is host-wide, not per-network

Docker cannot restrict a listening port to a specific Docker network.  If a
container binds `0.0.0.0:8080` and is attached to two networks, that port is
reachable from both networks.  Per-port-per-network ACL does not exist at the
Docker level.

**Implication**: east-west isolation between containers on the same Docker
network must be enforced at the application layer (bind address), the host
firewall (rules installed by the host's own setup), or by not co-locating
services on the same network at all.

### `ports:` vs `expose:`

- `ports:` publishes a container port to the Docker host's network interface.
  It is reachable from outside the host.
- `expose:` documents that a port is available but does NOT publish it.
  It is only reachable from containers on the same Docker network.

**Implication**: the edge proxy uses `ports:` for public HTTP/HTTPS. HTTP
consumer services use `expose:` so Traefik can route to them without publishing
their own host ports. An application that serves raw TCP/UDP media publishes
that media port pair directly on the host.

### `127.0.0.1` is per-network-namespace, not per-host

Each container has its own network namespace.  `127.0.0.1` inside a container
refers to that container's loopback interface, not the Docker host's loopback
and not the physical host's loopback.  No other container can reach that
container's `127.0.0.1` address—unless it shares the same network namespace.

**Implication**: binding an ops listener to `127.0.0.1` inside a container is
a genuine isolation mechanism between Docker containers.  It is *not* reachable
via the host's loopback—but it also does *not* require publishing a host port.

### `internal: true` networks

Marking a Docker network `internal: true` blocks outbound connectivity from
that network to external networks (internet).  It does NOT restrict east-west
traffic between containers on the same internal network.

**Implication**: `internal: true` prevents accidental internet egress from
private backend containers, but it does not replace per-port isolation between
containers that share that network.

### `network_mode: service:<name>`

A container with this setting joins the exact network namespace of another
container.  They share the same IP addresses, loopback interface, and all
network interfaces.  This is the mechanism that enables the sidecar ops pattern.

Constraints:
- The sidecar cannot have its own `networks:` block (mutually exclusive).
- Port conflicts can occur if both containers try to bind the same port.
- The sidecar's lifecycle is independent; Docker does not enforce restart order
  beyond `depends_on`.

### `host` network mode

Using `network_mode: host` gives the container the host's full network stack.
It is the highest-performance option and can be useful for services that need
to bind to specific host interfaces.  It is also the most dangerous from an
isolation standpoint—the container can reach anything the host can reach and
can bind any host port.  **Not recommended** for services in this architecture.

---

## 3. Network Design

### Decision: separate ingress_public + per-stack private networks

```
ingress_public (shared, created by the tls-edge stack)
    │
    ├── tls-edge-<id>-traefik
    ├── stack-A public service
    └── stack-B public service

stack-A_private (internal: true, owned by stack A)
    ├── stack-A public service   ← also on ingress_public
    ├── stack-A database
    └── stack-A cache

stack-B_private (internal: true, owned by stack B)
    ├── stack-B public service   ← also on ingress_public
    └── stack-B worker
```

Why this shape:
- One shared ingress point keeps TLS policy centralized.
- Each stack's private network is invisible to other stacks.
- Backend services that do not need public access never join `ingress_public`.

### Alternatives considered

**One ingress network per trust class**
Multiple Traefik instances each attached to a different ingress network, each
with different routing policies, all behind a common L4 entry point.

- Pro: stronger blast-radius isolation between trust classes.
- Con: requires multiple Traefik instances; with a single public IP and port
  443, you need an L4 load balancer in front to fan out to the right Traefik
  instance, or you use different external ports per trust class.
- **Decision**: deferred.  Start with a single ingress trust zone; move to
  multi-Traefik when the need for different trust classes is proven.

**Host-native Nginx as edge proxy**
Keep Nginx on the host rather than running it in a container.

- Pro: avoids Docker socket exposure; no container runtime overhead.
- Con: harder to version-control; host-level compromise risk; no automatic
  service discovery from Docker labels; manual config updates required on every
  service change.
- **Decision**: rejected.  A hardened container is preferable.

**OPNsense or physical appliance as external firewall**
Put a separate VM or appliance (OPNsense, pfSense, HAProxy) in front of the
Docker host.

- Pro: highest isolation; professional firewall policy.
- Con: requires additional infrastructure; OPNsense is FreeBSD-based and is
  not realistically run as a Docker Compose service.
- **Decision**: noted as the ideal production architecture for high-assurance
  environments.  Out of scope for this implementation.
- **Future option**: see [Section 16](#16-future-options).

### traefik_internal network

A separate internal bridge used only for the communication between `traefik`
and `dockerproxy`.  Marked `internal: true`.  No other container joins it.

This ensures that even if the Docker socket proxy were compromised, it has no
route to the internet or to any application network.

---

## 4. TLS Strategy

### Decision: ACME-first, five configurable modes

TLS mode is selected via `[tls_edge.tls] mode` in `ciu-stack/ciu.defaults.toml.j2`
(or a gitignored `ciu.toml.j2` override); that file carries every mode and the
default, and `tls-edge show` reports the mode an instance uses.  The rest of
this section is why each mode exists, not a second list of its settings.

#### acme-tls (default)

Traefik's built-in Let's Encrypt via TLS-ALPN-01.  The entire challenge
exchange happens on port 443; no port 80 is needed.  Traefik automatically
issues a certificate for every `Host()` name declared in a consumer router,
provided that DNS name resolves to this host. Certificates are stored in the
`acme-data` named volume at `/var/lib/traefik/acme.json` (`0600`, created by
Traefik). The volume is mounted outside `/etc/traefik`, because CIU mounts the
parent directories of configured Traefik config files read-only. Docker creates
the mountpoint when needed, so the container root filesystem can stay read-only.

Renewal is automatic and zero-downtime.

**WARNING**: `docker compose down -v` (or `ciu --reset`) destroys the
`acme-data` volume, forcing re-issuance of all certificates on next start.
Let's Encrypt rate limits apply (approximately 5 duplicate certificates per
week per domain).  Use the staging CA (`acme-staging-v02.api.letsencrypt.org`)
when experimenting.

#### acme-http

HTTP-01 challenge.  Requires `[tls_edge.ports] expose_http = true` in the
configuration, which adds the `web` entrypoint on port 80 with a permanent
HTTP→HTTPS redirect.  Otherwise identical to `acme-tls`.

#### acme-dns

DNS-01 challenge via a lego provider API (Cloudflare, Hetzner, Route 53,
deSEC, RFC 2136, and others).  Traefik creates and removes the
`_acme-challenge` TXT record itself via provider API credentials.

When `wildcard_main` is set (e.g. `"my.domain"`), a single wildcard
certificate is requested for `my.domain` + `*.my.domain` on both entrypoints.

**Credential delivery**:
- Trial standalone mode: plain environment variables in the generated,
  gitignored `edge-proxy/.env` (loaded via `env_file`; `chmod 600`).
- ciu mode: Docker secrets referenced by `*_FILE` variable names declared in
  `[tls_edge.secrets]`.

**Traefik has NO interactive mode** for DNS-01: there is no "paste this TXT
record into your DNS zone" prompt.  If the primary DNS host has no API, the
workaround is CNAME delegation of `_acme-challenge` to a zone that does
(e.g. acme-dns server, deSEC).  For purely manual wildcard renewal without API
access, use `mode = "static"` with `certbot --manual`.

#### static

Certificates provided as files on the host (host certbot, corporate PKI, or a
manual certbot wildcard).

**Critical mount rule**: both parent directories must be mounted—
`cert_base/live → /certs/live` and `cert_base/archive → /certs/archive`.
Certbot's `live/<domain>/*.pem` files are relative symlinks pointing to
`../../archive/<domain>/fileN.pem`.  Mounting only `live/<domain>` directly
breaks symlink resolution inside the container and Traefik silently falls back
to its self-signed default certificate.

For a manual wildcard: `certbot certonly --manual --preferred-challenges dns`
works, but every renewal (approximately every 60 days) requires a fresh manual
TXT record entry.  This is supported but discouraged for automated workflows.

#### dev

Self-signed certificates generated by `scripts/dev-certs.sh` (openssl local
CA or mkcert) in the same `live/<domain>/` layout as static mode.  For
devcontainer / Docker-outside-of-Docker (DooD) environments, a named volume
(`certs-dev`) carries the certificate material into the container.

### Port 80 can be turned off

Port 80 carries a permanent HTTP→HTTPS redirect and ACME HTTP-01 challenges
(`acme-http` mode requires `expose_http = true`).  `tls-edge configure` can turn
it off for a host that must not serve it; the edge is then TLS-only.  Consumers
should still publish `https://` URLs.  Whether a given instance serves it is
reported by `tls-edge show`, not restated here.

### Wildcard DNS record vs wildcard certificate vs per-name ACME

These are three independent concepts:

- A **wildcard DNS A/CNAME record** (`*.my.domain → host IP`) causes all
  subdomains to resolve to this host automatically.  It is a DNS-layer
  convenience for subdomain self-service and is entirely independent of TLS.
- A **wildcard TLS certificate** (`*.my.domain`) covers all subdomains with a
  single cert.  It requires DNS-01 (ACME or manual).
- **Per-name ACME** (acme-tls or acme-http) issues an individual certificate
  for each `Host()` declared in a consumer router.  Combined with a wildcard
  DNS record, this gives fully self-service subdomain onboarding: any consumer
  adds their subdomain label, DNS resolves via the wildcard record, and Traefik
  obtains the certificate automatically.  No wildcard *certificate* needed.

### Alternatives considered (superseded)

**Phase 1 (original): host-managed certbot with file mounts**
Certbot ran on the physical host and wrote certificate material to
`/etc/letsencrypt/live/<domain>/`.  The edge proxy mounted both the `live/`
and `archive/` subtrees (both required; see static mode mount rule above).
Mounts were `:ro` in the container.

- Superseded by: ACME-first design with five configurable modes.  Static mode
  preserves this approach for environments that require it.

---

## 5. Edge Proxy Selection

### Decision: Traefik as the default edge proxy

| Criterion | Traefik | Nginx |
|---|---|---|
| Self-service consumer onboarding | Label-driven; no central config change | Requires central config file edit and reload |
| Dynamic service discovery | Native via Docker provider | Requires additional tooling or manual reload |
| TLS policy centralization | Supported via file provider | Supported via static config |
| Configuration drift risk | Low (labels travel with the service) | Higher (central config may get out of sync) |
| Attack surface | Moderate (Docker socket required) | Lower (no Docker socket needed) |
| Community familiarity | Growing | Very high |
| Operational complexity | Low for consumers; moderate for operator | High for consumers (need proxy config access) |

### Why Nginx was rejected as the primary self-service option

For consumers to register a new service with Nginx, someone must:
1. Edit the Nginx config files (requires access outside the consumer's stack).
2. Test the config (`nginx -t`).
3. Reload Nginx (`nginx -s reload` or `docker exec`).

This creates a dependency between the consumer team and the operator.  It also
means that the config for a service lives in a different repository from the
service itself, increasing drift risk.

**Nginx is still a valid choice** for environments where:
- All routing changes go through a central approval workflow (GitOps).
- Routes are stable and infrequent.
- Docker socket exposure is not acceptable even through a proxy.

### Docker socket access and its risk

Traefik needs to watch Docker events to discover containers.  This requires
access to the Docker socket, which in turn grants significant host control.

**Mitigation**: Docker socket proxy (`tecnativa/docker-socket-proxy:v0.5.0`).

The socket proxy is a separate container that accepts a restricted subset of
Docker API calls and proxies them to the real socket.  The allowlist it enforces,
and why each endpoint is there, is [§6](#6-docker-socket-security); the values the
instance actually ships are the `dockerproxy` service's `environment` block in
`ciu-stack/ciu.compose.yml.j2`.

Traefik talks to `tcp://dockerproxy:2375` and never touches
`/var/run/docker.sock` directly.  The socket proxy sits on `traefik_internal`
(no internet access).

---

## 6. Docker Socket Security

### Why the raw Docker socket is not mounted into Traefik

The Docker daemon socket (`/var/run/docker.sock`) is effectively a **root
backdoor to the host**.  Any process that holds a file descriptor to it can:

- Start, stop, pause, or delete any container on the host.
- Execute arbitrary commands inside running containers (`docker exec`).
- Pull and run new images.
- Mount host directories into new containers.
- Escape to the host filesystem entirely via a privileged container.

No additional Linux capabilities or kernel exploits are needed: the socket API
alone is sufficient for a complete host takeover.

Traefik is internet-facing by design.  It terminates TLS for all public
services and processes every inbound HTTP(S) connection.  An unpatched zero-day
in Traefik, a server-side request forgery (SSRF) in a proxied application, or a
misconfigured route that exposes Traefik's internal API would all give an
attacker a foothold in the proxy process.  If that process holds the Docker
socket, the game is over.

### Mitigation: `tecnativa/docker-socket-proxy`

The socket proxy is a minimal HAProxy container that interposes between
Traefik and the Docker daemon.  Traefik speaks to `tcp://dockerproxy:2375`
(inside `traefik_internal`) and never holds a file descriptor to
`/var/run/docker.sock`.  Only the socket proxy container mounts the socket.

The proxy enforces an endpoint allowlist.  What the shipped stack sets is the
`environment` block of the `dockerproxy` service in
`ciu-stack/ciu.compose.yml.j2`, reproduced here so the list is readable in one
place; that template is authoritative and this block must agree with it:

```
CONTAINERS: 1   # read container metadata
NETWORKS:   1   # read network info
EVENTS:     1   # read the event stream the Docker provider watches
SERVICES:    1   # read swarm service metadata (no-op on standalone Docker)
TASKS:       1   # read swarm task metadata (no-op on standalone Docker)
POST:        0   # DENY all mutating calls
```

No other endpoint is enabled, and **all write operations are denied globally by
that `POST: 0`** before the request ever reaches the Docker daemon.  `EVENTS` is
what makes the Docker provider's `watch: true` (`ciu-stack/traefik.yml.j2:116`)
do anything: the socket proxy is the only holder of `/var/run/docker.sock`, so
without it every container started after Traefik is not routed.
`docker-socket-proxy:v0.5.0` already defaults `EVENTS` to `1`; the instance sets
it explicitly so a change of that default cannot silently stop new containers
from being routed.  Verified on r1002 on 2026-11-26 (tls-edge 4.0.0): a labelled
container was routed about 1 s after start and unrouted about 1 s after `docker
stop`, with Traefik restarts still 0 (TE-14, closed).

### Blast radius analysis

| Scenario | Without proxy | With proxy |
|---|---|---|
| Traefik crash / restart | Normal recovery | Normal recovery |
| Traefik process compromise | Full host takeover via socket | Read-only Docker metadata |
| Socket proxy compromise | n/a | Read-only Docker metadata (proxy has no other capabilities) |
| Both compromised | Full host takeover | Full host takeover |

The common case — a compromised Traefik process — changes from "total loss"
to "attacker can enumerate running containers and read their labels."  No
lateral movement to other containers, no persistence mechanism, no host escape.

### Additional hardening applied

- `security_opt: no-new-privileges:true` on the socket proxy container.
- Socket proxy is attached to `traefik_internal` only — it is not reachable
  from `ingress_public` or any consumer network.
- Socket proxy image is version-pinned (`tecnativa/docker-socket-proxy:v0.5.0`)
  for reproducibility.

See [Section 5](#5-edge-proxy-selection) for the edge proxy selection rationale.

**Future option**: use Docker Swarm secrets or a dedicated control-plane
service to further restrict which labels Traefik acts on, enforcing that only
approved label key prefixes are honoured.

---

## 7. Consumer Authorization

### Decision: label + ingress_public membership as authorization gate

A container is routed by Traefik if and only if:
1. It carries the label `traefik.enable=true`.
2. It is attached to the `ingress_public` network.

Both conditions must be true.  Satisfying only one is not sufficient.

`traefik.enable=true` without `ingress_public` membership: Traefik sees the
container but cannot route to it (no route via the configured network).

`ingress_public` membership without `traefik.enable=true`: Traefik ignores
the container entirely (`exposedByDefault: false`).

### Why a shared secret was not used

A token or shared-secret-based self-registration mechanism was considered.
This would require a custom control-plane that validates tokens before allowing
routing entries to be created.

- Pro: explicit cryptographic authorization per service registration.
- Con: significant added complexity; labels travel in plaintext in Docker
  metadata anyway; the shared ingress network membership already provides a
  physical access control (a container cannot join `ingress_public` without
  host-level compose access).
- **Decision**: the network membership gate is sufficient for the threat model
  of a single-operator host.  Custom token auth is deferred.

**Future option**: use Traefik middleware (BasicAuth, ForwardAuth) to add
authentication on individual routers.  Use Traefik's label constraints feature
(`providers.docker.constraints`) to filter which containers Traefik will route
based on label key/value patterns.

---

## 8. Routing Strategy

### The core problem: one domain, one port 443, many services

HTTP/1.1 and TLS both use the `Host` header and SNI (Server Name Indication)
to distinguish virtual hosts.  This means a single IP:port combination can
serve multiple services—as long as each has a unique hostname.

If two services share the same hostname AND the same path, routing is
ambiguous.  At least one discriminator is required.

### Discriminator options evaluated

| Option | Requirement | Recommendation |
|---|---|---|
| Different subdomains (`svc1.domain`, `svc2.domain`) | DNS that resolves each name to this host (wildcard record covers all at once); ACME modes issue certs automatically | **Preferred** |
| Different external ports | Only one service per port; limited number of ports; every extra port needs firewall and provider rules | **Rejected** (no alternate HTTPS port) |
| Different path prefixes (`/svcA`, `/svcB`) | Services must not conflict on `/api` etc.; requires path rewriting sometimes | Possible addition |
| Custom routing header | Non-standard; clients must set the header | Not viable for general use |
| Different host IPs | Multiple public IPs on the host | Infrastructure option |

### Decision: host-name routing only

One HTTP/TLS Traefik entrypoint carries all HTTPS: `websecure`, on the single
public HTTPS port.  Every service gets its own host name; there is no alternate
HTTPS port.  HTTP/3 optionally adds UDP on that same public port.  The shipped
port set is in `ciu-stack/ciu.defaults.toml.j2` and what an instance publishes is
in `tls-edge show`.  Raw media stays outside Traefik: the application publishes
its own TCP/UDP ports directly.

Path-based routing can be layered on top by adding `&& PathPrefix(...)` to the
router rule.

### Why no plain HTTP service port

All external-facing traffic is TLS (port 80 only redirects), for the same
cipher, certificate, and transport security guarantees.

---

## 9. Consumer Patterns

### Pattern A: Public-only service

**When to use**: a container that exposes a single public API or web UI with no
private dependencies in the same stack.

**Network attachment**: `ingress_public` (required), optionally `private` if
the container has local-only peer services.

**Labels required**: four — `traefik.enable`, router rule, entrypoint, service
port.  TLS is enabled at the entrypoint level; `tls=true` and `tls.certresolver`
labels are not needed (harmless if present).

**What Traefik does**: matches incoming requests by hostname (and optionally
path), forwards to the container's `expose`d port over `ingress_public`.

**Router/service naming**: names must be unique across the whole host, not just
within a stack.  Convention: `<stack>-<service>` (e.g. `pattern-a-app`).

### Pattern B: Public service with private stack backends

**When to use**: a service that needs to be publicly reachable but also talks
to a database, cache, or worker that must never be exposed publicly.

**Network attachment**:
- Public service: `ingress_public` + `private`.
- Backend services: `private` only.  No labels.  No `ports:`.

**Key rule**: backends must not join `ingress_public`.  East-west visibility
between containers on `ingress_public` means that if a database joined the
shared ingress network, all other containers on that network could attempt
connections to it.

### Pattern C: Public API with isolated ops endpoint

**When to use**: a service that exposes health, metrics, or admin APIs that
must be reachable by an ops agent but not by any peer container or public
traffic.

**Mechanism**: the application binds its ops listener to `127.0.0.1:<port>`
(loopback inside the container's network namespace).  A sidecar using
`network_mode: service:app` shares that namespace and can reach the ops
endpoint at `127.0.0.1:<port>`.

**Critical requirement**: the application must be configurable to bind the ops
listener to `127.0.0.1` instead of `0.0.0.0`.  This is an **application-level
configuration**, not a Docker feature.  If the application binds to `0.0.0.0`,
any container on the same Docker network can reach the ops port.

**Traefik label note**: `loadbalancer.server.port` must be set to the public
API port only.  If omitted and the container exposes multiple ports, Traefik's
selection is indeterminate.

---

## 10. Ops Endpoint Isolation

### Options evaluated

| Approach | Isolation strength | Requirements |
|---|---|---|
| App binds ops to `127.0.0.1` + sidecar namespace share | Strong: loopback not reachable by peers | App must support configurable bind address |
| Dedicated ops Docker network | Weak: app on both nets; ops port reachable from ops net AND other nets if bound to 0.0.0.0 | App must still bind correctly; adds network complexity |
| Separate container for ops endpoint | Strong: ops and API are separate processes | Requires app refactoring |
| Host firewall rules | Strong: enforced at kernel level | Requires iptables knowledge; rules must survive Docker restarts |
| mTLS on ops endpoint | Strong: cryptographic enforcement | Significant PKI complexity |

### Decision: loopback binding + sidecar namespace sharing

Rationale:
- Does not require cross-stack dependencies.
- Does not require per-container host firewall rules.
- Works entirely within the compose file of the consuming stack.
- Weakness: depends on the application being correctly configured.

### Why a dedicated ops network was rejected

When an application joins both `private` and `ops_net`, any listener bound
to `0.0.0.0` is reachable from both networks.  The dedicated network provides
no isolation guarantee without the same application-layer binding restriction.
It adds complexity without adding isolation, so it was rejected.

**Out of scope**: host firewall rules can enforce that only specific source
container IPs reach specific destination ports, independent of application
binding.  That is host setup, not part of the edge: the per-host rules, their
survival across Docker restarts, and the CIDR bookkeeping belong to the host's
own configuration.

---

## 11. Configuration Model & Rendering

### Configuration model: Jinja2 templates in `ciu-stack/`

All configuration is generated from Jinja2 templates.  The configuration
schema and every shipped default live in one file:

```
ciu-stack/
  ciu.defaults.toml.j2      ← schema and default values for all options (TOML)
  ciu.compose.yml.j2        ← Compose template
  traefik.yml.j2            ← Traefik static config template
  conf.d/
    options.yml             ← TLS options baseline (not templated; hot-reloaded)
    middlewares.yml         ← Secure headers middleware (not templated; hot-reloaded)
    certs.yml.j2            ← Static cert store (templated; hot-reloaded after render)
```

`ciu-stack/` is also a complete ciu v2 stack package and can be deployed
directly by ciu without any extra tooling.

**Where the facts live.** Ports, keys and defaults are not restated here: read
`ciu-stack/ciu.defaults.toml.j2` for the schema, and `tls-edge show --json` for
the values a configured instance actually uses.  This document explains why the
choices were made, not what the current values are.

### Supported and trial render paths

**Supported (ciu)**:
Renders the templates natively as part of its deployment pipeline.  Uses
configfile overlay mounts (`[tls_edge.traefik.configfile.*]`) to inject
`traefik.yml` and the `conf.d/` files into the container at the right paths.
The Traefik service key in the compose template must remain `traefik` (ciu spec
S5.3); renaming it silently detaches the configfile mounts.

**Trial (standalone)** (`tools/render.sh`, maintainer-only and not shipped in an instance):
Reads `ciu.defaults.toml.j2` and optionally a gitignored `ciu-stack/ciu.toml.j2`
override, then writes a generated tree to the `--out` directory. This output is
never committed and exists only for inspection or a disposable trial.

Do not edit files in the generated `--out` directory directly; changes will
be overwritten on the next render.

### Overriding defaults

Create a gitignored `ciu-stack/ciu.toml.j2` next to the defaults file using
the same TOML schema and include only the keys to override.  Do not edit
`ciu.defaults.toml.j2` directly.

### Static vs dynamic configuration precedence

Traefik's static-config sources are mutually exclusive: config file beats CLI
flags beats environment variables — the first source found wins.  Because a
`traefik.yml` config file is always mounted, **environment variables cannot
override static config values** such as log level.  Changes to log level,
entrypoints, or ACME settings require a re-render and a container restart.

Dynamic configuration (`conf.d/`) is loaded by Traefik's file provider via
inotify and **hot-reloads** without restart.  This covers TLS options,
middlewares, and the static certificate store.

---

## 12. Container Hardening

### Traefik hardening decisions

| Directive | Why |
|---|---|
| `read_only: true` | Container root filesystem is immutable; attacker cannot persist files |
| `tmpfs: /tmp` | Traefik needs a writable temp path; keep it in memory only |
| `cap_drop: ALL` | Remove all Linux capabilities from process baseline |
| `cap_add: NET_BIND_SERVICE` | Re-add only the capability needed to bind ports < 1024 |
| `security_opt: no-new-privileges:true` | Block privilege escalation via setuid/setgid binaries |
| Volume mounts `:ro` | Config files and static certs cannot be modified from within the container |
| No explicit bind IP by default | Docker binds dual-stack IPv4+IPv6 when the host supports it; set `bind_ip = "0.0.0.0"` to force IPv4-only |
| `depends_on: condition: service_healthy` | Traefik starts only after dockerproxy passes its healthcheck |
| Healthcheck: `traefik healthcheck --ping` | Probes the loopback-only `ping` entrypoint at `127.0.0.1:8082` |
| Logging: json-file, 10 m × 3 | Bounded log storage; JSON format for structured querying |
| Image pinned: `traefik:v3.7` | Reproducible deployments; no surprise upgrades |
| Dashboard disabled | `api.insecure: false`, `dashboard: false`; enable only for dev on loopback |
| `secure-headers` middleware at entrypoint level | HSTS / nosniff / referrer-policy applied to all consumers without labels |

### edge-dockerproxy hardening decisions

| Directive | Why |
|---|---|
| `POST: 0`, and only the read-only endpoints of §6 | Prevents all mutating Docker API calls |
| `security_opt: no-new-privileges:true` | Privilege escalation prevention |
| `read_only: true` with `tmpfs: /run /tmp` | Immutable filesystem; writable scratch in memory |
| On `traefik_internal` only | No route to internet or application networks |
| `volumes: /var/run/docker.sock:ro` | Socket mounted read-only to the proxy container |
| Healthcheck: `wget /version` | Confirms the proxy is accepting API calls before Traefik starts |
| Logging: json-file, 10 m × 3 | Bounded log storage |
| Image pinned: `tecnativa/docker-socket-proxy:v0.5.0` | Reproducible deployments |

### Secure-headers middleware

`conf.d/middlewares.yml` defines the `secure-headers` middleware and attaches
it at the entrypoint level in `traefik.yml`, so **every consumer router inherits
HSTS, X-Content-Type-Options, and Referrer-Policy** without needing any labels.
Consumers can add their own per-router middlewares additively via labels.

### TLS options (`conf.d/options.yml`)

The default TLS option set enforces TLS 1.2 minimum, an explicit cipher
allowlist for 1.2, and `sniStrict: true`.  `sniStrict` rejects TLS connections
whose SNI matches no known certificate, which also rejects clients that send
no SNI at all (bare-IP probes, some uptime checkers, `openssl s_client` without
`-servername`).  Set `sniStrict: false` if such clients must be served the
default certificate.

These files are dynamic config and hot-reload without a container restart.

### Consumer service hardening baseline (recommended)

These are not enforced by Traefik but should be applied to all consumer services:

```yaml
read_only: true
security_opt:
  - no-new-privileges:true
cap_drop:
  - ALL
```

---

## 13. Certificate Renewal

Renewal behaviour depends on the configured TLS mode.

### ACME modes (acme-tls, acme-http, acme-dns)

Traefik renews certificates automatically before expiry.  Renewal is
zero-downtime and requires no operator action.  The only operational risk is
loss of the `acme-data` volume (see Known Limitations).

### static mode

Certbot runs on the host and renews certificates automatically.  After a
successful renewal, the deploy hook restarts Traefik so it rereads the
renewed certificate files.

**Deploy hook** (`scripts/certbot-deploy-hook.sh`):
The hook resolves the Traefik container with the same Compose-label discovery
as `scripts/verify.sh`, then runs `docker restart --time 5 <name>`.  The restart
takes about 3 seconds and writes no synced stack file.

```sh
# Install the stable hook symlink from the instance:
ln -sf /srv/tls-edge-instance/stack/scripts/certbot-deploy-hook.sh \
   /etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh

# Test renewal dry-run including hook execution:
certbot renew --dry-run
```

### Superseded alternatives

**SIGUSR2 graceful re-exec**: previously documented as a zero-downtime
alternative.  SIGUSR2 is not a Traefik feature (USR1 is used only for
access-log rotation in some proxies).  This section has been removed.

**Mounting `conf.d/` read-write to enable `touch`**: previously rejected
because it was believed that a `:ro` bind mount blocked the host-side writes
needed to trigger inotify.  This was incorrect.  `:ro` only restricts writes
from inside the container; the host can always write through the mount.  The
read-only flag is retained for hardening. The supported hook does not depend on
that inotify path; it performs the documented restart instead.

---

## 14. Port Collision Behaviour

### Host port collision (Docker level)

If two containers attempt to publish the same host port (e.g., both `ports: - "443:..."`):
- Docker rejects the second `docker compose up` with:
  `Bind for 0.0.0.0:443 failed: port is already allocated`
- The first stack retains the binding; the second fails to start.
- **Rule**: only the tls-edge instance may publish host HTTP/HTTPS ports. HTTP
  consumer stacks use `expose:` only; an application serving raw TCP/UDP media
  owns and publishes its separate host media port pair directly.

### Traefik router rule collision (proxy level)

If two containers both declare `traefik.http.routers.<name>.rule=Host('my.domain')`
on the same entrypoint:
- Traefik logs a warning and uses an indeterminate selection.
- Both containers may be alternately served.
- **Resolution**: router names (`<name>`) must be unique host-wide, not just
  within a stack.  Convention: `<stack>-<service>` (e.g. `pattern-a-app`,
  `pattern-b-api`, `pattern-c-app`).

### Path conflict within the same hostname

If two services share a hostname and a path prefix:
- Neither Docker nor Traefik has a deterministic winner at the prefix level.
- **Resolution**: make path rules mutually exclusive using `PathPrefix` and
  ensure they do not overlap.  Only one service can own a given Host+entrypoint
  pair; use path prefixes to share that pair between services.

---

## 15. Verification & Audit

### Codified check suite

`scripts/verify.sh` is the canonical verification tool.  It checks, in order: the
bundled `<root>/ciu check`; that `docker compose config -q` accepts the rendered
stack; both containers' health; `ingress_public` and the internal network; the
socket-proxy policy (GET allowed, POST refused); the HTTP redirect (when
`expose_http = true`); the served certificate (not Traefik's self-signed
default); the ACME volume or static cert file; the canary route through
`websecure`; the access-log JSON format; and the `--ops-check` isolation probe.

Run it after any configuration change before declaring the deployment healthy --
with `--no-canary` until 4.0.1.  4.0.0's canary routes `verify.<--domain>` on
`websecure` without router-level `tls=true`, so the entrypoint's `certResolver`
orders a REAL certificate for that name (public CT entry and a CA rate-limit
count) and then probes 5 s later, before issuance finishes.  Its check 5 cannot
pass (a single-quoted probe drops the proxy name) and the EXIT trap calls an
undefined `cleanup_tmp`, so a run that reaches the canary exits 127.  The
operator-visible summary is in `KNOWN_ISSUES_TODO_BACKLOG.md`; the fix is TE-15.

### Route registration audit

Traefik logs container discovery events at INFO level when `watch: true` is set.

```sh
# Live tail of route registration events
docker logs tls-edge-<id>-traefik --follow | grep -E "Adding|Removing|Configuration"

# Docker event stream: container start/stop/label changes
docker events --filter type=container --filter event=start \
              --filter event=die --filter event=destroy
```

### TLS and routing verification

```sh
# Full TLS handshake and cert chain check
openssl s_client -connect svc.my.domain:443 -servername svc.my.domain </dev/null 2>&1 \
  | grep -E "subject|issuer|Verify|Protocol|Cipher"

# HTTPS response check (ACME or static cert)
curl -sv https://svc.my.domain/ 2>&1 | grep -E "< HTTP|Server"

# Dev mode: trust the local CA or skip verification
curl --cacert /path/to/local-ca.crt https://svc.my.domain/
# or (insecure, dev only):
curl -k https://svc.my.domain/
```

Note: an instance whose override keeps the HTTP listener answers there with a
permanent redirect to https.  When `expose_http = false`, `curl http://...` times
out or is refused.

### Access log as audit trail

The Traefik access log is written to stdout in **JSON format**.  Every request
record includes the router name, service name, upstream address, HTTP status,
and latency.

```sh
# Structured access log query: time, router, status for all routed requests
docker logs tls-edge-<id>-traefik | jq -r 'select(.RouterName != null) | [.time, .RouterName, .DownstreamStatus] | @tsv'

# Filter by router name
docker logs tls-edge-<id>-traefik | jq -r 'select(.RouterName == "pattern-a-app@docker")'

# Count requests by router
docker logs tls-edge-<id>-traefik | jq -r 'select(.RouterName != null) | .RouterName' | sort | uniq -c | sort -rn
```

Pipe `docker logs tls-edge-<id>-traefik` to a log aggregator (Loki, Elastic, Splunk)
for persistent, searchable, tamper-resistant audit.

### Ops isolation verification

```sh
# Confirm ops port is NOT reachable from a peer container
docker run --rm --network <stack>_private alpine \
  sh -c "wget -qO- --timeout=3 http://<app-container>:9090/metrics 2>&1 || echo BLOCKED"
# Expected: Connection refused or no route to host
```

---

## 16. Future Options

### F1. Wildcard TLS certificate via Traefik ACME (DNS-01) — IMPLEMENTED

This option has been implemented as `mode = "acme-dns"` with optional
`wildcard_main` in `[tls_edge.acme.dns]`.  See [Section 4](#4-tls-strategy).

### F2. Multiple Traefik instances per trust class — out of scope

Run separate Traefik containers, each attached to a different ingress network,
serving different consumer groups with different routing policies.  Dropped: no
demand.  The requirements, recorded so the option is not re-argued:

- Different consumer groups need different TLS policies (e.g., mTLS for internal
  services, standard TLS for public).
- Routing table size per Traefik instance needs to be bounded.
- Regulatory requirement for network-level isolation between trust zones.

The implementation would need either a different published host port per Traefik
instance or an upstream L4 load balancer (HAProxy, IPVS) distributing by SNI —
neither of which the single-host compose deployment has.

### F3. Host firewall enforcement — out of scope

Host firewall rules can enforce east-west access control between containers
when Docker itself cannot express it (a per-port-per-network ACL does not exist
at the Docker level).  That is host setup, not part of the edge: rules, their
survival across Docker restarts, and the network-CIDR bookkeeping belong to the
host's own firewall configuration.  tls-edge therefore ships no helper and
only documents the limitation (see §17), which is the mitigation the operator
actually applies.

### F4. OPNsense as external firewall/reverse proxy

**What**: place an OPNsense (FreeBSD) appliance or VM in front of the Docker
host to handle WAN/LAN firewalling, NAT, and optionally reverse proxying.

**Architecture**:
```
Internet → OPNsense (WAN) → OPNsense LAN NIC → Docker host (port 443)
```

OPNsense handles:
- Stateful packet filtering
- NAT
- Optional HAProxy or Nginx as first-level reverse proxy
- Certificate termination at the perimeter (before traffic reaches Docker host)

The Docker host then only receives traffic from the OPNsense LAN interface,
not directly from the internet.

**Constraint**: OPNsense is a full FreeBSD appliance.  It is not practical to
run it as a Docker Compose service on the same host.  It requires a separate
VM or physical machine.

### F5. Traefik middleware additions — Partially implemented

Security headers (`secure-headers`) are now attached at the entrypoint level
and apply to all consumers.  Remaining middleware work is tracked as a roadmap
item in `KNOWN_ISSUES_TODO_BACKLOG.md` (FEAT-01 and its middleware list):
- Rate limiting (`rateLimit`)
- IP allowlisting (`ipAllowList`)
- HTTP Basic Auth (`basicAuth`)
- Forward Auth (OAuth2/OIDC gateway via a sidecar like `thomseddon/traefik-forward-auth`)
- Content Security Policy (per-router `headers` middleware, intentionally not
  set globally to avoid breaking iframe-embedding consumers)

### F6. Centralised log aggregation — out of scope

Shipping `docker logs tls-edge-<id>-traefik` to an aggregation stack is the host's
logging stack, not an edge feature: dropped.  The access log stays JSON and the
container's log driver stays bounded, so a host that runs Loki + Grafana,
Elastic Stack, or Splunk can collect it there without a tls-edge change.

### F7. Traefik label constraints for consumer governance

Traefik's `providers.docker.constraints` setting filters which containers it
will route.  Example: only route containers whose `stack` label matches an
approved list:

```yaml
providers:
  docker:
    constraints: "Label(`traefik.constraint-label`, `public`)"
```

Consumers must then also declare `traefik.constraint-label=public`.  This adds
a second gate that is harder to satisfy accidentally.

---

## 17. Known Limitations & Tradeoffs

| Limitation | Impact | Mitigation |
|---|---|---|
| Docker has no per-port-per-network ACL | Ops endpoints can only be isolated by app bind address or host firewall | Use loopback binding + sidecar; host firewalling is host setup, not the edge |
| Cert mount footgun (static mode): must mount parent dirs, not `live/<domain>` directly | Mounting `live/<domain>` breaks certbot's relative symlinks; Traefik silently serves its self-signed default cert | Fixed by design: compose template mounts `cert_base/live` and `cert_base/archive` as parents; explanation retained for awareness |
| `exposedByDefault: false` is the only admission gate | Any container on `ingress_public` with the right label is routed | Add label constraints (F7) or network segmentation by trust class (F2) |
| Static-mode certificate reload restarts Traefik | The hook resolves the instance container and restarts it, so the edge is unavailable briefly | `docker restart --time 5 <resolved-name>`; expect about 3 seconds of downtime after a renewal |
| acme-data volume loss | `docker compose down -v` destroys ACME state; all certs are re-issued on next start; LE rate limits (~5 duplicate certs/week) apply | Never use `-v` in production without a backup plan; use staging CA for experiments |
| sniStrict rejects SNI-less clients | Bare-IP probes, some uptime checkers, and `openssl s_client` without `-servername` are rejected | Expected and desirable; disable `sniStrict` in `conf.d/options.yml` only if such clients must be served |
| HTTP listener in the shipped defaults | The edge answers on a second public port | Use `tls-edge configure` to disable it for an installed instance; `acme-http` then cannot be used |
| Router name uniqueness is operator-enforced | Two stacks using the same router name will conflict | Naming convention adopted in examples: `<stack>-<service>` |
| App must configure loopback bind for ops | Docker cannot enforce this; relies on app configuration discipline | Document requirement prominently; verify via `scripts/verify.sh` |
| Single Traefik instance is a SPOF | If Traefik crashes, all public services are unreachable | `restart: unless-stopped` mitigates transient failures; HA Traefik requires Swarm or Kubernetes |
| `ingress_public` east-west visibility | Containers on ingress can attempt connections to each other | Keep ingress membership minimal; strict isolation needs host firewall rules installed by the host |
| Static config changes (log level, entrypoints, ACME) require re-render + restart | No live override via env vars once config file is mounted | Re-render via ciu (or `tools/render.sh` for a maintainer trial), then `docker compose restart traefik` |

Additional known issues and open items are tracked in `KNOWN_ISSUES_TODO_BACKLOG.md`.
