---
schema_version: 1
id: demo-P18-path
project: demo
title: Non-resolving path
tier: flash-high
input_revision: "0000000"
source: {kind: review, ref: "../dstdns/docs/spec.md"}
scope: {touch: ["src/test.py"]}
oracles:
  - id: O1
    observable: "pass"
    negative: "fail"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["trigger"]
---

# Non-resolving path

Source references a non-resolving path.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: non-resolving path.
