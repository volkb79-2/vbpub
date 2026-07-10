# P39 Work Log

## Context

- Branch: `feat/groop-p39-release-readiness-ledger`
- Worktree: `.worktrees/-groop-p39-release-readiness-ledger`
- Base: main at `b2f876d` (includes P38 and the unrelated MDT-P01 merge)
- Scope: release-readiness documentation and evidence alignment only

## Implementation

- Read `TUI-SPEC.md` section 9, README, STATUS, ROADMAP, OPERATIONS,
  `MEASUREMENTS.md`, and P33/P35/P36/P37/P38 reports.
- Added `docs/RELEASE-READINESS.md` as the canonical claim/checklist surface.
- Mapped all 14 spec acceptance items to strict `Pass`, `Partial`, or
  `Conditional` evidence states.
- Added explicit rootless commands, pipx packaging, live TUI performance,
  controlled DAMON, deployed daemon, and live non-root templates.
- Added explicit non-claims and production release blockers.
- Updated README, OPERATIONS, STATUS, ROADMAP, and MEASUREMENTS.

## Controller Review

The initial agent draft was corrected before merge:

- Added explicit `PYTHONPATH=groop/src` to checkout validation commands.
- Restored pipx as a required spec item rather than optional evidence.
- Marked raw-write drift, rendered replay fidelity, v2 action gating, and live
  non-root smoke as partial instead of overclaiming fixture coverage.
- Removed v2 foundations from the v1/v1.5 candidate claim.
- Replaced the nonexistent `groop damon vaddr start` CLI example with the
  actual TUI vaddr flow; retained the real paddr CLI command.
- Reworked the five-minute CPU/RSS sampling template.
- Added `MEASUREMENTS.md` alignment and a P40 handoff for an evidence-exposed
  Textual 8 full-suite blocker.

## Validation

Agent-local focused acceptance and P33/P35/P38 fixture commands passed. The
agent installed/changed Textual while validating, so controller evidence is
authoritative for the full suite.

Controller environment:

```text
Python 3.14.6
Textual 8.2.8
```

Controller full suite:

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# 367 passed, 15 failed in 48.27s
```

All failures are in `test_ui_app.py` and read the removed
`Static.renderable` attribute. This is recorded as a release blocker, not
reported as a passing P39 result. P40 owns the fix.

Additional controller checks in the same managed environment:

```text
Focused acceptance: 40 passed in 7.64s
P33 fixture smoke: exit 0, ok=true, 8 entities
P35 steady: exit 0, ok=true, 5/5 samples
P38 tui-smoke: exit 0, ok=true, frames=1, view=tree, profile=auto
Direct checkout CLI UI smoke: exit 0, ui smoke ok
Full-source py_compile: exit 0
git diff --check: exit 0
```

P39 changes no production or test Python files.

## Blockers

- P40 must restore the green full suite under current supported Textual.
- The strict manual gates listed in `docs/RELEASE-READINESS.md` remain before a
  production-certified tag.
