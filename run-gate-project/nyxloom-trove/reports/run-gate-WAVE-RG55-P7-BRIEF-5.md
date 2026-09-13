# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-5

Checkpoint fired per the handoff's own HARD checkpoint clause (ARM at
~60 tool calls / ~120k context, CUT at a coherent boundary). This session
ran meaningfully past the ~60-call guideline — a real bug (invalid JSON in
the liveness plugin's own event lines) surfaced by the FIRST real e2e
test's own failure required a genuine investigation (isolate the decision
logic in isolation, reproduce the CLI scenario standalone, read the raw
event file) before a fix was possible, and the planted-mutant table that
followed found two more real test-suite precision gaps needing their own
fixes — none of which offered an earlier clean stopping point once
started. Cut point: BOTH of BRIEF-4's own first items (the real e2e test,
the planted-mutant table) are done, green, committed, LOG/REPORT written —
a full, coherent deliverable (item 0 in the handoff's own ordering) at its
own natural boundary, not a mid-deliverable cut. Not a decision ask (every
judgment call this session made was BRIEF-3/BRIEF-4's own decision
implemented as written, or a bug fix directly required to make that
decision's own test pass — see "Judgment calls" below for the one
genuinely new one); not a red gate.

**Tip at cut:** `d1540eda` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits so far (full
history, newest first):

```
d1540eda test(assay): B091 A3 -- >=3 planted-mutant table for the monitoring loop (BRIEF-3/4's own ask)
99463ae5 fix(assay): B091 A3 remainder -- real e2e liveness fixture tests + a real plugin JSON bug found by them
4ace234f docs(run-gate-project): P7 LOG/REPORT for A3 (44dd12ca) + checkpoint BRIEF-4
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

(This BRIEF's own commit lands right after this file is written, per the
usual pattern — not yet in the list above.)

## Successor instructions

1. Re-read the ORIGINAL handoff in full first:
   `run-gate-WAVE-RG55-P7-HANDOFF.md` (on `main`). Every rule in it still
   binds.
2. Re-read BRIEF-1/BRIEF-2/BRIEF-3/BRIEF-4 (A1/A2/RW-36/A3-session-4's own
   history and design decisions) — still valid, not re-litigated.
3. Read this BRIEF in full (you are reading it now).
4. Read the LOG's `99463ae5` and `d1540eda` entries and REPORT.md's
   "A3 (session 5)" section for the FULL design/oracle/bug/test mapping —
   do not re-derive any of it.
5. Skim the diff of `99463ae5` (`git show 99463ae5`) for the exact
   `_PLUGIN_SOURCE` fix shape and the two new e2e tests' exact TOML/
   fixture-source construction — you will want the same argv/budget
   pattern if A4's own work touches the same fixture shape at all (it
   should not need to; A4 is progress-stream plumbing, not liveness
   classification, but the pattern is there if useful).

## What's DONE

- **A1** (session 1): `budget_per_candidate = "auto"` default. Complete.
- **A2** (session 2 + session 3's RW-36 correction): the materialized
  pytest plugin, the injection mechanism, `judge.mutation.liveness`.
  Complete.
- **A3** (sessions 4+5, complete end to end as of THIS session):
  - Session 4 (`44dd12ca`): the active `LivenessRunner` monitoring loop,
    `hung` bucket, threaded through every closed vocabulary, incl. the
    `judge_mutation` outcome-precedence gap it found and fixed. 100%
    line+branch on `liveness.py`. See BRIEF-4/LOG `44dd12ca` for the full
    account — still valid, not re-litigated.
  - Session 5 (`99463ae5`, `d1540eda`): the real end-to-end fixture-
    project CLI test (thread-join hang → `hung`; busy-loop →
    `budget_exceeded`) AND the ≥3-planted-mutant table BRIEF-3 asked for
    on top of the 100% self-check — BOTH now done. **A real,
    previously-undetected bug was found and fixed along the way**: the
    materialized plugin's own NDJSON event lines were never valid JSON
    (`%r`/`repr()` instead of `json.dumps` — single-quoted strings, bare
    `None` instead of `null`), silently swallowed by every downstream
    `json.loads` as a "tolerated torn line". Effect: `slowest_test_s` was
    NEVER measured (always the coarse 60s-floor fallback, not the tight
    15s-floor bound A3 was designed around) and the "`session_finish`
    seen, still alive 30s later" `hung` branch was dead code in practice.
    Fixed (`json.dumps` instead of hand-rolled `%r` strings), pinned by a
    new fast unit test. The planted-mutant table then found TWO of its
    OWN four candidate mutants were not caught by the existing suite
    (both off-by-one `>=`→`>` boundary conditions no existing test
    exercised AT the exact boundary) — both fixed with new,
    boundary-exact tests, per BRIEF-4's own explicit rule (fix the test,
    never just record "known gap"). `liveness.py`: still **100%
    line+branch** (285 stmts/78 branches) throughout every change this
    session. Full narrative: LOG `99463ae5`/`d1540eda`, REPORT's
    "A3 (session 5)" section.

**A3 is now fully, honestly complete** — every item BRIEF-3 named for it,
including the two BRIEF-4 flagged as session 4's own open gaps, is done
and green.

## What's OPEN — A4, A5, A6, in that order (unchanged from BRIEF-4)

### A4 — progress stream fields (blocked on nothing — A3 is now fully done
and, as of this session, its baseline-events wiring is PROVEN to actually
work end to end against a real plugin, not merely unit-tested against
hand-built fixtures)

`test` events to the progress stream for the BASELINE only (translate from
`baseline.ndjson`, real, populated, and — as of this session — VALID JSON,
after R0 completes); `plan` event gains `slowest_test_s`/
`expect_next_event_within_s` (the SAME computation
`compute_expect_next_event_within_s` already does — read it back rather
than re-deriving, B088's own "two derivations drift" lesson); `candidate`
event gains `tests_completed` (count `test` events in that candidate's own
`.ndjson` file — already being written, and — as of this session — now
genuinely parseable, just not read back yet); `"test"` added to
`mutation.PROGRESS_EVENTS`.

Read `mutation.py`'s `PROGRESS_EVENTS` (~line 808) and its `plan`/
`candidate` progress-event construction sites (search for
`"liveness":` around line 2037 for the `plan` event's own shape, and for
wherever the `candidate` event is built — this session did not need to
touch either) before writing anything.

### A5 — `--rejudge` / `--rejudge-outcome`

Untouched, unchanged from BRIEF-1/BRIEF-2's own sketch. Genuinely
independent of A4. `--rejudge-outcome hung` is implementable (the bucket
has existed since session 4).

### A6 — close-out

Untouched. Needs ALL of A4/A5 first. `CHANGES.md`/`CONSUMERS.md` have
NEVER been touched for A2, A3(session 4), OR A3(session 5) — A6's own pass
needs to cover all three (plus A4/A5) in one go, not incrementally. The
real gate (`tools/tester-unified-gate.sh`) has NEVER been run this whole
package — still deferred until every deliverable lands. When A6 runs it:
read the verdict in a SEPARATE step (LESSONS L4), check
`pgrep -af 'assay-r2|assay.cli run r2'`/`docker ps --no-trunc` for other
wave tracks' own mutation-lane runs first (non-mutation lanes may run
under PSI per RW-39; the mutation lane itself needs no OTHER assay-project
R2 run active), launch it UNTRACKED (`nohup … > log 2>&1 & disown`) with a
cheap watcher, per the handoff's own binding rule.

## Judgment calls this session made (flagged per BLOCKED protocol, not
asks — each proceeded on its own default, flag on return)

1. **The two e2e tests are SEPARATE tests (separate lanes/fixtures), not
   one test with two candidate mutants sharing one lane.** BRIEF-4's own
   prose reads as if a single lane/fixture might carry both a hang mutant
   and a busy-loop mutant. Two independent compare-swap sites in one lane
   would force ONE shared `budget_per_candidate` across both candidates —
   but the hang candidate needs a budget PAST its own ~31s detection
   point while the busy-loop candidate needs one PAST the 30s CPU-window
   check to genuinely exercise the "still growing after 30s" branch
   (not merely "budget ran out before the window was ever checked") —
   two different minimums with no single value that is both tight and
   correct for both. Splitting into two lanes resolved this cleanly and
   let each budget be chosen for exactly what it needs to prove. Default:
   proceeded with two tests. Flag: if a future reviewer wants ONE lane
   with two candidates instead (BRIEF-4's own literal two-bullet
   phrasing under item 1 does not explicitly rule this out), the budget
   tension above is the reason this session did not attempt it.
2. **The plugin JSON bug fix and its own regression test were treated as
   part of item (0) (A3's remainder), not spun into a separate numbered
   item.** It is a direct, unavoidable prerequisite the real e2e test
   itself could not pass without — not a scope expansion chosen freely.
   Default: fixed it inline, same commit as the e2e tests. Flag: this
   DOES mean session 5's diff is larger than "just write the two tests"
   would have been; the LOG/REPORT both give the full investigation
   trail so a reviewer can verify the fix was necessary, not merely
   convenient.
3. **No full `mutation`/`runner`/`verdict`/`verify`/`liveness`/`errors`-
   named 2107-test sweep this session** (session 4's own regression
   discipline) — targeted files only (the exact surface this session's
   changes reached: `test_liveness*.py`, `test_runner_execute.py`,
   `test_mutation_judge.py`, `test_verify_hung_bucket.py`,
   `test_verdict_reason_codes.py`, `test_verdict_conformance.py`,
   `test_errors.py`, plus the two new e2e tests — 877+ tests total across
   these, all green). Default: call-budget-conscious targeted regression,
   matching this session's actual blast radius (the plugin string and two
   test files — nothing in `mutation.py`/`runner.py`/`verdict.py`/
   `verify.py` itself changed this session, only session 4 touched
   those). Flag: A6's own close-out pass should still run the FULL
   `mutation`/`runner`/`verdict`-named sweep at least once before the
   real gate, per the handoff's own ordering — this session's targeted
   set is not a substitute for that, only a call-budget-appropriate
   interim check.

## HOST LOAD — this session's observation

`/proc/pressure/memory` `full avg10` read 6.92–9.43 (ABOVE the 5.0
back-off threshold) right when the real e2e run was due; backed off via a
bounded `Monitor` poll (8×15s ceiling, not a raw sleep) until it read 4.32,
then proceeded — stayed low (≤0.5) for the remainder of the session.
`nice -n19`/`ionice -c3` for every pytest invocation; serial; targeted
files. The busy-loop mutant's own ~100%-of-one-core spin runs at NORMAL
(non-niced) priority for ~35s by construction (`LivenessRunner` launches
the candidate directly via `Popen`, not through this session's own
`nice`-prefixed shell) — bounded and brief (the lane's own small
`budget_per_candidate` bounds it), not left running; worth noting for a
successor running the SAME two e2e tests again (e.g. as part of A6's
regression sweep) — re-check PSI first, same as this session did, since
the busy-loop test's own CPU spin is real and repeats every time the test
runs. Other wave tracks' own mutation-lane containers/launchers
(`run-gate.py --base main assay-r2`, PID 1141617; P1's `cgroup-profiler`
R2 resume, containers `run-gate-vbpub-r2-680904-*`) were confirmed alive
throughout — this session never ran the shared assay R2 mutation-lane gate
itself, only targeted pytest plus the two isolated real-subprocess e2e
tests, neither of which conflicts with either per the handoff's own
binding rule.

## Self-authored retention prompt (paste into the successor's first turn)

```
Resume RG-55 P7 (assay B091) from BRIEF-5. Re-read the ORIGINAL handoff
(run-gate-WAVE-RG55-P7-HANDOFF.md) in full, then BRIEF-1 through BRIEF-4
(A1, A2, RW-36, A3-session-4) and this BRIEF-5 (session 5: A3's remainder
-- the real e2e test AND the planted-mutant table, both now done) in full,
then REPORT.md's "A3 (session 5)" section and the LOG's 99463ae5/d1540eda
entries -- do not re-derive any of the design/oracle/bug/test mapping
recorded there. Tip is d1540eda on branch assay-liveness (already the
worktree's current branch -- no new worktree add).

A3 IS NOW FULLY, HONESTLY COMPLETE -- every item BRIEF-3 ever named for it,
including both of session 4's own flagged gaps, is done and green. Do NOT
re-attempt the real e2e test or the planted-mutant table; both exist,
both pass, both are committed.

KEEP as settled, do not re-litigate: the LivenessHungExpired/CANDIDATE_HUNG
mechanism (BRIEF-3's decision, session 4); the hung/budget_exceeded
precedence in both _classify_mutant_result AND judge_mutation
(budget_exceeded outranks hung when both present); the two e2e tests' own
lane-splitting design (SEPARATE lanes for the hang vs. busy-loop mutants,
per BRIEF-5's own "Judgment calls" #1 -- a single shared budget_per_
candidate cannot correctly bound both scenarios at once); the plugin's
json.dumps-based event-line format (BRIEF-5's own bug fix -- if you ever
touch _PLUGIN_SOURCE again, do NOT reintroduce %r/repr()-based string
building, and re-run test_materialized_plugin_writes_valid_json_events
after any change to either hook).

Order: A4 (progress-stream fields -- blocked on nothing, A3's baseline-
events wiring is now PROVEN correct end to end, not just unit-tested) ->
A5 (--rejudge/--rejudge-outcome, independent, --rejudge-outcome hung is
implementable) -> A6 (needs ALL of A4/A5; CHANGES.md/CONSUMERS.md owe A2,
A3-session-4, AND A3-session-5 in one pass, never touched for any of the
three; the real gate has NEVER run this whole package). One commit per
coherent unit, tests first, LOG per commit, checkpoint again at ~60 tool
calls or a coherent boundary -- this session ran past that guideline for
the same reason session 4 did (a real bug investigation plus a planted-
mutant table that found two more real gaps did not offer an earlier clean
stopping point once started), which is a pattern worth naming for whoever
picks up A6: budget generously for "the real test finds a real bug" as the
NORMAL case in this package, not the exception, and let a found bug's own
investigation run to a clean fix before cutting, same as this session and
session 4 both did.
```
