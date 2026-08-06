---
schema_version: 1
id: assay-P10-mutation-changed-lines
project: assay
title: "Changed-line mutation behind the adapter protocol, with bounded jobs and no derived test command"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P08-canary-gate-integrity]
session: fresh
scope:
  touch:
    - "src/assay/mutation.py"
    - "src/assay/adapters/python.py"
    - "src/assay/adapters/go.py"
    - "src/assay/cli.py"
    - "tests/**"
  forbid:
    - "src/assay/adapters/protocol.py"
    - "src/assay/evaluate.py"
    - "src/assay/coverage/**"
oracles:
  - id: O1
    observable: "`git diff --name-only` for this package touches NO file under `src/assay/adapters/protocol.py`, `src/assay/evaluate.py` or `src/assay/coverage/` -- mutation FITS the protocol settled by P04-P08 rather than reshaping it"
    negative: "the most language-idiosyncratic component reshapes the adapter protocol after two adapters already depend on it -- the risk A-004 exists to contain"
    gate: tester-unified
  - id: O2
    observable: "an adapter returning UNSUPPORTED from `generate_mutants`, and a target selection that yields zero viable mutants, both render exit 5 / INCONCLUSIVE / NO_MUTANTS -- never PASS"
    negative: "'no supported mutants' reads as 'all mutants killed', the vacuous pass TESTING-METHODOLOGY explicitly names as INCONCLUSIVE_NO_MUTANTS"
    gate: tester-unified
  - id: O3
    observable: "there is NO code path deriving a test command from a source path; the lane's declared argv is the only command run, and `grep -rn 'test_' src/assay/mutation.py` finds no filename construction"
    negative: "nyxloom's `src/<mod>.py -> tests/test_<mod>.py` mapping survives -- AGENTS.md 4.2a anti-pattern #2, the consumer inventing on absence (A-012)"
    gate: tester-unified
  - id: O4
    observable: "mutant jobs are bounded by the lane's declared `mutation.jobs`, and the MutationResult is byte-identical regardless of completion order -- proven by running the same target set twice with different job counts"
    negative: "the verdict depends on which mutant subprocess finished first, or an unbounded fan-out starves the host"
    gate: tester-unified
  - id: O5
    observable: "on a CLEAN tree each mutant is isolated in its own scratch worktree and the live checkout is never written; a mutation run refuses on a dirty tree with NO_MEASUREMENT / DIRTY_TREE rather than falling back to mutating in place"
    negative: "a scratch worktree at HEAD silently tests DIFFERENT source than what is on disk -- a mutation gate testing the wrong source is a laundering gate"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the mutation catalogue cannot be expressed through `generate_mutants(text, lines) -> mutants` without adding a protocol method; that is the signal A-004 was warning about -- stop and report rather than widening protocol.py"
mutexes: []
---

# P10 — changed-line mutation

The claim to attack: **do changed lines have non-hollow tests?** — and,
structurally, **does mutation fit the protocol rather than reshape it?** O1 is
the containment A-004 promised, made mechanical.

## Context to read first

1. `/workspaces/vbpub/nyxloom/src/nyxloom/mutation_gate.py` — the mutation
   catalogue (`compare-swap`, `boolop-swap`, `bool-const-flip`, `falsy-swap`),
   the deterministic site ordering, and the two isolation strategies. **Note
   what NOT to port**: `_derive_test_command` (A-012), and the dirty-tree
   in-place fallback, which O5 replaces with a refusal.
2. `reference/TESTING-METHODOLOGY.md` "Mutation testing" — the cost model, the
   `--jobs` cap the doc asks for, and why `pytest -n auto` must not nest inside
   parallel mutant jobs.
3. `docs/DESIGN-GUIDE.md` §11 — `generate_mutants -> mutants | UNSUPPORTED`.

## Work

1. `adapters/python.py::generate_mutants` — the four operators, deterministic
   order, one mutation per mutant, returning full mutated source.
2. `adapters/go.py::generate_mutants` — returns `UNSUPPORTED` (A-011).
3. `mutation.py` — target selection from changed lines, bounded fan-out honouring
   `mutation.jobs`, per-mutant worktree isolation, order-independent aggregation.
4. `cli.py` — `assay mutate`.

## Why this package is last

The adapter protocol is settled by P04 (coverage), P06 (second language) and P08
(canary) before mutation arrives, so mutation must fit a protocol that two other
consumers already depend on. Landing it earlier would let the most idiosyncratic
component define the seam.
