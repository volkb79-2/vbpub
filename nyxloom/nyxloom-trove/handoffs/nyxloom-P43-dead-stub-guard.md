---
schema_version: 1
id: nyxloom-P43-dead-stub-guard
project: nyxloom
title: "Guard: no role defined-but-never-dispatched (catch silent stubs)"
tier: sonnet5-high
input_revision: "f098cbf"
depends_on: []
session: fresh
source: {kind: product-goal, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/types.py"
    - "tests/test_types.py"
    - "nyxloom-trove/4-backlog.md"
  forbid:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/adapters.py"
oracles:
  - id: O1
    observable: "`types.py` gains `RESERVED_ROLES: frozenset[Role]` — the roles intentionally DEFINED but not yet dispatched, each justified by a backlog reference in a comment (currently exactly {Role.SELF_REVIEW}). A guard test in tests/test_types.py scans the dispatch source (daemon.py + reconcile.py text) for each Role instantiated at a dispatch site (`role=Role.<NAME>`), and asserts: (a) EVERY Role member is either found at a dispatch site OR in RESERVED_ROLES; (b) RESERVED_ROLES is disjoint from the dispatched set. NON-HOLLOW anchors the test MUST include: assert Role.SELF_REVIEW is NOT found at any dispatch site AND IS in RESERVED_ROLES; assert Role.IMPLEMENTER IS found at a dispatch site AND is NOT in RESERVED_ROLES (proving the scan actually distinguishes wired from stubbed)."
    negative: "no guard exists, so a role can be added to the enum + statefile schema and never wired to dispatch with nothing failing — exactly how SELF_REVIEW sat defined-but-dead through every per-package review (a package review checks its own contract, not 'is every enum member wired'). A hollow guard that just asserts `RESERVED_ROLES <= set(Role)` without verifying real dispatch sites also fails this oracle."
    gate: tester-unified
  - id: O2
    observable: "A backlog item is appended to nyxloom-trove/4-backlog.md tracking the deferred wiring: 'wire the SELF_REVIEW leg — an independent self-review attempt between IMPLEMENTER and FRONTIER_REVIEW, beyond P40's prompt-level self-review'. So the reserved role is TRACKED future work, not a silent park. Test/asserts: RESERVED_ROLES's comment references this backlog id, and backlog.md contains the item."
    negative: "SELF_REVIEW is reserved but untracked — still a silent stub, just relabelled; nothing points a future reader at the decision to wire or remove it."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "verifying real dispatch requires importing/executing daemon dispatch (side effects) rather than a static source scan — then implement the static scan (read the .py source text); do NOT import-and-run the daemon in a unit test"
  - "the only clean fix is to DELETE Role.SELF_REVIEW from the enum + statefile schema (YAGNI) rather than reserve it — that touches the schema (statefile.schema.json) which is fine to add to scope IF you take that path; if it also needs daemon.py/reconcile.py, BLOCKED"
---

# P43 — Guard against silent stubs (defined-but-never-dispatched roles)

`Role.SELF_REVIEW` was added to the enum (`types.py`) and the statefile schema
but **never dispatched** — a silent stub. No per-package review caught it (a
review checks that package's contract, not "is every enum member wired"), and no
backlog item tracked it, so it just sat. Add a **mechanical guard** so a future
defined-but-unwired role is caught by the gate, and TRACK the SELF_REVIEW
decision on the backlog (the operator's ask: "this should come up in review and
go to backlog, or not have the stub in the first place").

This is the durable fix — it does not rely on review vigilance. (P40 adds
prompt-level implementer self-review; the SELF_REVIEW *role* would be a separate
independent leg — reserved + backlogged here, not built.)

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P43-dead-stub-guard` from `main`);
commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these)

- `src/nyxloom/types.py` L133-136 — the `Role` enum (IMPLEMENTER, SELF_REVIEW,
  FRONTIER_REVIEW, CARVER). Add `RESERVED_ROLES: frozenset[Role]` after it, with a
  comment justifying each reserved member by backlog ref.
- `src/nyxloom/daemon.py` (READ only, forbidden) — the three dispatch sites
  `role=Role.CARVER` / `role=Role.IMPLEMENTER` / `role=Role.FRONTIER_REVIEW`
  (~L1251/L1472/L1810). These are the "wired" roles your test scans for.
- `tests/test_types.py` — add the guard test (scan daemon.py + reconcile.py
  source text for `Role.<NAME>` dispatch sites; assert the partition + the
  non-hollow anchors).
- `nyxloom-trove/4-backlog.md` — append the SELF_REVIEW-leg tracking item (mirror
  an existing `- **B<N>` entry's shape).

## Work

1. `types.py`: add `RESERVED_ROLES = frozenset({Role.SELF_REVIEW})` with a comment
   referencing the backlog item from step 3.
2. `tests/test_types.py`: the guard test — static-scan daemon.py+reconcile.py for
   dispatch sites; assert every Role is dispatched-or-reserved, disjoint; include
   the SELF_REVIEW (reserved, not dispatched) + IMPLEMENTER (dispatched, not
   reserved) non-hollow anchors.
3. `nyxloom-trove/4-backlog.md`: append the "wire the SELF_REVIEW leg" item.

## Scope / forbid

Touch ONLY `types.py`, `tests/test_types.py`, `nyxloom-trove/4-backlog.md`. Do NOT
edit `daemon.py`/`reconcile.py`/`adapters.py` — this guards dispatch, it does not
change it. (Exception per escalate_if: if you instead DELETE SELF_REVIEW, the
statefile schema may be added to scope.)

## BLOCKED rule

If the guard cannot verify real dispatch without importing/running the daemon, or
resolving SELF_REVIEW needs forbidden dispatch files, STOP — write `BLOCKED:
<reason>` to the LOG, commit, exit.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
