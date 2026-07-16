---
schema_version: 1
id: demo-P10-schema
project: demo
title: Schema violation
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

# Schema violation

Missing required tier field.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: schema invalid.
