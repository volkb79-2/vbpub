---
schema_version: 1
id: ciu-P05-exact-worktree-local-control
project: ciu
component: worktree
title: "Add exact selected-worktree up and local exec"
tier: implement-2
input_revision: "71f5ec79"
source: {kind: roadmap, ref: "nyxloom-trove/roadmap.md#package-c--machine-control-and-execution-ciu-29"}
stack: none
depends_on: [ciu-P04-structured-worktree-control]
session: "resume:ciu-worktree-control"
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/cli.py"
    - "src/ciu/workspace_env.py"
    - "tests/tests/test_ciu_worktree.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "tests/tests/test_ciu_documentation_contract.py"
    - "README.md"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/FEATURES.md"
    - "docs/ARCHITECTURE.md"
    - "CHANGES.md"
    - "nyxloom-trove/reports/ciu-P05-exact-worktree-local-control-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "nyxloom-trove/decisions.md"
oracles:
  - id: O1
    observable: "`ciu worktree up LOGICAL` resolves one ready managed record, parses that target CIU root's exact `ciu.env`, removes conflicting inherited CIU identity/root values, and invokes CIU's existing up path in that root; its exact child exit code is returned."
    negative: "Missing/invalid/mismatched target env, recovery state, sibling-root contamination, or a wrapper-masked child failure refuses or propagates nonzero without starting a different instance."
    gate: tester-unified
  - id: O2
    observable: "`ciu worktree exec LOGICAL -- ARGV...` executes the exact argv without a shell in the selected CIU root and sanitized target environment, never starts/cleans/renders implicitly, and propagates the exact child exit code."
    negative: "Shell interpretation, argv rewriting, implicit up, ambient sibling identity, missing `--`, or exit-code masking is detected by hostile argv/environment fixtures."
    gate: tester-unified
  - id: O3
    observable: "The capability allowlist adds exactly `worktree.up.v1` and `worktree.exec-local.v1`; README, DESIGN-GUIDE, CONSUMERS and documentation-contract tests expose pasteable adoption examples and all closed values."
    negative: "Advertising target exec early, stale examples, absent rationale, or an unresolved anchor makes the gate fail."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "The existing CIU up entry point cannot be called with an explicit cwd/environment without changing its public behavior."
  - "A required target identity value has no authoritative source in the record or exact target ciu.env."
---

# P05 — Exact selected-worktree local control

Contract class: **2c (bounded integration)**. Prefer a cost-effective strong
implementation model; public behavior and failure handling are fixed here.
Work only on the current serial feature branch; do not rename or merge it.

## Context to read first

1. `nyxloom/reference/AUTHORING.md` §§2c, implementation packet, 3/3b, 4-6.
2. `nyxloom-trove/decisions.md` D-003, D-006, D-007, D-009.
3. P04 report and diff; `KNOWN_ISSUES_TODO_BACKLOG.md` CIU-29 items 3-4.
4. `src/ciu/worktree.py` record lookup and P04 document helpers.
5. `src/ciu/cli.py` `_worktree`, `main`, and existing `up` dispatch.
6. `src/ciu/workspace_env.py` parser and required identity vocabulary.

## Implementation packet (normative)

### Interfaces and authoritative environment

- Add `up_instance(repo_root, logical_name) -> int` and
  `exec_instance(repo_root, logical_name, argv) -> int` (equivalent names okay;
  signatures/behavior fixed).
- Accept only a `ready` record. Read `<record.ciu_root>/ciu.env` with the shipped
  parser. Require its `REPO_ROOT`, `PHYSICAL_REPO_ROOT`, `INSTANCE_ID`,
  `DOCKER_NETWORK_INTERNAL`, and `REPO_NAME` to identify the selected record/root.
- Construct child env by copying ambient variables except every CIU root,
  identity, network, profile, and shared-infrastructure key; then overlay all
  exact parsed target values. Do not source a shell and do not invent absence.
- `up` invokes the existing CLI/module entry point in `record.ciu_root`; local
  exec invokes exact argv in that root. Both return the child's return code.

### Hostile fixtures

1. Ambient env points at sibling A while selected record/env points at B.
2. Args include spaces, glob characters, `$()`, semicolon, and a leading dash;
   the child receives identical argv and no shell effect occurs.
3. Child returns 17; command returns 17 even when stdout is captured.
4. Record is recovery-required or env instance/network/root differs by one field;
   no child starts.
5. Local exec records zero calls to up/render/clean.

### Documentation

Update all three user documents in this package. CONSUMERS includes pasteable
human and automation examples and explicitly states that `exec` never runs `up`.
Extend the existing parser/vocabulary/anchor documentation tests.

Degrees of freedom: subprocess helper/decomposition only; no shell, fallback,
implicit startup, or public error invention.

## Work

1. Implement sanitized exact-target environment construction once and reuse it.
2. Implement explicit up and local exec dispatch with exact status propagation.
3. Add all hostile fixtures and controlled-break evidence.
4. Synchronize README, DESIGN-GUIDE, CONSUMERS, reference docs, and CHANGES.
5. Add `worktree.up.v1` and `worktree.exec-local.v1` only after the code exists.
6. Write the report with actual commands and commit SHA.

## Test constraints (mandatory)

- No wall-clock deadlines/sleeps, global-state leaks, order/worker dependence,
  hollow tests, weakened assertions, coverage exclusions, live network, or live clock.
- Use `monkeypatch` restoration and fresh paths; never patch a lazy proxy instance.
- Include one real subprocess/CLI integration fixture and hostile combined axes.
- Witness each controlled break fail before the implementation passes it.

## Gate and BLOCKED rule

Run `tester-unified`; capture the job exit status directly.

## Out of scope / forbid

Do not add container aliases, alter allocation, or change Assay/nyxloom policy.

**BLOCKED:** emit only for
an `escalate_if` trigger with the exact missing authority/interface. Do not add a
fallback env value or silently call `up` to make an exec test pass.
