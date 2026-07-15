---
schema_version: 1
id: demo-P11-dangling
project: demo
title: Dangling dependency
tier: flash-high
input_revision: "0000000"
source: {kind: review}
scope: {touch: ["src/test.py"]}
depends_on: [demo-P99-ghost]
oracles:
  - id: O1
    observable: "pass"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

# Dangling dependency

Depends on a non-existent task.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: dependency does not exist.
