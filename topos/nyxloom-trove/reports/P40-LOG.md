# P40 Work Log

## Context

- Date: 2026-07-10 UTC
- Branch: `feat/topos-p40-textual-8-test-compatibility`
- Worktree: `.worktrees/-topos-p40-textual-8-test-compatibility`
- Stacked base: reviewed P39 commit `58b751f`

## Diagnosis

The managed Python 3.14.6 / Textual 8.2.8 environment reproduced 15 failures
in `test_ui_app.py`. Production code did not use the removed API. Three test
locations read `Static.renderable`, and shared helper use caused the 15-test
cascade.

The package declares `textual>=0.58,<1`; Textual 8.2.8 is therefore a newer
managed-environment compatibility target, not a silently broadened published
dependency range.

## Implementation

- Added `_static_text(Static)`, implemented as `str(widget.render())`.
- Replaced the three direct `.renderable` reads.
- Preserved every existing substring/behavior assertion.
- Added no skips, xfails, pins, or production-code changes.

`Static.render()` is a public widget method in both isolated Textual 0.58.1 and
managed Textual 8.2.8, avoiding branches over version-specific attributes.

## Validation

Agent evidence before controller refinement:

- Textual 8.2.8 UI tests: 23 passed.
- Full suite: 382 passed.
- Acceptance tests: 40 passed.
- P38 TUI smoke: exit 0.
- Changed test file compiled.

Controller evidence after the public-API refinement:

- Isolated Textual 0.58.1 UI suite: 23 passed in 8.35s.
- Managed Textual 8.2.8 UI suite: 23 passed in 11.24s.
- Managed Textual 8.2.8 full suite: 382 passed in 48.04s.
- Focused acceptance: 40 passed in 8.12s.
- P38 TUI smoke: exit 0, `ok: true`, one tree/auto frame.
- `git diff --check` and changed-file `py_compile`: passed.

## Documentation

README, STATUS, ROADMAP, and MEASUREMENTS mark P40 complete while preserving
P39's remaining manual production gates.

## Blockers

P40 has no implementation blocker. The live/manual release gates in
`docs/RELEASE-READINESS.md` remain unchanged.

## Merge Evidence

- Merge commit: `970953a` after P39 merge `bfdf3db`.
- Full suite on main: 382 passed in 47.73s.
- Focused acceptance on main: 40 passed in 7.54s.
- P38 TUI smoke on main: exit 0, `ok: true`, one tree/auto frame.
- Full-source `py_compile` and merge diff checks passed.
