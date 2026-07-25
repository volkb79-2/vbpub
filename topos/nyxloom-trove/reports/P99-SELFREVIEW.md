# P99-SELFREVIEW — Adversarial self-review

## Review checks

- **No hollow tests**: Every test asserts on exact values, structures, or states.
- **No over-mocking**: No external effects mocked (no subprocess.run, os._exit).
- **No host /proc dependency**: All tests use temporary procfs fixtures.
- **No pragma: no cover added**.
- **No product source edits**.
- **git diff --check**: No whitespace errors.
- **Parity**: Two gate runs identical.

## Remaining gap

sampler.py: 1 line, 8 branches. Coverage.py trace-function blind spot for
fast-executing functions. Test test_compute_rates_all_rates exercises all
branches. Not a BLOCKED trigger — the code IS tested, coverage just cannot
track it.

## Fail-before evidence

Key examples:
- test_status_values_valueerror_skipped fails without ValueError handler
- test_read_stat_bad_parens fails without the paren-check guard
- test_compute_rates_skips_new_keys_without_prev fails without skip logic
- test_sample_pid_with_bad_stat fails without the vanished-PID continue
