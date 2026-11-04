# fluxer-server (tryout)

A ciu project that runs **Fluxer** (<https://github.com/fluxerapp/fluxer>, AGPL-3.0): a Discord-style chat
server with persistent voice channels and screen share, built on LiveKit. It is a tryout in
`voice-screen-share/` next to `mattermost-server/` (live) and `mattermore/`; it never takes a port of those.

## Why Fluxer (verified 2026-11-04)

- **Noise suppression: yes, client-side neural, on by default.** The web and desktop client run an
  RNNoise WebAssembly model in an AudioWorklet before LiveKit publishes the microphone (backends: none,
  WebRTC standard, noise gate, Speex, **RNNoise (default)**, GTCRN, DeepFilterNet3). Nothing to configure on
  the server. Evidence with file:line: `reports/FLUXER-VERIFICATION-2026-11-04.md` of the release program.
- Also: echo cancellation and auto gain (default on), voice activity detection, push-to-talk, persistent voice
  channels, screen share (optionally with system audio).
- Not provided: remote control of a shared screen (keep RustDesk or Jitsi Electron for that), true end-to-end
  encryption of calls, mobile-app behaviour unverified.
- Young project (repo created 2026-01-01), per-service CalVer releases several times a day.

## What runs

21 containers on their own bridge network `fluxer` (the compose project is `fluxer-<instance id>`):
`edge` (Caddy, plain HTTP :8080, not published), `postgres`, `valkey`, `nats`, `meilisearch`, `seaweedfs`
(+ `seaweedfs-init`, exits 0 after creating buckets), `livekit`, and the Fluxer services `api`, `worker`,
`gateway`, `media-proxy`, `push`, `static-proxy`, `app-proxy`, `snowflakes`, `users`, `gifs`, `messages`,
`unfurl`, `admin`. Measured idle memory with the caps below: about 1 GiB in total.

## Pins and the 14-day rule

| Item | Pin | Note |
|---|---|---|
| Fluxer bundle | `fluxer-release@2026.1102.205049` (published 2026-11-02) | **14-day exception, recorded 2026-11-04:** no bundle line is 14 days old yet (the coherent `fluxer-release@` line only exists since 2026-10-24, oldest 2026.1024.194841). The newest bundle is pinned anyway as a tryout (operator decision). Re-pin to the newest bundle that is at least 14 days old when one exists. |
| First-party images (12) | tag@digest from the bundle's release manifest | same digests the bundle compose pins |
| caddy | `2.11.6-alpine` (2026-10-04) | 2.11.7 is 5 days old |
| postgres | `16.14-alpine` (2026-07-08) | 16.15 is 9 days old |
| valkey | `9.1.2-alpine` (2026-09-21) | 9.1.3 is 8 days old |
| nats | `2.14.7-alpine` (2026-09-18) | 2.14.8 is 13 days old, one day short |
| meilisearch | `v1.53.3` (2026-10-01) | the bundle's minor; v1.55/v1.56 are other minors, v1.56 is 9 days old |
| seaweedfs | `4.48` (2026-09-28) | 4.50 is 10 days old |
| livekit | `v1.12.0` (2026-06-08) | the bundle's tag; v1.13.10 is 21 days old but a different minor, revisit |

The third-party images are pinned by digest (`tag@sha256:...`); the bundle itself only pins floating tags
for them, so they were re-resolved against the registry. Dates are Docker Hub "last updated" on 2026-11-04.
All pins live in `ciu.defaults.toml.j2` (`[fluxer.images]`).

## Isolation contract (pinned by `tests/test_fluxer_project.py`)

- Own project name `fluxer`, container prefix `fluxer-<instance id>`, own network and own named volumes.
- **Only two host ports: LiveKit media, `7891/tcp` and `7892/udp`** (claimed in `../README.md`'s port plan;
  `7881/7882` stay with Matrix/LiveKit). Nothing else is published, and none of 80/443/8443/8444/9443.
- Web traffic is reached through tls-edge only (see below), never through a published port.
- Per-service memory/CPU caps from `[fluxer.limits]` (postgres 1 GiB, api/worker 768 MiB, gateway and
  media-proxy 384 MiB, meilisearch/seaweedfs 512 MiB, the rest 128-256 MiB; bundle defaults were 5 GiB for
  postgres alone). The whole project sits under `dev-background.slice` (the estate convention, global
  `[governance]`).

## Public access via tls-edge (consumer pattern, `tls-edge/docs/CONSUMERS.md`)

Fluxer is a normal HTTPS consumer with **its own host name and certificate**. Nothing in tracked files names a
domain: `public_host` defaults to `fluxer.<PUBLIC_FQDN>` and is a plain config value.

1. Create a DNS A record for `public_host` pointing at the host (the edge issues the certificate for it; if
   the edge uses a wildcard, nothing else is needed).
2. `[fluxer] expose_public = true` is the default (set to false in a sparse ciu.toml.j2 to keep the stack private). The Caddy `edge`
   service then joins `ingress_public` and carries the Traefik labels (`Host(<public_host>)`, entrypoint
   `websecure`, backend port 8080, router `fluxer-vss`). Caddy runs in the official behind-proxy mode (plain
   HTTP, `X-Forwarded-For` taken from private ranges).
3. Open `7891/tcp` and `7892/udp` in the provider firewall and the host firewall (both layers).
   `livekit` discovers the public IP over STUN (`use_external_ip = true`); for a LAN test set
   `[fluxer.livekit] node_ip`.

No change to the live edge is required for the HTTPS part: the labels are the documented pattern. Things to
check on the live Traefik config before relying on it (not edited here): the upstream proxy contract in
`operator/reverse-proxy.mdx` wants WebSocket upgrade (Traefik does it natively), 512 MB request bodies and a
1 h idle timeout. tls-edge's `traefik.yml` sets no `respondingTimeouts`, so Traefik's defaults apply: a read
timeout or the 180 s idle timeout could cut large uploads or idle sockets; verify with a large upload and a
long voice session. The edge adds no CSP header (good: Fluxer sets its own) and only `secure-headers`.

## Clients and server URL (links only, checked 2026-11-04)

- Server / web client: `https://fluxer.<PUBLIC_FQDN>/` (the `public_host`; sign-in is username + password,
  accounts in `USERS.md`; the credentials file is `.ciu/secrets/accounts.json`, never in git).
  Instance discovery document: `https://fluxer.<PUBLIC_FQDN>/.well-known/fluxer`.
- Official download page: <https://fluxer.com/download> (verification help: <https://fluxer.com/help/verify-fluxer-download>).
  Installers are NOT GitHub release assets (the `fluxer-desktop-stable@...` releases carry only update
  modules and a release manifest); they come from the rolling `latest` URLs of the package host:
  Windows x64 <https://pkgs.fluxer.com/desktop/stable/win32/x64/latest/setup>, Windows arm64
  <https://pkgs.fluxer.com/desktop/stable/win32/arm64/latest/setup>, macOS Apple silicon
  <https://pkgs.fluxer.com/desktop/stable/darwin/arm64/latest/dmg>, macOS Intel
  <https://pkgs.fluxer.com/desktop/stable/darwin/x64/latest/dmg>, Linux Flatpak
  <https://flathub.org/apps/app.fluxer.Fluxer> or the apt repository on the download page.
- How the desktop app is pointed at a self-hosted instance is not verified here: try the web client first
  (it talks to its own origin); for the desktop app look for an instance/server field on the sign-in screen.

## Running

```bash
cd /workspaces/vbpub
ciu env generate --root-folder voice-screen-share/fluxer-server     # creates ciu.env + a network (CIU-162)
ciu up --dir voice-screen-share/fluxer-server --yes --root-folder voice-screen-share/fluxer-server
```

`ciu up` runs the `pre_compose` hook (generates every secret once, see below), starts the stack and then the
`post_compose` hook (admin + dummy users, see `USERS.md`). Checks: `docker ps` shows all containers
`healthy` (`seaweedfs-init` exited 0), `GET /_health` on the edge answers `OK`.

Known tool issues (filed in the ciu backlog): `ciu env generate` attaches the devcontainer to a new network
(CIU-162; detach it by exact name afterwards), a root that exists only in a linked worktree cannot `ciu up`
(CIU-167; run it from the main checkout), and the S6.5 root helper refuses to run in a devcontainer
(see the jitsi-server README; this project uses named volumes and is not affected). `ciu up` from the main
checkout was verified on 2026-11-04 with `expose_public = true`: 21 containers up, accounts created, the edge
issued the certificate for `fluxer.<PUBLIC_FQDN>`.

External reachability (from a second host with another public IP, 2026-11-04): HTTPS with a valid Let's
Encrypt chain and HTTP/2, WebSocket upgrade on `/gateway` (101), TCP 7891 connect. UDP 7892: every probe
packet reached the host and was forwarded into the livekit container (iptables DNAT/FORWARD counters); LiveKit
does not answer a bare STUN request, so a real two-client call is still the acceptance test.

## Secrets

The images read all secrets from environment variables, and several need a fixed format (standard base64,
a P-256 VAPID pair), so `hooks/pre_compose_secrets.py` owns them instead of `[fluxer.secrets]` directives:
`.ciu/secrets/fluxer-secrets.json` (generated once, never regenerated) and derived per-role env files
(`env.fluxer`, `env.api`, `env.postgres`, ...), all mode 0600 in a 0700, gitignored directory, loaded by
compose with `env_file`. No secret is in a tracked file, a rendered compose, the log or this document; tests
assert it. `FLUXER_SETUP_TOKEN` is generated there too and used by the accounts hook.

## Accounts

`hooks/post_compose_accounts.py` creates the administrator and three dummy users idempotently over the
registration API, from inside the api container (no browser step). Where the credentials are, how to rotate
them and what stays manual: `USERS.md`.

## Tests

```bash
cd voice-screen-share/fluxer-server && python -m pytest tests -q
```

Offline: isolation, only the LiveKit pair published, digest pins, caps, port plan agreement with `../README.md`,
no credentials or domain in tracked files, hook idempotence and file modes, `docker compose config` when
docker is present. Gate lane: `run-gate.toml`.

## Manual acceptance (needs two clients, not done headlessly)

1. Sign in as two dummy users in two browsers (Chrome/Firefox) at `https://<public_host>`.
2. Create a community and a voice channel, join from both, verify audio both ways and that the microphone
   setting shows **RNNoise** (Settings > Voice).
3. Share a screen; verify the other client sees it, both over UDP 7892 and with UDP blocked (TCP 7891 fallback).
4. Check the admin panel at `https://<public_host>/admin`.

## Upstream files

`Caddyfile` is the bundle's, minus the two `conf.d` imports. The compose template is derived from the bundle's
`docker-compose.yml` (every empty `${X:-}` default dropped, the rexctl guard removed, ports and limits changed).
Upstream bundle: <https://fluxer.dev/compose/latest>, docs: <https://docs.fluxer.com/operator/>.
