# jitsi-server

Self-hosted **Jitsi Meet** as a ciu project: group calls, screen share, and (with the
Jitsi Meet Electron desktop client) remote control. Official `docker-jitsi-meet` images:
`web` (nginx + frontend), `prosody` (XMPP), `jicofo` (conference focus), `jvb` (video bridge).
Part of the [`voice-screen-share`](../README.md) area; own ciu root, own names.

- Client setup (Electron, remote control, noise suppression): [`CLIENT.md`](CLIENT.md)
- Accounts and rotation: [`USERS.md`](USERS.md)

## How it is wired

| Concern | Choice |
|---|---|
| HTTPS | Host-side **tls-edge** (`edge-traefik`, not modified). `jitsi-web` joins `ingress_public` with one router (`vss-jitsi-web`, entrypoint `websecure`, Host rule). Own hostname `jitsi.<domain>` and its own certificate (ACME at the edge). |
| Domain | `public_host = "jitsi.{{ env.PUBLIC_FQDN }}"` in `ciu.defaults.toml.j2`; no domain in tracked files. Override with a sparse `ciu.toml.j2` (`[jitsi] public_host = "..."`). DNS: `jitsi.<domain>` must resolve to the host (a wildcard record does it). |
| Media | JVB publishes **UDP 10000** directly on the host (`jvb.port`). The edge cannot carry it and is not changed. |
| TCP media fallback | **None.** The stable-11031 jvb image template has no TCP harvester settings, and the edge has no raw-TCP entrypoint for one. Clients that cannot reach UDP 10000 get no media. |
| Auth | Secure domain: `ENABLE_AUTH=1`, `AUTH_TYPE=internal`, `ENABLE_GUESTS=0` (everybody logs in). Users are registered by `hooks/post_compose_users.py` (`prosodyctl register`) on every `ciu up`. |
| Secrets | `GEN_LOCAL` under `.ciu/secrets/jitsi/` (gitignored, 0600). Only paths are ever printed. |
| Isolation | Own `internal` bridge; prosody/jicofo/jvb never join the edge network; only UDP 10000 is host-published. Per-service cpu/mem caps. |

## Firewall (required)

Open **UDP 10000** inbound to the host, in the host firewall AND the provider (netcup) firewall.
Docker-published traffic follows FORWARD rules, not host INPUT rules: check both layers. Example
(nftables/ufw syntax varies): `ufw route allow proto udp to any port 10000`. HTTPS needs nothing new
(443 is the edge's). Test media from an outside network: a 1:1 browser call can fall back to P2P, so
test with three participants or two plus screen share, and check audio both ways.

## Start / stop

```bash
ciu env generate --root-folder voice-screen-share/jitsi-server   # once (needs DOCKER_GID set if no docker group)
ciu up --dir voice-screen-share/jitsi-server --dry-run           # render + validate
ciu up --dir voice-screen-share/jitsi-server -y                  # start, register users
ciu down --dir voice-screen-share/jitsi-server
```

Like every ciu root, run `ciu up` from the main checkout path (S16.3 refuses to start a
worktree whose project does not yet exist in the primary checkout). A bare `ciu up` selects
nothing: always pass `--dir`.

## Images (freshness rule, with a recorded exception)

`ghcr.io/jitsi/{web,prosody,jicofo,jvb}:stable-11248-3`, pinned `tag@sha256` manifest-list digests
(`docker buildx imagetools inspect`, 2026-11-04; image build 2026-11-02). **14-day exception:** the
release is 2 days old; the operator explicitly prefers it for its security fixes (decision 2026-11-04;
`[jitsi.images] freshness_exception` in the defaults, enforced by the test). Docker Hub has no
`stable-11248*` tags (404), the official images are on GHCR. The previous pin was Hub
`stable-11031` (2026-06-08).

stable-11248 changed the image contract, which the templates follow: images run as the unprivileged user
`s6` (uid 1000, no chown of `/config`; hostdirs are `1000:1000` 0750), `/run` and `/tmp` are tmpfs (the
`/run` tmpfs needs `uid=1000,gid=1000`, upstream's `mode=1750` root-owned tmpfs is not writable for `s6`),
nginx listens on 8000 (not 80), prosody renders its config into `/run/prosody/config` and stores
accounts in `/var/lib/prosody` (hostdir `vol-prosody-data`). Still no TCP harvester for media.

## Verified (2026-11-04)

Started with `ciu up --dir voice-screen-share/jitsi-server -y` from the main checkout; all four containers
healthy; through the edge, `https://jitsi.<domain>/` answers 200 over HTTP/2 with a valid Let's Encrypt
chain, `/xmpp-websocket` upgrades (101), the pre-join page renders (pwmcp). From a second host (other
public IP): HTTPS, WebSocket, and UDP 10000 packets all arrive at the host and are forwarded to the jvb
container (iptables DNAT/FORWARD counters). JVB (ice4j single-port harvester) answers STUN only for a
known ICE ufrag, so a bare STUN request gets no reply even locally: reachability is shown by the counters,
media by a real multi-party call (still manual).

## devcontainer note (ciu defect)

In a devcontainer `ciu up` stops at S6.5 ("root helper cgroup parent ... is not verified loaded: systemd
is not PID 1") as soon as a hostdir needs an owner other than the operator. Workaround used: create
`vol-*-config` and `vol-prosody-data` once, `chown 1000:1000` / `chmod 0750` them with a one-shot root
container, then `ciu up` accepts them (S6.3). Filed in the ciu backlog.

## Tests

`tests/` (offline, stdlib + jinja2 + pytest; gate lane in `run-gate.toml`): render with a
dummy domain, `docker compose config` validation, no credentials/operator domain in tracked
files, secrets 0600 + gitignored, port plan equals `../README.md`, edge-consumer shape
(only `jitsi-web` on the edge network, one published port), digest pins + 14-day rule, and the
user hook against a fake docker (password only on stdin, never argv/stdout).
