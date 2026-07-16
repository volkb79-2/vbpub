# P36 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/topos-p36-cpu-sparklines
- Worktree: .worktrees/-topos-p36-cpu-sparklines
- Base commit: 55daa25 (docs(topos): record P34 P35 merge evidence)
- Package: P36 - CPU Sparkline Surface
- Current objective: Add compact ASCII CPU trend sparklines to TUI surfaces using existing HistoryRing data

## Timeline

```text
2026-07-10 02:00 UTC
- Action: Created worktree and feature branch from main.
- Commands: git worktree add -b feat/topos-p36-cpu-sparklines .worktrees/-topos-p36-cpu-sparklines main
- Files changed: (none yet)
- Result: Worktree ready at commit 55daa25
- Follow-up: Initialize LOG.md, create sparkline helper module

2026-07-10 02:15 UTC
- Action: Created sparkline helper module with ASCII-only sparkline rendering.
- Files changed: topos/src/topos/ui/sparkline.py
- Result: render_sparkline() and sparkline_from_history() functions

2026-07-10 02:25 UTC
- Action: Added cpu_trend virtual column to table.py, threaded ring through render chain.
- Files changed: table.py, tree.py, app.py
- Result: cpu_trend column supported in profiles at 160+ char widths

2026-07-10 02:35 UTC
- Action: Wrote 18 focused tests for sparkline helper and cpu_trend rendering.
- Files changed: topos/tests/test_ui_sparkline.py

2026-07-10 02:40 UTC
- Action: Ran quality gates - all 354 tests pass, compile clean, --once --json exit 0.
- Follow-up: Write REPORT.md and commit.

2026-07-10 CEST (controller review)
- Action: Hardened render_sparkline() width behavior for empty and zero-width inputs, added focused test, and converted new P36 files to ASCII-only wording.
- Files changed: topos/src/topos/ui/sparkline.py, topos/tests/test_ui_sparkline.py, topos/handoff/reports/P36-REPORT.md, topos/handoff/reports/P36-LOG.md
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_ui_sparkline.py -q -> 19 passed in 0.06s
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m py_compile topos/src/topos/ui/sparkline.py topos/src/topos/ui/table.py topos/src/topos/ui/tree.py topos/src/topos/ui/app.py topos/tests/test_ui_sparkline.py -> clean
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q -> 355 passed in 39.25s
- Follow-up: Commit controller review patch and merge
```

## Decisions

- Decision: Add cpu_trend as a virtual column in the entity table (preferred approach in handoff), showing an ASCII sparkline from HistoryRing cpu_pct data. Thread ring through render functions.
  Reason: The handoff prefers entity table/profile cell; banner approach is acceptable fallback. The entity table approach gives per-entity CPU trend visibility.
  Impact: Requires threading the ring through table/tree render functions. Adding ring=None default preserves backward compat for any external callers.
- Decision: Use 8-level ASCII sparkline characters: `_` `,` `-` `~` `=` `+` `%` `#`, with `.` for missing/None values.
  Reason: Handoff requires ASCII-only sparkline. 8 levels match the existing Unicode sparkline density in drill.py while being pure ASCII.
  Impact: Readable compact trend indicator in table cells.
- Decision: Pre-compute CPU sparkline strings in _refresh_view / render functions, not stored in frame.
  Reason: Sparklines are a rendering concern derived from live ring data, not persistent model data.

## Blockers

(none)

## Validation

```bash
python3 -m pytest topos/tests/test_ui_sparkline.py -v
# 18 passed in 0.06s

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_ui_sparkline.py -q
# 19 passed in 0.06s

python3 -m pytest topos/tests -q
# 354 passed in 40.24s

PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
# 355 passed in 39.25s

topos --once --json > /dev/null 2>&1; echo "exit=$?"
# exit=0
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
```
