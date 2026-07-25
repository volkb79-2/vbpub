---
schema_version: 1
id: topos-P96-max-test-gate
project: topos
title: "Bootstrap the parallel branch-coverage gate and measure the honest baseline"
tier: sonnet5-high
input_revision: "aa526cbf"
source: {kind: backlog, ref: "nyxloom-trove/4-backlog.md#B-046"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "topos/pyproject.toml"
    - "topos/tools/**"
    - "topos/tests/**"
    - "topos/nyxloom-trove/nyxloom.toml"
    - "topos/nyxloom-trove/4-backlog.md"
    - "topos/nyxloom-trove/handoffs/topos-P96-max-test-gate.md"
    - "topos/nyxloom-trove/reports/P96-*.md"
  forbid:
    - "topos/src/**"
oracles:
  - id: O1
    observable: "topos[dev] directly declares pytest-cov and pytest-xdist, and tester-unified can import both without relying on nyxloom's sibling dependencies"
    negative: "removing nyxloom's test extra from the image would also remove either topos gate tool"
    gate: topos-suite
  - id: O2
    observable: "a project-owned, unit-tested evaluator diffs merge-base(main,HEAD) to HEAD and rejects every uncovered changed executable line under topos/src/topos, with pragma: no cover as its reviewed escape hatch"
    negative: "an uncovered changed source line, missing/invalid coverage JSON, git failure, or source-path mismatch exits zero"
    gate: topos-suite
  - id: O3
    observable: "the implementation gate runs the complete topos/tests suite under pytest-xdist and pytest-cov branch measurement, then invokes the changed-line evaluator through fail-closed shell composition"
    negative: "pytest, coverage production, or evaluator failure is hidden by a pipe, loop, trailing command, or missing pipefail"
    gate: topos-suite
  - id: O4
    observable: "two stable serial runs and two stable parallel runs have identical per-file executed and missing line sets; any serial-covered/parallel-missed line is repaired with a deterministic in-process test"
    negative: "parallel mode is accepted from aggregate percentage alone or child-process coverage is recaptured to conceal a hollow test"
    gate: topos-suite
  - id: O5
    observable: "the report records exact statement and branch coverage by source file and a deterministic ordered list of uncovered lines/branches for the next healing package"
    negative: "the report rounds to 100%, omits branches, excludes source files merely because they are hard to test, or changes omit rules to improve the number"
    gate: topos-suite
  - id: O6
    observable: "topos-suite is the only implementation-phase verifier candidate; py-compile is fail-closed advisory review only"
    negative: "nyxloom can select py-compile instead of the pytest gate for gate verify"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "serial pytest is intrinsically flaky across two clean reruns, so an honest xdist parity conclusion cannot be made"
  - "parallel safety requires a product-code change under topos/src (forbidden in this foundation package; record the exact child contract instead)"
  - "tester-unified cannot be rebuilt or cannot install the declared topos[dev] closure"
advances: []
---

# P96 — Bootstrap the max-standard topos test gate

## Goal

Create the trustworthy development oracle used by the later 100%-coverage
healing packages. This package does **not** claim that today's global coverage
is 100%. It must enforce 100% coverage of changed executable lines immediately,
measure honest global statement and branch coverage, and leave a concrete gap
ledger. A later package may activate the absolute global floor only after the
ledger reaches zero.

The product goal is absolute 100% statement **and branch** coverage of every
Python module under `topos/src/topos`. Do not game the baseline with new omit
rules, exclusions, module removal, `# pragma: no cover`, hollow assertions, or
tests that mock the component under test. A pragma is acceptable only for
genuinely unreachable defensive code and must be called out line-by-line for
independent review.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/AUTHORING.md`, especially the behavior
   oracle, fail-before/pass-after, gate, and BLOCKED contracts.
3. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, sections “What nyxloom
   requires of a project (the gate contract)” and “Validation methodology”.
4. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL2 and PL3.
5. `/workspaces/vbpub/nyxloom/docs/gate-adoption-assessment.md`, sections
   “topos” and “Absolute-100 rollout note” if present.
6. `topos/nyxloom-trove/nyxloom.toml`, `topos/pyproject.toml`,
   `topos/tests/conftest.py`, and `topos/tests/test_gate_environment.py`.
7. Reference implementation only:
   `/workspaces/vbpub/nyxloom/src/nyxloom/coverage_gate.py`,
   `/workspaces/vbpub/nyxloom/tests/test_coverage_gate.py`, and the
   `[gates.tester-unified]` block in
   `/workspaces/vbpub/nyxloom/nyxloom-trove/nyxloom.toml`.

## Required work

1. Add direct `pytest-cov` and `pytest-xdist` requirements to `topos[dev]`.
   Extend the existing gate-environment regression test so those tools are
   project-owned and importable.
2. Adapt nyxloom's changed-line evaluator into
   `topos/tools/coverage_gate.py`. Keep merge-base/first-parent semantics,
   fail-closed error handling, executable-line analysis, and the explicit
   pragma escape hatch. Add focused tests under `topos/tests/` for positive,
   negative, malformed-input, git-failure, path, rename/deletion, and pragma
   behavior. Do not import the evaluator from the sibling nyxloom project.
3. Change `py-compile` to `phase = "review"` and make it fail closed and
   filename-safe. Correct the stale comment claiming `{worktree}` is not
   substituted; preserve the known environment-specific host bind.
4. Change `topos-suite` to run from the vbpub worktree root in
   `tester-unified`, using the interpreter in `/opt/tester-venv`, with this
   semantic command:

   ```bash
   python -m pytest topos/tests -q -n auto \
     --cov=topos/src/topos --cov-branch \
     --cov-report=json:/tmp/topos-coverage.json &&
   python topos/tools/coverage_gate.py \
     --repo . --base main \
     --coverage-json /tmp/topos-coverage.json \
     --source topos/src/topos
   ```

   Use `bash -c 'set -euo pipefail; ...'` at every shell-composition layer.
   Keep xdist in the gate argv, never global pytest addopts. The final argv
   must use pytest-cov, not `coverage run`, because xdist workers must combine
   coverage correctly.
5. Rebuild `tester-unified:local` from its declared Dockerfile so the result
   proves the edited `topos[dev]` closure. Do not validate from the cockpit.
6. Establish parity:
   - two serial coverage runs;
   - two `-n auto` coverage runs;
   - compare exact per-file executed and missing line sets, not percentages;
   - separately compare measured branch totals and missing branches;
   - classify any serial-to-serial discrepancy as intrinsic flakiness before
     blaming xdist.
7. Produce `topos/nyxloom-trove/reports/P96-LOG.md`,
   `P96-REPORT.md`, and a machine-readable or Markdown gap ledger at
   `P96-COVERAGE-GAPS.md`. Include exact commands, exits, totals, per-file
   gaps, parity evidence, and every skip/xfail. Update B-046 to say
   “Carved -> P96; global-100 healing follows measured child packages.”
8. Commit the complete package on this branch. Do not merge or push.

## Verification

The authoritative package gate is the revised `topos-suite` command in the
dedicated `tester-unified` container. Also run focused evaluator tests,
`git diff --check`, and the serial/parallel parity protocol above. The
controller will independently inspect the diff and rerun the gate.

Do not add `--cov-fail-under=100` or `canary-verified` in P96 unless the honest
statement and branch result is already exactly 100. Changed-line coverage must
still be enforced. Do not run `gate verify` recursively from the ordinary gate.

## Out of scope

- Product changes under `topos/src/**`.
- Filling the measured historical coverage gaps.
- Mutation testing.
- Merging, pushing, registering projects, or editing any sibling project.

## Worktree and session

- Worktree:
  `/workspaces/vbpub/.worktrees/feat/topos-P96-max-test-gate`
- Branch: `feat/topos-P96-max-test-gate`
- This is the first turn of the persistent topos Flash-Max implementation
  session. The controller will resume this exact session for a separate
  self-review and for subsequent measured coverage-healing packages.

If a named escalation trigger fires, STOP, write
`BLOCKED: <trigger and evidence>` to `P96-LOG.md`, commit the evidence, and
exit. Do not improvise around the boundary.
