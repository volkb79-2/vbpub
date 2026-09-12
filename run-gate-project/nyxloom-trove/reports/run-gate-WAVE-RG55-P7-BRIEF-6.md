# run-gate-WAVE-RG55-P7 — CHECKPOINT BRIEF-6

Checkpoint fired per the handoff's own HARD checkpoint clause (ARM at
~60 tool calls / ~120k context, CUT at a coherent boundary; never past 90
calls). This session ran well past the ~60-call guideline — A4 (progress-
stream fields) and A5 (`--rejudge`/`--rejudge-outcome`) are both full,
non-trivial deliverables with their own design decisions, threading
through three files each, and neither offered a clean earlier stopping
point once started (A4's baseline-forwarding + plan-field work and A5's
CLI-to-`run_mutation` threading chain are each one coherent unit). Cut
point: BOTH A4 and A5 are done, green, committed, LOG/REPORT written — two
full, coherent deliverables at a natural boundary, not a mid-deliverable
cut. Not a decision ask (every judgment call this session made proceeded
on its own stated default — see "Judgment calls" in the REPORT's own A4
and A5 sections; flagged there, not asked here); not a red gate.

**Tip at cut:** `1eaf5683` on branch `assay-liveness`, worktree
`/workspaces/vbpub/.worktrees/assay-liveness`. Commits so far (full
history, newest first):

```
1eaf5683 docs(run-gate-project): P7 LOG/REPORT for A5 (c15f6040)
c15f6040 feat(assay): B091 A5 -- --rejudge/--rejudge-outcome
e6a35ff0 docs(run-gate-project): P7 LOG/REPORT for A4 (5baf2670)
5baf2670 feat(assay): B091 A4 -- progress stream gains test events, slowest_test_s/expect_next_event_within_s, tests_completed
72baf838 docs(run-gate-project): P7 LOG/REPORT for 99463ae5+d1540eda + checkpoint BRIEF-5
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
2. Re-read BRIEF-1 through BRIEF-5 (A1, A2, RW-36, A3 sessions 4+5) — still
   valid, not re-litigated.
3. Read this BRIEF in full (you are reading it now).
4. Read the LOG's `5baf2670` and `c15f6040` entries and REPORT.md's "A4"
   and "A5" sections for the FULL design/oracle/test mapping — do not
   re-derive any of it.

## What's DONE

- **A1** (session 1): `budget_per_candidate = "auto"` default. Complete.
- **A2** (session 2+3): the materialized pytest plugin, injection
  mechanism, `judge.mutation.liveness`. Complete.
- **A3** (sessions 4+5): active `LivenessRunner` monitoring loop, `hung`
  bucket, real e2e CLI tests, ≥3-planted-mutant table, a real plugin-JSON
  bug found and fixed. Fully, honestly complete — every item BRIEF-3 ever
  named for it is done and green.
- **A4** (session 6, `5baf2670`): baseline `test` events forwarded to the
  progress stream (never per candidate); `plan` gains `slowest_test_s`/
  `expect_next_event_within_s` (read back from `assay.liveness.
  baseline_slowest_test_s`, never re-derived); `candidate` gains
  `tests_completed` (read back from that candidate's own liveness events
  file via the new `liveness.candidate_events_path`/`count_test_events`);
  `"test"` added to `mutation.PROGRESS_EVENTS`. `liveness.py` 100%
  line+branch (297 stmts/80 branches). Every changed line in `mutation.py`/
  `runner.py` confirmed covered against a 920-test combined run.
- **A5** (session 6, `c15f6040`): `run --resume --rejudge <id>[,...]` and
  `--rejudge-outcome BUCKET[,...]` (`"error"` aliased to `"crashed"` at
  the CLI layer). Drops matching state records before the resume store is
  consulted, so they genuinely re-execute; refuses an unknown/stale-source
  `--rejudge` id (`MutationStateError`, cross-referencing B088) before any
  record loads; the two selections union rather than double-execute.
  `"resume"` progress event gains `rejudged_total`. CLI-level parsing
  refuses cleanly (`BAD_LANE_CONFIG`, never an uncaught exception) at the
  same point `--shard`'s own malformed-string check already does.

**A4 and A5 are both fully, honestly complete** — every item the handoff
named for either is done and green, tests-first, coverage-verified on
every changed line in both files.

## What's OPEN — A6 (the only item left)

Close-out: needs a full pass over ALL of A2, A3 (sessions 4 AND 5), A4,
AND A5 — none of `CHANGES.md`/`CONSUMERS.md` have been touched for ANY of
these five deliverables yet, across the whole package. Do this in ONE
pass, not incrementally (the handoff's own explicit instruction, restated
by BRIEF-5, still binds).

### A6 checklist, in the order the handoff gives it

1. **Backlog** (`assay/nyxloom-trove/4-backlog.md`):
   - **B091 → FIXED**, with evidence (commit hashes for every deliverable:
     A1 `de32bb91`, A2 `f4fa1788`+RW-36 `e27b107b`, A3 `44dd12ca`+
     `99463ae5`+`d1540eda`, A4 `5baf2670`, A5 `c15f6040`).
   - **B090 → note "mitigated by B091"**.
   - **File B092** (new item from the controller mid-session, NOT in the
     original handoff — see RW-41 in the CONTROLLER-LOG on `main`):
     `--resume` identity (B088, per-tree) is invalidated by commits to
     non-judged paths (e.g. a records-only `.md` commit inside the judged
     tree makes the next `--resume` reject every record). Row only — do
     NOT implement. Exact text is in the dispatching message this session
     received; reconstruct from RW-41's own wording on `main`
     (`run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-
     CONTROLLER-LOG.md`) if this BRIEF's own copy is insufficient — search
     for "B092" there.
   - **Optional, call-budget permitting** (this session's own A5 REPORT
     section flags it, does not mandate it): a possible NEW backlog row on
     `MutantOutcome.identity` (a tuple: path/span/hash/operator) being a
     DIFFERENT identity from `mutation.candidate_id()`'s sha256 digest
     (what `--rejudge`/state records actually key by) — no verdict-level
     field exposes the digest today, so a consumer cannot build a
     `--rejudge <id>` invocation from a prior verdict's own
     `judgment.r2`/`Mutation.survived` list without reading the PERSISTED
     state record directly. A6's own judgment call whether this rises to
     a filed row or stays a `CONSUMERS.md` documentation caveat under
     `--rejudge`'s own section — see REPORT's A5 section, "A real, if
     minor, discovery" paragraph, for the full account before deciding.
2. **`CHANGES.md`** `[Unreleased]`: entries for A1 (if not already
   present — check first), A2, A3, A4, A5. Use the LOG/REPORT's own
   language as source material; do not re-derive descriptions from the
   diffs.
3. **`CONSUMERS.md`**, per the handoff's own explicit list:
   - `judge.mutation.liveness` (the policy, its three spellings, the
     `plan`/`judgment.r2` `liveness: {active, reason, plugin}` record).
   - "a native Python R2 lane's argv must invoke pytest for liveness" rule
     (RW-33's `argv_invokes_pytest` check — token `"pytest"`, a path
     ending `/pytest`, or the adjacent pair `-m pytest`; anything else is
     OFF with a WARN under `auto`, refused at load under an explicit
     `true`).
   - the `hung` bucket (additive to the v10 verdict's outcome enum;
     reported like `budget_exceeded`, never `killed`; the two detection
     branches — idle-with-flat-CPU, and `session_finish`-then-still-alive
     — from RW-33/A3's own design).
   - `--rejudge`/`--rejudge-outcome` (the two selection mechanisms, their
     union semantics, the `"error"`→`"crashed"` alias, the unknown-id
     refusal and its B088 cross-reference, requires `--resume`).
   - **Also update the EXISTING progress-stream table** (the one under
     "The progress stream" — currently stale even for A2/A3: it lists
     `plan`'s notable fields as only `baseline_s, budget_per_candidate_s,
     derived`, missing the `liveness` block RW-36/A3 already shipped AND
     this session's own `slowest_test_s`/`expect_next_event_within_s`;
     `PROGRESS_EVENTS` is missing `"test"`; the `resume` row's own
     `resumed_total`/`rejected_total` needs `rejudged_total` added; the
     `candidate` row needs `tests_completed` added). This table has now
     been stale across FOUR sessions (A2, A3, A4, A5 each added fields
     without updating it) — A6 is where all four catch up at once, not
     incrementally.
4. **`README.md`** (assay's own, if it documents lane-level flags/policy
   at all — check first; the handoff names it alongside CONSUMERS but this
   session did not verify whether README.md currently says anything about
   mutation/liveness that would need updating).
5. **Full regression sweep** — the handoff's own "mutation"/"runner"/
   "verdict"-named 2107-test sweep that no session in this package has yet
   run in full (session 4 flagged deferring it; session 5 flagged it
   again; this session's own targeted 920-test and 228-test combined runs
   are NOT a substitute, only interim checks matching each session's own
   blast radius). Run it ONCE before the real gate. `/proc/pressure/memory`
   `full avg10` was reading 0.01–2.91 throughout this session (briefly
   spiking on the `some avg10` companion figure to 6.26, never on `full`)
   — check fresh before running, back off if `full avg10 > 5`.
6. **The REAL registered gate**, run ONCE, per the handoff's own binding
   rule: read `assay/run-gate.toml`/`assay/cmru.toml` (this session did
   not open either) for the exact gate line; read the verdict in a
   SEPARATE step (LESSONS L4), never a pipe tail. Check
   `pgrep -af 'assay-r2|assay.cli run r2'`/`docker ps --no-trunc` for
   OTHER wave tracks' own mutation-lane runs first (non-mutation lanes may
   run now under PSI per RW-39; the mutation lane itself needs no OTHER
   assay-project R2 run active — this session never checked this, since it
   never reached the gate). Launch the mutation lane UNTRACKED
   (`nohup … > log 2>&1 & disown`) with a cheap watcher, per the handoff's
   binding rule. Do NOT run `cmru release` — the controller releases
   6.2.0.

## Judgment calls this session made (flagged per BLOCKED protocol, not
asks — each proceeded on its own default, flag on return)

**A4:**
1. **`compute_expect_next_event_within_s`'s public signature was kept
   byte-identical** (still returns a bare `float`, 13 pre-existing tests
   depend on it) rather than changed to also return `slowest_test_s`
   directly. Instead, the shared parse was extracted into
   `baseline_slowest_test_s`/`_iter_test_events`, and
   `compute_expect_next_event_within_s` now calls the former internally.
   `runner.py` calls BOTH functions (technically reading the small
   baseline file twice), but through the exact same single implementation
   both times, so the two facts (the `plan` event's `slowest_test_s` and
   `LivenessRunner`'s own idle threshold) can never disagree even though
   the disk is touched twice. Default: proceeded this way. Flag: a
   stricter reading of "read it back, never re-derive" (thread the raw
   value INTO `compute_expect_next_event_within_s` as a parameter to avoid
   even the double read) was considered and rejected — it would move the
   `max(3x, 15s)` FORMULA into two places instead of one, a worse trade.
   REPORT's A4 section has the full reasoning.
2. **`tests_completed` is computed inside `_run_one` while
   `snapshot.project_root` is still in scope**, not after the `with`
   block closes, even though the events file itself lives at the lane's
   PERSISTENT `liveness_events_dir` and would still be readable later —
   only the PATH to it needs the live `cwd`. Stored on a new
   `_MutantRun.tests_completed` field, matching the existing
   `elapsed_seconds` pattern exactly. No flag — this is the only correct
   place to compute it, not really a judgment call in retrospect, but
   noted for a reviewer tracing the data flow.

**A5:**
3. **"Unknown `--rejudge` id" refuses as `MutationStateError` inside
   `mutation.py`, never as a `runner.py`-level CLI parse refusal** — the
   one rejudge refusal that cannot be validated ahead of time, since the
   current candidate set does not exist until mutation-site collection has
   run against the current tree. Default: proceeded this way (matches the
   pre-existing "stale `source_sha256`" resume refusal's own shape
   exactly). No real alternative was viable; not flagged as contestable.
4. **"Requires `--resume`" and "unknown `--rejudge-outcome` bucket name"
   are validated in BOTH layers** (a clean CLI-level `BAD_LANE_CONFIG` in
   `run_lane`, AND a defensive `ValueError` in `run_mutation` for a direct
   library caller). Default: proceeded this way, matching A1's own
   two-layer precedent for `budget_per_candidate_auto`. Flag: a reviewer
   could reasonably ask why these two specific facts get two checks while
   the "unknown id" fact gets only one — the REPORT's A5 section explains
   the asymmetry (one is knowable ahead of time, the other is not).
5. **`"error"` is accepted as a CLI-level alias for the real bucket name
   `"crashed"`**, resolved in `runner.py` before `mutation.run_mutation`
   ever sees the value — never added to `verdict.MUTATION_BUCKETS` itself.
   Default: proceeded this way (honors the handoff's own literal example
   spelling without touching the schema-adjacent closed vocabulary). Flag:
   a reviewer preferring `"error"` as a REAL fifth bucket spelling would
   need a schema-version discussion this session did not attempt — see
   REPORT's A5 section, design note 3, for the full reasoning either way.
6. **A candidate matching both `--rejudge` and a matching
   `--rejudge-outcome` bucket is dropped once, not twice** (an `or` in one
   `if`, short-circuiting to one `continue`) — proven by an explicit test
   (`test_rejudge_ids_and_rejudge_outcomes_are_a_union`) rather than
   trusted by code inspection alone. No flag — straightforward, but the
   test exists because this is exactly the kind of thing an inspection-
   only review would wave through and a mutation-testing mindset would
   not.

## HOST LOAD — this session's observation

`/proc/pressure/memory` `full avg10` read 0.01–2.91 throughout this
session's own checks (`some avg10` briefly spiked to 6.26 mid-session —
the binding rule is `full avg10 > 5`, which never triggered) — well under
the back-off threshold every time this session checked. `nice -n19`/
`ionice -c3` for every pytest invocation; serial; targeted files, with
combined regression runs (920 tests for A4's coverage cross-check, 228 for
A5's, plus two narrower confirmation runs) rather than the whole
`mutation`/`runner`/`verdict`-named sweep — that sweep is still deferred
to A6, unchanged from every prior session's own note. Two REAL e2e
liveness CLI tests (from session 5, re-run this session as part of the
A4 coverage cross-check) ran their own real subprocess work (~70s
combined, unchanged shape from session 5's own account) — re-check PSI
before re-running them, same as every prior session.

## Self-authored retention prompt (paste into the successor's first turn)

```
Resume RG-55 P7 (assay B091) from BRIEF-6. Re-read the ORIGINAL handoff
(run-gate-WAVE-RG55-P7-HANDOFF.md) in full, then BRIEF-1 through BRIEF-5
(A1, A2, RW-36, A3 sessions 4+5) and this BRIEF-6 (session 6: A4 progress-
stream fields, A5 --rejudge/--rejudge-outcome, both now done) in full,
then REPORT.md's "A4" and "A5" sections and the LOG's 5baf2670/c15f6040
entries -- do not re-derive any of the design/oracle/test mapping recorded
there. Tip is 1eaf5683 on branch assay-liveness (already the worktree's
current branch -- no new worktree add).

A4 AND A5 ARE NOW FULLY, HONESTLY COMPLETE -- every item the handoff ever
named for either is done, green, tests-first, coverage-verified on every
changed line in mutation.py/runner.py/liveness.py/cli.py. Do NOT
re-attempt either; both exist, both are committed.

KEEP as settled, do not re-litigate: baseline_slowest_test_s/
_iter_test_events as the ONE canonical parse compute_expect_next_event_
within_s/baseline_test_events/count_test_events/candidate_events_path all
build on (never hand-roll a second NDJSON parse of a liveness events file
-- extend _iter_test_events's own callers instead); the "test" progress
event's phase: "baseline" field and the RW-33 "baseline only, never per
candidate" rule; tests_completed's None-vs-0 distinction (None = liveness
never ran for this lane, 0 = it ran and genuinely saw no test yet); the
--rejudge/--rejudge-outcome union semantics (an "or", drops once, never
twice); the "error"->"crashed" CLI-only alias (never added to
MUTATION_BUCKETS itself); the two-layer validation for "requires --resume"/
"unknown bucket name" (clean CLI refusal AND a defensive ValueError, both
deliberate); the "unknown rejudge id" refusal living in mutation.py as
MutationStateError (an AssayError), never as a CLI-level parse check (the
current candidate set is not knowable until mutation-site collection runs).

ONE thing left: A6, the close-out. Needs a SINGLE pass over ALL FIVE of
A2/A3(session 4)/A3(session 5)/A4/A5 for CHANGES.md and CONSUMERS.md --
none of the five has touched either file yet. This BRIEF's own "A6
checklist" section has the ordered list (backlog B091->FIXED with every
commit hash, B090->mitigated note, file NEW row B092 from the controller's
mid-session ask (RW-41 on main's CONTROLLER-LOG -- text is in this BRIEF
and/or the dispatching message), CHANGES.md, CONSUMERS.md's several
specific sections PLUS its own stale progress-stream table (stale across
FOUR sessions now, catch it all up at once), README.md if it needs it, the
full mutation/runner/verdict-named regression sweep this whole package has
deferred every session so far, then the REAL registered gate ONCE with a
separate verdict-read step and an untracked launch. Read assay/run-gate.
toml/cmru.toml for the exact gate line FIRST -- no session in this package
has opened either file yet. This is the LAST deliverable in the package;
budget generously for it (it touches five sessions' worth of undocumented
surface) but it is fundamentally a writing/verification pass, not a design
one -- every design decision A6 needs to describe is already made and
recorded in the LOG/REPORT.
```
