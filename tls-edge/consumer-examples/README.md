# Consumer examples, label by label

A consumer is any Compose stack that wants the edge to publish it. The edge (Traefik) discovers it
from container labels on the shared `ingress_public` network. HTTP consumers publish no host ports;
the edge owns 80 and 443. Raw TCP/UDP media is published directly by its application on a separate
host port pair. All hosts below use `${TLS_EDGE_DOMAIN}` from `.env.example` (the placeholder
`example.test`); an installed edge writes the same variable to
`<instance>/stack/consumer.env`, so a real consumer can run
`docker compose --env-file /srv/tls-edge-instance/stack/consumer.env up -d`.

Check any example offline before starting it: `docker compose --env-file ../.env.example config`.

| Example | Shows | Labels you need |
|---|---|---|
| `pattern-a-public-only` | one web app on its own host name | enable, router rule + entrypoint, service port |
| `pattern-d-guarded` | the same plus a basicAuth guard | + a middleware and its attachment |
| `pattern-e-media-udp` | HTTPS UI plus an application-published raw TCP/UDP media pair | HTTP router; media bypasses Traefik |
| [Jitsi Meet deployment](https://github.com/volkb79-2/vbpub/tree/main/voice-screen-share/jitsi-server) | in-repository consumer | standard `websecure` host router; JVB media stays direct on UDP 10000 |

## In-repository Jitsi deployment

The repository's real Jitsi Meet deployment is a tested consumer of the
default tls-edge HTTP endpoint. Its `jitsi-web`
service joins `ingress_public`, routes `jitsi.<domain>` through `websecure`, and
serves plain HTTP on container port 8000. TLS termination, HTTP/2, and optional
HTTP/3 are edge-wide; Jitsi needs no special tls-edge setting or per-router
transport label. WebSocket signaling uses the same HTTPS router.

Jitsi Videobridge media is separate: the JVB publishes UDP 10000 directly on
the host, so it does not use Traefik or tls-edge.
Only `jitsi-web` joins the public ingress network. See the
[Jitsi deployment guide](https://github.com/volkb79-2/vbpub/blob/main/voice-screen-share/jitsi-server/README.md)
for DNS, firewall, and media details. The Jitsi consumer suite renders its
actual Compose template and checks the route against tls-edge's rendered
default entrypoint, including the optional HTTP/3 setting.

## Rules that hold for every example

- **Join `ingress_public` as `external: true`.** The edge creates and owns that network. A service
  that is not on it cannot be reached by Traefik.
- **Router, service and middleware names are global on the host.** Use `<stack>-<service>`
  (`pattern-a-app`). Two stacks using one router name conflict and Traefik drops or misroutes one.
- **One host name per service**, all on port 443. There is no alternate HTTPS port; give each
  service its own DNS name (one A/AAAA record each, or one wildcard record).
- **Do not publish `80` or `443`** from an HTTP consumer; Docker would refuse the second bind.

## pattern-a: a web app

| Label | What it does |
|---|---|
| `traefik.enable=true` | Opt in. The edge runs with `exposedByDefault=false`, so unlabelled containers are never routed. |
| ``traefik.http.routers.pattern-a-app.rule=Host(`app.${TLS_EDGE_DOMAIN}`)`` | Which requests belong to this router. The `Host()` names are also the names the ACME resolver requests certificates for, **one certificate per host** the first time it is requested (a wildcard certificate only exists when the edge is configured `acme-dns` with `wildcard_main`). |
| `traefik.http.routers.pattern-a-app.entrypoints=websecure` | Attach to the HTTPS entrypoint on :443. TLS is switched on at that entrypoint and the certificate resolver is set there too, so **no `tls=true` or `certresolver` label is needed**. |
| `traefik.http.services.pattern-a-app.loadbalancer.server.port=8080` | The container port Traefik forwards to (the app's plain HTTP port; TLS ends at the edge). |

`expose: ["8080"]` is documentation only. `private` (`internal: true`) is an optional second network for
the app's own backends; backends never join `ingress_public`.

## pattern-d: a guarded route

Everything from pattern-a, plus:

| Label | What it does |
|---|---|
| `traefik.http.middlewares.pattern-d-app-guard.basicauth.users=${GUARD_HASH}` | Declares a basicAuth middleware. `users` is a comma-separated list of `user:hash` htpasswd lines. The hash comes from the environment, not from the compose file. |
| `traefik.http.middlewares.pattern-d-app-guard.basicauth.realm=Restricted` | Text shown in the browser prompt. |
| `traefik.http.routers.pattern-d-app.middlewares=pattern-d-app-guard` | Attaches the middleware to the router. The edge's `secure-headers` middleware is attached at the entrypoint already and is not listed. |

Generate the hash with `scripts/gen-guard-secret.sh user 'a-long-secret'` and put it in `.env`
**single-quoted** (`GUARD_HASH='user:$2y$12$...'`) so Compose does not read `$2y` as a variable. See that
example's own README for escaping rules and why WebSocket clients should not be guarded.

## pattern-e: web UI plus application-published media

The HTTPS part is pattern-a. The application also publishes its raw TCP+UDP
listener directly on the host, using `MEDIA_PUBLIC_PORT` and
`MEDIA_CONTAINER_PORT` from `.env.example`. There are no TCP/UDP Traefik
routers or entrypoints.

| Setting | What it does |
|---|---|
| `MEDIA_PUBLIC_PORT` | The host port pair clients use. Allow it for both TCP and UDP in the host and provider firewalls. |
| `MEDIA_CONTAINER_PORT` | The application's listener, reached directly through Docker's host publication. |
| `traefik.http.routers.pattern-e-app.rule=Host(...)` | Routes only the HTTPS UI through `websecure`. |

The application must advertise the same public host and port pair to its
clients. Mattermost Calls and Jitsi JVB are examples of application-owned
media endpoints.
