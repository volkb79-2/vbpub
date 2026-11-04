# voice-screen-share

Area for self-hosted chat, voice, group calls, screen sharing and remote control: the
live Mattermost and the tryout projects that evaluate alternatives. Each project below is
its own ciu root (own instance id, names, volumes). Tryouts stay inside this area and
never take a port of a live project.

## Projects

| Project | Status | What |
|---|---|---|
| [`mattermost-server/`](mattermost-server/README.md) | live stack (moved into this area; phase 1 of `MIGRATION.md` applied in the main checkout, named-volume copy is phase 2) | Mattermost Team Edition + Calls (on by default), provisioned for nyxloom; media via tls-edge |
| [`mattermore/`](mattermore/README.md) | tryout, rendered/validated only, never started | Mattermost fork with group calls and other paywalled features enabled; isolated, no published ports |
| [`jitsi-server/`](jitsi-server/README.md) | rendered/validated, smoke-tested up 2026-11-04 (host-side only; the worktree start used compose directly, `ciu up` needs the project in the main checkout) | docker-jitsi-meet (web, prosody, jicofo, jvb): group calls, screen share, noise suppression, remote control via Jitsi Meet Electron (`CLIENT.md`); secure-domain auth with generated users (`USERS.md`); HTTPS at `jitsi.<domain>` via tls-edge, JVB UDP 10000 on the host |
| [`fluxer-server/`](fluxer-server/README.md) | tryout, running on its own network; web route via tls-edge needs a DNS name and `expose_public` | Fluxer (Discord-style chat, persistent voice channels, screen share, client-side RNNoise noise suppression); 21 containers, capped; media TCP 7891 / UDP 7892 |
| Mumble (planned) | not created | always-on voice channels (TeamSpeak-like), tiny |
| Matrix + LiveKit (planned) | not created | Synapse, Element Web/Cinny, LiveKit SFU, lk-jwt-service; replaces rather than adds to chat |
| RustDesk server (planned) | not created | remote-control sidecar usable with any chat |

New tryouts go below this folder, one ciu root each, following the `mattermore/` pattern:
own project name, no host ports by default, pinned tag@digest at least 14 days old, an
isolation test.

## Port plan

Public ports are claimed in one place so projects cannot collide. The edge (tls-edge) owns
80 (HTTP redirect / HTTP-01) and 443, and forwards exactly one raw media pair today. There
is no alternate HTTPS port (removed from tls-edge).

| Port | Proto | Owner | Purpose |
|---|---|---|---|
| 80 | TCP | tls-edge | HTTP redirect and ACME HTTP-01 challenge |
| 443 | TCP | tls-edge | HTTPS for every web UI (subdomain routing) |
| 8443 | TCP+UDP | tls-edge -> Mattermost Calls | media pair (UDP preferred, TCP fallback); same number 8443 inside the edge container for both protocols |
| none (default) | - | mattermore tryout | optional `media_host_port`, never one of the above |
| 10000 | UDP | Jitsi (`jitsi-server`) | JVB media (single port), published directly on the host (not via the edge); open it in host and provider firewall. No TCP fallback in the stable-11031 image |
| 64738 | TCP+UDP | Mumble (planned) | control + voice |
| 7881 TCP, 7882 UDP | | Matrix/LiveKit (planned) | LiveKit single-port mux; if not muxed the range UDP 50000-60000 |
| 7891 TCP, 7892 UDP | | Fluxer tryout (`fluxer-server/`) | LiveKit single-port mux, published directly on the host (no edge change); 7881/7882 stay with Matrix/LiveKit |
| 3478/5349 TCP+UDP, relay UDP 49152-65535 | | optional TURN (coturn) | only when direct media is blocked; the relay range is large: prefer LiveKit's built-in TURN first |
| 21115-21117 TCP, 21116 UDP | | RustDesk server (planned) | hbbs/hbbr (+21118/21119 for the web client) |

Planned rows (Mumble 64738 TCP+UDP; RustDesk 21115-21117 TCP, 21116 UDP, optional 21118/21119) match
the port tables in the `COMPARISON.md` port tables; nothing is created or published for them yet. Fluxer
and Jitsi rows above equal their compose/defaults (`fluxer livekit tcp_port/udp_port`, `jitsi.jvb.port`).

Security note: Jitsi's service secrets and the Mattermost/Mattermore database password are passed
through the container environment (the images take no `*_FILE` form), so anyone with docker access
(`docker inspect`) can read them; the `0444` secret file relies on `.ciu/` being 0700.

Rules: prefer single UDP ports over ranges; a second raw media service needs either new
Traefik TCP/UDP entrypoints in tls-edge (a tls-edge change and release, one backend per UDP
entrypoint) or a directly published host port plus a host/provider firewall rule (check
both layers; Docker-published traffic follows forwarding rules, not host OUTPUT rules). Never
use host networking as a fix for a container that will not start.

## Options summary (research, 2026-11-03/04)

Source: the self-hosted comms options evaluation (capability matrix, image tags, ciu sketches).
What the operator wants: markdown chat plus voice, screen sharing, group calls, and what
Mattermost lacks: remote control and noise suppression.

- Noise suppression and remote control are client-side almost everywhere; a server choice
  does not provide them. Jitsi Meet Electron (>= 2026.8.0) is the verified option with group
  calls, screen share, RNNoise and remote control in one stack (Apache-2.0).
- Mumble is the closest TeamSpeak/Discord feel (join a channel, you are talking) at negligible
  cost, but has no screen share or markdown chat.
- Matrix + Element Call/LiveKit (Cinny for a Discord-like client) gives persistent chat, group
  calls and E2EE; the most moving parts; replaces Mattermost.
- Zulip + Jitsi, Nextcloud Talk, Rocket.Chat+Jitsi, MiroTalk: lower fit (weight, licence or
  meetings-only).
- RustDesk OSS server is the remote-control sidecar for any chat.
- Mattermore (this area's `mattermore/`): the report marked it unverified; it is verified
  now (AGPL-3.0, active, group calls via `MM_CALLS_GROUP_CALLS_ALLOWED`), with single-maintainer
  and rebase-lag risk.
- Order of tryouts: Jitsi, Mumble, Matrix/LiveKit, RustDesk; pin images by digest after the
  14-day check.

The full report is kept with the release-program reports
(`SELF-HOSTED-COMMS-OPTIONS-2026-11-04.md`).

## Calls defaults

Calls is ON by default in both places and they agree: `mattermost-server`
(`[mattermost.calls] enabled = true, port = 8443, public_port = 8443`) and tls-edge
(`[tls_edge.calls] enabled = true, public_port = 8443`, TCP and UDP; no alternate HTTPS port). Tests in
`mattermost-server/tests/test_calls_defaults_agree.py` and
`tls-edge/tests/test_calls_defaults_agree.py` pin the agreement.
