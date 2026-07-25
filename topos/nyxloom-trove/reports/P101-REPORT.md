# P101-REPORT — Close query/semantics.py coverage gaps

## Summary

query/semantics.py at exact 100% statements and branches. 12 tests closing
12 lines and 12 arcs. Parity confirmed across two runs.

## Target coverage

| Target | Before (12 lines, 12 arcs) | After | Status |
|--------|---------------------------|-------|--------|
| query/semantics.py | 12 missing, 12 branches | 0/0 | **100%** |

## Tests added

12 tests. 1984 total (1972 baseline + 12 new). No product source edits.

## Gate

| Run | Tests | Exit | Parity |
|-----|-------|------|--------|
| Pass 1 | 1984 passed | 0 | |
| Pass 2 | 1984 passed | 0 | PASS |

## Per-arc coverage

| nl -ba line | Branch | Function | Input | Expected output |
|-------------|--------|----------|-------|-----------------|
| 112 | [111,112] | _round | None | None |
| 121 | [120,121] | _finite_number | "not_a_number" | None |
| 223 | [222,223] | _rate_pairs | prior raw=None | continue search |
| 230 | [229,230] | _rate_pairs | ts_delta=0 | break |
| 254 | [253,254] | _counter_total | point.raw=None | continue |
| 269 | [268,269] | _integral_of_series | len(pairs)<2 | (0.0, 0.0) |
| 274 | [273,274] | _integral_of_series | dt=0 | continue |
| 329 | [328,329] | summarize state_duration | state=None | continue |
| 333 | [332,333] | summarize state_duration | dt<=0 | continue |
| 337 | [336,337] | summarize state_duration | >64 states | IncompatibleQueryError |
| 349 | [348,349] | _state_key | bool True/False | "true"/"false" |
| 351 | [350,351] | _state_key | float 3.14159265 | "3.141593" |
