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

`nyxloom-admin` **is** declared as of nyxloom-P110, reversing P107. Measured on
a genuinely empty instance, P107's config created **three** accounts, not four
— nothing created `nyxloom-admin`, which existed only as an artefact of P106's
manual bootstrap on the live server and would have vanished on any rebuild. It
is listed **first** because Mattermost promotes the first-ever account on an
empty server to system admin (measured), so the promotion lands on the account
that is meant to have it. On the live instance the entry reconciles to a
no-op: the hook still never touches an account that already exists.

It stays a **bootstrap/system** account either way — log in as
`nyxloom-operator` for daily use.

Two channels are declared; a fresh team comes up with **four**, because
`mmctl team create` also creates Mattermost's own `town-square` and
`off-topic` and auto-joins every team member to them. That is Mattermost
behaviour, not drift, and `all_channels = true` resolves against all four.

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

**The `installer_webhook_url` is reachable from an install host as of the
2026-09-09 go-public cutover** (`expose_public = true`, "Going public" below).
Its base is `MM_SERVICESETTINGS_SITEURL`, so it resolves to the public
`https://mattermost.gstammtisch.dchive.de` form, deliverable from a
freshly-provisioned host off nyxloom's private network. (`scripts/netcup` /
`scripts/debian-install-v2` are out of scope here and untouched — that work
brings its own payload translator and a `notify_backend` selector; this
package only puts the Mattermost side in place.)

**`installer_pat` (nyxloom-P111, follow-up to P109/P110).** A personal access
token for the same `nyxloom-installer` account, minted through the same
`[[mattermost.provision.tokens]]` mechanism as `intake_pat` (see "The `intake`
channel and its PAT" above for the full mechanics — dedup key, orphan refusal,
rotation). It carries the account's existing scope, nothing wider: read/post
in `installs` only. Verified live: a REST GET against `installs` returns 200;
the same lookup against the private `alerts` channel returns 404 (Mattermost
hides a private channel's existence from a non-member's name lookup, unlike
the 403 a *known-id* posts-read gets — both refuse the read, this is just a
different endpoint). Lands in `nyxloom/mattermost/.ciu/secrets/installer_pat`,
0440, same store as the webhook URLs.

This PAT exists because the webhook is POST-only and Mattermost's
incoming-webhook API has no attachment support at all — a PAT + the REST
Files API is the only path to file uploads. **File attachments are now on**
(`enable_file_attachments = true`, `MM_FILESETTINGS_ENABLEFILEATTACHMENTS`,
nyxloom-P111, 2026-09-10): upload+attach was verified end-to-end against the
real public endpoint (`POST /api/v4/files` then `POST /api/v4/posts` with
`file_ids`, both over `https://mattermost.gstammtisch.dchive.de`, not a
mock) — see `CONSUMER.md` for the exact recipe. Uploaded files land in the
`mattermost-data` **named volume**, not a bind mount like config/logs — a
residual left open deliberately, see `enable_file_attachments`'s own comment
in `ciu.defaults.toml.j2` for the conversion recipe if that's ever wanted.

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

The ordered steps for actually flipping it are further down, under
[Going public](#going-public--the-post-merge-recipe). Read the audit first.

## Security audit for public exposure (nyxloom-P110)

Everything in this section was **measured** against a throwaway 11.10.1
instance in its own `ciu worktree` — never read out of the Mattermost docs and
never inferred from a setting's name. That discipline was not decoration: the
two items that looked most obviously fine — `ENABLEOPENSERVER=false` closing
self-signup, and OAuth "surely defaulting off" — were the two that were wrong.

### Found wrong, and fixed

| Setting | Was | Now | What the measurement showed |
|---|---|---|---|
| `MM_TEAMSETTINGS_ENABLEUSERCREATION` | `true` | `false` | `POST /api/v4/users` unauthenticated → **403** `no_open_server`, but `POST /api/v4/users?iid=<team invite_id>` → **201 CREATED**. `ENABLEOPENSERVER=false` closes the bare signup route and nothing else. |
| `MM_SERVICESETTINGS_ENABLEOAUTHSERVICEPROVIDER` | unset | `false` | The default is **`true`**. Team Edition ships an OAuth2 authorization server switched on — a second credential factory behind the one human admin login. |
| `MM_RATELIMITSETTINGS_ENABLE` (+ `PERSEC`/`MAXBURST`/`MEMORYSTORESIZE`/keying) | unset | `true` (10/100/10000) | `RateLimitSettings.Enable` defaults **false**, and tls-edge does not compensate: its only entrypoint middleware is `secure-headers`, and `rateLimit` sits under ARCHITECTURE.md **F5 "Partially implemented"** with no definition in `edge-proxy/conf.d/middlewares.yml`. App-level is the only limiter in the path — which is also the shape `../ntfy/server.yml` already uses. |
| `MM_SERVICESETTINGS_SESSIONLENGTHWEBINHOURS` | unset | `168` | Default **4320 hours = 180 days**. A browser session stolen once stayed valid for half a year. |
| `MM_PRIVACYSETTINGS_SHOWEMAILADDRESS` / `SHOWFULLNAME` | unset | `false` | Both default **true**: any authenticated account — including `nyxloom-installer`, whose credential is meant to live on a third-party install host — read every other account's address from `GET /api/v4/users/username/<name>`. After the change the same call returns `email: ""`. **Partial, stated as such:** `roles` is *not* covered by `PrivacySettings` and still comes back, so an authenticated caller can still tell which account is the system admin. Closing that would mean a permissions-scheme change, not a config flag. |
| `MM_SERVICESETTINGS_ENABLEEMAILINVITATIONS` | unset | `false` | Defaults **true**. Inert without SMTP, but it is the other invite surface and nothing but the absence of SMTP was stopping it. |
| `MM_SERVICESETTINGS_ENABLESECURITYFIXALERT` | unset | `false` | Defaults **true** and is a telemetry channel, which made this README's "no telemetry" claim untrue. Stated cost: nobody will mail this instance about a CVE — the pinned image tag is the operator's only notification channel. |

### Checked and already correct — restated explicitly, not changed

Restating a value that already matches is this estate's "explicit over
silently-inherited-default" rule (the same reason `cgroup_parent` and `device`
are spelled out rather than autodetected): a measured default is a fact about
today's image, not a contract across the next bump.

| Setting | Measured default | Note |
|---|---|---|
| `MaximumLoginAttempts` | `10` | The public login form is **not** the zero-lockout surface it was suspected to be. Restated at 10 rather than tightened — two human logins with `GEN_LOCAL` passwords do not need a 3-strike lockout, and a tight threshold is its own denial of service on the operator. |
| `EnableAPITeamDeletion` / `EnableAPIUserDeletion` / `EnableAPIChannelDeletion` | all `false` | Restated: these are the ones that turn a stolen admin session into data loss rather than a leak. |
| `EnableMultifactorAuthentication` | `false`, and **available in Team Edition** | Turned **on** (not enforced) so the operator can opt the human login in; `ENFORCE` stays false because it applies server-wide. |

### Checked and found to need nothing

- **Non-admin webhook minting.** `MM_SERVICESETTINGS_ENABLEONLYADMININTEGRATIONS`
  does not exist in 11.10.1 — `mmctl config get` answers `invalid key`. The
  setting was replaced by the permissions scheme, and the scheme is already
  right: `system_user`, `team_user` and `channel_user` hold **no** `*webhook*`
  permission at all, and a logged-in non-admin `POST /api/v4/hooks/incoming`
  is refused **403** where the same call as a system admin returns **201**.
  Nothing to set; the `nyxloom-installer` pattern cannot self-mint webhooks.
- **Clickjacking / response headers.** Mattermost already sends
  `Content-Security-Policy: frame-ancestors 'self'`, `X-Frame-Options:
  SAMEORIGIN`, `X-Content-Type-Options: nosniff` and `Referrer-Policy:
  no-referrer` on the web app; tls-edge adds HSTS at the entrypoint. No
  per-router Traefik `headers` middleware is warranted — and this is exactly
  the case tls-edge's `secure-headers` deliberately leaves `frameDeny` unset
  for.

### Known residual, NOT fixed here — stated so it is not mistaken for covered

`alerts` is a **public** channel. Measured on the throwaway, logged in as
`nyxloom-installer` — an account deliberately left OUT of `alerts`:
`GET /api/v4/channels/<alerts>/posts` → **200** (it reads the channel without
being a member) and `POST /api/v4/channels/<alerts>/members` → **201** (it
joins). So `ciu.defaults.toml.j2`'s claim that a compromised install host
"cannot read nyxloom's own operator traffic" is weaker than it reads.

**The signup fix above does not touch this, and it is worth being blunt about
why**, because the two look related and are not. `ENABLEUSERCREATION=false`
stops *new accounts being created*. This gap is about accounts that **already
exist**: any member of the team can browse and join any *public* channel in
it, and no server setting in this stack's posture changes that. Removing an
attacker's ability to sign up does not remove an existing service account's
ability to join `alerts`. (An earlier revision of this section led with the
team's `type: "O"` / `allow_open_invite: true` shape, which invited exactly
that wrong inference — those govern joining the **team**, and every account
here is already a team member.)

Closing it means making `alerts` **private**, the way nyxloom-P109 made
`intake` private for the same read-boundary reason. The hook only ever
*creates* channels, so setting `private = true` in
`[[mattermost.provision.channels]]` would silently do nothing to the existing
live channel — it is an **operator action on the live instance**, not a code
change a package can verify. It is a named decision point in the go-public
recipe below (step 2b), not just a note here.

The blast radius that actually matters — the installer's webhook URL — is
post-only and targets `installs` alone, which is unchanged.

### One consequence worth knowing before debugging anything

A setting supplied through `MM_*` **cannot be changed at runtime**. Measured:
`mmctl --local config set TeamSettings.EnableUserCreation false` prints
`Value changed successfully` and the value stays `true`, because the
environment overlay wins over `config.json`. So the System Console shows these
greyed out, and *verifying* hardening means reading the running config
(`mmctl --local config get`) or `docker inspect`, never a successful
`config set`.

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
- Accounts: no open server, **no user creation over the API at all** — created
  only through the local admin socket. Until nyxloom-P110 this line said "no
  self-signup" and was **not true**; see the audit below for what closed it.
- No plugin framework (the prepackaged playbooks/AI plugins would otherwise
  run their own processes inside this 2g cgroup), no marketplace, no
  outgoing webhooks, no slash commands, no telemetry, no email (no SMTP is
  configured), no OAuth2 authorization server.
- **File attachments: ON as of nyxloom-P111** (`enable_file_attachments`,
  its own declared flag, same shape as `enable_user_access_tokens` below).
  Off by default from P106 through P110 for the reason stated in its own
  `ciu.defaults.toml.j2` comment (SPEC §13 keeps nyxloom's own notifications
  typed-text-only regardless); flipped specifically so `nyxloom-installer`'s
  PAT can use the REST Files API. See "The `intake` channel and its PAT" /
  `installer_pat`'s own section above.
- **Personal access tokens: ON server-wide** (`[mattermost].
  enable_user_access_tokens`, its own declared flag rather than an
  implication of provisioning one). Two are currently minted:
  `intake_pat` (P109) and `installer_pat` (P111). See "The `intake` channel
  and its PAT" above for what turning this on widens and why the bridge's
  `mmctl` transport exists so it does not strictly have to be turned on.
- Postgres is dedicated to this stack, on a private bridge, never published,
  password from ciu's `GEN_LOCAL` store via `POSTGRES_PASSWORD_FILE`.
- The one deliberate concession: the app's DSN password arrives through ciu's
  `expose_env` escape hatch (S4.19), because the 11.x image ships no shell for
  the S4.18 entrypoint-wrapper pattern and Mattermost has no `*_FILE` form for
  its datasource. See the comment in `ciu.defaults.toml.j2`.

## Going public — the post-merge recipe

Run **after** this package is merged, against the live
`nyxloom-prod-mattermost` stack, from the deployment checkout
(`/workspaces/vbpub`). The package itself does **not** flip exposure:
`expose_public` stays `false` in `ciu.defaults.toml.j2` and the controller
changes it here, live, in step 3 — deliberately, so that landing the hardening
and creating a public endpoint are two separately reversible decisions.

Steps 0–2 are worth doing **on their own** even if the flip is postponed: none
of the hardening is conditional on being public.

```bash
cd /workspaces/vbpub
R="--define-root /workspaces/vbpub/nyxloom"
```

### 0. Pre-flight — READ-ONLY, and it gates everything after it

Nothing here mutates anything. It exists because this package declares
`nyxloom-admin` in `[[mattermost.provision.accounts]]` for the first time
(P107 left it out), and the hook's "never touch an account that already
exists" rule only protects the live bootstrap admin **if the live bootstrap
admin is actually called `nyxloom-admin`**. If it is named anything else, the
first `ciu up` after this merge creates a *brand-new* `nyxloom-admin` from the
`GEN_LOCAL` secret, adds it to the team and promotes it to system admin —
which is not wrong so much as **silent**, and only visible afterwards.

```bash
C=nyxloom-prod-mattermost
docker exec $C /mattermost/bin/mmctl --local --json user search nyxloom-admin \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["username"], "|", d["roles"])'
# expect: nyxloom-admin | system_admin system_user   (order of roles may vary)

docker exec $C /mattermost/bin/mmctl --local channel users list nyxloom:alerts --all \
  | grep nyxloom-admin
# expect: one line naming nyxloom-admin

docker exec $C /mattermost/bin/mmctl --local user list --all
# expect exactly the accounts you think exist — read the whole list, this is
# the step that tells you whether the bootstrap admin is named something else
```

**STOP conditions — do not run step 1 if any of these hold:**

| Observation | What it means | Do this instead |
|---|---|---|
| `user search nyxloom-admin` fails / not found | the live bootstrap admin has a **different name** | rename it to `nyxloom-admin`, **or** change `username` in the first `[[mattermost.provision.accounts]]` entry to match the real one, before any `ciu up` |
| it exists but has **no** `system_admin` role | the hook will promote it on the next run | intended, but confirm that is what you want first — this hook only ever promotes, never demotes an existing account |
| it exists but is **not** in `alerts` | the hook will add it (`channels = ["alerts"]`) | intended; noted so it is not a surprise |
| `user list --all` shows an unexpected extra admin | somebody provisioned by hand | resolve that first; it is outside this recipe |

Everything the pre-flight can find is cheaper to find here than after a
`ciu up` has already created an account and promoted it.

### 1. Apply the hardening (no exposure change yet)

```bash
ciu up --dir nyxloom/mattermost --dry-run -y $R          # read the rendered env diff
ciu up --dir nyxloom/mattermost -y $R
```

The env block changed, so **the app container is RECREATED** — and a recreate
is precisely where governance silently drops (nyxloom-P106's own lesson, and
the reason the P107 migration recipe below ends the same way). Re-verify caps
against the *new* container, do not assume they carried:

```bash
docker inspect nyxloom-prod-mattermost nyxloom-prod-mattermost-db \
  --format '{{.Name}} {{.HostConfig.CgroupParent}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'
# expect (name / cgroup / memory / nanocpus):
#   /nyxloom-prod-mattermost     dev-background.slice 2147483648 1500000000
#   /nyxloom-prod-mattermost-db  dev-background.slice  536870912  500000000
docker exec nyxloom-prod-mattermost-db cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max
```

Postgres is **not** recreated by this change (its env is untouched), so its
data is not at risk here; the app container holds no state outside its mounts.

The `post_compose` hook runs as part of this `ciu up`. Expect it to change
**nothing** on the live instance — the accounts, channels and webhooks already
exist:

```
[PROVISION] team_created=False channels_created=[] accounts_created=[] \
            admin_role_stripped=[] memberships_added=0 webhooks=[...]
```

`admin_role_stripped=[]` is the expected live value: that step only ever
touches accounts the same run created.

### 2. Verify the hardening actually took effect — before exposing anything

Reading the *running* config, not `config.json` and not a `config set` (see
["One consequence worth knowing"](#one-consequence-worth-knowing-before-debugging-anything)
— an `MM_*` value cannot be changed at runtime and `config set` reports
success while changing nothing):

```bash
for k in TeamSettings.EnableUserCreation TeamSettings.EnableOpenServer \
         ServiceSettings.EnableOAuthServiceProvider ServiceSettings.SessionLengthWebInHours \
         PrivacySettings.ShowEmailAddress RateLimitSettings.Enable \
         ServiceSettings.MaximumLoginAttempts ServiceSettings.EnableSecurityFixAlert; do
  printf '%-50s ' "$k"
  docker exec nyxloom-prod-mattermost /mattermost/bin/mmctl --local config get "$k"
done
# expect: false false false 168 false true 10 false
```

> `mmctl --local config get RateLimitSettings.VaryByHeader` **panics** on
> 11.10.1 (`reflect: call of reflect.Value.IsNil on string Value`) — an mmctl
> bug, not a misconfiguration. Read that one from `docker inspect` instead.

And the probe that actually matters — the invite-id signup route that was open
before this package. From the devcontainer, joined to the stack's private
bridge:

```bash
docker network connect nyxloom-prod-mattermost_internal <this-container>
B=http://nyxloom-prod-mattermost:8065
IID=$(docker exec nyxloom-prod-mattermost /mattermost/bin/mmctl --local --json team search nyxloom \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["invite_id"])')
curl -s -X POST "$B/api/v4/users?iid=$IID" \
  -H 'Content-Type: application/json' \
  -d '{"email":"probe@invalid.example","username":"p110probe","password":"Pr0be-Pw!2026"}'
# BEFORE this package: 201, and a real account exists afterwards.
# AFTER (measured):    501  api.user.create_user.signup_email_disabled
docker network disconnect nyxloom-prod-mattermost_internal <this-container>
```

**If that returns 201, stop — do not proceed to step 3.** (501 rather than
403 is Mattermost's own choice of code for a disabled signup route; it is the
`id` field that identifies the refusal, not the status.)

The two human logins must still work — `ENABLEUSERCREATION` gates account
*creation*, not authentication, and this was verified on the throwaway before
being written down:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$B/api/v4/users/login" \
  -H 'Content-Type: application/json' \
  -d "{\"login_id\":\"nyxloom-operator\",\"password\":\"$(cat /workspaces/vbpub/nyxloom/.ciu/secrets/mattermost/operator_password)\"}"
# expect 200
```

### 2b. 🛑 DECISION POINT — `alerts` is joinable by every account on this server

**This one is not a check that passes or fails. It is a choice, and it has to
be made here, before the flip, because nothing later in this recipe closes
it.**

`alerts` is a **public** channel. Any account that is a member of the
`nyxloom` team can browse it and join it — including `nyxloom-installer`,
whose credential is meant to live on a third-party install host, and
including `nyxloom-intake`, whose PAT (P109) is a bearer credential if it is
ever enabled. Measured as `nyxloom-installer`, an account deliberately left
out of `alerts`: reading its posts returns **200** and joining returns
**201**.

**None of the hardening in step 1 affects this.** `ENABLEUSERCREATION=false`
stops new accounts from being *created*; it does nothing about what the
accounts that already exist are allowed to join. Do not read step 2's green
signup probe as covering this.

See it for yourself:

```bash
PW=$(cat /workspaces/vbpub/nyxloom/.ciu/secrets/mattermost/installer_password)
TOK=$(curl -s -D- -o /dev/null -X POST "$B/api/v4/users/login" \
      -H 'Content-Type: application/json' \
      -d "{\"login_id\":\"nyxloom-installer\",\"password\":\"$PW\"}" \
      | tr -d '\r' | awk 'tolower($1)=="token:"{print $2}')
TEAM=$(docker exec $C /mattermost/bin/mmctl --local --json team search nyxloom \
       | python3 -c 'import sys,json; print(json.load(sys.stdin)[0]["id"])')
CH=$(curl -s "$B/api/v4/teams/$TEAM/channels/name/alerts" -H "Authorization: Bearer $TOK" \
     | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -s -o /dev/null -w 'installer reads alerts: %{http_code}\n' \
  "$B/api/v4/channels/$CH/posts" -H "Authorization: Bearer $TOK"
# 200 today — the installer account is not a member and reads it anyway
```

**Option A — close it (recommended before going public).** Convert the live
channel to private, then make a fresh create match. Both halves, or the next
rebuild silently reopens it:

```bash
docker exec $C /mattermost/bin/mmctl --local channel modify nyxloom:alerts --private
docker exec $C /mattermost/bin/mmctl --local channel list nyxloom     # expect: alerts (private)
curl -s -o /dev/null -w 'installer reads alerts: %{http_code}\n' \
  "$B/api/v4/channels/$CH/posts" -H "Authorization: Bearer $TOK"      # expect 403
```

Then set `private = true` on the `alerts` entry in
`[[mattermost.provision.channels]]` — the hook only ever *creates* channels,
so that flag changes nothing live and exists purely so a future fresh create
comes up private too. This is exactly the shape nyxloom-P109 used for
`intake`. Verify afterwards that the accounts which are *supposed* to be
in `alerts` (`nyxloom-admin`, `nyxloom-daemon`, `nyxloom-operator`) still are
(`mmctl --local channel users list nyxloom:alerts --all`) — converting a
channel does not drop members, but it is one command
to confirm rather than assume.

**Option B — accept it.** Defensible: the accounts in question are all
nyxloom's own, `alerts` carries operator notifications rather than secrets,
and the credential with the widest distribution (`installer_webhook_url`) is
post-only and targets `installs`. If you choose this, **write it down** —
an accepted risk that nobody recorded is indistinguishable from one nobody
noticed.

Either way, decide before step 3. Exposure is what turns "an account on this
server" into a thing an outsider might eventually get hold of.

### 3. Flip exposure

```bash
$EDITOR nyxloom/mattermost/ciu.defaults.toml.j2     # expose_public = false -> true
ciu up --dir nyxloom/mattermost --dry-run -y $R
```

Before the real `ciu up`, confirm the render actually carries exposure — a
silent no-op here is the failure mode that wastes the most time:

```bash
grep -E 'traefik|ingress|SITEURL|VARYBYHEADER' nyxloom/mattermost/ciu.compose.yml
# expect: traefik.enable=true, router rule Host(`mattermost.gstammtisch.dchive.de`),
#         traefik.docker.network=ingress_public, an `ingress` network on the app
#         service, MM_SERVICESETTINGS_SITEURL=https://mattermost.gstammtisch.dchive.de,
#         and MM_RATELIMITSETTINGS_VARYBYHEADER=X-Forwarded-For
```

That last one is not cosmetic: behind tls-edge every request arrives from the
Traefik container's address, so if the keying did not switch the rate limiter
would put the whole internet in one bucket.

```bash
ciu up --dir nyxloom/mattermost -y $R
```

Container recreates again → **re-run the governance check from step 1**.

### 4. Verify from OUTSIDE

```bash
dig +short mattermost.gstammtisch.dchive.de
curl -sSI https://mattermost.gstammtisch.dchive.de/ | head -1     # 200
curl -sS  https://mattermost.gstammtisch.dchive.de/api/v4/system/ping
openssl s_client -connect mattermost.gstammtisch.dchive.de:443 \
  -servername mattermost.gstammtisch.dchive.de </dev/null 2>/dev/null | openssl x509 -noout -dates -subject
```

Re-run the **step 2 invite-id probe against the public URL** — internally-true
is not externally-true, and this is the whole reason the flip is gated:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  "https://mattermost.gstammtisch.dchive.de/api/v4/users?iid=$IID" \
  -H 'Content-Type: application/json' \
  -d '{"email":"probe@invalid.example","username":"p110probe","password":"Pr0be-Pw!2026"}'
# expect 501 — the same refusal as step 2, now proven from outside the host
```

Then the thing the flip actually buys: `installer_webhook_url` is built on
`siteurl`, so it was **unreachable from an install host until this moment**.
The hook re-persists it on the step-3 `ciu up`; prove it end to end from
outside the private network:

```bash
cat nyxloom/mattermost/.ciu/secrets/installer_webhook_url   # now https://mattermost...
curl -sS -X POST "$(cat nyxloom/mattermost/.ciu/secrets/installer_webhook_url)" \
  -H 'Content-Type: application/json' -d '{"text":"P110 external delivery check"}'
docker exec nyxloom-prod-mattermost /mattermost/bin/mmctl --local post list nyxloom:installs --number 3
```

Also confirm the rate limiter is keyed on the real client, not on Traefik:
two clients from different addresses must not share a budget. The cheap
version is to check that a burst from one source returns `429` while the
operator's browser is unaffected.

### 5. Rollback

Exposure is reversible on its own:

```bash
$EDITOR nyxloom/mattermost/ciu.defaults.toml.j2     # expose_public = true -> false
ciu up --dir nyxloom/mattermost -y $R               # recreates: re-check governance
```

That removes the Traefik labels and the `ingress_public` join, and re-points
`SITEURL` (and therefore the next-minted `installer_webhook_url`) back at the
internal address. Already-minted webhook ids keep working over the private
bridge; the persisted `installer_webhook_url` file goes back to the internal
form on that same run.

**The hardening from step 1 needs no separate revert and should not get one.**
Nothing in it is conditional on being public: it closes an anonymous
account-creation route, an OAuth authorization server, a 180-day session, and
a directory disclosure that were all equally live while the stack was
internal-only. The single value that *is* topology-dependent —
`MM_RATELIMITSETTINGS_VARYBYHEADER` — is emitted by the template's own
`expose_public` branch, so it reverts itself as part of the same edit and
needs no manual step.

## Storage — bind-mounted hostdirs + named volumes (nyxloom-P107)

| Path in container | Backing | Ownership | Why |
|---|---|---|---|
| `/var/lib/postgresql/data` | hostdir `vol-postgres-data` | `70:DOCKER_GID` `0700` | the data that matters; host-visible for `du`/wipe |
| `/mattermost/config` | hostdir `vol-mattermost-config` | `2000:DOCKER_GID` `0770` | `config.json` is the file an operator reads and diffs |
| `/mattermost/logs` | hostdir `vol-mattermost-logs` | `2000:DOCKER_GID` `0770` | grep across restarts; makes the one unbounded-growth surface visible |
| `/mattermost/data` | named volume | image | file attachments are **on** (nyxloom-P111) — holds uploaded files; still a named volume, not host-visible (residual, see `enable_file_attachments`'s comment in `ciu.defaults.toml.j2`) |
| `/mattermost/plugins`, `/mattermost/client/plugins` | named volumes | image | plugin framework is **off**; these exist only because the image declares `VOLUME` on both paths |

The three hostdirs are ciu-managed (S6.1/S6.3), auto-pathed as
`<stack>/vol-<service>-<purpose>`, and pre-owned through ciu's S6.5 root
helper — `vol-*/` is gitignored.

**These are deliberately NOT `external` volumes.** External would *protect*
them from teardown; the requirement is the opposite — the ability to wipe on
demand. `ciu down` preserves them.

> ### ⚠️ Neither `ciu up --reset` NOR `ciu clean` wipes the hostdirs on this root
>
> Both are broken, for the **same single missing key**, and the second one
> fails *quietly* — which is worse. Measured on ciu 7.12.0 and traced through
> the installed source (nyxloom-P110):
>
> * `ciu up --dir mattermost --reset` →
>   `[ERROR] deploy.labels.prefix is required for reset`.
>   `engine.reset_service` raises that **before its Step 1**, because
>   `nyxloom/ciu.global.defaults.toml.j2` does not set the key.
> * `ciu clean` calls **the same `engine.reset_service`**, and
>   `deploy.action_clean` wraps it in `except Exception` — it prints
>   `reset failed for <stack>`, sets `rc=1`, and **carries on**. Its own
>   Step 3 and Step 4 then remove docker named volumes and networks, so the
>   command looks like it did most of its job. It did not do the part that
>   matters here: removing the `vol-*` hostdirs is `reset_service`'s **Step
>   2**, inside the call that never ran.
>
> An earlier revision of this section said "use `ciu clean` instead", which
> was wrong in exactly the way that costs an afternoon: the command runs,
> reports a failure for one stack among several, and leaves a full Postgres
> data directory behind for the next `ciu up` to adopt as if it were fresh.
>
> **The only wipe path that works today** is the root helper — and the three
> `vol-*` trees are uid 70 / uid 2000, so the operator cannot `rm` them
> directly. This is the block this package's own throwaway teardown used:
>
> ```bash
> P=/home/vb/volkb79-2/vbpub/nyxloom/mattermost
> docker run --rm -v "$P":/t alpine:3.20 sh -c \
>   'rm -rf /t/vol-postgres-data /t/vol-mattermost-config /t/vol-mattermost-logs'
> ```
>
> Remove the containers and named volumes alongside it (`docker rm -f
> nyxloom-prod-mattermost nyxloom-prod-mattermost-db`, then
> `docker volume rm $(docker volume ls -q --filter name=nyxloom-prod-mattermost)`).
>
> **Fixing it at the source** is one line — `labels.prefix` under `[deploy]`
> in `../ciu.global.defaults.toml.j2` — and it unblocks both commands for
> every stack on this root. nyxloom-P110 deliberately did **not** make that
> change: it is a root-level file shared with `ntfy`, `nyxloomd` and
> `pwmcp-instance`, and it converts `ciu clean` from partly-inert into
> genuinely destructive for all of them, which is a decision for whoever owns
> the root rather than a side effect of a Mattermost package. One fact for
> whoever does it, since it is the thing that looks scary and is not: ciu uses
> the prefix in exactly one place, Step 4's orphan-sweep filter
> `label=<prefix>.component=<service>`, and **ciu never writes that label** —
> only a consumer's own compose template would. nyxloom's templates do not, so
> adding the key relabels nothing and orphans nothing; the sweep simply
> matches zero containers. (v8 drops `labels.prefix` entirely for fixed
> `ciu.*` ownership labels, so this is a v7-only wart either way.)

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
# NOTE: --dry-run is NOT read-only (CIU-103) -- it still runs the real
# post_compose hook against the LIVE container. Here that means the hook
# sees the new render's `true` but the container it's still talking to has
# not recreated yet, so it correctly REFUSES to mint against a server that
# doesn't have the feature enabled yet and this command exits rc=1. That is
# expected -- no token or secret file is written on this refusal -- and the
# very next (real) `ciu up` proceeds normally once the container recreates.
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

