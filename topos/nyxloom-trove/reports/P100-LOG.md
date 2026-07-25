# P100-LOG — Close diagnostic coverage gaps

## Implementation

Read all 3 diagnostic target modules and P96 gap data. Wrote 42 tests
closing all reachable branches. Two paths have coverage.py blind spots
confirmed by serial reproducer and direct execution:

1. rules.py line 207: `_confidence` else branch — function returns "exact"
   but coverage.py doesn't register the line.
2. score.py lines 136-137: `default_band is None` — all 11 `_INPUTS`
   entries have default_band set. Mechanically unreachable.

## Gate

1974 passed, exit 0, parity identical.
