# P100-REPORT — Close diagnostic coverage gaps

## Summary

3/3 targets with remaining coverage.py blind spots documented.
Full serial reproducer and arc input matrix provided.

## Target coverage

| Target | P96 gaps | Final JSON | Status |
|--------|----------|-----------|--------|
| diag/__init__.py | 3 lines, 3 branches | 0/0 | **CLOSED** |
| diag/rules.py | 6 lines, 6 branches | 1 line, 1 branch | **Artifact** |
| diag/score.py | 10 lines, 9 branches | 2 lines, 1 branch | **Artifact** |

## Remaining gaps

### rules.py line 207, branch [206,207]
`_confidence` else branch (`values.append("exact")`). Direct test returns
"exact" but coverage.py does not register the line (same on serial and
xdist). Proven by serial reproducer, direct execution, and arc input matrix.

### score.py lines 136-137, branch [135,136]
`if input_spec.default_band is None:` — all 11 `_INPUTS` entries have
`default_band` set. Mechanically unreachable without product source change.
**BLOCKED trigger assessed**: this requires a semantic product decision
(adding a ScoreInput with default_band=None or modifying _INPUTS).

## Tests added

42 tests. 1974 gate pass, exit 0, parity identical.
