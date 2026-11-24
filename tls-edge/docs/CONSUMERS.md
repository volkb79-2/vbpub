# Consumers: Jitsi Meet and Mattermost Calls

The [README](../README.md) lists tls-edge's public surface. The
[design guide](DESIGN-GUIDE.md) records why Calls uses TCP and UDP forwarding
separate from HTTPS.

## Jitsi Meet web and signaling

The repository's [Jitsi Meet deployment](https://github.com/volkb79-2/vbpub/tree/main/voice-screen-share/jitsi-server)
uses the ordinary HTTP consumer contract: `jitsi-web` joins `ingress_public`,
routes `jitsi.<domain>` on `websecure`, and serves plain HTTP on container port
8000. The edge terminates TLS and handles HTTP/2; when HTTP/3 is enabled at the
edge, the same router is available over QUIC without Jitsi-specific labels.
WebSocket signaling uses that same host router.

Jitsi Videobridge media is a separate path: JVB publishes UDP 10000 directly on
the host. That port does not pass through Traefik and does not require the
Mattermost Calls preset on 8443. Jitsi's registered test suite renders the
consumer Compose template and compares its route with tls-edge's rendered
default entrypoint.

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
bundled ciu (`cd /srv/tls-edge-instance/stack && ./ciu up`); see [INSTALL.md](INSTALL.md). Its
Traefik container is `tls-edge-<id>-traefik`. The standalone renderer is only a disposable trial
and is not a deployment path.

## Network/firewall requirements

For the Mattermost instance in this repository, allow inbound TCP and UDP 8443
to the host, alongside TCP 443 (HTTPS) and TCP 80 (redirect, ACME HTTP-01). There is no alternate
HTTPS port. A provider firewall and a host firewall can both filter the traffic;
check both layers. Docker-published traffic follows the host's forwarding
rules, so an egress or ingress rule for host processes alone may not cover it.

After deployment, test from a client network: open Mattermost, start a call
between two users, verify audio in both directions, and share a screen. Do not
infer media reachability from an HTTPS health check alone.
