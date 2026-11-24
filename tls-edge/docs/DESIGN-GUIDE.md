# tls-edge design: HTTPS edge, application-published media

The feature surface is listed in the [README](../README.md). The
[consumer guide](CONSUMERS.md) explains HTTP routing and direct media
publication.

## Why raw media bypasses Traefik

Traefik's HTTP routers terminate HTTPS on TCP 443. WebRTC media is opaque
TCP/UDP traffic, so it cannot use an HTTP router or the TLS certificate
entrypoint. The application owns that listener and publishes it directly on
the host, then advertises the same public endpoint to its clients.

This keeps application-specific ports and protocols out of the generic edge.
Jitsi Videobridge uses this model for UDP 10000, and Mattermost publishes its
Calls listener directly on TCP+UDP 8443. The edge has no TCP/UDP media
entrypoints and does not forward layer-4 traffic.

## Privacy and operating boundary

The application, not Traefik, accepts the media packets. This does not deploy
a third-party media server. A self-hosted media service should add a TURN relay
only when user networks cannot reach the application directly, and operate
that relay under the same privacy requirements.

The host and provider firewalls must allow each application's published media
ports. The exact rule must match the host's firewall backend and Docker
forwarding policy. Do not assume a host `OUTPUT` rule filters traffic forwarded
by Docker.
