---
schema_version: 1
id: ciu-P06-worktree-container-targets
project: ciu
component: worktree
title: "Add declared exact container targets for worktree exec"
tier: implement-2
input_revision: "71f5ec79"
source: {kind: roadmap, ref: "nyxloom-trove/roadmap.md#package-c--machine-control-and-execution-ciu-29"}
stack: none
depends_on: [ciu-P05-exact-worktree-local-control]
session: "resume:ciu-worktree-control"
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/cli.py"
    - "src/ciu/config_model.py"
    - "tests/tests/test_ciu_worktree.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "tests/tests/test_ciu_config_model.py"
    - "tests/tests/test_ciu_documentation_contract.py"
    - "README.md"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/FEATURES.md"
    - "docs/ARCHITECTURE.md"
    - "CHANGES.md"
    - "nyxloom-trove/reports/ciu-P06-worktree-container-targets-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/governance.py"
    - "nyxloom-trove/decisions.md"
oracles:
  - id: O1
    observable: "Config accepts only `[ciu.worktree.exec_targets.<alias>]` with required non-empty `stack`, `service`, `workdir` and boolean `requires_worktree_mount` defaulting to true; unknown keys/types/aliases fail before Docker."
    negative: "Arbitrary service selection, a consumer-invented default, unknown key, empty string, or malformed boolean reaching Docker fails the contract tests."
    gate: tester-unified
  - id: O2
    observable: "`worktree exec LOGICAL --target ALIAS -- ARGV...` resolves the selected target's exact rendered stack and Compose project/service/network, requires exactly one already-running container, verifies the selected Git worktree source mount contains the declared workdir by default, then uses shell-free Docker exec and returns its exact exit code."
    negative: "Zero/multiple containers, wrong project/network/worktree mount, namespace-local `is_file` validation, implicit startup, shell interpretation, or masked exit status refuses/fails deterministically."
    gate: tester-unified
  - id: O3
    observable: "Capability `worktree.exec-target.v1` is advertised only after O1/O2 ship; README, DESIGN-GUIDE, CONSUMERS and documentation tests define every config value, rationale, security boundary, and pasteable example."
    negative: "An undocumented config value, stale schema example, arbitrary container escape hatch, or broken anchor makes the gate red."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "Exact Compose project/service/network identity cannot be derived from existing target config and selected ciu.env without guessing."
  - "Mount verification would require validating a host path through the container namespace or vice versa."
---

# P06 — Declared worktree container targets

Contract class: **2b (complex solution-bearing execution)**. This is the one
remaining package where a stronger model is justified: Docker/Compose and
host/container namespace boundaries make convenient wrong implementations unsafe.
Work only on the current serial feature branch; do not rename or merge it.

## Context to read first

1. `nyxloom/reference/AUTHORING.md` §§2b, implementation packet, 3/3b, 4-6.
2. Root `AGENTS.md`: defaults/fallbacks, cockpit doctrine, cgroup placement,
   check-strength, and exit-status sections.
3. `nyxloom-trove/decisions.md` D-007, D-009, D-010.
4. P04/P05 reports and `KNOWN_ISSUES_TODO_BACKLOG.md` CIU-29 items 5-6.
5. `src/ciu/config_model.py` global schema/validation and stack resolution.
6. `src/ciu/worktree.py` exact target environment from P05.
7. Existing Compose identity helpers/tests in `src/ciu/engine.py` (read only).

## Implementation packet (normative)

### Public config grammar

```toml
[ciu.worktree.exec_targets.tester]
stack = "test"
service = "tester"
workdir = "<absolute-container-workdir>"
requires_worktree_mount = true
```

Alias is a Git-safe single component. Required fields are non-empty strings.
Only the four shown keys exist. Omitted `requires_worktree_mount` means true as
an explicit security policy; false is the only opt-out.

### Required flow and namespaces

1. Resolve ready record and exact target env using P05; render the selected
   target's global chain without writing, using that env for both Jinja and
   variable expansion.
2. Validate alias/config entirely before Docker.
3. Derive the exact Compose file/project/service/network using existing CIU
   naming rules; never select by service/container-name substring.
4. Query running containers and require exactly one. Never call `up`.
5. Inspect Docker's host-source/container-destination mount records. Compare
   host source to the selected Git worktree host path and destination to the
   declared container workdir. Do not call local filesystem predicates on a
   path belonging to the other namespace.
6. Run `docker exec -w WORKDIR CONTAINER -- ARGV...` without a shell and return
   its exact status.

### Combined-axis attacks

- Same service name exists in sibling worktrees; only selected project/network
  may match, and the wrong mount must refuse.
- One selected Compose service scales to two running containers: refuse both.
- Correct container/project but primary checkout is mounted while a linked
  checkout is selected: refuse by default.
- `requires_worktree_mount=false` permits a deliberate non-source utility
  container but does not weaken project/service/network uniqueness.
- Host source and container workdir contain spaces/metacharacters; argv is exact.

### Traceability

| work | oracle | controlled break |
|---|---|---|
| config grammar | O1 | typo key and empty service |
| exact selection/mount/status | O2 | sibling container + wrong mount + exit 23 |
| docs/capability | O3 | omit one closed value and break one anchor |

Degrees of freedom: private query/helper decomposition only.

## Work

1. Implement and validate the exact config grammar.
2. Implement exact already-running target selection, mount proof, and exec.
3. Add deterministic fake-Docker unit tests plus one real command-construction
   integration test; no live Docker dependency in unit tests.
4. Synchronize all three user documents and reference docs in this same package.
5. Advertise `worktree.exec-target.v1` only after behavior exists.
6. Write the report with controlled-break results and commit SHA.

## Test constraints (mandatory)

- No wall-clock deadlines/sleeps, global leaks, order dependence, hollow tests,
  weakened assertions, coverage exclusions, live network/clock, or shell strings.
- Do not mock the parser/selector being tested; fake only the Docker boundary.
- Tests must distinguish zero, one-correct, one-wrong, and multiple matches.
- Witness regression negatives fail before implementation.

## Gate and BLOCKED rule

Run `tester-unified`, preserving its true exit status.

## Out of scope / forbid

Do not add arbitrary service selection, implicit startup, or change engine,
deploy, governance, Assay, or nyxloom.

**BLOCKED:** emit on an exact
`escalate_if` trigger; never broaden selection, skip mount proof, invent a
project name, or start a container as a fallback.
