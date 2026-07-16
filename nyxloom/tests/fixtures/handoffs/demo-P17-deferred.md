---
schema_version: 1
id: demo-P17-deferred
project: demo
title: Deferred oracle
tier: flash-high
input_revision: "0000000"
source: {kind: review}
scope: {touch: ["src/test.py"]}
oracles:
  - id: O1
    observable: "the reviewer will validate the venv build"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

# Deferred oracle

The oracle delegates to the reviewer.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: deferred oracle.
