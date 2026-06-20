# TLS-as-a-Service – Consumer Guide

## Overview

The edge-proxy stack runs a single Traefik instance that:
- terminates TLS for all consumer services on this host
- routes requests by subdomain (preferred) or by external port (fallback)
- discovers consumer services automatically from Docker labels
- exposes nothing by default; containers must opt in

Port 80 is **off by default**.  The edge is TLS-only.  There is no
HTTP→HTTPS redirect unless the operator enables it.  Always publish
`https://` URLs.

Consumers attach to the shared `ingress_public` network and declare routing
via Compose labels.  No access to the edge-proxy stack itself is needed.

---

## Installing tls-edge

If tls-edge is not already installed on the host, bootstrap it in one command:

```sh
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/tls-edge/get.py \
  | sudo python3 - install
```

This downloads the latest release artifact from GitHub Releases, verifies its
SHA256 checksum, and extracts it to `/opt/tls-edge-src/tls-edge`.

**Pin a specific version** (recommended in production):

```sh
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/tls-edge/get.py \
  | sudo python3 - install --version tls-edge-v0.2.0
```

**Update to a newer release**:

```sh
tls-edge update
```

User config (`ciu-stack/ciu.toml.j2`, `edge-proxy/.env`) is backed up and
restored automatically across updates.

**Air-gapped / dev installs** (no GitHub Releases access):

```sh
curl -fsSL https://raw.githubusercontent.com/volkb79-2/vbpub/main/tls-edge/get.py \
  | sudo python3 - install --via git
```

This falls back to a sparse git clone (no artifact download, no checksum
verification). Requires `git`.

---

## Prerequisites

1. The edge-proxy stack is running (any of these starts it):
   ```sh
   # Standalone (pre-rendered default — acme-tls mode, no tooling required):
   cd tls-edge/edge-proxy && docker compose up -d

   # Via installer:
   tls-edge/scripts/install.sh

   # Via ciu v2 (renders + deploys from templates):
   cd tls-edge/ciu-stack && ciu
   ```

2. The shared ingress network exists (created by the edge-proxy stack):
   ```sh
   docker network ls | grep ingress_public
   ```

3. Your domain and DNS are in place:
   - **ACME modes** (`acme-tls`, `acme-http`, `acme-dns`): the DNS name in
     each consumer's `Host()` label must resolve to this host.  Traefik
     issues and renews the certificate automatically.  No cert files to manage.
   - **static / dev modes**: the certificate files must already exist at the
     path configured in `[tls_edge.static]`:
     ```sh
     ls /etc/letsencrypt/live/<your-domain>/
     # expected: cert.pem  chain.pem  fullchain.pem  privkey.pem
     ```

---

## Routing modes

### Subdomain routing (preferred)

Each service gets its own subdomain: `svc.my.domain`, `api.my.domain`, etc.

Requirements:
- The subdomain must resolve to this host.  A wildcard DNS `A/CNAME` record
  (`*.my.domain → host IP`) satisfies this for all subdomains at once and is
  the recommended approach for self-service onboarding.
- In **ACME modes**, the TLS certificate is issued automatically for each
  `Host()` name — no cert work needed.  A wildcard DNS record + per-name ACME
  gives full subdomain self-service without requiring a wildcard certificate.
- In **static mode**, the subdomain must be present in the certificate's
  Subject Alternative Names (SANs), or a wildcard certificate (`*.my.domain`)
  must be in use.  Wildcard certificates require DNS-01 challenge (either an
  API-capable DNS provider in `acme-dns` mode, or `certbot --manual` for a
  one-time issuance).

Labels for subdomain routing (four labels; TLS is at the entrypoint level):
```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.<name>.rule=Host(`svc.my.domain`)
  - traefik.http.routers.<name>.entrypoints=websecure
  - traefik.http.services.<name>.loadbalancer.server.port=<container-port>
```

`tls=true` and `tls.certresolver` labels are not needed — TLS and the cert
resolver are configured at the entrypoint level by the edge proxy.  They are
harmless if present, but omitting them keeps labels clean.

### Port-based routing (fallback)

Use when a subdomain cannot be made to resolve to this host, or (in static
mode) is not in the certificate SAN list.  The service is reached at
`https://my.domain:8443`.

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.<name>.rule=Host(`my.domain`)
  - traefik.http.routers.<name>.entrypoints=websecure-alt
  - traefik.http.services.<name>.loadbalancer.server.port=<container-port>
```

> Only one service can own the combination of hostname + entrypoint.
> Two services on `my.domain` + `websecure-alt` would conflict.
> Add a path matcher to distinguish them:
> `- traefik.http.routers.<name>.rule=Host(`my.domain`) && PathPrefix(`/svcA`)`

### Router and service naming

Router and service names in labels must be **unique across the whole host**,
not just within the stack.  Two stacks declaring the same router name will
conflict, and Traefik will drop or misroute one of them.

**Convention**: `<stack>-<service>` — for example `pattern-a-app`,
`pattern-b-api`, `pattern-c-app`.  The consumer examples follow this pattern.

---

## Consumer patterns

### Pattern A – Public-only service

**File**: `consumer-examples/pattern-a-public-only/docker-compose.yml`

The simplest case.  The container joins `ingress_public` and declares a
Traefik router.

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.pattern-a-app.rule=Host(`svc.my.domain`)
  - traefik.http.routers.pattern-a-app.entrypoints=websecure
  - traefik.http.services.pattern-a-app.loadbalancer.server.port=8080
```

Checklist:
- [ ] Image and container port are correct
- [ ] `traefik.enable=true` label is present
- [ ] Router rule uses your actual subdomain or domain
- [ ] `loadbalancer.server.port` matches the port your app listens on
- [ ] `ingress_public` is declared as `external: true`
- [ ] Router/service name is unique on this host (`<stack>-<service>`)

---

### Pattern B – Public service with private backends

**File**: `consumer-examples/pattern-b-public-and-private/docker-compose.yml`

The public-facing service joins both `ingress_public` and `private`.
Backend services (databases, caches, workers) join only `private`.

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.pattern-b-api.rule=Host(`api.my.domain`)
  - traefik.http.routers.pattern-b-api.entrypoints=websecure
  - traefik.http.services.pattern-b-api.loadbalancer.server.port=8080
```

Checklist:
- [ ] Only the public-facing container has `traefik.enable=true`
- [ ] Databases and caches do NOT join `ingress_public`
- [ ] `private` network is declared `internal: true`
- [ ] No backend container publishes host ports

Security note: even though `api` joins `ingress_public`, only its explicitly
labelled router port is routed by Traefik.  Other ports on the same container
are not automatically exposed to the internet — they can still be reached by
other containers on `ingress_public` though (east-west).  Keep `ingress_public`
membership minimal.

---

### Pattern C – Public API with ops endpoint isolated to sidecar

**File**: `consumer-examples/pattern-c-public-and-ops-sidecar/docker-compose.yml`

The app binds its ops/metrics endpoint to `127.0.0.1` (loopback) inside
the container.  A sidecar joins the app's network namespace via
`network_mode: service:app` and scrapes the ops endpoint at `127.0.0.1:<ops-port>`.

No other container — including containers on the same `private` or
`ingress_public` network — can reach the ops endpoint because loopback
(`127.0.0.1`) is per-network-namespace.

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.pattern-c-app.rule=Host(`app.my.domain`)
  - traefik.http.routers.pattern-c-app.entrypoints=websecure
  - traefik.http.services.pattern-c-app.loadbalancer.server.port=8080
```

Checklist:
- [ ] App is configured to bind ops listener to `127.0.0.1`, NOT `0.0.0.0`
      (this is an application configuration responsibility, not a Docker feature)
- [ ] `traefik.http.services.<name>.loadbalancer.server.port` is set to the
      public API port only (e.g. 8080), not the ops port (e.g. 9090)
- [ ] Sidecar has `network_mode: "service:app"` and no `networks:` block
- [ ] Sidecar has NO `traefik.enable=true` label
- [ ] Sidecar binds its own listener to `127.0.0.1` on a port the app does
      not use (a sidecar listening on `0.0.0.0` is reachable by peers)

---

### Pattern D – Guarding a route (per-route basicAuth)

**File**: `consumer-examples/pattern-d-guarded/docker-compose.yml`

**Helper**: `scripts/gen-guard-secret.sh`

Protects the route with HTTP Basic Authentication declared entirely in Docker
Compose labels.  Traefik discovers the middleware automatically — no changes to
the edge-proxy stack are needed.

#### How it works

A Traefik `basicAuth` middleware is defined on the consumer container and
attached to its router.  Any unauthenticated request receives `401 Unauthorized`.
Traefik checks credentials against a list of `user:hash` pairs embedded in the
label value.

#### Generating the hash

```sh
# Helper (bcrypt preferred; falls back to openssl APR1 if htpasswd unavailable):
tls-edge/scripts/gen-guard-secret.sh user mysecret

# Or directly:
htpasswd -nbB user mysecret          # bcrypt (apache2-utils package)
openssl passwd -apr1 mysecret        # APR1-MD5 fallback
```

#### Dollar-sign escaping in Compose labels

**This is the most common mistake.**  Bcrypt hashes contain literal `$`
characters (e.g. `$2y$12$...`).  In a `docker-compose.yml` label value, `$`
is a variable prefix that Compose expands.  Each `$` in the hash must be
written as `$$`.

The helper script prints both the raw hash and the `$$`-escaped form ready to
paste into a label.

```yaml
labels:
  - traefik.enable=true
  - traefik.http.routers.mystack-app.rule=Host(`guarded.my.domain`)
  - traefik.http.routers.mystack-app.entrypoints=websecure
  - traefik.http.services.mystack-app.loadbalancer.server.port=8080

  # Define the basicAuth middleware (every $ in hash → $$ in label value):
  - traefik.http.middlewares.mystack-app-guard.basicauth.users=user:$$2y$$12$$xyz...

  # Attach the middleware to the router:
  - traefik.http.routers.mystack-app.middlewares=mystack-app-guard
```

Using a `.env` variable avoids manual `$$`-escaping — see the example stack's
`README.md` for details.

#### What clients send

```sh
# curl with credentials:
curl -u user:mysecret https://guarded.my.domain/

# Explicit header:
curl -H "Authorization: Basic $(printf 'user:mysecret' | base64)" \
     https://guarded.my.domain/
```

#### Playwright / WebSocket note

Playwright's `connect()` sends a WebSocket upgrade request.  Most WS client
libraries do not forward `Authorization: Basic` headers on the upgrade, so
guarded WS routes require either URL-embedded credentials
(`wss://user:secret@host/`) or — more reliably — placing automated clients on
the `private` internal network and reserving the basicAuth guard for HTTP routes
only (e.g. an MCP SSE endpoint or a docs page).  For true bearer-token
protection of WS routes, see the "Follow-up" section below.

Checklist:
- [ ] Hash generated with `gen-guard-secret.sh` (not the placeholder)
- [ ] Every `$` in the hash is `$$`-escaped in the label value (or hash comes from `${VAR}`)
- [ ] Middleware name is unique on this host: `<stack>-<service>-guard`
- [ ] Middleware is listed in `traefik.http.routers.<name>.middlewares`

---

### Follow-up: bearer-token guard via forwardAuth

True `Authorization: Bearer <token>` validation requires an external auth
service that Traefik calls for every request via the `forwardAuth` middleware.
The auth service receives the full request headers, validates the token (JWT
signature check, OIDC introspection, etc.), and responds with `2xx` (allow) or
`401`/`403` (deny).

This pattern is **not yet provided** in tls-edge.  When it is, the consumer
labels will look like:

```yaml
# Hypothetical future labels — not yet implemented:
- traefik.http.middlewares.mystack-bearer.forwardauth.address=http://auth-svc:8080/verify
- traefik.http.middlewares.mystack-bearer.forwardauth.authResponseHeaders=X-User,X-Roles
- traefik.http.routers.mystack-app.middlewares=mystack-bearer
```

Until then, use the basicAuth pattern (Pattern D) for simple shared-secret
access control, or run the auth service yourself and point `forwardauth.address`
at it.

---

## Verification

### 1. Run the codified check suite

```sh
tls-edge/scripts/verify.sh
```

Checks: template freshness, socket-proxy policy, TLS cert loaded (not the
Traefik self-signed default), canary route end-to-end, ops isolation probe.

### 2. Check Traefik saw your service

```sh
# Traefik logs show provider reload events when containers start/stop.
docker logs edge-traefik --follow --tail 50

# Look for lines like:
#   level=info ... msg="Adding route for [svc.my.domain]"
#   level=info ... msg="Configuration received"
```

### 3. Check registered routers and services via API (dev mode only)

If `api_insecure = true` and dashboard is enabled in the config:
```sh
# List all active HTTP routers
curl http://127.0.0.1:8080/api/http/routers | python3 -m json.tool

# List all active HTTP services
curl http://127.0.0.1:8080/api/http/services | python3 -m json.tool
```

### 4. Test TLS and routing

```sh
# Verify cert chain and TLS handshake
openssl s_client -connect svc.my.domain:443 -servername svc.my.domain </dev/null 2>&1 | \
  grep -E "subject|issuer|Verify"

# Verify HTTPS responds
curl -v https://svc.my.domain/
# Expected: 200 from your application

# Dev mode: trust the local CA or skip verification
curl --cacert /path/to/local-ca.crt https://svc.my.domain/
curl -k https://svc.my.domain/        # insecure; dev only

# Port-based routing check
curl -v https://my.domain:8443/
```

Note: port 80 is off by default.  `curl http://svc.my.domain/` will be
refused or time out — there is no redirect.

### 5. Access log audit trail

The Traefik access log is in **JSON format**.  Use `jq` to query it:

```sh
# Time, router name, and HTTP status for all routed requests
docker logs edge-traefik | jq -r 'select(.RouterName != null) | [.time, .RouterName, .DownstreamStatus] | @tsv'

# Filter to a specific router
docker logs edge-traefik | jq -r 'select(.RouterName == "pattern-a-app@docker")'

# Docker events show container registration / deregistration moments.
docker events --filter type=container --filter event=start --filter event=die
```

### 6. Confirm ops endpoint is NOT reachable from a peer container

```sh
docker run --rm --network <your-stack>_private alpine \
  wget -qO- http://<app-container-name>:9090/metrics
# Expected: Connection refused or no route to host
# (only succeeds if ops listener is incorrectly bound to 0.0.0.0)
```

---

## Port collision: what happens when two containers claim the same route

Docker host port collisions (two containers try to `ports: - "443:..."` directly):
- Docker rejects the second `docker compose up` with an error like:
  `Bind for 0.0.0.0:443 failed: port is already allocated`
- Only the edge-proxy stack should ever publish ports 443 and 8443 (and
  optionally 80).
- Consumer stacks must NOT publish their own ports on those numbers.

Traefik router rule collisions (two services declare the same hostname + entrypoint):
- Traefik logs a warning and routes to one of them (indeterminate selection).
- Use unique subdomains, or add a path prefix rule to distinguish them.
- Only one service can own a given Host + entrypoint combination; add
  `&& PathPrefix(...)` to share it between multiple services.
- Check for conflicts: `docker logs edge-traefik | grep -i "conflict\|duplicate"`

---

## Certificate renewal

Renewal behaviour depends on the TLS mode configured in the edge proxy.

### ACME modes (acme-tls, acme-http, acme-dns) — zero operator action needed

Traefik renews all certificates automatically before expiry.  Renewal is
zero-downtime.  There is nothing for the consumer to do.

The only risk is loss of the `acme-data` named volume (e.g. via
`docker compose down -v`), which forces re-issuance of all certificates on
the next start.  Let's Encrypt rate limits apply (approximately 5 duplicate
certificates per week per domain).  Do not delete the volume in production.

### static mode — per-subdomain or SAN cert (automatic certbot renewal)

Certbot runs on the host and renews certificates automatically.  After
renewal, the deploy hook rewrites the `# rendered:` timestamp in
`edge-proxy/conf.d/certs.yml` on the host filesystem.  The `:ro` bind mount
propagates this change into the container and triggers Traefik's inotify
file-provider watcher, reloading the new certificate with **zero downtime**.

```sh
# Install the hook (run once, or via scripts/install.sh):
cp tls-edge/scripts/certbot-deploy-hook.sh \
   /etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh
chmod +x /etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh

# Test renewal dry-run including hook execution:
certbot renew --dry-run
```

Expected output during renewal:
```
Running deploy hook: /etc/letsencrypt/renewal-hooks/deploy/01-reload-traefik.sh
[certbot-hook] Certificate renewed.  Reloading Traefik dynamic config...
[certbot-hook] Reload triggered.
```

If the inotify reload does not fire for any reason, the fallback is:
```sh
docker restart --time 5 edge-traefik   # ~3 seconds downtime
```

### static mode — wildcard cert via manual DNS TXT (no DNS API required)

Use this when you want a `*.example.com` wildcard cert but do **not** have
programmatic access to your DNS provider's API.  The DNS-01 challenge requires
adding a TXT record manually; certbot pauses and waits while you do so.

**Initial issuance:**

```sh
certbot certonly \
  --manual --preferred-challenges dns \
  --email ops@example.com --agree-tos --no-eff-email \
  -d example.com -d '*.example.com'
```

certbot will output:

```
Please deploy a DNS TXT record under the name:
_acme-challenge.example.com
with the following value:
AbCdEf1234...randomvalue...

Press Enter to Continue
```

1. Log into your DNS zone control panel.
2. Add a TXT record: `_acme-challenge.example.com` → `AbCdEf1234...`
3. Wait 30–60 seconds for DNS propagation, then press Enter in certbot.
4. Repeat if certbot issues two challenges (it does for `d` + `*.d`).
5. Cert is saved to `/etc/letsencrypt/live/example.com/`.

Verify the cert is readable by Traefik:
```sh
docker run --rm -v /etc/letsencrypt:/c:ro alpine \
  cat /c/live/example.com/fullchain.pem | openssl x509 -noout -subject -ext subjectAltName
# Expected: DNS:example.com, DNS:*.example.com
```

**Renewal (every ~60 days):**

`certbot --manual` certs do **not** renew via `certbot renew` alone — there is
no hook to create the TXT record automatically.  You must re-run the command
and add a fresh TXT record each time:

```sh
# Check expiry (set a calendar reminder ~2 weeks before):
certbot certificates

# Re-issue (same interactive flow as above):
certbot certonly \
  --manual --preferred-challenges dns \
  --email ops@example.com --agree-tos --no-eff-email \
  -d example.com -d '*.example.com'

# After certbot completes, Traefik reloads via the deploy hook automatically.
# No container restart needed.
```

> **Tip**: if you later gain API access to your DNS provider, switch to
> `acme-dns` mode (`ciu.toml.j2`: `[tls_edge.tls] mode = "acme-dns"`) for
> fully automated renewals — no more manual TXT records.

### dev mode

Self-signed certificates are generated by `scripts/dev-certs.sh` and do not
require renewal during normal development.  Regenerate when the cert expires
or when adding new domains.

---

## Label reference

| Label | Required | Description |
|---|---|---|
| `traefik.enable=true` | yes | Opt this container into Traefik routing |
| `traefik.http.routers.<n>.rule` | yes | Routing rule, e.g. `Host(...)` |
| `traefik.http.routers.<n>.entrypoints` | yes | `websecure` (443) or `websecure-alt` (8443) |
| `traefik.http.services.<n>.loadbalancer.server.port` | yes | Container port to forward to |
| `traefik.http.routers.<n>.tls=true` | no | TLS is enabled at the entrypoint level — not needed |
| `traefik.http.routers.<n>.tls.certresolver` | no | Cert resolver is set at the entrypoint level — not needed |
| `traefik.http.middlewares.<m>.basicauth.users` | no | Comma-separated `user:hash` list for per-route basic auth (Pattern D); every `$` in the hash must be `$$` in the label value |
| `traefik.http.routers.<n>.middlewares` | no | Comma-separated middleware names to attach to this router |

`<n>` must be a unique name per router/service **across all containers on the host**.
Convention: `<stack>-<service>`.

---

## File layout

```
tls-edge/
  ciu-stack/                        ← Jinja2 templates (single source of truth)
    ciu.defaults.toml.j2            ←   all configuration options + defaults
    ciu.compose.yml.j2              ←   Compose template
    traefik.yml.j2                  ←   Traefik static config template
    conf.d/
      options.yml                   ←   TLS options (hot-reloaded; not templated)
      middlewares.yml               ←   secure-headers (hot-reloaded; not templated)
      certs.yml.j2                  ←   static cert store template
    ciu.toml.j2                     ←   (gitignored) your local overrides

  edge-proxy/                       ← rendered output — DO NOT EDIT DIRECTLY
    docker-compose.yml              ←   pre-rendered for acme-tls (default mode)
    traefik.yml                     ←   pre-rendered static config
    conf.d/
      options.yml                   ←   copied from ciu-stack/conf.d/
      middlewares.yml               ←   copied from ciu-stack/conf.d/
      certs.yml                     ←   rendered cert store (ACME mode: empty)
    .env                            ←   (gitignored) acme-dns credentials

  scripts/
    render.sh                       ←   standalone re-render (writes edge-proxy/)
    install.sh                      ←   first-time setup + hook installation
    verify.sh                       ←   post-deploy check suite
    dev-certs.sh                    ←   generate self-signed certs for dev mode
    certbot-deploy-hook.sh          ←   install to /etc/letsencrypt/renewal-hooks/deploy/

  consumer-examples/
    pattern-a-public-only/
      docker-compose.yml
    pattern-b-public-and-private/
      docker-compose.yml
    pattern-c-public-and-ops-sidecar/
      docker-compose.yml
    pattern-d-guarded/
      docker-compose.yml
      .env.example
      README.md
```

To change any configuration value, edit `ciu-stack/ciu.toml.j2` (create it if
it does not exist) with only the keys to override, then re-render:

```sh
# Standalone:
tls-edge/scripts/render.sh

# ciu v2:
cd tls-edge/ciu-stack && ciu
```

Static config changes (log level, entrypoints, TLS mode, ACME settings) take
effect only after a container restart.  Dynamic config changes (TLS options,
middlewares, static certs) hot-reload automatically.
