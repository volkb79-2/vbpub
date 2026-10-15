# Consumers: Mattermost Calls media

The [README](../README.md) lists tls-edge's public surface. The
[design guide](DESIGN-GUIDE.md) records why Calls uses TCP and UDP forwarding
separate from HTTPS.

## What a Calls consumer needs

The Mattermost app must join `ingress_public`, expose its Calls listener to
that network, and advertise its public hostname and media port. Its Traefik
labels need one TCP router and one UDP router that point to the app's media
listener. The Mattermost stack in this repository provides those labels in
`mattermost-server/ciu.compose.yml.j2`; do not add a second media route for the
same app.

For a different self-hosted consumer, follow the same transport mapping:

- HTTP/HTTPS application traffic uses an HTTP router on `websecure`.
- TCP media needs a TCP router with a ``HostSNI(`*`)`` match on its own TCP
  entrypoint.
- UDP media needs a UDP router on a UDP entrypoint; UDP has no HTTP hostname
  rule.
- Expose the backend listener only to the shared ingress network, not with a
  host `ports:` mapping on the application container.

The live tls-edge service is deployed with host-level Docker Compose from
`/root/tls-edge-LIVE`. Edit `ciu-stack` templates and the tracked host override
in the repository, render with `scripts/render.sh`, copy the rendered config
to the host deployment, validate it with `docker compose config`, then run
`docker compose up -d` through `host-escape`. The deployed host does not run
CIU.

## Network/firewall requirements

For the Mattermost instance in this repository, allow inbound TCP and UDP 8443
to the host. Keep alternate HTTPS on TCP 9443 and the main HTTPS service on
TCP 443. A provider firewall and a host firewall can both filter the traffic;
check both layers. Docker-published traffic follows the host's forwarding
rules, so an egress or ingress rule for host processes alone may not cover it.

After deployment, test from a client network: open Mattermost, start a call
between two users, verify audio in both directions, and share a screen. Do not
infer media reachability from an HTTPS health check alone.
