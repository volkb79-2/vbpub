---
schema_version: 1
id: nyxloom-P31-direct-carve-from-brief
project: nyxloom
title: "Seed the carver with an intake brief (direct carve, no context loss)"
tier: sonnet5-high
input_revision: "685e8b7"
depends_on: [nyxloom-P28-backlog-schema-autotick, nyxloom-P29-intake-agent-backend]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
# RE-AUTHORED 2026-07-16 after the first attempt was REJECTED. Root cause was a
# handoff DEFECT, not just the implementer: reconcile.py was FORBIDDEN, but O2
# (targeted carve) genuinely needs the carve-dispatch control flow that lives in
# reconcile.py — so the task should have BLOCKED, but a cheap pass improvised a
# no-op for briefed items + a hollow test (515 green, opposite of the oracle on
# real data). Fixes: reconcile.py is now IN SCOPE; re-tiered to sonnet; O1
# tightened so a no-op cannot pass.
scope:
  touch:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/backlog_items.py"
    - "tests/test_carve_from_brief.py"
  forbid:
    - "src/nyxloom/wrapper.py"
oracles:
  - id: O1
    observable: "When the carver assembles its carve-source notes (daemon.py `_carve_source_note_lines`) for a backlog item that carries an intake brief (P29's structured detail), the brief's pre-carve detail (aligned purpose, elicited detail, linked `D-NNN` decisions, priority) is INCLUDED in the source notes the carver reads — so a carve of a briefed item loses no interview context. The test MUST use a GENUINELY briefed item (a backlog item whose brief carries distinctive, unique content strings via P28/P29's structured detail) and assert those SPECIFIC strings appear in the assembled source notes; AND assert that the SAME item WITHOUT a brief yields source notes that do NOT contain them (so an implementation that ignores the brief — a no-op — fails). Do NOT test with an empty/placeholder brief. (First attempt was rejected precisely for being a no-op on real briefed items while passing a hollow test.)"
    negative: "the carver sees only the terse backlog title for a briefed item and re-derives everything from scratch — the 'loss of context' this phase exists to prevent; OR the phase is a no-op that inlines unrelated/legacy prose rather than the item's actual brief (the rejected first-attempt failure mode)"
    gate: tester-unified
  - id: O2
    observable: "A briefed item can be carved on demand (a targeted-carve entry — CLI verb or flag — that dispatches a carver leg seeded with that specific item's brief), distinct from the untargeted headroom-refill carve. A test asserts the targeted carve's spec/source references the chosen brief."
    negative: "carving is ONLY the untargeted headroom refill, so a freshly-briefed feature cannot be carved directly and immediately"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the targeted-carve (O2) requires a change OUTSIDE scope.touch (wrapper.py, or a NEW event/state type) — then BLOCKED; do NOT improvise a no-op (the first-attempt failure mode)"
  - "the brief detail cannot be surfaced to the carver without a new event-schema field"
---

# P31 — Seed the carver with an intake brief (direct carve, no context loss)

Phase **δ** — closes the intake loop. The intake agent (P29) persists a
structured brief; this phase makes a carve of that item **pull the brief in**,
so "direct carve" loses none of the interview's pre-researched context. Turns
the front door (P29/P30) into finished, carve-ready work with full continuity.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P31-direct-carve-from-brief` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first

- `src/nyxloom/daemon.py` — the carve mechanism: `_carve_source_note_lines`
  (assembles the source notes naming backlog/roadmap for the carver; already
  trove-aware) and `CarveDispatch` execution (the FRONTIER carver leg, role
  CARVER, `carve-<project>-<seq>` task). Read both. You EXTEND
  `_carve_source_note_lines` to append a briefed item's detail; you add a
  targeted-carve entry that seeds a carve with one item's brief. O2's targeted
  carve WILL require touching the carve-dispatch control flow in `reconcile.py`
  (`CarveDispatch` planning / the carve trigger at reconcile.py ~584) — that IS
  in scope now; implement it properly, do NOT stub or no-op it.
- `src/nyxloom/backlog_items.py` (P28/P29) — the structured item + brief detail
  accessor. Add a `brief_detail(item) -> str | None` if not already exposed.
- `nyxloom-trove/backlog.md` — items with vs. without a brief (the two cases
  O1 distinguishes).

## Work

1. `src/nyxloom/backlog_items.py`: expose a briefed item's pre-carve detail
   (accessor) if P28/P29 didn't already.
2. `src/nyxloom/daemon.py`: extend `_carve_source_note_lines` to include the
   brief detail for a briefed item (plain backlog line otherwise); add a
   targeted-carve entry (CLI verb or flag) that dispatches a carver leg seeded
   with a specific item's brief.
3. `tests/test_carve_from_brief.py`: prove O1 (brief detail in source notes;
   fallback for un-briefed) and O2 (targeted carve seeds the brief).

## Scope / forbid

Touch ONLY the four files in `scope.touch` — `reconcile.py`'s carve-dispatch
control flow IS in scope now (O2's targeted carve needs it). Do NOT edit
`wrapper.py` or `intake_chat.py` (P29's). If the contract genuinely needs a file
outside `scope.touch`, BLOCK — do NOT improvise a no-op to make a hollow test
pass (that is exactly why the first attempt was rejected).

## BLOCKED rule

If the contract genuinely cannot be met without a file OUTSIDE `scope.touch`
(e.g. `wrapper.py`, or a NEW event/state type), STOP — write `BLOCKED: <reason>`
to the LOG, commit, exit; raise it as a `D-<NNN>`. NOTE: `reconcile.py` IS in
scope — changing its carve-dispatch flow is NOT a BLOCKED trigger. Never
improvise a no-op to make a hollow test pass (the first-attempt failure).

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
