---
schema_version: 1
id: demo-P13-unknown
project: demo
title: Unknown gate
tier: flash-high
input_revision: "0000000"
source: {kind: review}
scope: {touch: ["src/test.py"]}
oracles:
  - id: O1
    observable: "pass"
    negative: "fail"
    gate: no-such-gate
gates: [no-such-gate]
escalate_if: ["trigger"]
---

# Unknown gate

Uses a gate that doesn't exist.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: unknown gate.
