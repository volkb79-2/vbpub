# Assay B097 adversarial review handoff

Review the exact final B097 tip on branch `assay-b097` in
`/workspaces/vbpub/.worktrees/assay-b097`, after the controller's test-only
import repair. The review target is the complete B097 diff from
`ee41553d` through the tip, including the implementation, tests, docs,
backlog, brief, report, and this handoff. Do not modify product code, merge,
release, or launch a mutation campaign. Use Luna xhigh.

## Read first

1. `nyxloom-trove/4-backlog.md`, B097 and B091's liveness contract.
2. `src/assay/liveness.py`, especially `_PLUGIN_SOURCE._append`,
   `_iter_events`, `_selected_test_events`, `_iter_test_events`,
   `baseline_event_gaps`, `_read_events_progress`, and
   `LivenessRunner._monitor`.
3. `tests/test_liveness.py`, `tests/test_liveness_proc_helpers.py`, and
   `tests/test_liveness_runner_monitor.py`, including the B6-a progressing
   tail controls.
4. `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P7-REVIEW-round2.md`
   and `run-gate-project/nyxloom-trove/reports/run-gate-WAVE-RG55-P7-REVIEW-HANDOFF.md`.
5. The B097 implementation report and the three adopter-facing documents.

## Contract to attack

The materialized plugin must stamp each record with its producer's positive
integer `os.getpid()` and must include non-empty `PYTEST_XDIST_WORKER` only as
descriptive metadata. It must not trust caller-supplied identity or mutate the
caller record. New fully stamped records use the pid that owns the first
`session_start` for test-event selection; a real controller plus two workers
with four tests must count four owner records, not worker duplicates. The
monitor may arm the session-finish grace only for the candidate's own stamped
pid. Baseline gaps must be sorted and measured per pid, then take the worst
process-local gap; merged timelines must not hide a slow worker. Missing,
boolean, non-positive, non-integer, and mixed pids preserve the documented
legacy interpretation without dropping evidence or inventing identity.

The B6-a idle conjunct remains binding: a finish grace expires only after the
candidate process tree is idle for the complete grace. `/proc` failure remains
progress/growth, and budget, schema, scoring, and public liveness policy are
unchanged. B095's O(N) side-file reread is a disclosed residual, not a review
blocker unless the change accidentally worsens the contract beyond the report.

## Required live review probes

Use fresh memory PSI checks before any process launch and serial
`nice -n 19 ionice -c 3` commands. Inspect the generated plugin source and run
a real materialized-plugin subprocess probe. Exercise a synthetic interleaved
controller/two-worker file with four owner tests and worker/controller finish
records; vary event order and timestamps so a merged or file-order algorithm
would give a different result. Probe legacy, malformed, mixed, bool, and
worker-label-only records. Run focused tests if PSI permits. Check the docs'
examples, vocabulary, anchors, and the no-schema-change boundary.

Reviewers must add at least one attack not named by the implementer's tests,
record every command and result, and write
`nyxloom-trove/reports/assay-B097-REVIEW-round1.md` with an unambiguous
`ACCEPT` or `REJECT`, ranked P0/P1/P2 findings, exact tip, and residuals.
Acceptance requires no P0/P1/P2 defect and no unverified claim presented as
green. If a defect is found, leave the report as REJECT and do not repair it;
the controller will decide whether a same-reviewer fix-verification round is
needed.
