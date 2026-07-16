# P13 — Dashboard Visuals: Real DAG, Usable Timeline, Readable Live Page — Implementation Report

**Status:** done · **Date:** 2026-07-15

## Summary

Fixed the confirmed DAG "No edges" bug (edge extraction now parses each task's
own handoff frontmatter via `frontmatter.parse_handoff`, never the statefile)
and delivered the three visual upgrades: an inline SVG dependency graph with
a pure-python Kahn-level layout, an auto-fit timeline axis with tick labels
and floored bar widths, and a parsed/colorized live event page with a raw
JSON toggle. All existing P05 tests remain green unchanged; 10 new tests
cover the P13 oracles plus one defensive cycle test for the "must not hang"
requirement called out in the findings.

## Oracle Results

| # | Oracle | Status | Notes |
|---|--------|--------|-------|
| 1 | DAG edges from frontmatter (dep + mutex), `<svg>` present, `class="edge-dep"` marker, edges-table rows for both kinds | **PASS** | `test_dag_edges_from_frontmatter` |
| 1 (neg) | Task with unparseable handoff still renders its node, no crash | **PASS** | `test_dag_unparseable_handoff_negative` |
| 2 | Layout sanity: A->B dependency places A's `<rect x=...>` strictly left of B's | **PASS** | `test_dag_layout_places_dependency_left` |
| — | Finding 2 (cycles must not hang; broken edge marked) | **PASS** | `test_dag_cycle_does_not_hang_and_marks_edge_red` (defensive, not a numbered oracle but explicitly called out in the findings) |
| 3 | Timeline: 10-min-old attempt bar width >= 6px (MIN_BAR_WIDTH_PX), axis row has >= 4 tick labels | **PASS** | `test_timeline_autofit_bar_width_and_ticks` |
| 3 | Timeline: empty-window lane shows "No activity in window" note | **PASS** | `test_timeline_empty_window_note` |
| 4 | Live page: `textContent`-only renderer (no `innerHTML`), raw toggle, project-less EventSource URL, type-based coloring | **PASS** | `test_live_html_parsed_renderer` |
| 5 | Idempotence + escaping tests from P05 still pass unchanged | **PASS** | `test_idempotence`, `test_index_html_active_tasks`, etc. — untouched, still green |

## Files Touched

- `src/nyxloom/render.py` — bug fix + SVG DAG layout + timeline auto-fit + live page rewrite (module docstring updated to match)
- `tests/test_render.py` — 10 new tests + `_write_handoff`/`dag_data`/`timeline_data` local fixtures; minor cleanup (imported `Role`/`re` at top instead of the inline `__import__` hack)

## Implementation Notes

### 1. DAG bug fix (root cause confirmed)

`_render_dag` previously read `tsf.frontmatter` — an attribute `TaskStateFile`
never has (confirmed in `src/nyxloom/types.py`; `hasattr(tsf, 'frontmatter')`
was always `False`), so `task_deps()`/`effective_mutexes()` were never called
and the edges list was always empty. Fixed via a new `_load_frontmatter(root,
tsf)` helper that reads `root / tsf.handoff_path` (root = `registry[project]`)
through `frontmatter.parse_handoff`, catching `Exception` broadly (missing
file, unset `handoff_path`, or a parse/schema error) and returning `None` —
the task's node still renders in that case, only its edges are skipped.

### 2. SVG DAG layout

`_dag_levels` is a Kahn-style layered topological sort over the `(from, to)`
edge list (from = the task with `depends_on`, to = its dependency/mutex
resource — matching the handoff's own "A->B places A left of B" framing, i.e.
edge source renders at a strictly lower level than its target). It terminates
in O(V+E) regardless of cycles: nodes never reached once the queue drains are
pinned to level 0 and their inbound edges are returned in a `broken` set,
rendered with `class="edge-broken"` and a red stroke/marker — this can never
hang. `_render_dag_svg` places nodes as rounded `<rect data-node="...">`
colored via the existing `COLORS` map (dark text per the handoff), with mutex
targets (`<project>.<mutexname>`) as neutral gray pseudo-nodes and any
dependency target that has no matching task (cross-project or stale
reference) as a distinct "external" pseudo-node, so no edge is silently
dropped from the drawing. Dependency edges are solid; mutex edges dashed;
arrowheads via SVG `<marker>`. The edges table (unchanged shape: from, to,
kind) still follows below the SVG.

### 3. Timeline auto-fit

`_timeline_axis` computes `window = min(earliest attempt start across ALL
projects, now-1h)` to `now`, clamped so `axis_start` can never exceed `now`
(defensive against a clock-skewed or entirely-empty statefile set), then pads
5% on both ends. Bars are absolutely positioned in pixels over a fixed
`TIMELINE_WIDTH_PX` (960) rather than percentages, so `MIN_BAR_WIDTH_PX` (6)
is enforced exactly (`max(6.0, end_px - start_px)`) instead of relying on
sub-pixel rounding of a percentage width. 5 tick labels (`HH:MM`, within the
handoff's 4-6 range) are rendered at even fractions of the window. The route
id is rendered inside the bar via a `.bar-label` span once the bar is wider
than 60px; the `title` attribute (`<attempt_id> <route> <state>`) is always
present. Tasks with no attempts inside the window get a `.no-activity` note
instead of an empty track — this now includes tasks that previously got
dropped from the page entirely (the old code only appended a lane `if bars`).

### 4. Live page

Kept the same `EventSource('/api/stream')` call (no `?project=`, matching
`daemon.py`'s "default to the first registered project" behavior). Each
`onmessage` event is `JSON.parse`d and rendered as one row built entirely via
`createElement`/`textContent`/`appendChild` — `innerHTML` is never used
anywhere in the script. `keyDetail()` extracts `payload.to` (transitions),
`payload.attempt.state` (`ATTEMPT_*`), `payload.reason` (`SPEC_ATTENTION`), or
the first 80 chars of `payload.error` (`TICK_ERROR`); rows get
`evt-tick-error`/`evt-task-transitioned`/`evt-attempt` classes (red/green/blue
via the shared CSS block). A `#raw-toggle` checkbox toggles a `show-raw`
class on the container, swapping each row's hidden `.evt-raw` span (the exact
original JSON text) into view via CSS — no re-render needed. Auto-scroll
checks whether the container was already within 4px of its bottom before
appending, and only then scrolls to the new bottom, so a user who has
scrolled up to read history is not yanked back down.

### Out of scope (left untouched)

`_render_task_page`'s "Frontmatter" table has the identical latent bug
(`hasattr(tsf, 'frontmatter')` always `False`) but was not in P13's confirmed
findings list, so it was left as-is to keep this handoff strictly scoped;
flagging it for a future package.

## Gate Output (tail)

Scoped gate (`tests/test_render.py`):

```
....................                                                     [100%]
20 passed in 1.84s
```

Full suite (`tests/`):

```
........................................................................ [ 23%]
........................................................................ [ 46%]
........................................................................ [ 69%]
........................................................................ [ 92%]
........................                                                 [100%]
312 passed in 58.97s
```

## Deviations or Assumptions

- The handoff's "A->B dependency places A left of B" phrasing is taken
  literally as "edge source (the task with `depends_on`) renders left of the
  edge target" — this is a rendering-order convention, not a claim about real
  execution order (the target/dependency itself, e.g. topos-P91, must
  actually complete first at runtime; the DAG's left-to-right axis mirrors
  the handoff's own worked examples, not wall-clock sequencing).
- Mutex-resource nodes (`<project>.<mutexname>`) and cross-project/stale
  dependency targets that don't correspond to any current task are rendered
  as distinct pseudo-nodes (gray, no state color) so their edges are never
  silently dropped from the SVG — not explicitly required by the oracles but
  a direct consequence of "keep the edges table below the SVG" needing a
  node to draw a line to.
- `HOURS = 48` (the old fixed timeline window constant) was removed; nothing
  outside `render.py`/`test_render.py` referenced it (confirmed via
  repo-wide grep before removal).

## Suggestions for the Reviewer (informational only — not acted on)

- Consider a follow-up package to fix `_render_task_page`'s frontmatter table
  (same root cause as the P13 DAG bug) since it currently always renders
  empty.
- The SVG layout is intentionally simple (level = longest path from an
  indegree-0 node, row = sorted order within a level); a wide fan-out DAG
  will grow tall rather than using any horizontal compaction — acceptable at
  pilot scale per the "pure-python, no libs" constraint, but worth
  revisiting if projects grow deep dependency chains.
