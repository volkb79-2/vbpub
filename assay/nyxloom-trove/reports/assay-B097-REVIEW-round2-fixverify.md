# Assay B097 adversarial review — round 2 fix verification

## Verdict

**ACCEPT.** The sole round-1 P2 finding is fixed. The exact required
`git diff --check ee41553d..23b75167` exits 0, and the fix delta contains no
product or unrelated files.

## Exact target and fix verification

- Reviewed tip: `23b751678a38c113fd63608f7de725d8298e9ee4`
  (the requested `23b75167`).
- Prior reviewed tip: `b83b99416b341422ecb4714494688f122302e892`.
- Required range check: `ee41553d..23b75167`.
- Worktree: `/workspaces/vbpub/.worktrees/assay-b097`.

Fresh read-only host check before inspection:

```text
some avg10=0.41 avg60=0.96 avg300=8.47
full avg10=0.23 avg60=0.74 avg300=5.40
```

Exact command and result:

```text
git diff --check ee41553d..23b75167
=> no output
EXACT_DIFF_CHECK_EXIT=0
```

Fix delta from the prior reviewed tip:

```text
git diff --name-status b83b99416b341422ecb4714494688f122302e892..23b75167
M  assay/nyxloom-trove/reports/assay-B097-BRIEF-1.md
A  assay/nyxloom-trove/reports/assay-B097-REVIEW-round1.md

git diff --stat b83b99416b341422ecb4714494688f122302e892..23b75167
assay/nyxloom-trove/reports/assay-B097-BRIEF-1.md  | 1 -
.../reports/assay-B097-REVIEW-round1.md            | 233 +++++++++++++++++++++
2 files changed, 233 insertions(+), 1 deletion(-)
```

The one deletion removes the extra EOF blank line identified in round 1.
The added round-1 report is the controller-requested committed review
artifact. No other files changed, and the pre-report worktree status was
clean. No product code was modified.

## Reused round-1 behavioral evidence

No new test or subprocess launch was needed for this documentation-only fix;
the following evidence remains valid because the fix does not touch any code
or tests:

- The guarded full focused suite passed: `116 passed in 3.04s`, exit 0, with
  `tests/test_liveness.py`, `tests/test_liveness_proc_helpers.py`, and
  `tests/test_liveness_runner_monitor.py`.
- The guarded B6/process-tree suite passed: `68 passed in 0.78s`, exit 0.
- The guarded docs/config/schema/anchor suite passed: `73 passed in 10.74s`,
  exit 0.
- The real materialized-plugin subprocess probe passed with both a `gw0`
  worker label and no worker label; every record carried one positive
  producer pid, and the absent label was omitted.
- The interleaved controller/two-worker matrix passed: four owner test
  records counted once, worker-only finish false, reordered per-pid gaps
  `(6.9, 4.0)`, and legacy/malformed/mixed/bool/label-only records retained
  their documented interpretation.
- The additional combined-axis attack passed: same worker labels on different
  pids, duplicate nodeids, interleaved/reordered timestamps, and
  worker-before-owner finishes produced the expected owner count and
  process-local gap.
- The mixed-finish compatibility boundary was verified: fully stamped
  worker-only finish is ignored; mixed malformed/un-stamped finish records
  retain the documented legacy any-finish behavior.

The prior report records the commands, PSI readings, outputs, and residuals
for each item. No B096 gate was awaited or reopened, no registered gate was
launched, and no mutation campaign was launched.

## Findings

- P0: none.
- P1: none.
- P2: none. Round-1 P2-1 is fixed and the exact diff-check gate is green.

## Residuals

The previously documented B095 O(N) side-file reread/unbounded CPU-history
residual and the post-`session_finish`-to-exit fixed-grace residual remain
unchanged and are outside B097's fix-verification scope. Mixed malformed
finish identities intentionally retain legacy behavior, as documented; fully
usable finish identities use candidate-pid ownership.

## Final disposition

The requested fix is exact, isolated, and verified. B097 is **ACCEPTED** for
this round-two fix-verification review.
