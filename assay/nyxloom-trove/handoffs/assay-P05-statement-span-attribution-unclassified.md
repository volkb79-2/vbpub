---
schema_version: 1
id: assay-P05-statement-span-attribution-unclassified
project: assay
title: "Multi-line-statement attribution and the unclassified third bucket -- ambiguity is never passed silently"
tier: implement-2
input_revision: "d87f028b"
source: {kind: product-goal, ref: "docs/DESIGN-GUIDE.md"}
stack: none
depends_on: [assay-P04-evaluate-core-adapter-protocol-python-adapter]
session: resume evaluate
scope:
  touch:
    - "src/assay/evaluate.py"
    - "src/assay/adapters/python.py"
    - "tests/**"
  forbid:
    - "src/assay/coverage/**"
    - "src/assay/measurability.py"
oracles:
  - id: O1
    observable: "a changed line inside a multi-line dict/list/call literal inherits its enclosing statement's covered/uncovered status; with attribution disabled the same line vanishes from BOTH numerator and denominator (the pre-P80 behaviour), proving the recovery is what changed the verdict"
    negative: "a real, changed, possibly-untested line silently disappears, producing an unexplained 0/0 pass -- confirmed live on dstdns-P77"
    gate: tester-unified
  - id: O2
    observable: "a COMPOUND statement (If/For/While/Try/With/FunctionDef/ClassDef/ExceptHandler/Match) claims only its own HEADER span, so a nested statement's independently-tracked coverage is never swallowed by its enclosing block; a one-line compound (`if x: return 1`) clamps to a single-line span rather than an end-before-start one"
    negative: "every line of an `if` body inherits the `if` line's status, silently crediting untested nested code as covered"
    gate: tester-unified
  - id: O3
    observable: "a multi-line DECORATOR's continuation lines and a multi-line match-case PATTERN's continuation lines each inherit their own construct's status, not the enclosing node's"
    negative: "a line inside a never-taken `case` inherits the always-executed `match` line and is reported covered -- worse than the vanish this fixes"
    gate: tester-unified
  - id: O4
    observable: "a changed line inside some statement's span whose enclosing statement is ITSELF untracked lands in `unclassified` and FAILS with reason_code UNCLASSIFIED_LINES, with no opt-out flag; an unparseable file puts ALL its unattributed changed lines there"
    negative: "the gate guesses at genuine ambiguity, silently passing OR silently failing past it"
    gate: tester-unified
  - id: O5
    observable: "a bare string-literal statement (module/class/function docstring) is excluded from spans, so a multi-line docstring's interior lines do NOT land in unclassified; a comment or blank line between statements inherits nothing and is correctly ignored"
    negative: "every multi-line docstring in a changed file fails the gate as ambiguous -- correct by the letter of the rule, wrong in spirit"
    gate: tester-unified
gates: ["tester-unified"]
escalate_if:
  - "attribution requires the core to import ast directly rather than calling through the adapter -- that breaks P04 O1"
mutexes: []
---

# P05 — statement-span attribution and the unclassified bucket

The claim to attack: **is genuine ambiguity ever passed silently?**

This is dstdns's B065/P80 work, the single largest behaviour the other three
copies lack. It arrives behind the adapter (`statement_spans` is an adapter
method, `attribute_line` is core), which is what P04's O1 protects.

## Context to read first

1. `/workspaces/dstdns/scripts/coverage_gate.py` — the block comment above
   `_COMPOUND_STATEMENT_TYPES` is the specification; `statement_spans`,
   `_first_nested_line`, `attribute_line` and the `unattributed` branch of
   `evaluate` are the implementation. **Read the comments, not just the code**:
   both the decorator and match-case cases were found in review, after the first
   pass shipped.
2. `docs/DESIGN-GUIDE.md` §11 — `requires_span_attribution`, and why a
   block-based format (Go) sets it False and needs none of this.

## Work

1. `adapters/python.py::statement_spans(text) -> list[Span] | None` — `None` on a
   real SyntaxError, which callers must treat as "cannot attribute anything
   here", never as a guess.
2. `evaluate.py::attribute_line` — smallest (innermost) containing span wins.
3. The `unclassified` bucket in `Verdict`, with **no opt-out flag** (unlike
   `excluded`): this surfaces a tool limitation, never a developer choice.

## Validate against real source, not only synthetic cases

Run the attribution over this repo's own files and over a sample of dstdns's
`libs/common/src`. The decorator and match-case gaps were both invisible to
synthetic fixtures.
