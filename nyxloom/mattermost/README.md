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

Two incoming webhooks, one per producer (the operator account is a human login
and gets none — an unused credential is worse than no credential):

| Secret file | Channel | Posts as | URL base |
|---|---|---|---|
| `daemon_webhook_url` | `alerts` | `nyxloom-daemon` | internal bridge address |
| `installer_webhook_url` | `installs` | `nyxloom-installer` | `MM_SERVICESETTINGS_SITEURL` |

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

`alerts` is a **public** channel, and the team the hook creates is an open team
(`type: "O"`, `allow_open_invite: true`). Measured on the throwaway, logged in
as `nyxloom-installer` — an account deliberately left OUT of `alerts`:
`GET /api/v4/channels/<alerts>/posts` → **200** (it reads the channel without
being a member) and `POST /api/v4/channels/<alerts>/members` → **201** (it
joins). So
`ciu.defaults.toml.j2`'s claim that a compromised install host "cannot read
nyxloom's own operator traffic" is weaker than it reads. Closing it means
making `alerts` private, and the hook only ever *creates* channels: setting
`private = true` in `[[mattermost.provision.channels]]` would silently do
nothing to the existing live channel. It is therefore an **operator action on
the live instance** (convert the channel, then set the flag so a future fresh
create matches), not a code change that can be verified from a package. The
blast radius that actually matters — the installer's webhook URL — is
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
- No file attachments, no plugin framework (the prepackaged playbooks/AI
  plugins would otherwise run their own processes inside this 2g cgroup), no
  marketplace, no personal access tokens, no outgoing webhooks, no slash
  commands, no telemetry, no email (no SMTP is configured), no OAuth2
  authorization server.
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

Steps 1–2 are worth doing **on their own** even if the flip is postponed: none
of the hardening is conditional on being public.

```bash
cd /workspaces/vbpub
R="--define-root /workspaces/vbpub/nyxloom"
```

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
| `/mattermost/data` | named volume | image | file attachments are **disabled** — nothing to read |
| `/mattermost/plugins`, `/mattermost/client/plugins` | named volumes | image | plugin framework is **off**; these exist only because the image declares `VOLUME` on both paths |

The three hostdirs are ciu-managed (S6.1/S6.3), auto-pathed as
`<stack>/vol-<service>-<purpose>`, and pre-owned through ciu's S6.5 root
helper — `vol-*/` is gitignored.

**These are deliberately NOT `external` volumes.** External would *protect*
them from teardown; the requirement is the opposite — the ability to wipe on
demand. `ciu down` preserves them; **`ciu clean` deletes them** (S6.4, which
routes to the PHYSICAL path under DooD and degrades to the root helper for the
uid-70/uid-2000 subtrees the operator cannot remove himself), taking every
account, channel, message and webhook with them.

> **`ciu up --reset` does not work on this root** and the previous wording here
> promised it did. Measured (ciu 7.12.0, nyxloom-P110):
> `[ERROR] deploy.labels.prefix is required for reset` — `engine.py` requires
> that key for the reset path and `../ciu.global.defaults.toml.j2` does not set
> it. (v8 drops `labels.prefix` entirely in favour of fixed `ciu.*` ownership
> labels, so this is a v7-only wart.) Until the key is added, wipe with
> `ciu clean`, or remove the three `vol-*` trees through a root helper
> container — the uid-70/uid-2000 subtrees cannot be removed by the operator
> directly:
>
> ```bash
> P=/home/vb/volkb79-2/vbpub/nyxloom/mattermost
> docker run --rm -v "$P":/t alpine:3.20 sh -c \
>   'rm -rf /t/vol-postgres-data /t/vol-mattermost-config /t/vol-mattermost-logs'
> ```

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
