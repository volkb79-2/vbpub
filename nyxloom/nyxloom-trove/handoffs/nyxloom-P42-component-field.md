---
schema_version: 1
id: nyxloom-P42-component-field
project: nyxloom
title: "First-class optional component field for handoffs (categories/grouping)"
tier: sonnet5-high
input_revision: "f098cbf"
depends_on: []
session: fresh
source: {kind: product-goal, ref: nyxloom-trove/STANDARD.md}
scope:
  touch:
    - "src/nyxloom/schemas/handoff-frontmatter.schema.json"
    - "src/nyxloom/frontmatter.py"
    - "src/nyxloom/render.py"
    - "tests/test_frontmatter.py"
    - "tests/test_render.py"
  forbid:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/lint.py"
oracles:
  - id: O1
    observable: "An OPTIONAL `component` field is added to handoff-frontmatter.schema.json (string, pattern `^[a-z][a-z0-9-]*$`, NOT required) and parsed by frontmatter.py into the Frontmatter object as `component: str | None`. A handoff WITH `component: lifecycle` parses with `.component == 'lifecycle'`; a handoff WITHOUT the field parses with `.component is None` and still validates (backward compatible). Test asserts both."
    negative: "component is required (breaks every existing handoff that lacks it) OR is not surfaced on the parsed object (so nothing downstream can group by it) OR the schema rejects a handoff that omits it."
    gate: tester-unified
  - id: O2
    observable: "The dashboard surfaces the component: the task index (render.py `_render_index`) GROUPS or labels a project's tasks by their `component` (tasks sharing a component are visually grouped or tagged; tasks with no component fall under an 'uncategorized'/ungrouped default). A render test asserts a task with `component: lifecycle` renders its component label/group in the generated HTML, and a componentless task still renders (no crash, ungrouped)."
    negative: "component is parsed but never shown, so 'categories' are invisible on the human surface (the whole point — grouping/filtering — is missing); OR a componentless task crashes the render."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "surfacing the component on the dashboard requires touching daemon.py/reconcile.py (statefile plumbing) rather than reading frontmatter at render time — then BLOCKED (raise a D-NNN on where component should live: frontmatter-only vs mirrored to the statefile)"
  - "a full group-by UI is too large for one pass — implement the minimal labelled grouping and note the richer filter UI as a follow-up in the REPORT (do NOT silently drop O2)"
---

# P42 — First-class optional component field (categories / grouping)

Projects want to categorise handoffs by **component** (dstdns already has
lifecycle / worker / test / ui / infra / deploy / launch). Today the component is
only a slug convention (`<project>-P<NN>-<component>-<slug>`) — the id regex
allows just ONE hyphen-free token before `-P<NN>` (the project id), so the
component rides in the slug and nothing can group/filter by it. Add an OPTIONAL
first-class `component` field so categories are real on the human surface.

The naming convention is already documented in `STANDARD.md` (Document
conventions → Naming → Component / category convention). This adds the field it
promises.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P42-component-field` from `main`);
commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these)

- `src/nyxloom/schemas/handoff-frontmatter.schema.json` — the `properties` block
  + `required` list. Add `component` to properties (optional; NOT in `required`).
- `src/nyxloom/frontmatter.py` — the `Frontmatter` dataclass + its parser
  (`parse_handoff` / the field extraction). Add `component: str | None = None`
  and read it from the parsed YAML.
- `src/nyxloom/render.py` — `_render_index` (the per-project task list) and
  `_load_frontmatter` (already loads a task's frontmatter for the render). Use
  the loaded frontmatter's `component` to group/label rows. Read how rows are
  currently built.
- `tests/test_frontmatter.py`, `tests/test_render.py` — mirror existing tests for
  O1 (parse with/without component) and O2 (component shows in the render).

## Work

1. `handoff-frontmatter.schema.json`: add optional `component` (string, pattern
   `^[a-z][a-z0-9-]*$`).
2. `frontmatter.py`: add `component: str | None` to `Frontmatter`; parse it.
3. `render.py`: group/label the task index by `component` (ungrouped default for
   componentless tasks).
4. Tests: O1 (parse both cases, backward compatible) + O2 (render shows it).

## Scope / forbid

Touch ONLY the five files in `scope.touch`. Do NOT edit `daemon.py`,
`reconcile.py`, or `lint.py` — component is read from frontmatter at render time,
not mirrored into the statefile or lint-enforced (a possible follow-up, out of
scope here).

## BLOCKED rule

If surfacing the component needs statefile plumbing (daemon.py/reconcile.py) or a
lint rule (all forbidden), STOP — write `BLOCKED: <reason>` to the LOG, commit,
exit; raise a `D-NNN` on where component should live.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
