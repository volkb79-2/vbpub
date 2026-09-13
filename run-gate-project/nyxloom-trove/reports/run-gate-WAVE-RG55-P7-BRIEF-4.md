# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-4

Checkpoint fired per the handoff's own HARD checkpoint clause (E-008: ARM at
~60 tool calls / ~120k context, CUT at a coherent boundary, never past ~90
calls). This session ran meaningfully past 90 calls before cutting — A3's
implementation plus its full closed-vocabulary threading plus two real,
pre-existing gaps found along the way (see below) turned into more work than
a single clean stopping point offered before the loop itself was done, green,
and regression-clean. Cut point: A3 committed (`44dd12ca`), 100% line+branch
on `liveness.py`, 2107+148 targeted tests green across the whole affected
surface, LOG/REPORT written — the clause's own top-priority boundary
("commit > LOG/REPORT write"). Not a decision ask (RW-9 doesn't apply, every
judgment call this session made was BRIEF-3's own decision implemented as
written, not a fresh one); not a red gate.

**Tip at cut:** `44dd12ca` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits so far (full history,
newest first):

```
44dd12ca feat(assay): B091 A3 -- active LivenessRunner monitoring loop, hung bucket
d2b7c76d docs(run-gate-project): P7 checkpoint BRIEF-3 (RW-36 shipped; A3 design decided)
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

(This BRIEF's own commit will land right after this file is written, per the
usual pattern — not yet in the list above at the moment this text was
composed.)

## Successor instructions

1. Re-read the ORIGINAL handoff in full first:
   `run-gate-WAVE-RG55-P7-HANDOFF.md` (on `main`). Every rule in it still
   binds.
2. Re-read BRIEF-1/BRIEF-2/BRIEF-3 in full (A1/A2/RW-36's own history) —
   still valid, not re-litigated.
3. Read this BRIEF in full (you are reading it now).
4. Read the LOG's `44dd12ca` entry and REPORT.md's "A3 (session 4)" section
   for the FULL design/oracle/test mapping — do not re-derive any of it.
5. Read `src/assay/liveness.py` in full (it roughly doubled in size this
   session — the monitoring loop, the `/proc` tree-CPU sampler, the
   `LivenessHungExpired` class, `compute_expect_next_event_within_s` are all
   new).
6. Read `mutation.judge_mutation`'s docstring and body (its precedence
   chain now has a `hung` branch) and `runner._execute_plan_inner`'s
   `except subprocess.TimeoutExpired` clause (the `isinstance` check) —
   both changed this session, both are load-bearing for anything A4/A5/A6
   touches near them.

## What's DONE

- **A1** (session 1): `budget_per_candidate = "auto"` default. Complete.
- **A2** (session 2 + session 3's RW-36 correction): the materialized
  pytest plugin, the injection mechanism, `judge.mutation.liveness`.
  Complete.
- **A3** (session 4, `44dd12ca`): the ACTIVE monitoring loop is fully
  implemented and tested per BRIEF-3's exact decided mechanism:
  - Non-blocking `Popen(start_new_session=True)`, stdio to files, 1s poll.
  - `/proc`-tree CPU sampling (`tree_cpu_seconds`, task-API primary +
    ppid-scan fallback, per-pid not just at the root) — ANY `/proc` read
    failure reads as "still growing", proven by a dedicated test.
  - `hung` iff idle (no new `test`/`session_finish` event, no
    stdout/stderr growth) for `expect_next_event_within_s` AND CPU flat
    over the trailing 30s; OR `session_finish` seen and still alive 30s
    later (CPU-independent).
  - `LivenessHungExpired` (a `subprocess.TimeoutExpired` subclass) raised
    on `hung`; plain `subprocess.TimeoutExpired` on genuine elapsed-budget
    expiry (a CPU-spinning mutant is proven NOT hung).
  - `runner._execute_plan_inner`'s one-line `isinstance` check maps the
    subclass to `ReasonCode.CANDIDATE_HUNG`.
  - `expect_next_event_within_s` computed from the baseline's OWN events
    file (`ASSAY_LIVENESS_EVENTS` now wired onto the baseline's env too,
    never `_EXIT`) — `max(3 x slowest_test_s, 15s)`, falling back to
    `max(60s, baseline_s / 4)` when unavailable. This wiring is the shared
    A3/A4 prerequisite BRIEF-3 asked for, and it is DONE.
  - `hung` threaded through `MUTATION_BUCKETS` (now six), `Mutation.hung`,
    the schema (additive, not `required`), `_classify_mutant_result`/
    `_classify_mutant_result_with_equivalence` (per-candidate),
    **`judge_mutation`'s own precedence chain (OVERALL claim outcome — a
    real gap this session found and fixed, see below)**, and `verify.py`'s
    raw-layer checks (`_mutation_of` normalizes a missing key, additive).
  - `liveness.py`: **100% line+branch** (285 stmts, 78 branches).
  - Regression: **2107 targeted tests green** across every
    mutation/runner/verdict/verify/liveness/errors file, plus 148 more
    (`test_cli_run.py` full file, config, docs-vocabulary).
- **Two real, pre-existing gaps found and fixed this session** (full
  account in LOG `44dd12ca` and REPORT's "A3" section — READ these before
  touching either area again, do not re-discover them):
  1. `mutation.judge_mutation` had NO `hung` branch — a `hung`-only
     candidate's OVERALL R2 claim silently fell through to `PASS`. Fixed;
     two tests pin both the fix and its own precedence-ordering regression
     (`budget_exceeded` still outranks `hung` when both are present).
  2. Four independent hand-written vocabulary transcriptions
     (`docs/DESIGN-GUIDE.md` §6, `tests/test_errors.py`, `tests/
     test_verdict_conformance.py` [plus its own fixture-completeness check
     — a new fixture, `r2_budget_exceeded_candidate_hung.json`, was
     required], `tests/test_verdict_reason_codes.py`'s hardcoded `32`→`33`
     counts) needed updating for the new `CANDIDATE_HUNG` code. If YOUR
     session adds another new `ReasonCode` or `MUTATION_BUCKETS` entry,
     expect to update ALL FOUR of these same places again — they are not
     cross-checked against `errors.py`/`verdict.py` by construction
     (that independence is their whole point, per each file's own
     docstring), so nothing catches a missed one except running the
     regression sweep and reading the failure.

## What's OPEN — A3's own remaining honesty gaps, then A4-A6

### A3 remainder (do this FIRST — it's the most direct continuation)

1. **The real end-to-end fixture-project test BRIEF-3 names explicitly,
   through the REAL installed `assay run` CLI** (mirror
   `test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end`'s own
   shape: a real two-commit `git_repo` fixture, a real `assay.toml` lane
   declaring `rigor = ["R0", "R2"]`, `judge.language = "python"`,
   `judge.mutation` with a small `max_mutants`/`jobs = 1`, argv that
   genuinely invokes pytest so liveness activates):
   - **A thread-join-style hang → `hung` within ~45s.** A verified-
     available direction for a REAL `python:compare-swap` mutant to flip
     baseline-true→mutant-false at one chosen input: source `x <= 0`
     (`_COMPARE_SWAP` in `src/assay/adapters/python.py` maps `ast.LtE` →
     `ast.Lt`) tested at `x = 0` — baseline `0 <= 0` is `True`, mutant
     `0 < 0` is `False`. Design the fixture module so that
     TRUE-branch-taken sets a `threading.Event` (or similar CPU-idle
     blocking primitive — `Event.wait()`/`Queue.get()`, NEVER a busy loop)
     that a background thread's `.wait()` (started before the branch, with
     `t.join()` called with NO timeout, inside the test body itself — not
     after the test returns, so the hang happens BEFORE
     `pytest_sessionfinish` ever fires and A2's `os._exit` cure cannot
     mask it) is blocked on; the FALSE branch (only the mutant reaches it)
     never signals the event, so the mutant's own `t.join()` blocks
     forever while burning ~0 CPU — exactly the `hung` shape. Keep the
     fixture's lane budget small (a few minutes at most) so the test's own
     wall-clock stays under ~90s total per BRIEF-3's own bound (the ~30-45s
     to reach `hung` classification, plus normal test overhead).
   - **A busy-loop → `budget_exceeded`, NOT `hung`.** Same mechanism, but
     the FALSE branch (mutant-only) enters a genuinely CPU-bound spin
     (`while True: x = x + 1`, or similar — must actually consume CPU, not
     block) instead of blocking — proves `tree_cpu_seconds` reading real
     growth from a REAL spinning child correctly prevents `hung` and lets
     the ordinary elapsed-budget path classify it `budget_exceeded`
     instead. This is RW-33's own explicit distinguishing case, not
     optional coverage.
   - Assert on the REAL verdict document's `claims[1].mutation.hung`/
     `.budget_exceeded` buckets, `claims[1].status`/`.reason_code`,
     exactly like the existing `test_run_evaluates_a_real_r2_pass_end_to_
     end` asserts on `.killed`.
2. **≥3 planted mutants for the monitoring loop's own logic, recorded in
   REPORT.md with the test that catches each** (BRIEF-3's own explicit
   ask, SEPARATE from and IN ADDITION TO the 100% line+branch self-check
   already done — a planted mutant proves a REAL logic defect is caught by
   a REAL test, not merely that every line executed once). Suggested
   candidates, each targeting a genuine judgment the loop makes (back up
   `liveness.py` with `cp` first, per HOST LOAD below, never `git checkout
   --` on uncommitted work — though this file IS already committed at
   `44dd12ca`, so a real accidental revert is recoverable either way, but
   still don't rely on that):
   - Flip `idle_for >= self._expect_next_event_within_s` to `>` (off-by-
     one on the idle threshold) — should be caught by
     `test_idle_with_flat_cpu_is_hung` or a new boundary-exact test.
   - Flip `cpu_growing = (cpu_now - baseline_cpu) >= _HUNG_CPU_GROWTH_
     FLOOR_S` to `>` — should be caught by
     `test_cpu_growing_prevents_hung_even_when_idle` (or needs a new test
     if it isn't — check first, don't assume).
   - Remove the `session_finish_at is not None and ...` disjunct entirely
     (collapse `hung` to the idle-clause only) — should be caught by
     `test_session_finish_then_still_alive_is_hung_regardless_of_cpu`.
   - Swap `LivenessHungExpired` for plain `subprocess.TimeoutExpired` in
     the hung-raise branch (or vice versa in the budget branch) — should
     be caught by `test_runner_execute.py::test_liveness_hung_expired_is_
     budget_exceeded_candidate_hung` and/or the monitor tests' own
     `type(excinfo.value) is subprocess.TimeoutExpired` assertions (which
     use `type() is`, not `isinstance`, specifically to catch this).
   A mutant NOT caught by an existing test is a real bug in this session's
   own test suite — fix the test (or the code, if the mutant reveals a
   genuine defect), do not just record it as "uncaught, known gap".

### A4 — progress stream fields (blocked on nothing now — A3 already
wired the shared baseline-events prerequisite)

`test` events to the progress stream for the BASELINE only (translate from
`baseline.ndjson`, now real and populated, after R0 completes); `plan`
event gains `slowest_test_s`/`expect_next_event_within_s` (the SAME
computation `compute_expect_next_event_within_s` already does — read it
back rather than re-deriving, B088's own "two derivations drift" lesson,
already applied once this session to `judge_mutation`'s gap, do not
reintroduce the pattern here); `candidate` event gains `tests_completed`
(count `test` events in that candidate's own `.ndjson` file — already
being written, just not read back); `"test"` added to
`mutation.PROGRESS_EVENTS`.

### A5 — `--rejudge` / `--rejudge-outcome`

Untouched, unchanged from BRIEF-1/BRIEF-2's own sketch. Genuinely
independent — `--rejudge-outcome hung` can now actually be implemented
(the bucket exists), which it could not before this session.

### A6 — close-out

Untouched. Needs ALL of A3(remainder)-A5. `CHANGES.md`/`CONSUMERS.md` have
NOT been touched for A2 or A3 (deferred per the handoff's own ordering,
same precedent BRIEF-2 already established for A2) — A6's own pass needs
to cover A2 AND A3 in one go, not incrementally. The real gate
(`tools/tester-unified-gate.sh`) has NEVER been run this whole package —
still deferred until every deliverable lands.

## HOST LOAD — this session's observation

`/proc/pressure/memory` `full avg10` stayed ≤4.5 throughout (never
approached the 5.0 back-off threshold); `nice -n 19 ionice -c 3` used for
every pytest invocation; every run serial, targeted files only. Two other
wave tracks' own mutation-lane containers/launchers were confirmed alive
at both the start (`run-gate.py --base main assay-r2`, watcher PID
2415767/launcher 2415766) and later in the session (P1's `cgroup-profiler`
R2 resume joined partway through, PID 680903/680904) — **re-check
`docker ps --no-trunc`/`pgrep -af 'run-gate.py'` before running anything
mutation-lane-shaped**, per the handoff's own binding rule. The next
session's real end-to-end fixture test (a genuine `assay run` subprocess)
is NOT the shared gate container and does not by itself conflict, per
BRIEF-3's own precedent note — but it DOES spawn real, possibly
CPU-spinning children (the busy-loop mutant, by design) for real seconds,
so check PSI immediately before running it regardless.

## Self-authored retention prompt (paste into the successor's first turn)

```
Resume RG-55 P7 (assay B091) from BRIEF-4. Re-read the ORIGINAL handoff
(run-gate-WAVE-RG55-P7-HANDOFF.md) in full, then BRIEF-1/BRIEF-2/BRIEF-3
(A1, A2, RW-36) and this BRIEF-4 (session 4: A3 implemented) in full, then
REPORT.md's "A3 (session 4)" section and the LOG's 44dd12ca entry -- do not
re-derive any of the design/oracle/test mapping recorded there. Tip is
44dd12ca on branch assay-liveness (already the worktree's current branch --
no new worktree add). A1, A2 and A3's CORE MECHANISM are all DONE: the
active LivenessRunner monitoring loop (Popen, /proc-tree CPU sampling,
hung/budget_exceeded classification via LivenessHungExpired, killpg) is
implemented, 100% line+branch on liveness.py, and threaded through every
closed vocabulary (MUTATION_BUCKETS, the schema, both classifier functions,
AND judge_mutation's own outcome precedence -- a real gap this session found
where a hung-only candidate silently verdicted PASS, now fixed and
regression-tested). 2107+148 targeted tests green.

KEEP as settled, do not re-litigate: the LivenessHungExpired/CANDIDATE_HUNG
mechanism itself (BRIEF-3's own decision); the hung/budget_exceeded
precedence in both _classify_mutant_result AND judge_mutation
(budget_exceeded outranks hung when both are present -- arbitrary but
stable, not load-bearing); the newest-to-oldest CPU-sample scan shape in
_monitor (rewritten this session specifically to avoid a structurally-
unreachable coverage branch, read the LOG's own explanation before
"simplifying" it back).

TWO explicit gaps this session did NOT close, and they are BRIEF-4's own
first items -- do them before anything else:
1. The real end-to-end fixture-project test BRIEF-3 names explicitly,
   through the REAL assay CLI: a thread-join-style hang -> hung within
   ~45s, a busy-loop -> budget_exceeded (not hung). BRIEF-4's own "A3
   remainder" section has a concrete, verified-available mutant design
   (python:compare-swap on `x <= 0` at x=0, LtE->Lt) -- read it, do not
   re-derive the operator-catalogue research from scratch.
2. A >=3 planted-mutant table for the monitoring loop's own logic,
   recorded in REPORT.md with the catching test for each -- separate from
   and in addition to the 100% line+branch self-check already done.
   BRIEF-4 has four concrete candidate mutants already sketched.

Order: A3's two remaining gaps above (do these FIRST, they complete A3
honestly) -> A4 (now unblocked -- the baseline-events wiring A3 shipped is
its own shared prerequisite, already done) -> A5 (independent, --rejudge-
outcome hung is now actually implementable) -> A6 (needs ALL of A3-A5;
CHANGES.md/CONSUMERS.md still owe BOTH A2 and A3 in one pass, never
touched for either). One commit per coherent unit, tests first, LOG per
commit, checkpoint again at ~60 tool calls or a coherent boundary -- this
session ran past ~90 calls before cutting (A3's threading work plus two
real gaps found along the way did not offer an earlier clean stopping
point once started), which the handoff's own clause treats as an
exception to note and correct for, not a new normal to repeat without
comment.
```
