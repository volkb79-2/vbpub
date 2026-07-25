# P102-REPORT — Close query validation coverage tranche

## Summary

All 22 declared validation lines and 20 branch pairs closed in both
xdist runs. 18 tests. 2002 total (1984 baseline + 18). Parity confirmed.

## Before/after literal residual sets

### 22 target lines: {138,143,151,153,156,159,173,174,175,176,177,178,179,185,188,212,219,220,256,260,269,308}

| Baseline | Run 1 residual | Run 2 residual |
|----------|---------------|---------------|
| ALL 22   | ∅ | ∅ |

### 20 target branch pairs:
{(137,138),(142,143),(148,156),(150,151),(152,153),(158,159),(171,173),(173,174),(173,185),(175,176),(175,177),(177,178),(177,179),(187,188),(211,212),(218,219),(255,256),(259,260),(268,269),(307,308)}

| Baseline | Run 1 residual | Run 2 residual |
|----------|---------------|---------------|
| ALL 20   | ∅ | ∅ |

## Per-arc binding (nl -ba, input, exact output)

| Line | Arc | Function | Input | Expected |
|------|-----|----------|-------|----------|
| 138 | [137,138] | from_dict | non-dict | InvalidQueryError("must be a mapping") |
| 143 | [142,143] | from_dict | no shape | InvalidQueryError("requires a 'shape'") |
| 151 | [150,151] | from_dict | metric extra field | UnknownFieldError |
| 153 | [152,153] | from_dict | metric no name | InvalidQueryError("requires a 'name'") |
| 156 | [148,156] | from_dict | metric int spec | InvalidQueryError("invalid metric spec") |
| 159 | [158,159] | from_dict | selector not dict | InvalidQueryError("must be a mapping") |
| 173 | [171,173] | from_dict | sort as dict | Query built, sort.metric='ram' |
| 174 | [173,174] | from_dict | sort as dict | Query built |
| 175-177 | [175,176],[175,177] | from_dict | sort extra field | UnknownFieldError |
| 178-179 | [177,178],[177,179] | from_dict | sort no metric | InvalidQueryError("requires a 'metric'") |
| 185 | [173,185] | from_dict | sort invalid type | InvalidQueryError("invalid sort spec") |
| 188 | [187,188] | from_dict | caps not dict | InvalidQueryError("must be a mapping") |
| 212 | [211,212] | _as_int | bool True | InvalidQueryError("must be an integer") |
| 219-220 | [218,219] | _parse_metric_token | "ram:rate" | MetricRef(ram, rate) |
| 256 | [255,256] | _validate | bad visibility | InvalidQueryError("unknown visibility") |
| 260 | [259,260] | _validate | bad on_exceed | InvalidQueryError("unknown caps.on_exceed") |
| 269 | [268,269] | _validate | negative max_rows | InvalidQueryError("non-negative integer") |
| 308 | [307,308] | _validate | bad sort order | InvalidQueryError("unknown sort order") |

## Tests

18 test functions (19 removed 1 duplicate). All call real from_dict, _validate,
or _parse_metric_token. No assertion-free tests, no non-None-only checks.

## Gate

| Run | Tests | Exit | Engine parity |
|-----|-------|------|---------------|
| Pass 1 | 2002 | 0 | Baseline identical |
| Pass 2 | 2002 | 0 | Run1=Run2 PASS |

P103 owns remaining engine.py gaps (17 lines, 19 branches, line 392+).
