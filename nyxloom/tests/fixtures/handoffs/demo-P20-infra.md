---
schema_version: 1
id: demo-P20-infra
project: demo
title: Infra without stack
tier: flash-high
input_revision: "0000000"
source: {kind: review}
scope: {touch: ["infra/deploy.yml"]}
oracles:
  - id: O1
    observable: "pass"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

# Infra without stack

Touches infra but no stack mutex.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: infra without stack.
