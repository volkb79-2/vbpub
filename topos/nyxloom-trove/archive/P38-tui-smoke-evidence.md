# P38 - TUI Smoke Evidence Harness

## Goal

Add a rootless, repeatable Textual UI smoke evidence command to close the
remaining release-confidence gap between collector-only evidence (P33/P35) and
the actual TUI entry point.

This should exercise the existing `topos --ui-smoke` path from outside the UI
process, collect wall/CPU/RSS measurements for the child process, and emit
paste-friendly JSON/text suitable for `MEASUREMENTS.md`.

## Workflow

Follow `topos/README.md` "Workflow protocol" exactly.

- Branch: `feat/topos-p38-tui-smoke-evidence`
- Worktree: `.worktrees/-topos-p38-tui-smoke-evidence`
- Branch from local `main`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P38-LOG.md` updated while working
- Finish with `topos/handoff/reports/P38-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `topos/README.md`
- `topos/CONTRACTS.md`
- `topos/TUI-SPEC.md` sections 3.0, 6.1, 6.4, 9
- `topos/src/topos/acceptance.py`
- `topos/src/topos/cli.py`
- `topos/src/topos/ui/app.py`
- `topos/tests/test_acceptance.py`
- `topos/tests/test_record.py`
- `topos/MEASUREMENTS.md`
- `topos/docs/OPERATIONS.md`

## Functional Requirements

Extend `python -m topos.acceptance` with a `tui-smoke` subcommand.

Expected behavior:

- Run the existing CLI/UI smoke path in a child process, for example:
  `python -m topos.cli --replay PATH --step --ui-smoke`.
- Default replay path should be the deterministic fixture
  `topos/tests/fixtures/frames/gstammtisch-once.jsonl` when the command is run
  from the repository checkout. Provide `--replay PATH` override.
- Support `--config PATH` and `--profile NAME` pass-throughs so the command can
  exercise default tree view, configured container view, and custom profiles.
- Support `--timeout-s FLOAT`, default bounded and test-friendly.
- Emit deterministic JSON with `--json` / `--pretty-json`, and concise text
  otherwise.
- Capture:
  - exit code;
  - stdout/stderr snippets;
  - parsed UI smoke line fields where practical (`frames`, `view`, `profile`);
  - wall time;
  - child user/sys CPU;
  - child max RSS.
- Exit `0` when the UI smoke child exits `0` and output matches the expected
  `ui smoke ok ...` shape; exit `1` on failed smoke; exit `2` on argument
  validation errors.
- Preserve the existing acceptance module import contract: importing
  `topos.acceptance` must not import Textual or `topos.ui.*`. The new command
  should use `subprocess` to exercise the UI.

## Tests

Add focused tests covering:

- `python -m topos.acceptance tui-smoke --json` on the fixture replay exits `0`;
- text output contains the UI smoke result and measurement fields;
- `--pretty-json` is parseable;
- `--profile minimal` or a temporary `--config` path is passed through and
  reflected in the smoke output;
- bad replay path fails with exit `1` and useful output;
- timeout/invalid timeout behavior is covered without making tests slow;
- importing `topos.acceptance` still does not import Textual or `topos.ui`.

## Documentation

Update:

- `topos/MEASUREMENTS.md` with the new preferred TUI smoke evidence command and
  one fixture result from the branch.
- `topos/docs/OPERATIONS.md` with a short release-check command example.
- `topos/docs/STATUS.md` TUI acceptance notes as appropriate.

Do not update merge evidence in `docs/STATUS.md`; the controller does that
after review and merge.

## Out Of Scope

- A real 5-minute manual live TUI run. This command is rootless automation for
  repeatable UI smoke evidence; live-host measurement still needs operator
  evidence in `MEASUREMENTS.md`.
- DAMON live-root acceptance.
- Packaging/pipx release certification.
