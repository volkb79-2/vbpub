# mattermost — nyxloom notification channel

Self-hosted [Mattermost](https://mattermost.com/) Team Edition + a **dedicated**
PostgreSQL, as one ciu-managed stack. Replaces [ntfy](../ntfy/) as nyxloom's
live notification channel (backlog NL-17, package nyxloom-P106). ntfy is kept —
stopped, and still selectable as a `notify.py` backend — not deleted.

Payloads are typed-fields-only per nyxloom SPEC §13: `notify.py`'s Mattermost
backend folds the note's `title`/`body`/`click`/`priority`/`tags` into the
incoming webhook's Markdown `text`, because that is the only field Mattermost
renders.

## Files

- `ciu.defaults.toml.j2` + `ciu.compose.yml.j2` — ciu v2 stack package.
- `hooks/post_compose_provision.py` — idempotent team/channel/account/webhook
  provisioning, run by ciu on every `ciu up` (S9.1 `post_compose`).
- `docker-compose.yml` — pre-rendered copy for plain-compose deploys, carrying
  ABSOLUTE PHYSICAL host paths (DooD) **and hand-inlined governance caps**.
  Keep in sync with the templates; read its header before using it.

## Deploy (ciu-managed — the ONLY supported path)

```bash
cd /workspaces/vbpub && ciu up --dir nyxloom/mattermost -y
```

Containers: `nyxloom-prod-mattermost`, `nyxloom-prod-mattermost-db`.

> **Do not start this stack from `docker-compose.yml` and believe it is
> governed.** The plain-compose path receives no ciu overlay: that is exactly
> how the live ntfy container ran unconfined on this shared host for weeks
> (backlog NL-6). The fallback file inlines the same caps by hand; if you touch
> one, touch both.

## Provisioning — automatic and idempotent (nyxloom-P107)

There is **no manual recipe any more**. `hooks/post_compose_provision.py` is a
ciu `post_compose` hook (S9.1) that reconciles the whole account/channel/
webhook surface on **every** `ciu up`, from the declarations in
`[mattermost.provision]`. Every mutating step is guarded by a state probe, so
re-running converges instead of duplicating; a run over an already-correct
instance prints `channels_created=[] accounts_created=[] memberships_added=0`
and changes nothing.

Local mode is still what it talks over: an in-container admin socket, no
network-exposed admin API.

| Account | Role | Channels | Password secret |
|---|---|---|---|
| `nyxloom-admin` | system admin — **bootstrap/system only**, not for daily use | `alerts` | `mattermost/admin_password` |
| `nyxloom-daemon` | regular | `alerts` | `mattermost/daemon_password` |
| `nyxloom-operator` | system admin — the human operator login | **all** | `mattermost/operator_password` |
| `nyxloom-installer` | regular — 3rd-party service account | `installs` | `mattermost/installer_password` |
| `nyxloom-intake` | regular — the feature-intake chatbot (nyxloom-P109) | `intake` (**private**) | `mattermost/intake_password` |

`nyxloom-admin` is deliberately **not** declared in `[mattermost.provision]`:
the hook never touches an account it does not own, so the P106 bootstrap
account is left exactly as it is. Log in as `nyxloom-operator` instead.

"Access to all channels" is `all_channels = true`, resolved against the team's
channel list **at reconcile time** — a channel added to
`[[mattermost.provision.channels]]` is joined on the same run that creates it.
A channel created out of band in the UI is joined on the **next** `ciu up`;
Mattermost has no "member of all future channels" primitive, so that gap is a
documented caveat, not an oversight.

Three incoming webhooks, one per producer (the operator account is a human login
and gets none — an unused credential is worse than no credential). A Mattermost
incoming webhook is bound to **one** channel at creation, so a producer that
writes to a different channel needs its own:

| Secret file | Channel | Posts as | URL base |
|---|---|---|---|
| `daemon_webhook_url` | `alerts` | `nyxloom-daemon` | internal bridge address |
| `installer_webhook_url` | `installs` | `nyxloom-installer` | `MM_SERVICESETTINGS_SITEURL` |
| `intake_webhook_url` | `intake` | `nyxloom-intake` | internal bridge address |

### The `intake` channel and its PAT (nyxloom-P109 / backlog B9)

`intake` is where the feature-intake interview (`src/nyxloom/intake_chat.py`)
is conducted with a human, bridged by `src/nyxloom/intake_bridge.py`. Two
things about it are deliberate and were measured, not assumed.

**It is private, and that is the read boundary.** Verified on a throwaway
11.10.1 instance with a plain `system_user` account: a team member who is not a
channel member reads a **public** channel's posts (HTTP 200) and is refused on a
**private** one (HTTP 403). The intake account holds a personal access token,
Team Edition has no per-token scoping, and a PAT therefore inherits its
account's full permissions — so "which channels is this account in" is the only
thing bounding what a leaked token can read.

**Its PAT belongs to a dedicated, non-admin, single-channel account.** The other
four accounts each fail on that: `nyxloom-operator` is a system admin (a leaked
PAT would be a system-admin bearer token), `nyxloom-daemon` would gain the
ability to read all of `alerts` and to forge notifications, `nyxloom-installer`
is a third party's credential, and `nyxloom-admin` is the untouched bootstrap
account.

The token is provisioned by the hook through S9.4a into
`nyxloom/mattermost/.ciu/secrets/intake_pat`, alongside the webhook URLs and on
the same terms. It is minted only while
`[mattermost].enable_user_access_tokens = true`; while that is false the hook
prints the token names it is **not** minting and moves on, so the account, the
channel and the reply webhook are all provisioned and waiting — the same shape
`installer_webhook_url` already has against `expose_public`.

**Rotating the PAT**: `mmctl --local token revoke <token-id>` (id from
`mmctl --local token list nyxloom-intake`), delete
`.ciu/secrets/intake_pat`, then `ciu up`. Both steps are required: Mattermost
reveals a token's value exactly once, so a live token with no store file is a
state the hook **refuses** rather than guesses at — it cannot re-derive the
value, and minting a second would leave an unrevocable orphan.

**Enabling PATs is a server-wide widening.** `enable_user_access_tokens` drives
`MM_SERVICESETTINGS_ENABLEUSERACCESSTOKENS`, which lets any system admin mint a
bearer token for any account. It is required by the bridge's `rest` transport
and by nothing else — the `mmctl` transport needs no server change at all, which
is exactly why both exist. It cannot be avoided by using a Mattermost *bot*
account (bots are exempt from the setting): `mmctl bot create` answers
`This command cannot be run in local mode`, and reaching the bot API would mean
using the network admin API with an admin password — the widening this stack's
local-mode design exists to avoid. That was tested, not assumed.

**Where the secrets live — two directories, on purpose.** The generated
*passwords* are S4 `GEN_LOCAL` directives and land in the **project** store,
`<ciu-root>/.ciu/secrets/mattermost/`. The *webhook URLs* are persisted by the
hook through S9.4a and land in the **stack** store,
`nyxloom/mattermost/.ciu/secrets/`. Both are gitignored by `**/.ciu/`, both are
0440 `vscode:docker`. The split is ciu's, not a choice: a webhook id does not
exist until Mattermost mints it, so no directive can express it, and S9.4a is
the channel for exactly that case.

```bash
export NYXLOOM_WEBHOOK_URL="$(cat /workspaces/vbpub/nyxloom/mattermost/.ciu/secrets/daemon_webhook_url)"
```

**Rotating a webhook**: `mmctl --local webhook delete <id>`, then `ciu up`. The
hook mints a fresh one and re-persists the URL. Do **not** leave two webhooks
with the same display name — the hook refuses to guess which is current and
fails the deploy naming the count.

**The `installer_webhook_url` is not yet reachable from an install host.**
While `expose_public = false` its base is the internal bridge address. The
credential is provisioned and waiting; it becomes usable in the same edit that
flips exposure. (`scripts/netcup` / `scripts/debian-install-v2` are out of
scope here and untouched — that work brings its own payload translator and a
`notify_backend` selector; this package only puts the Mattermost side in
place.)

**Why a hook and not a one-shot init container** (both were evaluated): S9.4a
is the only sanctioned way to get a minted webhook id back into ciu's secret
store — a sidecar would need a writable bind mount of the credential store to
avoid using the API for writing to the credential store. `ctx.wait_healthy()`
already solves readiness, which S9.3 says a hook must not re-implement. And
`mmctl --local` needs the app container's own local-mode socket, which is not
on a shared volume: exporting it to a sidecar would *widen* the admin surface
(anything that can mount the volume gets unauthenticated system-admin) purely
to avoid `docker exec`. The full argument is in the module docstring.

*Residual exposure, stated rather than buried:* `mmctl user create` takes the
password as a command-line flag (no stdin, no `*_FILE` form), so a generated
password is briefly visible in the host process table during account
**creation**. A reconcile over existing accounts passes no password at all.
This is strictly less exposure than the recipe it replaces, which also put the
password in shell history and an exported variable.

Read messages back from the host (useful as a delivery oracle):

```bash
docker exec nyxloom-prod-mattermost mmctl --local post list nyxloom:alerts --number 5
```

## nyxloom wiring (consumer project.toml)

nyxloom's own `nyxloom-trove/nyxloom.toml` is **already cut over** (P107):

```toml
[notify]
backend = "mattermost"          # explicit selector — NL-17; sole channel, no fallback
mattermost_channel = "alerts"   # optional; the webhook already targets a channel
# mattermost_username is deliberately NOT set: posts carry the real
# `nyxloom-daemon` account identity now, not P106's override_username display
# trick from the system-admin account.
# webhook_url is NOT committed: the URL is the credential. Export it instead —
# NYXLOOM_WEBHOOK_URL wins over any toml value (config.py, same shape as NTFY_URL).
```

```bash
export NYXLOOM_WEBHOOK_URL="$(cat /workspaces/vbpub/nyxloom/mattermost/.ciu/secrets/daemon_webhook_url)"
```

The feature-intake bridge is wired in the same file's `[intake_bridge]` table
(nyxloom-P109), defaulting to the `mmctl` transport so it needs no server
change. Its two credentials are exported the same way, and the ingress stays
closed until an operator is named:

```bash
export NYXLOOM_INTAKE_WEBHOOK_URL="$(cat /workspaces/vbpub/nyxloom/mattermost/.ciu/secrets/intake_webhook_url)"
export NYXLOOM_INTAKE_MM_TOKEN="$(cat /workspaces/vbpub/nyxloom/mattermost/.ciu/secrets/intake_pat)"   # `rest` only
export NYXLOOM_CHANNEL_OPERATOR_ID="<operator identity>"
nyxloom intake-bridge poll nyxloom
```

That default runs anywhere the docker socket does. `--transport rest` does
NOT: it dials `base_url` over the network below, so it only works from inside
`nyxloom-prod-mattermost_internal` — which is why the transport is a config
selector and not a fallback chain. See step 5b of the P109 recipe.

`/workspaces/dstdns`'s own config is a **separate repo and out of scope** —
it still points at its previous channel and is left for a dstdns-side session.

Anything that must reach Mattermost has to share a network with it. The stack
owns a private bridge (`nyxloom-prod-mattermost_internal`); attach a consumer
(or, for a one-off check from the devcontainer) with:

```bash
docker network connect nyxloom-prod-mattermost_internal <container>
# ... and afterwards:
docker network disconnect nyxloom-prod-mattermost_internal <container>
```

## Network exposure — DECIDED "internal-only", OPERATOR TO CONFIRM

`expose_public = false` in `ciu.defaults.toml.j2`. Today the stack publishes
**no host ports and joins no shared ingress network**: it is reachable only
from its own private bridge. That is the safe default for an endpoint nobody
has signed off on, and it is enough for nyxloom's own delivery path.

It is **not** enough for a phone, a desktop client, or a browser off this host
— the same reason ntfy had to be publicly reachable. Setting
`expose_public = true` publishes `mattermost.gstammtisch.dchive.de` through
tls-edge (same domain convention and router-label pattern as ntfy) and re-points
`MM_SERVICESETTINGS_SITEURL` at it. **That is an operator decision**: it creates
a real public endpoint on a real domain. The locked-down posture below applies
either way.

## Governance / safety posture

- Governance is ciu-resolved, not hand-written: the root's `[governance]`
  supplies `enabled`/`cgroup_parent = dev-background.slice`/`device`, and
  `[mattermost.governance]` layers `mem_limit = 2g`, `mem_swap_limit = 18g`,
  `cpus = 1.5` over it (S15.10 shallow merge — do NOT restate the inherited
  keys). Postgres narrows itself to 512m/4g/0.5 via author-set compose keys
  (S15.3). Neither service emits `cgroup_parent` — that author-precedence trap
  is what NL-6 was.
- Verified live (2026-09-08): both containers sit under
  `/dev.slice/dev-background.slice/`, with `memory.max` / `memory.swap.max` /
  `cpu.max` matching the declared values.
- Accounts: no open server, no self-signup — admin-created only.
- No file attachments, no plugin framework (the prepackaged playbooks/AI
  plugins would otherwise run their own processes inside this 2g cgroup), no
  marketplace, no outgoing webhooks, no slash commands, no telemetry, no email
  (no SMTP is configured).
- **Personal access tokens: off by default**, and their own declared flag
  (`[mattermost].enable_user_access_tokens`) rather than an implication of
  provisioning one. See "The `intake` channel and its PAT" above for what
  turning it on widens and why the bridge's `mmctl` transport exists so it
  does not have to be turned on at all.
- Postgres is dedicated to this stack, on a private bridge, never published,
  password from ciu's `GEN_LOCAL` store via `POSTGRES_PASSWORD_FILE`.
- The one deliberate concession: the app's DSN password arrives through ciu's
  `expose_env` escape hatch (S4.19), because the 11.x image ships no shell for
  the S4.18 entrypoint-wrapper pattern and Mattermost has no `*_FILE` form for
  its datasource. See the comment in `ciu.defaults.toml.j2`.

## Storage — bind-mounted hostdirs + named volumes (nyxloom-P107)

| Path in container | Backing | Ownership | Why |
|---|---|---|---|
| `/var/lib/postgresql/data` | hostdir `vol-postgres-data` | `70:DOCKER_GID` `0700` | the data that matters; host-visible for `du`/wipe |
| `/mattermost/config` | hostdir `vol-mattermost-config` | `2000:DOCKER_GID` `0770` | `config.json` is the file an operator reads and diffs |
| `/mattermost/logs` | hostdir `vol-mattermost-logs` | `2000:DOCKER_GID` `0770` | grep across restarts; makes the one unbounded-growth surface visible |
| `/mattermost/data` | named volume | image | file attachments are **disabled** — nothing to read |
| `/mattermost/plugins`, `/mattermost/client/plugins` | named volumes | image | plugin framework is **off**; these exist only because the image declares `VOLUME` on both paths |

The three hostdirs are ciu-managed (S6.1/S6.3), auto-pathed as
`<stack>/vol-<service>-<purpose>`, and pre-owned through ciu's S6.5 root
helper — `vol-*/` is gitignored.

**These are deliberately NOT `external` volumes.** External would *protect*
them from teardown; the requirement is the opposite — the ability to wipe on
demand. `ciu down` preserves them; **`ciu up --reset` and `ciu clean` delete
them** (S6.4, which routes to the PHYSICAL path under DooD and degrades to the
root helper for the uid-70/uid-2000 subtrees the operator cannot remove
himself), taking every account, channel, message and webhook with them.

**No init container**, unlike dstdns's `infra/db-core` (`postgres_init`, with
its `CLEAN_DATA_DIR` wipe gate). That container exists there because db-core
forces `user: "1000:${DOCKER_GID}"` onto `timescaledb-ha`, removing the
entrypoint's root phase. Measured on **this** stack's images (P107):

- `postgres:16-alpine` with **no** `user:` override self-initialises a bind
  mount it does not own — its entrypoint starts as root, chowns to uid **70**
  (not 999; 999 is the Debian image) and chmods `0700` itself.
- The same image **with** `user: "70:994"` over a dir it does not own fails
  exactly as db-core's would: `initdb: error: could not change permissions of
  directory "/var/lib/postgresql/data": Operation not permitted`.
- The Mattermost image is the opposite case: its default user **is**
  `mattermost` (uid 2000) — no root phase, no `/bin/sh` — so it can never fix
  up a bind mount, and fails with `could not create config file: open
  /mattermost/config/config.json: permission denied` unless the directory is
  pre-owned.

So ciu's S6.5 helper does the pre-owning ("stacks SHOULD NOT carry init
containers for ownership fixes") and the images keep their own startup paths.

**Honest limit:** `postgres` chmods PGDATA to `0700` unconditionally, so
`vol-postgres-data` is host-*visible* but not host-*readable* — declaring
S6.7(a)'s suggested `0770` would be a lie `initdb` overwrites, and would then
fail S6.3's ownership check on the next `ciu up`. Backups still go through
`pg_dump` in the container:

```bash
docker exec nyxloom-prod-mattermost-db sh -c \
  'PGPASSWORD=$(cat /run/secrets/postgres_password) pg_dump -U mmuser mattermost' > mattermost.sql
```

### Migrating an EXISTING named-volume deployment to the hostdirs

Required once, from the checkout the stack is deployed from. Verified in
rehearsal (P107) on a copy of the live data.

```bash
# 0. dump first, and keep it OUT of the repo
docker exec nyxloom-prod-mattermost-db sh -c \
  'PGPASSWORD=$(cat /run/secrets/postgres_password) pg_dump -U mmuser mattermost' > ~/mattermost.sql

# 1. STOP first — a live copy would need crash recovery
ciu down --define-root /workspaces/vbpub/nyxloom
docker rm nyxloom-prod-mattermost nyxloom-prod-mattermost-db   # no -v: named volumes survive

# 2. let ciu create the hostdirs with the right ownership
ciu up --dir nyxloom/mattermost -y --dry-run --define-root /workspaces/vbpub/nyxloom

# 3. copy the data in, then RE-ASSERT the ownership `cp -a` clobbers.
#    `cp -a /from/. /to/` copies the SOURCE directory's own owner/mode onto
#    the target, which leaves 70:70 / 2000:2000 — and S6.3 then REFUSES the
#    hostdir on the next `ciu up` as incompatible. This step is not optional.
P=/home/vb/volkb79-2/vbpub/nyxloom/mattermost
docker run --rm -v nyxloom-prod-mattermost_postgres-data:/from:ro \
  -v "$P/vol-postgres-data":/to alpine:3.20 sh -c 'cp -a /from/. /to/'
docker run --rm -v nyxloom-prod-mattermost_mattermost-config:/from:ro \
  -v "$P/vol-mattermost-config":/to alpine:3.20 sh -c 'cp -a /from/. /to/'
docker run --rm -v "$P":/t alpine:3.20 sh -c '
  chown 70:994   /t/vol-postgres-data     && chmod 0700 /t/vol-postgres-data
  chown 2000:994 /t/vol-mattermost-config && chmod 0770 /t/vol-mattermost-config'

# 4. real up, then verify BOTH data and governance — a recreate is exactly
#    where governance silently drops (P106's own lesson).
ciu up --dir nyxloom/mattermost -y --define-root /workspaces/vbpub/nyxloom
docker exec nyxloom-prod-mattermost-db sh -c \
  'PGPASSWORD=$(cat /run/secrets/postgres_password) psql -U mmuser -d mattermost -tAc \
   "select (select count(*) from users), (select count(*) from teams), (select count(*) from channels), (select count(*) from posts)"'
docker inspect nyxloom-prod-mattermost nyxloom-prod-mattermost-db \
  --format '{{.Name}} {{.HostConfig.CgroupParent}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'
docker exec nyxloom-prod-mattermost-db cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max
```

The old named volumes are left in place after step 4 as a rollback point;
delete them (`docker volume rm nyxloom-prod-mattermost_postgres-data
nyxloom-prod-mattermost_mattermost-config
nyxloom-prod-mattermost_mattermost-logs`) only once the bind-mounted stack has
been healthy for a while.

### Enabling personal access tokens + minting the intake PAT (nyxloom-P109)

Required once, on the live stack, from the checkout it is deployed from. This
is a **server-wide widening** — read "The `intake` channel and its PAT" above
before running it. Everything except the PAT itself (the `nyxloom-intake`
account, the private `intake` channel, `intake_webhook_url`) lands on an
ordinary `ciu up` with no flag change at all, so step 0 is worth doing on its
own first.

```bash
# 0. WITHOUT the flag: account + private channel + reply webhook only.
#    Expect `channels_created=['intake'] accounts_created=['nyxloom-intake']`
#    and a line naming intake_pat as NOT minted. This is also the first live
#    exercise of the ` (private)` channel-name parse -- if `channel list`
#    drifted, the hook refuses here, before anything is widened.
ciu up --dir nyxloom/mattermost -y --define-root /workspaces/vbpub/nyxloom
docker exec nyxloom-prod-mattermost mmctl --local channel list nyxloom   # expect `intake (private)`
docker exec nyxloom-prod-mattermost mmctl --local channel users list nyxloom:intake --all

# 1. THE WIDENING. Set it in ciu.defaults.toml.j2 ([mattermost] table):
#      enable_user_access_tokens = true
#    then re-render and confirm the compose really carries it before `up`.
ciu up --dir nyxloom/mattermost -y --dry-run --define-root /workspaces/vbpub/nyxloom
# ciu's rendered compose output is `ciu.compose.yml` at the stack root (S8.5)
# -- NOT the hand-maintained `docker-compose.yml` fallback beside it, which
# receives no ciu overlay and is not what `ciu up` deploys.
grep ENABLEUSERACCESSTOKENS nyxloom/mattermost/ciu.compose.yml   # expect "true"

# 2. Real up. The container RECREATES (an env change), so re-verify governance
#    in the same breath -- a recreate is exactly where it silently drops.
ciu up --dir nyxloom/mattermost -y --define-root /workspaces/vbpub/nyxloom
docker inspect nyxloom-prod-mattermost \
  --format '{{.Name}} {{.HostConfig.CgroupParent}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'

# 3. Verify the PAT was minted ONCE and is idempotent. The second `ciu up`
#    must print `tokens_minted=[]` -- if it mints again, STOP and revoke.
#    Capture, THEN read: a `| grep` here would replace ciu's own exit status
#    with grep's, so a `ciu up` that failed outright would still look fine as
#    long as the word appeared somewhere (LESSONS L4, the same reason gate
#    verdicts are never read from a pipe tail).
docker exec nyxloom-prod-mattermost mmctl --local token list nyxloom-intake
ls -l nyxloom/mattermost/.ciu/secrets/intake_pat          # 0440, non-empty
ciu up --dir nyxloom/mattermost -y --define-root /workspaces/vbpub/nyxloom \
  >/tmp/p109-reup.log 2>&1; echo "ciu up rc=$?"           # rc MUST be 0
grep tokens_minted /tmp/p109-reup.log                     # expect tokens_minted=[]
docker exec nyxloom-prod-mattermost mmctl --local token list nyxloom-intake   # still exactly ONE

# 4. Point the bridge at it. Both credentials come from the stack store and
#    neither is committed; NYXLOOM_INTAKE_MM_TOKEN wins over any toml value.
export NYXLOOM_INTAKE_WEBHOOK_URL="$(cat nyxloom/mattermost/.ciu/secrets/intake_webhook_url)"
export NYXLOOM_INTAKE_MM_TOKEN="$(cat nyxloom/mattermost/.ciu/secrets/intake_pat)"
export NYXLOOM_CHANNEL_OPERATOR_ID="<the operator identity this channel belongs to>"

# 5. The `mmctl` transport, against the real channel. This one runs from the
#    controller's own shell because it shells out via `docker exec` -- the
#    docker socket is the transport, so no network reachability is involved.
#    The FIRST poll of a channel only adopts the head
#    (`status=bootstrapped`, nothing ingested) -- that is correct, not a
#    failure. Post something in `intake` from the Mattermost UI as
#    nyxloom-operator, then poll again.
nyxloom intake-bridge poll nyxloom --transport mmctl
```

#### Step 5b — `--transport rest` is NOT runnable from the controller's shell

Do not add `nyxloom intake-bridge poll nyxloom --transport rest` to the block
above; it fails with a connection error and proves nothing.
`[intake_bridge].base_url` is `http://nyxloom-prod-mattermost:8065`, and that
name exists only inside the `nyxloom-prod-mattermost_internal` docker network —
the stack publishes no host port (see "Network exposure" above, which is the
point, not an oversight).

The REST transport was verified end-to-end against the throwaway 11.10.1
instance during development, and its parsing is covered by unit tests built
from verbatim captures. What has NOT been exercised is that path against the
LIVE stack with the LIVE PAT, and that stays true until `nyxloomd` itself runs
as a container on that network — which this track has deliberately not done
yet.

What CAN be checked today, without deploying anything, is the half that is
actually in doubt: the credential and the reachability. This is a one-shot
throwaway container on the private network that makes exactly the two GETs
`RestReader.fetch` makes, with the same stdlib client, and exits:

```bash
# Uses python:3-slim (already on this host) and stdlib urllib only -- no pip,
# no image build, nothing persistent. Prints the two HTTP statuses and the
# resolved channel id. Expect: 200, 200, a 26-char id.
docker run --rm --network nyxloom-prod-mattermost_internal \
  --cpus=1 --memory=256m \
  -e MM_TOKEN="$NYXLOOM_INTAKE_MM_TOKEN" \
  python:3-slim python3 -c '
import json, os, urllib.request
BASE = "http://nyxloom-prod-mattermost:8065"
H = {"Authorization": "Bearer " + os.environ["MM_TOKEN"]}
def get(path):
    req = urllib.request.Request(BASE + path, headers=H, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())
st, meta = get("/api/v4/teams/name/nyxloom/channels/name/intake")
print("channel lookup:", st, "id=" + meta["id"])
st, page = get("/api/v4/channels/%s/posts?per_page=1" % meta["id"])
print("posts read   :", st, "posts=%d" % len(page.get("posts", {})))
'
```

A `401` means the PAT is revoked or `enable_user_access_tokens` went back to
false; a `403` means `nyxloom-intake` is not a member of the private `intake`
channel (step 0's `channel users list` is the check for that); a connection
error means the container did not join the right network. Any of those is a
real finding about the live stack — none of them is a bridge defect, which is
exactly why this probe is worth running before the daemon ever is.

Rollback is a flag, not a migration: set `enable_user_access_tokens = false`,
`ciu up`, and revoke the token (`mmctl --local token revoke <token-id>`, then
delete `.ciu/secrets/intake_pat`). The `mmctl` transport keeps working.

