# B24 — transient-failure backoff-resume (D-R17) — implementation log

Worktree: `/workspaces/vbpub/.worktrees/nyxloom-B24-backoff-resume/nyxloom`
Branch: `feat/nyxloom-B24-backoff-resume` (based on main `1e13e8ac`)

## Context read (in order, per handoff)

1. `docs/routing-model-redesign.md:385-418` (D-R17). Confirmed the two
   documented stale anchors: the resume executor is `daemon.py:3243`
   (`elif isinstance(action, reconcile.ResumeAttempt):`), and the
   no-healthy-route check is `reconcile.py:1554` (`_check_healthy_route`).
2. `src/nyxloom/adapters.py:769-817` (`classify_log_tail`) — confirmed the
   docstring's "error prefix / contains 'error'/'exceeded'/HTTP 429"
   heuristic does not exist anywhere in the code (no `ERROR_PREFIXES`
   constant); the only real gate is the `limit_phrase` regex over
   `tail_lim`. Corrected the docstring rather than implementing the phantom
   heuristic.
3. `src/nyxloom/wrapper.py:1-70` (module docstring / INTERFACE CONTRACT),
   `:396-500` (tail read, classification chain, WARNING/DEBUG log bucket,
   `Receipt(...)` construction, event append).
4. `src/nyxloom/daemon.py`: constants block `:358-392`, `_resume_failures`
   `:1156-1185` (skips any attempt whose `receipt.json` exists — confirmed
   this is the trap D-B24-5 names: a transient attempt always has one),
   `_provider_ok` `:1066-1074`, `_provider_pause` `:1694-1703`, snapshot
   build (`_build_input`) `:860-955`, `run_pass` `:764-800` (confirmed
   `_reconcile_decisions` runs BEFORE `_build_input`/`plan_project`, mutating
   `states` in place — the precedent my `_transient_escalate` mirrors),
   `ResumeAttempt` executor `:3243-3300` (`_next_resume_n` `:3133`, stale
   receipt archived to `receipt.pre-resume-{n}.json` before relaunch —
   confirmed I never touch this path), receipt fan-out (`EmitAttemptExit`)
   `:3581-3658` with the LIMIT branch verbatim at `:3643-3645`.
5. `src/nyxloom/reconcile.py`: `ResumeAttempt` planner gate `:1113-1168`
   (exact line for the "ONE extra conjunct": the `if attempts_used(tsf) <
   ... and attempt.session_handle:` line, confirmed at `reconcile.py:1130`
   before edits), `ReconcileInput.resume_failures` `:552-559`, first-healthy
   selection loop `:966-975` (`routes = inp.routes.for_tier(...)`, filtered
   only by `inp.provider_ok`), `_check_healthy_route` `:1554-1559`,
   `attempts_used` `:1676-1699`.
6. `src/nyxloom/types.py:124-141` (`TERMINAL_ATTEMPT_STATES`,
   `ATTEMPT_TRANSITIONS` — confirmed `INTERRUPTED -> {RUNNING, ABANDONED}`
   only, `EXITED -> frozenset()`), `:166-178` (`ReceiptResult`, additive
   site).
7. `src/nyxloom/storage.py:99-107` (`_attempt_regression` — confirmed
   `INTERRUPTED -> RUNNING` is explicitly whitelisted as not-a-regression),
   `:351-355`/`apply_event`'s `ATTEMPT_*` branch (confirmed an event for a
   terminal attempt is silently dropped by the regression guard — the
   reason D-B24-1 forbids `ATTEMPT_EXITED`/`EXITED` for a transient
   classification). READ ONLY — not edited (frozen core).
8. `src/nyxloom/config.py` — READ ONLY (frozen core): confirmed no
   `Policy` field is added; `TRANSIENT_BACKOFF_SCHEDULE`/
   `MAX_TRANSIENT_RESUMES` are `daemon.py` module constants instead.
9. Test-side: `tests/test_daemon.py` (`_seed_running_attempt`,
   `_write_receipt`, `_scripted`, and the direct-private-method-call
   convention already used for `_attempt_scan`/`_parse_review_verdict`/
   `_build_carve_packet` — confirmed direct calls to a new `Daemon` helper
   are an established pattern, used here for `_transient_backoff_ready`/
   `_transient_escalate`); `test_daemon.py:1024-1047`
   (`test_emit_attempt_exit_limit`, the O5 model: two `run_pass` calls via
   `_scripted`, asserting `PROVIDER_STATE_CHANGED`/`NEEDS_OPERATOR` then
   `captured[1].provider_ok`); `tests/test_reconcile.py` (`make_tsf`/
   `make_attempt` helpers, the existing INTERRUPTED-branch oracle tests
   around `test_receipt_interrupted_with_session_handle_resume` and the P34
   poisoned-attempt section); `tests/test_behavioral.py:1-300` (fixtures,
   `FakeStep`/`FakeScript`, `_tick`/`_wait`), `:431-517` (the scope-amendment
   O1/O3 tests — modeled the O4 test's STRUCTURE on these, not their
   content); `src/nyxloom/testing.py` (full file, read-only — confirmed
   `fake_cli_main` only needs `--role`/`--task` and `capture_session` for a
   bare `cli="fake"` route with no `session_capture`/`session_discover` set
   always returns `None`, so the O4 behavioral test manually seeds
   `session_handle`, same as `test_mark_interrupted_and_resume`
   (test_daemon.py) does for its own resume test).

## Decisions made / traps confirmed (resolving forks the recon didn't pin exactly)

- **D-B24-1 (the critical seam) confirmed exactly as specified.** Added a
  `classification == "transient"` branch in `wrapper.py` setting
  `event_type = EventType.ATTEMPT_INTERRUPTED`, `attempt_state =
  AttemptState.INTERRUPTED` — the SAME pair the pre-existing
  `if interrupted:` (signal) branch uses. Did **not** touch `daemon.py`'s
  `EmitAttemptExit` fan-out at all: traced through
  `reconcile.py`'s attempt-actions loop (`:980-1050`) and confirmed
  `EmitAttemptExit` is only ever planned for `attempt.state in
  _INTERRUPTIBLE_STATES = (RUNNING, PREFLIGHTING, STALLED)` (wrapper died
  pre-event) or an already-`EXITED` attempt — never for `INTERRUPTED`. A
  transient-classified attempt therefore structurally never reaches that
  fan-out; the existing `ResumeAttempt` gate is what evaluates it.
- **D-B24-2 (backoff readiness).** Added `Daemon._transient_backoff_ready`
  beside `_resume_failures`, deriving readiness from
  `_next_resume_n(attempt_dir) - 1` (prior resume count) and the newest
  `attempt.resume-N.log`'s mtime, per `TRANSIENT_BACKOFF_SCHEDULE`. Passed
  into `ReconcileInput.transient_backoff_ready` (new `field(default_factory
  =dict)`, defaulted so no existing test needed updating). **Extended the
  handoff's exact instruction ("add ONE extra conjunct to the ResumeAttempt
  gate at reconcile.py:1130") to TWO sites**, both reading the SAME
  `ready = inp.transient_backoff_ready.get(attempt.attempt_id, True)` value:
  the `ResumeAttempt` line itself, AND the `elif tsf.state ==
  TaskState.ACTIVE:` dead-end-BLOCKED fallback immediately below it. This
  was necessary, not optional: gating only the resume line would let a
  merely-still-backing-off attempt (ready=False, session_handle present,
  budget available) fall straight into the "no resume handle or attempts
  exhausted" BLOCKED branch on the very next pass — misreporting a
  transient wait as a genuine dead end, directly contradicting O3/O4 ("the
  task never BLOCKED"). Both branches now park (do nothing) while
  `ready=False`, whether "still backing off" or "permanently capped" (see
  next point).
- **D-B24-5 (bounding) — resolved where the "existing selector" the handoff
  cites (`reconcile.py:966-975`) actually lives.** That is the ORDINARY
  QUEUED-task dispatch loop, not the `poisoned`/`fresh_start_eligible` loop
  further down (`:1163+`), which is a DIFFERENT, though structurally
  similar, first-healthy-route selector. This means escalation must move
  the task through `ACTIVE -> QUEUED` (the ordinary path), not through the
  `poisoned` branch's fresh-start machinery. Implemented `Daemon.
  _transient_escalate`: once `_next_resume_n(attempt_dir) - 1 >=
  MAX_TRANSIENT_RESUMES`, transitions the task `ACTIVE -> QUEUED` (the
  SAME legal edge the LIMIT branch in `EmitAttemptExit` already uses) and
  calls `self._provider_pause(..., state="throttled")`. Once capped,
  `_transient_backoff_ready` returns `False` FOREVER for that attempt_id
  (not just "not yet"), so the ordinary `ResumeAttempt` gate never revives
  it again — it is left permanently INTERRUPTED, inert, the SAME "stale
  record" shape a resume-poisoned attempt already has today (this is why
  `implementer_record_count` exists as a SEPARATE counter from
  `attempts_used` — confirmed by reading both).
  `_transient_escalate` is called in `run_pass`, BEFORE `_build_input`/
  `plan_project` (mirroring `_reconcile_decisions`'s exact early-mutation
  timing) — this is the "daemon-side work" the handoff's D-B24-1 closing
  line refers to (the backoff gate + the at-cap escalation), distinct from
  the reconcile.py planner, which stays otherwise unmodified.
- **D-B24-3 (no new EventType) confirmed as specified.** Added only
  `ReceiptResult.TRANSIENT`. Extended `Daemon._provider_pause` with an
  optional `state: str = "limited"` parameter (backward compatible — every
  pre-B24 call site is byte-identical) so `_transient_escalate` can pass
  `state="throttled"`, giving the escalation a distinct
  `PROVIDER_STATE_CHANGED` payload from a genuine LIMIT pause without a new
  EventType. No `tests/test_invariants.py` change was needed (confirmed:
  `PROVIDER_STATE_CHANGED` is already in `KNOWN_IGNORED_EVENT_TYPES`).
- **D-B24-4 (module constants) confirmed as specified.**
  `TRANSIENT_BACKOFF_SCHEDULE = (60, 300, 900)` and `MAX_TRANSIENT_RESUMES
  = 3` added to `daemon.py` beside `MAX_SCOPE_AMENDMENTS_PER_TASK`.
- **D-B24-6 (budget exclusion) confirmed and implemented, with a caveat
  documented in the accessor's own docstring:** added `ReceiptResult.
  TRANSIENT` to `attempts_used`'s exclusion tuple (alongside `LIMIT`).
  Traced carefully whether this is actually reachable given my own
  `_transient_escalate` design (which leaves the capped attempt
  permanently INTERRUPTED, never terminal) — under steady-state B24
  behavior, `attempts_used`'s OWN `a.state in TERMINAL_ATTEMPT_STATES`
  guard already excludes a still-INTERRUPTED transient attempt regardless
  of the receipt-result filter. The exclusion is still added exactly as
  instructed (cheap, correct, mirrors LIMIT's identical treatment, and
  guards any future/edge path that DOES mark a transient-receipted attempt
  terminal) — documented as such rather than silently implying it is
  load-bearing against a scenario this implementation cannot itself
  produce. `test_attempts_used_excludes_transient` covers the accessor's
  contract directly (mirrors the existing LIMIT test's exact shape).
- **Transient classification regex scoped to avoid a real regression.**
  The handoff's O1 lists "HTTP 429" as one of the five transient
  signatures. The EXISTING `classify_log_tail` tests (`tests/test_
  adapters.py::test_classify_log_tail_limit_variants` and the inline
  `"429 too many requests"` case a few lines below) already assert "429
  too many requests" -> `"limit"`. Rather than reclassifying that existing
  phrasing (which the handoff's no-regression list does NOT ask for — it
  only names BLOCKED/scope_amendment/"genuine session/usage limit"/domain
  prose as must-not-regress), `TRANSIENT_PHRASE`'s HTTP-429 alternative is
  deliberately narrower (`http ... 429` / `error code: 429`), so it does
  NOT overlap "too many requests" wording. Both existing tests remain
  green untouched; my own `test_classify_log_tail_transient_variants` uses
  a distinct HTTP-429 signature (`anthropic.APIStatusError: Error code:
  429 ...`) for its own coverage. The ONE deliberate overlap the handoff
  explicitly calls out — "Worker local total request limit reached" also
  matching `limit_phrase`'s bare "limit reached" alternative — IS
  exercised (`test_classify_log_tail_transient_ordering_beats_limit`) and
  resolved by ordering (transient checked before limit).

## Files touched

- `src/nyxloom/types.py` — `ReceiptResult.TRANSIENT` (additive).
- `src/nyxloom/adapters.py` — `TRANSIENT_PHRASE` constant +
  `classify_log_tail`'s new transient branch + corrected docstring.
- `src/nyxloom/wrapper.py` — new `classification == "transient"` branch;
  module docstring update.
- `src/nyxloom/daemon.py` — `TRANSIENT_BACKOFF_SCHEDULE`,
  `MAX_TRANSIENT_RESUMES` constants; `_transient_backoff_ready`,
  `_transient_escalate` new methods; `_provider_pause` gains `state=`
  param; `_build_input` wires `transient_backoff_ready`; `run_pass` calls
  `_transient_escalate` early.
- `src/nyxloom/reconcile.py` — `ReconcileInput.transient_backoff_ready`
  field; `ready` conjunct on both `ResumeAttempt`/BLOCKED branches;
  `attempts_used` excludes `TRANSIENT`.
- `tests/test_adapters.py`, `tests/test_wrapper.py`,
  `tests/test_reconcile.py`, `tests/test_daemon.py`,
  `tests/test_behavioral.py` — new tests (see REPORT for the
  oracle -> test mapping).
- `docs/handoff/routing/B24-LOG.md` (this file) / `B24-REPORT.md`.

Did NOT touch `config.py`, `storage.py`, `types.py`'s
`ATTEMPT_TRANSITIONS`/`TASK_TRANSITIONS`/`TERMINAL_ATTEMPT_STATES`,
`coverage_gate.py`, or `tests/test_invariants.py` — all as anticipated by
the handoff's framing that scope.touch was designed so none of these would
be needed.

## Verification run in this worktree (NOT the authoritative gate)

`python -m py_compile` on all 5 changed source modules and all 5 changed
test files: clean. Full local `pytest` runs (this devcontainer happens to
have the app's dependency closure available, unlike the documented
cockpit-vs-gating split) for `tests/test_adapters.py`,
`tests/test_wrapper.py`, `tests/test_reconcile.py`, `tests/test_daemon.py`,
`tests/test_behavioral.py` individually: all green, no regressions. Then a
final `pytest tests/ -q` (the WHOLE suite, every file, background run):
**all green, exit code 0** (one pre-existing `x` xfail, unrelated). **None
of this is the authoritative gate** — the real gate is the Docker
`tester-unified` run under a 100% diff-coverage floor, which the controller
runs separately; this agent did not and cannot run it.

## 2026-07-23 (later) — controller-reported gate flake: O4 test never sees INTERRUPTED

The controller ran the authoritative `tester-unified` gate: **everything
passed except O4** (`test_transient_throttle_resumes_same_attempt_end_to_end`).
All six D-B24 decisions were independently re-verified correct in the
actual code (not just docstrings). Symptom from the controller's captured
logs: the task reaches CARVED→QUEUED→dispatch→ACTIVE, then the daemon
repeats `pass-summary actions=1 events=0` forever — no receipt, no
`ATTEMPT_INTERRUPTED` ever. Raising both wait budgets (30→120, 20→60) did
NOT fix it — ruled out as "just needs more time." Reliable in this
devcontainer, unreliable (3/4 failing) under `tester-unified` for the
identical test.

**Investigation (this agent, this session):**
- Re-derived `classify_log_tail`'s correctness independently (already
  confirmed by the controller) — not the cause.
- Confirmed `route.resume` (the field my routes.toml override adds) is read
  ONLY by `adapters.build_resume` — nothing in the dispatch path (`build_
  dispatch`, `_ensure_worktree`, `Routes.load`) branches on its presence, so
  merely adding it cannot affect the FIRST leg (before any resume). Ruled
  out.
- Confirmed the FIRST scripted step (`FakeStep(extra_stdout=...,
  exit_code=1)`) has NO `commit_files`, so `testing._run_step` never calls
  `git commit` for it — a GPG-signing-hang theory (a real risk for the
  SECOND, commit-carrying step) does not apply to the leg that actually
  fails. Ruled out for this specific symptom.
- Reproduced the CONTROLLER's exact gate invocation locally as closely as
  this devcontainer allows: `coverage run --source=src/nyxloom -m pytest
  tests/test_behavioral.py -k transient_throttle -q` (read straight out of
  `coverage_gate.py`'s own docstring — the ACTUAL command `tester-unified`
  runs), repeated ~12 times (bare pytest, `coverage run`, and the exact
  `--source=` invocation) plus a full-suite run: **100% green every time in
  this devcontainer** — could NOT reproduce the flake locally despite
  matching the gate's own command. This is itself informative: whatever is
  wrong is specific to the `tester-unified` CONTAINER/dependency-version
  combination, not the test's logic in the abstract.
- Read `tester-unified/Dockerfile`: confirms a SEPARATE venv
  (`/opt/tester-venv`) built by `pip install .../nyxloom[test]` against
  nyxloom's own loosely-pinned `pyproject.toml` (`pytest>=8`,
  `hypothesis>=6`, `coverage[toml]>=7`) — meaning `tester-unified`'s
  installed `pytest`/`coverage`/`hypothesis` versions can legitimately
  differ from this devcontainer's, even though the Python runtime itself
  (3.14) is the same in both. The Dockerfile's OWN comment: **"The one test
  that relied on the pre-3.14 multiprocessing 'fork' default is now
  explicitly fork-scoped, so 3.14 is green"** — i.e. this exact codebase has
  ALREADY hit one fork-related Python-3.14 fragility before. `wrapper.py`'s
  `launch_detached` uses a raw double `os.fork()` (not `multiprocessing`) to
  detach the wrapper process, called from inside the SAME pytest process a
  `coverage run` trace is active in (per `coverage_gate.py`'s own
  documented invocation) — `os.fork()` under active line-tracing/coverage
  instrumentation, combined with a `pytest`/`coverage` version this
  devcontainer does not have installed, is the strongest candidate theory
  for a container-specific, non-deterministic (the controller saw 1-pass-
  in-4 too) hang. I could NOT prove this without the actual `tester-unified`
  image, and it is a PRE-EXISTING characteristic of `launch_detached`
  (shared by every other `test_behavioral.py` test), not something this
  package introduced.
- Also flagged (not fixed, unproven): `daemon._pid_alive` (`daemon.py:600`)
  is a raw `os.kill(pid, 0)` check with no pid-reuse guard (no `/proc/PID`
  start-time/comm verification). Under a busier gate container running many
  subprocess-heavy tests in one process, this is a THEORETICAL pid-reuse
  blind spot that could make a dead wrapper look permanently "alive" — but
  I found no way to confirm or rule this out from here, and it is
  pre-existing, general daemon.py behavior outside this package's scope, so
  I did not touch it speculatively.

**What I changed (`tests/test_behavioral.py` only — no source-file changes,
since I could not PROVE a feature bug; per the controller's own
instruction, source changes require a demonstrated bug and I have none):**
1. **`wrapper.SESSION_CAPTURE_DELAY` pinned to 0** via `monkeypatch` (was
   the real, unconditional 5.0s wall-clock block in `wrapper_main` Step 5,
   paid by EVERY dispatch in this test for nothing — `capture_session`
   always returns `None` for this bare `cli="fake"` route with no
   `session_capture`/`session_discover` set). Precedented
   (`test_daemon.py`/`test_wrapper.py` already do this for "real wrapper"
   tests). This shrinks the real-time window for whatever the gate's
   timing-sensitive issue is, and makes the test meaningfully faster
   locally too (confirmed same-content passes, faster wall clock).
2. **Replaced the mid-test `paths.routes_path().write_text(...)` second
   file-write with an in-memory `config.Routes` object + a
   `config.Routes.load` monkeypatch.** Every route lookup in this codebase
   goes through `Routes.load()` (confirmed via grep), never a direct file
   read, so this is behavior-preserving and removes one genuinely unique
   (no other `test_behavioral.py` test does a second routes.toml rewrite
   mid-test) moving part with zero offsetting benefit — reduces risk
   surface even though I could not prove it was causal.
3. **Added `_diagnose_stuck_attempt(project, task_id)`** — a durable
   (reads straight from disk/the event log, never in-memory/sampled state)
   forensic dump wired into every assertion message in this test. If this
   recurs under the gate, the NEXT report will show: attempt state,
   receipt-on-disk existence, `child.pid` content, the tails of
   `wrapper.log`/`attempt.log`, and the full event-type tail + any
   `TICK_ERROR` payloads — turning a bare "never went INTERRUPTED" into an
   actionable trace without needing another round-trip.

**What I deliberately did NOT do:** delete, skip, or xfail the test; weaken
its assertions (still requires the SAME attempt resumed, exactly one
implementer `ATTEMPT_CREATED`, never `BLOCKED` — all read from durable
storage, unchanged); or speculatively patch `daemon.py`/`wrapper.py`
without being able to demonstrate the bug myself.

**Honest status:** I could not reproduce the failure in this devcontainer
under ~12 attempts including the gate's own exact `coverage run` invocation,
so I cannot claim to have PROVEN the fix. The three changes above are each
independently justified (real risk reduction / real diagnosability gain),
not cosmetic dodges, and the strongest candidate root cause (`os.fork()`
under active coverage instrumentation, in a container with different
`pytest`/`coverage` versions than this devcontainer, for a codebase that has
already hit one Python-3.14 fork-related issue per the Dockerfile's own
comment) is now recorded here for the controller to weigh. Re-run the gate;
if it recurs, `_diagnose_stuck_attempt`'s output is the next lead.
