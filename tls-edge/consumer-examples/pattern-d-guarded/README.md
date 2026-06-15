# Pattern D — per-route basicAuth guard

Protects a Traefik route with HTTP Basic Authentication declared entirely in
Docker Compose labels.  No changes to the shared edge-proxy stack are required.
Traefik auto-discovers the middleware from the container's labels.

---

## When to use this pattern

- Staging or preview environments that should not be publicly browsable
- Tooling endpoints (docs, dashboards) that require a shared secret
- Any route where a 401 without credentials is an acceptable UX

For OAuth2/OIDC or JWT-based access control, see the "Follow-up: bearer-token
via forwardAuth" note in `CONSUMER_GUIDE.md`.

---

## How to generate the password hash

```sh
# Use the helper (preferred — falls back to openssl if htpasswd is unavailable):
tls-edge/scripts/gen-guard-secret.sh user mysecret

# Or directly with htpasswd (bcrypt, cost factor 12):
htpasswd -nbB user mysecret

# Or with openssl (MD5-APR1; weaker but universally available):
openssl passwd -apr1 mysecret
```

The helper prints the `user:hash` line and a reminder about `$$`-escaping.

---

## Dollar-sign escaping in Compose labels

**This is the most common mistake with basicAuth-via-labels.**

Bcrypt and APR1 hashes contain literal `$` characters (e.g. `$2y$12$...`).
In a `docker-compose.yml` label value, `$` is a variable reference prefix that
Compose expands at startup.  Each `$` in the hash must be written as `$$` so
that Compose emits a single `$` for Traefik.

| Context | Write |
|---|---|
| Directly in `docker-compose.yml` label value | `$$2y$$12$$...` (double every `$`) |
| In `.env` via `${GUARD_HASH}` variable substitution | `$2y$12$...` (single `$`; Compose handles escaping) |

The `.env.example` file uses the variable-substitution approach, which is
easier to get right because you copy the hash from `gen-guard-secret.sh`
output directly — no manual doubling needed.

---

## Setup

1. Generate a hash and write it to `.env`:

   ```sh
   tls-edge/scripts/gen-guard-secret.sh user mysecret
   # Output:  user:$2y$12$xyz...
   # Copy the output line:
   cp .env.example .env
   # Edit .env and set GUARD_HASH=<copied line> (single $ — Compose handles escaping)
   ```

2. Update `docker-compose.yml`:

   - Replace `your-image:tag` with your actual image.
   - Replace `guarded.my.domain` with your actual subdomain.
   - Replace the placeholder hash with `${GUARD_HASH}` or a `$$`-escaped literal.

3. Start:

   ```sh
   docker compose up -d
   ```

---

## What the guard does

Any request to the protected route without valid credentials receives:

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Basic realm="traefik"
```

With valid credentials:

```sh
# curl
curl -u user:mysecret https://guarded.my.domain/

# Authorization header
curl -H "Authorization: Basic $(printf 'user:mysecret' | base64)" https://guarded.my.domain/

# Browser: shows a login popup; enter username and password
```

---

## Playwright and WebSocket clients

Playwright's `connect()` method (for connecting to a remote Playwright server)
uses a WebSocket upgrade request.  WebSocket clients generally **cannot** send
HTTP Basic Auth on the WS upgrade because the `Authorization` header is not
forwarded by all WS client implementations, and Playwright's `connect()` does
not have a built-in basic-auth option.

Two options for WebSocket access behind a basicAuth guard:

- **URL-embedded credentials** (RFC 3986): some WS client libraries honour
  `wss://user:secret@guarded.my.domain/`.  Traefik accepts this form because
  it extracts and validates the credentials from the URL.  Check that your
  client library actually sends these as an `Authorization: Basic` header on
  the upgrade request — not all do.

- **Recommended: no guard on the WS route; guard only the HTTP route.**  Place
  test runners and automated clients on the `private` internal network or on a
  separate unguarded route.  Use the basicAuth guard only for the human-facing
  or tooling HTTP endpoints (e.g. an MCP SSE endpoint, a docs site) where
  browser-based Basic Auth prompts are acceptable.

If you need to protect both HTTP and WS routes with a shared secret, bearer
tokens via a forwardAuth service are a better fit — see the "Follow-up" section
in `CONSUMER_GUIDE.md`.

---

## Checklist

- [ ] Image and container port are correct
- [ ] `traefik.enable=true` label is present
- [ ] Router rule uses your actual subdomain or domain
- [ ] `loadbalancer.server.port` matches the port your app listens on
- [ ] `ingress_public` is declared as `external: true`
- [ ] Router/service/middleware names are unique on this host (`<stack>-<service>`)
- [ ] Password hash is set — not the placeholder
- [ ] `$` characters in the hash are `$$`-escaped in the label value
      (or hash is supplied via `${GUARD_HASH}` from `.env`)
- [ ] Guard middleware is listed in the router's `middlewares` label
