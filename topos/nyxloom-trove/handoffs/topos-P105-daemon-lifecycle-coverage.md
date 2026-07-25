---
schema_version: 1
id: topos-P105-daemon-lifecycle-coverage
project: topos
title: "Complete daemon status and deployment coverage"
tier: sonnet5-high
input_revision: "0ac15127"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P104-snapshot-coverage]
session: "resume:topos-coverage"
scope:
  touch:
    - "src/topos/daemon/status.py"
    - "src/topos/daemon/deploy.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P105-daemon-lifecycle-coverage.md"
    - "nyxloom-trove/reports/P105-*.md"
  forbid:
    - "src/topos/cli.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/daemon/status.py and src/topos/daemon/deploy.py each have empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 21-line/10-pair residual"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial coverage without both whole-file records empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert exact status serialization/text, preflight failure reports and remedies, identity fallbacks, connection failures, JSON rendering, and install-plan steps that omit commands while retaining warnings"
    negative: "tests mock the function under test, contain pass or assertion-free bodies, assert only substrings/non-None/ranges/calls, swallow errors, or rely on mutable host identity or services"
    gate: topos-suite
  - id: O3
    observable: "every swallowed-error test proves the exact inducing precondition and exact postcondition; receipts bind literal lines/pairs to exact-revision nl -ba and print per-run intersections plus complete target-record hashes"
    negative: "a test name or covered except line substitutes for causal proof, counts replace literal sets, or exact-command/parity claims lack captured evidence"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical complete executed/missing records for both target files"
    negative: "serial-only evidence, xdist drift, reporter failure after a passing pytest summary, or any target gap is accepted"
    gate: topos-suite
  - id: O5
    observable: "every retained test has exact behavioral evidence; all temp paths are worker-unique and no pragma, omit, evaluator, gate, dependency, sleep, global leak, or nondeterministic host-state reliance is introduced"
    negative: "coverage is raised with hollow, duplicate, partial-field, membership-only, or unrelated tests, or with receipt prose contradicted by code"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a residual branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside the two daemon modules"
  - "either clean full-gate run fails or target-file executed/missing records differ"
advances: []
---

# P105 — Complete daemon status and deployment coverage

## Goal

Bring all of `daemon/status.py` and `daemon/deploy.py` to exact 100% statement
and branch coverage with deterministic behavioral tests for deployment
preflight failures, status degradation, identity fallbacks, and install-plan
rendering.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P105-daemon-lifecycle-coverage`.

## Literal residual

The full-gate JSON must close these 21 remaining lines:

```text
daemon/status.py: 90 91 131 132 166 168
daemon/deploy.py: 67 68 82 102 103 141 180 210 211 212 328 329 335 336 344
```

and these 10 remaining branch pairs:

```text
daemon/status.py: 44->46 46->48 48->50 74->76 85->90
daemon/deploy.py: 81->82 101->102 134->141 179->180 558->560
```

Completion additionally requires `missing_lines=[]` and
`missing_branches=[]` for both complete file records.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4 through P104.
5. `topos/nyxloom-trove/reports/P104-REPORT.md` and `P104-REVIEW.md`, only the
   verified 2,040-case baseline and evidence discipline.
6. `topos/tests/test_daemon_status.py`, `test_daemon_deploy.py`, and the two
   target source files at the assigned revision.

## Work

1. Relocation preflight: print `pwd`, repository root, status, and HEAD.
   Treat every earlier P96–P104 worktree as stale and forbidden.
2. Run the full branch-aware gate and mechanically confirm the literal
   21-line/10-pair residual plus both complete target-file missing sets.
3. Bind every line/pair to exact-revision `nl -ba`, a concrete input, and an
   exact returned dataclass, dictionary, text, or exception. Do not infer
   branch identities from stale labels.
4. Exercise a status report without preflight, optional protocol fields
   independently present/absent, the base `DaemonClientError`, swallowed
   preflight failure with exact degraded report, unavailable/runtime-file/
   world-writable runtime paths, non-member group, non-socket path, connection
   `OSError`, missing uid/gid labels, deterministic preflight JSON, and an
   install-plan text step with no command and a warning.
5. For every swallowed exception, prove the inducing seam and exact
   postcondition. Use worker-unique `tmp_path` and narrow dependency patches;
   do not touch real passwd/group databases, services, sockets, or `/run`.
6. Zero `pass` bodies, assertion-free calls, weak ranges, substring-only or
   non-None-only assertions, function-under-test mocks, duplicate cases, or
   invented mutation/fail-before claims.
7. Use the bind-mounted `tester-unified:local`; never rebuild it.
8. Run the full xdist gate twice. On both JSON files require the literal P105
   intersections empty and complete `missing_lines`/`missing_branches` empty
   for both files; print and compare a normalized hash of complete target
   records inside the container to avoid cross-UID receipt writes.
9. Separately report added test-function count and collected-case delta against
   the verified P104 total of 2,040. Run `git diff --check`.
10. Write `P105-LOG.md`, `P105-REPORT.md`, and `P105-SELFREVIEW.md` with literal
    before/after sets, whole-file records, exact commands/exits, truthful
    negative evidence, collection arithmetic, causal assertion audit, and
    parity.
11. Commit, adversarially self-review, repair every finding, rerun verification,
    and commit the final state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused or serial runs are diagnostic.
Completion requires two complete parallel runs, literal residual checks, and
both whole-file target checks all exiting zero. A passing pytest summary
followed by coverage-report or evaluator failure is red.

## Scope / forbid

Only frontmatter touch paths may change. Do not alter CLI, gate, tooling, or
dependencies. No coverage pragmas or omissions. Target source edits require a
regression-proven defect or independently demonstrable dead path.

## BLOCKED

On a mechanical `escalate_if`, stop and write
`BLOCKED: <trigger and exact evidence>` to `P105-LOG.md`. Never return partial
`done`.
