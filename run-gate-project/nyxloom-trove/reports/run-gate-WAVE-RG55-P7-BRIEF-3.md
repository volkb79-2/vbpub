# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-3

Checkpoint fired per the handoff's HARD checkpoint clause (E-008: ARM at
~60 tool calls / ~120k context, CUT at a coherent boundary; session 1 ran
370 calls before cutting, explicitly called out as unacceptable; session 2
cut at ~80). This session used well over 60 tool calls implementing RW-36
in full (a materially larger "small commit" than its name suggests, once
the `execute_plan` refusal-check interaction was discovered) plus
substantial regression testing. Cut point: RW-36 committed (two commits,
`e27b107b` + `c8200271`), its own tests green (38/38 on `liveness.py`,
100% line+branch; 406 total targeted tests green across the affected
surface), LOG/REPORT written — the clause's own top-priority boundary
("commit > LOG/REPORT write"). Not a decision ask (RW-9 doesn't apply);
not a red gate. **A3 was NOT started this session** — see "The A3 design
decision, made explicit" below, which is this BRIEF's main payload: BRIEF-2
asked the next session to make that decision and write it down BEFORE
coding the monitoring loop, and this session did that final design step
but ran out of budget before implementing it.

**Tip at cut:** `c8200271` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits so far (full
history, newest first):

```
c8200271 docs(run-gate-project): P7 LOG self-hash for RW-36 commit (e27b107b)
e27b107b fix(assay): B091 RW-36 -- liveness via argv_appended + judge.mutation.liveness gate
ef5088f6 docs(run-gate-project): P7 LOG/REPORT for A2 (f4fa1788) + checkpoint BRIEF-2
f4fa1788 feat(assay): B091 A2 -- materialized pytest liveness plugin + os._exit candidate wrapper (D-23/RW-33)
723c431d docs(run-gate-project): P7 checkpoint BRIEF-1 (A1 done, A2-A6 open)
afac6fcb docs(run-gate-project): P7 LOG entry for f649a249
f649a249 test(assay): fix a stray leftover assertion in the A1 diagnostics=None regression test
5a25625f docs(run-gate-project): P7 LOG/REPORT records for A1 (de32bb91)
de32bb91 feat(assay): B091 A1 -- budget_per_candidate = "auto" default (D-23)
```

## Successor instructions

1. Re-read the ORIGINAL handoff in full first:
   `run-gate-WAVE-RG55-P7-HANDOFF.md` (on `main`). Every rule in it still
   binds.
2. Re-read `run-gate-WAVE-RG55-P7-BRIEF-1.md` (session 1, A1) and
   `-BRIEF-2.md` (session 2, A2 partial) in full — still valid, not
   re-litigated, except where THIS brief explicitly supersedes a detail
   (the `argv_declared`→`argv_appended` channel, the A-036 exception is
   now RESOLVED not open).
3. Read the controller log's **RW-36** ruling (`main`,
   `run-gate-WAVE-RG55-CONTROLLER-LOG.md`) — already summarized above and
   in this session's own commit, but read it verbatim once.
4. Read this BRIEF in full (you are reading it now) — especially "The A3
   design decision, made explicit" below. This is the one thing BRIEF-2
   asked for that this session actually delivered; treat it as decided,
   not as a proposal to re-litigate.
5. Read `src/assay/liveness.py` in full — it changed substantially this
   session (`inject_liveness_plugin` rewritten, `LivenessInjection`
   NamedTuple, the `LIVENESS_*` vocabulary). Its module docstring now
   states RW-36 as settled.
6. Read `assay.runner.CommandPlan.cli_argv_appended`'s own docstring and
   `execute_plan`'s refusal check (both in `runner.py`) — new this
   session, and A3's `LivenessRunner` will sit right next to the second
   `cli_argv_appended`-adjacent call site (`_run_prepared_lane`'s R2
   candidate dispatch).
7. Read `run-gate-WAVE-RG55-P7-LOG.md`'s `e27b107b` entry and
   `-REPORT.md`'s "RW-36 (session 3)" section for the full design/oracle/
   test mapping — do not re-derive any of it.

## What's DONE

- **A1** (session 1): `budget_per_candidate = "auto"` default. Complete.
- **A2** (session 2 + session 3's RW-36 correction): the materialized
  pytest plugin, the pytest-argv detection rule, the plan-injection
  function (now `argv_appended`-based per RW-36), `LivenessRunner` **v1
  scope only** (env-stamping, still delegates blockingly to `inner`), the
  new `judge.mutation.liveness` gate, and the `liveness: {active, reason,
  plugin}` wire record on both the `plan` progress event and
  `judgment.r2`. Fully tested (100% line+branch on `liveness.py`, 406
  targeted tests green across the whole affected surface). **A2 is now
  fully settled and closed** — no known open items remain in it; the
  A-036 exception that was flagged for reviewer attention through session
  2 is RESOLVED by RW-36 and this session's implementation of it.
- A real, pre-existing bug fixed incidentally: `verify.py`'s
  `_reconstruct_judgment_r2` never read `budget_per_candidate_derived_s`
  (A1) back off the raw document — `assay verify` was silently broken for
  every real A1-produced document with that field set. Fixed.

## What's OPEN — A3 through A6, unchanged in substance from BRIEF-2

### A3 — the active monitoring loop, `hung` bucket (still the largest remaining item)

Nothing in `LivenessRunner.__call__` changed this session beyond what
env-stamping already did (it still delegates blockingly to `inner`). RW-33's
full ask (non-blocking Popen launch, 1s poll, process-tree CPU sampling,
`hung` vs `budget_exceeded` classification, `killpg(SIGKILL)`) is entirely
undone. Re-read BRIEF-2's own A3 section (points 1-6) for the mechanics —
not repeated here to avoid drift between two copies — **except point 3's
open design question, which THIS session now answers**:

### The A3 design decision, made explicit (per BRIEF-2's own request)

**Question:** `CommandResult`/`Outcome`/`ReasonCode` are closed vocabularies
(A-050: "an implementer who needs a code not listed here stops and asks").
`_execute_plan_inner` (runner.py) has exactly ONE path that produces
`Outcome.BUDGET_EXCEEDED`: catching `subprocess.TimeoutExpired` from
`process_runner`, which it turns into `CommandResult(outcome=BUDGET_EXCEEDED,
reason_code=LANE_TIMEOUT, ...)` using the tails from the exception's own
`.stdout`/`.stderr`. The NORMAL completion path (`proc.returncode == 0`)
can only ever produce PASS or FAIL — there is no way to return a
`CompletedProcess` and have it come out `BUDGET_EXCEEDED` at all. So
`LivenessRunner`'s new monitoring loop, on detecting `hung`, MUST go
through the exact same `except subprocess.TimeoutExpired` path a genuine
elapsed-budget timeout already does (raise it, do not try to return a
`CompletedProcess` and hope `_execute_plan_inner` does something special
with it — it will not, on the current code). The only remaining question is
how `_classify_mutant_result` (mutation.py) tells a genuine timeout
(`budget_exceeded`) apart from an idle-stall kill (`hung`), given both
arrive at `_execute_plan_inner` as a caught `subprocess.TimeoutExpired`.

**Decision:**
1. `liveness.py` gains one new exception class:
   ```python
   class LivenessHungExpired(subprocess.TimeoutExpired):
       """Raised by LivenessRunner's monitoring loop when it kills a
       candidate for HUNG (idle stall), never for a genuine elapsed-budget
       timeout -- which still raises plain subprocess.TimeoutExpired,
       unchanged. The ONLY thing this subclass exists for is letting
       _execute_plan_inner's except clause (runner.py) tell the two apart
       without CommandResult growing a new field."""
   ```
   Construct it exactly the way `subprocess.run` constructs the real
   thing: `LivenessHungExpired(cmd=argv, timeout=timeout, output=<captured
   stdout bytes>, stderr=<captured stderr bytes>)` — same shape
   `_decode_timeout_stream`/`_bounded_tail` in `runner.py` already consume,
   so NOTHING about that decode path needs to change.
2. `errors.py` gains one new `ReasonCode` member in the
   `Outcome.BUDGET_EXCEEDED` group, beside `LANE_TIMEOUT`:
   ```python
   #: (B091/RW-33, P7) A LivenessRunner-classified idle stall -- the
   #: candidate's process tree stopped producing progress (no new `test`
   #: event, no CPU growth) though nothing about the lane's own elapsed
   #: budget expired. Distinct from LANE_TIMEOUT's genuine elapsed-time
   #: expiry so mutation._classify_mutant_result can tell "ran out of time"
   #: (budget_exceeded) apart from "stopped making progress" (hung), which
   #: score identically (both excluded from killed/(killed+survived)) but
   #: are reported as different buckets (RW-33: never conflate the two).
   CANDIDATE_HUNG = "CANDIDATE_HUNG"
   ```
   Add it to `REASON_CODES[Outcome.BUDGET_EXCEEDED]`'s frozenset beside
   `LANE_TIMEOUT`/`MUTANT_LIMIT_EXCEEDED`/`SNAPSHOT_LIMIT_EXCEEDED`. This
   is the SAME kind of addition this file's own history already shows
   repeatedly (each new reason code arrives with a package/ruling
   annotation, e.g. `MUTANT_LIMIT_EXCEEDED`'s own `(P21/A-163)` tag) — not
   a fresh violation of A-050's closed-vocabulary discipline, but the
   mechanism BY WHICH that discipline has always been extended, now
   invoked under RW-33's own sanction rather than an implementer's
   unilateral choice.
3. `runner.py`'s `_execute_plan_inner` except-clause gets a ONE-LINE
   change: `reason_code = ReasonCode.CANDIDATE_HUNG if isinstance(exc,
   liveness.LivenessHungExpired) else ReasonCode.LANE_TIMEOUT`, then uses
   `reason_code` instead of the hardcoded `ReasonCode.LANE_TIMEOUT` in the
   `CommandResult(...)` it returns. `runner.py` already imports `liveness`
   as a module (see its own top-of-file import block) — no new import, no
   cycle (`liveness.py` still only imports `runner` under
   `TYPE_CHECKING`). Functionally INERT for every other `process_runner`:
   none of them ever raise `LivenessHungExpired`, so this branch is
   unreachable dead code for R0/R1/R3/every non-liveness R2 call site,
   exactly as "other runners untouched" requires.
4. `mutation.py`'s `_classify_mutant_result` and
   `_classify_mutant_result_with_equivalence` both gain: where they
   currently do `if result.outcome is Outcome.BUDGET_EXCEEDED: return
   "budget_exceeded"`, change to `if result.outcome is
   Outcome.BUDGET_EXCEEDED: return ("hung" if result.reason_code is
   ReasonCode.CANDIDATE_HUNG else "budget_exceeded")`.
5. **Why not the handoff's literal "a CompletedProcess-shaped result
   carrying the classification"?** That phrasing (BRIEF-2's paraphrase of
   RW-33) is a sketch, not an API mandate — RW-33's actual text just says
   "kill the tree, classify hung, report it like budget_exceeded" without
   specifying a `LivenessRunner`-internal mechanism. A literal bare
   `CompletedProcess` return (e.g. a negative returncode from SIGKILL) hits
   the NORMAL completion branch, which only ever computes PASS/FAIL from
   `returncode == 0` — it cannot reach `BUDGET_EXCEEDED` without a second,
   parallel special-case in `_execute_plan_inner`'s happy path, which is a
   BIGGER and uglier change than reusing the exception path
   `budget_exceeded` already has. Raising a distinguishable
   `TimeoutExpired` subclass reuses 100% of the existing, already-tested
   tail-decoding/truncation logic and adds exactly one `isinstance` check
   to a function every lane already goes through, with zero behavioural
   change for anyone who never raises the subclass.
6. **CPU-spinning mutants are NOT hung** (RW-33 is explicit) — they hit the
   ordinary budget ceiling. This means `LivenessRunner`'s own poll loop
   must ALSO still enforce the plain elapsed-`timeout` bound itself (since
   it now owns the launch, `process_runner`'s caller-side
   `subprocess.run(timeout=...)` enforcement no longer applies once
   `LivenessRunner` switches to `Popen`) and raise a PLAIN
   `subprocess.TimeoutExpired` (not the `LivenessHungExpired` subclass) in
   that case — this is the "enforce the existing budget/timeout exactly as
   today" requirement BRIEF-2 already named; now it is explicit HOW: same
   exception type, same `_execute_plan_inner` branch, `reason_code`
   defaults to `LANE_TIMEOUT` for it via the `isinstance` check above.

### A3 remainder, unchanged from BRIEF-2 (re-read BRIEF-2's own numbered
list for the mechanics; this section only updates the ONE item the
decision above resolves)

1. `LivenessRunner.__call__` rewrite: non-blocking `Popen(start_new_session
   =True, stdout=<file>, stderr=<file>)`, read files back for
   `stdout`/`stderr` at the end (match `COMMAND_TAIL_BYTES`/`_bounded_tail`
   convention — reuse `runner._bounded_tail` directly rather than a second
   implementation; it is a private name (`_bounded_tail`) so either import
   it explicitly with a comment explaining the cross-module reach, or
   ask/flag if that feels wrong — this is exactly BLOCKED-protocol
   territory if the reviewer disagrees).
2. 1s poll loop: `proc.poll()`; parse the events NDJSON for the newest
   `test`/`session_finish` event time (tolerant of a torn last line);
   sample process-TREE utime+stime from `/proc/<pid>/stat` (+children via
   `/proc/<pid>/task/*/children`, ppid-scan fallback; ANY `/proc` read
   failure must make CPU look like it's STILL GROWING — write a test that
   proves this explicitly, it is an RW-33 requirement not a nice-to-have).
3. `hung` iff: no `test`/stdout-growth for `expect_next_event_within_s =
   max(3 x slowest_test_s, 15s)` (from the BASELINE's own events file —
   see point 4 below) AND tree CPU grew < 1.0s over the trailing 30s; OR
   `session_finish` seen and the process still alive 30s later. On
   hung: raise `liveness.LivenessHungExpired` (this BRIEF's own new
   design). On plain elapsed-budget expiry: raise plain
   `subprocess.TimeoutExpired`, unchanged meaning. Either way:
   `os.killpg(pgid, SIGKILL)` and reap BEFORE raising, so the exception
   carries real, final captured output.
4. **Baseline events wiring, shared prerequisite for A3's
   `expect_next_event_within_s` AND A4's `plan.slowest_test_s`:** the
   baseline currently runs the plugin fully INERT (no env vars set at
   all — A2's contract is candidate-only `os._exit`). Needs
   `ASSAY_LIVENESS_EVENTS=.../baseline.ndjson` set on the baseline's OWN
   env at its `execute_plan`/`_execute_snapshot_unit` call site (a few
   lines above where `_run_prepared_lane`'s injection guard sits), NEVER
   `ASSAY_LIVENESS_EXIT`. Do this once, feed both A3 and A4 from the same
   read.
5. Add `"hung"` to `MUTATION_BUCKETS` (`verdict.py`) and thread it through:
   `verdict.py`'s `Mutation.hung` field + `to_dict` + `_check_arithmetic`
   (re-grep — this session did not touch `Mutation`'s bucket fields at all,
   BRIEF-1/BRIEF-2's list is a starting point only), `verdict.schema.json`'s
   `mutation` definition + outcome enum, `mutation.py`'s bucket dict +
   `_classify_mutant_result`/`_classify_mutant_result_with_equivalence`
   (this BRIEF's own decision above), `verify.py`'s re-derivation
   (`_reconstruct_...` functions AND whatever re-derives the score from
   bucket counts — re-grep `MUTATION_BUCKETS` fresh in `verify.py`, this
   session did not). Scored like `budget_exceeded` (excluded from
   `killed/(killed+survived)`). Additive schema (no v12 cut) — prove it
   the same way A1 proved `budget_per_candidate_derived_s`'s additivity.
6. Fixture-project end-to-end tests, real subprocesses, budgeted
   carefully under HOST LOAD below: a mutant that blocks on a non-daemon
   thread join → `hung`; one that busy-loops (CPU-bound) →
   `budget_exceeded`, NOT `hung`. Every new conditional tested with the
   OTHER optional parameter at its default (BRIEF-1's own lesson, restated
   because A1 and A2 both caught a real bug exactly this way).

### A4 — progress stream fields (unchanged from BRIEF-2; blocked on A3's
baseline-events wiring, point 4 above, which is now explicitly shared
between A3 and A4 rather than merely "naturally shared work")

### A5 — `--rejudge` / `--rejudge-outcome` (unchanged from BRIEF-1/BRIEF-2;
genuinely independent, could move earlier for a quick win)

### A6 — close-out (unchanged; needs ALL of A2-A5 — A2 is NOW done, A3-A5
are not)

## HOST LOAD — this session's observation

`/proc/pressure/memory` `full avg10` ranged 0.3–5.1 across this session's
several targeted-test batches (one reading at exactly 5.1, taken right
after the heaviest batch of the whole session — `avg300` never exceeded
2.8, so this was not a sustained spike; no run was skipped or delayed).
Re-check before the next session's first run regardless. Two other wave
tracks' R2 mutation containers were confirmed alive at both the start and
the end of this session
(`run-gate-vbpub-r2-2315801-1789214565`, watcher PID `2415767`, and the
launcher PID `2415766` running `run-gate.py --base main assay-r2` from
`.worktrees/rg55-run-gate-client/run-gate-project`) — **re-check
`docker ps`/`pgrep -af 'run-gate.py'` before running anything
mutation-lane-shaped**; A3's own future fixture-project e2e tests are
real subprocesses but are NOT the estate's shared mutation-lane gate
container, so they do not by themselves conflict with the binding
"no second mutation run of YOUR project" rule — the REAL gate's own
mutation lane (A6) is the one that must wait for both trackers to clear.

## Self-authored retention prompt (paste into the successor's first turn)

```
Resume RG-55 P7 (assay B091) from BRIEF-3. Re-read the ORIGINAL handoff
(run-gate-WAVE-RG55-P7-HANDOFF.md) in full, then BRIEF-1 (session 1, A1),
BRIEF-2 (session 2, A2 partial + the BRIEF-2 A3 sketch) and this BRIEF-3
(session 3, RW-36 shipped + the A3 design decision) in full, then
REPORT.md's A1/"A2 (session 2)"/"RW-36 (session 3)" sections and the LOG's
de32bb91/f4fa1788/e27b107b entries. Tip is c8200271 on branch
assay-liveness (already the worktree's current branch -- no new worktree
add). A1 and A2 are BOTH fully DONE now (RW-36 closed A2's one open
judgment call). A3 is NOT started, but its single biggest open design
question -- how does a HUNG kill reach mutation._classify_mutant_result as
a distinct bucket from budget_exceeded, given CommandResult's
Outcome/ReasonCode vocabularies are closed -- IS decided and written down
in this BRIEF's own "The A3 design decision, made explicit" section:
liveness.LivenessHungExpired (a subprocess.TimeoutExpired subclass, raised
by LivenessRunner's new monitoring loop only for the idle-stall case, never
for a genuine elapsed-budget timeout) + one new ReasonCode.CANDIDATE_HUNG
under Outcome.BUDGET_EXCEEDED + a one-line isinstance check in
runner._execute_plan_inner's existing except clause + a two-line branch in
mutation.py's two classifier functions. KEEP that decision as settled, do
not re-derive or re-litigate it -- implement it. Read src/assay/liveness.py
and CommandPlan.cli_argv_appended's docstring in runner.py first (both
changed substantially this session). Order: baseline ASSAY_LIVENESS_EVENTS
wiring (shared A3/A4 prerequisite, do it FIRST) -> the active monitoring
loop using the decided hung/budget_exceeded mechanism -> the hung bucket's
full threading (verdict.py/schema/verify.py/mutation.py bucket dict) ->
fixture-project e2e tests (thread-join -> hung, busy-loop ->
budget_exceeded) -> A4's cheap stream fields (baseline-events read already
exists by then) -> A5 (independent, a quick win if you want one first) ->
A6. One commit per coherent unit, tests first, LOG per commit, checkpoint
again at ~60 tool calls or a coherent boundary -- A3's remaining
implementation (the actual Popen/proc-sampling loop plus its tests) is
real, non-trivial work and may consume the whole session by itself; that
is an acceptable single-session scope.
```
