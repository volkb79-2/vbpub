# nyxloom-P109 — feature-intake chat bridge over Mattermost (backlog B9)

**Branch:** `nyxloom-P109` · **Worktree:** `/workspaces/vbpub/.worktrees/nyxloom-p109`
**Date:** 2026-09-09 · **Backlog:** B9 "intake-over-ntfy chatbot (human-initiated
new direction)", retargeted at Mattermost after the P106/P107 cutover; the
`steering-loop-design.md` **Phase D** item.

`intake_chat.py` (P29) was already the whole conversational backend and had no
transport. This package is that transport and nothing else: it reads one
Mattermost channel, folds new posts into ONE `advance_intake` turn, posts the
reply through `notify.send()`, and exits.

---

## 1. What was measured before anything was designed

Every wire fact below was verified against a real Mattermost 11.10.1 — the live
`nyxloom-prod-mattermost` for read-only probes, and a **throwaway instance** for
anything mutating. Six of them contradict the obvious guess, and each one would
have been a silent defect.

The throwaway instance used **named volumes only** (`p109-scratch-pgdata`,
`p109-scratch-mmconfig`, `p109-scratch-mmdata`), its own network, its own
container names, `--memory`/`--cpus` caps, and **no bind mounts and no `ciu up`**
— deliberately sidestepping the P107 hazard where a same-directory scratch stack
shares physical hostdir storage with prod. Zero overlap was confirmed against
`docker inspect nyxloom-prod-mattermost`'s mounts before starting, and the whole
instance (containers, volumes, network) plus both captured tokens were destroyed
afterwards; prod was verified still healthy.

| # | Claim | Measured result |
|---|---|---|
| 1 | `mmctl post list --since` takes "ISO 8601" | Accepts **only** `2006-01-02T15:04:05-07:00`. A trailing `Z` is **rejected** (`invalid since time`). The obvious RFC3339 spelling is the one that does not work. |
| 2 | `--number` bounds a `--since` window | **Ignored** when `--since` is present. A since-window is unbounded in size. |
| 3 | `--since` boundary | **Inclusive** (`create_at >= t`), truncated to whole seconds. |
| 4 | REST `?since=` boundary | **Exclusive**, and matches on `update_at` — so an **edited old post reappears** in a REST window and never in an mmctl one. |
| 5 | Result ordering | mmctl JSON is **oldest-first**; REST `order` is **newest-first**. Opposite. |
| 6 | `mmctl --json` container shape | **Not stable.** `post list` prints one row *per post*: 0 posts = **empty stdout**, 1 post = **bare object**, N = array. `token list` prints the whole slice as one row: 0 = `null`, N≥1 = array. A parser assuming "array" reported a one-post channel as **21 messages** (it counted the post's dict keys). |
| 7 | `mmctl post create` in local mode | **Refused** — "creating posts is not supported in local mode". The mmctl transport is read-only *by construction*. |
| 8 | `mmctl bot create` in local mode | **Refused** — "This command cannot be run in local mode". |
| 9 | PAT needs `system_user_access_token` on the target | **No.** Minted successfully against a plain `system_user` with no extra roles. (`mmctl roles` could not have granted it anyway — it only promotes/demotes system admin.) |
| 10 | `token generate` output | plain `<token>: <description>`; `--json` a **one-element array** with the secret in `token`. The secret is on **stdout**. |
| 11 | `token list` output | `<id>: <description>`; **never** the token value. Empty = rc 1 + `there are no tokens for the "<user>"` on stderr, empty stdout. |
| 12 | `token revoke <id>` | **Deletes** the row — absent from `--active` *and* `--inactive` afterwards. |
| 13 | Private-channel read boundary | Plain `system_user`, non-member: **public** channel posts → HTTP 200; **private** → HTTP **403**. Channel membership is a real boundary only when the channel is private. |
| 14 | Mattermost's first-ever account | Auto-promoted to `system_admin`. This invalidated an earlier probe of mine and is why every permission claim above was re-measured against a *second*, plain account. |

Fact 14 is worth calling out as a method note: my first private-channel probe
returned 200 and looked like "team members can read any private channel". The
account was silently a system admin. Every permission conclusion in this report
comes from the second, non-admin account.

---

## 2. Judgment calls

### JC-1 — Dedicated identity? **Yes: a 5th account `nyxloom-intake` + a new PRIVATE `intake` channel.**

P107's stated boundary is "team membership + channel scoping is where the real
boundary lives, not just which account posts". Applying that same test:

B9 introduces a capability **none of the existing four accounts has: reading.**
Every current identity is post-only (`nyxloom-daemon`, `nyxloom-installer`, via
channel-bound webhooks) or a human login (`nyxloom-operator`). A PAT is a
read **and** write bearer credential that inherits its account's *full*
permissions, so the account is the only scoping mechanism available (see JC-2).
Each reuse candidate fails on exactly that:

- **`nyxloom-operator`** — system admin. A leaked PAT is a system-admin bearer
  token on a service headed for public exposure. Non-starter.
- **`nyxloom-daemon`** — a PAT here could read *all* of `alerts` (decision
  pushes, budget figures, operator traffic) and **post as the daemon**, i.e.
  forge notifications. It also fuses the notification identity with the
  conversational one.
- **`nyxloom-installer`** — a third party's credential, scoped to `installs` for
  precisely the reason P107 gives. Adding a second, unrelated capability to it
  is the opposite of what it is for.
- **`nyxloom-admin`** — the bootstrap account the hook deliberately never
  touches.

So: `nyxloom-intake`, **regular** (never `system_admin`), member of `intake` and
nothing else. A leaked intake PAT can read and post in one channel it was
already conversing in, and can do nothing else.

**The channel is required, not optional.** The reader ingests every human post
in its channel as an interview turn — pointing it at `alerts` would turn every
operator remark on a decision push into a new intake turn.

**And it is PRIVATE, which is where the boundary actually lives** (fact 13): a
public `intake` would be readable by every team member's session or PAT, so
"member of one channel" would bound nothing. This is also the **first live use
of `private = true` from the shipped config surface** — P107's ` (private)`
suffix handling in `_channel_names` was written for exactly this and had never
been exercised. It was re-verified against the throwaway instance
(`channel list` prints `intake (private)`; a private-channel incoming webhook
mints and delivers normally), and the post-merge recipe exercises it *before*
anything is widened.

### JC-2 — PAT scope and storage

**Scope.** Mattermost Team Edition has no per-token scoping: a PAT inherits its
owning account's full permissions. There is no knob to turn. The account **is**
the scope — which is what makes JC-1 a security decision rather than tidiness.

**A cheaper path was looked for and does not exist.** Mattermost exempts *bot*
accounts from `EnableUserAccessTokens` entirely (confirmed in
`app.CreateUserAccessToken`: `if !*...EnableUserAccessTokens && !user.IsBot`),
which would have avoided the server-wide flip altogether. It is **unreachable
here**: `mmctl bot create` is refused in local mode (fact 8), and reaching the
bot API means the network admin API plus an admin password — the exact widening
this stack's local-mode design rejects. **The operator's decision that the flip
is required is therefore correct, and it is now correct on evidence rather than
assumption.**

**Storage.** The PAT is persisted through the same `persist: "secret"` (S9.4a)
channel the webhook URLs use, landing at
`nyxloom/mattermost/.ciu/secrets/intake_pat` (0440, `**/.ciu/` gitignored,
visible to `ciu secrets list`, removable by `ciu secrets reset`). It reaches the
consumer as `NYXLOOM_INTAKE_MM_TOKEN`, env-wins-over-toml, exactly like
`NYXLOOM_WEBHOOK_URL`; the value is never in a committed file.

**The one new exposure, stated rather than buried.** `token generate` prints the
secret on **stdout** (fact 10). It is never an *argument*, so unlike the
password flags it does not reach the host process table. It is read out of one
captured `docker exec`, handed straight to S9.4a, and `_must` is deliberately
**not** used for that command because `_must` folds stdout into the exception it
raises. A test pins that the error path cannot carry it.

**Expiry** is supported (`expires_in` → `--expires-in`) but **unset by default**:
an expiring token would silently break the bridge between two `ciu up` runs and
there is no daemon to notice. A revoked/expired token vanishes from `token list`
(fact 12), so the hook self-heals on the next run either way — expiry is safe to
enable *alongside a schedule that re-runs provisioning*, and is documented that
way rather than defaulted.

### JC-3 — Idempotent PAT provisioning

`token list` **does** expose the description (fact 11), so deduplication works
the same way `_ensure_webhooks` matches on display name, with the same
`_refuse_unrecognised` drift guard applied to the token rows.

**But `token list` never returns the token VALUE** (fact 11). That makes "a token
with our description exists" and "we still hold that token" *different
questions*, and only the second one keeps the consumer working. `_ensure_tokens`
therefore keys on **both**:

| Mattermost | Store file | Action |
|---|---|---|
| absent | absent | mint, persist |
| present | present | **nothing** — no re-mint, no rotation, no second write |
| present | **absent** | **REFUSE**, naming the exact `token revoke` that resolves it |

The third row is the one that must not be guessed at. Skipping it (the naive
"it exists, we're done") leaves the consumer with no credential *forever*, with
no signal. Minting again leaves a live orphan token nobody can revoke by value
and starts exactly the unbounded duplication P107's F1 fix exists to prevent.
Two same-description tokens is likewise a refusal, not a coin flip.

**A real ciu limitation was found doing this and is filed, not hidden.**
`ctx.secret_file(name)` resolves *declared* directive names only and raises
`KeyError` for every hook-persisted name — which is by definition every S9.4a
name. A hook cannot read back its own persisted secret through the context it
was given. Worked around by computing `Path(ctx.stack_dir)/".ciu"/"secrets"/name`
(the path S9.4a states **normatively**, so this is not reaching around the API —
but it does hardcode a layout the context should own). Filed as **CIU-102**.

### JC-4 — Cursor tracking, and whether it is an event

**Per transport, verified rather than assumed** (facts 1–6). The two transports'
windowing semantics differ in three ways that would each silently corrupt a
cursor. The design consequence: **neither reader is trusted to have windowed
correctly.** A reader's only job is "hand me posts at or after this
millisecond"; the cursor filter, the loop guard and the human/program split live
once, in `poll_once`, for every transport — because those are exactly the rules
that must not be able to differ between two implementations of one bridge.

The cursor is `(last_create_at_ms, boundary_ids)`. `boundary_ids` is not
decoration: `create_at` is not unique and mmctl's `--since` is second-truncated,
so without it two posts in the same millisecond either both replay or one is
lost depending on rounding. `_mmctl_since` floors to the second, which is safe in
exactly one direction (mmctl's window is *inclusive*, so flooring can only
over-fetch); REST is queried at `since - 1` because its window is *exclusive*.
Both over-fetch on purpose and let the shared id filter decide.

**Not event-sourced, and the line is worth stating.** The event log is the audit
ledger of *domain* state, replayable into task/decision state. A poll cursor is
neither: it is one reader's resumption offset into an external system's clock,
meaningless on replay and derived from a source the ledger does not own. The
local precedent is exact — `IntakeChat.session_id` and `DecisionChat.session_id`
are the same class of transport-internal resumption handle and live in the same
kind of JSON record beside the same `project_dir`.

**What IS event-sourced is the consequence.** Every turn the bridge drives
appends `INTAKE_REPLY_RECORDED` under the *named operator* — the same event
`daemon.py`'s authenticated HTTP `/api/intake` route appends — and the D-NNN
decisions and backlog items a turn opens are audited by their own existing
paths. A refused ingress appends `CONTROL_MUTATION_REFUSED` to the control
ledger, like every other channel refusal.

### JC-5 — Invocation shape

`nyxloom intake-bridge poll <project> [--transport mmctl|rest]`. One poll, then
exit: no thread, no sleep, no signal handling anywhere in the module, so B20 can
adopt `poll_once` as a job without unpicking a loop.

**A separate verb GROUP, not `intake poll-mattermost`.** `intake <project>
<intake_id> <message>` is a frozen three-positional contract (P29) that cannot
grow a sub-parser without breaking it. The hyphenated-group shape matches the
established `free-models list` / `capability-map refresh` / `route doctor`
convention. `--transport`'s `choices` come from `config.INTAKE_TRANSPORTS`
rather than a literal, so a transport added to the registry cannot be missing
from the CLI.

Exit code: **1 only on a refusal** (no named channel operator). An unconfigured
bridge and an empty channel are both 0 — a scheduled job must not alarm because
nobody has spoken yet.

---

## 3. What was built

| File | Change |
|---|---|
| `src/nyxloom/intake_bridge.py` | **new** — the module: `MessageReader` seam, `MmctlReader`, `RestReader`, `resolve_reader`, `BridgeState`, `poll_once`. |
| `src/nyxloom/config.py` | `IntakeBridgeConfig` + `INTAKE_TRANSPORTS` + `[intake_bridge]` parsing with env-wins credential resolution. |
| `src/nyxloom/cli.py` | `intake-bridge poll` verb group + frozen-contract docstring entry. |
| `mattermost/hooks/post_compose_provision.py` | `_user_tokens`, `_generate_token`, `_ensure_tokens`, `_hook_secret_path`; token step in `run()`; `[[...tokens]]` preflight in `validate_config`. |
| `mattermost/ciu.defaults.toml.j2` | private `intake` channel, `nyxloom-intake` account + password secret, `intake_webhook_url`, `[[mattermost.provision.tokens]]`, `enable_user_access_tokens = false`. |
| `mattermost/ciu.compose.yml.j2` | `MM_SERVICESETTINGS_ENABLEUSERACCESSTOKENS` templated on that flag. |
| `mattermost/README.md` | account/webhook tables, the `intake`-channel + PAT section, the post-merge recipe (§5 below). |
| `nyxloom-trove/nyxloom.toml` | `[intake_bridge]` for nyxloom itself, defaulting to `mmctl`. |
| `docs/USAGE.md` | the new verb group in the canonical CLI table — the gap P102's retroactive review found for six earlier verb groups. |
| `tests/test_intake_bridge.py` | **new** — 73 tests; `intake_bridge.py` at 100% statement coverage. |
| `tests/test_mattermost_provision_hook.py` | +25 tests (the token surface, plus five that validate the ACTUALLY-SHIPPED `ciu.defaults.toml.j2` rather than a synthetic dict). |
| `ciu/KNOWN_ISSUES_TODO_BACKLOG.md` | **CIU-102** filed — on **this branch**, not committed to `main` separately, so the finding lands with the package that found it. |

### Two design points worth flagging to the reviewer

**Merging this does not change the live stack's behaviour, and cannot break its
next `ciu up`.** `[[mattermost.provision.tokens]]` is **inert while
`enable_user_access_tokens` is false** — the hook prints the token names it is
*not* minting and returns, rather than attempting a mint that a
PAT-disabled server refuses. Without that gate, merging the entry would break
the very next `ciu up` on prod. The account, private channel and reply webhook
*do* provision on an ordinary `ciu up` with no flag change — the same
"provisioned and waiting" shape `installer_webhook_url` already has against
`expose_public`.

**A second real bug, caught by driving coverage to 100%.**
`bootstrap_messages > 0` was broken: the first-poll path called
`_advance_cursor(state, fetched)` over the *whole* fetch before the freshness
filter ran, so it marked the very messages it was adopting as
already-decided — the filter then dropped every one of them and the poll
reported success having ingested nothing. It was invisible because the shipped
default is `bootstrap_messages = 0` and every test until then used the default.
The cursor now parks *just before* the adopted window
(`fetched[:len(fetched) - len(adopted)]`), so adopted messages still flow
through the ordinary fresh/human/coalesce path and get the same loop guard and
one-turn-per-poll bound as any later message. Three tests pin it.

**A smaller one, caught on re-read.** The reply's `click` target was
`intake.html?intake=<id>`. `intake.html` reads **no query parameters at all** —
it renders every open conversation on one page — so that link only *looked*
like it deep-linked. It does emit `id="transcript-<intake_id>"` per card
(`render.py`), so the target is now that **fragment**, which actually scrolls to
the conversation, and falls back to the bare page URL for any id that is not
fragment-safe (the conversation-reset notice has no id at all).

**A bug this design nearly shipped, caught and pinned by a test.**
`cfg.notify.mattermost_channel` is `"alerts"` on the live deployment, and
Mattermost honours a payload `channel` key as an **override**. Inheriting
`cfg.notify` wholesale would have posted **every intake reply into `alerts`** —
the notification channel, bound to a different account. `_reply_channel` clears
it explicitly (the webhook is already channel-bound, so the correct value is "no
override at all"), and `test_the_reply_never_inherits_the_alerts_channel_override`
fails without that line.

### Safety properties

- **Loop guard** — the bot's own replies arrive through the incoming webhook and
  would otherwise be re-ingested as human turns forever. Guarded on
  `props.from_webhook == "true"`, which is **server-attributed**, not
  self-declared text — the guard ntfy could never have (`decision_chat`'s
  tag-based one is self-declared by necessity). Plus `type != ""` (system posts)
  and `delete_at != 0`.
- **Skipped posts still advance the cursor**, or the bot's own replies are
  re-fetched on every poll forever and the window grows without bound.
- **One poll = at most one model call.** All of a poll's new messages coalesce
  into one turn: the poll interval *is* the utterance boundary, and three lines
  typed between two polls are one thing the operator said. Anything past
  `max_messages_per_poll` is deferred, never dropped.
- **SPEC §13** — decision_chat's three safeguards inherited unchanged
  (redaction + read-only tool allowlist + reply cap, all inside
  `advance_intake`), plus an **inbound** cap (`MAX_TURN_CHARS`) decision_chat has
  no equivalent of, because an ntfy message is small and a Mattermost post is
  not.
- **Fail closed, in order.** The operator is resolved *before* any state is read
  (CR-15's rule: a refusal must not be distinguishable by what it touched). No
  reply webhook → refuse rather than fall back to `cfg.notify` (which would
  answer an intake question in `alerts`). A corrupt cursor → refuse rather than
  degrade to 0 and re-run finished interviews. An unparseable mmctl payload →
  refuse rather than report "nothing new" (P107's lesson).
- **`resolve_reader` has no fallback chain**, unlike `notify.resolve_backends`:
  falling back from `rest` to `mmctl` would silently re-route a token-scoped read
  onto the unrestricted local-mode admin socket — an *escalation*, not a
  degradation.

---

## 4. Gate + verification

### Mutation-verified tests

Per the brief's warning about a test made vacuously true by a bad seed, every
central assertion was checked against a deliberately broken implementation.
**18 mutations, 18 caught:**

| Mutation | Test that failed |
|---|---|
| loop guard disabled | `test_the_bots_own_reply_is_never_ingested` |
| `_json_rows` drops the bare-object shape | `test_mmctl_reader_uses_since_once_a_cursor_exists` |
| `--since` spelled with `Z` | `test_mmctl_reader_uses_since_once_a_cursor_exists` |
| reply inherits the `alerts` channel override | `test_the_reply_never_inherits_the_alerts_channel_override` |
| skipped posts do not advance the cursor | `test_skipped_posts_still_advance_the_cursor` |
| operator gate moved after the read | `test_poll_refuses_without_a_named_channel_operator` |
| bootstrap ingests the whole channel | `test_first_poll_adopts_the_head_and_ingests_nothing` (+2) |
| REST `since` not decremented | `test_rest_reader_windows_one_ms_below_the_cursor` |
| coalescing removed | `test_one_poll_costs_exactly_one_turn...` (+1) |
| orphan case silently skips | `test_ensure_tokens_refuses_the_orphan_rather_than_minting_a_second` |
| empty `token list` treated as failure | `test_user_tokens_treats_the_no_tokens_error_as_an_empty_set` (+1) |
| drift guard removed from `_user_tokens` | `test_user_tokens_refuses_a_drifted_row_format` |
| server-flag gate removed | `test_ensure_tokens_skips_entirely_while_the_server_flag_is_off` |
| token generate leaks stderr into its error | `test_generate_token_error_never_carries_the_token_value` |
| bootstrap advances past the adopted window | `test_bootstrap_messages_adopts_that_many_from_the_head` (+2) |
| `_json_rows` stops handling a `null` payload | `test_json_rows_handles_the_array_and_the_two_empty_spellings` |
| click target drops its fragment-safety guard | `test_a_click_target_never_interpolates_an_unsafe_id` |
| REST error echoes the response body | `test_rest_reports_the_http_status_and_never_the_response_body` |

Every fixture payload in both test files is a **verbatim 11.10.1 capture**, not a
hand-written approximation — the entire class of bug this code can have is "the
wire shape is not what the parser assumed".

### Gate

`run-gate tester-unified --fresh`, run from the worktree's `nyxloom/`. Verdict
read from `.assay/verdict-tester-unified.json` in a **separate step**, never a
pipe tail (LESSONS L4):

| field | value |
| --- | --- |
| `outcome` | **PASS** (`reason_code: null`, `exit_code: 0`) |
| `commit` | `16c3e36107f23dfcd25bd9e536033914995a6ad8` |
| `enforcement` / `scope` | `gate` / `S1` |
| `declared_rigor` | `["R0", "R1"]` — **R0 PASS**, **R1 PASS** |
| R1 changed-line coverage | **100.0% (446/446 executable)**, `files_missing_coverage: []` |
| judged base | `ec868f141a7f0bc34eabc1be21315441d1117d24` (merge-base) |
| assay | 6.1.0 |

Independent of the gate, at the same commit: the full suite serial under
`nice -n 10 ionice -c2 -n7` is **3932 passed** in 339.99s, exit 0.

#### A host-rule conflict the controller should know about

This took **three** gate attempts, and the two invalid ones were caused by the
standing host rule, not by this branch:

| run | container CPU cap | result |
| --- | --- | --- |
| 1 | — | aborted: `--worktree` path doubling (the RG-47 shape) — re-run from the project dir instead |
| 2 | `--cpus=3` | `FAIL / COMMAND_FAILED` — 2 failures in `tests/test_behavioral.py`, a file this branch does not touch. **R1 already passed at 100.0%** |
| 3 | `--cpus=3` | `BUDGET_EXCEEDED / LANE_TIMEOUT` at the lane's `budget = "30m"`; container pinned at its ceiling (295% of 300%) |
| 4 | `--cpus=6` | **PASS**, 9m24s wall |

The lane's judged argv is `pytest tests -n auto`, documented in `assay.toml` as
"the measured optimum on this 8-core host" — so `-n auto` spawns 8 workers. The
standing host rule (`docker update --cpus=3` on any container you launch) then
oversubscribes those 8 workers onto 3 CPUs, 2.7x. The two behavioral tests that
failed at run 2 are bounded `for _ in range(20)` daemon-tick loops driving real
subprocesses; under that contention they exhaust their tick budget before the
state they wait on lands. Evidence it was contention and not this branch: at the
identical commit, the full suite passes serially (3932 passed), the full suite
passes under `-n 4`, and `test_behavioral.py` alone under xdist is 14 passed.

`[environments.tester-unified]` in the monorepo-root `run-gate.toml` declares
**no `resources.cpus`** — the gate container starts CPU-uncapped and is
restrained only by `dev-background.slice` (production Wings sits in a separate
`wings-mgmt.slice`, `NanoCpus=0`). Run 4 used `--cpus=6`, chosen as a deliberate
reconciliation rather than a quiet exemption: it keeps the gate off two of the
host's eight cores and inside the deprioritised slice, while matching the
8-worker layout the lane declares.

Filed as **RG-48** in `run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md` (this
branch), with the measurements above: either declare `resources.cpus` for the
environment so the cap and `-n auto` are decided in one place, or let the lane
pin its worker count. **The RG-48 heading carries an ID note** — this branch's
base predates main's RG-47, so renumber on merge if 48 was taken concurrently
(the same collision this branch already hit once, with CIU-102).

---

## 5. POST-MERGE RECIPE (for the controller — the live stack)

Also in `mattermost/README.md` ("Enabling personal access tokens + minting the
intake PAT"), which is the copy to keep current. Run from
`/workspaces/vbpub`.

**Step 0 needs no flag change and is worth doing on its own first** — it is the
first live exercise of the private-channel parse, and the hook *refuses* there
if `channel list` has drifted, before anything is widened.

```bash
# 0. WITHOUT the flag: account + private channel + reply webhook only.
#    Expect channels_created=['intake'] accounts_created=['nyxloom-intake']
#    and a line naming intake_pat as NOT minted.
ciu up --dir nyxloom/mattermost -y --define-root /workspaces/vbpub/nyxloom
docker exec nyxloom-prod-mattermost mmctl --local channel list nyxloom     # expect `intake (private)`
docker exec nyxloom-prod-mattermost mmctl --local channel users list nyxloom:intake --all
ls -l nyxloom/mattermost/.ciu/secrets/intake_webhook_url                   # 0440, non-empty

# 1. THE WIDENING (operator decision). In ciu.defaults.toml.j2, [mattermost]:
#      enable_user_access_tokens = true
#    Re-render and CONFIRM the compose carries it before `up`.
ciu up --dir nyxloom/mattermost -y --dry-run --define-root /workspaces/vbpub/nyxloom
# ciu's rendered compose output is `ciu.compose.yml` at the stack root (S8.5)
# -- NOT the hand-maintained `docker-compose.yml` fallback beside it, which
# receives no ciu overlay and is not what `ciu up` deploys.
grep ENABLEUSERACCESSTOKENS nyxloom/mattermost/ciu.compose.yml   # expect "true"

# 2. Real up. The container RECREATES (an env change) -- re-verify governance in
#    the same breath; a recreate is exactly where it silently drops (P106).
ciu up --dir nyxloom/mattermost -y --define-root /workspaces/vbpub/nyxloom
docker inspect nyxloom-prod-mattermost \
  --format '{{.Name}} {{.HostConfig.CgroupParent}} {{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}'

# 3. Verify the PAT was minted ONCE and is idempotent.
#    The SECOND `ciu up` must print tokens_minted=[]. If it mints again, STOP
#    and revoke -- that is the duplication failure mode.
docker exec nyxloom-prod-mattermost mmctl --local token list nyxloom-intake
ls -l nyxloom/mattermost/.ciu/secrets/intake_pat                           # 0440, non-empty
#    Capture, THEN read -- a `| grep` here would report grep's exit status as
#    ciu's (LESSONS L4, the pipe-tail hazard, same reason gate verdicts are
#    read in a separate step).
ciu up --dir nyxloom/mattermost -y --define-root /workspaces/vbpub/nyxloom \
  >/tmp/p109-reup.log 2>&1; echo "ciu up rc=$?"                            # rc MUST be 0
grep tokens_minted /tmp/p109-reup.log                                      # expect tokens_minted=[]
docker exec nyxloom-prod-mattermost mmctl --local token list nyxloom-intake  # still exactly ONE

# 4. Point the bridge at it. Neither credential is committed.
export NYXLOOM_INTAKE_WEBHOOK_URL="$(cat nyxloom/mattermost/.ciu/secrets/intake_webhook_url)"
export NYXLOOM_INTAKE_MM_TOKEN="$(cat nyxloom/mattermost/.ciu/secrets/intake_pat)"
export NYXLOOM_CHANNEL_OPERATOR_ID="<the operator identity this channel belongs to>"

# 5. Exercise the `mmctl` transport against the real channel. It runs from the
#    controller's shell because the docker socket IS its transport.
#    The FIRST poll of a channel only adopts the head (status=bootstrapped,
#    nothing ingested). That is correct, not a failure. Then post something in
#    `intake` from the Mattermost UI as nyxloom-operator and poll again.
nyxloom intake-bridge poll nyxloom --transport mmctl
```

**Step 5 does NOT include `--transport rest`, and that is a real limitation,
not an omission.** `base_url` is `http://nyxloom-prod-mattermost:8065`, a name
that resolves only inside the `nyxloom-prod-mattermost_internal` docker
network; the stack publishes no host port, deliberately. The controller's own
shell is not on that network, so a `--transport rest` poll there fails with a
connection error and demonstrates nothing. **The REST path against the LIVE
stack stays unverified until `nyxloomd` runs as a container on that network** —
which this track has explicitly deferred. It was verified end-to-end against
the throwaway 11.10.1 instance during development, and its parsing is pinned by
unit tests built from verbatim captures of that instance.

The half that is genuinely in doubt on the live stack — does the PAT
authenticate, and is the private channel readable with it — *can* be checked
today without deploying anything, with a one-shot container that makes exactly
`RestReader.fetch`'s two GETs using the same stdlib client and exits. The exact
command is **step 5b** in `mattermost/README.md`; it uses `python:3-slim`
(already on this host), no pip, no build, nothing persistent. A 401 means the
PAT is revoked or the flag went back off, a 403 means `nyxloom-intake` is not a
member of `intake`, and a connection error means the container did not join the
right network — all live-stack findings, none of them bridge defects.

**Expected end state**: exactly one token on `nyxloom-intake`; `intake` private
with `nyxloom-intake` + `nyxloom-operator` as members; one `nyxloom-intake`
incoming webhook; a reply visible in `intake` and **not** in `alerts`.

**Rollback is a flag, not a migration**: set
`enable_user_access_tokens = false`, `ciu up`, then
`mmctl --local token revoke <token-id>` and delete `.ciu/secrets/intake_pat`.
The `mmctl` transport keeps working throughout — that is why it exists.

**Do not skip the revoke when deleting the store file, or vice versa.** A live
token with no store file is the orphan state the hook refuses on (JC-3); it is
recoverable, but only by revoking.

---

## 6. Not done / out of scope (as briefed)

- No daemon, no background loop, no scheduler (B20/F015) — `poll_once` is a
  clean future consumer of one.
- No dashboard/UI change — deliberately sequenced after this package.
- `expose_public` untouched.
- **PATs were not enabled and no PAT was minted on the live stack.** The
  throwaway instance was destroyed and prod verified healthy.

### Left for the reviewer's attention

1. **The private `intake` channel is the first live use of `private = true`.**
   Verified on a throwaway instance and covered by P107's existing unit tests,
   but never yet run against prod — hence step 0 of the recipe standing alone.
2. **`_hook_secret_path` hardcodes ciu's store layout** (S9.4a-normative, filed
   as CIU-102). If CIU-102 is fixed, that helper should collapse into
   `ctx.secret_file`.
3. **The `new intake` control phrase** is the only inbound verb, anchored and
   argument-free. It exists because there is no daemon to notice a wedged
   interview and no second channel to abandon one from. If the reviewer thinks
   any inbound verb is too much surface, it is six lines to remove.
4. **A known, accepted narrow edge in the per-poll cap.** `decided` is
   computed as "every fresh message at or before the last TAKEN message's
   `create_at`". If the cap bites *exactly* between two messages sharing one
   millisecond, the untaken twin is marked decided and never ingested.
   Reaching it needs >`max_messages_per_poll` (default 20) new messages in one
   poll *and* a millisecond collision precisely at the cap boundary. Recorded
   rather than engineered around; the fix, if a reviewer wants one, is to cut
   `decided` on `(create_at, post_id)` instead of `create_at` alone.
5. **Author-level authorisation is the channel operator plus channel
   membership**, not a per-username allowlist. Mattermost *does* attribute a
   verifiable author (unlike ntfy), so a username allowlist would now be
   meaningful — it was left out because the private channel's membership already
   answers "who may speak here", and a second list would drift from it.

---

## 7. Independent review round 1 — NEEDS-FIXES (documentation only), resolved

An independent reviewer re-verified all 14 numbered claims above, mutation
reproduction included, and found **no defect in any of the code**
(`intake_bridge.py`, `config.py`, `cli.py`, the provisioning hook). Two
blocking documentation findings and four nits; all six are fixed here.

| # | Finding | Fix |
| --- | --- | --- |
| **F1** | The recipe's step 5 could not execute as written: `base_url` resolves only inside `nyxloom-prod-mattermost_internal`, and the recipe told the controller to run `--transport rest` from a shell that is not on that network. | Step 5 now runs `mmctl` only. New **step 5b** states plainly that the live REST path stays unverified until `nyxloomd` runs on that network, and supplies a one-shot `docker run --rm --network …` probe (python:3-slim, stdlib urllib, no pip, nothing persistent) making exactly `RestReader.fetch`'s two GETs. |
| **F2** | `ciu.defaults.toml.j2` claimed the token step *raises* under a false flag; `_ensure_tokens` prints and returns `{}`. Wrong mechanism, in the comment an operator reads before deciding the flag is safe to leave off. | Comment rewritten to the module docstring's own (correct) framing: a no-op that announces what it is not minting. |
| N1 | `channel_operator_for` hardcoded an `ntfy:` prefix, so P109's audit record read `ntfy:mattermost:intake-bridge`. | Prefix moved to the three call sites; the helper now records what it is given. Existing ntfy payloads are byte-identical. The bridge's audit test was strengthened from "a refusal exists" to pinning the exact payload — the weakness that let this through. |
| N2 | `cli.py` and `docs/USAGE.md` said exit 1 "only" on a refused ingress; a `BridgeError` also exits 1 via `main()`'s catch-all. | Both corrected; a new test pins the `BridgeError` → 1 path so the sentence a B20 consumer will be written against is enforced. |
| N5 | The "merging cannot widen the live server" tests all sat behind `importorskip("jinja2")` and would skip silently in a gate container without it. | Added a jinja-free assertion on the shipped literal: exactly one `enable_user_access_tokens` assignment, and it is `false`. |
| N6 | Recipe step 3 read `ciu up … \| grep tokens_minted`, replacing ciu's exit status with grep's (LESSONS L4). | Capture to a file, echo the real `rc`, then grep. |

N3/N4/N7/N8 and observations O1–O4 were judged non-blocking and are
deliberately untouched.

**Each of the three new/changed assertions was verified red against a broken
implementation** before being accepted: restoring the `ntfy:` prefix fails the
audit-payload test; flipping the template flag to `true` fails the jinja-free
test; making the CLI swallow `BridgeError` fails the exit-code test.
