# P83-REVIEW — frontier review pass #2

Reviewer: Opus high, fresh session. Wave of 4 (P78/P83/P84/P85).
Date: 2026-07-13.

## Verdict

**Merged after review-fixes.** The pure-function split is right (`grouping.py`
outside `ui/`, no Textual import, unit-testable), the numeric phase rule is
correct and genuinely driven by topos's code (Oracles 1/2/5 hold, and the
`phase_2`-before-`phase_10` trap the carve named is properly handled, including
the unparseable-vs-absent distinction). But the package failed the one oracle
the carve cared most about, and shipped a dead-parameter UX defect.

Note on provenance: P83's self-review was done **inline by the implementer**, not
as a separate resumed pass. It rated Oracle 4 "low hollow risk". It was not.

## Findings

### F1 — Oracle 4 failed against its own verbatim scenario (CONFIRMED, fixed)

`flagged-by-pass-1: no`

Oracle 4: *"A `label`-sourced and an `inferred`-sourced entity **in the same
stack** are rendered distinguishably; assert on the rendered artifact, not on an
internal flag."*

`group_entities()` promoted a group's `source` to `"label"` whenever **any**
member was label-confirmed, and the tier was rendered **only on the group
header**. Driving the oracle's exact scenario through the real renderer:

```
row_key='__group__app/web__phase_1'  ->  '  app/web  |  phase 1  (label)'
row_key='c-lab'                      ->  'lab-01'
row_key='c-inf'                      ->  'inf-01'
```

The two entities render **identically**, and the inferred one sits under a header
asserting `(label)`. This is verbatim the failure the handoff warned about — *"a
view that hides the tier hides that class of error"* — and the error it hides is
real: P76's review found the inference heuristic claiming unrelated containers.

The contract is about **entities** ("an entity grouped via `source="inferred"`
must be visually distinct from `source="label"`"), and the group key is fixed by
the handoff at `(stack, phase)`, so the tier cannot be split out into its own
group — it has to be marked per entity.

Worse, the package's own test **codified the bug as correct**
(`test_label_sources_promote_group_source`), and its Oracle-4 test
(`TestGroupHeaderRow`) only ever compared two *different* groups with different
sources — never two entities in one stack, which is the whole scenario.

**Fix.** Group tier is now the honest aggregate — `label` / `inferred` /
`SOURCE_MIXED` — never promoted; inferred **entities** carry an `(inferred)`
marker on their name cell. Rendered result:

```
'  app/web  |  phase 1  (mixed)'
'inf-01 (inferred)'
'lab-01'
```

Mutation-tested twice (restore the promotion -> red; drop the per-entity marker
-> red).

### F2 — the grouped view accepted `sort_by`/`sort_reverse` and ignored them (CONFIRMED, fixed)

`flagged-by-pass-1: no`

`render_data_table_container_grouped()` took `sort_by: str` and `sort_reverse:
bool | None` and never referenced either. Every other renderer routes through
`_sort_rows`. Consequences in the new view: `F6`/`s` sort cycling was a silent
no-op, P50's header-click sorting was a silent no-op, the status bar still
reported a sort mode that was not applied, and rows came out in dict-insertion
order. A control that looks like it works and does not is worse than an absent
one.

**Fix.** Entities within each group (and within the ungrouped block) now sort via
the same `_sort_rows` the flat container view uses; group order remains fixed by
`(stack, phase)`. Mutation-tested (ignore `sort_by` again -> red).

### F3 — minor (fixed)

- `action_toggle_view` re-declared the `("tree", "container", "ciu-grouped")`
  literal instead of using the `VIEW_MODES` it already imports — two sources of
  truth for the cycle order.
- Dead branch: `if not filtered and not needle: continue` followed by
  `if not filtered: continue`.
- `README.md` still said "`F5`/`t` toggles tree vs. container view" after the
  package made it a three-way cycle.

### F4 — Oracle 3 is satisfied, but only indirectly (ACCEPTED)

Oracle 3 wants a zero-ciu frame to render "byte-identical" to today. No test
asserts that directly; the defence is that `render_data_table_container` and
`render_data_table_tree` are untouched by the diff (true — the grouped view is a
third renderer, reached only from the new view mode) and the existing suite
covers them. Acceptable, and the full suite confirms no regression. Noted rather
than fixed: adding a byte-identical row-set assertion is worth doing, but it
belongs with the missing Textual integration test (see below), not bolted on
here.

## Out of scope, carried forward

The REPORT's own "Known gaps" #1 stands: there is **no Textual integration test**
for the new view mode — nothing presses `F5` twice and asserts the app reaches
`ciu-grouped` and renders. All P83 tests drive the renderer functions directly.
The view mode is therefore wired but never exercised end-to-end. Folded into the
P86 carve.

## Pass #1 overlap

The inline self-review found one unused import. It rated Oracle 4 "low hollow
risk" and Oracle 3 "low" — both wrong. **Substantive overlap: 0/2.** An inline
self-review is weaker still than a resumed one: it never re-read the diff cold.

## Gates (controller environment)

Environment: `/tmp/p79-venv` (Python 3.14.6; zstandard 0.25.0, textual 8.2.8,
pytest 8.4.2, mcp 1.28.1).

```
pytest topos/tests/test_grouping.py topos/tests/test_grouping_ui.py -q -W error -p no:schemathesis
  -> 38 passed          (33 original, corrected + 5 added at review)
timeout 900 pytest topos/tests -q -W error -p no:schemathesis
  -> 2 failed, 1365 passed
     both failures pre-exist on this branch's base and neither is attributable
     to P83:
       - test_zst_without_zstandard_exits_2  (the handoff names it explicitly:
         "P82 owns the repair. Do not attribute it to this package.")
       - test_pilot_snapshot_running_status_appears_immediately  (the P85 flake)
py_compile / git diff --check -> clean
```

Post-merge validation from `main` is recorded in P83-LOG.md.
