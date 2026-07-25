---
schema_version: 1
id: topos-P107-network-provider-coverage
project: topos
title: "Complete private-netns and BPF provider coverage"
tier: sonnet5-high
input_revision: "85ffeebb"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P106-daemon-deploy-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/providers/net_netns.py"
    - "src/topos/providers/net_bpf.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P107-network-provider-coverage.md"
    - "nyxloom-trove/reports/P107-*.md"
  forbid:
    - "src/topos/providers/net_host.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "src/topos/providers/net_netns.py and src/topos/providers/net_bpf.py each have empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 28-line/12-pair residual"
    negative: "completion is claimed from aggregate, serial, focused, rounded, or partial coverage without both whole-file records empty"
    gate: topos-suite
  - id: O2
    observable: "tests assert complete NetSample/status/candidate outputs for every rejection, aggregation-proof, malformed snapshot, read-error, and no-counter path"
    negative: "tests mock the function under test, contain pass or assertion-free bodies, assert only substrings/selected fields/non-None/ranges/calls, or use incidental host namespaces"
    gate: topos-suite
  - id: O3
    observable: "fixtures deterministically prove multiple/shared/missing namespaces, absent child observations, duplicate namespace aggregation, invalid cgroup mappings/map rows, snapshot OSError, and mapped ids without counters"
    negative: "coverage hits substitute for causal inputs, unrelated large integration fixtures obscure the path, or branch directions are inferred backwards"
    gate: topos-suite
  - id: O4
    observable: "two clean exact topos-suite runs pass with identical complete executed/missing records for both target files"
    negative: "serial-only evidence, xdist drift, reporter failure, or any target gap is accepted"
    gate: topos-suite
  - id: O5
    observable: "receipts contain literal before/after intersections, exact case arithmetic from 2052, normalized target hashes, exact commands/exits, and no contradicted universal claims"
    negative: "counts replace literal sets, evidence is reconstructed from memory, or prose contradicts code or command traces"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a residual branch is mechanically unreachable and closing it requires a semantic product decision rather than removal of demonstrably dead or redundant code"
  - "a real defect requires source outside the two provider modules"
  - "either clean full-gate run fails or target records differ"
advances: []
---

# P107 — Complete private-netns and BPF provider coverage

## Goal

Bring `providers/net_netns.py` and `providers/net_bpf.py` to exact 100%
statement and branch coverage using deterministic provider inputs and complete
network-sample/status assertions.

## Literal residual

```text
net_netns.py lines:
67 68 75 76 82 83 114 115 122 123 147 152 153 164 165 177

net_netns.py pairs:
66->67 74->75 81->82 113->114 121->122 176->177

net_bpf.py lines:
72 78 81 82 101 106 133 134 135 148 151 196

net_bpf.py pairs:
71->72 77->78 100->101 147->148 150->151 195->196
```

Completion also requires empty whole-file missing sets for both files.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and
   validation principles.
3. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL3 and PL4 through
   P106.
4. `topos/nyxloom-trove/reports/P106-REPORT.md` and `P106-REVIEW.md`, only the
   verified 2,052-case baseline and exact-evidence discipline.
5. `topos/tests/test_network_providers.py`, provider fixtures, and both target
   files at the assigned revision.

## Work

1. Preflight the assigned P107 worktree and exact revision; all earlier
   worktrees and shared-main project files are stale/forbidden.
2. Confirm the literal residual mechanically against full xdist coverage JSON
   and exact `nl -ba`.
3. Add bounded tests for netns multiple namespaces, missing net/dev, shared
   private namespace, absent child observation, duplicate child namespace,
   status copying, host namespace stat failure, invalid pid text, and missing
   pid net/dev.
4. Add bounded tests for invalid BPF snapshot shape, non-string/invalid cgroup
   mappings, mapped cgroups without counters, snapshot read `OSError`,
   non-list maps/non-dict rows, and unmatched valid entries. Compare complete
   `NetSample`, candidate, result, or status structures.
5. Prefer direct stable helper tests where constructing a complete entity tree
   would obscure one parser branch. Patches may cover filesystem read/stat
   seams but never the target function itself.
6. No partial-field, substring, membership-only, non-None, range, length-only,
   hollow, duplicate, sleep, host-state, pragma, omit, gate, or dependency
   weakening.
7. Use the declared bind-mounted tester runner only; never create host venvs,
   copy worktrees, rebuild images, or guess mounts.
8. Run two complete xdist gates and print literal intersections, whole-file
   sets, executed counts, and normalized hashes inside each container.
9. Record function/case arithmetic from 2,052, exact commands/exits, causal
   assertions, parity, `git diff --check`, LOG/REPORT/SELFREVIEW, and commit.

## BLOCKED

On a mechanical `escalate_if`, write
`BLOCKED: <trigger and exact evidence>` to `P107-LOG.md` and stop. Never return
partial `done`.
