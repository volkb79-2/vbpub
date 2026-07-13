# P70 review (frontier pass #2) - Steady-state detector performance

**Verdict: MERGE.** No review-fixes needed. This is the first package in the
trial where pass #1 caught the substantive bug before pass #2 saw the diff.

## What the package had to prove

P70's entire contract is *selection equivalence*: make `--window auto` linear
without changing which window it picks. A faster detector that is 1 ULP off on a
CoV sitting at the `0.05` bound silently changes reports, and P62's oracle tests
would not necessarily catch it. So the review question is not "is it faster"
(obviously) but "is it the same function".

## Verification performed

**Independent differential fuzz.** I did not re-use the corpus in the package's
own test file, because that corpus is the implementer's transcription of the old
detector, and a transcription bug would make the oracle agree with the code for
the wrong reason. Instead I loaded `detect_steady_window` straight out of
`main:groop/src/groop/report.py` (the merged P62 code) and compared it against
P70's, over 4000 randomized recordings: constant / near-boundary / noisy / ramp /
step / all-zero / zero-mean-with-spread / gappy / 1e33-magnitude / tied-mean
series, 1-5 entities, 3-21 frames, `stability_cov` in {0, 0.01, 0.05, 0.1},
`min_frames` in {2, 3, 5}.

```
cases=4000 mismatches=0
```

**Probe of the compatibility guard.** P70 keeps a fast Welford path and falls
back to P62's exact forward summation order whenever a decision sits within
`_FLOAT_DECISION_TOLERANCE = 1e-12` of flipping. That tolerance is a fixed
constant, but the forward-vs-reverse summation divergence it must cover grows
with frame count -- and this feature exists precisely for long recordings (the
carve cites 24h captures). A worst-case error bound (`n * eps * mean`) reaches
~3.8e-12 at 17280 frames, which would exceed the guard. So I measured the actual
divergence with adversarial (sorted / reverse-sorted) orderings:

| frames | worst measured relative divergence | vs the 1e-12 guard |
|---:|---:|---|
| 2880 (4h @ 5s, the default profile) | 4.4e-15 | 230x margin |
| 17280 (24h @ 5s) | 1.0e-14 | 97x margin |
| 100000 | 2.5e-14 | 40x margin |

The guard is comfortably sized; the worst-case bound is not attained by real
float sequences. **Not a defect** -- recording it because the constant is load-
bearing and its safety margin was not previously stated anywhere.

## Findings

1. **`flagged-by-pass-1: yes` - reverse-Welford boundary flip.** The one real
   defect in this package. Raw reverse Welford accepted a window at CoV
   `0.049999999999999996` where P62 rejected at `0.05000000000000001`, changing
   the selected window on a 14-value series. Pass #1 found it, fixed it (forward-
   order recompute inside the tolerance band), and pinned the exact disagreeing
   series as a regression oracle. I re-derived the same class of failure
   independently and could not produce a surviving instance: fix confirmed.

2. **`flagged-by-pass-1: no` - README work-package row not updated.** P70 leaves
   its row at `Queued`. Not the implementer's miss (the handoff has no Docs
   section, unlike P71's); folding into the reviewer's merge-hygiene commit on
   `main` along with the ROADMAP `:done:` marker.

No other findings. The mechanism oracle (`test_finite_gauge_reads_are_linear`:
exactly `frames x entities` gauge reads, vs 50,799 for the old detector) is a
real complexity guard, not a hollow test -- it fails on a revert, which the
differential and boundary tests would not.

## Note for the pass-#1 trial metric

The workflow doc's deciding log (2026-07-13) records pass #1 as running "at 0% on
substantive findings across every package reviewed so far". **P70 is the first
counter-example** -- but read it carefully before updating the prior. The carve
had already named this exact failure mode: contract 3 told the implementer that
reverse cumulative sums "change summation order *and* are numerically unstable
for large means with small variance", and demanded a case "engineered to sit
within 1e-12 of the CoV bound". Pass #1 did not discover an unknown risk; it
executed a check the carver had pre-identified.

The honest reading is that this is evidence for *"tightening contracts beats
upgrading the model"* (README, handoff authoring guide) rather than evidence that
same-session self-review finds substantive bugs on its own. A carve that names
the failure mode can be checked by the same session that wrote the code; a carve
that does not, still cannot.

## Gates (clean venv, Python 3.14.6, pytest 9.1.1, no zstandard extra)

Environment note: this is the same venv whose numbers the P60 reviewer recorded
(`1101 passed, 2 skipped` on main), so the figures are comparable across waves.
The agent-env greens in the REPORT were not trusted; everything below is my rerun.

```
main (baseline, pre-merge)      1101 passed, 2 skipped in 137.20s
P70 branch                      1108 passed, 2 skipped in 138.25s   (+7 P70 tests)
differential fuzz vs main       4000 cases, 0 mismatches
py_compile (report.py, test_report.py)   OK
git diff --check                OK
```
