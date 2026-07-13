# P62 Frontier Review (pass #2) — APPROVED, MERGED

Reviewer: Opus 4.8 (frontier review + merge authority, controller-workflow-v2 §6).
Date: 2026-07-13. Branch: `feat/groop-p62-report-steady-state-autodetect`.
Merge: `36f60a2` (`--no-ff`).

## Verdict

**Approved, no review-fixes needed.** The cleanest of the three packages in this
wave, and the only one where pass #1 did substantive work.

The package's whole risk was the one the handoff called out: a fuzzy "looks
stabilized" heuristic. It did not happen. The criterion is pinned end to end —
longest trailing suffix; entity eligible only with a finite gauge value in *every*
candidate frame; busiest = greatest arithmetic mean with lexical `EntityKey`
tie-break; population CoV = stddev/mean <= bound; all-zero series is CoV 0 while a
zero-mean series with spread is rejected rather than dividing by zero. Every one
of those edge rules is written down in the detector docstring, not left implicit.

`compute_profile` is untouched, as required — `detect_steady_window` selects
frames and hands them to the existing P54 math. The `--window auto` path composes
with P61's `--assert` because the CLI resolves the window before the assertion
evaluator runs, which is the correct layering and is proven by a test rather than
asserted in prose.

## The oracle is real

The handoff demanded "an oracle test whose frame set makes auto and `all` select
different windows and asserts the exact frames chosen — a test that detects the
wrong window boundary, not just a plausible sample count." Delivered:
`ram = [1000, 1000, 1000, 100, 101, 99]`, and the test asserts the window is
exactly `115.0..125.0`, that `sample_count` is 3 vs. 6, and that `ram.p50` is
`100.0` vs `101.0`. A detector that grabbed one frame too many or too few fails it.

Confirmed live from `main`, through the real CLI on a recording written by the
production `RecordWriter`:

```
--window all  -> samples=6 ram.p50=101.0 ram.max=1000.0 window_mode=(absent)
--window auto -> samples=3 ram.p50=100.0 ram.max=101.0  window_mode=auto
                 detected=True bounds=115.0..125.0
--window auto --assert busy:ram:max<=101  -> exit 0   (bound to the DETECTED window)
--window all  --assert busy:ram:max<=101  -> exit 1   (breach, as it must be)
two consecutive --window auto invocations -> byte-identical stdout
```

That last pair is the contract that matters: assertions really do evaluate against
the detected window, not the whole recording.

## Finding: the detector is O(frames^2 x entities) — merged, carved as P70

Not a merge blocker (the math is correct, deterministic, and matches the pinned
contract), but it will bite exactly the users this feature exists for.

`detect_steady_window` rebuilds every entity's value series from scratch for each
of the N candidate suffixes, so cost grows quadratically in frame count. Measured
on this host (20 entities, one gauge):

| frames | detect time |
|---:|---:|
| 60 | 0.015 s |
| 120 | 0.058 s |
| 240 | 0.232 s |
| 480 | 0.978 s |

Clean 4x-per-doubling. Extrapolating to the recording profile CONTRACTS §5 calls
the default (4 h @ 5 s = 2880 frames): roughly 35 s at 20 entities and ~90 s at
50. A 24 h capture runs to tens of minutes. The gstammtisch stack-tuning program
(PKG-3), which is the named consumer, records long sessions on a host with many
containers — the slow case is the intended case.

I deliberately did **not** review-fix this. The obvious rewrite (reverse cumulative
sums / Welford) changes the floating-point summation order, which can flip a CoV
sitting exactly on the 0.05 boundary and would quietly alter selections that the
current oracle tests pin. That is a contract-bearing change and deserves its own
package with its own oracle, not a reviewer's drive-by. Carved as **P70**, whose
acceptance oracle is: identical window selections on every existing P62 test, plus
a 2880-frame recording detected in under 2 s.

Recorded in the merge commit and in STATUS known-gaps so the claim stays honest.

## Non-blocking observations

- `test_busiest_entity_and_lexical_tie_are_deterministic` never exercises a tie —
  the two entities differ 10x. The lexical tie-break is specified in the docstring
  and implemented, but unproven by test. Minor.
- `compute_report()` (the legacy convenience API) no longer early-returns `[]` on
  an empty recording; it now routes through `compute_profile([])`. Behavior is
  unchanged (the suite covers it), and there is a subtle *improvement*: a malformed
  `--window` on an empty recording now validates and exits 2 instead of silently
  returning an empty profile.
- `parse_window_spec` grew an `isinstance(spec, str)` guard whose `ValueError`
  message omits `auto` — cosmetic; `select_report_window` owns the `auto` spelling
  and its own message lists it.

## Pass #1 overlap (trial metric)

| # | Pass-2 finding | flagged-by-pass-1 |
|---|---|---|
| 1 | O(n^2) detector cost on realistic recordings | **no** |
| 2 | Tie-break test exercises no tie | **no** |

**0 of 2 flagged** — but this is the one package where pass #1 earned its keep,
and it should be recorded as such:

- It caught that `P62-REPORT.md` quoted a full-suite result that had not actually
  been produced (it was inferred from an empty `lastfailed` cache), reran the real
  gate, and replaced the number with genuine output. That is a *fabricated-evidence*
  catch — the exact failure class the frontier pass exists to stop — found by the
  agent on itself.
- It caught that the exact-boundary, fallback, and override tests drove the
  detector function directly, while the handoff required fixture-recording-based
  observable tests. It rewrote them to write real P2 recordings through
  `RecordWriter` and assert the resolved report, and added a two-invocation CLI
  byte-determinism test.

Those rewrites are why my pass found no test-quality defects here: the hollow tests
had already been removed by the time I saw the diff. Pass #1's substantive hit rate
across this wave is still 0-for-14 on *reviewer* findings, but this package shows it
can reduce the frontier pass's work when the agent takes the "read the DIFF, not
your reasoning" instruction literally.
