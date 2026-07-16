---
schema_version: 1
id: nyxloom-P41-direct-carve-from-brief
project: nyxloom
title: "Seed the carver with an intake brief (direct carve, no context loss)"
tier: sonnet5-high
input_revision: "f098cbf"
depends_on: []
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
# Re-carve of the REJECTED+stale nyxloom-P31 (its branch was 30 commits behind
# main — missing P34-P39 — so unmergeable; and its MERGE_READY was a pre-P33
# rubber-stamp). This is a FRESH implementation from current main. The rejected
# branch DID discover the right approach — re-apply it here, implemented against
# main (read main's actual files, do NOT cherry-pick the stale branch):
#   * backlog_items.is_briefed(item): True iff header-comment present AND detail
#     non-empty (create()/P29 ALWAYS writes a header; legacy free-prose bullets
#     never do) — NOT detail-alone (an un-headered bullet's continuation prose is
#     ordinary body text, not an intake brief). This fixed the "inverted detail
#     extraction" the first attempt was rejected for.
#   * brief_detail(cfg, item_id) gates on is_briefed, not raw detail.
#   * daemon.dispatch_targeted_carve(project, item_id) + a targeted
#     _carve_source_note_lines mode embedding ONLY that one item's brief.
scope:
  touch:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/backlog_items.py"
    - "tests/test_carve_from_brief.py"
  forbid:
    - "src/nyxloom/wrapper.py"
    - "src/nyxloom/intake_chat.py"
oracles:
  - id: O1
    observable: "When the carver assembles its carve-source notes (daemon.py `_carve_source_note_lines`) for a backlog item that carries an intake brief (P29 structured detail), the brief's pre-carve detail (aligned purpose, elicited detail, linked D-NNN, priority) is INCLUDED in the source notes the carver reads — so a carve of a briefed item loses no interview context. The test MUST use a GENUINELY briefed item (created via the P28/P29 create() path so it has a header comment + distinctive unique detail strings) and assert those SPECIFIC strings appear in the assembled source notes; AND assert the SAME item WITHOUT a brief (a legacy un-headered bullet, or header stripped) yields source notes that do NOT contain them — so a no-op implementation that ignores the brief FAILS. Do NOT test with an empty/placeholder brief (the first attempt was rejected precisely for a no-op that passed a hollow test)."
    negative: "the carver sees only the terse backlog title for a briefed item and re-derives everything from scratch (the context loss this closes); OR the phase is a no-op / inlines unrelated prose rather than the item's actual brief; OR is_briefed keys on raw detail so an un-headered legacy bullet's body prose is mis-treated as a brief (the 'inverted detail extraction' rejection)."
    gate: tester-unified
  - id: O2
    observable: "A briefed item can be carved on demand: daemon.dispatch_targeted_carve(project, item_id) (or an equivalent CLI verb/flag) dispatches a carver leg seeded with that specific item's brief — distinct from the untargeted headroom-refill carve — building the CarveDispatch through reconcile's carve-dispatch control flow (NOT a stub). A test asserts the targeted carve's spec/source references the chosen item's brief and only that item's."
    negative: "carving is ONLY the untargeted headroom refill, so a freshly-briefed feature cannot be carved directly and immediately; OR the targeted path is a no-op stub that never routes the brief through carve dispatch."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the targeted carve (O2) requires a change OUTSIDE scope.touch (wrapper.py/intake_chat.py, or a NEW event/state type) — then BLOCKED; do NOT improvise a no-op (the first-attempt failure). reconcile.py IS in scope — its carve-dispatch flow is where O2 lives."
  - "the brief detail cannot be surfaced to the carver without a new event-schema field"
---

# P41 — Seed the carver with an intake brief (direct carve, no context loss)

Closes the intake loop: the intake agent (P29) persists a structured brief; this
makes a carve of that item **pull the brief in**, so "direct carve" loses none of
the interview's pre-researched context — turning the front door (P29/P30) into
finished, carve-ready work with full continuity.

This RE-CARVES the rejected P31 from current main. See the frontmatter note for
the approach the rejected branch correctly discovered (`is_briefed` header-gate,
`dispatch_targeted_carve`) — re-apply it here, implemented against main.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P41-direct-carve-from-brief` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/backlog_items.py` — the parsed `BacklogItem` (its `header_line`
  and `detail` fields) + `brief_detail`. Add `is_briefed(item) -> bool` (header
  present AND detail non-empty) and make `brief_detail` gate on it. Read the
  `_build_item` parser to see how `detail` and the header comment are extracted —
  body prose lives BEFORE the header comment, never after.
- `src/nyxloom/daemon.py` — `_carve_source_note_lines` (assembles the carver's
  source notes; already trove-aware) and the carve-dispatch execution (role
  CARVER). You EXTEND `_carve_source_note_lines` to append a briefed item's
  detail, and add `dispatch_targeted_carve`.
- `src/nyxloom/reconcile.py` — the carve trigger + `CarveDispatch` planning
  (~L584 headroom-refill). O2's targeted carve routes through this; it IS in
  scope — implement it properly, do NOT stub.
- `tests/test_carve_from_brief.py` — the test file to (re)create; O1 uses a
  genuinely briefed item with distinctive strings, O2 asserts the targeted carve
  seeds that item's brief.

## Work

1. `backlog_items.py`: add `is_briefed(item)` (header present AND non-empty
   detail); make `brief_detail(cfg, item_id)` return detail only when
   `is_briefed`.
2. `daemon.py`: extend `_carve_source_note_lines` to include a briefed item's
   detail (plain backlog line otherwise); add `dispatch_targeted_carve(project,
   item_id)` seeding a carve leg with one item's brief.
3. `reconcile.py`: the targeted-carve entry through the carve-dispatch flow.
4. `tests/test_carve_from_brief.py`: prove O1 (brief detail in source notes for a
   real briefed item; absent for an un-briefed one) and O2 (targeted carve seeds
   the brief).

## Scope / forbid

Touch ONLY the four files in `scope.touch` — `reconcile.py`'s carve-dispatch
control flow IS in scope (O2 needs it). Do NOT edit `wrapper.py` or
`intake_chat.py`. If the contract genuinely needs a file outside scope, BLOCKED —
do NOT improvise a no-op to make a hollow test pass (the P31 first-attempt
failure).

## BLOCKED rule

If the contract cannot be met without a file OUTSIDE `scope.touch` (e.g.
`wrapper.py`, or a NEW event/state type), STOP — write `BLOCKED: <reason>` to the
LOG, commit, exit. NOTE: `reconcile.py` IS in scope — changing its carve-dispatch
flow is NOT a BLOCKED trigger. Never improvise a no-op to pass a hollow test.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
