# mattermore (tryout)

A ciu tryout project for **Mattermore** (<https://github.com/dennisklappe/mattermore>,
site mattermore.dev), a free Mattermost fork that turns paywalled features back on.
It is isolated from the live Mattermost (`../mattermost-server`) and the live
tls-edge by construction; `tests/test_isolation.py` pins that.

Retained research: [voice and transcription analysis](docs/VOICE-ANALYSIS-2026-11-04.md).

## What Mattermore is (researched 2026-11-04)

| Question | Finding |
|---|---|
| Repository / licence | `dennisklappe/mattermore`, AGPL-3.0 (image label `AGPL-3.0-only`). Not affiliated with Mattermost, Inc. A successor patch-set fork `vibeci/MaintainedMost` builds on it. |
| Method | Patch files applied to a pinned upstream tag (`upstream.env`): server `v12.0.1`, Calls plugin `v1.12.6`, transcriber `master`. A patch that fails on a new upstream release is dropped for that release. Calls plugin version is `1000.<upstream>` so a server upgrade never replaces it with official Calls. |
| What it unlocks | Group audio calls (3+), call recording and local transcription (via a `calls-offloader` job service), OIDC SSO, guest accounts, no user cap, full message history. Group **video** is documented as "being ported", not in the current release. LDAP/SAML/Google/Office365 stay locked (private upstream module). |
| Calls without the limit | Yes: the server and plugin honour `MM_CALLS_GROUP_CALLS_ALLOWED=true` (set in the image and in this project). Verify empirically with 3 accounts before relying on it. |
| Image | `ghcr.io/dennisklappe/mattermore`, single-arch linux/amd64, user `mattermost`, ports 8065, 8067, 8074; expects Calls media on 8443 TCP+UDP. Tags `v1.0.0 ... v1.6.2` and `latest`. |
| Releases | v1.6.2 (2026-10-23), v1.6.1 (2026-10-17), v1.6.0 (2026-10-15), v1.5.x (2026-10-06/07) ... ~10 releases in two months; repo pushed 2026-10-23; 1 open issue; one open issue about a v11.11.1 rebase. Small project (9 stars): single maintainer risk. |
| Data compatibility | Built on Mattermost v12; the live project runs Team Edition 11.10.1. Do not point it at the live database. It has its own Postgres here. |

## Pins (14-day freshness rule)

- App: `ghcr.io/dennisklappe/mattermore:v1.6.1@sha256:0021adfc29d331266ff2089c6b31fc86c65ca6d9254d0373e79f511b24293abd`
  (published 2026-10-17, 17 days old on 2026-11-04). v1.6.2 (2026-10-23, 12 days) was skipped
  for the vetting buffer; both carry Calls plugin 1000.12.6. Re-pin to the newest release at
  least 14 days old when revisiting.
- Postgres: `postgres:17.10-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193`
  (the rolling `17-alpine` was rebuilt 2026-10-26, inside the window).

## Isolation contract

- Own project name `mattermore`, container prefix `mattermore-<instance id>`, own bridge
  network and own named volumes; the instance id is derived from this folder's physical
  path, so it differs from `mattermost-server`'s.
- No host ports published, no `ingress_public`, no tls-edge Calls routers. The live edge owns
  public TCP+UDP 8443 for the live Mattermost.
- `[mattermore.tryout] media_host_port` (default `0`) can publish the Calls pair on ANOTHER host
  port for a LAN experiment; never 80/443/8443/8444/9443. Starting it needs explicit approval.
- `expose_public = true` joins the shared ingress network under a distinct router name
  (`mattermore-tryout`) for HTTPS only; media is not routed through the edge.

## Use

```bash
cd /workspaces/vbpub
ciu env generate --root-folder voice-screen-share/mattermore     # creates this project's OWN network
ciu up --dir voice-screen-share/mattermore --yes --dry-run --root-folder voice-screen-share/mattermore
```

Status: rendered and validated only (`ciu up --dry-run`: render, secrets, governance, leak
scan all pass); never started. A first start, if approved, is on its own network with no
published ports; create the first admin with `mmctl --local user create` in the container
(`MM_SERVICESETTINGS_ENABLELOCALMODE=true`). Recording and transcription need the
`calls-offloader` service with the Docker socket (not included here on purpose).
