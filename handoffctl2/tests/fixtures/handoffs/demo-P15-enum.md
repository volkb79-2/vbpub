---
schema_version: 1
id: demo-P15-enum
project: demo
title: Enumerated oracle
tier: flash-high
input_revision: "0000000"
source: {kind: review}
scope: {touch: ["src/test.py"]}
oracles:
  - id: O1
    observable: "every audit record field matches: `outcome`, `stderr`"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

# Enumerated oracle

Assert every audit record field is present.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: enumerated oracle.
