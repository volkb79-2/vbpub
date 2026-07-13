# P70-REPORT -- Linear-Time Auto-Window Detector

## Result

`detect_steady_window()` now uses a single reverse pass rather than rebuilding
every entity series for every trailing suffix. It keeps per-eligible-entity
running statistics and removes an entity permanently as soon as a backwards
extension finds an absent, non-finite, or non-gauge value. This makes detector
work O(frames x entities), while the public signature, window criterion, CLI,
recording format, and report JSON remain unchanged.

The selected suffix is still the longest accepted one. At each suffix, the
busiest eligible entity is selected by greatest running arithmetic mean, then
lexical entity key; its population CoV is tested exactly as before. All-zero
series remain stable and zero-mean series with spread remain rejected.

## Numerical Method

P70 uses Welford population variance while extending each suffix backwards.
This was chosen over reverse cumulative `x`/`x^2` sums because subtracting two
large nearly equal values is unstable in the steady-state case. A running total
is retained for the busy-mean comparison and zero-mean behavior. When a CoV or
busy-mean comparison is within 1e-12 of changing the selected result, P70
rebuilds only that ambiguous entity suffix and repeats P62's forward-order
`sum`, mean, squared-deviation sum, and tie-break operations. This preserves the
pinned floating-point decision without returning the normal path to quadratic
work.

The test-only copy of the old detector is the selection oracle. The new detector
agrees with it over 210 generated recordings, two synthetic CoV boundaries
within 1e-12, and a 14-value adversarial recording that made raw reverse Welford
accept at 0.049999999999999996 while P62 rejected at 0.05000000000000001.

## Files Changed

- `src/groop/report.py`: reverse-pass running statistics and detector rewrite.
- `tests/test_report.py`: pre-P70 reference oracle, generated differential
  corpus, near-boundary and accumulation-order regressions, deterministic
  linear-read oracle, pinned pre-P70 CLI bytes, and 2880/5760 scaling test.
- `handoff/reports/P70-LOG.md`: execution log.

## Performance Evidence

Environment: Linux amd64; Python 3.14.6; pytest 9.1.1; temporary clean
`/tmp/p70-gate-venv`; 20 entities, one `ram` gauge per frame.

```text
2880 frames, pre-P70 reference detector: 80.176641 s
2880 frames, P70 reverse pass:            0.068608 s
5760 frames, P70 reverse pass:            0.156531 s
5760 / 2880:                              2.282x
```

The P70 performance test asserts both recordings complete under two seconds
and that doubling frames stays within 2.5x plus a small scheduling allowance.

## Test Evidence

```text
PATH=/tmp/p70-gate-venv/bin:$PATH PYTHONPATH=groop/src \
  python3 -m pytest groop/tests/test_report.py -q -W error
113 passed in 6.13s

PATH=/tmp/p70-gate-venv/bin:$PATH timeout 900 env PYTHONPATH=groop/src \
  python3 -m pytest groop/tests -q -W error
1108 passed, 2 skipped in 146.95s (0:02:26)

python3 -m py_compile groop/src/groop/report.py groop/tests/test_report.py
git diff --check
# both OK
```

## Deviations / Known Gaps

The fast path is O(frames x eligible entities). Numerically ambiguous threshold
or busy-mean comparisons intentionally rebuild the affected suffix in P62's
forward order; this rare compatibility path can add superlinear work on an
adversarial recording engineered to keep every suffix within 1e-12 of a
decision boundary. Default-profile and doubled-profile performance remain well
inside the acceptance bounds, and the read-count oracle pins the ordinary path.
