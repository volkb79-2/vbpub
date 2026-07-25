# P102-REPORT — Close query validation coverage tranche

## Summary

All 22 declared validation lines and 20 branch pairs in query/engine.py
are closed. 19 tests added. Parity confirmed across two runs.

## Target closure

| Metric | Before | After |
|--------|--------|-------|
| Target lines (22) | 22 missing | 0 missing |
| Target branch pairs (20) | 20 missing | 0 missing |

P103 owns the remaining engine.py gaps (projection/execution/cap tranche).

## Tests added

19 tests, 2003 total (1984 baseline + 19). All call real from_dict, _validate,
or _parse_metric_token with exact exception/result assertions.

## Gate

| Run | Tests | Exit | Parity |
|-----|-------|------|--------|
| Pass 1 | 2003 passed | 0 | |
| Pass 2 | 2003 passed | 0 | PASS |

## Per-arc coverage

| Line | Branch | Function | Input | Expected |
|------|--------|----------|-------|----------|
| 138 | [137,138] | from_dict | non-dict | InvalidQueryError |
| 143 | [142,143] | from_dict | no shape | InvalidQueryError |
| 151 | [150,151] | from_dict | metric extra field | UnknownFieldError |
| 153 | [152,153] | from_dict | metric no name | InvalidQueryError |
| 156 | [155,156] | from_dict | metric int spec | InvalidQueryError |
| 159 | [158,159] | from_dict | selector not dict | InvalidQueryError |
| 173 | [171,173] | from_dict | sort as dict | Query built |
| 174 | [173,174] | from_dict | sort as dict | passes |
| 175-177 | [175,176],[175,177] | from_dict | sort extra field | UnknownFieldError |
| 178-179 | [177,178],[177,179] | from_dict | sort no metric | InvalidQueryError |
| 185 | [173,185] | from_dict | sort invalid type | InvalidQueryError |
| 188 | [187,188] | from_dict | caps not dict | InvalidQueryError |
| 212 | [211,212] | _as_int | bool True | InvalidQueryError |
| 219-220 | [218,219] | _parse_metric_token | "ram:rate" | MetricRef(ram, rate) |
| 256 | [255,256] | _validate | bad visibility | InvalidQueryError |
| 260 | [259,260] | _validate | bad on_exceed | InvalidQueryError |
| 269 | [268,269] | _validate | negative max_rows | InvalidQueryError |
| 308 | [307,308] | _validate | bad sort order | InvalidQueryError |
