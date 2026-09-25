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
    observable: "Both judged and bystander default-arg records with matching signature-line arms keep their arcs and are not named contradictory; the existing B054 witness keeps its current behavior."
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
2. **A-459 operator narrowing, 2026-09-24:** proceed only when that physical
   line is absent from statement-derived `executed | missing` AND at least
   one arm of that same branch is attributed to the node's physical line by
   the existing `_arm_line` location/fallback rule. If none matches, leave
   the node line unclassified, keep existing arc aggregation/disposition
   unchanged, and do not read function metadata for this node. Otherwise,
   find the unique `fnMap` entry whose function
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
`default-arg` node with a matching signature-line arm; those fields remain otherwise irrelevant to line
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
- State and test why no previously-PASS number changes: recovered default-arg
  signature lines necessarily carried an arc on that same previously
  unclassified line and therefore caused a refusal when their file was judged;
  default-arg lines outside a
  changed-lines judged set do not contribute to its number. Whole-target cases
  that were refused become judged and gain the six measured executable lines.
- Pin A-459's hostile multiline case: node 34, arm/statement 35, body 36.
  The old and narrowed parsers keep a changed-node-line PASS at 0/0; the
  broader function-call rule changes its line count to 1/1. Preserve the
  unmatched signature gap and document the broader alternative's consequence.

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

## Implementation evidence — 2026-09-24

**Resolved by operator ruling, A-459 (2026-09-24):** recover only nodes with
a matching signature-line arm of the same branch. The multiline counterexample
below retains its prior 0/0 PASS. Implementation has resumed; independent review
and the controller's authoritative gate remain pending. This is not a ship receipt.

The fixed shape-C implementation and acceptance tests are complete locally.
It preserves all model/evaluator invariants, scopes function metadata reads
to statement-less default nodes with matching signature-line arms, validates full start positions and the exact
matched function count, and charges newly classified lines to the existing
artifact budget. B054's rule is unchanged. The decision ledger's pre-existing
A-456 row was retained; A-459 appends the operator's narrower rule and the
broader alternative's consequences. Reserved A-457/A-458 were not used.

### Focused checks

Run serially from `assay/`, with the estate interpreter and source path:

```sh
nice -n19 ionice -c3 env PYTHONPATH=src /home/vscode/.venv/bin/python -m pytest -q \
  tests/test_coverage_istanbul_default_arg_signature.py \
  tests/test_coverage_istanbul_contradictory_branch_arcs.py \
  tests/test_evaluate_javascript_end_to_end.py \
  tests/test_coverage_istanbul_branch_arcs.py \
  tests/test_cli_run_javascript.py \
  tests/test_docs_examples_and_vocabulary.py \
  tests/test_coverage_istanbul_real_fixtures.py \
  tests/test_coverage_istanbul_provider_accuracy.py \
  tests/test_coverage_parsers_coverage_istanbul_json.py \
  tests/test_coverage_parsers_vite_plugin_istanbul_artifact.py \
  tests/test_coverage_parsers_model.py \
  tests/test_adapters_javascript_registration.py
```

Actual result: **329 passed in 5.32s, exit 0**. The initial narrower run caught
one test-helper mistake (a whole-target config carrying `base` instead of
`targets`); the helper was corrected, then the first six modules passed
**190 tests in 4.57s** before the broader run above. Pyflakes on both changed
source modules and all three changed/new test modules passed, exit 0.
`git diff --check` passed. These are bounded local checks; tester-unified is
controller-owned and has not been launched by the implementer.

### Behavioral traceability and controlled wrong implementations

| contract | committed oracle | observed control / break |
|---|---|---|
| O1/O2: classify the real signature from calls, retain arcs | `test_function_calls_classify_the_signature_and_keep_default_arcs` (3 states); CLI `test_default_argument_signature_is_judged_with_its_branch_through_cli` | three parser states and three CLI states pass; parser states all fail under M1 |
| O3: independent missing-line integrity | `test_tampered_missing_raises_independently_at_filecoverage` | passes under control, M1, and M2 |
| O3/O4: non-default B054 isolation | `test_b054_neither_bucket_branches_still_drop_and_name` (4 types × zero/nonzero); existing judged/bystander CLI cases now cover zero too | all 8 parser witnesses pass under control and fail under both M1 and M2 |
| O4/O5: bystander numbers and whole-target recovery | `test_default_argument_bystander_keeps_previously_passing_numbers_through_cli`; `test_whole_target_now_counts_the_previously_refused_signature_through_cli` | measured same-line default shapes pass; A-459's narrowing resolves the broader-rule counterexample below |
| docs sync | `test_docs_examples_and_vocabulary.py` | shipped-loader examples, public vocabulary and README→DESIGN-GUIDE anchors pass |

Controlled runs used disposable copies of `src/`, the owning tests and the
committed fixture. Each process group had a 60-second hang failsafe and a
256 KiB log bound; exceeding either meant inconclusive, never expected red.
No timeout or output limit was reached. Original parser/model SHA-256 digests
were unchanged after all runs; `FileCoverage` was never edited in the worktree.

The exact three selected test nodes were the three parser oracle names in
the first three rows above, run with `python -m pytest -q --tb=short`.

- **Control:** 12 passed, exit 0.
- **M1:** replace shape C with global tolerance: remove `_parse_record`'s
  `signature_hits` call/update block; remove invariant 3's `unconsidered`
  check from the temporary model; remove the parser's `unconsidered`
  calculation and return only `tampered`. **11 failed, 1 passed, exit 1**.
  All three default-argument classification states and all eight B054 states
  fail. The sole passing oracle is direct `tampered_missing`.
- **M2:** keep shape C but replace its `entry["type"] != "default-arg"`
  guard with `False`, so all branch types attempt classification. **8 failed,
  4 passed, exit 1**. All eight B054 states fail; the three default-argument
  states and direct `tampered_missing` remain green.

Local reproducibility artifacts: `/tmp/assay-b080-controlled-mutations.py`
and `/tmp/assay-b080-{control,M1,M2}.log`. The transformation recipes and exact
test names above are the durable evidence if those temporary files disappear.

### Controller-owned real-artifact measurement

The controller read the full 35-file consumer artifact against this parser.
All six statement-less default nodes became executed with preserved `(1,1)`
arcs and no contradictions: ChartCard 34 (`f=9,b=[9]`); DataTable 33/34/35
(`f=6,b=[6]` each); StatCard 17 (`f=18,b=[15]`); StatTile 28 (`f=26,b=[7]`).
Executable lines changed 54→55, 105→108, 1→2, 37→38, exactly +6 with no
other additions. The controller retains the full receipt; tests read only
the committed ChartCard fixture.

### Counterexample and resolution by operator ruling

A final combined-axis probe used a copy of the committed specimen with its
default node still starting on line 34, its arm moved to line 35, an executed
statement on line 35 (the return inside an immediately invoked arrow used as
the default initializer), and the enclosing function's body starting on line
36. The unique function match and counts remain valid. This models the shape:

```typescript
export function ChartCard({ title =
  (() => { return 'title'; })()
}) {}
```

The prior parser was loaded from `git show HEAD:assay/src/assay/coverage_parsers/coverage_istanbul_json.py`
in an isolated module namespace; both versions were evaluated against a diff
containing only the node line, 34. **Measured** using `evaluate_coverage`:

| parser | outcome | covered/executable | branch covered/total | contradiction |
|---|---|---|---|---|
| committed pre-fix parser | PASS | 0/0 | 0/0 | none |
| required shape-C implementation | PASS | 1/1 | 0/0 | none |

The original arm line already had a statement classification, so B054 never
refused this file. The broad shape C adds the distinct node line, changing a previously
passing number. This is synthetic parser/evaluator evidence; it is not a new
measurement of an actual producer artifact. The explicit BLOCKED trigger
prompted escalation; the operator resolved it by requiring a matching
signature-line arc (A-459). With no matching arm on 34, the narrowed parser
keeps 34 unclassified and the arm on 35 unchanged, preserving PASS 0/0.

The broader function-call rule remains an alternative: it counts an additional
executable signature line and can catch multiline defaults whose arm begins
later, at the cost of changing prior 0/0-style PASS numbers. The chosen rule
retains that gap. README, DESIGN-GUIDE, CONSUMERS, parser/adapter docstrings,
CHANGES and the B080/B089 records now state the narrowed guarantee.

### Checks after the A-459 narrowing

The focused command above was rerun against the narrowed implementation:
**339 passed in 5.49s, exit 0**. Pyflakes passed for the same five changed
source/test modules; `git diff --check` passed. Ten added cases exercise the
multiline hostile record, unread function metadata when unmatched, explicit
and fallback arm attribution, one qualifying arm among several, and the
requirement that the matching arm belong to this exact branch.

M1/M2 were rerun on the narrowed source: **control 12 passed; M1 11 failed,
1 passed; M2 8 failed, 4 passed**, with the independent direct tamper oracle
still green under both wrong implementations. The same temporary driver also
selected these two new hostile regression nodes:

- `test_coverage_istanbul_default_arg_signature.py::test_a_multiline_default_without_a_matching_arc_keeps_its_node_unclassified`
- `test_evaluate_javascript_end_to_end.py::test_multiline_default_without_matching_arc_preserves_prior_zero_over_zero`

The **pre-fix parser** from `db29266f8a006b22a30609a74de7645d1e4c50b7`
passed both; the **narrowed parser** passed both; the **broad alternative**,
formed by deleting only the new `entry_line`/`any(_arm_line(...))` guard,
failed both, including the concrete `(1, 1) != (0, 0)` count assertion.
Logs are `/tmp/assay-b080-{before,narrow,broad}.log`; bounded process groups
and restoration checks are the same as above. Source digests were unchanged.

The controller additionally verified that every one of the six consumer sites
has its sole arm on the node line; all remain eligible under A-459. Its seventh
default node (`preferences.ts:138`) already has a statement and is outside the
recovery rule. No test reads the consumer checkout.
