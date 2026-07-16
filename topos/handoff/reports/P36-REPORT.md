# P36 - CPU Sparkline Surface - Report

## What was built

A compact ASCII sparkline surface for CPU trend recognition in the TUI,
using existing `HistoryRing` data. No new collector metrics, model fields,
or persistent storage.

### New module: `topos/src/topos/ui/sparkline.py`

- **`render_sparkline(values, *, width=8)`** - renders a pure-ASCII sparkline
  from a list of numeric values. Uses an 8-level ASCII ramp (`_ , - ~ = + % #`)
  with `.` for missing values. Handles down-sampling when the input is longer
  than `width`, flat series (all middle-char), empty/all-None input,
  zero-width input, and single-element input.
- **`sparkline_from_history(history, *, width=6)`** - convenience wrapper that
  returns `""` (empty string) when history is absent, or `" [sparkline]"` when
  data exists, for easy conditional appending.

### Virtual column: `cpu_trend`

Added a `cpu_trend` virtual column (label `CPU_TREND`) to the entity table
at the 160+ char auto profile width tier. When `HistoryRing` data for
`cpu_pct` exists on the entity, the cell shows a 6-character ASCII sparkline
in brackets (e.g., ` [_,-~=+#]`). When no ring is available or no history
exists for the entity, the cell shows `-` (dimmed).

### Render chain threading

The `HistoryRing` is now threaded through the table/tree render chain via
an optional `ring` parameter (default `None` for backward compatibility):

- `app.py:_render_rows()` -> passes `self.ring`
- `table.py:render_container_table()` -> passes to `_row_cells()`
- `tree.py:render_tree_table()` -> passes to `format_metric_value()`
- `table.py:format_metric_value()` -> handles `cpu_trend` via
  `_format_cpu_trend()`

### Sort support

`cpu_trend` sorts by the current `cpu_pct` value (descending when
`cpu_pct` is the primary sort column).

## Deviations from the handoff doc

None. The implementation follows the preferred entity-table path.

## Contract changes

None. `CONTRACTS.md` is untouched. The ring threading is additive and
backward-compatible via default `None` parameters.

## Test evidence

```bash
$ PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_ui_sparkline.py -q
# 19 passed in 0.06s
-> rising, falling, flat, missing, short series, empty, zero-width, all-None, single value
-> sparkline_from_history empty/all-None/bracketed
-> _format_cpu_trend with ring/no-ring/no-history
-> format_metric_value cpu_trend column via public API with/without ring
-> cpu_trend sort value (cpu_pct based)

$ PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
# 355 passed in 39.25s
# (previously 336 - 19 new tests added)

$ topos --once --json > /dev/null 2>&1; echo $?
# 0
```

Python compile clean on all new/changed files.

## Known gaps

- CPU sparkline appears in the auto profile only at 160+ character widths.
  Users can add `cpu_trend` to narrower profiles via config profiles.
- The existing `drill.py:_sparkline()` (Unicode block chars) is not replaced;
  the drill-down screen continues to use the richer Unicode rendering.
- Banner-level CPU trend from root/aggregate history is not implemented
  (the handoff listed this as acceptable fallback; entity table was preferred).

## Files changed

```
M topos/src/topos/ui/table.py          # cpu_trend virtual column, render chain ring threading
M topos/src/topos/ui/tree.py           # ring parameter threading
M topos/src/topos/ui/app.py            # pass self.ring to _render_rows
A topos/src/topos/ui/sparkline.py      # new ASCII sparkline helper
A topos/tests/test_ui_sparkline.py     # 19 focused tests
M topos/docs/STATUS.md                 # updated for P36
M topos/handoff/reports/P36-LOG.md     # resumability log
```
