# P43 - Current Textual Dependency Baseline

## Goal

Make normal Topos installations resolve the current supported Textual release
line instead of the obsolete pre-1.0 line. As of the carve on 2026-07-10, the
official PyPI release is Textual 8.2.8 and the managed Python 3.14 environment
already validates that version.

## Workflow

- Branch: `feat/topos-p43-textual-current-baseline`
- Worktree: `.worktrees/-topos-p43-textual-current-baseline`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P43-LOG.md` current
- Finish with `topos/handoff/reports/P43-REPORT.md` and focused commits

## Requirements

- Change the published dependency from `textual>=0.58,<1` to
  `textual>=8.2.8` with no artificial upper bound. We rely on tests and release
  validation for upstream compatibility rather than silently holding fresh
  installs on an old major line.
- Add a small packaging-metadata regression test that proves the lower bound is
  at least 8.2.8 and that no `<...` Textual ceiling is reintroduced. Read the
  project metadata; do not duplicate application behavior or query the network
  from the normal test suite.
- Build a fresh wheel and inspect its `METADATA` to prove the emitted
  `Requires-Dist` matches the source declaration.
- In an isolated environment with no preinstalled Textual, install the local
  wheel using normal dependency resolution and record the resolved Textual
  version. Require 8.2.8 or newer and run `topos --version`, direct replay UI
  smoke, P38 `tui-smoke`, UI tests, acceptance tests, and the full suite against
  that environment.
- Preserve Python `>=3.11` support and optional zstandard behavior. Do not add
  `textual-dev` as a runtime dependency.
- Update README, ROADMAP, STATUS, RELEASE-READINESS, MEASUREMENTS, and packaging
  guidance so P40's 0.58 evidence is clearly historical and superseded rather
  than rewritten or deleted.
- Keep the dependency policy explicit: latest compatible upstream releases are
  preferred; upper bounds require a demonstrated incompatibility and a tracked
  removal condition.

## Acceptance

- Source metadata and built wheel both declare `textual>=8.2.8` without an
  upper cap.
- A clean resolver install selects Textual 8.2.8 or newer.
- UI, acceptance, full-suite, replay smoke, and `py_compile` gates pass in the
  clean resolved environment.
- Existing managed-environment gates remain green.

## Out Of Scope

- Adopting Textual prereleases.
- Adding an upper version ceiling without a reproduced upstream break.
- UI redesign or use of new Textual-only features in this package.
- Rewriting historical P40 reports or measurements.
