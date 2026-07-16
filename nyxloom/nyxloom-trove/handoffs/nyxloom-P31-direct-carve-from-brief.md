---
schema_version: 1
id: nyxloom-P31-direct-carve-from-brief
project: nyxloom
title: "Seed the carver with an intake brief (direct carve, no context loss)"
tier: sonnet5-high
input_revision: "e329de2"
depends_on: [nyxloom-P28-backlog-schema-autotick, nyxloom-P29-intake-agent-backend]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/backlog_items.py"
    - "tests/test_carve_from_brief.py"
  forbid:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/intake_chat.py"
oracles:
  - id: O1
    observable: "When the carver assembles its carve-source notes (daemon.py `_carve_source_note_lines`) for a backlog item that carries an intake brief (P29's structured detail), the brief's pre-carve detail (aligned purpose, elicited detail, linked `D-NNN` decisions, priority) is INCLUDED in the source notes the carver reads — so a carve of a briefed item loses no interview context. A test asserts the assembled source notes contain the brief's detail for a briefed item, and fall back to the plain backlog line for an un-briefed one."
    negative: "the carver sees only the terse backlog title for a briefed item and re-derives everything from scratch — the 'loss of context' this phase exists to prevent"
    gate: tester-unified
  - id: O2
    observable: "A briefed item can be carved on demand (a targeted-carve entry — CLI verb or flag — that dispatches a carver leg seeded with that specific item's brief), distinct from the untargeted headroom-refill carve. A test asserts the targeted carve's spec/source references the chosen brief."
    negative: "carving is ONLY the untargeted headroom refill, so a freshly-briefed feature cannot be carved directly and immediately"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "seeding the carve requires changing the carve-dispatch control flow in reconcile.py (forbidden — propose it as a D-decision)"
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
  targeted-carve entry that seeds a carve with one item's brief. Do NOT change
  the carve-dispatch control flow itself (that lives partly in reconcile.py,
  forbidden).
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

Touch ONLY the three files in `scope.touch`. Do NOT change carve-dispatch
control flow in `reconcile.py` (forbidden) or edit `intake_chat.py` (P29's).

## BLOCKED rule

If seeding the carve requires reconcile.py control-flow changes or a new
event-schema field, STOP — write `BLOCKED: <reason>` to the LOG, commit, exit;
raise it as a `D-<NNN>`.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
