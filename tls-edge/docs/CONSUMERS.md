# Consumers: Jitsi Meet and Mattermost Calls

The [README](../README.md) lists tls-edge's public surface. The
[design guide](DESIGN-GUIDE.md) records why Calls uses TCP and UDP forwarding
separate from HTTPS.

## Rendered consumer contract

`tls-edge configure` writes the effective values below to
`stack/consumer.env`. Consumers and their tests use those exact strings; they
do not load tls-edge defaults or renderer internals. `tls-edge show --json`
returns the same mapping under `contract`, together with the structured port
plan and the current `consumer_env` state.

| Key | Effective value | Example |
|---|---|---|
| `TLS_EDGE_DOMAIN` | normalised `domain`; empty when unset | `example.test` |
| `TLS_EDGE_NETWORK` | `ingress_network` | `ingress_public` |
| `TLS_EDGE_HTTPS_ENTRYPOINT` | the HTTPS entrypoint carrying the resolver | `websecure` |
| `TLS_EDGE_HEADERS_MIDDLEWARE` | the middleware named by that entrypoint | `secure-headers@file` |
| `TLS_EDGE_HTTP3` | `true` or `false` | `false` |
| `TLS_EDGE_PUBLIC_PORTS` | sorted `<port>/<protocol>` values, comma-joined | `80/tcp,443/tcp,443/udp` |

`tls-edge validate` and CIU's preflight hook refuse when an existing
`consumer.env` is missing an owned key or differs from the effective
configuration. The message names the drifted keys and says
`run tls-edge configure to re-sync`; `configure` rewrites only the six owned
lines and keeps every other line. An absent `consumer.env` in a checkout is
not treated as drift. `tls-edge show` reports the same state without failing.

## Jitsi Meet web and signaling

The repository's [Jitsi Meet deployment](https://github.com/volkb79-2/vbpub/tree/main/voice-screen-share/jitsi-server)
uses the ordinary HTTP consumer contract: `jitsi-web` joins `ingress_public`,
routes `jitsi.<domain>` on `websecure`, and serves plain HTTP on container port
8000. The edge terminates TLS and handles HTTP/2; when HTTP/3 is enabled at the
edge, the same router is available over QUIC without Jitsi-specific labels.
WebSocket signaling uses that same host router.

Jitsi Videobridge media is a separate path: JVB publishes UDP 10000 directly on
the host. That port does not pass through Traefik and does not require the
Mattermost Calls preset on 8443. Jitsi's registered test suite compares its
network, entrypoint and published ports with the rendered consumer contract.

## What a Calls consumer needs

The Mattermost app must join `ingress_public`, expose its Calls listener to
that network, and advertise its public hostname and media port. Its Traefik
labels need one TCP router and one UDP router that point to the app's media
listener. The Mattermost stack in this repository provides those labels in
`voice-screen-share/mattermost-server/ciu.compose.yml.j2`; do not add a second media route for the
same app.

For a different self-hosted consumer, follow the same transport mapping:

- HTTP/HTTPS application traffic uses an HTTP router on `websecure`.
- TCP media needs a TCP router with a ``HostSNI(`*`)`` match on its own TCP
  entrypoint.
- UDP media needs a UDP router on a UDP entrypoint; UDP has no HTTP hostname
  rule.
- Expose the backend listener only to the shared ingress network, not with a
  host `ports:` mapping on the application container.

The tls-edge service is installed as a get.py instance in `/srv/tls-edge-instance` and run with the
bundled ciu (`cd /srv/tls-edge-instance/stack && ./ciu up`); see [INSTALL.md](INSTALL.md). Until
the live host is migrated (the plan is in INSTALL.md, section "Migrate from a hand-made
deployment") it still runs host-level Docker Compose from `/root/tls-edge-LIVE`, rendered from
`edge-proxy/` with a site override; there the container is `edge-traefik`, in an instance it is
`tls-edge-<id>-traefik`.

## Network/firewall requirements

Allow every value in `TLS_EDGE_PUBLIC_PORTS`, plus any port an application
publishes directly (Jitsi's JVB UDP 10000 and, when Mattermost publishes Calls
media directly (tls-edge Calls preset off), its TCP/UDP 8443). Use `tls-edge
show --json` for the authoritative list. A provider firewall and a host
firewall can both filter the traffic;
check both layers. Docker-published traffic follows the host's forwarding
rules, so an egress or ingress rule for host processes alone may not cover it.

After deployment, test from a client network: open Mattermost, start a call
between two users, verify audio in both directions, and share a screen. Do not
infer media reachability from an HTTPS health check alone.
