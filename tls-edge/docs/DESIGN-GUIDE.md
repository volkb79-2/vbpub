# tls-edge design: Mattermost Calls media

The feature surface is listed in the [README](../README.md). The
[consumer guide](CONSUMERS.md) explains which labels and network ports a
consumer needs.

## Why Calls uses a separate L4 route

Traefik's existing HTTP routers terminate HTTPS for Mattermost on TCP 443.
Calls media is raw WebRTC media traffic, so it cannot use an HTTP router or
the TLS certificate entrypoint. The Mattermost service therefore advertises
and exposes its own Calls listener, while Traefik's TCP and UDP routers
forward the two transports on the shared ingress network.

UDP 8443 is the preferred media transport; TCP 8443 is the fallback. Both
protocols use the same public port. On this host, alternate HTTPS already
uses the Traefik container's TCP port 8443 but is published externally on
9443. The Calls TCP entrypoint therefore listens on container TCP 8444 and
the host publishes 8443/TCP to it. Calls UDP 8443 is published to the
Traefik container's 8443/UDP socket. There is no TCP/UDP port collision.

The listener is optional and disabled in tls-edge defaults. The tracked host
override enables it for the Mattermost deployment. Other tls-edge installs
do not open a media port unless their own override enables the feature.

## Privacy and operating boundary

The edge routes only the Calls media packets to the Mattermost container.
This does not deploy a third-party media server. Mattermost's integrated
Calls mode is self-hosted; use a TURN relay only when user networks cannot
reach the media service directly, and operate that relay under the same
privacy requirements if one is added later.

An inbound allow rule for public TCP and UDP 8443 is needed in any host or
provider firewall that filters Docker-published traffic. The exact rule must
match the host's existing firewall backend and Docker forwarding policy. Do
not assume a host `OUTPUT` rule filters traffic forwarded by Docker.
