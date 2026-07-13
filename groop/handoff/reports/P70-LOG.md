# P70 Work Log

## Context

- Branch: `groop-p70-report-detector-performance`.
- Package: P70 -- Steady-state detector performance.
- Objective: make `groop report --window auto` linear in frames and entities
  without changing selected windows.

## Timeline

```text
2026-07-13 UTC
- Read the complete P70 handoff, the P62 handoff/review, detector source, and
  existing P62 report/CLI tests.
- Replaced the quadratic candidate rebuild with one reverse pass. Each entity
  eligible at the final frame has backwards Welford statistics until its first
  missing/non-finite value permanently removes it from consideration.
- Added a test-only pre-P70 reference detector, a 210-recording differential
  corpus, two CoV-boundary cases within 1e-12, and 2880/5760-frame scaling
  coverage.
- Focused report tests excluding one environment-sensitive zstandard test:
  109 passed, 1 deselected in 4.91s. P70-specific tests: 4 passed in 1.52s.
- Compiled changed files and ran git diff --check successfully.
```

## Decisions

- Use backwards Welford updates for population variance. It avoids the
  cancellation in `E[x^2] - E[x]^2` for large, nearly constant gauge values.
- Retain a running total for busy-entity mean comparison and zero-mean handling;
  lexical entity-key ordering remains the tie breaker.
- Keep the old implementation only in `tests/test_report.py` as the P70 oracle.

## Blockers

None.

## Validation

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

## Self-Review Pass 1

```text
2026-07-13 UTC
- Mechanically reviewed 15108c4 against all five required contracts, all five
  acceptance oracles, and every gate in the P70 handoff.
- Finding (critical): reverse Welford did not preserve P62's floating-point
  decision at the threshold. An adversarial 14-value series produced old CoV
  0.05000000000000001 and reverse-Welford CoV 0.049999999999999996, changing
  the selected window. Fixed by recomputing numerically ambiguous CoV and
  busy-mean decisions in P62's original forward operation/summation order.
  Added the exact disagreeing series as a regression oracle.
- Finding (major): acceptance oracle 5 only checked two new invocations against
  each other; it did not pin the pre-P70 bytes. Added a literal byte-for-byte
  expected CLI result for P62's exact-tail recording.
- Finding (major): the original report mislabeled the venv interpreter as
  Python 3.14.6 (it is 3.13.5), gave no real full-suite summary, excluded an
  environment-sensitive test, and called an approximately observed 28.1 s a
  measurement. Replaced this evidence with verbatim gates in a clean temporary
  Python 3.14.6 environment containing pytest and the declared groop dependency,
  without the optional zstandard extra.
- Hollow-test audit: the differential and boundary tests would still pass if
  detect_steady_window were reverted to the reference implementation. They are
  valid selection-equivalence oracles but are hollow as complexity guards.
  Added a deterministic gauge-read mechanism oracle: P70 performs exactly 840
  reads for 120 frames x 7 entities; the pre-P70 implementation performed
  50,799. The existing 2880-frame under-two-second test also fails a revert
  (the reference implementation measured 80.176641 s on this gate host).
- Existing P62 TestAutoSteadyWindow and TestReportAutoCLI tests remain unchanged.
```

Self-review validation environment: Linux amd64, Python 3.14.6, pytest 9.1.1,
temporary `/tmp/p70-gate-venv`; groop installed editable with declared
dependencies and without the optional zstandard extra.

```text
PATH=/tmp/p70-gate-venv/bin:$PATH PYTHONPATH=groop/src \
  python3 -m pytest groop/tests/test_report.py -q -W error
113 passed in 6.13s

PATH=/tmp/p70-gate-venv/bin:$PATH timeout 900 env PYTHONPATH=groop/src \
  python3 -m pytest groop/tests -q -W error
1108 passed, 2 skipped in 146.95s (0:02:26)

Same-host detector measurements, 20 entities and one ram gauge:
pre-P70 reference, 2880 frames: 80.176641 s
P70, 2880 frames:              0.068608 s
P70, 5760 frames:              0.156531 s (2.282x)
```
