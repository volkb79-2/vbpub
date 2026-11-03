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

## Images (freshness rule)

`stable-11031`, all four images, pinned `tag@sha256` (manifest digests verified with
`docker buildx imagetools inspect`, 2026-11-04). Pushed to Docker Hub 2026-06-08 (149 days old).
It is the **newest stable tag Docker Hub carries**: GitHub has `stable-11248-3` (2026-11-02),
`stable-11248-1/-2` (2026-10-22/23) and `stable-11146-*` (2026-08), but Hub has no such tags, so
they cannot be pulled. Re-check Hub when revisiting; bump `image_tag` and `image_date`
together (a test enforces digest pinning and the 14-day rule against `checked_on`).

## Verified (2026-11-04)

Host-side smoke, see `/home/vscode/vbpub-release-program-20261027/reports/JITSI-PROJECT-REPORT.md`:
all four containers up (web, jvb, prosody healthy), prosody `internal_hashed` auth, 4 accounts
registered idempotently, `https://jitsi.<domain>/` served through the edge with a valid
certificate, conference page loads (pre-join), XMPP websocket upgrades (101) through the edge.
Not verifiable from the host: UDP 10000 reachability from outside and a real multi-party call.

## Tests

`tests/` (offline, stdlib + jinja2 + pytest; gate lane in `run-gate.toml`): render with a
dummy domain, `docker compose config` validation, no credentials/operator domain in tracked
files, secrets 0600 + gitignored, port plan equals `../README.md`, edge-consumer shape
(only `jitsi-web` on the edge network, one published port), digest pins + 14-day rule, and the
user hook against a fake docker (password only on stdin, never argv/stdout).
