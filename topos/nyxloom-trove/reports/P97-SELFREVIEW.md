# P97-SELFREVIEW — Adversarial self-review

## Review against P97-REVIEW.md findings

| Finding | Status | Resolution |
|---------|--------|------------|
| F1 (O1 contract) | ADDRESSED | 9/16 closed. 7 gaps documented. 1 BLOCKED (registry.py unreachable). |
| F2 (Report overclaims) | FIXED | Report now shows accurate 9/16 closed. |
| F3 (Wrong function target) | FIXED | Changed to test metric_from_jsonable with non-list. |
| F4 (Wrong branch in sparkline) | FIXED | Added truncation test (many data points, narrow width). |
| F5 (Missing damon_control test) | DOCUMENTED | Requires Textual app harness — infrastructure gap. |
| F6 (Duplicate registry test) | FIXED | Removed duplicate. |
| F7 (Premature canary-verified) | FIXED | Removed canary-verified from asserts. |
| F8 (Test count) | FIXED | Reports accurate 48 test count. |
| F9 (Weak assertions) | STRENGTHENED | Added exact-value assertions throughout. |

## Adversarial checks

- **No hollow tests**: All tests assert on exact values, exception messages,
  state transitions, or return types.
- **No over-mocking**: Only external effects (subprocess.run) are mocked.
- **No exception swallowing**: All pytest.raises blocks match on text.
- **No scope violations**: All changes in tests/, tools/, config, reports.
- **No pragma: no cover added**.
- **git diff --check**: No whitespace errors.
- **Parity**: Two gate runs identical.

## BLOCKED

registry.py line 279 `if not kept_metrics:` — mechanically unreachable.
Every valid token adds metrics; unknown tokens raise ValueError earlier.
No input can trigger this branch without product semantic change.
