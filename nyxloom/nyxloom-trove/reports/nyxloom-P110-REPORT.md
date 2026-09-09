# nyxloom-P110 — Mattermost hardening for public exposure, fresh-create re-verification, and the go-public recipe

**Branch**: `nyxloom-P110` · **Worktree**: `/workspaces/vbpub/.worktrees/nyxloom-p110`
**Date**: 2026-09-09 · **Image under test**: `mattermost/mattermost-team-edition:11.10.1`

## Summary

Three things were asked for and all three are done: a security audit for public
exposure, an empirical re-verification of account provisioning **from a
genuinely empty instance**, and a written (not executed) post-merge recipe.

The audit found **seven** settings whose silent default was wrong for an
internet-facing instance — including one that made this README's own
"no self-signup — admin-created only" claim **false**: with
`ENABLEOPENSERVER=false`, an anonymous request carrying a team's `invite_id`
created a full account (HTTP **201**).

The fresh-create verification found **three real defects**, none of which could
ever have shown up on the live instance, because every previous run had P106's
hand-made state already in place:

1. **The provisioning hook could not provision an empty instance at all.**
   `mmctl team search <missing>` exits **0**, so a returncode probe reported the
   team as existing, `team create` was skipped, and the deploy aborted.
2. **The first account created would silently have become a system admin.**
   Mattermost promotes the first-ever account on an empty server; that would
   have been `nyxloom-daemon`, which declares `system_admin = false` and whose
   webhook credential is the one destined for third-party install hosts.
3. **Only three of the four documented accounts were created.** Nothing ever
   created `nyxloom-admin`; it existed purely as an artefact of P106's manual
   bootstrap and would have vanished on any rebuild.

All three are fixed and re-verified end to end against a genuinely empty
instance, twice (create, then idempotent re-run).

**No live stack was touched.** No `ciu up`, no `docker exec` mutation and no
read of `nyxloom-prod-mattermost` was performed at any point; `expose_public`
remains `false` in the committed config. Every measurement in this report comes
from a throwaway instance in this package's own `ciu worktree`.

> **Round 2.** Independent review returned NEEDS-FIXES on three bounded items
> and **cleared the highest-risk one** (whether declaring `nyxloom-admin` could
> touch the live bootstrap admin's password — it cannot). All three are fixed,
> one optional hardening item was taken, `main` (now carrying nyxloom-P109) is
> merged in, and the suite and gate were re-run at the merged tip. See
> **"Review round 1"** near the end for what changed and why.

## Isolation — the corrected approach, verified before trusting anything

The previous attempt at this task caused a contained production incident: an
`environment_tag` override in the **same stack directory** did not isolate
`[<svc>.hostdir]` paths (they are stack-directory-relative), so a scratch stack
bind-mounted live Postgres' data directory.

This package used `ciu worktree create nyxloom-p110` — a genuinely separate
physical directory — and **verified the isolation before running anything**:

```
/nyxloom-p110-mattermost    [.../.worktrees/nyxloom-p110/nyxloom/mattermost/vol-mattermost-config -> /mattermost/config]
                            [.../.worktrees/nyxloom-p110/nyxloom/mattermost/vol-mattermost-logs   -> /mattermost/logs]
/nyxloom-p110-mattermost-db [.../.worktrees/nyxloom-p110/nyxloom/.ciu/secrets/mattermost/postgres_password -> /run/secrets/...]
                            [.../.worktrees/nyxloom-p110/nyxloom/mattermost/vol-postgres-data     -> /var/lib/postgresql/data]
```

Every bind resolves under the worktree. Named volumes, the compose project and
the bridge network are all `nyxloom-p110-*`.

A gitignored `ciu.global.instance.toml.j2` in the worktree set
`deploy.environment_tag = "p110"`. That is **not** the isolation mechanism and
the file says so — the worktree already isolated the paths; the tag only
de-conflicts Docker's global *container-name* namespace on top of a directory
separation that already held. Doing it the other way round is the incident.

The belief that `standalone_root = true` blocks worktree-based `ciu up` is
false, confirmed in passing: `ciu up --dir mattermost --define-root <worktree>/nyxloom`
worked throughout.

Teardown is recorded at the end of this report.

---

## Part 1 — Security hardening audit

Everything below was **measured** against the throwaway. Nothing was taken from
the Mattermost documentation or from a setting's name. That mattered: the two
items most likely to have been waved through — "`ENABLEOPENSERVER=false`
obviously closes signup" and "OAuth surely defaults off" — were both wrong.

### Item 1 — the self-signup gap → **REAL GAP, FIXED**

| Probe (anonymous, no token) | Result |
|---|---|
| `POST /api/v4/users` | **403** `api.user.create_user.no_open_server` |
| `POST /api/v4/users?iid=<team invite_id>` | **201 CREATED** — a real `system_user` account |
| `GET /signup_email` | 200, but that is the SPA shell; every route returns 200 |

`ENABLEOPENSERVER=false` closes the bare signup route and **nothing else**.
Anyone holding a team `invite_id` creates an account anonymously.

The id is not meaningfully secret: the team this stack's own hook creates comes
out `type: "O"` with `allow_open_invite: true` and a stable `invite_id`, which
is in the invite link every team member can copy — including
`nyxloom-installer`, whose credential is meant to live on third-party install
hosts. Behind `expose_public = true` that is an unauthenticated
account-creation endpoint on the public internet.

**Fix**: `MM_TEAMSETTINGS_ENABLEUSERCREATION: "false"` (was `"true"`).

**Verified after the change, on the same instance:**

| Probe | Before | After |
|---|---|---|
| `POST /api/v4/users?iid=<invite_id>` | 201 | **501** `api.user.create_user.signup_email_disabled` |
| `POST /api/v4/users` | 403 | **501** |
| account count | — | unchanged (4) |
| `POST /api/v4/users/login` as `nyxloom-operator` | — | **200** (human login unaffected) |
| `POST /api/v4/users/login` as `nyxloom-admin` | — | **200** |
| `mmctl --local user create` (the hook's own path) | — | **works** — all four accounts created on a fresh instance with this set |

The flag gates account *creation over the API*, not authentication, and not the
local admin socket. "Admin-created only" is now literally true instead of
aspirational; the README line that claimed it has been corrected to say what is
actually enforced.

### Item 2 — OAuth → **REAL GAP, FIXED**

`ServiceSettings.EnableOAuthServiceProvider` **defaults to `true`** (measured —
this was the guess most likely to have gone the other way). Team Edition ships
an OAuth2 authorization server switched on: a system admin can register
third-party OAuth apps that mint bearer credentials for this server. Nothing in
nyxloom uses it.

**Fix**: `MM_SERVICESETTINGS_ENABLEOAUTHSERVICEPROVIDER: "false"`, set
explicitly rather than left to an unstated default — the same rule this stack
already applies to `cgroup_parent` and `device`.

### Item 3 — non-admin webhook creation → **VERIFIED SAFE, no change**

The setting the brief named does not exist any more:

```
$ mmctl --local config get ServiceSettings.EnableOnlyAdminIntegrations
Error: invalid key
```

It was replaced by the permissions scheme, and the scheme is already correct:

- `system_user`, `team_user`, `channel_user` hold **no** `*webhook*` permission.
- A logged-in non-admin `POST /api/v4/hooks/incoming` → **403**
  `api.context.permissions.app_error`.
- The identical call as a system admin → **201**, which is the control proving
  the 403 was a permission refusal and not a malformed request.

Nothing to set. The `nyxloom-installer` / `nyxloom-daemon` pattern cannot
self-mint webhooks.

### Item 4 — rate limiting → **REAL GAP, FIXED (and the "is the edge already doing it" question answered)**

- `RateLimitSettings.Enable` **defaults `false`**.
- **tls-edge does not compensate.** Its only entrypoint-level middleware is
  `secure-headers` (`edge-proxy/conf.d/middlewares.yml`: HSTS, nosniff,
  referrer-policy — `frameDeny` deliberately unset). `rateLimit` appears only
  under `ARCHITECTURE.md` **F5 "Traefik middleware additions — Partially
  implemented"** as a *future* item, and in `KNOWN_ISSUES.md` as an unbuilt
  "middleware template library". No definition ships.

So app-level limiting is **not** redundant — it is the only limiter in the path.

The estate already has a pattern for this and it is app-level, so it was
mirrored rather than reinvented: `../ntfy/server.yml` carries
`visitor-request-limit-burst` / `-replenish` / `-subscription-limit` /
`-message-daily-limit` ("defense in depth; all real use is authenticated") plus
`behind-proxy: true` so the limiter keys on the forwarded address.

**Fix**: `MM_RATELIMITSETTINGS_ENABLE: "true"` with `PERSEC`/`MAXBURST`/
`MEMORYSTORESIZE` restated at their measured defaults (10/100/10000) — the
values were fine, `Enable: false` was not.

**Keying is the one genuinely topology-dependent piece**, and it sits inside
the template's existing `expose_public` conditional:

- exposed → `VARYBYREMOTEADDR: "false"` + `VARYBYHEADER: "X-Forwarded-For"`.
  Behind tls-edge every request arrives from the Traefik container's address,
  so the default would put the **entire internet in one bucket** — a single
  client could exhaust the budget for the operator, the daemon and every
  install host at once. That inverts the control rather than weakening it.
- not exposed → `VARYBYREMOTEADDR: "true"`. There is no proxy and no
  `X-Forwarded-For`; keying on an absent header has the mirror-image failure.

Verified live on the throwaway (`expose_public = false`): `RateLimitSettings.Enable`
reads `true`, `VaryByRemoteAddr` reads `true`, `VARYBYHEADER` is correctly
absent from the container env, and a real incoming-webhook POST still delivered
(**HTTP 200**, post visible in `alerts` as `nyxloom-daemon`).

> Wire fact worth recording: `mmctl --local config get RateLimitSettings.VaryByHeader`
> **panics** on 11.10.1 (`reflect: call of reflect.Value.IsNil on string Value`).
> That is an mmctl bug, not a misconfiguration — read that key from
> `docker inspect` instead. The recipe says so.

### Item 5 — brute force / lockout / MFA → **PARTLY VERIFIED-TRUE, one real gap fixed**

| Measured | Verdict |
|---|---|
| `MaximumLoginAttempts` = **10** | The public login form is **not** the zero-lockout surface it was suspected to be. Restated at 10 rather than tightened: two human logins with `GEN_LOCAL` passwords do not need a 3-strike lockout, and a tight threshold is its own denial of service on the operator. A wrong password returns 401 as expected. |
| `SessionLengthWebInHours` = **4320** (180 days) | **Real gap.** A browser session stolen once stayed valid for half a year. Set to `168` (7 days) with `ExtendSessionLengthWithActivity` restated `true`, so the operator's own session is refreshed by use rather than expiring mid-week. |
| `EnableMultifactorAuthentication` = false, and **available in Team Edition** | Turned **on**, deliberately **not** enforced: enabling exposes the per-account opt-in for the human login; `ENFORCE` is server-wide and would apply to accounts that authenticate with a password and cannot hold a TOTP secret. |

This is the item where over-engineering was the risk, and the audit
deliberately declined to: no password-complexity policy was added (the four
account passwords are `GEN_LOCAL`, and a symbol requirement is a way to break
`mmctl user create` for no gain), and the lockout threshold was left alone.

### Item 6 — everything else → **two more real gaps fixed, one non-issue closed out**

| Finding | Measured | Action |
|---|---|---|
| `PrivacySettings.ShowEmailAddress` / `ShowFullName` | both **true**. Any authenticated account — `nyxloom-installer` included — read every other account's email from `GET /api/v4/users/username/<name>`. | Both `false`. Verified after: the same call returns `email: ""`. **Partial and stated as such:** `roles` is not covered by `PrivacySettings` and still comes back, so a caller can still tell which account is the system admin. Closing that needs a permissions-scheme change, not a flag. |
| `ServiceSettings.EnableEmailInvitations` | **true** | `false`. Inert without SMTP, but it is the other invite surface and only the absence of SMTP was stopping it. |
| `ServiceSettings.EnableSecurityFixAlert` | **true** — a telemetry channel | `false`. The README already claimed "no telemetry" and `ENABLEDIAGNOSTICS` was already false; leaving this on made the claim untrue. Cost stated rather than hidden: the pinned image tag becomes the operator's only CVE notification channel. |
| `EnableAPITeamDeletion` / `EnableAPIUserDeletion` / `EnableAPIChannelDeletion` | all **false** | Restated explicitly. These are the ones that turn a stolen admin session into data loss rather than a leak; a measured default is a fact about today's image, not a contract across the next bump. |
| **Clickjacking / response headers** | Mattermost already sends `Content-Security-Policy: frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`; tls-edge adds HSTS at the entrypoint | **Nothing to do.** No per-router Traefik `headers` middleware is warranted — this is precisely the case tls-edge's `secure-headers` leaves `frameDeny` unset for. |

### A residual that is NOT fixed — recorded so it cannot be mistaken for covered

`ciu.defaults.toml.j2` claims `nyxloom-installer`'s exclusion from `alerts`
means "a compromised install host cannot read nyxloom's own operator traffic".
Measured, logged in as `nyxloom-installer`:

- `GET /api/v4/channels/<alerts>/posts` → **200** (reads it without membership)
- `POST /api/v4/channels/<alerts>/members` → **201** (joins it)

`alerts` is a **public** channel in an open team, so any team member can read
and join it. The claim is weaker than it reads.

This was **not** silently half-fixed, because it cannot be fixed from a
package: the hook only ever *creates* channels, so setting `private = true` in
`[[mattermost.provision.channels]]` would do nothing to the existing live
channel and would produce a config that lies about the running state. It is an
**operator action on the live instance** (convert `alerts` to private, then set
the flag so a future fresh create matches), and it is written up as such in the
README. The blast radius that actually matters — the installer's webhook URL —
is post-only and targets `installs` alone, unchanged.

### An incidental finding: the README's documented wipe path does not work

Trying to reset the throwaway the way `mattermost/README.md` says to:

```
$ ciu up --dir mattermost --reset -y
[ERROR] deploy.labels.prefix is required for reset
```

`engine.py` requires `deploy.labels.prefix` for the reset path and
`nyxloom/ciu.global.defaults.toml.j2` does not set it, so the README's claim
that "`ciu up --reset` and `ciu clean` delete them" was **false for
`--reset`** on this root. (v8 drops `labels.prefix` entirely in favour of
fixed `ciu.*` ownership labels — CIU-V8 R-15 — so it is a v7-only wart.)

The README now says what actually works and shows the root-helper removal the
throwaway teardown used. **No ciu backlog entry was filed**: nothing in ciu
misbehaved, nyxloom's root simply does not set a key ciu requires, and the key
is already scheduled for removal upstream. Flagged for the controller in case
adding `deploy.labels.prefix` to the nyxloom root is preferred over the
documentation fix.

### One operational consequence, measured and documented

An `MM_*` setting **cannot be changed at runtime**:

```
$ mmctl --local config set TeamSettings.EnableUserCreation false
Value changed successfully
$ mmctl --local config get TeamSettings.EnableUserCreation
true
```

The write lands in `config.json` and the environment overlay wins. So the
System Console shows these greyed out, and *verifying* hardening means reading
the running config or `docker inspect` — never a successful `config set`. The
recipe's verification step is built on that.

---

## Part 2 — Fresh-instance provisioning re-verification

Method: bring the stack up from **nothing** in the worktree — no containers, no
named volumes, no `vol-*` hostdirs, and both secret stores (project *and*
stack) deleted — then run the hook, inspect the result, run it again.

### What the first attempt found

The very first `ciu up` against an empty instance **failed the deploy**:

```
[ERROR] [hook] .../post_compose_provision.py: cannot list channels of team 'nyxloom'
        (rc=1): unable to find team "nyxloom"
```

Root cause, measured:

```
$ mmctl --local team search nyxloom          # empty instance
Unable to find team 'nyxloom'
$ echo $?
0
```

`_team_exists` was `return rc == 0`. Exit 0 for "not found" → the hook believed
the team existed → `team create` skipped → the next step aborted. Invisible for
as long as it was, because every previous run had P106's team already there.

`mmctl user search <missing>` exits **1**, so `_user_exists` is *not* affected —
confirmed in the same session, which is why only the one probe was changed.

**Fix**: `_team_exists` parses the output, matches the team name **exactly**
(mmctl matches on a prefix: `team search probe` prints `probe-team: ...`, so
"a row came back" is not "the team exists"), raises when the probe itself
fails, and refuses on an unrecognised row rather than reporting "absent" —
the same drift discipline the other list parsers in the file already use.

### Second defect — Mattermost's first-account auto-promotion

Measured on an empty server:

```
create probe-first   -> roles: "system_admin system_user"
create probe-second  -> roles: "system_user"
```

On a fresh `ciu up`, account #1 was whichever entry headed
`[[mattermost.provision.accounts]]` — `nyxloom-daemon`, which declares
`system_admin = false` precisely so the thing that only ever POSTs has no
administrative rights. A fresh create would have handed server administration
to the account whose webhook credential ships to install hosts. Confirmed live:
the first corrected run reported `admin_role_stripped=['nyxloom-daemon']`.

Ordering turned out to be load-bearing, and it too was measured rather than
assumed:

```
$ mmctl --local roles member probe-first
can't update roles for user "probe-first": Cannot demote last System Admin.   (rc=1)
```

So the new `_demote_unintended_admins` step runs **after** the whole
create-and-promote pass, once `nyxloom-operator` exists to be the other admin.
Its scope is deliberately narrow — only accounts **this run created**, and only
where the spec says `system_admin` is not wanted. P107's rule that the hook
never touches a pre-existing account (so an operator's manual promotion
survives) is unchanged, and is exactly why this cannot be a blanket
"reconcile roles downward" pass.

### Third defect — only three of the four documented accounts existed

A genuinely empty instance produced **three** accounts, not four. Nothing
creates `nyxloom-admin`: P107 deliberately left it undeclared, so it existed
only as an artefact of P106's manual bootstrap on the live server and would
have disappeared on any rebuild — including a `ciu up --reset`.

The operator's requirement is "our 4 users are created **on fresh stack
create**", so `nyxloom-admin` is now declared, **first** in the list, so that
Mattermost's first-account promotion lands on the account that is supposed to
have it. A second, independent reason: `mattermost_admin_password` was declared
`consumed_by = "hook"` — S4.20's marker for "deliberately consumed outside
compose" — and no hook step consumed it, so the marker was suppressing an
accurate warning.

**This reverses a P107 decision and is flagged for the reviewer as the one
judgment call in the package.** Live-safety analysis: on the live instance the
account already exists, so `_ensure_account` creates nothing, sends no password
and revokes no role; `team users add` is a server-side no-op; the declared
`system_admin` is already held; and `alerts` membership already exists. The
entry reconciles to a **no-op**. The demotion step is kept anyway rather than
being made redundant by the ordering — relying on ordering alone would mean
deliberately mis-provisioning and then repairing.

### Verified end state — fresh create, then idempotent re-run

Run 1, against nothing:

```
[PROVISION] team_created=True channels_created=['alerts', 'installs']
            accounts_created=['nyxloom-admin', 'nyxloom-daemon', 'nyxloom-operator', 'nyxloom-installer']
            admin_role_stripped=[] memberships_added=5
            webhooks=['daemon_webhook_url', 'installer_webhook_url']
```

Run 2, immediately after, unchanged instance:

```
[PROVISION] team_created=False channels_created=[] accounts_created=[]
            admin_role_stripped=[] memberships_added=0
            webhooks=['daemon_webhook_url', 'installer_webhook_url']
```

State inspected directly:

| Check | Result |
|---|---|
| accounts | exactly 4: `nyxloom-admin` (`system_admin system_user`), `nyxloom-operator` (`system_admin system_user`), `nyxloom-daemon` (`system_user`), `nyxloom-installer` (`system_user`) |
| channel membership | `alerts` = admin + daemon + operator; `installs` = installer + operator — matches the declarations exactly |
| declared channels | `alerts`, `installs` both created |
| incoming webhooks | exactly **2**, `nyxloom-daemon` → `alerts`, `host-installer` → `installs`; still 2 after the second run |
| webhook delivery | a real POST to the persisted `daemon_webhook_url` → **200**, post appears in `alerts` authored by `nyxloom-daemon` |
| `admin_role_stripped` | `[]` in the shipped ordering; was `['nyxloom-daemon']` in the ordering that exposed the bug |

`admin_role_stripped=[]` is also the expected value on the **live** instance,
where nothing the hook creates is ever account #1.

One Mattermost behaviour worth recording rather than treating as drift: a fresh
team comes up with **four** channels, not two — `mmctl team create` also creates
`town-square` and `off-topic` and auto-joins every team member. `all_channels = true`
resolves against all four. Documented in the README.

Also confirmed while checking the privacy change did not break the hook:
`mmctl --local channel users list` still prints the `(<email>)` field with
`ShowEmailAddress=false`, so `_channel_members`' parse is unaffected — the
local admin socket is not subject to `PrivacySettings`. That was a real risk:
had the field been omitted, the membership dedup would have silently broken and
re-added every member on every run.

---

## Part 3 — The post-merge recipe

Written, **not executed**. It lives inline in `mattermost/README.md` under
**"Going public — the post-merge recipe"** and covers, in order:

1. **Apply the hardening alone** (`--dry-run` first, then real `ciu up`), with
   the explicit note that the env change **recreates** the app container and
   that governance must therefore be re-verified against the new container —
   P106's lesson, with the exact `docker inspect` / `cgroup` commands and the
   expected values. Postgres is not recreated (its env is untouched). The
   expected hook output for a live no-op is spelled out, including why
   `admin_role_stripped=[]` is correct there.
2. **Verify the hardening took effect before exposing anything** — reading the
   *running* config (with the reason a `config set` cannot be used as
   evidence), plus the invite-id probe with its measured before/after codes
   (201 → 501 `signup_email_disabled`) and an explicit **stop** if it still
   returns 201, plus a login check proving the human logins survive.
3. **Flip `expose_public`**, with a render-inspection step before the real
   `ciu up` — grepping the rendered compose for the Traefik labels, the
   `ingress` network, the `https://` `SITEURL` and `VARYBYHEADER`, because a
   silent no-op there is the failure mode that wastes the most time. Container
   recreates again → governance re-check again.
4. **Verify from outside**: DNS, TLS certificate dates/subject, site load, and
   the invite-id probe **against the public URL** (internally-true is not
   externally-true). Then the thing the flip actually buys — an end-to-end POST
   to `installer_webhook_url`, which is built on `siteurl` and was therefore
   unreachable from an install host until this moment — read back with
   `mmctl post list`. Plus a check that the rate limiter is keyed on the real
   client rather than on Traefik.
5. **Rollback**: `expose_public = false` + `ciu up` is the reversal, and it
   states explicitly that **the hardening needs no separate revert and should
   not get one** — none of it is conditional on being public; it closes an
   anonymous account-creation route, an OAuth authorization server, a 180-day
   session and a directory disclosure that were all equally live while the
   stack was internal-only. The single topology-dependent value
   (`VARYBYHEADER`) is emitted by the template's own `expose_public` branch and
   reverts itself.

`expose_public` stays **`false`** in the committed `ciu.defaults.toml.j2` — a
test asserts it, anchored to the start of a line (a substring check passed with
the real assignment flipped; caught by deliberate mutation).

---

## Tests

> Counts in this section are the **first-round** figures, before `main`
> (with nyxloom-P109) was merged in. The final numbers are 98 tests in this
> file and 16 mutations — see "Review round 1" below.

`tests/test_mattermost_provision_hook.py`: **35 → 62**, all passing. The 27 new
tests cover `_team_exists` (including the rc=0 sentinel, exact-vs-prefix
matching, probe failure and row drift), `_demote_unintended_admins` (including
its short-circuit and its refusal to touch a pre-existing account), and the
**actually-shipped files** — the hardening keys in the ciu template, the same
keys hand-synced into the pre-rendered `docker-compose.yml` fallback, the
rate-limit keying living inside the `expose_public` branch, `expose_public`
still false, and all four accounts declared with `nyxloom-admin` first.

The last of those is a whole-block **drift check** rather than a handful of
picked keys: every literal `MM_*` setting must match between
`ciu.compose.yml.j2` and `docker-compose.yml`, skipping the ones whose template
value is a Jinja expression (SITEURL, the DSN, LISTENADDRESS — the same setting
rendered, not a disagreement) and the one that legitimately exists only in the
`expose_public` branch. The fallback receives no ciu overlay and carries the
whole env block by hand; two hand-edited files drift, and NL-6 is what that
costs.

Testing the shipped files rather than a synthetic dict is P109's precedent and
it earned its place here: a hand-built fixture would have passed while the file
that actually deploys regressed.

### Deliberate mutations — 10 introduced, 10 caught (2 only after fixing the tests)

| # | Mutation | Failures |
|---|---|---|
| M1 | `_team_exists` back to a bare returncode read | 5 |
| M2 | exact name match weakened to substring | 1 |
| M3 | demotion iterates every declared account, not just created ones | 1 |
| M3b | M3 plus the `if not created` short-circuit removed | 2 |
| M4 | OAuth hardening reverted in the template | 1 |
| M5 | `nyxloom-admin` removed from the accounts list | 1 |
| M6 | `VARYBYHEADER` emitted unconditionally | 1 |
| M7 | pre-rendered fallback left un-synced | 1 |
| M8 | `expose_public` flipped in the committed defaults | 1 |
| M9 | a template setting silently deleted from the fallback | 1 |
| M10 | one value drifting between the two compose files | 1 |

**M3 and M8 initially survived**, and both were genuine test defects rather
than noise:

- M3 survived because the test passed `created=[]`, which the function
  short-circuits on — so a version ignoring `created` entirely still passed.
  Fixed by using a non-empty `created` that simply excludes the promoted
  account, and by adding a separate test for the short-circuit itself.
- M8 survived because `expose_public = false` also appears inside a **comment**
  about the installer webhook's URL base, so a substring check matched with the
  real assignment set to `true`. Fixed by anchoring to the start of a line.

Both are reported rather than quietly repaired, because "the mutation was
caught" was false for them until the tests were changed.

## Full test suite

`python3 -m pytest tests/` on the whole nyxloom suite, serial, under
`nice -n 15 ionice -c3` per the shared-host rule. **At the merged tip** (this
branch plus nyxloom-P109):

```
3973 tests, progress characters: {'.': 3973}      # no F, no E, no x, no s
PYTEST_RC=0
```

(Round 1's pre-merge figure was 3863. The run's final `N passed` status line is
not emitted by this project's pytest configuration, so the census of progress
characters plus the exit code is the evidence — captured explicitly after a
first attempt piped the output through `tail` and lost the end of it.)

## Gate

`run-gate tester-unified`, run from inside the worktree's `nyxloom/` directory
**at the merged tip**. Verdict read from `.assay/verdict-tester-unified.json`
in a **separate step** (LESSONS L4 — never a pipe tail):

```
tester-unified: PASS (exit 0)
  commit: 6b8b0acda7a07736c6953af871015a92c4960922
  argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
run-gate: lane 'tester-unified' exit 0
```

From the verdict artifact:

| Field | Value |
|---|---|
| `outcome` | **PASS** |
| `exit_code` | 0 |
| `commit` | `6b8b0acda7a07736c6953af871015a92c4960922` — equals this branch's HEAD at the time of the run |
| `declared_rigor` | `R0`, `R1` — both `PASS`, both `verified_by_assay: true` |
| R1 coverage | `pct: 100.0`, `covered: 450 / executable: 450`, `files_missing_coverage: []` |
| R1 judgment | `mode: changed_lines`, `fail_under: 100.0`, base `ec868f14` (merge-base) |
| `assay_version` | 6.1.0 |
| `argv_modified` | `false` |

(Round 1's pre-merge run also PASSed, at commit `9dc61cad`, 53/53 changed
lines. The jump to 450 is the merge: the merge-base is unchanged, so P109's
`src/nyxloom/intake_bridge.py` is now inside the changed-line window too.)

**What the 100% coverage figure does and does not mean.** R1 judges changed
lines within the lane's coverage scope, which is `--cov=src/nyxloom`. The
provisioning hook lives at `mattermost/hooks/post_compose_provision.py`,
**outside `src/`**, so R1 structurally cannot see it — the lines it measured
are changed surface in `src/`, not this package's hook. That is not a gap in
the gate and it is not being glossed: it is exactly why
`tests/test_mattermost_provision_hook.py` exists as a deliberate act rather
than as a coverage by-product, and why the evidence for the hook is the
mutation table rather than a percentage. Read "100%" as "the changed lines the
judge can see are covered", never as "the hook is covered".

**RG-48 did not bite, and this time the cap was applied.** The gate went out at
1-minute load **1.75** and its container
(`run-gate-vbpub-tester-unified-742855-…`) was capped with
`docker update --cpus=3` immediately after launch, per the shared-host rule —
the lane's `pytest -n auto` under that cap produced no false failures here.
(Round 1's run finished before a cap could be applied and is recorded as
uncapped; that is the honest difference between the two runs.)

## Files changed

| File | What |
|---|---|
| `mattermost/ciu.compose.yml.j2` | the hardening env block; rate-limit keying inside the `expose_public` branch |
| `mattermost/docker-compose.yml` | the same values hand-synced into the pre-rendered fallback (it receives no ciu overlay — NL-6's trap) |
| `mattermost/ciu.defaults.toml.j2` | `nyxloom-admin` declared, first, with the ordering rationale. `expose_public` untouched |
| `mattermost/hooks/post_compose_provision.py` | `_team_exists` rewritten; `_demote_unintended_admins` added and wired after the create/promote pass; `_require_user_roles` (the fail-CLOSED role read that guard uses); `admin_role_stripped` in the summary line and in the S9.4a state result |
| `mattermost/README.md` | the audit, the corrected "no self-signup" claim, the fresh-create facts, the residual, and the go-public recipe |
| `tests/test_mattermost_provision_hook.py` | +37 tests |

## Scope kept

- **No live stack contact of any kind** — not even a read.
- `expose_public` not flipped anywhere, including in the committed default.
- **P109's territory untouched**: no PAT enablement, no `intake` account,
  channel or `[[mattermost.provision.tokens]]` table. P109 has since merged and
  this branch merged it in (see "Review round 1"); the only P109 lines touched
  are two comment corrections its own text invited, both named below.
- No dstdns / installer-side integration.

## Review round 1 — NEEDS-FIXES, addressed

The reviewer **cleared the highest-risk item outright**: the `nyxloom-admin`
idempotency question. Traced directly, `mmctl user change-password` appears
nowhere in the hook (only in docstring prose), `--password` reaches only the
create-a-new-account branch, and role changes only ever promote. The live
bootstrap admin's password cannot be touched by this hook. No action needed —
but the reviewer noted `_ensure_account` had **zero direct unit coverage**, so
that whole argument rested on reading the code. It does not any more; see
"Tests added this round".

### Merged `main` (which now contains nyxloom-P109)

`git merge main` into `nyxloom-P110`. As predicted, `ciu.compose.yml.j2`,
`ciu.defaults.toml.j2` and the hook itself auto-merged; the two textual
conflicts were `README.md` and `tests/test_mattermost_provision_hook.py`, both
pure append-vs-append:

- **README.md** — one bullet. P109 promoted "no personal access tokens" from a
  clause in the posture list to its own bullet; P110 had appended "no OAuth2
  authorization server" to that same clause. Resolution keeps P109's structure
  and carries the OAuth clause into it.
- **tests** — the two packages' blocks are disjoint; the markers came out and
  both blocks stayed. One collision the merge could not see: **both files
  define a module-level `_Ctx`**, and P109's is defined later, so it silently
  won and my three `_ensure_account` tests failed with
  `_Ctx.__init__() missing 1 required positional argument: 'stack_dir'`. Mine
  is renamed `_NoSecretCtx`. Worth naming because a same-named *helper* rather
  than a same-named *test* is the kind of merge hazard that can also resolve
  the other way and silently weaken an assertion instead of erroring.

`run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` was not touched — RG-48 is only
referenced from this report, and RG-47/RG-48 were left exactly as the
controller resolved them.

**The account-count test was the one real breakage.** P109 added a fifth
account (`nyxloom-intake`), so the expected list is now five. It is also
renamed `test_every_account_is_declared_with_admin_first` and the docstring now
separates the two things it asserts: `nyxloom-admin` heading the list is what
carries weight (Mattermost promotes the first-ever account); the full list is
asserted so that adding an account is a deliberate act that updates this test.
No other test carried a count assumption — checked, not assumed.

### F1 — the wipe-path replacement was broken the same way `--reset` is ✅

The reviewer is right, and I verified the mechanism in the installed ciu 7.12.0
source rather than taking it on trust:

- `engine.reset_service` raises `deploy.labels.prefix is required for reset` at
  `engine.py:731`, **before its Step 1**.
- `deploy.action_clean` (`deploy.py:4360`) calls the same `reset_service` inside
  `except Exception` — it prints `reset failed for <stack>`, sets `rc=1` and
  **continues**. Its own Step 3 and Step 4 remove named volumes and networks,
  so the command looks mostly successful. Removing the `vol-*` hostdirs is
  `reset_service`'s **Step 2**, inside the call that never ran.

So my "use `ciu clean` instead" was wrong in the worst available way: the
command runs, reports a failure for one stack among several, and leaves a full
Postgres data directory for the next `ciu up` to adopt as though it were fresh.
The README now says plainly that **both** are broken for the same missing key,
explains that `clean` fails *quietly*, and presents the root-helper
`docker run alpine rm -rf` block — the one this package's own teardown used —
as the only working path.

**I did not add `deploy.labels.prefix` to the root**, and the reasoning is in
the README so the next person does not have to redo it. It is a root-level file
shared with `ntfy`, `nyxloomd` and `pwmcp-instance`, and adding the key converts
`ciu clean` from partly-inert to genuinely destructive for all of them — a
decision for whoever owns the root, not a side effect of a Mattermost package.
One fact recorded to make that decision cheap, because it is the part that
looks alarming and is not: ciu uses the prefix in **exactly one place**, Step
4's orphan-sweep filter `label=<prefix>.component=<service>`, and **ciu never
writes that label** — only a consumer's compose template would, and nyxloom's
do not. Adding the key therefore relabels nothing and orphans nothing (the
CIU-V8 R-15 hazard does not apply here); the sweep just matches zero
containers.

### F2a — read-only pre-flight before the risky step ✅

New **step 0** in the recipe, and the recipe's preamble now says steps 0–2
stand alone. It checks, mutating nothing, that `nyxloom-admin` exists, holds
`system_admin`, and is in `alerts`; and it prints the full account list,
because the failure this is really guarding is *the live bootstrap admin being
named something else* — in which case the first `ciu up` after the merge
creates a brand-new `nyxloom-admin` from the `GEN_LOCAL` secret and promotes
it. Not wrong, but silent, and only visible afterwards.

It ships as an explicit **STOP table**: four observations, what each means, and
what to do instead — including the two outcomes that are *intended* but should
not be surprises (promotion of an un-promoted admin; adding it to `alerts`).

### F2b — the `alerts` gap is now IN the operator's path ✅

Three changes, because the reviewer's point was that a residuals section is not
where a flip-time decision belongs:

1. **New recipe step 2b**, immediately before the flip, headed as a decision
   point rather than a check: `alerts` is public, any team member can read and
   join it, with a runnable probe that demonstrates it as `nyxloom-installer`.
   **Option A** converts it (`mmctl --local channel modify nyxloom:alerts
   --private`, plus setting `private = true` so a fresh create matches — both
   halves, or the next rebuild silently reopens it) and re-runs the probe
   expecting 403. **Option B** accepts it, with the instruction to *write the
   acceptance down*, since an unrecorded accepted risk is indistinguishable
   from one nobody noticed. The `mmctl channel modify --private` syntax was
   verified against the pinned image (`docker run --rm --network none
   mattermost/mattermost-team-edition:11.10.1 ... channel modify --help`) rather
   than recalled — the throwaway was already destroyed, and this recipe is
   meant to be run verbatim on production.
2. **The misleading parenthetical is gone.** The residual section led with the
   team's `type: "O"` / `allow_open_invite: true` shape, which invites exactly
   the wrong inference — that F1's `ENABLEUSERCREATION=false` closes this too.
   It does not: that flag stops accounts from being *created*, and this gap is
   about accounts that *already exist* joining a public channel. The section now
   says so in as many words, and explains that the open-team shape governs
   joining the **team**, which every account here already did.
3. **`ciu.defaults.toml.j2` records it at the source**, next to the `alerts`
   declaration — including why `private = true` was not simply set here (the
   hook only creates channels, so it would change nothing live while making the
   committed config assert something untrue of the running server), and the
   instruction to set it in the same pass if the channel is ever converted.

### F3 (optional) — taken: the privilege guard now fails CLOSED ✅

`_user_roles` returned `set()` on any rc/parse failure, and
`_demote_unintended_admins` read that as "not an admin, nothing to strip" — so
an mmctl output-format drift would have silently left an auto-promoted service
account holding server administration, reinstating the exact defect the guard
was added for, with no message anywhere.

Rather than change `_user_roles` globally, there is now a
`_require_user_roles` twin that **raises** on a failed probe, non-JSON output,
or JSON with no `roles` field. The demotion guard uses it; `_ensure_account`'s
**promotion** guard deliberately keeps the fail-open original, because there an
empty answer means "promote", and promoting an account that already holds the
role is a server-side no-op — erring toward a redundant promotion is harmless,
aborting a deploy over a transient parse failure is not. Both functions now
document which caller they are safe for.

The blast radius of the new refusal is small and lands where it should: it can
only fire for an account the same run just created, i.e. on a fresh instance,
which is exactly when an operator is present and wants to be told.

### Two comment corrections in P109's text

Both are places where P110's own change made P109's prose untrue, and both are
in regions this merge already touched:

- The `nyxloom-intake` rationale disqualified `nyxloom-admin` as "the bootstrap
  account this hook deliberately never touches" — no longer true now that it is
  declared. Corrected to the accurate reason, which is also the stronger one:
  it is `system_admin`, so it is disqualified for exactly the same reason
  `nyxloom-operator` is. The conclusion is unchanged.
- The posture list's PAT bullet (README) kept P109's structure with P110's
  OAuth clause folded in, rather than either package's version winning.

### Tests added this round

`tests/test_mattermost_provision_hook.py`: **88 after the merge → 98**.

- 4 for `_require_user_roles` (parses roles; raises on rc≠0, on non-JSON, and
  on valid JSON whose `roles` field has gone away — the drift a bare
  `.get("roles", "")` turns into a confident "this account has no roles").
- 1 end-to-end: the demotion guard *refuses* rather than skipping when roles
  are unreadable, and mutates nothing while doing so.
- 1 pinning that the fail-open twin is still fail-open, so the split is
  deliberate rather than an accident waiting to be "tidied up".
- 4 for **`_ensure_account`** — the coverage gap the reviewer named. These turn
  the manual trace that cleared the highest-risk item into an enforced
  invariant: no secret is read for an existing account and no `--password`
  reaches any argv; the complete verb set for the live `nyxloom-admin`
  reconcile is exactly one idempotent `team users add`; the function promotes
  but never demotes; and the password *is* read and passed when the account is
  genuinely being created.

### Mutations — 6 more introduced this round, 6 caught (16/16 overall)

| # | Mutation | Failures |
|---|---|---|
| M11 | demotion guard reverted to the fail-OPEN role read | 2 |
| M12 | `_require_user_roles` returns an empty set instead of raising | 2 |
| M13 | `_ensure_account` re-sends the password to an EXISTING account | 3 |
| M14 | `_ensure_account` demotes an existing non-declared-admin account | 1 |
| M15 | `nyxloom-intake` dropped from the declared accounts | 3 |
| M16 | `nyxloom-admin` moved out of first position | 1 |

The template-vs-fallback drift check was re-verified as still *meaningful*
after the merge rather than merely still green: it compares 42 literal `MM_*`
settings and skips 4 whose template value is a Jinja expression — P109's
`ENABLEUSERACCESSTOKENS` joined `SITEURL`, the DSN and `LISTENADDRESS` in that
set, which is correct, and no drift was found.

## Throwaway teardown

Destroyed after the last measurement, and the absence verified rather than
assumed:

| Resource | After teardown |
|---|---|
| containers matching `nyxloom-p110` | **0** |
| volumes matching `nyxloom-p110` | **0** |
| networks matching `nyxloom-p110` | **0** |
| `vol-*` hostdirs under the worktree stack dir | **0** (removed through a root helper — uid 70 / uid 2000 subtrees) |
| worktree secret stores (project + stack) | **0** |
| `nyxloom-prod-mattermost` / `-db` | **Up 7 hours (healthy)** — never restarted, never contacted |

The devcontainer was also disconnected from the throwaway's bridge network.

The worktree itself (`/workspaces/vbpub/.worktrees/nyxloom-p110`) is left in
place for review, holding only the branch and a gitignored
`ciu.global.instance.toml.j2`. It is `ciu worktree rm nyxloom-p110` when the
package lands.
