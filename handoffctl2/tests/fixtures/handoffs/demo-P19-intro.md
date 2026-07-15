---
schema_version: 1
id: demo-P19-intro
project: demo
title: Introspective escalation
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
escalate_if: ["reflect whether this suits your expertise"]
---

# Introspective escalation

Escalate trigger is introspective.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: introspective escalation.
