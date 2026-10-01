---
schema_version: 1
id: topos-P126-damon-nonpositive-pids
project: topos
title: "Prove DAMON refuses non-positive cgroup PIDs"
tier: claude-haiku-high
input_revision: "5c671e05"
source: {kind: product-goal, ref: "global-coverage-healing"}
stack: none
depends_on: [topos-P125-damon-control-guards]
session: "resume:damon-control"
scope:
  touch: ["topos/tests/test_damon_control.py", "topos/nyxloom-trove/handoffs/topos-P126-damon-nonpositive-pids.md"]
  forbid: ["topos/src/topos/damon/control.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "a cgroup file containing only zero and negative numeric values cannot start a DAMON target"
    negative: "non-positive values are accepted as target PIDs"
    gate: topos-suite
gates: [topos-suite]
review_focus: ["assert plan_start_session's public NoEntityPids outcome, not an internal parser call"]
escalate_if: ["the stated fixture cannot reach the public refusal", "scope requires an out-of-scope edit"]
advances: []
---

# P126 — DAMON non-positive PID guard

Read only this handoff and `topos/tests/test_damon_control.py`; append exactly
one test to that file. Do not alter existing tests or any other file. No shell,
network, gate, commit, source edit, search/listing, or new file is authorized.
The controller runs tests and gates.

Under `tmp_path`, create `cgroup_root / "nonpositive.scope" / "cgroup.procs"`
containing exactly `"0\n-5\n"`. Call `plan_start_session("nonpositive.scope",
cgroup_root=..., damon_root=_damon_root(...), state_dir=_state_dir(...),
config=DamonConfig(), require_root=False)` and assert `NoEntityPids`. Do not
mock the parser or PID reader. This proves valid-but-non-positive numeric
records cannot reach DAMON configuration.

Stop after the edit and reply with test-name → behavior and the exact changed
file. Do not claim a test/gate ran. If impossible, reply `BLOCKED: <one
sentence>` and make no edit.

## Frozen source capsule

```python
233 pids: set[int] = set()
234 for line in str(procs.value).splitlines():
235     value = parse_int_text(line)
236     if value is not None and value > 0:
237         pids.add(value)
238 return tuple(sorted(pids))
```
