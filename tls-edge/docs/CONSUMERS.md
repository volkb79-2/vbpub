# Consumers: HTTPS and application-published media

The [README](../README.md) lists tls-edge's public surface. HTTP and HTTPS
traffic uses the ordinary shared `websecure` entrypoint. Raw L4 media is
published directly by the application; examples include Jitsi Videobridge on
UDP 10000 and Mattermost Calls on TCP+UDP 8443.

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
uses the HTTP consumer contract: `jitsi-web` joins `ingress_public`, routes
`jitsi.<domain>` on `websecure`, and serves plain HTTP on container port 8000.
The edge terminates TLS and handles HTTP/2; when HTTP/3 is enabled at the edge,
the same router is available over QUIC without Jitsi-specific labels. WebSocket
signaling uses that same host router.

Jitsi Videobridge media is separate: JVB publishes UDP 10000 directly on the
host and advertises the host's public address. That port does not pass through
Traefik. Jitsi's registered test suite compares its network, entrypoint and
published ports with the rendered consumer contract.

## Direct media publishing

An application that serves raw TCP or UDP media publishes its own host port
pair and advertises that same public endpoint to clients. It does not declare
Traefik TCP/UDP routers or labels. The Mattermost stack in this repository
publishes its Calls media listener directly; Jitsi publishes JVB UDP directly.

The firewall must allow the application's public media ports in addition to
the edge's HTTPS ports. Docker-published media follows the host's forwarding
rules, so a host firewall rule that only covers host processes may not be
enough.

The tls-edge service is installed as a get.py instance in `/srv/tls-edge-instance` and run with the
bundled ciu (`cd /srv/tls-edge-instance/stack && ./ciu up`); see [INSTALL.md](INSTALL.md). Its
Traefik container is `tls-edge-<id>-traefik`. The standalone renderer is only a disposable trial
and is not a deployment path.

The live host still runs the hand-made `/root/tls-edge-LIVE` stack with `edge-traefik` until the cut-over
described in [INSTALL §8](INSTALL.md#8-migrate-from-a-hand-made-deployment-plan-not-executed).

## Network/firewall requirements

Allow every value in `TLS_EDGE_PUBLIC_PORTS`, plus any port an application
publishes directly: Jitsi's JVB UDP 10000 and Mattermost Calls TCP/UDP 8443.
Use `tls-edge show --json` for the edge's authoritative public-port list, and
the application's own configuration for direct media ports. A provider
firewall and a host firewall can both filter the traffic;
check both layers. Docker-published traffic follows the host's forwarding
rules, so an egress or ingress rule for host processes alone may not cover it.

After deployment, test from a client network: open Mattermost, start a call
between two users, verify audio in both directions, and share a screen. Do not
infer media reachability from an HTTPS health check alone.
