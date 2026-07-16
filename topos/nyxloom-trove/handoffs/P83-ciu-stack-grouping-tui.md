# P83 - CIU stack grouping in the TUI

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P76 (merged - CiuMeta on the entity)
> **Base:** main
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** grouping requires a change to `CiuMeta` or to the collector (it must not -- P76 already puts everything needed on the entity); or Textual cannot express the grouped view without a dependency bump.

<!--
CARVE SOURCE (controller-workflow-v2 §8): **roadmap-driven** (Optional-plugins
bucket residue). docs/ROADMAP.md, after P76: "The TUI-grouping and ciu-gated-action
successors remain as the bucket's residue." TUI-SPEC §4.3 already specifies the
grouping and the numeric-phase rule, so this is not a cold carve.
-->

## Goal

Group the entity table by ciu stack and phase, using the `CiuMeta` that P76 now
puts on every entity. P76 shipped **detection only** -- it deliberately ships no
grouping or ordering code, so this package writes the first real consumer of that
metadata.

## The trap this carve is naming

P76's own test suite contained grouping and phase-ordering "oracles" that
hand-built `CiuMeta` objects and then grouped and sorted them **with a lambda
defined inside the test**. They passed against an implementation that did not
exist. The P76 review deleted them rather than leave false coverage behind.

So: **every grouping and ordering assertion in this package must drive topos's
grouping code**, not a comprehension written in the test file. If you find
yourself writing `sorted(metas, key=lambda m: ...)` in a test, you are testing
CPython, and the reviewer will read it as a hollow test.

The specific numeric rule to get right (TUI-SPEC §4.3, and the reason
`CiuMeta.phase` is an `int` and not a string):

- `phase_2` sorts **before** `phase_10`. A lexicographic sort puts `phase_10`
  first and is wrong.
- A phase that is **present but unparseable** (`CiuMeta.phase_raw` set,
  `CiuMeta.phase is None`) is a *different state* from **no phase at all** (both
  `None`). P76's review fixed exactly this collapse in the parser; do not
  re-collapse it in the view. Decide and document where each sorts -- an unknown
  phase must not silently sort as `0`.

## Required Contracts

- Grouping is a **pure function over entities**, unit-testable without Textual,
  living outside `src/topos/ui/` (spec §6.1: Textual only under `ui/`). The UI
  layer renders what it returns.
- Group key is `(stack, phase)`. Entities with `ciu is None` are **not** forced
  into a synthetic group -- they keep their existing tree/container placement.
  Ungrouped is a real state, not a bucket called "other".
- The two detection tiers stay distinguishable in the view: an entity grouped via
  `source="inferred"` must be visually distinct from `source="label"`, because
  inference is a heuristic and the operator needs to know which is which. (The
  P76 review found the inference heuristic claiming unrelated containers; a view
  that hides the tier hides that class of error.)
- No new collector work, no new subprocess, no `ciu` invocation. Everything needed
  is already on the entity.

## Acceptance Oracles (numbered, adversarial)

1. **Numeric phase ordering, driven by topos's code.** A stack with phases 1, 2,
   and 10 renders in that order. Assert against the grouping function's output.
   A test that sorts the list itself proves nothing.
2. **Unparseable phase does not sort as zero.** An entity with `phase_raw="phase_x"`,
   `phase=None` is placed per the documented rule and is distinguishable from an
   entity with no phase label at all.
3. **Ungrouped entities are untouched.** A frame with zero ciu-managed containers
   renders exactly as it does today -- byte-identical row set. (Guard against the
   grouping code quietly restructuring the normal view.)
4. **Tier is visible.** A `label`-sourced and an `inferred`-sourced entity in the
   same stack are rendered distinguishably; assert on the rendered artifact, not
   on an internal flag.
5. Mixed frame: two stacks, three phases, plus ungrouped entities and a container
   whose `ciu` is `None` -- exact group membership sets, no entity lost, no entity
   duplicated across groups.

## Out Of Scope

- ciu-gated **actions** (the other half of the bucket residue -- separate package;
  actions keep their root/admin/typed-confirmation/audit posture and grouping does
  not shortcut it).
- Changing `CiuMeta`, the collector, or the detection heuristics (P76 owns those;
  the review just hardened them).
- The `[ciu] known_stacks` config surface.

## Gates

```bash
PYTHONPATH=topos/src python3 -m pytest <focused P83 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result. Write P83-LOG.md / P83-REPORT.md.

Note: `topos/tests/test_report.py::TestReportCLI::test_zst_without_zstandard_exits_2`
currently fails on unmodified `main` (P82 owns the repair). Do not attribute it to
this package.
