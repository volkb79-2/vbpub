# P100-SELFREVIEW — Adversarial self-review

## Review checks

- **No hollow tests**: Every test asserts on exact values, finding presence,
  or exact contribution breakdowns.
- **No over-mocking**: monkeypatch replaces `_INPUTS` data (module-level config)
  then calls unmocked `score_entity`. Not mocking the function under test.
- **No pragmas**, **no product edits**, **no sleeps**, **no host proc**.
- **git diff --check**: Clean.
- **Parity**: Two gate runs identical.
- **All 3 targets at exact 100%**: empty missing_lines and missing_branches.

## Fail-before evidence

Each test was verified to fail when its targeted branch is removed.
Key examples:
- `test_confidence_host_network_confidence` fails without `elif metric.src == "host"` branch
- `test_score_entity_default_band_none` fails without the `if default_band is None:` path
- `test_score_raw_sum_exceeds_100_scales_to_exact` fails without scaling logic
