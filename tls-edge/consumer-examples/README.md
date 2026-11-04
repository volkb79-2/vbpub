# Consumer examples, label by label

A consumer is any Compose stack that wants the edge to publish it. The edge (Traefik) discovers it
from container labels on the shared `ingress_public` network; the consumer publishes no host ports
of its own (80, 443 and 8443 belong to the edge). All hosts below use `${TLS_EDGE_DOMAIN}` from
`.env.example` (the placeholder `example.test`); an installed edge writes the same variable to
`<instance>/stack/consumer.env`, so a real consumer can run
`docker compose --env-file /srv/tls-edge-instance/stack/consumer.env up -d`.

Check any example offline before starting it: `docker compose --env-file ../.env.example config`.

| Example | Shows | Labels you need |
|---|---|---|
| `pattern-a-public-only` | one web app on its own host name | enable, router rule + entrypoint, service port |
| `pattern-d-guarded` | the same plus a basicAuth guard | + a middleware and its attachment |
| `pattern-e-media-udp` | HTTPS UI plus a raw TCP/UDP media port (Mattermost Calls) | + a TCP router and a UDP router |

## Rules that hold for every example

- **Join `ingress_public` as `external: true`.** The edge creates and owns that network. A service
  that is not on it cannot be reached by Traefik.
- **Router, service and middleware names are global on the host.** Use `<stack>-<service>`
  (`pattern-a-app`). Two stacks using one router name conflict and Traefik drops or misroutes one.
- **One host name per service**, all on port 443. There is no alternate HTTPS port; give each
  service its own DNS name (one A/AAAA record each, or one wildcard record).
- **Do not publish `80`, `443` or `8443`** from a consumer; Docker would refuse the second bind.

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

## pattern-e: web UI plus TCP and UDP media

The HTTPS part is pattern-a. The media part uses the two extra entrypoints that exist while the edge has
`[tls_edge.calls] enabled = true` (the default): `mattermost-calls-tcp` and `mattermost-calls-udp`, both
published on 8443.

| Label | What it does |
|---|---|
| `traefik.docker.network=ingress_public` | Which of the container's networks Traefik dials (matters when the container has several). |
| ``traefik.tcp.routers.pattern-e-media-tcp.rule=HostSNI(`*`)`` | TCP routers match on SNI. Media is opaque (no TLS terminated here), so `*` accepts everything. |
| `traefik.tcp.routers.pattern-e-media-tcp.entrypoints=mattermost-calls-tcp` | Bind to the TCP entrypoint on 8443 (the fallback transport). |
| `traefik.tcp.routers.pattern-e-media-tcp.service=...` and `traefik.tcp.services.pattern-e-media-tcp.loadbalancer.server.port=8443` | Which TCP service receives it and its port. |
| `traefik.udp.routers.pattern-e-media-udp.entrypoints=mattermost-calls-udp` | Bind to the UDP entrypoint on 8443 (the preferred transport). A UDP router has **no rule**: UDP carries no host name. |
| `traefik.udp.routers.pattern-e-media-udp.service=...` and `traefik.udp.services.pattern-e-media-udp.loadbalancer.server.port=8443` | The UDP service and its port. |

There is **one media pair per edge**: only one consumer may attach to it. The application must advertise
`<public host>:8443` to its clients, and the host or provider firewall must allow 8443/tcp and 8443/udp inbound.
