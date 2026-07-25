# P99-SELFREVIEW — Adversarial self-review

## Review checks

- **No hollow tests**: Every test asserts on exact values, structures, exception messages, or state transitions.
- **No over-mocking**: No external effects mocked. All tests use temporary procfs fixtures.
- **No host /proc dependency**: Verified — all procfs-dependent tests use tmp_path trees.
- **No pragma: no cover added**.
- **No product source edits**.
- **git diff --check**: No whitespace errors.
- **Parity**: Two gate runs identical.
- **All 3 targets at exact 100%**: collect/procs.py, procs/procfs.py, procs/sampler.py each have empty missing_lines and missing_branches in the full xdist JSON.

## Fail-before evidence

Each test was verified to fail when the targeted branch is removed or the specific condition is inverted.

Key examples:
- `test_status_values_valueerror_skipped` fails without the ValueError handler
- `test_read_stat_bad_parens` fails without the paren-check guard
- `test_compute_rates_degraded_baseline` fails if any delta is computed from None fields
- `test_frame_source_returns_with_history_and_evicted` fails without the wrapping method
- `test_omitted_reasons_non_empty` fails when no PIDs exceed the candidate budget
- `test_warm_up_new_pid_not_in_prev` fails when all retained keys are in _prev
