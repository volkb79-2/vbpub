# mattermost — nyxloom notification channel

Self-hosted [Mattermost](https://mattermost.com/) Team Edition + a **dedicated**
PostgreSQL, as one CIU-managed stack. Its HTTPS endpoint is reachable from
outside the host, while account creation stays under our control: we provision
the users and credentials, and public self-signup is disabled. The built-in
Calls plugin provides 1:1 audio and screen sharing. See the
[design guide](docs/DESIGN-GUIDE.md) for the network and privacy choices and
the [consumer guide](docs/CONSUMERS.md) for client use and firewall details.
Mattermost replaces [ntfy](../nyxloom/ntfy/) as nyxloom's live notification
channel (backlog NL-17, package nyxloom-P106). ntfy is kept — stopped, and
still selectable as a `notify.py` backend — not deleted.

Payloads are typed-fields-only per nyxloom SPEC §13: `notify.py`'s Mattermost
backend folds the note's `title`/`body`/`click`/`priority`/`tags` into the
incoming webhook's Markdown `text`, because that is the only field Mattermost
renders.

## Files

- `ciu.defaults.toml.j2` + `ciu.compose.yml.j2` — ciu v2 stack package.
- `hooks/post_compose_provision.py` — idempotent team/channel/account/webhook
  provisioning, run by ciu on every `ciu up` (S9.1 `post_compose`).
- `docker-compose.yml` — legacy plain-compose fallback, carrying ABSOLUTE
  PHYSICAL host paths (DooD) and hand-inlined governance caps. It does not
  include the live shared ingress or Calls media routing; CIU is the supported
  deployment path for this instance.
- `docs/DESIGN-GUIDE.md` — why this endpoint is public with managed users,
  which data leaves the instance, and how Calls media is routed.
- `docs/CONSUMERS.md` — how members use Calls and what the edge firewall needs.

## Deploy (ciu-managed — the ONLY supported path)

```bash
cd /workspaces/vbpub
ciu env generate --root-folder /workspaces/vbpub/mattermost-server   # first time only; also creates the instance network
ciu up --dir mattermost-server -y
```

(The flag is `--root-folder`; ciu 7.15 has no `--define-root`. This root declares
no deployment profile, so a bare `ciu up` selects nothing: always pass `--dir`.)

**Location (MM-MOVE, 2026-10-07).** This directory used to be `nyxloom/mattermost`,
a sub-stack of nyxloom's ciu root. It is now its OWN standalone ciu root at the
vbpub root (the pwmcp layout: stack files in the root directory, plus
`ciu.global.defaults.toml.j2`). Nothing was migrated: the instance is recreated
by ciu with fresh users.

**Folder name (MM-RENAME, 2026-10-07).** The folder was renamed `mattermost/` to
`mattermost-server/` for clarity. Only the project FOLDER changed: the ciu
stack/service name, `deploy.project_name`, the `[mattermost.secrets]` keys, the
secret-store subpath `.ciu/secrets/mattermost/<name>`, `GEN_LOCAL:mattermost/<name>`
and the container/hostname `mattermost...` all stay. See `docs/MM-RENAME-REPORT.md`.

Containers: `{container_prefix}-mattermost` and `{container_prefix}-mattermost-db`,
where `container_prefix` is `{deploy.project_name}-{deploy.environment_tag}`
(`deploy.project_name = "mattermost"` and `environment_tag = "$INSTANCE_ID"` in
`./ciu.global.defaults.toml.j2`; the fixed `"prod"` was retired by CIU-104).
The instance id comes from this checkout's own `ciu.instance.generated.toml`
and differs per checkout, by design (no two checkouts can collide on a
container name). Do NOT copy a literal container name into any consumer config:
read it from `ciu resolve` / `docker ps` (nyxloom backlog NL-38).

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
`mattermost-server/.ciu/secrets/intake_pat`, alongside the webhook URLs and on
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
`mattermost-server/.ciu/secrets/`. Both are gitignored by `**/.ciu/`, both are
0440 `vscode:docker`. The split is ciu's, not a choice: a webhook id does not
exist until Mattermost mints it, so no directive can express it, and S9.4a is
the channel for exactly that case.

```bash
export NYXLOOM_WEBHOOK_URL="$(cat /workspaces/vbpub/mattermost-server/.ciu/secrets/daemon_webhook_url)"
```

**Rotating a webhook**: `mmctl --local webhook delete <id>`, then `ciu up`. The
hook mints a fresh one and re-persists the URL. Do **not** leave two webhooks
with the same display name — the hook refuses to guess which is current and
fails the deploy naming the count.

**The `installer_webhook_url` is reachable from an install host as of the
2026-09-09 public-endpoint cutover** (`expose_public = true`).
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
different endpoint). Lands in `mattermost-server/.ciu/secrets/installer_pat`,
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
docker exec <container_prefix>-mattermost mmctl --local post list nyxloom:alerts --number 5
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
export NYXLOOM_WEBHOOK_URL="$(cat /workspaces/vbpub/mattermost-server/.ciu/secrets/daemon_webhook_url)"
```

The feature-intake bridge is wired in the same file's `[intake_bridge]` table
(nyxloom-P109), defaulting to the `mmctl` transport so it needs no server
change. Its two credentials are exported the same way, and the ingress stays
closed until an operator is named:

```bash
export NYXLOOM_INTAKE_WEBHOOK_URL="$(cat /workspaces/vbpub/mattermost-server/.ciu/secrets/intake_webhook_url)"
export NYXLOOM_INTAKE_MM_TOKEN="$(cat /workspaces/vbpub/mattermost-server/.ciu/secrets/intake_pat)"   # `rest` only
export NYXLOOM_CHANNEL_OPERATOR_ID="<operator identity>"
nyxloom intake-bridge poll nyxloom
```

That default runs anywhere the docker socket does. `--transport rest` does
NOT: it dials `base_url` over the network below, so it only works from inside
`<container_prefix>-mattermost_internal` — which is why the transport is a config
selector and not a fallback chain. See step 5b of the P109 recipe.

`/workspaces/dstdns`'s own config is a **separate repo and out of scope** —
it still points at its previous channel and is left for a dstdns-side session.

Internal service-to-service traffic must share a Docker network with
Mattermost. The stack owns a private bridge
(`<container_prefix>-mattermost_internal`); attach an internal consumer (or,
for a one-off check from the devcontainer) with:

```bash
docker network connect <container_prefix>-mattermost_internal <container>
# ... and afterwards:
docker network disconnect <container_prefix>-mattermost_internal <container>
```

Browser, desktop, and mobile clients outside the host use the public HTTPS
route through tls-edge, described below.

## Public endpoint, managed accounts

`expose_public = true` publishes `mattermost.gstammtisch.dchive.de` through
the host's tls-edge service and sets Mattermost's `SiteURL` to that HTTPS
address. This is intentional: clients need to reach the service from outside
the host. The phrase “internal use” describes account ownership, not network
location. We create and distribute accounts and credentials; Mattermost's
public user-creation route is disabled, and the CIU provisioning hook creates
the declared service and operator accounts. This is not an open-registration
community server.

The current live membership and channel posture is recorded in
[`ciu.defaults.toml.j2`](ciu.defaults.toml.j2) and the public-exposure audit
below. The old staged go-public recipe has been removed because the cutover
has already happened; see [Public endpoint and rollback](#public-endpoint-and-rollback)
for the current deployment control and the [Calls consumer guide](docs/CONSUMERS.md)
for client access.

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

### Risk found and closed: `alerts` channel membership

The security audit found that `alerts` was public: `nyxloom-installer`, an
account deliberately left out of the channel, could read its posts and join.
That was a separate issue from self-signup, because an existing team member
could join a public channel even when no new accounts could be created.

The live channel was changed to private during the 2026-09-09 cutover. The
installer read probe then returned **403**, existing members and the daemon
webhook remained in place, and `ciu.defaults.toml.j2` now declares
`alerts` private for future creates. The `installs` channel remains public
because its dedicated webhook is post-only and is intended for producers
outside this host.

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
- The plugin framework is enabled only for built-in Calls. All other bundled
  plugin states are explicitly disabled; plugin uploads and both Marketplaces
  are disabled. Outgoing webhooks, slash commands, diagnostics reporting,
  security-fix alerts, email (no SMTP is configured), and the OAuth2
  authorization server are disabled.
- Calls uses Mattermost's integrated media service. The public edge forwards
  TCP and UDP on port 8443 to the Calls listener in this Mattermost container;
  it does not use an external media provider. See the
  [Calls design](docs/DESIGN-GUIDE.md#calls-media-path).
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

## Public endpoint and rollback

The public endpoint is already enabled. `expose_public = true` adds the
Mattermost service to `ingress_public`, installs its HTTPS router labels, and
sets `SiteURL` to `https://mattermost.gstammtisch.dchive.de`. To make the
service private again, change that value to `false` and run the supported
`ciu up --dir mattermost-server -y` workflow. That removes the edge route and
restores the internal `SiteURL`; it also makes the service unavailable to
clients outside the host. Account provisioning is independent of this network
switch. Calls additionally needs the edge's TCP and UDP media route; see the
[consumer guide](docs/CONSUMERS.md) before changing that topology.

## Outbound requests and diagnostics

The following are the configured outbound behaviors relevant to privacy:

- **Product notices:** Mattermost makes an HTTPS GET to
  `https://notices.mattermost.com/` every 3600 seconds. It retrieves notice
  definitions that may be shown in the client; it is not a chat or file
  upload. The remote endpoint can see the server's IP address and request
  timing. See the [notices repository](https://github.com/mattermost/notices).
- **`EnableSecurityFixAlert`: false.** Mattermost's
  [telemetry guide](https://docs.mattermost.com/administration-guide/manage/telemetry)
  describes an optional once-per-24-hours diagnostic report containing
  build/version, edition, OS,
  database type/version, team/user counts, a diagnostic ID, unit-test status,
  and check time. It is disabled here, so this instance does not send that
  report or receive those alerts. This is separate from the notice fetch.
- **`EnableDiagnostics`: false.** Server errors and panics are not sent to
  Mattermost's Sentry endpoint. Mattermost says these reports omit message
  content and personally identifiable information; this instance opts out.
- **Marketplace:** both local Marketplace UI and remote Marketplace access
  are disabled. Plugin uploads are disabled, and Calls is the only enabled
  bundled plugin.
- **User-triggered integrations:** link previews may fetch URLs users post,
  and the enabled GIF picker uses GIPHY. Those features can disclose the
  requested URL or GIF search to their destination service. Outgoing webhooks
  are disabled here. These feature requests do not upload the conversation
  history as a whole.

These settings document the known paths; they are not packet-level proof that
no other egress exists. For the data boundary, Calls design, and how to observe
host egress, see [data and outbound traffic](docs/DESIGN-GUIDE.md#data-and-outbound-traffic).

## Storage — bind-mounted hostdirs + named volumes (nyxloom-P107)

| Path in container | Backing | Ownership | Why |
|---|---|---|---|
| `/var/lib/postgresql/data` | hostdir `vol-postgres-data` | `70:DOCKER_GID` `0700` | the data that matters; host-visible for `du`/wipe |
| `/mattermost/config` | hostdir `vol-mattermost-config` | `2000:DOCKER_GID` `0770` | `config.json` is the file an operator reads and diffs |
| `/mattermost/logs` | hostdir `vol-mattermost-logs` | `2000:DOCKER_GID` `0770` | grep across restarts; makes the one unbounded-growth surface visible |
| `/mattermost/data` | named volume | image | file attachments are **on** (nyxloom-P111) — holds uploaded files; still a named volume, not host-visible (residual, see `enable_file_attachments`'s comment in `ciu.defaults.toml.j2`) |
| `/mattermost/plugins`, `/mattermost/client/plugins` | named volumes | image | Calls is enabled; these paths retain its installed server and client plugin files |

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
> * `ciu up --dir mattermost-server --reset` →
>   `[ERROR] deploy.labels.prefix is required for reset`.
>   `engine.reset_service` raises that **before its Step 1**, because
>   `ciu.global.defaults.toml.j2` (this root's own, copied from nyxloom's at
>   the MM-MOVE; neither sets the key) does not set the key.
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
> P=/home/vb/volkb79-2/vbpub/mattermost-server
> docker run --rm -v "$P":/t alpine:3.20 sh -c \
>   'rm -rf /t/vol-postgres-data /t/vol-mattermost-config /t/vol-mattermost-logs'
> ```
>
> Remove the containers and named volumes alongside it (`docker rm -f
> <container_prefix>-mattermost <container_prefix>-mattermost-db`, then
> `docker volume rm $(docker volume ls -q --filter name=<container_prefix>-mattermost)`).
>
> **Fixing it at the source** is one line — `labels.prefix` under `[deploy]`
> in `./ciu.global.defaults.toml.j2` — and it unblocks both commands for
> every stack on this root. nyxloom-P110 deliberately did **not** make that
> change: at the time the root file was nyxloom's, shared with `ntfy`,
> `nyxloomd` and `pwmcp-instance`, and it would have converted `ciu clean`
> from partly-inert into genuinely destructive for all of them. Since the
> MM-MOVE this root holds only the Mattermost stack, so the constraint no
> longer applies; the key is still unset (a follow-up, not part of the move). One fact for
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
docker exec <container_prefix>-mattermost-db sh -c \
  'PGPASSWORD=$(cat /run/secrets/postgres_password) pg_dump -U mmuser mattermost' > mattermost.sql
```

### Migrating an EXISTING named-volume deployment to the hostdirs

Required once, from the checkout the stack is deployed from. Verified in
rehearsal (P107) on a copy of the live data.

```bash
# 0. dump first, and keep it OUT of the repo
docker exec <container_prefix>-mattermost-db sh -c \
  'PGPASSWORD=$(cat /run/secrets/postgres_password) pg_dump -U mmuser mattermost' > ~/mattermost.sql

# 1. STOP first — a live copy would need crash recovery
ciu down --root-folder /workspaces/vbpub/mattermost-server
docker rm <container_prefix>-mattermost <container_prefix>-mattermost-db   # no -v: named volumes survive

# 2. let ciu create the hostdirs with the right ownership
ciu up --dir mattermost-server -y --dry-run --root-folder /workspaces/vbpub/mattermost-server

# 3. copy the data in, then RE-ASSERT the ownership `cp -a` clobbers.
#    `cp -a /from/. /to/` copies the SOURCE directory's own owner/mode onto
#    the target, which leaves 70:70 / 2000:2000 — and S6.3 then REFUSES the
#    hostdir on the next `ciu up` as incompatible. This step is not optional.
P=/home/vb/volkb79-2/vbpub/mattermost-server
docker run --rm -v <container_prefix>-mattermost_postgres-data:/from:ro \
  -v "$P/vol-postgres-data":/to alpine:3.20 sh -c 'cp -a /from/. /to/'
docker run --rm -v <container_prefix>-mattermost_mattermost-config:/from:ro \
  -v "$P/vol-mattermost-config":/to alpine:3.20 sh -c 'cp -a /from/. /to/'
docker run --rm -v "$P":/t alpine:3.20 sh -c '
  chown 70:994   /t/vol-postgres-data     && chmod 0700 /t/vol-postgres-data
  chown 2000:994 /t/vol-mattermost-config && chmod 0770 /t/vol-mattermost-config'

# 4. real up, then verify BOTH data and governance — a recreate is exactly
#    where governance silently drops (P106's own lesson).
ciu up --dir mattermost-server -y --root-folder /workspaces/vbpub/mattermost-server
docker exec <container_prefix>-mattermost-db sh -c \
  'PGPASSWORD=$(cat /run/secrets/postgres_password) psql -U mmuser -d mattermost -tAc \
   "select (select count(*) from users), (select count(*) from teams), (select count(*) from channels), (select count(*) from posts)"'
docker inspect <container_prefix>-mattermost <container_prefix>-mattermost-db \
  --format '{{.Name}} {{.HostConfig.CgroupParent}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'
docker exec <container_prefix>-mattermost-db cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max
```

The old named volumes are left in place after step 4 as a rollback point;
delete them (`docker volume rm <container_prefix>-mattermost_postgres-data
<container_prefix>-mattermost_mattermost-config
<container_prefix>-mattermost_mattermost-logs`) only once the bind-mounted stack has
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
ciu up --dir mattermost-server -y --root-folder /workspaces/vbpub/mattermost-server
docker exec <container_prefix>-mattermost mmctl --local channel list nyxloom   # expect `intake (private)`
docker exec <container_prefix>-mattermost mmctl --local channel users list nyxloom:intake --all

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
ciu up --dir mattermost-server -y --dry-run --root-folder /workspaces/vbpub/mattermost-server
# ciu's rendered compose output is `ciu.compose.yml` at the stack root (S8.5)
# -- NOT the hand-maintained `docker-compose.yml` fallback beside it, which
# receives no ciu overlay and is not what `ciu up` deploys.
grep ENABLEUSERACCESSTOKENS mattermost-server/ciu.compose.yml   # expect "true"

# 2. Real up. The container RECREATES (an env change), so re-verify governance
#    in the same breath -- a recreate is exactly where it silently drops.
ciu up --dir mattermost-server -y --root-folder /workspaces/vbpub/mattermost-server
docker inspect <container_prefix>-mattermost \
  --format '{{.Name}} {{.HostConfig.CgroupParent}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'

# 3. Verify the PAT was minted ONCE and is idempotent. The second `ciu up`
#    must print `tokens_minted=[]` -- if it mints again, STOP and revoke.
#    Capture, THEN read: a `| grep` here would replace ciu's own exit status
#    with grep's, so a `ciu up` that failed outright would still look fine as
#    long as the word appeared somewhere (LESSONS L4, the same reason gate
#    verdicts are never read from a pipe tail).
docker exec <container_prefix>-mattermost mmctl --local token list nyxloom-intake
ls -l mattermost-server/.ciu/secrets/intake_pat          # 0440, non-empty
ciu up --dir mattermost-server -y --root-folder /workspaces/vbpub/mattermost-server \
  >/tmp/p109-reup.log 2>&1; echo "ciu up rc=$?"           # rc MUST be 0
grep tokens_minted /tmp/p109-reup.log                     # expect tokens_minted=[]
docker exec <container_prefix>-mattermost mmctl --local token list nyxloom-intake   # still exactly ONE

# 4. Point the bridge at it. Both credentials come from the stack store and
#    neither is committed; NYXLOOM_INTAKE_MM_TOKEN wins over any toml value.
export NYXLOOM_INTAKE_WEBHOOK_URL="$(cat mattermost-server/.ciu/secrets/intake_webhook_url)"
export NYXLOOM_INTAKE_MM_TOKEN="$(cat mattermost-server/.ciu/secrets/intake_pat)"
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
`[intake_bridge].base_url` is `http://<container_prefix>-mattermost:8065`, and that
name exists only inside the `<container_prefix>-mattermost_internal` docker network —
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
docker run --rm --network <container_prefix>-mattermost_internal \
  --cpus=1 --memory=256m \
  -e MM_TOKEN="$NYXLOOM_INTAKE_MM_TOKEN" \
  python:3-slim python3 -c '
import json, os, urllib.request
BASE = "http://<container_prefix>-mattermost:8065"
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
