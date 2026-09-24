---
schema_version: 1
id: assay-P80-js-default-arg
project: assay
title: "Classify istanbul default-argument signature lines from their enclosing function"
tier: frontier-review
input_revision: "db29266f8a006b22a30609a74de7645d1e4c50b7"
source: {kind: backlog, ref: "nyxloom-trove/4-backlog.md#b080"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/assay/coverage_parsers/coverage_istanbul_json.py"
    - "tests/test_coverage_istanbul_default_arg_signature.py"
    - "tests/test_coverage_istanbul_contradictory_branch_arcs.py"
    - "tests/test_evaluate_javascript_end_to_end.py"
    - "src/assay/adapters/javascript.py"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "README.md"
    - "CHANGES.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/4-backlog.md"
    - "nyxloom-trove/handoffs/assay-P80-js-default-arg.md"
    - "tests/fixtures/coverage/coverage-istanbul-json.default-arg-signature.json"
  forbid:
    - "src/assay/coverage_parsers/model.py"
    - "src/assay/errors.py"
    - "src/assay/schemas/verdict.schema.json"
    - "src/assay/verify.py"
    - "nyxloom-trove/nyxloom.toml"
oracles:
  - id: O1
    observable: "The committed ChartCard-shaped real artifact classifies line 34 as executed from f[0] == 9 and preserves branch coverage 1/1; a changed-lines require_branch lane judges it through the CLI."
    negative: "Ignoring fnMap/f leaves the line unclassified and drops the branch from judgment."
    gate: tester-unified
  - id: O2
    observable: "A default count of zero with f > 0 classifies the signature line executed while retaining a 0/1 branch; f == 0 classifies it missing."
    negative: "Using the branch count as the function hit count misclassifies one of the two states."
    gate: tester-unified
  - id: O3
    observable: "A nonzero arc explicitly on a missing line still raises at FileCoverage construction; B054 zero-count braceless-if records remain isolated by name."
    negative: "Removing either independent integrity check or applying the rule without checking branch type changes the B054 witness."
    gate: tester-unified
  - id: O4
    observable: "Both judged and bystander default-arg records keep their arcs and are not named contradictory; the existing B054 witness keeps its current behavior."
    negative: "The old type-blind B054 path either refuses changed files or silently alters the B054 disposition."
    gate: tester-unified
  - id: O5
    observable: "The real dstdns specimen re-derives six newly classified executable lines across four files, and no previously-PASS lane changes its number."
    negative: "A line-count comparison against the measured artifact or changed-line refusal regression disagrees."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "A statement-less default-arg node in the committed real-shaped fixture maps to zero or multiple function entries by decl.start <= node < loc.start."
  - "The implementation would need a change to FileCoverage invariants, B054's disposition, a verdict field, a reason code, or lane schema."
  - "A previously-PASS verdict changes its judged line or branch number."
mutexes: [merge-lane]
---

# P1 — B080: default-argument branches on signature lines

## Dispatch contract

- Contract class: **2c — bounded parser integration**.
- The carver selected parser-level shape C after measuring the read-only
  dstdns artifact. The six statement-less default-argument branches map to
  exactly one function using `fnMap.decl.start <= branchMap.loc.start <
  fnMap.loc.start`; line-only matching would be ambiguous for half of them.
- Required role: fresh implementer, followed by a fresh independent adversarial
  reviewer. The current main tip for this carve is `db29266f`.
- Work only in `/workspaces/vbpub/.worktrees/assay-b080-js-default-arg` on
  branch `assay-b080-js-default-arg`. Before its gate, merge the then-current
  `main` into this branch if `main` has moved.

## Context to read first

1. `AGENTS.md` (repository root); `nyxloom/reference/AUTHORING.md`,
   `nyxloom/reference/STANDARD.md`, and `nyxloom/reference/DOCTRINE.md`.
2. `nyxloom-trove/4-backlog.md` sections B080, B089, and B054; decisions
   A-342, A-344, and A-410; the B080 audit addendum in the wave prompt.
3. `src/assay/coverage_parsers/coverage_istanbul_json.py` in full,
   `src/assay/coverage_parsers/model.py` `FileCoverage.__post_init__`,
   `src/assay/adapters/javascript.py`, and `src/assay/evaluate.py`'s changed
   line / branch tally path.
4. `tests/test_coverage_istanbul_contradictory_branch_arcs.py`,
   `tests/test_coverage_istanbul_branch_arcs.py`,
   `tests/test_evaluate_javascript_end_to_end.py`, and
   `tests/test_cli_run_javascript.py`.
5. `docs/DESIGN-GUIDE.md` §11 and `docs/CONSUMERS.md`'s JavaScript/TypeScript
   sections. Read `README.md` and `CHANGES.md` for their product-facing roles.
6. The full Wave C contract in
   `nyxloom-trove/WAVE-PROMPT-2026-09-23-wave-c-refusals-js-liveness.md` §5.

## Implementation packet (normative)

### Parser-owned rule

The owner is `coverage_istanbul_json._parse_record`. Keep `FileCoverage`'s
invariants unchanged. For an arc-bearing artifact only:

1. Parse the already validated `default-arg` branch entry's node start from
   `branchMap[id].loc.start` as a `(line, column)` position.
2. Only when that physical line is absent from statement-derived
   `executed | missing`, find the unique `fnMap` entry whose function
   declaration contains the node position:
   `decl.start <= node_start < loc.start`, comparing `(line, column)` tuples.
3. Read that exact entry's `f[id]`. A positive integer classifies the
   signature line as executed; zero classifies it as missing. A missing,
   malformed, or ambiguous mapping is `ERROR/UNREADABLE_ARTIFACT`; never guess
   from the default-argument branch's own `b[id]` count.
4. If more than one qualifying default-arg node targets an otherwise
   unclassified physical line, the line is executed if any mapped function
   call count is positive, and missing only when all are zero. Never replace a
   statement-derived classification already present on that line.
5. Keep the branch arcs exactly as emitted. A missing line with a nonzero arc
   still hits the independent `tampered_missing` check. Non-default branch
   types on neither-bucket lines still follow B054's current drop-and-name
   behavior.

Do not read or validate `fnMap`/`f` for files with no statement-less
`default-arg` node; those fields remain otherwise irrelevant to line
classification. Do not change the verdict or lane schema, reason-code
vocabulary, `FileCoverage`, `evaluate`, or B054 policy.

### Measured fixture and expected result

`tests/fixtures/coverage/coverage-istanbul-json.default-arg-signature.json` is
a carver-prepared minimal copy of the real `ChartCard.tsx:34` shape from the
read-only consumer artifact. Its actual coordinates are declaration start
`34:16`, branch node start `34:72`, body start `34:104`; `f[0] = 9` and
`b[0] = [9]`. It preserves the producer's metadata while omitting unrelated
statement/function/branch entries. Tests read only this committed fixture,
never a dstdns path. The pre-fix parser was probed against it and returned
`executed=[]`, `missing=[]`, `branches={}`, `contradictory={34}`: the exact
drop-and-continue behavior this package must replace for this branch type.

On the full consumer artifact, the rule adds six executable signature lines:
ChartCard 1, DataTable 3, StatCard 1, StatTile 1. Their old executable-line
counts were 54, 105, 1, and 37; new counts are 55, 108, 2, and 38 (six total).
The `b` counts are not function-call counts: StatCard is `f=18, b=[15]` and
StatTile is `f=26, b=[7]`. Branch counts therefore cannot classify the source
line.

### Required behavioral proof

- Cover nonzero `b` with `f > 0`, `b=[0]` with `f > 0`, and `f=0` with
  `b=[0]`. Assert line buckets, unchanged arc counts, and counted
  `require_branch` totals.
- Exercise a `tampered_missing` nonzero arc directly at `FileCoverage` level;
  it must still raise independently.
- Add a B054-shaped zero-count braceless-`if` witness. Run both controlled
  wrong implementations described in Wave C §5.3: M1 removes invariant 3 and
  its parser-side `unconsidered` isolation half; M2 applies the new mapping to
  every branch type. Each named oracle must go red, and the `tampered_missing`
  oracle stays green under M1.
- Through the real CLI, judge a JS changed-lines lane with declared
  `producer = "istanbul"` and `require_branch = true`. Cover both a judged
  default-arg file and an out-of-diff bystander; neither becomes a
  contradictory-line report after the fix, while B054 remains unchanged.
- State and test why no previously-PASS number changes: judged default-arg
  signature lines previously caused a refusal, and default-arg lines outside a
  changed-lines judged set do not contribute to its number. Whole-target cases
  that were refused become judged and gain the six measured executable lines.

## Work

1. Implement the parser-owned rule and its narrow malformed-metadata refusals.
2. Add the behavior tests and the CLI integration above; keep the default-arg
   witness independent of any dstdns checkout.
3. Update A-342's adapter guarantee, parser documentation, DESIGN-GUIDE §11,
   the JavaScript consumer guide, assay README feature summary, and
   `CHANGES.md`'s `## [Unreleased]` entry in this same package.
4. Record A-456, mark B080 DONE and cross-reference B089's withdrawn duplicate
   status. State B054 is unchanged, list the rejected B′/D alternatives and
   the zero-count ruling, and quantify the six-line denominator change.
5. Run focused tests serially under `nice -n19 ionice -c3` from `assay/` with
   `PYTHONPATH=src`; do not run the Docker-reaching dstdns SQL qualification.
   Then run the registered `tester-unified` gate before merge.

## Out of scope

Do not change `FileCoverage`, B054's refusal/drop boundary, verdict or lane
schemas, reason codes, evaluator semantics, or consumer lane configuration.
Do not edit dstdns; the committed fixture is the only consumer artifact input.

## BLOCKED rule

**BLOCKED:** stop and report immediately if the measured fixture fails the unique
function mapping, a compatible record requires schema/model/B054 changes, a
previously-PASS lane changes its number, or the required gate cannot be
launched under the host's verified gate cgroup. Do not substitute branch
counts, guess an enclosing function, or relax an invariant. Product choices
outside the fixed rule require a decision row and operator answer before code
continues.
