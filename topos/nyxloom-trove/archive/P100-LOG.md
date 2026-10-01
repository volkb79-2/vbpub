# P100-LOG — Close diagnostic coverage gaps (repair)

## Implementation

Read P100 handoff and 3 diagnostic target modules. Wrote targeted tests.

## Review findings addressed

- **F1** (rules.py net_* confidence): Added `test_confidence_host_network_confidence`
  with `net_rx_bps` host metric and entity network confidence="estimated".
  Verifies branch `elif metric.src == "host" and name.startswith("net_")`.
- **F2-F4** (score.py default_band=None): Replaced two hollow tests with one
  real test that monkeypatches `_INPUTS` with a `ScoreInput(default_band=None)`,
  calls `score_entity`, and asserts exact score/contribution/threshold behavior.
- **F7-F8** (scaling tests): Consolidated into single exact-expected test.
- **F5-F6** (reports): Corrected — no BLOCKED claims, no false statements.

## Gate

| Run | Tests | Exit | Coverage JSON |
|-----|-------|------|---------------|
| Pass 1 | 1972 passed | 0 | /tmp/r1.json |
| Pass 2 | 1972 passed | 0 | /tmp/r2.json |

**Parity:** PASS. **All 3 targets at 100%.** No remaining gaps.
