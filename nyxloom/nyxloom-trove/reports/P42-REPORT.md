# P42 — First-class optional `component` field — Implementation Report

**Status:** done · **Date:** 2026-07-16

## Summary

Adds an OPTIONAL first-class `component` frontmatter field so handoffs can
declare a real category (`lifecycle`, `worker`, `ui`, `infra`, ...) instead of
relying on the id's slug-convention (which nothing could group/filter by).
`component` is read from frontmatter at render time only — never mirrored to
the statefile, lint, or daemon/reconcile logic (out of scope per the handoff).
The dashboard's `index.html` active-tasks table gains a `Component` column;
componentless tasks (or tasks whose handoff is missing/unparsable) render as
`uncategorized`, never crash.

## Per-oracle results

| Oracle | Result | Test |
| --- | --- | --- |
| O1 (schema optional + parses to `Frontmatter.component`, backward compatible) | PASS | `test_component_optional_and_valid`, `test_parse_with_component`, `test_parse_without_component_is_backward_compatible` |
| O2 (dashboard shows component; componentless renders ungrouped, no crash) | PASS | `test_index_html_groups_task_by_component`, `test_index_html_componentless_task_renders_ungrouped` |

## Files touched

- `src/nyxloom/schemas/handoff-frontmatter.schema.json` — added optional
  `component` property (`^[a-z][a-z0-9-]*$`), not in `required`.
- `src/nyxloom/types.py` — added `component: str | None = None` to the
  `Frontmatter` dataclass. **Deviation from `scope.touch`:** the handoff
  listed `frontmatter.py` as the file owning the `Frontmatter` dataclass, but
  the dataclass actually lives in `types.py` (`frontmatter.py` only imports
  it and calls the fully generic `Frontmatter.from_dict`, which needed no
  change). `types.py` is not in `scope.forbid`; there is no automated
  scope-touch enforcement (checked `lint.py` — only L7/L9 exist, neither
  diffs real edits against `scope.touch`), so this was the minimal correct
  change rather than a workaround.
- `src/nyxloom/render.py` — `_render_index` now loads each active task's
  frontmatter (mirrors the existing `_load_frontmatter` pattern already used
  by `_render_dag`) and renders a `<span class="component-tag">` cell,
  defaulting to `uncategorized`. Updated `_render_carve_row`'s colspan
  (10 -> 11) and the module docstring's `index.html` contract section to
  match the new column.
- `tests/test_frontmatter.py`, `tests/test_render.py` — new tests per above.

## Follow-up (noted, not dropped)

O2 asked for "GROUPS or labels" with the minimal-pass escape hatch spelled
out in `escalate_if`. This implementation does the minimal labelled version
(a tagged column, sorted/interleaved exactly as today — same row order). A
richer group-by/filter UI (e.g. collapsible per-component sections, a
client-side filter control) is a reasonable follow-up but was intentionally
not attempted here to stay within the single-pass scope.

## Gate

`tester-unified`: full `pytest tests -q` run inside the gate container —
all tests pass (two `test_daemon.py` failures around `HTTP_BIND`/hostname
default seen in an unrelated local shell run are environment-dependent, do
not reproduce inside the gate container, and are pre-existing on `main`
prior to this change — confirmed via `git stash`).
