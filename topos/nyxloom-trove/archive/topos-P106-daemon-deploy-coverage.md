---
schema_version: 1
id: topos-P106-daemon-deploy-coverage
project: topos
title: "Complete daemon deployment coverage"
tier: sonnet5-high
input_revision: "c9490162"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P105-daemon-lifecycle-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/daemon/deploy.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P106-daemon-deploy-coverage.md"
    - "nyxloom-trove/reports/P106-*.md"
  forbid:
    - "src/topos/daemon/status.py"
    - "src/topos/cli.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/daemon/deploy.py has empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 15-line/5-pair residual"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial coverage without the whole-file record empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert complete deterministic preflight reports/checks, identity-label fallbacks, canonical JSON, and exact install-plan text for a warning-only step"
    negative: "tests mock the function under test, contain pass or assertion-free bodies, assert only substrings/selected fields/non-None/ranges/calls, swallow errors, or rely on host users/groups/services"
    gate: topos-suite
  - id: O3
    observable: "every swallowed-error test proves the exact inducing precondition and complete resulting check/report; receipts bind literal pairs to source and destination lines and print per-run intersections plus complete target-record hashes"
    negative: "test names or covered except lines substitute for causal proof, branch truth is inferred backwards, counts replace literal sets, or exact-command/parity claims lack captured evidence"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical complete executed/missing records for deploy.py"
    negative: "serial-only evidence, xdist drift, reporter failure after a passing pytest summary, or any deploy.py gap is accepted"
    gate: topos-suite
  - id: O5
    observable: "every retained test has exact behavioral evidence; all temp paths are worker-unique and no pragma, omit, evaluator, gate, dependency, sleep, global leak, expensive physical boundary setup, or nondeterministic host-state reliance is introduced"
    negative: "coverage is raised with hollow, duplicate, partial-field, membership-only, or unrelated tests, or with receipt prose contradicted by code"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a residual branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside daemon/deploy.py"
  - "either clean full-gate run fails or the target-file executed/missing records differ"
advances: []
---

# P106 — Complete daemon deployment coverage

## Goal

Bring all of `daemon/deploy.py` to exact 100% statement and branch coverage
with deterministic behavioral tests for preflight failures, identity fallback,
canonical rendering, and warning-only install steps.

Assigned worktree:
`/workspaces/vbpub/.worktrees/feat/topos-P106-daemon-deploy-coverage`.

## Literal residual

The full-gate JSON must close these 15 remaining lines:

```text
67 68 82 102 103 141 180 210 211 212 328 329 335 336 344
```

and these 5 remaining branch pairs:

```text
81->82 101->102 134->141 179->180 558->560
```

Completion additionally requires `missing_lines=[]` and
`missing_branches=[]` for the complete file record.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and seven
   validation principles.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, especially PL3 and
   living PL4 through P105.
5. `topos/nyxloom-trove/reports/P105-REPORT.md` and `P105-REVIEW.md`, only the
   verified 2,046-case baseline and session-health/arc-direction discipline.
6. `topos/tests/test_daemon_deploy.py` and `daemon/deploy.py` at the assigned
   revision.

## Work

1. Relocation preflight: print `pwd`, repository root, status, and HEAD.
   Treat every P96–P105 worktree and Reasonix session as stale and forbidden.
2. Run the full branch-aware gate and mechanically confirm the literal
   15-line/5-pair residual plus the complete deploy.py missing sets.
3. Bind every line/pair to exact-revision `nl -ba`, its actual destination,
   a concrete input, and a complete returned report/check or rendered string.
4. Exercise deterministic runtime-stat failure, runtime path that is a file,
   world-writable runtime directory, existing group without membership,
   existing non-socket path, `_can_connect` `OSError`, missing uid/gid label
   fallbacks, canonical preflight JSON, and install-plan text containing a
   warning-only step with no command. Use `tmp_path`, a real worker-unique Unix
   socket only where its file type is the behavior, and narrow patches for
   identity/connection seams.
5. Never consult or mutate real group membership, `/run`, systemd, or packaged
   destinations. Patch `grp`/`pwd` and `_current_identity` to explicit values;
   compare complete `PreflightCheck`, `DaemonPreflightReport`, JSON object/string,
   or full rendered text.
6. Zero `pass` bodies, assertion-free calls, weak ranges, substring-only,
   selected-field, non-None-only, function-under-test mocks, duplicate cases,
   or invented mutation/fail-before claims. Interrupt rather than weaken an
   assertion after a failure.
7. Use the bind-mounted `tester-unified:local`; never rebuild it.
8. Run the full xdist gate twice. On both JSON files require the literal P106
   intersections and complete missing sets empty; print and compare a
   normalized hash of the complete deploy.py record inside the container.
9. Separately report added test-function count and collected-case delta against
   the verified P105 total of 2,046. Run `git diff --check`.
10. Write `P106-LOG.md`, `P106-REPORT.md`, and `P106-SELFREVIEW.md` with literal
    before/after sets, the whole-file record, exact commands/exits, truthful
    negative evidence, collection arithmetic, causal assertion audit, and
    parity.
11. Commit, adversarially self-review, repair every finding, rerun verification,
    and commit the final state.

## Gate

Use the exact `[gates.topos-suite]` argv. Focused or serial runs are diagnostic.
Completion requires two complete parallel runs, literal residual checks, and
the whole-file target check all exiting zero.

## Scope / forbid

Only frontmatter touch paths may change. Do not alter status, CLI, gate,
tooling, or dependencies. No coverage pragmas or omissions. Target source
edits require a regression-proven defect or independently demonstrable dead
path.

## BLOCKED

On a mechanical `escalate_if`, stop and write
`BLOCKED: <trigger and exact evidence>` to `P106-LOG.md`. Never return partial
`done`.
