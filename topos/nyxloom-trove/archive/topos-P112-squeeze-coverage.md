---
schema_version: 1
id: topos-P112-squeeze-coverage
project: topos
title: "Repair and complete guided squeeze coverage"
tier: sonnet5-high
input_revision: "a1c94c74"
source: {kind: product-goal, ref: "nyxloom-trove/reports/P96-COVERAGE-GAPS.md"}
stack: none
depends_on: [topos-P111-update-ops-coverage]
session: "fresh"
scope:
  touch:
    - "src/topos/actions/squeeze.py"
    - "tests/**"
    - "nyxloom-trove/handoffs/topos-P112-squeeze-coverage.md"
    - "nyxloom-trove/reports/P112-*.md"
  forbid:
    - "src/topos/actions/execute.py"
    - "src/topos/actions/update_ops.py"
    - "src/topos/cli.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tools/coverage_gate.py"
    - "pyproject.toml"
oracles:
  - id: O1
    observable: "squeeze.py has empty missing_lines and missing_branches in branch-aware JSON from the complete xdist gate, closing the literal 41-line/11-pair residual plus every repair line"
    negative: "completion is claimed from aggregate, serial, focused, rounded, warning-bearing, dirty-tree, or partial coverage without the whole-file record empty"
    gate: topos-suite
  - id: O2
    observable: "a multi-step run writes each measured memory.high in exact order, samples only after that write, and restores the configured value exactly once"
    negative: "later step records change only the Python variable while cgroup memory.high remains at the initial value, or restoration is inferred from outcome text"
    gate: topos-suite
  - id: O3
    observable: "ordinary audit/root/measurement failures retain documented fail-closed or nonfatal behavior, while KeyboardInterrupt/SystemExit propagate except for the explicit in-loop KeyboardInterrupt result contract"
    negative: "BaseException swallowing is retained or codified at audit, measurement, or root boundaries"
    gate: topos-suite
  - id: O4
    observable: "tests assert exact default reader paths/results, SIGTERM restore/exit call, log failure behavior, interruption/error results, render output, and audit/root calls without host cgroupfs or real signals"
    negative: "tests mock run_squeeze or another target function, assert only substrings/selected fields/non-None/ranges/calls, contain pass or assertion-free bodies, or invoke os._exit/host signals"
    gate: topos-suite
  - id: O5
    observable: "two complete xdist gates run from the exact clean implementation commit, close the literal/whole-file sets with identical normalized records, and reconcile from 2135"
    negative: "uncommitted evidence, serial-only evidence, xdist drift, reporter warning/failure, count-only receipts, or contradicted prose is accepted"
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "another residual is mechanically unreachable or exposes a new safety/product decision"
  - "a real defect requires source outside squeeze.py"
  - "either full gate fails or the target record differs"
advances: []
---

# P112 — Repair and complete guided squeeze coverage

Assigned branch/worktree:
`feat/topos-P112-squeeze-coverage` at
`/workspaces/vbpub/.worktrees/feat/topos-P112-squeeze-coverage`.

## Literal residual

```text
lines:
51 73 75 76 81 83 84 89 91 92 100 101 238 257 258 317 318
335 351 352 367 368 369 422 423 451 459 460 462 464 465 466
467 468 494 495 510 511 714 744 745

pairs:
50->51 235->238 247->249 249->-244 334->335 398->400 450->451
462->464 462->476 664->676 713->714
```

Completion also requires the complete file record empty.

## Product defects to repair

1. `high -= step_bytes` changes the next step record but does not write the new
   value to `memory.high`. Apply each in-floor next high before its sample and
   prove the complete initial/step/restore write sequence.
2. Audit start/end, the generic measurement handler, and the root-check wrapper
   catch `BaseException`. Narrow them to `Exception`. Preserve the explicit
   in-loop `KeyboardInterrupt` → `stop_reason="interrupted"` contract; prove
   operator interrupts escape the other three boundaries.

## Context to read first

1. `/workspaces/vbpub/AGENTS.md`.
2. `/workspaces/vbpub/nyxloom/reference/STANDARD.md`, gate contract and
   validation principles.
3. `/workspaces/vbpub/nyxloom/nyxloom-trove/LESSONS.md`, PL3/PL4 through P111.
4. P111 final report/review only for the 2,135 baseline and
   baseline→current mapping discipline.
5. `test_squeeze.py` and `squeeze.py`.

## Work

1. Confirm the exact worktree/revision and current literal residual.
2. Repair the step-write defect and four overbroad catches.
3. Cover suffix parse failure; default int/flat/pressure reader paths;
   SIGTERM restore plus injected exit; absent original handler; restore write
   failure; explicit start; log open/header/step/summary failures; zero-delta
   refault; floor-without-steps; interruption with/without steps; ordinary
   measurement failure; audit start/end failure; no-step render; `_mib(None)`;
   and root-check failure.
4. Assert complete results, JSONL/call structures, applied write order, and
   exact paths/messages. Patch only filesystem/signal/exit/audit/reader seams;
   never invoke host cgroupfs, a real signal, or real `os._exit`.
5. No weak/partial/hollow/duplicate tests, pragmas, omissions, gate changes,
   host venvs, copied worktrees, image rebuilds, or guessed runners.
6. Commit the tested implementation before authoritative gate execution. Run
   two full declared gates from its exact clean hash with in-container
   whole-file record hashes; record baseline→current mappings, complete sets,
   case arithmetic, commands/exits, `git diff --check`, LOG/REPORT/SELFREVIEW,
   and commit receipts afterward.

## Scope / forbid

Only the frontmatter touch paths may change. Execution/update/CLI code,
gate/tooling, and dependencies are out of scope and forbidden.

## BLOCKED

On a mechanical trigger, write `BLOCKED: <trigger and exact evidence>` to the
P112 log and stop. Never report partial completion.
