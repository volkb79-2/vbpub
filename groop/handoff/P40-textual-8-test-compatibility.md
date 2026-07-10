# P40 - Textual 8 Test Compatibility

## Goal

Restore a green full suite in the current managed devcontainer environment
(Python 3.14, Textual 8.2.8) without weakening UI behavior assertions or
breaking compatibility with the project's supported Textual range.

P39 controller validation reproduced 15 failures in
`groop/tests/test_ui_app.py`. Every failure reads `Static.renderable`, an API
that is absent in Textual 8.2.8. The production `tui-smoke` subprocess path
still passes, so the initial evidence points to test inspection compatibility,
not a product rendering failure. Confirm that diagnosis before editing.

## Workflow

- Branch: `feat/groop-p40-textual-8-test-compatibility`
- Worktree: `.worktrees/-groop-p40-textual-8-test-compatibility`
- Touch only `groop/**`
- Keep `groop/handoff/reports/P40-LOG.md` current
- Finish with `groop/handoff/reports/P40-REPORT.md` and focused commits

## Requirements

- Replace direct dependence on removed Textual widget internals with a small,
  reusable test helper that obtains the displayed content through supported
  APIs across the intended Textual versions.
- Preserve the semantic assertions for replay status, reserved actions,
  snapshot progress/results, and DAMON confirmation/status.
- Do not paper over failures with broad skips, xfails, version exclusions, or
  assertions against private implementation state.
- Inspect the package dependency range. Pin Textual only if a real production
  incompatibility requires it; prefer compatible code/tests when behavior is
  sound on current Textual.
- Add focused coverage for the compatibility helper if useful.
- Update README/ROADMAP/STATUS/MEASUREMENTS only with verified results.

## Validation

- Focused `groop/tests/test_ui_app.py` under the managed environment.
- Full `groop/tests` suite with `PYTHONPATH=groop/src`.
- `groop/tests/test_acceptance.py`.
- P38 fixture `tui-smoke` command.
- `py_compile` for touched Python files.

## Out Of Scope

- UI redesign or unrelated Textual refactors.
- Relaxing product behavior assertions.
- Live-root DAMON or other manual release gates.
