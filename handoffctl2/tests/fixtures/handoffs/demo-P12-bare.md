---
schema_version: 1
id: demo-P12-bare
project: demo
title: Bare pytest
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

# Bare pytest

Bare pytest block without gate argv.

Worktree: branch context.
Branch: main

Out of scope: nothing.

Read first: docs.

```
pytest tests/ -q
```

BLOCKED: bare pytest.
