---
schema_version: 1
id: demo-P01-sample
project: demo
title: Sample bounded package
tier: flash-high
input_revision: "0000000"
source: {kind: roadmap}
scope:
  touch: ["src/demo/thing.py", "tests/test_thing.py"]
oracles:
  - id: O1
    observable: "pytest tests/test_thing.py::test_bound passes"
    negative: "a value over the limit raises BoundError (test_bound_violation)"
    gate: pytest-q
gates: [pytest-q]
escalate_if: ["a named contract cannot be met as specified"]
---

# Sample bounded package

Contract body. If a named contract cannot be met as specified, STOP, write
`BLOCKED: <reason>` to the LOG, commit, exit.

Worktree: create a git worktree for branch `feat/demo-P01-bound` from local main.

Branch name: feat/demo-P01-bound

Out of scope: database migrations, other modules.

Read first: docs/SPEC.md for context.

BLOCKED: if a requirement cannot be met as specified.
