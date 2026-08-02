# nyxloom deep review — amendment

Date: 2026-08-02
Parent: [`DEEP-REVIEW-2026-08-01.md`](DEEP-REVIEW-2026-08-01.md)
Companion: [`CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md`](CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02-AMENDMENT.md)

## What this document is

An independent second pass over the same tree, written after re-deriving the parent
review's claims from the code rather than from its prose. **The parent stands except where
this document amends it.** Its architecture reading is accurate, its two P0 behavioural
risks are real, and its recommended direction is sound. What follows is: the claims that
did not survive verification, six risks it did not raise, and the amendments those imply.

The parent review is a product and architecture review. This amendment leans harder on the
*deployed* system, because three of the six new risks are only visible there — in the
compose files, the container state, and the environment the daemon actually hands its
agents. A review that reads only `src/` cannot see them.

## Verification of the parent's claims

Re-derived against the tree at commit `0d421212`, i.e. before the fixes this amendment
applies. Line numbers are therefore the parent's numbers, not the current working tree's.

| Parent claim | Verified | Note |
| --- | --- | --- |
| `daemon.py` 8,300 lines | Yes — 8,308 | `Daemon` is one class with 155 methods |
| `_execute` ~1,060 lines | Yes | 6,362–7,425 |
| `plan_project` ~1,160 lines | Yes | 975–2,137 |
| `reconcile.py` 2,302 / `render.py` 2,526 | Yes | |
| `cli.py` 2,103 lines | Close — 2,114 | Immaterial |
| `plan_project` is pure | Yes | No filesystem, subprocess, clock or env read in the module |
| Seven registered stage kinds | Yes | `STAGE_REGISTRY`, `stages.py:78` |
| `storage <-> storage_sqlite` import cycle | Yes | Function-local `from . import storage_sqlite` inside `storage.py` |
| Self-host trove not lint-green: 6 warnings + 1 L7 error | Yes, exactly | The L7 is P42's `scope.forbid` naming a nonexistent `nyxloom-trove/STANDARD.md`. **Fixed in this amendment** — see below |
| `requires-python >=3.11` vs a 3.14-only gate | Yes | `pyproject.toml:5` |
| Dashboard escapes agent-authored text | Yes | 158 `html.escape` call sites; no unescaped interpolation found |
| **84 `except Exception` in `daemon.py`** | **No — 83, and the scope is too narrow** | See correction 1 |
| **"Make SQLite the single runtime store now"** | **Premise stale** | See correction 2 |

### Correction 1 — the fail-open audit is a `src/`-wide problem, not a `daemon.py` one

`daemon.py` has 83 `except Exception` sites. The tree has **144, spread over 15 modules** —
so 61 sit outside `daemon.py` — and roughly 30 are followed immediately by `pass`. (The one
`except BaseException`, in `wrapper.py`'s double-fork child, is legitimate.) Scoping RISK-004
to `daemon.py` would leave more than 40% of the category unaudited — including
`config.ProjectConfig.load` failures swallowed by a `continue` in `Daemon._chosen_http`,
which silently decides which port and bind the *entire* control plane listens on.

The rule the parent proposes (authoritative versus advisory, encoded in helper names and
types) is right. Its scope should be every module that contributes a fact to a dispatch,
merge, admission, or authority decision.

### Correction 2 — SQLite is already the live store; DR-09/CR-04 is code deletion, not a cutover

`nyxloomd/docker-compose.yml:83` sets `NYXLOOM_STATE_BACKEND: "sqlite"` and records the
cutover as **live since 2026-07-21** ("SP03 cutover LIVE"), with `events.jsonl` retired to
`.pre-sqlite` and backups under `~/.local/state/nyxloom-backups/`. The live state directory
confirms it: `~/.local/state/nyxloom/projects/nyxloom/` contains `state.db` alongside
`events.jsonl.pre-sqlite`.

Both parent documents describe this as work still to be done — the plan goes further and
requires "a separately approved operational runbook that stops the daemon, backs up/verifies
the database, and names rollback" before a "destructive live cutover" that has already
happened. This matters in both directions:

- It **lowers** the risk and cost of DR-09. What remains is deleting the file backend, the
  selector, and the dual-backend tests, plus adding schema/projection versioning and
  upcasters. That is a bounded refactor, not a data migration.
- It **raises** the priority of the parent's own RISK-003. A document set that cannot tell a
  reviewer which storage backend is live is exactly the failure RISK-003 describes, and the
  reviewer inherited the error. `README.md`'s headline — "files are the database" — has been
  false for twelve days.

### Correction 3 — one fail-open case is worse than "an appropriate optional-observability catch"

`storage._last_sequence` reads the trailing 64 KB of `events.jsonl` and parses the last line
to assign the next sequence number. An event whose serialized form exceeds that window makes
`json.loads` raise, and the append fails. It fails closed, so it is not a correctness hole —
but it is a latent availability cliff sitting under every event append on the file backend.
It is further evidence for the SQLite-only decision rather than an argument against it, and
it needs no fix if that backend is deleted.

## New risks

### RISK-005 [P0] — The control plane is unauthenticated, and the deployment is not loopback

`Daemon._start_http` serves a **mutating** API with no authentication, no authorization, and
no operator identity:

| Endpoint | Effect |
| --- | --- |
| `POST /api/config/policy` | Rewrite project policy (concurrency caps, budgets, targets) |
| `POST /api/config/pause` | Pause or resume a project — the emergency brake |
| `POST /api/config/tier` | Remap a tier to different routes |
| `POST /api/decision/reply` | **Answer a human decision** |
| `POST /api/intake` | Open or advance a feature-intake conversation |
| `POST /api/finding/promote` | Promote a finding into an intake |
| `POST /api/config/log-level` | Change the daemon's effective log level |

`nyxloomd/docker-compose.yml:94` sets `NYXLOOM_HTTP_BIND: "0.0.0.0"`, justified as "safe here
— a private ciu bridge network, never host-net". The daemon itself logs a warning at bind
time stating the assumption honestly. But several handler docstrings still assert
"Loopback-only, same as every other route on this server" — written before P38 moved the
bind and never revised. The code's own account of its security posture contradicts itself.

Why this is a P0 and not a hardening nice-to-have: the north star's central promise is *"the
human owns direction; the machine owns execution"* and *"silence is never consent: a stuck or
ambiguous call escalates to a human, it is never guessed."* `POST /api/decision/reply` is the
mechanism that promise runs on. Anything that can open a TCP connection to that port can
supply the human's answer. The invariant is not enforced by the system; it is enforced by
network topology maintained in a different layer, asserted in a comment.

Two concrete exposures follow:

1. **Any container joined to `nyxloomd-net`** holds full control-plane authority. The
   devcontainer is joined by design (that is how the dashboard is reachable from VS Code).
2. **Drive-by CSRF from a browser.** Before this amendment there was no `Content-Type` check
   and no `Origin` check, so a cross-site HTML form (`enctype="text/plain"`) could produce a
   body that `_read_json_body` parses, against any dashboard the operator's browser can
   reach.

Exposure 2 is fixed in this amendment (see *Direct improvements*). Exposure 1 requires real
authentication and is a package — see CR-15 in the companion plan.

Acceptance proof for the package: a request without a valid operator credential produces
zero config mutation, zero decision reply, and one audited refusal event; the credential is
bound to an operator identity that appears in the `Actor` of every resulting event (today
every UI write is attributed to the literal string `"ui"`, which is not an identity).

### RISK-006 [P0] — Dispatched agents inherit the daemon's full trust: environment, socket, and home

`wrapper.wrapper_main` spawned every agent CLI with `env = os.environ.copy()`. The daemon's
environment is loaded from `nyxloomd/.env` and `nyxloomd/secrets.env`, so each agent received
`AA_API_KEY`, `NTFY_TOKEN`, `NTFY_CMD_TOKEN`, `OPENROUTER_API_KEY` and `DEEPSEEK_API_KEY`.

The project already knows this is wrong. `nyxloomd/secrets.env.example` documents least
privilege per key and states that `AA_API_KEY` is "**NOT forwarded to any CLI**". The code
forwarded it. That file also names the intended remedy — "under the planned per-use
CLI-container model (D-R7 blast-radius isolation), the daemon injects only the relevant
subset into each container" — so this is a known, designed-for gap that has simply not been
scheduled.

The environment is only part of the trust the agent inherits. From the compose file, the
daemon container also holds:

- `/var/run/docker.sock` — host-root-equivalent, mounted so gates can run in containers;
- `/home/vb/mdt--mounted-folders:/home/vscode` — the operator's home, including the manual
  subscription credentials the Claude and codex CLIs authenticate with;
- `/home/vb/volkb79-2/vbpub` plus every registered project repository;
- network membership of `nyxloomd-net`, which is where the unauthenticated control plane of
  RISK-005 listens.

An agent CLI is a direct child of the daemon in that namespace. `routes.host.toml` already
defines `[tiers.implement-1-free]` with four free OpenRouter routes, and the guard applied to
them is a sentence injected into the prompt asking the model not to send secrets. The parent
review states the principle correctly — "prompt text is not a security boundary" — but files
the remedy as DR-14/CR-13 in Phase D, **after** CR-09 and CR-10 have made cheap and free
routes the normal path. The program's own value thesis increases the exposure before the
containment lands.

Amendment: split containment. Execution containment (per-use CLI container, no docker socket
in the agent's namespace, no operator home mount, per-route secret injection) is a
prerequisite of the cost/capability work, not a successor to it. Full resource and permission
policy (cgroups, wall-time, network egress) can remain in Phase D. The stopgap environment
strip is applied in this amendment.

### RISK-007 [P1] — The system has no liveness signal, and has been failing silently for ten days

Observed on the host on 2026-08-02:

```text
nyxloom-prod-nyxloomd    Exited (143) 10 days ago
nyxloom-ntfy             Restarting (1) 23 seconds ago
```

The daemon is stopped. The notification channel — the transport every escalation, decision
request and runaway alarm travels on — has been crash-looping. Nothing reported either.

The ntfy failure is worth stating in full because it is a compact case study in three of this
review's themes at once:

```text
if set, base-url must be a valid URL, e.g. https://ntfy.mydomain.com:
parse "https://{{ ntfy.public_host }}": invalid character "{" in host name
```

`ntfy/server.yml` was converted into a ciu Jinja template by the ntfy-fqdn work. The ciu
deploy path renders it. The plain-compose path — `ntfy/docker-compose.yml`, which is what
actually deployed the running container — binds the file verbatim, so ntfy received a literal
`{{ ... }}` as its base URL and refused to start. And `ntfy/ntfy-fqdn-REPORT.md`'s
"Forbidden-file confirmation" section records exactly why the implementer did not fix it:

> `nyxloom/ntfy/docker-compose.yml`: read-only inspection, not edited (per forbid list).

So: a carve-scope error put the one file that needed the change out of bounds, the implementer
correctly obeyed the contract, the change shipped half-applied, and the resulting outage in
the escalation channel went unnoticed for over a week. `LESSONS.md` already records "I twice
forbade the file the correct implementation needed" as a recurring authoring error; this is
its third occurrence, and the first with a production consequence.

The detection gap is separate from the carve gap and is the more important of the two.
`watchdog.detect_runaways` covers three patterns — `notification-storm`, `reconcile-thrash`,
`attempt-loop`. Every one of them detects *too much activity*. Nothing detects **absence**:

- no heartbeat or deadman for "no reconcile pass completed in N minutes";
- no health check on the notification transport itself, so a dead alarm channel is
  indistinguishable from a quiet system — the alarm cannot report its own failure;
- `TICK_ERROR` is appended and rendered red, but repeated `TICK_ERROR` is not an escalation
  trigger. `Daemon.run_pass` wraps the whole pass in `try/except`, appends `TICK_ERROR`, and
  returns 0. A systematic fault therefore produces a daemon that is *up*, *healthy* by its
  TCP healthcheck, and doing nothing, indefinitely.

For a system whose entire proposition is unattended operation, "fails silently" is the one
failure mode that must not exist. Recommendation: a deadman event plus an out-of-band
liveness signal, transport health as a first-class probe with its own escape path (the
dashboard and an on-disk status file, not only ntfy), and `TICK_ERROR` streaks added to the
watchdog's pattern set. Sized as CR-16 in the companion plan.

### RISK-008 [P1] — The projection is a caller-owned dictionary, and there are two concurrent writers

`storage.append_and_apply(project, states, ...)` takes an **in-memory `dict` owned by the
caller**, appends the event, and applies the projection to that dict. Two independent writers
use it against the same project:

- the reconcile loop, which loads `states = storage.list_states(project)` once at the top of
  `run_pass` and holds it across the whole pass;
- the HTTP handler threads, via `_append_ui_event`, which load their own view.

The event log is safe: appends take an exclusive `flock` and the sequence is assigned under
it. The **projection** is not. A UI write during a reconcile pass updates the statefile on
disk; the pass then writes its own version of that task's statefile from a snapshot taken
before the UI write, and the UI write's projection is lost. Replay heals it, because events
are authoritative — but until a replay, the projection and the log disagree, and every
consumer reads the projection.

This has consequences beyond today's races, which is why it belongs in the redesign rather
than the bug list. The companion plan's §4.9 promises "event append and projection update in
one transaction" and "one logical daemon writer". Neither is achievable while the projection
is computed from a caller-supplied dictionary: a transaction that writes state derived from a
stale in-process snapshot is atomic and wrong. The store must derive the projection from
committed state **inside** the transaction, and `append_and_apply`'s signature must lose the
`states` parameter. That is a small API change with a wide blast radius, and it needs to land
with CR-04 rather than be discovered during CR-05.

### RISK-009 [P2] — Terminal-task handoff resolution is already silently broken

The live state database records `nyxloom-P23-init-command`'s `handoff_path` as
`nyxloom-trove/handoffs/P23-init-command.md`. The file has been named
`nyxloom-P23-init-command.md` since a rename that predates this review. The path has been
dangling and nothing has reported it.

This is minor operationally — every affected task is terminal — but it undermines an
invariant the redesign depends on: the parent's DR-13 and the plan's CR-03 bind results to
artifacts, and CR-12 binds product criteria to evidence. Both assume a stored path resolves
or the discrepancy surfaces. Here it neither resolves nor surfaces. Any evidence-binding
package should include a mechanical check that every stored artifact reference resolves, with
stale references reported rather than absorbed.

### RISK-010 [P1] — The most-inherited contract in the project contradicts reality three ways

`nyxloom-trove/STANDING.md` opens with "Inherited by EVERY handoff in this directory. Read
once, follow exactly." Every carved package receives it. As found, it contained three false
statements, each of which degrades every future handoff at the source:

1. **"Today's date: 2026-07-15 (use it; wrong dates are review-rejected)."** A date pinned in
   an inherited contract is wrong by construction, and this one had been mandating a date 18
   days stale — under threat of review rejection for using the correct one.
2. **"Python: `/workspaces/vbpub/.venv/bin/python` (3.13 ...)"** That path does not exist. The
   interpreter is 3.14.6 elsewhere. Every handoff's gate command was therefore uncopyable, and
   the reports in `reports/` show the command being pasted forward for months.
3. **"Gate (the only accepted evidence): `... .venv/bin/python -m pytest ... -q`"** This
   contradicts `nyxloom-trove/nyxloom.toml`'s `[gates.tester-unified]` — the real gate, which
   runs in a container with `-n auto`, coverage, and a changed-line floor — and it contradicts
   the project's own rule, stated in the companion plan's §3, that "cockpit tests are
   diagnostic only and never release evidence". The standing contract was instructing every
   implementer to submit exactly the evidence the project does not accept.

This is RISK-003 in its most consequential form. A stale `[refs]` document wastes a limited
model's context; a stale *standing contract* silently lowers the evidence bar on every package
the factory produces. It is also self-concealing: an implementer who follows the contract
exactly produces a REPORT that looks compliant.

Corrected in this amendment (see *Direct improvements*, item 5). The durable fix is the
standing lint rule proposed under RISK-003 below, extended to cover the contract files
themselves — the declared interpreter and the declared gate are machine-checkable facts.

## Amendments to the parent's existing findings

### RISK-001 — answer the cost-amplification question the parent asks but does not resolve

The parent's own external-review checklist asks: *"does immediate promotion create an
abuse/cost-amplification path?"* The recommendation as written does not answer it, and the
plan inherits the gap. Under "an explicit capability decline immediately promotes the
unchanged task to the next band and excludes the declining route", a route that declines
liberally — from miscalibration, not malice — silently converts the whole cheap tier into a
toll booth on the way to expensive bands. Every task pays a band-1 call and lands at band 2+
anyway, which is strictly worse than not having band 1.

Three guards make immediate promotion safe, and all three should be part of the requirement
rather than left to implementation:

1. **Decline validity.** A decline is honoured only when it names a mechanical unmet condition
   from a closed vocabulary and is corroborated by an observable fact the wrapper establishes
   (empty diff, no tool invocations, context measurably over the packet envelope). An
   uncorroborated decline is recorded as evidence and treated as an ordinary failure.
2. **Per-route decline rate as a routing input.** Excluding the route for *this task* is not
   enough. A route whose decline rate for a task archetype exceeds a threshold stops being
   selected for that archetype at all — the learning loop's first and cheapest job.
3. **A per-task decline cap** that terminates in a typed human decision, so a pathological
   interaction between a task and a band cannot walk the whole ladder on repeat.

Without these, "cheap-first" has no floor and the objective in §4.8 optimises a cost model
whose inputs it cannot trust.

### RISK-003 — make document truth a standing gate, not a one-time cleanup

The parent recommends a lifecycle (`current|superseded|historical`), archiving, and
contradiction checks over a small set of machine-known facts. Correct, with one structural
amendment: **the contradiction check must be a lint rule that runs in the gate on every
package**, not a task completed once by CR-01.

The evidence is in this very review chain. The declared facts drifted, a reviewer read the
stale documents, and the error propagated into two planning documents and nearly into an
implementation program. A cleanup performed once will drift again; CR-04 will itself
invalidate `README.md`'s headline claim the moment it deletes the file backend. Only a
mechanical check that runs continuously survives its own program.

### Storage recommendation — endorsed, with the premise corrected

SQLite-only is the right call, and correction 2 makes it cheaper than the parent assumed. Two
additions:

- The projection API must change with it (RISK-008). Atomicity is a property of the whole
  read-modify-write, not of the write alone.
- "Retain an append-only JSONL export for greppability and disaster recovery" should be
  strengthened to a *tested* export: an export that has never been re-imported and replayed
  to an identical state is a backup nobody has restored.

### Monolith metrics — one addition

The parent counts lines and functions. The number that predicts change cost better is that
`Daemon` is a **single class with 155 methods** sharing mutable instance state
(`_gate_verify_running`, `_post_merge_gate_running`, `_httpd`, `registry`). Splitting by
concern is therefore not only a file-size exercise; it requires deciding what state each
effector owns. CR-05's acceptance should say so explicitly, or the split will produce six
modules that all reach back into one god object.

### Test strategy — name the retirement policy

The parent observes that `test_daemon.py` is over 7,000 lines and mirrors implementation
structure. It does not follow that observation to its consequence for the program: **a
rewrite of `daemon.py` invalidates most of that file**, and nothing in the plan owns
retiring it. Under the plan as written, every core package will fight false reds from tests
that assert the shape of code being deliberately replaced, and the cheapest way through that
is to weaken tests under deadline pressure — the exact failure this project's doctrine exists
to prevent. Sized as an explicit obligation in the companion plan.

## Considered and not adopted

**Reordering the program to deliver escalation before the structural redesign.** I put this
to the operator: CR-09 is the package that unlocks the stated goal, and it sits behind three
serial rewrites of the same two files. A hybrid — typed results (CR-03) plus a pure
capability-policy module, with the ladder implemented against the existing planner, then the
structural work — would deliver the cost win considerably earlier and give the risky refactor
a richer behavioural baseline to preserve.

**The operator held decision #13 as written: core redesign first.** The reasoning against my
proposal is sound — adding a second escalation implementation to the monolith is precisely
how this codebase reached 8,308 lines, and a policy module called from planner branches is
still a planner branch. Recorded here so it is not re-litigated.

Two consequences follow from holding it, and both are amendments rather than objections:

1. **CR-00 must characterize behaviour that does not exist yet.** If escalation is built last,
   the characterization corpus captures a system without it, and CR-07's compiler will be
   shaped by the flows it can see. The corpus needs executable acceptance fixtures for the
   five-band ladder written *up front*, red until CR-09 turns them green — so the workflow
   model is designed against the behaviour it must eventually host.
2. **A stop-loss becomes essential.** Under strict core-first ordering the program delivers no
   operator-visible value until CR-09. That is defensible only with explicit criteria for
   when to stop, de-scope, or fall back. Specified in the companion plan.

## Direct improvements made in this amendment

All four were approved by the operator before being applied. Evidence follows in the next
section.

1. **Restored the notification channel** (RISK-007). `ntfy/docker-compose.yml` now supplies
   `NTFY_BASE_URL` in the environment, which takes precedence over the config file, making
   the plain-compose path independent of the ciu render step. Verified empirically that ntfy
   accepts the override with the unrendered template still mounted. Container recreated and
   healthy. The value matches `ntfy/ciu.toml` `public_host` and `nyxloomd/.env` `NTFY_URL`.
   *Follow-up, not done here: consolidating on one deploy path. Two paths that render
   configuration differently is the defect class, and it will recur.*
2. **Stopped daemon-only secrets reaching agent CLIs** (RISK-006). `wrapper.child_env()`
   strips `AA_API_KEY`, `NTFY_TOKEN` and `NTFY_CMD_TOKEN` from the child environment;
   `spec.env_overrides` is applied afterwards so a dispatch can still pass one deliberately.
   Provider keys the CLIs genuinely need are untouched. The frozen wrapper contract docstring
   was updated to match. **This reduces the blast radius; it is not containment** — the agent
   still shares the daemon's namespace, mounts and docker socket.
3. **Closed the drive-by CSRF path on the control plane** (RISK-005). Mutating POSTs now
   require `Content-Type: application/json` (a cross-site form cannot produce it) and reject a
   cross-origin `Origin` when one is present (absent `Origin` is not evidence of cross-site,
   so curl and the CLI are unaffected). Every dashboard `fetch()` already sends the header, so
   nothing legitimate changes. Four comments in `daemon.py` asserting "loopback-only" — the
   module docstring, the `_CONFIG_POST_PATHS` comment, and two handler docstrings — were
   corrected to state the real posture. **This is CSRF hardening, not authentication.**
5. **Corrected `nyxloom-trove/STANDING.md`** (RISK-010). The pinned date is replaced with an
   instruction to use the actual date, with the reason stated so it is not re-pinned; the
   interpreter line is corrected and marked diagnostic; and "the only accepted evidence" now
   names `[gates.tester-unified]` from `nyxloom.toml`, including the note that the coverage
   phase fail-closes on uncommitted work so an implementer does not mistake it for a red.
   *This changes what agents treat as proof, so it is worth being explicit: it is a correction
   toward authority that already existed in `nyxloom.toml` and in the plan's own §3, not a new
   policy.*
4. **Made the self-host trove lint-green.** All 20 handoffs in `nyxloom-trove/handoffs/` were
   moved to the configured `nyxloom-trove/archive/`, which `nyxloom.toml` already designates
   as where "merged handoffs+reports land". Every one of the 20 was verified terminal in the
   live state database first (17 COMPLETED, 1 CANCELLED, and the archive already held
   CANCELLED precedents in P44/P45). This clears the blocking L7 error and all 6 warnings.

## Verification evidence

- **Real project gate** (`[gates.tester-unified]`, the only accepted evidence per
  `STANDING.md`), run in the `tester-unified:local` container over the working tree, with
  `{worktree}` = `/workspaces/vbpub` and without `--cgroup-parent` (the
  `nyxloom-gates.slice` unit is host-side and not present in this cockpit):

  ```text
  ................................................... [100%]      <- pytest phase, 1 xfail, 0 F/E
  ================================ tests coverage ================================
  Coverage JSON written to file /tmp/nyxloom-cov.json
  diff-coverage NO MEASUREMENT: 2 uncommitted file(s) under src/nyxloom -- the gate
  diffs committed HEAD, so these are invisible. Commit, then re-run.
  Affected: src/nyxloom/daemon.py, src/nyxloom/wrapper.py
  GATE_EXIT=3
  ```

  Read honestly: **the pytest phase is green** — the shell chain is `pytest && coverage_gate`,
  so the second phase running at all proves the first exited 0. The **changed-line coverage
  phase did not run its measurement**, because the gate diffs committed `HEAD` and these
  changes are deliberately uncommitted. That is the gate fail-closing exactly as designed, not
  a coverage failure. The changed lines are covered by the ten new tests, but that claim is
  unproven until the changes are committed and the gate re-run — which is the operator's call.
- Cockpit `pytest tests/ -q` before any edit: exit 0 (baseline).
- New regression tests: 3 in `tests/test_wrapper.py`
  (`TestDaemonOnlySecretsAreNotInherited`) asserting the environment the CLI *actually
  receives* — the fake CLI dumps its own `environ` and the assertions read it back, so the
  oracle is the child process, not the helper's return value; and 7 in
  `tests/test_config_ui.py` asserting that every refused POST leaves `project.toml`
  byte-identical and appends no `CONFIG_CHANGED` event, plus two positive cases proving the
  same-origin browser path and the header-less CLI path still mutate.
- Lint: `lint_project` over the nyxloom trove returns **0 findings** (was 6 warnings + 1
  error).
- ntfy: `docker logs` shows `Listening on :8080[http], ntfy 2.14.0`; `/v1/health` returns
  `{"healthy":true}`; container status `Up (healthy)`.
- Nothing was committed. All changes are in the working tree for review.

## Change log

| # | Change | Reason | Parent section |
| --- | --- | --- | --- |
| 1 | Corrected the fail-open audit scope from `daemon.py` (83, not 84) to `src/`-wide (144 over 15 modules, ~30 `pass`-swallowed) | Scoping to one file leaves >40% of the category unaudited, including the config load that decides the control-plane bind | RISK-004 |
| 2 | Corrected the storage premise: SQLite has been the live backend since 2026-07-21 | Both documents plan a cutover that already happened; DR-09 is code deletion, and the error is itself an instance of RISK-003 | Storage and event sourcing; RISK-003 |
| 3 | Noted `_last_sequence`'s 64 KB window as further support for SQLite-only | Latent availability cliff under every file-backend append; needs no fix if the backend is deleted | Storage and event sourcing |
| 4 | Added RISK-005 — unauthenticated mutating control plane on a non-loopback bind | `POST /api/decision/reply` is the mechanism the "human owns direction" invariant runs on; it is enforced by network topology asserted in a comment, and the code's own docstrings contradict the deployment | New |
| 5 | Added RISK-006 — agents inherit the daemon's full environment, docker socket, and operator home | `secrets.env.example` documents the intended least privilege; the code contradicts it, and free routes are already a configured tier | New; amends DR-14 |
| 6 | Added RISK-007 — no liveness or channel-health signal; daemon down 10 days, ntfy crash-looping, nothing reported | The watchdog detects only excess activity; "fails silently" is the one failure mode an unattended control plane must not have | New |
| 7 | Added RISK-008 — projection is a caller-owned dict with two concurrent writers | Lost projection updates today; the plan's "atomic event + projection" promise is unreachable without changing `append_and_apply`'s signature | New; amends §4.9 of the plan |
| 8 | Added RISK-009 — a stored `handoff_path` has been dangling silently | Evidence-binding packages assume stored references resolve or the discrepancy surfaces; here it does neither | New |
| 8b | Added RISK-010 — `STANDING.md`, inherited by every handoff, pinned a stale date, named a nonexistent interpreter, and declared cockpit pytest "the only accepted evidence" against `nyxloom.toml` | A stale ref wastes context; a stale standing contract lowers the evidence bar on every package the factory produces, and does so invisibly | New; sharpest instance of RISK-003 |
| 9 | Amended RISK-001 with three decline guards (validity corroboration, per-route decline rate, per-task cap) | The parent's own review checklist asks whether immediate promotion amplifies cost and does not answer it; without a floor, band 1 becomes a toll booth | RISK-001 |
| 10 | Amended RISK-003: contradiction checks become a standing lint rule, not a one-time cleanup | The drift this review is correcting happened once already and propagated into two planning documents; CR-04 will re-invalidate the README | RISK-003 |
| 11 | Amended the monolith finding: `Daemon` is one class with 155 methods over shared mutable state | Predicts change cost better than line count, and dictates that CR-05 must assign state ownership or produce six modules reaching into one god object | Monolith metrics |
| 12 | Added an explicit test-retirement obligation | A `daemon.py` rewrite invalidates most of a 7,000-line implementation-mirroring suite; unowned, it becomes deadline pressure to weaken tests | Test and gate review |
| 13 | Recorded the sequencing proposal as considered and rejected, with two consequences of holding decision #13 | The operator held core-first; the reasoning is sound and should not be re-litigated, but CR-00 must pre-write the escalation fixtures and the program needs a stop-loss | Recommended delivery order |
| 14 | Recorded four applied fixes with evidence | Approved low-hanging items; each is a stopgap or hygiene fix, and each names what it does *not* solve | Direct improvements made in this review |
