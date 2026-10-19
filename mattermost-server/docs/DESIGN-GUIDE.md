# Mattermost deployment design

This note records the decisions behind the reachable Mattermost endpoint,
its managed membership, and the Calls media path. The feature summary is in
the [README](../README.md); client steps are in the
[consumer guide](CONSUMERS.md).

## Public service, managed membership

The server must be reachable over HTTPS by browser, desktop, and mobile
clients that are not on the Docker host. `expose_public = true` attaches the
app to `ingress_public`, and tls-edge publishes the HTTPS router. “Internal
use” means membership and credentials are issued by us; it does not mean the
network endpoint is private. Mattermost user creation is disabled, and CIU
provisions only the declared accounts. The endpoint still needs normal
internet-facing maintenance, security updates, and access controls.

## Calls media path

Mattermost's integrated Calls plugin supplies audio and screen sharing in the
Mattermost server container. This keeps the signaling and media service under
the same operator and data boundary as the chat server; no separate RTCD or
TURN service is configured. Mattermost documents the integrated service as
self-hosted and says call media and metadata do not traverse a third-party
system. See the [Calls deployment guide](https://docs.mattermost.com/deployment-guide/calls/calls-deployment-guide).

The public HTTPS/WebSocket route remains on TCP 443. Calls media uses public
TCP and UDP 8443, with UDP preferred and TCP as a fallback. Both edge routers
forward to port 8443 inside the Mattermost container. On this host, HTTPS-alt
uses public TCP 9443 and Traefik's internal TCP 8443. Therefore Traefik's
Calls TCP entrypoint uses internal TCP 8444; UDP 8443 does not conflict with
TCP 8443 because these are separate protocols.

The Calls plugin advertises the configured public hostname and port so clients
can connect to this host directly. This avoids the server having to discover
its public address through STUN. The plugin's default ICE server configuration
may still let clients use Mattermost's STUN service for NAT traversal; STUN
exchanges connection addresses, not chat content. Networks that block both
direct UDP and TCP media would need a separately operated TURN relay, which
this deployment does not provide.

## Data and outbound traffic

Chat posts, files, and the database stay on this host's Mattermost/PostgreSQL
stack. The automatic requests currently configured are narrower:

- Product notices are fetched by HTTPS GET from
  `notices.mattermost.com` once per hour. The server receives notice
  definitions; the remote site can see the host IP and request timing.
- `EnableSecurityFixAlert` is false, so the server does not send the daily
  diagnostic summary used for security-fix alerts.
- `EnableDiagnostics` is false, so server errors and panics are not sent to
  Mattermost-hosted Sentry.
- Marketplace access and plugin uploads are disabled. Calls is the only
  enabled bundled plugin.

Some user-triggered features intentionally contact other services. Link
previews can request a URL posted in a message; the GIF picker uses GIPHY and
can send its search to that service. Outgoing webhooks are disabled in this
stack. These feature paths should be evaluated separately from automatic
upload of conversations, which is not configured here.

These configuration facts are not proof that the process makes no other
outbound connections. A host-level capture on the Docker bridge or the
Mattermost container's veth can show destinations, timing, and byte counts;
DNS logs can associate names with new connections. HTTPS payloads remain
encrypted, so a packet capture alone cannot certify the content of an
encrypted request. An egress firewall belongs in the host's Docker forwarding
path (for example, its `DOCKER-USER`/nftables forward rules), not only in the
host process `OUTPUT` chain. This change does not add a deny rule; doing so
requires an explicit allowlist for the documented service needs.

## Source availability and upstream review

The deployed server tag is [Mattermost v11.10.1 source](https://github.com/mattermost/mattermost/tree/v11.10.1).
Mattermost describes the project as open-core and says its compiled core
releases are MIT-licensed. Mattermost also reports manual and automated source
reviews, penetration testing, and a responsible-disclosure/bug-bounty process
in its [security overview](https://mattermost.com/platform/security/). Those
are upstream-reported practices; they do not establish that this exact image
tag was independently audited or that our deployment configuration has been
audited by Mattermost.

## Public endpoint and rollback

The current endpoint is public by design. To remove it, set
`mattermost.expose_public = false` in the source config and deploy with the
documented CIU workflow. That removes the HTTPS route and makes the app
unavailable to off-host clients; it does not change who may hold an account.
