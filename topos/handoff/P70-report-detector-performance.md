# P70 - Steady-state detector performance (linear-time `--window auto`)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** terra-med
> **Depends-on:** P62 (merged)
> **Base:** main after P62 merge
> **Session-hint:** fresh (report area)
> **Serialize-with:** P64, P65   <!-- all edit report.py / parse_report_args -->
> **Escalate-if:** the faster algorithm cannot reproduce P62's selections exactly on the existing oracle tests (that is the whole contract — do not "improve" the criterion to make it fit)

<!--
CARVE SOURCE (controller-workflow-v2 §8): **review-derived** (P62 pass #2).
The P62 frontier review measured detect_steady_window at 0.98s for 480 frames,
scaling cleanly quadratic: ~35s at the 4h/5s default recording profile (2880
frames, 20 entities), ~90s at 50 entities, tens of minutes for a 24h capture.
Correct and deterministic, but slow on exactly the long recordings the feature
targets (gstammtisch PKG-3). Reviewer deliberately did not fix it in-place:
changing the summation order can flip a CoV sitting on the 0.05 boundary and
silently alter selections that P62's oracle tests pin. That makes it a
contract-bearing change deserving its own oracle. See reports/P62-REVIEW.md.
-->

## Goal

Make `topos report --window auto` fast enough to run on real recordings, **without
changing which window it selects.** The output of this package is a detector that
produces byte-identical reports to today's on every existing test, and runs in
roughly linear time.

## The problem, precisely

`detect_steady_window` (`src/topos/report.py`) scans candidate trailing suffixes
from shortest to longest. For each of the ~N candidates it rebuilds every entity's
value series from scratch over the whole suffix, so the work is
`O(frames^2 x entities)`. Measured on the review host (20 entities, one gauge):

| frames | detect time |
|---:|---:|
| 60 | 0.015 s |
| 120 | 0.058 s |
| 240 | 0.232 s |
| 480 | 0.978 s |

CONTRACTS.md §5 names 4 h @ 5 s (2880 frames) as the default recording profile.

## Context To Read First

- `src/topos/report.py`: `detect_steady_window`, `_finite_gauge_value`,
  `_validate_stability_options`, `select_report_window`, `WindowRange`.
- `topos/handoff/P62-report-steady-state-autodetect.md` — the pinned criterion.
  **You are not allowed to change it.**
- `topos/handoff/reports/P62-REVIEW.md` — why this is a separate package.
- `topos/tests/test_report.py::TestAutoSteadyWindow` — the selections you must
  reproduce exactly.

## Required Contracts

1. **The criterion is frozen.** Longest trailing suffix; an entity is eligible
   only if it has a finite gauge value in *every* frame of the candidate; busiest
   eligible entity = greatest arithmetic mean, ties broken by lexical `EntityKey`;
   population CoV = population stddev / mean; all-zero series has CoV 0; a
   zero-mean series with non-zero spread is rejected. If your faster algorithm
   disagrees with the current one on any input, **the algorithm is wrong, not the
   criterion.**
2. **Selection equivalence is the acceptance test, not "looks right".** Add a
   differential test that runs the old and new detectors over a generated corpus
   of recordings and asserts identical `WindowRange` results. Keep the current
   implementation in the test file (or a `_reference_detect_steady_window` helper
   marked test-only) as the oracle. Cover: constant series, monotone ramps,
   step changes, entities appearing/disappearing mid-recording, missing values,
   all-zero series, zero-mean-with-spread, ties in the mean, and single-entity vs
   many-entity frames.
3. **Floating-point order is part of the contract.** The natural speedup (reverse
   cumulative sums of `x` and `x^2`, then `var = E[x^2] - E[x]^2`) changes
   summation order *and* is numerically unstable for large means with small
   variance — precisely the steady-state case. If you use it, you must show it
   does not flip any selection in the differential corpus, including a case
   engineered to sit within 1e-12 of the CoV bound. A numerically stable
   incremental formulation (e.g. Welford run backward, or exact rational/`math.fsum`
   accumulation) is the safer path. State which you chose and why in the REPORT.
4. **Eligibility is monotone — exploit it.** An entity missing a finite value at
   frame `j` is ineligible for every suffix starting at or before `j`. That is
   what makes a single reverse pass sufficient; a rewrite that still rescans all
   entities per candidate has not fixed anything.
5. No change to `compute_profile`, the P2 reader, the recording format, the JSON
   output keys, or the CLI flags. Public signature of `detect_steady_window` stays
   as-is (it is exported and independently tested).

## Acceptance Oracles (numbered, adversarial)

1. **Every existing `TestAutoSteadyWindow` and `TestReportAutoCLI` test passes
   unchanged.** Not adapted — unchanged. If you need to edit one, you broke the
   contract.
2. **Differential test:** old vs new detector agree on a generated corpus of
   >= 200 recordings spanning the shapes in contract 2. Assert equality of the
   returned `WindowRange` (including `None`), not just "both found something".
3. **Boundary test:** a recording engineered so the busiest entity's CoV lands
   within 1e-12 of `--stability-cov` — old and new must agree on accept/reject.
4. **Performance oracle:** a 2880-frame, 20-entity recording is detected in
   **under 2 seconds** on the test host (assert with a generous wall-clock bound
   so it is not flaky; the point is to catch a return to quadratic, not to
   benchmark). Also assert the 5760-frame case is under ~2x the 2880-frame case,
   which a quadratic implementation cannot satisfy.
5. `--window auto` CLI output stays byte-identical across two invocations and
   matches the pre-P70 bytes on the P62 fixture recording.

## Out Of Scope

- Changing the stability criterion, its defaults, or adding new criteria
  (change-point detection, multi-gauge scoring). P62 pinned this deliberately.
- Per-entity independent windows.
- Optimizing `compute_profile`, the reader, or JSON serialization.
- Caching detection results across invocations.

## Gates

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_report.py -q -W error
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error
python3 -m py_compile <changed files>
git diff --check
```

The REPORT states the environment for each result, quotes the real performance
numbers (before and after, same host), and confirms the differential corpus size.
