---
schema_version: 1
id: nyxloom-P40-implementer-self-review
project: nyxloom
title: "Implementer self-reviews its work against the oracles before finishing"
tier: sonnet5-high
input_revision: "f098cbf"
depends_on: []
session: fresh
source: {kind: product-goal, ref: nyxloom-trove/3-roadmap.md}
scope:
  touch:
    - "src/nyxloom/adapters.py"
    - "tests/test_adapters.py"
  forbid:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/types.py"
    - "src/nyxloom/reconcile.py"
oracles:
  - id: O1
    observable: "The IMPLEMENTER dispatch prompt built in adapters.py gains a MECHANICAL, oracle-anchored self-review step: before committing/finishing, the agent is told to verify EACH of the handoff's oracles by running its observable in the gate and confirming it passes on REAL data (a test that would also pass on the oracle's NEGATIVE behavior is a hollow test — a defect to fix, not ship), to confirm every numbered Work step was met, and to fix findings or BLOCKED (never paper over). A test in tests/test_adapters.py builds an implementer dispatch (role implementer) and asserts the returned prompt contains this self-review directive via DISTINCTIVE substrings (e.g. 'self-review', 'each ... oracle', 'hollow')."
    negative: "the implementer prompt stays the five path lines (handoff/worktree/branch/gate/receipt) + the single commit-discipline sentence (today's state): the implementer never checks its own work, so the first quality gate is the expensive frontier-review — exactly how P31 shipped inverted-extraction + hollow tests into a reject cycle. A GENERIC 'please review your work' string (introspective, not oracle-anchored) also fails this oracle: AUTHORING §5 — models are poor at knowing what they missed, so the step must be trigger/oracle-based, not 'reflect on your work'."
    gate: tester-unified
  - id: O2
    observable: "The addition is PURELY ADDITIVE to the existing implementer prompt and stays within the adapter's argv_max budget: a test asserts the prompt STILL contains the commit-discipline sentence ('git add'/'git commit ... before finishing') and all five path lines, and that len(prompt) <= route.argv_max (default 1500) for a representative route — the self-review text did not truncate or replace existing content."
    negative: "the self-review text overruns argv_max (AdapterError at dispatch) or displaces the commit-discipline / path lines, breaking implementer dispatch or review-capture."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the self-review text cannot fit within adapters.py's argv_max budget alongside the existing lines without truncation — then BLOCKED; raise a packet-size D-NNN (do not silently drop existing lines)"
  - "delivering a per-oracle self-review requires a new DISPATCHED leg (SELF_REVIEW role in daemon.py/reconcile.py, both forbidden) rather than a prompt addition — then BLOCKED"
---

# P40 — Implementer self-reviews its work against the oracles before finishing

The implementer flow has **no self-review step**. The implementer's whole prompt
(`adapters.py`, `_build_implementer_argv`, ~L161-178) is five path lines + one
commit-discipline sentence; the adversarial-verify-against-oracles instruction
exists only in the SEPARATE frontier-review packet (`daemon.py` ~L1695-1739). A
`SELF_REVIEW` role is defined (`types.py`) and in the statefile schema but is
never dispatched. Result: the first quality gate is the expensive frontier
reviewer, and a whole reject→redo cycle burns when the implementer ships hollow
tests (live P31 lesson: "inverted detail extraction" + a green-but-hollow test
went straight to review and was rejected).

Add a **prompt-level** self-review step to the implementer dispatch — the
cheapest form of the discipline that earned its money in the pre-nyxloom
controller-workflow. It must be **mechanical and oracle-anchored** (AUTHORING §5:
models are demonstrably poor at knowing what they missed, so "reflect on your
work" yields false confidence — a trigger/oracle-based check works).

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P40-implementer-self-review` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these)

- `src/nyxloom/adapters.py` L158-229 — `_build_implementer_argv` (the function
  name may differ; it is the one returning `(argv, prompt)` for the implementer):
  the `prompt = (...)` block (L161-178) is where the five path lines + the
  commit-discipline sentence are assembled, and L184-189 enforces `argv_max`.
  You APPEND the self-review text to this prompt (before or after the commit
  sentence), keeping within `argv_max`.
- `src/nyxloom/daemon.py` L1695-1739 (READ only, forbidden) — the frontier
  reviewer's adversarial-verify checklist. Your self-review text is the
  implementer's SELF-applied, condensed analogue (each oracle → run its
  observable on real data; hollow tests; every Work step met) — NOT a second
  independent review, and it must not duplicate into the reviewer packet.
- `tests/test_adapters.py` — mirror its existing implementer-dispatch test to add
  O1 (self-review substrings present) and O2 (commit-discipline + path lines
  preserved, len <= argv_max).

## Work

1. `adapters.py`: append a concise, MECHANICAL self-review block to the
   implementer `prompt`. It must instruct the agent, before it commits/finishes:
   for EACH oracle in the handoff, run its observable in the gate and confirm it
   passes on real data (a test that would also pass on the oracle's negative is a
   hollow test — fix it); confirm every numbered Work step is met; fix findings,
   or write `BLOCKED: <reason>` if a contract cannot be met (never paper over).
   Keep it short enough to stay under `argv_max`.
2. `tests/test_adapters.py`: prove O1 (distinctive self-review substrings in the
   implementer prompt) and O2 (commit-discipline sentence + all five path lines
   still present; `len(prompt) <= route.argv_max`).

## Scope / forbid

Touch ONLY `adapters.py` + `tests/test_adapters.py`. Do NOT edit `daemon.py`,
`reconcile.py`, or `types.py` — this is a prompt addition, NOT a new dispatched
`SELF_REVIEW` leg (that is a separate, larger package).

## BLOCKED rule

If the self-review text cannot fit within `argv_max` without truncating existing
lines, or a per-oracle self-review genuinely needs a dispatched leg (forbidden
files), STOP — write `BLOCKED: <reason>` to the LOG, commit, exit.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
