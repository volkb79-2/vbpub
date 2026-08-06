---
schema_version: 1
id: assay-P08-canary-gate-integrity
project: assay
title: "Canary: prove a declared gate actually REJECTS broken code and enforces its coverage floor"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P07-runner-cli-verdict-emission]
session: fresh
scope:
  touch:
    - "src/assay/canary.py"
    - "src/assay/adapters/python.py"
    - "src/assay/adapters/go.py"
    - "src/assay/cli.py"
    - "tests/**"
  forbid:
    - "src/assay/evaluate.py"
    - "src/assay/coverage/**"
oracles:
  - id: O1
    observable: "`inject_import_break` and `inject_uncovered_line` are PURE -- `(text) -> (text, description)` -- and are unit-tested with no filesystem; the Python import-break insertion lands AFTER any module docstring and `from __future__` imports, and the result parses"
    negative: "injection writes the file itself (nyxloom's shape), so adapters cannot be tested without a filesystem, and a `from __future__` import moves and the canary fails for a syntax reason rather than the intended one"
    gate: tester-unified
  - id: O2
    observable: "a canary is built as a real commit in a disposable worktree that is ALWAYS removed, and the commit survives the worktree's removal so the runner can check it out fresh"
    negative: "the canary mutates the live checkout, or leaks scratch worktrees on every verify"
    gate: tester-unified
  - id: O3
    observable: "verify reports KILLED when any attempt genuinely fails the gate; LAUNDERS only when EVERY attempt exits 0; and INCONCLUSIVE (exit 5 / CANARY_INCONCLUSIVE) when no attempt survives cleanly but every non-zero exit was itself a timeout or exec failure"
    negative: "a gate that never rendered a verdict on the bad code is reported as trustworthy, or one bad file pick is reported as LAUNDERS"
    gate: tester-unified
  - id: O4
    observable: "candidate discovery never leaves the project's own subtree, and never selects a test file, a VCS directory, or a scratch worktree; a project that self-hosts as a subdirectory of a larger repo picks only its own files"
    negative: "the canary wanders into a sibling project the gate never runs against, deterministically reporting LAUNDERS for a strong gate"
    gate: tester-unified
  - id: O5
    observable: "the uncovered-line canary is valid, typed and side-effect-free, so a gate that runs tests but has no coverage floor ACCEPTS it while one with a floor REJECTS it -- proven against two fixture lanes differing only in their rigor declaration"
    negative: "the canary fails for a syntax, lint or test reason, over-crediting the coverage assert"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "the Go inject_uncovered_line cannot avoid an unused-symbol failure; export the symbol rather than suppressing the linter, and report if that is still insufficient"
mutexes: []
---

# P08 — canary

The claim to attack: **does the gate demonstrably reject?** This is the only
rigor level (R3) whose failure invalidates every other result already recorded.

## Context to read first

1. `/workspaces/vbpub/nyxloom/src/nyxloom/gate_canary.py` — the entire module
   docstring is the specification. Every design choice listed there closes a
   review finding on a first cut that was wrong; do not re-derive them.
   Specifically: subtree scoping, why import-break rather than AST mutation
   (`ast.unparse` reformats the whole file, so a lint-checking gate "detects" the
   canary for the wrong reason), and why multi-attempt.
2. `docs/DESIGN-GUIDE.md` §3 (Tier 1), §7 (assay is not a test runner).

## Work

1. `adapters/*.py` — fill in the two `inject_*` methods, PURE (A-010).
   Go: `func init() { panic("assay-gate-canary") }` for import-break; an
   **exported** never-called function for uncovered-line, so no unused-symbol
   lint fires (nyxloom documents that caveat for Python; Go can avoid it).
2. `canary.py` — candidate discovery (deterministic ranking), commit building,
   the multi-attempt loop, and the KILLED / LAUNDERS / INCONCLUSIVE rollup.
3. `cli.py` — `assay verify`.

## Note for self-hosting

`assay verify` against assay's own lane is a **second, non-independent** layer.
The independent oracle is the P06 fixture suite asserted by pytest. Document
that in the verb's help text so it is not mistaken for the proof (A-041).
