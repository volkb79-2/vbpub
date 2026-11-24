# Consumers: HTTPS and application-published media

The [README](../README.md) lists tls-edge's public surface. HTTP and HTTPS
traffic uses the ordinary shared `websecure` entrypoint. Raw L4 media is
published directly by the application; examples include Jitsi Videobridge on
UDP 10000 and Mattermost Calls on TCP+UDP 8443.

## Jitsi Meet web and signaling

The repository's [Jitsi Meet deployment](https://github.com/volkb79-2/vbpub/tree/main/voice-screen-share/jitsi-server)
uses the HTTP consumer contract: `jitsi-web` joins `ingress_public`, routes
`jitsi.<domain>` on `websecure`, and serves plain HTTP on container port 8000.
The edge terminates TLS and handles HTTP/2; when HTTP/3 is enabled at the edge,
the same router is available over QUIC without Jitsi-specific labels. WebSocket
signaling uses that same host router.

Jitsi Videobridge media is separate: JVB publishes UDP 10000 directly on the
host and advertises the host's public address. That port does not pass through
Traefik. Jitsi's registered test suite renders the consumer Compose template
and compares its HTTP route with tls-edge's rendered default entrypoint.

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
bundled ciu (`cd /srv/tls-edge-instance/stack && ./ciu up`); see [INSTALL.md](INSTALL.md). Until
the live host is migrated (the plan is in INSTALL.md, section "Migrate from a hand-made
deployment") it still runs host-level Docker Compose from `/root/tls-edge-LIVE`, rendered from
`edge-proxy/` with a site override; there the container is `edge-traefik`, in an instance it is
`tls-edge-<id>-traefik`.

## Network/firewall requirements

Allow the edge's public HTTPS/HTTP ports and any application-published media
ports. For the Mattermost instance in this repository that adds direct TCP and
UDP 8443 alongside edge TCP 443 (HTTPS) and TCP 80 (redirect, ACME HTTP-01).
There is no alternate HTTPS port. A provider firewall and a host firewall can
both filter the traffic; check both layers.

After deployment, test from a client network: open Mattermost, start a call
between two users, verify audio in both directions, and share a screen. Do not
infer media reachability from an HTTPS health check alone.
