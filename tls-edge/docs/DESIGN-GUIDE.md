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
protocols use the same public port and the same container port: the Calls TCP
entrypoint listens on container TCP 8443 and the Calls UDP entrypoint on
container UDP 8443. There is no alternate HTTPS port, so nothing else uses 8443
and there is no TCP/UDP collision.

The listener is on in tls-edge defaults (`[tls_edge.calls] enabled = true,
public_port = 8443`), matching the Mattermost project's default media port. An
edge that must not open a media port sets `enabled = false`. The agreement
between the two defaults is pinned by `tests/test_calls_defaults_agree.py`.

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
