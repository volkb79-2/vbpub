---
schema_version: 1
id: demo-P16-review
project: demo
title: Reviewer deliverable
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

# Reviewer deliverable

Update DECISIONS-INBOX.md with the merged decision.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

BLOCKED: reviewer deliverable.
