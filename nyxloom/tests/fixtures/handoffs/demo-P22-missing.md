---
schema_version: 1
id: demo-P22-missing
project: demo
title: Missing sections
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

# Missing sections

Just body text here.

BLOCKED: sections missing.
