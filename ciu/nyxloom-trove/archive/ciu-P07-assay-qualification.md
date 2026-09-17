---
schema_version: 1
id: ciu-P07-assay-qualification
project: ciu
component: qualification
title: "Qualify the CIU worktree milestone through Assay and adversarial review"
tier: implement-2
input_revision: "71f5ec79"
source: {kind: roadmap, ref: "nyxloom-trove/roadmap.md#package-d--gate-documentation-and-qualification"}
stack: none
depends_on: [ciu-P06-worktree-container-targets]
session: fresh
scope:
  touch:
    - "assay.toml"
    - "nyxloom-trove/nyxloom.toml"
    - "tests/tests/test_ciu_documentation_contract.py"
    - "README.md"
    - "docs/DESIGN-GUIDE.md"
    - "docs/CONSUMERS.md"
    - "docs/SPEC.md"
    - "docs/CONFIG.md"
    - "docs/FEATURES.md"
    - "docs/ARCHITECTURE.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/roadmap.md"
    - "nyxloom-trove/reports/ciu-P07-assay-qualification-LOG.md"
  forbid:
    - "src/ciu"
    - "nyxloom-trove/decisions.md"
oracles:
  - id: O1
    observable: "CIU's implementation gate runs in tester-unified and consumes the released installed Assay CLI/artifact contract, with a current valid assay.toml; it does not import Assay source or any nyxloom coverage/mutation/canary judge."
    negative: "Source-tree coupling, stale schema, cockpit-only pytest, or remaining nyxloom evidence-judgment command fails qualification."
    gate: tester-unified
  - id: O2
    observable: "The gate reads `$CGROUP_PARENT_DEV_BACKGROUND`, verifies the named systemd slice is loaded before Docker, passes it as Docker cgroup parent, fails closed when absent/unloaded, and captures the test/Assay job status rather than a wrapper or pipe status."
    negative: "A hardcoded/fallback slice, nonexistent slice accepted, unconfined Docker launch, or masked failing job is rejected by a controlled canary."
    gate: tester-unified
  - id: O3
    observable: "The full 2,076+ suite, 100% line/branch requirements, Assay verdict, documentation contracts, and a new independent combined-axis adversarial review all pass; accepted findings are repaired before CIU-28/CIU-29 and the roadmap milestone are closed."
    negative: "Zero-test/mutant evidence, stale producer-authored receipt, unresolved review finding, stale docs, or issue closure without exact proof keeps the milestone open."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "The released Assay installed in tester-unified does not support the required current schema/command contract."
  - "The background cgroup variable is absent or names a systemd unit whose LoadState is not loaded."
  - "Adversarial review finds an accepted defect requiring CIU source changes forbidden by this qualification package."
---

# P07 — Assay-backed qualification

Contract class: **2c for gate integration, plus independent adversarial review**.
Use a stronger reviewer than the implementation agents; this package is not a
mechanical green-check exercise.
Qualify the current serial feature branch; do not merge it or modify main.

## Context to read first

1. Root `AGENTS.md`: cockpit gate, cgroup, docs, check-strength, exit-status.
2. `nyxloom-trove/decisions.md` D-009 and D-010.
3. P04-P06 reports and their actual diffs/commits; trust Git, not receipts.
4. Current released Assay CLI help/schema from the installed tester-unified
   artifact, plus Assay's consumer docs. Do not read/import another worktree's source.
5. `nyxloom-trove/nyxloom.toml` gate and CIU's current test runner.

## Implementation packet (normative)

1. Probe the installed Assay version and supported config schema inside
   tester-unified. Record exact commands/output before editing. If it is not the
   expected released contract, trigger BLOCKED rather than guessing.
2. Add/update CIU's `assay.toml` using only supported public keys. The test run
   produces evidence; Assay judges it; nyxloom only routes the gate verdict.
3. Replace the old nyxloom coverage-gate invocation in `nyxloom.toml` with the
   Assay CLI/artifact boundary. Preserve full CIU tests and meaningful coverage.
4. Resolve cgroup parent only from the explicit CIU override if one exists or
   `$CGROUP_PARENT_DEV_BACKGROUND`; with neither, refuse. Verify `LoadState=loaded`
   via `systemctl show` before Docker. No literal slice and no fallback.
5. Run a controlled bad-test/import canary in code the gate actually executes;
   prove the full gate rejects it, then restore it. Capture job status directly.
6. Perform a fresh adversarial review of code/spec/tests, adding at least one new
   combined-axis attack not named in P04-P06. Source fixes trigger BLOCKED for a
   repair handoff; documentation/config fixes may be completed here.
7. Close CIU-28/29 and the milestone only after all evidence is exact and green.

## Documentation qualification

Re-run the parser/current-schema, closed-vocabulary, and anchor tests across
README, DESIGN-GUIDE, and CONSUMERS. This package may repair drift but may not be
used to excuse missing feature docs from P04-P06.

## Test constraints (mandatory)

- No wall-clock verdicts/sleeps, global leaks, order dependence, hollow tests,
  weakened assertions, coverage exclusions, live network, or source imports.
- The canary must land in CIU code the gate imports and must be observed red.
- Read the Assay/test job exit status, never the trailing wrapper/pipe command.
- Verify actual branch Git state and review new combined-axis conditions.

## Gate and BLOCKED rule

The gate being built is also the final gate. Record commands, Assay artifact and
schema version, cgroup proof, canary rejection, exact exit status, test count,
coverage/verdict, review findings, repairs, and final commit.

## Out of scope / forbid

Do not modify CIU source, Assay source, or tester-unified. This package consumes
their released/configured interfaces only.

**BLOCKED:** emit for an `escalate_if` trigger with exact evidence; never invent
an Assay key, cgroup fallback, or source-level repair.
