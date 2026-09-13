# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-2

Checkpoint fired per the handoff's HARD checkpoint clause (E-008: ARM at
~120k context / ~60 tool calls, CUT at a coherent boundary; session 1 ran
370 calls before cutting, explicitly called out as unacceptable). This
session used roughly 80 tool calls. Cut point: A2 committed, its own tests
green (28/28), a targeted regression run green (42 + 1 real e2e), a real
self-run mutation-check done, LOG/REPORT written — the clause's own
top-priority boundary ("commit > LOG/REPORT write"). Not a decision ask
(RW-9 doesn't apply); not a red gate.

**Tip at cut:** `f4fa1788` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits so far (full history):

```
de32bb91 feat(assay): B091 A1 -- budget_per_candidate = "auto" default (D-23)
5a25625f docs(run-gate-project): P7 LOG/REPORT records for A1 (de32bb91)
f649a249 test(assay): fix a stray leftover assertion in the A1 diagnostics=None regression test
afac6fcb docs(run-gate-project): P7 LOG entry for f649a249
[this session's BRIEF-2 commit, hash not yet known when this line was written]
f4fa1788 feat(assay): B091 A2 -- materialized pytest liveness plugin + os._exit candidate wrapper (D-23/RW-33)
```

## Successor instructions

1. Re-read the ORIGINAL handoff in full first:
   `run-gate-WAVE-RG55-P7-HANDOFF.md` (on `main`). Every rule in it still
   binds.
2. Re-read `run-gate-WAVE-RG55-P7-BRIEF-1.md` in full (session 1's own
   checkpoint) for A1's design decisions — still valid, not re-litigated.
3. Re-read this BRIEF in full (you are reading it now).
4. Re-read `run-gate-WAVE-RG55-P7-LOG.md`'s `f4fa1788` entry and
   `-REPORT.md`'s "A2 (session 2)" section for the FULL design/oracle/test
   mapping — do not re-derive any of it.
5. Read `src/assay/liveness.py` in full (it is ~330 lines, self-contained,
   and its own module docstring explains the A-036 exception reasoning —
   this is the single most important file to actually read, not skim).
6. Controller log: RW-33 (verbatim design, already summarized in BRIEF-1 and
   the original handoff) still stands. No new ruling has landed since this
   session began. If the reviewer this session's A2 commit gets has already
   weighed in on the flagged A-036 judgment call, read that verdict FIRST
   and follow it over anything below.

## What's DONE

- **A1** (session 1): `budget_per_candidate = "auto"` default. Complete,
  tested, documented, committed. Not re-verified this session beyond the
  regression run below (which exercises it indirectly).
- **A2, PARTIAL** (this session, `f4fa1788`): the materialized pytest
  liveness plugin (`assay.liveness._PLUGIN_SOURCE`,
  `materialize_liveness_plugin`), the pytest-argv detection rule
  (`argv_invokes_pytest`), the plan-injection function
  (`inject_liveness_plugin`, extends `argv_declared` + `PYTHONPATH`), and
  `LivenessRunner` **v1 scope only** (env-stamping
  `ASSAY_LIVENESS_EVENTS`/`ASSAY_LIVENESS_EXIT=1`, delegates to `inner` —
  NOT the active monitoring loop). Wired into `runner._run_prepared_lane`:
  injection happens once, before baseline, for every native R2 python lane
  whose argv invokes pytest; `LivenessRunner` wraps `process_runner` ONLY
  at the R2 candidate call site (`mutation.run_mutation`'s own
  `process_runner=` argument), never for the baseline.
- **Spike done, real, recorded** (LOG `f4fa1788`): proved the plugin
  resolves from a venv where assay isn't importable, proved the RW-28 hang
  class is cured, and CAUGHT A REAL BUG (`os._exit` drops the buffered
  terminal summary/coverage report unless stdout/stderr are flushed first)
  before any production code existed — fixed in the shipped plugin source.
- 28 new unit tests, 100% line+branch on `liveness.py` itself
  (`coverage run --branch --source=assay.liveness`). 3 planted mutants, all
  caught by an existing test (table in REPORT.md).
- Targeted regression, GREEN: `test_mutation_judge.py` +
  `test_runner_run_lane_r2.py` (42 passed), plus the real end-to-end
  `test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end` (1
  passed, genuine subprocess, plugin genuinely injected and exercised).

## What's OPEN

### A2/A3 remainder — the active monitoring loop, `hung` bucket (the largest remaining item)

`LivenessRunner.__call__` currently just stamps two env vars and calls
`self._inner(argv, env=stamped_env, cwd=cwd, timeout=timeout)` — i.e. it is
STILL a blocking call today (`inner` is `default_process_runner`, which is
`subprocess.run(...)`). What RW-33 actually asks for and is NOT yet built:

1. Rewrite `LivenessRunner.__call__` to launch NON-blocking:
   `subprocess.Popen(argv, env=stamped_env, cwd=cwd, stdout=<file>,
   stderr=<file>, start_new_session=True)` (files, not pipes — RW-33 is
   explicit about this; `default_process_runner` uses `capture_output=True`
   which is pipes, so this is a genuine behavior difference to get right:
   read the files back at the end to build the `CompletedProcess`'
   `stdout`/`stderr` strings, respecting whatever the rest of the codebase
   expects for `stdout_tail`/`stderr_tail` truncation — check
   `COMMAND_TAIL_BYTES`/`_bounded_tail` in `runner.py` for the existing
   convention and MATCH it, do not invent a second one).
2. A 1s poll loop: (a) `proc.poll()` for exit; (b) read the events side
   file's newest `test`/`session_finish` event time (the plugin already
   writes hand-rolled JSON lines — `{"event": "test", ...}` — parse with
   `json.loads` per line, tolerant of a partial last line since the plugin
   opens/appends/flushes/closes per event, so a torn write should be rare
   but not assumed impossible mid-read); (c) sample the process TREE's
   utime+stime from `/proc/<pid>/stat` (children via
   `/proc/<pid>/task/*/children`, fall back to a ppid scan across
   `/proc/*/stat` if that file is absent — any `/proc` read failure must
   make CPU look like it's STILL GROWING, i.e. never declare hung on
   missing data — this is an explicit RW-33 requirement, write a test that
   proves it); (d) enforce the existing budget/timeout exactly as today
   (the SAME `timeout` parameter the Protocol already receives) →
   `budget_exceeded`, unchanged classification.
3. `hung` iff: no `test` event (or, when the plugin isn't active for this
   lane — check `liveness_injected`/whether `ASSAY_LIVENESS_EVENTS` is even
   set, which it always is for a `LivenessRunner`-wrapped call in THIS
   design, so the "plugin not active" fallback in RW-33 is really about a
   lane where liveness was never injected at all, i.e. `LivenessRunner`
   itself is never even constructed — re-read RW-33's exact wording before
   assuming which fallback path applies where) for longer than
   `expect_next_event_within_s` AND the tree's CPU time grew by < 1.0s over
   the trailing 30s; OR `session_finish` was seen and the process is still
   alive 30s later. On hung or budget: `os.killpg(pgid, SIGKILL)`, reap,
   return a `CompletedProcess`-shaped result carrying the classification so
   `_classify_mutant_result` can map it to the new bucket (this needs a
   real design decision: `CompletedProcess` has no spare field for
   "hung" — RW-33 says "a `CompletedProcess`-shaped result carrying the
   classification"; the cleanest thing is probably a synthetic returncode
   sentinel PLUS a distinguishing marker in `stderr`/`stdout_tail`, OR (more
   honest) `_classify_mutant_result` needs to inspect something OTHER than
   the bare `CompletedProcess` — check whether `execute_plan`/`CommandResult`
   already has a hook for "the runner itself classified this outcome"
   before inventing a sentinel; this is the single most important design
   decision left, make it explicit and write it down before coding).
4. `expect_next_event_within_s = max(3 x slowest_test_s, 15s)` where
   `slowest_test_s` comes from the BASELINE's own events file — which means
   the baseline ALSO needs `ASSAY_LIVENESS_EVENTS` set (pointing at
   `.assay/liveness/baseline.ndjson`) even though it must NEVER get
   `ASSAY_LIVENESS_EXIT`. This session's A2 cut deliberately did NOT wire
   this (the baseline today runs with the plugin present but fully inert,
   zero env vars set) — wiring it is required before `slowest_test_s` can
   be computed at all, and is naturally shared work with A4 (the `plan`
   event's new fields need the exact same baseline-events read). Consider
   doing this wiring once, feeding both A3's `expect_next_event_within_s`
   and A4's `plan` event fields from the same baseline-events read.
5. Add `"hung"` to `MUTATION_BUCKETS` (`verdict.py`) and thread it through
   every place the closed vocabulary is checked — re-grep
   `MUTATION_BUCKETS`, this session did NOT do this grep freshly; BRIEF-1's
   list (`verdict.py`'s `to_dict`/schema enum, `mutation.py`'s bucket dict
   and `_classify_mutant_result`, `verify.py`'s re-derivation) is a
   starting point, not a final list. Scored like `budget_exceeded` (outside
   the killed/(killed+survived) denominator). Additive schema (no v12 cut)
   — prove it the same way A1 proved `budget_per_candidate_derived_s`'s
   additivity (REPORT.md's A1 section has the pattern).
6. Fixture-project end-to-end tests the handoff's own ORDER names
   explicitly and this session did NOT reach: a tiny real project whose
   mutant blocks on a non-daemon thread join → `hung`; one that busy-loops
   → `budget_exceeded`. These are real, slow(er) subprocess tests — budget
   them accordingly, run under `nice`/`ionice`, check
   `/proc/pressure/memory` first (see HOST LOAD below).

### A4 — progress stream fields (blocked on A3's baseline-events wiring above)

`test` events to the progress stream for the BASELINE only (translate from
`baseline.ndjson` after R0 completes — NOT emitted live from inside the
candidate/baseline subprocess, the progress stream is assay's own process'
object); `plan` event gains `slowest_test_s`/`expect_next_event_within_s`;
each `candidate` event gains `tests_completed` (count of `test` events in
that candidate's own ndjson file — already being written by the plugin
today, just not yet read back) and `outcome` may be `hung`. Add `"test"` to
`mutation.PROGRESS_EVENTS` (currently: check its exact current members
before assuming — this session did not modify it). Side files: rename plan
matches `.assay/liveness/baseline.ndjson` +
`.assay/liveness/candidates/<hash>.ndjson` (the candidates subdir + cwd-hash
naming is ALREADY what `LivenessRunner` does today, this session — reuse it,
don't invent a second naming scheme) — removed after classification unless
an existing keep/debug flag says otherwise (find that flag; this session
did not look for it).

### A5 — `--rejudge` / `--rejudge-outcome`

Untouched this session. BRIEF-1's sketch (drop_rejudge_records,
REJUDGE_OUTCOME_ALIASES with `"error"` -> `"crashed"`, CLI wiring order) is
still the design of record — re-read BRIEF-1's own A5 section in full, it
was not repeated here to avoid drift between two copies. Genuinely
independent of A2/A3/A4; could be done in parallel by a different session if
the controller wants to split the remaining work, EXCEPT that
`--rejudge-outcome hung` obviously cannot be implemented until the `hung`
bucket itself exists (A3).

### A6 — close-out

Untouched. Needs ALL of A2-A5 done first (do not mark B091 FIXED early).
CONSUMERS.md has NOT been touched this session for A2 (A1's CONSUMERS
edits from session 1 stand; A2 needs its own pass — the progress protocol
section, the R2-admission table, migration notes for `hung`). README if it
references mutation execution. The REAL gate
(`tools/tester-unified-gate.sh`) has NEVER been run this whole package —
still deferred, per the handoff's own ordering, until every deliverable
lands.

## Known gaps / honesty notes for the next session and any reviewer

- `runner.py`'s own two changed call sites (the injection guard and the
  `candidate_process_runner` conditional inside `_run_prepared_lane`) were
  NOT individually diff-coverage-checked against the project's real
  coverage judge this session — only a targeted pytest regression run
  (green) confirms they don't crash and don't regress existing behavior.
  Both branches (`liveness_injected` True and False) are very likely
  exercised by the existing R2 test fixtures (most use `argv=("pytest",
  ...)`-shaped lanes) but this was not confirmed line-by-line. Run the real
  diff-coverage self-check on `runner.py`'s diff before or alongside A3's
  work.
- The A-036 exception (argv_declared augmentation bypassing
  `allow_argv_append`) is flagged three times now (this module's own
  docstring, the LOG entry, this BRIEF) specifically so it cannot be missed
  by a reviewer. It is NOT re-litigating RW-33 (RW-33 didn't specify
  `argv_declared` vs. a hypothetical third field) — it is a real judgment
  call this session made under the BLOCKED protocol, with a stated default,
  proceeding rather than stopping. If a reviewer overturns it, the fix is
  contained entirely to `liveness.inject_liveness_plugin`'s body — nothing
  else in this codebase depends on WHICH CommandPlan field carries the
  injected tokens.
- The baseline currently runs the plugin fully INERT (no env vars set) —
  this is deliberate (A2's contract is candidate-only `os._exit`) but means
  A3/A4 cannot start writing baseline-timing logic without first wiring
  `ASSAY_LIVENESS_EVENTS=.../baseline.ndjson` into the baseline's own env at
  its `execute_plan`/`_execute_snapshot_unit` call site (a few lines above
  where this session's injection guard sits in `_run_prepared_lane`) — this
  is explicitly flagged above under A3/A4, not forgotten, just not done.

## HOST LOAD — this session's observation

`/proc/pressure/memory` `full avg10` ranged 1.1-3.8 throughout this
session — never backed off, never needed to. Re-check before the next
session's first heavy run regardless; do not assume this baseline holds.
Two other wave tracks (P1, P2) were noted as running their own assay R2
mutation lanes in RW-33's own dispatch table as of this session's start —
re-check `docker ps`/`pgrep -af 'run-gate.py'` before running anything
mutation-lane-shaped, per the handoff's own binding rule.

## Self-authored retention prompt (paste into the successor's first turn)

```
Resume RG-55 P7 (assay B091) from BRIEF-2. Re-read the ORIGINAL handoff
(run-gate-WAVE-RG55-P7-HANDOFF.md) in full, then BRIEF-1 (session 1, A1) and
this BRIEF-2 (session 2, A2 partial) in full, then REPORT.md's A1 and "A2
(session 2)" sections and the LOG's de32bb91/f4fa1788 entries. Tip is
f4fa1788 (plus this BRIEF-2's own follow-up commit) on branch
assay-liveness (already the worktree's current branch -- no new worktree
add). A1 is DONE. A2 is PARTIAL: the plugin, the injection mechanism, and
LivenessRunner v1 (env-stamping only, still blocking) are shipped, tested
100%, and regression-checked; the ACTIVE monitoring loop, the hung bucket,
and the fixture-project end-to-end tests are NOT done. Read
src/assay/liveness.py in full before writing anything -- it is the one file
everything else builds on, and its own module docstring carries a flagged,
unresolved A-036 judgment call a reviewer may have already weighed in on
(check for a review round on f4fa1788 first). KEEP: the "what's open"
section's exact technical detail (the CompletedProcess-shaped hung-result
design question is the single biggest undecided thing -- make that decision
explicitly and write it down BEFORE coding the monitoring loop); the
baseline-events-wiring dependency shared by A3 and A4 (do it once). DROP:
nothing yet -- this BRIEF is already the distillation, there is no earlier
narrative to drop. Order: wire baseline ASSAY_LIVENESS_EVENTS (shared A3/A4
prerequisite) -> the active monitoring loop + hung bucket + its schema/
verify.py threading (A3, the big one) -> A4's stream fields (cheap once A3's
baseline-events read exists) -> A5 (independent, can move earlier if you
want a quick win) -> A6. One commit per coherent unit, tests first, LOG per
commit, checkpoint again at ~60 tool calls or a coherent boundary -- A3
alone may consume the whole budget given its real complexity (a genuine
Popen-based monitoring loop with process-tree CPU sampling), and that is an
acceptable single-session scope if so.
```
