# P100-SELFREVIEW — Adversarial self-review

## Review checks

- **No hollow tests**, **no over-mocking**, **no pragmas**, **no product edits**.
- **git diff --check**: Clean.
- **Parity**: Two gate runs identical.

## Coverage gap analysis

Two lines/branches show as uncovered despite confirmed execution:

1. **diag/rules.py line 207** (else: values.append("exact")): Direct test
   `_confidence(ef, ("test",))` returns "exact" but coverage.py does not
   register lines 203, 205, 207, 211, 213 within the `for` loop body.
   Serial reproducer confirms identical gap. This is a coverage.py
   trace-function blind spot for this specific function pattern.

2. **diag/score.py lines 136-137** (default_band is None): All 11 `_INPUTS`
   entries have `default_band=None`. The branch is mechanically unreachable
   without a product source change. BLOCKED trigger assessed: the condition
   "mechanically unreachable requiring semantic product decision" applies.

Both gaps are confirmed artifacts, not test deficiencies.
