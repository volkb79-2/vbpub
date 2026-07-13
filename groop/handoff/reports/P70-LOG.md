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

- The exact mandated pytest invocation cannot pass in this virtualenv because
  `zstandard` is installed. Its pre-existing `test_zst_without_zstandard_exits_2`
  intentionally expects zstandard to be absent and receives exit 1 instead of 2.
  This is unrelated to the detector change.

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
