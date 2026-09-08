# nyxloom-P107 — REPORT

Branch `nyxloom-P107`, worktree `/workspaces/vbpub/.worktrees/nyxloom-p107`.
NOT merged, no PR. Follow-up to nyxloom-P106 (`ff2b33c3` + `3d299917`).

## Commits (merge-base with main is `ff2b33c3`'s descendant `cb3886f5`)

| Commit | What |
|---|---|
| `83d311cc` | bind-mount hostdirs, provisioning hook, cutover |
| `702f16a8` | unit tests pinning the two parser bugs found live |
| `69f0589d` | this report |
| `e5d954f0` | **review round 1 fixes (F1-F6)** — see the section at the end |

## Gate

`python nyxloom/run-gate.py tester-unified` (run-gate rev 37, assay 5.2.0),
re-run after the review-fix round, at `e5d954f0`:

```
tester-unified: PASS (exit 0)
  R0 PASS   R1 PASS  pct=100.0 considered=0 missing={}
```

(The pre-review gate was PASS at `702f16a8` with the same claims.) Verdict read
from `.assay/verdict-tester-unified.json` in a separate step, not off a pipe
tail. This commit itself touches only the report, so that gate stands for the
whole code change.

`considered=0` is honest and worth a reviewer's attention: nothing under
`src/nyxloom` changed, so assay's changed-line judge had nothing to judge. The
hook module sits at `nyxloom/mattermost/hooks/`, **outside** the lane's
`--cov=src/nyxloom` scope, and is therefore invisible to the coverage gate.
That is exactly why its 33 tests are a deliberate act — and the review round
proved the point: every finding it raised was in code the gate cannot see.

Host discipline: every gate run launched with no other gate container up,
`nice -n 10 ionice -c2 -n7`, `docker update --cpus=3` on each container right
after launch. Load at launch 1.8, 7.2 and 5.9. One run was wasted on
`NO_MEASUREMENT/DIRTY_TREE` — `run-gate --allow-dirty` does not bypass assay's
own clean-tree requirement, since a higher-rigor lane measures the resolved
commit from a snapshot.

---

## Workstream 1 — volume architecture

### The empirical UID question, answered by measurement

The operator asked whether dstdns's root init-container dance
(`infra/db-core`'s `postgres_init`) is really needed here or is specific to
db-core's own choices. Five scratch containers, all against fresh host
directories:

| # | Image | Host dir | `user:` override | Result |
|---|---|---|---|---|
| T1 | `postgres:16-alpine` | `1003:1003` `0775` | none | **works** — entrypoint starts as root, chowns to uid **70**, chmods `0700`, initdb completes, `pg_isready` accepts |
| T2 | `postgres:16-alpine` | `1003:1003` `0775` | `70:994` | **fails** — `initdb: error: could not change permissions of directory "/var/lib/postgresql/data": Operation not permitted` |
| T3 | `postgres:16-alpine` | `70:994` `0770` | `70:994` | **works** — and postgres immediately chmods the dir to `0700` |
| T4 | `mattermost-team-edition:11.10.1` | `1003:1003` `0775` | none (image default) | **fails** — `failed to load configuration: could not create config file: open /mattermost/config/config.json: permission denied` |
| T5 | `mattermost-team-edition:11.10.1` | `2000:994` `0770` | none | **works** — writes `config.json`; the dir stays `2000:994 0770` |

Findings:

1. **The init container is NOT needed for `postgres:16-alpine`.** dstdns needs
   `postgres_init` because it forces `user: "1000:${DOCKER_GID}"` onto
   `timescaledb-ha`, which removes the entrypoint's root phase. T2 reproduces
   that failure on this image exactly; T1 shows the unforced image fixes its
   own bind mount. So this stack does **not** carry the complexity, and does
   **not** carry dstdns's `CLEAN_DATA_DIR` env-gated wipe either — see below.
2. **`postgres:16-alpine`'s postgres account is uid/gid 70, not 999.**
   (`postgres:x:70:70:` in the image's `/etc/passwd`; 999 is the *Debian*
   `postgres:16`.) P106's compose comment and README both said 999. Harmless
   while the data lived in a named volume the image initialised itself,
   load-bearing the moment it becomes a bind mount. Corrected in place.
3. **Mattermost is the opposite case and DOES need a pre-owned directory.**
   The image's default user *is* `mattermost` (uid 2000) — `Config.User` is
   set, there is no root phase, and the image ships no `/bin/sh` — so it can
   never chown anything. T4 is the failure, T5 the fix.
4. So both images are served by the SAME mechanism, and it is not an init
   container: ciu's own `[<svc>.hostdir]` (S6.1/S6.3) pre-creates and pre-owns
   the directory through the S6.5 root helper. S6.5 asks for precisely this —
   *"stacks SHOULD NOT carry init containers for ownership fixes"*.

### What was converted, and what was not

| Mount | Backing | Reason |
|---|---|---|
| `/var/lib/postgresql/data` | hostdir `vol-postgres-data`, `70:DOCKER_GID` `0700` | operator-requested |
| `/mattermost/config` | hostdir `vol-mattermost-config`, `2000:DOCKER_GID` `0770` | operator-requested; `config.json` is the file an operator reads and diffs |
| `/mattermost/logs` | hostdir `vol-mattermost-logs`, `2000:DOCKER_GID` `0770` | **judgment call, converted**: grep across restarts, and it makes the stack's one unbounded-growth surface visible to `du` instead of hidden under `/var/lib/docker`. Logs are disposable, so the conversion carries no migration risk |
| `/mattermost/data` | named volume | **judgment call, kept**: file attachments are disabled (`MM_FILESETTINGS_ENABLEFILEATTACHMENTS=false`) — there is nothing in it to look at |
| `/mattermost/plugins`, `/mattermost/client/plugins` | named volumes | **judgment call, kept**: the plugin framework is off entirely. They exist only because the image declares `VOLUME` on both paths; dropping the declarations would hand Docker two *anonymous* volumes per recreate, which is worse |

### Wipe-on-demand — why hostdirs and not `external`, and not `CLEAN_DATA_DIR`

The operator explicitly did **not** want these protected from `ciu clean` /
`ciu up --reset`; `external: true` was considered and rejected because it does
the opposite. ciu already has the wanted semantics natively: S6.4 removes the
stack directory's `vol-*` trees on `--reset`/`clean`, resolves the **physical**
path first under DooD (the CIU-9 clause, which exists precisely because a
logical-path `rmtree` can "succeed" without touching what the daemon mounted),
and degrades to the S6.5 root helper for a subtree the operator cannot remove —
which is exactly our uid-70 `0700` case. dstdns's `CLEAN_DATA_DIR=true`
env-gated `rm -rf` inside an init container is an approximation of that for a
stack that has no such engine support; reproducing it here would be a second,
weaker implementation of a mechanism ciu already owns.

**One claim is deliberately NOT live-verified**: that `ciu up --reset` /
`ciu clean` actually wipe these dirs. Both verbs also tear down the stack's
containers, and the only stack on this host is the live one. It is
spec-normative (S6.4) and code-read, and the mechanism it degrades to — a root
helper removing the uid-70 `0700` tree — *was* exercised here (it is how the
rehearsal copies were cleaned up). A reviewer or the operator can confirm the
ciu wiring on a throwaway instance post-merge.

### The live stack was NOT converted — read this before merging

The hostdir paths are stack-dir-relative by design (that is what makes them
`vol-*` under the stack dir and therefore reachable by S6.4). Deploying the
converted stack from **this worktree** would bind live production data to
`/…/.worktrees/nyxloom-p107/…`, which is destroyed with the worktree — a worse
version of the stale-path trap P106's own post-merge follow-up hit, since there
the data survived in a named volume. So:

- **The live stack still runs on its named volumes and was never recreated in
  this package.** Both containers are `(healthy)`, `Up 2 hours` at the time of
  writing, i.e. from before this work started.
- The conversion is **rehearsed and verified on a copy of the real data**
  instead, and written up as a post-merge recipe in
  `nyxloom/mattermost/README.md` ("Migrating an EXISTING named-volume
  deployment"). This mirrors P106's own S1 post-merge migration exactly.

Rehearsal evidence (all against a fresh `pg_dump` safety net taken first, kept
out of the repo at
`/tmp/claude-1003/-workspaces-vbpub/d2c44f67-…/scratchpad/p107-backups/mattermost-20260908-225408.sql`,
188 KB):

1. `ciu up --dry-run` created all three hostdirs with the declared ownership —
   `vol-mattermost-config (2000:994, 0770)`, `vol-mattermost-logs (2000:994,
   0770)`, `vol-postgres-data (70:994, 0700)` — via ciu's root helper.
2. The live `nyxloom-prod-mattermost_postgres-data` and `…_mattermost-config`
   volumes were copied into them with `cp -a` through a root helper.
3. A throwaway `postgres:16-alpine` was started **on the bind-mounted copy**,
   with the real `POSTGRES_PASSWORD_FILE` secret: it recovered and reported
   `database system is ready to accept connections`, and the data was
   identical to live — **6 users / 1 team / 4 channels / 17 posts / 2 live
   webhooks**, with all six usernames present. It then left the directory at
   `70:994 0700`, i.e. still S6.3-compatible for the next `ciu up`.
4. A second `ciu up --dry-run` reported `Exists with compatible ownership` for
   all three — the migrated dirs pass ciu's own re-check.

**A trap the recipe now calls out explicitly**: `cp -a /from/. /to/` copies the
SOURCE directory's own owner and mode onto the target, leaving `70:70` /
`2000:2000 0777`. S6.3's compatibility check then REFUSES the hostdir on the
next `ciu up` (`Existing hostdir has incompatible ownership/permissions`). The
migration step that re-asserts `chown 70:994` / `chown 2000:994` is not
optional, and I hit this in the rehearsal.

**An honest limit of bind-mounting postgres**: `initdb` chmods PGDATA to `0700`
unconditionally, so `vol-postgres-data` is host-*visible* but not
host-*readable*. Declaring S6.7(a)'s suggested `0770` would be a lie the image
overwrites — and would then fail S6.3 on the following run. Backups keep going
through `pg_dump` in the container.

---

## Workstream 2 — three accounts, a second channel, automation

### Hook vs. one-shot container — the hook wins on mechanics, not taste

Three reasons, in order of weight:

1. **The webhook URL has to get back into ciu's secret store.** A webhook id
   does not exist until Mattermost mints it, so no S4 directive can express it
   — which is the case S9.4a's `persist: "secret"` exists for. A sidecar has no
   access to that API: it would need a *writable* bind mount of
   `<stack>/.ciu/secrets/` — handing a container write access to the credential
   store in order to avoid using the sanctioned interface for writing to the
   credential store.
2. **`mmctl --local` needs the app container's own local-mode socket**
   (`/var/tmp/mattermost_local.socket`), which is on that container's
   filesystem and not on a shared volume. Exporting it to a sidecar would
   *widen* the admin surface — anything that can mount the volume gets
   unauthenticated system-admin — purely to avoid `docker exec`. (The existing
   healthcheck reaches it the same way this hook does: in-container.)
3. **Readiness is already solved.** `ctx.wait_healthy()` (S9.3/CIU-4) is wired
   by the engine and S9.3 forbids hand-rolling a poll loop where a helper
   suffices.

S6.5's "stacks SHOULD NOT carry init containers" points the same way for the
sibling ownership problem, so the stack ends up with **zero** helper containers.

### What it provisions

`hooks/post_compose_provision.py`, declared as
`[mattermost.hooks].post_compose`, reconciling data from
`[mattermost.provision]`:

| Account | Role | Channels | Password secret (project store) |
|---|---|---|---|
| `nyxloom-admin` | system admin — **not declared**, left exactly as P106 made it | `alerts` | `mattermost/admin_password` |
| `nyxloom-daemon` | regular | `alerts` | `mattermost/daemon_password` |
| `nyxloom-operator` | system admin | **all** | `mattermost/operator_password` |
| `nyxloom-installer` | regular | `installs` | `mattermost/installer_password` |

Channels: `alerts` (existing) + **`installs`** (new, "Host Installs").

Webhooks: **two**, not three. `daemon_webhook_url` (→`alerts`, posts as
`nyxloom-daemon`, internal URL base) and `installer_webhook_url`
(→`installs`, posts as `nyxloom-installer`, SITEURL base). The operator account
is a human login with nothing to post programmatically; minting an unused
credential is strictly worse than not minting one. Flagging this because the
brief said "three webhook URLs" — say the word and a third is one table entry.

Judgment calls, stated:

- **`nyxloom-operator` is a system admin.** "Operator admin" most naturally
  means the account that administers the server — the job `nyxloom-admin` has
  been doing by accident. The literal requirement ("access to all channels") is
  met *separately* by `all_channels = true`, because system-admin alone does
  **not** make you a member of a private channel; it only lets you manage one
  from the console. Both were needed, neither substitutes for the other.
- **`nyxloom-admin` is now the bootstrap/system account.** It is deliberately
  absent from `[mattermost.provision]`, and the hook never touches an account
  it does not declare.
- **`installs` is a public channel within the team, and the installer account
  is NOT in `alerts`.** The security boundary here is team membership (no open
  server, no self-signup, four trusted accounts); the thing that actually
  travels to an install host is the webhook URL, which is post-only and targets
  `installs` alone. Scoping the *account* out of `alerts` is what makes
  "separate channel" real.
- **"present and future" channels**: `all_channels` resolves against the team's
  channel list at reconcile time, so a channel added to the declarations is
  joined on the same run that creates it. A channel created out of band in the
  UI is joined on the **next** `ciu up`. Mattermost has no "member of all
  future channels" primitive; documented caveat, not an oversight.

### Idempotency — verified, and it took two fixes to get there

Every mutating step is guarded by a state probe. Four consecutive `ciu up
--dry-run` runs against the live instance:

```
run 1: channels_created=['installs'] accounts_created=[3] memberships_added=6
run 2: channels_created=[]           accounts_created=[]  memberships_added=6   <-- BUG
run 3: channels_created=[]           accounts_created=[]  memberships_added=0
run 4: channels_created=[]           accounts_created=[]  memberships_added=0   webhooks stable at 2
```

Two real bugs, both found only because the live instance grew duplicates —
neither is loud, both look like a successful idempotent run:

- `_channel_members` collected whole `mmctl` output lines instead of usernames
  (`<id>: <username> (<email>) <role>`), so the guard never matched and every
  member was re-added every run.
- `_incoming_webhooks` did not strip mmctl's `Incoming:<TAB>` prefix, so every
  webhook lookup missed — and **a webhook-lookup miss mints a second webhook**.
  Three runs produced six webhooks.

Both are fixed, both are now pinned by tests, and the hook additionally
**refuses** an ambiguous display name outright rather than picking one
(naming the count, never the ids — S4.23).

All six duplicate webhooks were deleted and exactly two clean ones minted, for
the P106-S3 reason as well: their ids had appeared in an agent transcript. The
two live ids have not been printed anywhere since.

One step is idempotent by the *server's* contract rather than a local guard, and
is called out in the code: `mmctl team users` has no `list` subcommand in
11.10.1, so team membership is added unconditionally (re-adding an existing
member exits 0).

**Residual exposure, documented not buried**: `mmctl user create` takes the
password as a command-line flag — no stdin, no `*_FILE` form — so a generated
password is briefly visible in the host process table during account
**creation**; a reconcile over existing accounts passes no password at all.
This is strictly less exposure than the README recipe it replaces, which also
put the password in shell history and an exported variable.

Also added: an S9.5 `validate_config` preflight, so a typo in a username, an
undeclared `password_secret`, a webhook pointing at a nonexistent channel, or a
webhook secret a directive already owns is refused by `ciu check` / `ciu up`'s
preflight before anything starts.

### Secret placement

Passwords are `GEN_LOCAL` directives → **project** store
`<ciu-root>/.ciu/secrets/mattermost/{daemon,operator,installer}_password`.
Webhook URLs are hook-persisted (S9.4a) → **stack** store
`nyxloom/mattermost/.ciu/secrets/{daemon,installer}_webhook_url`. Both `0440
vscode:docker`, both gitignored by `**/.ciu/`, provenance recorded in
`.hook-persisted.toml`. The split is ciu's, not a choice.

**Secret migration needed at merge (same shape as P106's S1).** The five files
that exist only in this worktree's stores must be copied to the main checkout
before the first from-main `ciu up`, or the accounts' credentials are lost (no
SMTP, no recovery path). The worktree store was pre-seeded from main's
`admin_password`/`postgres_password` first (sha256-identical), so those two
need nothing.

---

## Workstream 3 — cutover (nyxloom's own config only)

`nyxloom/nyxloom-trove/nyxloom.toml`:

```toml
[notify]
backend = "mattermost"
mattermost_channel = "alerts"
```

`mattermost_username` was **removed**. P106 posted as `nyxloom-admin` with the
incoming webhook's `override_username` display trick; the daemon now has a real
non-admin account and posts as itself. The ntfy keys are kept below it so
`backend = "ntfy"` remains a one-word rollback.

Live-oracle verification, through nyxloom's own code path (not a raw curl):

```
backend       = mattermost
mm_channel    = alerts
mm_username   = None
backends      = ['MattermostBackend']
probe         = NotifyTransportProbe(status='healthy', channel='mattermost', ...)
send          = (True, 'mattermost ok')
```

A 200 is not proof of rendering, so it was read back out:

```
$ mmctl --local post list nyxloom:alerts --number 2
[nyxloom-daemon] :warning: **nyxloom-P107 cutover**
Posted by nyxloom's own notify.send() as the nyxloom-daemon account.
```

and the identity confirmed in the posts table — the row's `userid` resolves to
`nyxloom-daemon`, with `{"from_webhook": "true", "override_username":
"nyxloom-daemon", ...}`. The devcontainer was attached to the stack's own
bridge (`nyxloom-prod-mattermost_internal`) only for that check and **has**
been detached from it — verified.

(Correcting this report's own earlier wording, review F9: that sentence
originally read as if *all* attachments had been undone. The devcontainer is
still attached to the ciu **workspace identity** networks my `ciu` invocations
created — `nyxloom-885472-network` and `nyxloom-p107-791abb-network`, alongside
two older ones from previous nyxloom worktrees. Those are workspace-scoped
bridges, not the stack's data path; no security impact, and judgment item 7
already lists them as teardown-time cleanup.)

---

## Live stack state at hand-off

Unchanged and healthy; **no container was recreated in this package**.

```
nyxloom-prod-mattermost      Up (healthy)   cgroup=dev-background.slice mem=2147483648
                                            memswap=19327352832 nanocpus=1500000000
nyxloom-prod-mattermost-db   Up (healthy)   cgroup=dev-background.slice mem=536870912
                                            memswap=4294967296  nanocpus=500000000
in-container: memory.max 536870912 · memory.swap.max 3758096384 · cpu.max "50000 100000"
data: 6 users · 1 team · 4 channels · 17 posts · 2 live incoming webhooks
```

---

## Out of scope, confirmed untouched

- `expose_public` is still `false`; `EnableOAuthServiceProvider` is unchanged
  and still must be set false in the SAME edit that flips exposure.
- `/workspaces/dstdns` — not touched.
- `scripts/netcup/`, `scripts/debian-install-v2/` — not touched. Their TODO's
  "adopt nyxloom's Mattermost notification pattern" section was read read-only;
  this package only provisions the Mattermost side (`installs` channel,
  `nyxloom-installer` account, `installer_webhook_url`) for that work to point
  at later.

## Needs reviewer / operator judgment before merge

1. **The live storage conversion is a POST-MERGE step**, not done here, for the
   worktree-path reason above. Recipe in the README, rehearsed on real data.
   If the controller would rather the live stack were converted before merge,
   that has to happen from the main checkout.
2. **Secret migration at merge**: copy the three new `*_password` files (project
   store) and the two `*_webhook_url` files (stack store) from this worktree
   into the main checkout **before** the first from-main `ciu up`.
3. **Two webhooks, not three** — reasoning above; a third is one table entry if
   the operator wants one anyway.
4. **`installer_webhook_url` is not reachable from an install host** until
   `expose_public = true`. The credential is provisioned and waiting.
5. **The `--reset`/`clean` wipe of the hostdirs is spec-backed but not
   live-verified**, because both verbs would tear down the live stack.
6. The compose fallback (`docker-compose.yml`) now **shares** the three bind
   paths with the ciu path, so "stop the ciu stack first" became a hard
   requirement there; its header was rewritten accordingly, including a manual
   `chown` prep step it needs and ciu does not.
7. Leftover from my `ciu` runs against the worktree root: a
   `nyxloom-885472-network` bridge and a devcontainer attachment to it, plus
   the worktree's own `.ciu/` store. `ciu worktree rm` at teardown, not bare
   `git worktree remove`.

---

## Review round 1 (ACCEPT with nits) — what changed after it

Independent adversarial review accepted the package and raised two MEDIUM
findings in the exact bug class this package exists to prevent, plus four
cheap ones. All six are fixed here; F7/F8/F10 were explicitly the controller's
to carry and are untouched.

### F1 — the duplication guard did not defend against the actual failure mode

Correct and important. `_ensure_webhooks`' ambiguity refusal only fires at
`len(found) > 1`; the incident that minted six webhooks was the parser
returning `[]`, which that branch can never see. The fix went through three
cuts, and the two failed ones are worth recording because both were caught on
the LIVE instance rather than by reasoning:

1. **Cut 1 — cross-check the parsed count against mmctl's `There are N ... on
   local instance` sentence, scanned on stdout.** Refused every deploy: that
   sentence is on **stderr**, while the rows are on stdout
   (`mmctl --local channel list nyxloom 2>/dev/null` prints only names;
   `2>&1 1>/dev/null` prints only the sentence).
2. **Cut 2 — same cross-check, scanning both streams.** Refused every *empty*
   channel. `N` is a printed-LINE count, not an entity count: an empty private
   channel prints `No users found` on stdout and reports
   `There are 1 userss on local instance` on stderr. One "user", zero users.
   Caught by a live private-channel probe, which is also how F2 got its
   end-to-end test.
3. **Cut 3, shipped — `_refuse_unrecognised`: every non-empty stdout row must
   be recognised by the parser.** Needs no count sentence, so neither of the
   above can recur. It catches the original bug on the second run (six
   `Incoming:` rows, zero recognised → refuse), tolerates a genuinely empty
   list, skips `Outgoing:` rows rather than calling them drift, and names only
   the row COUNT in its message because a webhook row carries an id (S4.23).
   `_channel_names` additionally refuses an UNKNOWN ` (<kind>)` suffix, which
   is the shape F2's bug took.

### F2 — `_channel_names` mis-parsed private and archived channels

Correct, and both consequences the reviewer derived are real. Verified against
the running server rather than accepted on description: private channels carry
a ` (private)` **suffix**, archived a ` (archived)` suffix, and there is no
`*` prefix at all — the old `lstrip("*")` defended against a format mmctl does
not emit. Fixed: both suffixes handled, archived dropped, docstring corrected
with the real output.

Verified end-to-end on the live instance, which is the evidence that matters
here since neither case was reachable with the four existing channels:

```
create private channel out of band  -> memberships_added=1 (operator joined it)
re-run                              -> memberships_added=0   (idempotent)
archive it                          -> run succeeds, channel correctly dropped
delete it, re-run                   -> back to baseline
```

Pre-fix, run 1 would have tried to re-create `p107-f2-priv` and aborted the
deploy permanently; post-archive, `_channel_members(team, "x (archived)")`
would have aborted every subsequent `ciu up`. Also improves the error text when
a *declared* channel goes missing, so "archived" is named as a cause.

### F3 — `channel users list` pagination

`--all` added. Pages at 200 by default; past that an unpaged read
under-reports and re-triggers the membership re-add loop. Pinned by a test that
asserts the flag is actually passed.

### F4 — the second P106 "uid 999"

Fixed. `ciu.defaults.toml.j2`'s secrets comment said "postgres, uid 999" while
the compose file's copy had been corrected — the package's own headline
correction sitting uncorrected in the file that makes it. The conclusion it
supports (file mode 0444) is unaffected by the number, and says so now.

### F5 — the compose fallback's hardcoded absolute paths

Disclosed in the header rather than re-templatised. The three bind paths are
absolutes under the MAIN checkout, so running that file from a worktree writes
into main's `vol-*` — the inverse of the worktree-path trap this report
describes for the ciu side. The header now says so and says not to do it.

### F6 — real ids in test fixtures

Swapped for synthetic (`aaaadaemon…`, `ddddinstaller…`). They were revoked and
non-live, but the file already used a synthetic style elsewhere.

### F9 — this report contradicted itself

Fixed in Workstream 3 above: the devcontainer *was* detached from the stack's
own bridge; it is still attached to the ciu workspace-identity networks, which
judgment item 7 already covers as teardown cleanup.

### Test coverage after the round

16 tests → **33**. The new ones cover: empty-parse drift refusal end-to-end
(`_ensure_webhooks` must abort, not mint), the `No users found` sentinel that
broke cut 2, noise-line classification, `Outgoing:` rows not counting as drift,
the private/archived suffixes, an unknown kind suffix, an empty team, `--all`,
and the archived-channel error text. Every one of them corresponds to a
failure that actually occurred against the live server during this round.

### Live state after the fix round

Unchanged from hand-off and re-verified: 6 users, 4 channels
(`alerts installs off-topic town-square`), **2** incoming webhooks, both
containers healthy, no container recreated, no storage touched.
