---
schema_version: 1
id: topos-P113-execute-primitives-coverage
project: topos
title: "Complete action execution validation and audit primitives"
tier: sonnet5-high
input_revision: "1f73f238"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P112-squeeze-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/actions/execute.py"
    - "tests/test_p113_execute_primitives_coverage.py"
    - "nyxloom-trove/handoffs/topos-P113-execute-primitives-coverage.md"
    - "nyxloom-trove/reports/P113-*.md"
  forbid:
    - "src/topos/actions/catalog.py"
    - "src/topos/actions/preview.py"
    - "src/topos/actions/update_ops.py"
    - "src/topos/actions/squeeze.py"
    - "src/topos/cli.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "the complete xdist branch-coverage JSON has empty intersections with P113's literal 32-line/24-pair execute.py primitive residual in two clean immutable runs"
    negative: "a count, serial/focused run, aggregate percentage, or a whole-file completion claim substitutes for the named line/pair intersections"
    gate: topos-suite
  - id: O2
    observable: "bounded output/UTF-8 decoding, identity and timeout validation, immutable catalog-plan validation, audit-record bounds, and oversized JSONL refusal have exact behavioral tests"
    negative: "a test reaches a helper without asserting its exact result/error or accepts bool/control/forged-plan input that the production boundary must refuse"
    gate: topos-suite
  - id: O3
    observable: "safe-audit tests prove the relevant no-follow, directory/file mode, ownership, creation, cleanup, and KeyboardInterrupt-propagation contracts through narrow OS seams"
    negative: "tests invoke host audit paths, mock the helper under test, swallow BaseException, or assert only calls rather than the resulting security refusal/cleanup"
    gate: topos-suite
  - id: O4
    observable: "any source repair is a demonstrated product/safety defect with a fail-before/pass-after behavioral oracle; otherwise this package is test-only"
    negative: "a source edit merely manufactures coverage, changes another action module, or uses a pragma/omission to lower the floor"
    gate: topos-suite
  - id: O5
    observable: "two exact clean-commit declared gates pass, changed lines are covered, P113 intersections are empty in both target records, and the report reconciles from the verified 2,156-case baseline"
    negative: "dirty-tree, reporter-warning, no-data, cached-image, host-venv, rebuilt-image, guessed-runner, or self-reported evidence is accepted"
    gate: topos-suite
gates: [topos-suite]
review_focus:
  - "verify every literal line/pair against nl -ba from the immutable reviewed commit"
  - "adversarially inspect safe-audit fake seams for a mocked target or unproved cleanup"
  - "reject partial/count-only assertions and unproved product edits"
escalate_if:
  - "a required behavioral fix needs a forbidden source or gate/dependency file"
  - "a literal primitive is genuinely unreachable after a concrete attempted input/seam matrix"
  - "the declared runner cannot produce a target record, a full gate fails, or the two records differ"
advances: []
---

# P113 — Complete action execution validation and audit primitives

Assigned branch/worktree:
`feat/topos-P113-execute-primitives-coverage` at
`/workspaces/vbpub/.worktrees/feat/topos-P113-execute-primitives-coverage`.

This is the first of three `actions/execute.py` packages. Do not claim the
whole file exact; P114 owns the central runner/audit machinery and P115 owns
the public wrappers and remaining residual.

## Literal residual

```text
lines:
83 95 123 124 132 138 140 146 161 163 165 168 178 180 186 188
198 201 202 203 211 213 224 225 238 240 242 245 246 247 264 291

pairs:
82->83 131->132 133->138 139->140 145->146 156->161 162->163
164->165 167->168 177->178 179->180 185->186 187->188 197->198
210->211 212->213 222->224 224->225 224->226 237->238 239->240
241->242 263->264 290->291
```

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
3. `/workspaces/vbpub/nyxloom/reference/LESSONS.md`, especially L2, L5, L7,
   L8, and L12.
4. `topos/nyxloom-trove/nyxloom.toml`, `[gates.topos-suite]` only.
5. `topos/src/topos/actions/execute.py`, lines 1–307 only.
6. `topos/tests/test_actions.py`, `TestOutputBounding`; and
   `topos/tests/test_p78_action_kernel.py` only as the public-chain precedent.

## Work

1. Start with relocation preflight: print `pwd`, top-level, exact `HEAD`, and
   clean status. Read only the context above before changing files.
2. Add `tests/test_p113_execute_primitives_coverage.py`. Use exact behavioral
   assertions for the literal primitive residuals. Prefer real temporary paths
   for ordinary file behavior and narrowly controlled `os` seams only where a
   secure-open failure is otherwise unsafe or nondeterministic.
3. Cover the invalid-result branches of `_bound_output`, `_production_identity`,
   `_coerce_identity`, `_validate_timeout`, `_validate_plan`, `_audit_record`,
   and `_write_json_record`; assert exact returned values/errors and durable
   record structure. Test `_decode_output` with invalid UTF-8 replacement.
4. Exercise `_open_safe_audit`'s listed path, missing-component, mode/owner,
   existing-leaf, leaf-stat, and cleanup branches without following a symlink or
   mutating production paths. The two `BaseException` cleanup blocks are
   deliberate only if the resource closes and `KeyboardInterrupt` re-raises;
   prove that contract rather than narrowing them by reflex.
5. If a test reveals a genuine product/safety defect in `execute.py`, repair it
   directly and prove fail-before/pass-after. Otherwise do not alter product
   source. Never add a coverage pragma or change the gate/tooling/dependencies.
6. Self-review before the full gate: scan the new test AST/source for empty,
   assertion-free, count-only, selected-field, duplicate, or fixed-shared-path
   tests; correct every finding. Run the focused file in the declared container
   only.
7. Commit the implementation before authoritative evidence. From its exact
   clean hash, run the declared `topos-suite` twice using the existing bound
   `tester-unified` image (no rebuild); print/hash the `execute.py` record in
   the container and mechanically show both literal intersections empty.
   Record full commands, exits, test-function/collection arithmetic, mappings,
   receipts, self-review, and any source defect in `P113-LOG/REPORT/SELFREVIEW`.

## Scope / forbid

Only `execute.py`, the one new P113 test file, and P113 handoff/report evidence
may change. The catalog, preview, update, squeeze, CLI, gate/tooling, and
dependency files are forbidden. A needed change outside that list is BLOCKED.

## Runner

Use only the declared gate environment. The focused/full command shape is:

```text
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub \
  tester-unified:local bash -c 'set -euo pipefail && cd <assigned-worktree> &&
  export PYTHONPATH=topos/src:topos && /opt/tester-venv/bin/python -m pytest ...'
```

Do not use a cockpit Python, create a host venv, copy the worktree, guess a
mount, or rebuild `tester-unified` unless a dependency/image input changes.

## BLOCKED

On any `escalate_if` trigger, write `BLOCKED: <trigger and exact evidence>` to
`P113-LOG.md`, commit only the evidence allowed by scope, and stop. Do not
broaden the package, weaken an assertion, or report partial completion.
