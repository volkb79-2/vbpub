# RG-55 Assay B107 — independent final review

**Verdict: ACCEPT for the repaired Assay code review.** No scoped code blocker
remains. The controller must run the registered exact-tree `tester-unified` gate
on the repair commit before merge; this reviewer was explicitly directed not
to start containers or gates. This verdict does not claim that post-repair gate.

## Identity and scope

- Base: `facbacd2320cae34dd48d2c7673ce19a746168e3`.
- Candidate HEAD before review: `f239216a6644050faa2532dd841604d5675df37c`;
  `git rev-parse HEAD` matched and `git status --porcelain=v1` was empty.
- Reviewed the complete base-to-candidate delta, including the Assay code,
  tests, README, DESIGN-GUIDE, CONSUMERS, and CHANGES. The same range includes
  unrelated generated CHANGES entries in cmru/nyxloom/topos and a nyxloom
  test-race correction; their diff was inspected for integration context and
  no files outside Assay were edited.
- Read the canonical nyxloom DOCTRINE and STANDARD, controller RW-57/RW-357,
  Assay backlog B107, and the named liveness/resource, mutation, runner, and
  focused test files. No Assay trove DOCTRINE sibling exists.

## Pre-fix findings and repairs

1. **False `hung` after a process-tree CPU drop.** A live process-tree CPU
   total can fall when a busy child exits. The monitor compared a reading
   after that drop with an older, larger reading, treating negative growth as
   proof of quiet CPU. A virtual-time combined-axis probe with a
   `session_finish` event, an unavailable resource observation at t=10, CPU
   growth before and after a child exited at t=32, and a 60-second budget
   raised `LivenessHungExpired` before that budget. Expected outcome was the
   ordinary `TimeoutExpired`/`configured-budget-expired` at t=60. The CPU
   sample history now restarts on a decrease; cached-hang validation applies
   the same boundary. The probe now observes ordinary budget expiry at t=60.
2. **Cached-hang false acceptance.** The validator previously trusted the
   top-level idle duration without deriving it from the retained samples,
   and ignored event/output growth inside the claimed idle trace. It could
   accept a cached `hung` that its own trace did not prove. It now derives
   the idle span from the trace endpoints, requires event and output counts,
   and refuses intervening growth. Regression tests cover inflated idle time,
   progress, missing fields, and a CPU drop with insufficient versus complete
   subsequent quiet history.
3. **Malformed counter acceptance.** Comparing a negative PSI or throttle
   counter with itself previously returned `clear`, which could let malformed
   cached evidence pass. Negative counters now return `unknown`; focused
   tests cover both counter families.

README, DESIGN-GUIDE, CONSUMERS, and the Unreleased CHANGES entry describe the
repaired CPU and resume behavior. No config vocabulary or schema version was
changed; old cached `hung` records lacking the now-required progress samples
are intentionally rejected and rerun.

## Independent commands and results

- `/proc/pressure/memory` was read before each local pytest launch; memory
  `full avg10` was 1.56, 0.48, 0.29, 0.33, 0.54, and 0.19 respectively, below the
  prescribed ceiling of 5.
- `nice -n 19 ionice -c 3 /home/vscode/.venv/bin/python -m pytest -q`
  over the five named focused modules: **193 passed** before edits.
- The new combined-axis monitor probe alone: **1 failed as expected** before
  the fix; actual exception was `LivenessHungExpired` rather than the required
  plain `TimeoutExpired`.
- The six focused modules (the five named modules plus
  `test_b106_reuse_and_witness.py`) after the CPU and resume repairs:
  **222 passed**, then **224 passed** after malformed-counter coverage.
- Six targeted cases after the final live-evidence validator assertions:
  **6 passed**. Both live-generated idle and session-finish hang traces are
  accepted by the cached-evidence validator.
- The long virtual CPU-history oracle after preserving monotone readings:
  **1 passed**.
- `git diff --check`: clean. No mutation campaign, daemon, release gate, or
  container was launched.

The controller's reported full-suite and changed-line coverage numbers were
treated as prior context, not as a measurement of this repaired tree. The
registered gate remains the controller's next required verification.
