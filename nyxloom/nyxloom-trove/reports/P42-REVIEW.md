# P42 — First-class optional `component` field — Independent Review

**Reviewer:** independent frontier reviewer (merge gate) · **Date:** 2026-07-16
**Branch:** `feat/nyxloom-P42-component-field` · **Reviewed commit:** `37f24da`
**Base:** `main` @ `245eeb5` (merge-base confirmed identical — no drift)

## Git state (verified directly, not from the receipt)

- `git log main..feat/nyxloom-P42-component-field` → exactly one commit, `37f24da`.
- `git diff --stat main...` matches the packet's claimed 6 files / 213 insertions.
- Worktree `/workspaces/vbpub/.worktrees/feat/nyxloom-P42-component-field` at
  `37f24da`, `git status --porcelain` empty — the packet's "clean" claim is true.
  No uncommitted work to rescue.

## Gate — re-run independently, not trusted from the REPORT

Declared `tester-unified` gate, run by me in the declared container against the
branch worktree:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../feat/nyxloom-P42-component-field/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

**EXIT=0**, 568 tests, all pass, zero F/E. (Note: this suite's `-q` terminal
summary line is suppressed by config — the exit code and the all-dots output are
the evidence, which is why the count is stated from the dot stream, not a pasted
summary line.) Re-run again after my own fix below: still **EXIT=0**.

## O1 — optional `component`, parsed, backward compatible → **MET**

- Schema: `component` is `{"type":"string","pattern":"^[a-z][a-z0-9-]*$"}`, and I
  confirmed programmatically that `required` is
  `[schema_version, id, project, title, tier, input_revision, source, scope, oracles, gates, escalate_if]`
  — `component` is **not** in it. `additionalProperties: false` at the root means
  the property genuinely had to be declared for a `component:` handoff to validate.
- Parsed onto the object: `Frontmatter.component: str | None = None`, surfaced via
  the fully generic `Frontmatter.from_dict` that `frontmatter.parse_handoff` already
  calls.
- Backward compatibility is real, not asserted-only: a handoff omitting the field
  validates and parses to `.component is None`.
- O1's negative is not triggered: not required, surfaced on the parsed object,
  omitting it does not fail validation.

## O2 — component surfaced on the dashboard → **MET**

- `_render_index` reads the component at render time via the existing
  `_load_frontmatter(root, tsf)` and renders
  `<td><span class="component-tag">…</span></td>`, defaulting to `uncategorized`.
- O2 explicitly permits "grouped **or tagged**" — a labelled column satisfies the
  observable as written; the richer group-by/filter UI is correctly deferred (see
  Follow-up compliance below), not silently dropped.
- **Column-count consistency verified end-to-end** (the classic defect when
  inserting a column): 11 `<th>` (Project, Task, Component, State, Route, Started,
  Minutes, Cost, Leases, Notes, Last Activity), 11 `<td>` in the row, and *both*
  colspan sites updated 10→11 — `_render_carve_row` and the "No active tasks"
  empty state. No stale `colspan="10"` remains anywhere in `src/`.
- **No positional column dependency breakage**: grepped for `nth-child`,
  `nth-of-type`, `cells[`, `children[`, `querySelectorAll('td` — none. The only JS
  touching `#active-tasks` is a `classList.toggle('show-carves')`, which is
  index-independent. Inserting a column at index 2 is therefore safe.
- **Componentless / missing-handoff path is genuinely crash-free**, not just
  test-asserted: `_load_frontmatter` (render.py:849) returns `None` — never raises
  — when `handoff_path` is unset, the file is missing, or it fails to parse. The
  `(fm.component if fm else None) or "uncategorized"` coercion is total. An empty
  string cannot slip through as a component, since the schema pattern requires ≥1 char.
- O2's negative is not triggered: the component is shown, and a componentless task
  renders as `uncategorized` without crashing.

## Adversarial check: are the new tests hollow? → **No — mutation-tested**

The gate passing proves nothing on its own, so I mutated each implementation site
and confirmed the corresponding tests actually die (worktree restored clean after
each; verified with `git status --porcelain`):

| Mutation | Result |
| --- | --- |
| Remove `component` field from `types.py` `Frontmatter` | `test_parse_with_component`, `test_parse_without_component_is_backward_compatible`, `test_index_html_groups_task_by_component` **FAIL** |
| Remove the component `<td>` from `render.py` | `test_index_html_groups_task_by_component`, `test_index_html_componentless_task_renders_ungrouped` **FAIL** |
| Remove `component` from the schema | `test_component_optional_and_valid`, `test_parse_with_component`, `test_index_html_groups_task_by_component` **FAIL** |

Every new test is load-bearing. I specifically checked one assertion that *could*
have been hollow — `test_component_optional_and_valid`'s
`any("component" in str(e).lower() for e in errors)` would also pass on an
`additionalProperties` violation if the property were undeclared — but it is
paired with `schema_errors(with_component) == []`, so the pair only passes when
the pattern is really enforced. Mutation C confirms this empirically.

## Findings

1. **Scope deviation: `types.py` touched, not in `scope.touch` — ACCEPTED (handoff
   defect, not implementer defect).** The handoff named `frontmatter.py` as owning
   the `Frontmatter` dataclass; it actually lives in `types.py`. `frontmatter.py`'s
   `parse_handoff` only calls the generic `Frontmatter.from_dict` and needed no
   change. `types.py` is **not** in `scope.forbid` (which lists only `daemon.py`,
   `reconcile.py`, `lint.py` — all correctly untouched). Mutation A proves the field
   is load-bearing exactly where it was put. This was the minimal correct change,
   and the implementer proactively disclosed it in the REPORT rather than hiding it
   or contorting the code to match a mistaken scope. No action needed; the
   *handoff's* `scope.touch` was inaccurate.
2. **REPORT overclaim check — clean.** The REPORT's one env-specific claim (local
   `test_daemon.py` `HTTP_BIND`/hostname failures that "do not reproduce inside the
   gate container, and are pre-existing on `main`") is consistent with my own
   container run being fully green at EXIT=0. The REPORT does not overclaim: it
   marks O2 as the *minimal* labelled version, not a full group-by.
3. **`escalate_if` follow-up compliance — satisfied.** The handoff required that a
   minimal grouping pass note the richer filter UI as a REPORT follow-up rather than
   silently dropping O2. The REPORT does exactly this ("Follow-up (noted, not
   dropped)"). Neither BLOCKED condition applies: no statefile plumbing or lint rule
   was needed — component is read from frontmatter at render time, as designed.
4. **Consistency with `STANDARD.md` — confirmed.** STANDARD.md:82-83 already
   promises "an optional first-class `component:` frontmatter field is preferred
   over parsing the slug". This delivers precisely that field, with a description
   distinguishing it from the id's convention-only slug segment.

## What I fixed myself

**`.component-tag` had no CSS rule** (`render.py`). The frozen interface contract
docstring the change itself added advertises "a span.component-tag", and O2 asks
for tasks to be "visually grouped or **tagged**" — but the class styled nothing, so
the tag rendered as bare text, a dangling hook. Added one minimal rule alongside
the existing `.live-indicator` / `.carve-row` conventions, matching the dark theme:

```css
.component-tag { background: #263140; color: #9fb4cc; padding: 1px 6px;
                 border-radius: 3px; font-size: 11px; white-space: nowrap; }
```

To be explicit about severity: this was **not** blocking and would not have failed
O2 on its own — the component name did already render as readable text in its own
labelled `Component` column, so O2's negative ("invisible on the human surface")
was never triggered. It is polish that makes the tag read as a tag. Deliberately a
single rule with no `uncategorized` variant: a second class would have changed the
emitted markup that the tests pin. Gate re-run after the fix: **EXIT=0**.

## Minor, non-blocking (left for follow-up, not defects)

- **Double frontmatter parse per render (N+1).** `_render_index` now parses each
  active task's handoff, and `_render_dag` parses the same handoffs again — so each
  handoff is read + jsonschema-validated twice per render. This mirrors the existing
  `_load_frontmatter` pattern and is fine at current scale, but a per-render cache
  would be the natural fix if the active-task list grows.
- **Test reuse nit.** `test_index_html_groups_task_by_component` hand-rolls
  `SAMPLE_HANDOFF.replace(...)` where `conftest.py:120` already provides a
  `handoff_override` helper for textually overriding frontmatter keys. Cosmetic; the
  hand-rolled replaces are self-checking (a missed replace fails the assert), so
  there is no hidden-pass risk.

## Verdict reasoning

Both oracles are met and independently verified rather than taken on the REPORT's
word: the gate was re-run by me (EXIT=0), every new test was mutation-tested and
bites, the column-insertion hazards (colspan, positional JS) were checked and are
clean, and the crash-free componentless path was traced to `_load_frontmatter`'s
never-raises contract rather than inferred from a passing test. The single scope
deviation is a defect in the handoff's `scope.touch`, not in the work, and was
disclosed rather than concealed. No forbidden file was touched. The one gap I found
was cosmetic and I fixed it in-scope.

VERDICT: APPROVED
