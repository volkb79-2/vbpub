---
schema_version: 1
id: demo-P14-trivial
project: demo
title: Trivial negative
tier: flash-high
input_revision: "0000000"
source: {kind: review}
scope: {touch: ["src/test.py"]}
oracles:
  - id: O1
    observable: "test passes"
    negative: "n/a"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

# Trivial negative

Oracle has trivial negative case.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: trivial negative.
