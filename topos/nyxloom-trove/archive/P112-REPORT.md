# P112 report — repaired, exact guided squeeze coverage

## Result

`actions/squeeze.py` has exact 100% statement and branch coverage in two clean,
immutable, complete parallel gate runs. Each run passed 2,156 cases and covered
all six changed executable lines. Multi-step runs now apply every measured
`memory.high`; generic failure reliably returns its typed error after summary
and restoration; overbroad exception catches are removed.

## Evidence

Both accepted runs produced:

```text
squeeze.py missing_lines=[]
squeeze.py missing_branches=[]
target_record_sha256=c33dd2a3559cd9214ff5ebb13159eaaa8575ed811218c777af173195ff5df672
```

| Run | Pytest | Changed-line floor | Exit |
| --- | --- | --- | ---: |
| 1 | 2,156 passed in 63.19s | 6/6, 100% ≥ 100% | 0 |
| 2 | 2,156 passed in 61.77s | 6/6, 100% ≥ 100% | 0 |

Twenty-one new cases collect as twenty-one cases: 2,135 plus 21 equals 2,156.

## Behavioral and safety coverage

The key oracle proves exact applied writes `2`, `1`, `max` and complete step
records at 2 and 1. Signal tests inject SIGTERM exit and prove restoration
without invoking a real exit. Log/audit/root/measurement failures have complete
results and effect records. Operator interrupts propagate outside the explicit
in-loop interruption contract. No test invokes host cgroupfs or real signals.
