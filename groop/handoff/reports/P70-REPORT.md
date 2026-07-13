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
is retained for the busy-mean comparison and zero-mean behavior. The test-only
copy of the old detector is the selection oracle: the new detector agrees with
it over 210 generated recordings, including a CoV boundary placed within 1e-12
of the configured threshold.

## Files Changed

- `src/groop/report.py`: reverse-pass running statistics and detector rewrite.
- `tests/test_report.py`: pre-P70 reference oracle, generated differential
  corpus, near-boundary test, and 2880/5760-frame scaling regression test.
- `handoff/reports/P70-LOG.md`: execution log.

## Performance Evidence

Environment: Linux amd64; Python 3.14.6; pytest 9.1.1;
`/workspaces/vbpub/.venv/bin/python`; 20 entities, one `ram` gauge per frame.

```text
2880 frames, pre-P70 reference detector: approximately 28.1 s
2880 frames, P70 reverse pass:             0.061770 s
5760 frames, P70 reverse pass:             0.119430 s
5760 / 2880:                               1.933x
```

The P70 performance test asserts both recordings complete under two seconds
and that doubling frames stays within 2.5x plus a small scheduling allowance.

## Test Evidence

```text
PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python -m pytest \
  groop/tests/test_report.py -q -W error -k 'not zst_without_zstandard'
109 passed, 1 deselected in 4.91s

PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python -m pytest \
  groop/tests/test_report.py -q -W error -k 'SteadyWindowDetectorPerformance'
4 passed, 106 deselected in 1.52s

timeout 900 env PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python -m pytest \
  groop/tests -q -W error -k 'not zst_without_zstandard'
# completed successfully

python3 -m py_compile groop/src/groop/report.py groop/tests/test_report.py
git diff --check
# both OK
```

The exact mandated pytest command was also run with this environment's Python
and exposes one existing environment-sensitive failure:
`test_zst_without_zstandard_exits_2` expects zstandard not to be installed,
but this virtualenv provides it. It is unrelated to P70; all report tests that
do not encode that absent-dependency assumption pass under `-W error`.

## Deviations / Known Gaps

None in implementation scope. The only incomplete gate is the pre-existing
zstandard-availability expectation described above.
