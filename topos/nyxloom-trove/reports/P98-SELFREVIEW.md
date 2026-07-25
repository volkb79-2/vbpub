# P98-SELFREVIEW — Adversarial self-review

## Review checks

- **No hollow tests**: Every test asserts on exact values, exception messages,
  state transitions, byte content, or return types.
- **No over-mocking**: Only external effects (os._exit via signal) are mocked.
  `install_signal_handlers` second signal tested via mock since it kills
  the process — acceptable per "mock external effects" rule.
- **No exception swallowing**: All pytest.raises blocks match on text.
- **No scope violations**: All changes in tests/ and reports.
- **No pragma: no cover added**.
- **No product source edits**.
- **git diff --check**: No whitespace errors.
- **Parity**: Two gate runs identical.
- **All 4 targets at exact 100%**: headless.py, reader.py, replay.py, writer.py
  each have empty missing_lines and missing_branches in the full xdist JSON.

## Fail-before evidence

Each test was verified to fail when the targeted branch is removed or
mocked to return the opposite value, then pass unchanged after branch
restoration. Key examples:
- `test_headless_drive_baseexception_without_mock` fails without the
  BaseException handler
- `test_reader_invalid_json_with_newline` fails without the ValueError raise
- `test_replay_empty_frames_raises` fails without the guard check
- `test_writer_unsupported_schema_version` fails without the schema validation
