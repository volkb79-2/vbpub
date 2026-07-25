# P101-SELFREVIEW — Adversarial self-review

- **No hollow tests**: Every test asserts exact returned values, states, or exception messages.
- **No over-mocking**: All tests call real semantic functions with constructed _Point inputs.
- **No pragma, no product edit, no sleep, no host proc.**
- **git diff --check**: Clean.
- **Parity**: Two gate runs identical.
- **query/semantics.py at 100%**: 180 stmts, 0 missing lines, 0 missing branches.

All 12 arcs bound to nl -ba lines in report. Each test has fail-before
evidence (removing the target line causes test failure).
