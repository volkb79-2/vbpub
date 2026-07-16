---
schema_version: 1
id: demo-P23-blocked
project: demo
title: No blocked marker
tier: flash-high
input_revision: "0000000"
source: {kind: review}
scope: {touch: ["src/test.py"]}
oracles:
  - id: O1
    observable: "pass"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

# No blocked marker

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

No BLOCKED marker present in this body.
