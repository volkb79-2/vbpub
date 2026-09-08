# nyxloom-P106 — REPORT

Branch `nyxloom-P106`, worktree `/workspaces/vbpub/.worktrees/nyxloom-p106`.
NOT merged, no PR. (Written by the controller into the worktree — the
implementer agent was blocked from writing this file itself.)

## Commits (4, merge-base with main is `847a1f57`)

| Commit | What |
|---|---|
| `0c1ad4bb` | Mattermost + dedicated Postgres as a ciu-managed stack (Half 1) |
| `6aedc910` | NL-17 `NotifyBackend` seam + Mattermost backend (Half 2) |
| `2388a74b` | census/inventory debt the gate caught, ARCHITECTURE §8 refresh |
| `df385725` | tests for the seam's remaining changed lines (assay R1) |

## Gate

`python nyxloom/run-gate.py tester-unified` (run-gate rev 36, assay 5.2.0) at `df385725`:

```
tester-unified: PASS (exit 0)
  R0 PASS   R1 PASS  pct=100.0 considered=3 missing={}
```

Three runs needed; the first two failures were real, not flakes:
1. Exception census — `MattermostBackend`'s broad handler was new
   unclassified debt, tagged `# census: advisory-degradation (NL-17)` with
   its reason; `notify.py`'s LEGACY_BUDGET of 2 untouched.
2. Ownership inventory row for `notify.py` (532 -> 788), reason recorded in
   the row.
3. Assay's changed-line judge named six lines, each got a real test rather
   than a pragma.

Host discipline: gate run only with no other `run-gate-*` container up,
`docker update --cpus=3` on each gate container right after launch, stack
brought up at load 1.6. FYI noted by the implementer: the sibling P104
gate failed with exit 1 at ~20:00 (its own pre-existing-drift-check
failure, already covered in P104's own REPORT.md) and briefly ran a
second, uncapped gate container concurrently with this one.

---

## Half 1 — `nyxloom/mattermost/`

ciu v2 stack package mirroring ntfy's shape: `ciu.defaults.toml.j2` (root
key `mattermost`), `ciu.compose.yml.j2`, a pre-rendered `docker-compose.yml`
fallback with absolute physical paths and hand-inlined caps, and a README.
`mattermost/mattermost-team-edition:11.10.1` (deliberately not the
day-old 11.11.0) + a dedicated `postgres:16-alpine` on a private
stack-owned bridge, no host ports.

### Governance — the NL-6 trap avoided and verified live

Deployed via `ciu up --dir nyxloom/mattermost -y`, never the compose
fallback. Neither service emits `cgroup_parent` (that author-precedence
skip is exactly what left ntfy unconfined); the stack table layers over
the root `[governance]` per S15.10's shallow merge; only postgres narrows
itself via author-set compose keys.

ciu: `[GOVERNANCE] enabled -- cgroup_parent=dev-background.slice;
mem_limit=2g; mem_swap_limit=18g; mem_reservation=256m; cpus=1.5;
device=/dev/vda (explicit); services_injected=2 exempt=0`

`docker inspect`:
```
/nyxloom-prod-mattermost     cgroup=dev-background.slice mem=2147483648 memswap=19327352832
                             memres=268435456 nanocpus=1500000000 read/write iops 200/400 on /dev/vda
/nyxloom-prod-mattermost-db  cgroup=dev-background.slice mem=536870912  memswap=4294967296
                             memres=268435456 nanocpus=500000000  read/write iops 200/400
```

Because inspect only reports *config* -- the thing NL-6 proved
insufficient -- the implementer also read live kernel state, from the host
PID+cgroup namespace:
```
0::/dev.slice/dev-background.slice/docker-b26634d3....scope   # mattermost
0::/dev.slice/dev-background.slice/docker-e05aeaa4....scope   # postgres
# inside the db container: memory.max 536870912 . memory.swap.max 3758096384 . cpu.max "50000 100000"
```
Real slice, real limits, both services. Idle footprint after disabling the
plugin framework: 94 MiB/2 GiB and 94 MiB/512 MiB. Sizing (2 GiB =
Mattermost's own documented small-deployment figure used as a ceiling,
`cpus` explicit because ciu never defaults one) is justified in a comment
beside the value, per NL-11's lesson.

### Network exposure -- decided internal-only, NEEDS OPERATOR CONFIRMATION

`expose_public = false`: no host ports, no `ingress_public` join, no public
hostname. Safe default for an unconfirmed public endpoint, and sufficient
for nyxloom's own delivery path. NOT sufficient for a phone/desktop client
or an off-host browser -- the same reason ntfy had to be public. The
public path is written and one flag away (`expose_public = true` adds the
four tls-edge labels, joins `ingress_public`, re-points `SITEURL` at
`mattermost.gstammtisch.dchive.de`, covered by the existing wildcard A
record). Locked down either way: no open server, no self-signup
(admin-created only), no attachments, no plugin framework/marketplace, no
personal access tokens, no outgoing webhooks or slash commands, no
telemetry, no SMTP.

### Secrets

`GEN_LOCAL` Postgres password in ciu's project store, read by Postgres via
`POSTGRES_PASSWORD_FILE`; a second `GEN_LOCAL` holds the admin password so
provisioning invents nothing. The app's DSN uses ciu's `expose_env` escape
hatch (S4.19), which ciu itself calls discouraged -- flagged, not buried.
Forced by the image: `mattermost-team-edition:11.x` ships NO `/bin/sh`
(verified the hard way), so the sanctioned S4.18 entrypoint-wrapper cannot
run, and Mattermost has no `*_FILE` form for its datasource. The rendered
compose holds only `${MM_DB_PASSWORD}`, so ciu's leak scan stays
meaningful; residual exposure is the DSN in that container's env. The
configfile alternative was rejected (S5.3a mounts the parent dir read-only;
Mattermost writes its own config back).

**ntfy retired, not deleted**: moved out of the root's default `ciu up`
profile into `[deploy.profiles.legacy]` so a bare `ciu up` stops
restarting the container that was stopped, plus a banner on its README.

---

## Half 2 -- the `NotifyBackend` seam (NL-17)

`send()` dispatches over `resolve_backends(nc)`. `NotifyBackend` has three
methods; `deliver()` returns `(ok, detail)` for a definitive outcome or
`None` for a transport fault the caller may retry on the next backend --
that sentinel preserves ntfy's exact prior semantics (a raise fell through
to webhook, a non-200 did not; both still do). Backends: `NtfyBackend`
(verbatim), `WebhookBackend` (the legacy raw passthrough, kept for
receivers built for it), `MattermostBackend` (new). Telegram/Discord are a
subclass + a registry line away and are NOT built.

Payload mapping: Mattermost renders only `text` and ignores
`title`/`click`/`priority`/`tags`, so everything folds into Markdown --
bold title line, body, `[open](click)`, `_tags: ..._`, and priority 5/4 ->
a rotating-light/warning emoji prefix (no native priority field on
incoming webhooks; <=3 gets no badge). SPEC §13 holds -- typed fields plus
fixed templates only; deliberately not Markdown-escaped because
`notification_for` emits only ids/enums/counts, with a test asserting an
extra untyped note key never reaches the wire.

### Config selector + backward compat

`NotifyConfig.backend` (validated against `config.NOTIFY_BACKENDS`;
unknown -> `ValueError`). Absent -> the historical implicit precedence,
unchanged, so every existing `nyxloom.toml` behaves identically. Set ->
sole channel, no implicit fallback. Also `webhook_url_env` (default
`NYXLOOM_WEBHOOK_URL`, env-wins-over-toml, mirroring the `NTFY_URL`
precedent) because that URL IS the credential. All new keys are in the
JSON schema so lint's CFG1 still catches typos. `probe_transport` follows
the selected backend; doctor's probe cache keys on it. One intentional
detail change: when every backend faults, `send()` now returns the last
fault instead of the untrue `"unconfigured"` (nothing asserted that
string; `notify_event`'s genuine unconfigured path is unchanged).

### Live oracle

Real notes from `notification_for()` on real typed Events, against the
live stack:
```
probe: NotifyTransportProbe(status='healthy', channel='mattermost', ...)
payload: {'text': ':rotating_light: **Decision needed: D-17**\nDecision D-17 opened and
          awaiting resolution.\n[open](http://127.0.0.1:8942/www/index.html)\n_tags: decision_',
          'username': 'nyxloom', 'channel': 'alerts'}
send: (True, 'mattermost ok')   # x3 (DECISION_OPENED, NEEDS_OPERATOR, WAVE_CLOSED)
```
A 200 is not proof of rendering -- that is the exact NL-17 failure mode --
so the messages were read back out:
```
$ docker exec nyxloom-prod-mattermost mmctl --local post list nyxloom:alerts --number 5
[nyxloom-admin] :rotating_light: **Decision needed: D-17**
Decision D-17 opened and awaiting resolution.
[open](http://127.0.0.1:8942/www/index.html)
_tags: decision_
[nyxloom-admin] :rotating_light: **Operator attention needed** ...
[nyxloom-admin] **Wave merged: 2 task(s)** ...
```
and the username override is genuinely applied (posts table):
`{"from_webhook":"true","override_username":"nyxloom","webhook_display_name":"nyxloom"}`.
The devcontainer was attached to the stack's bridge only for that check
and has been detached.

20 new tests in the files' existing real-HTTP-server style (17 in
`test_notify.py`, 3 in `test_config.py`); all 43 pre-existing notify tests
pass unchanged -- that is the ntfy no-regression evidence. Includes an
anti-drift test tying `_BACKENDS` to `config.NOTIFY_BACKENDS` and the
schema enum, and one that pins ntfy's non-200-does-not-fall-through
semantics with a webhook actually configured (review C1).

NL-17 got a dated `nyxloom backlog note`, left `open` -- status transition
is left for merge time.

---

## Files touched

`nyxloom/ciu.global.defaults.toml.j2`, `nyxloom/docs/ARCHITECTURE.md`,
`nyxloom/mattermost/README.md`, `nyxloom/mattermost/ciu.compose.yml.j2`,
`nyxloom/mattermost/ciu.defaults.toml.j2`,
`nyxloom/mattermost/docker-compose.yml`, `nyxloom/ntfy/README.md`,
`nyxloom-trove/backlog/NL-17-*.md`,
`nyxloom-trove/reports/CORE-REDESIGN-OWNERSHIP-INVENTORY-2026-08-02.md`,
`src/nyxloom/config.py`, `src/nyxloom/doctor.py`, `src/nyxloom/notify.py`,
`src/nyxloom/schemas/nyxloom-config.schema.json`, `tests/test_config.py`,
`tests/test_notify.py`.

Confirmed via `git diff --stat $(git merge-base main HEAD)..HEAD`
(merge-base `847a1f57`) -- file list matches exactly, no
cross-contamination.

## Needs reviewer / operator judgment before merge or cutover

1. **Public exposure.** Internal-only today. If Mattermost should reach a
   phone -- most of the point of replacing ntfy -- flip
   `expose_public = true` and re-up; that creates a real public endpoint at
   `mattermost.gstammtisch.dchive.de`.
2. **No production config was switched.** Nothing sets
   `backend = "mattermost"` anywhere committed. Cutover = create the
   webhook (README), export `NYXLOOM_WEBHOOK_URL`, set the selector.
   Deliberately left undone.
3. **The live stack currently runs from THIS worktree's ciu root, and the
   SECRETS do not carry over with the data (review S1 — the one real
   operational gap the reviewer found).** The only copy of both
   `postgres_password` and `admin_password` lives in
   `.worktrees/nyxloom-p106/nyxloom/.ciu/secrets/mattermost/`; the main
   checkout has no `.ciu/` for nyxloom at all. The volumes are
   compose-project-scoped (`nyxloom-prod-mattermost_*`) so the DATA carries
   over, but a from-main `ciu up` would have `GEN_LOCAL` mint a FRESH
   Postgres password that does not match the `mmuser` role baked into the
   persisted volume, and the admin password would be lost outright (no
   SMTP, no recovery path). **Migrate both secret files to the main
   checkout's store BEFORE the first from-main `ciu up`**, then re-up and
   re-verify health. Deliberately not done here: it is a post-merge step,
   sequenced by the controller.
4. **The verification webhook has been DELETED (review S3).** Its id had
   appeared in two agent transcripts. `mmctl --local webhook list nyxloom`
   now reports 0 webhooks, and no committed file referenced the id. It was
   deliberately not replaced: the cutover step in
   `nyxloom/mattermost/README.md` creates a fresh one, so the credential
   that ends up in production is minted at cutover and has never been in a
   transcript.
5. **`expose_env` for the DSN** is a real, documented concession -- the
   only alternative found was a custom image with a shell (Mattermost's
   official image ships none).
6. **`EnableOAuthServiceProvider` is left at Mattermost's default of true
   (review S4).** Harmless while `expose_public = false` (nothing off-host
   can reach the OAuth endpoints), and deliberately not changed here so the
   lockdown set stays the one that was actually verified running. **It must
   be set to false as part of any public-exposure flip**, in the same edit
   that sets `expose_public = true` -- add
   `MM_SERVICESETTINGS_ENABLEOAUTHSERVICEPROVIDER: "false"` to the app
   service's environment in both `ciu.compose.yml.j2` and the
   `docker-compose.yml` fallback.

---

## Review round 1 (ACCEPT with nits) — what changed after it

Independent adversarial review accepted the package and raised one
operational gap plus nits. Fixed in this branch:

- **C1** — a test now pins "an ntfy non-200 does NOT fall through to a
  configured webhook", the load-bearing half of the no-regression claim.
  The pre-existing 2xx-non-200 test could not catch a regression there
  because it configures no `webhook_url` to fall through to.
- **C6** — `NotifyTransportProbe`'s docstring said `"unconfigured"` meant
  "neither ntfy nor webhook"; it now describes the resolved-chain rule
  (including a selected-but-unconfigured backend), and `channel` documents
  that it carries any registered backend name.
- **C4** — the plain-compose fallback's header now warns that the VOLUMES
  diverge with the container names: starting from it yields an empty
  database and a fresh init, not a second door into the ciu stack's data.
- **C2** — the "not Markdown-escaped" justification no longer overclaims.
  A few branches interpolate a payload value that is enum-like by upstream
  discipline rather than by construction (`SPEC_ATTENTION`'s
  `payload.reason`); the docstring now says so and bounds the residual
  blast radius (formatting oddity in a private channel; Mattermost
  sanitizes HTML; `click` is always a code-owned constant).
- **C3** — the `NYXLOOM_WEBHOOK_URL` precedence caveat is recorded at the
  resolution site: the backward-compat guarantee is "unchanged while that
  var is unset", not unconditional.
- **C5** — the test count above corrected (20, not 17).
- **S3** — webhook deleted, see item 4.
- **S1** — deliberately NOT fixed here; folded into item 3 as the
  post-merge step it is.
- **S4** — deliberately NOT fixed here; recorded as item 6, gated on the
  exposure flip.

Re-gated after the fixes rather than reasoning about whether a test-only
change needed it (C7's "probably fine to skip"): `run-gate.py
tester-unified` at `2a98da5c` — **PASS (exit 0)**, R0 PASS, R1 PASS
`pct=100.0 considered=3 missing={}`. This commit itself only re-touches
REPORT.md with the verdict, so the gate stands for the whole code change.
