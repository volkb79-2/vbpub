---
schema_version: 1
id: topos-P125-damon-control-guards
project: topos
title: "Cover DAMON missing-input, slot, marker, and teardown guards"
tier: claude-haiku-high
input_revision: "d37fe502"
source: {kind: product-goal, ref: "global-coverage-healing"}
stack: none
depends_on: [topos-P124-damon-control-safety]
session: fresh
scope:
  touch: ["topos/tests/test_damon_control.py", "topos/nyxloom-trove/handoffs/topos-P125-damon-control-guards.md"]
  forbid: ["topos/src/topos/damon/control.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "missing PID input and an unreadable DAMON slot count fail closed"
    negative: "a start plan invents targets or chooses a slot when required kernel input is unavailable"
    gate: topos-suite
  - id: O2
    observable: "reserved/malformed markers cannot cause a collision, and teardown recreates absent context state safely"
    negative: "a reserved slot is reused, a malformed marker crashes selection, or stopping an owned marker depends on a pre-existing contexts directory"
    gate: topos-suite
gates: [topos-suite]
review_focus: ["tests must observe plan/stop behavior through public control functions, not patch internal helpers", "all paths remain under tmp_path"]
escalate_if: ["a supplied expectation contradicts this frozen capsule", "scope requires an out-of-scope source edit"]
advances: []
---

# P125 — DAMON input and teardown guard capsule

Read only this handoff and `topos/tests/test_damon_control.py`; append exactly
three tests to that file. Do not alter existing tests or any other file. No
shell, network, gate, commit, source edit, search/listing, or new file is
authorized. The controller runs tests and gates.

1. Build an empty `cgroup_root` directory with no `cgroup.procs`; call
   `plan_start_session("", ..., require_root=False)` and assert
   `NoEntityPids`. Separately make `_damon_root`, overwrite its
   `nr_kdamonds` with `"not-a-number\n"`, use the fixture cgroup root, and
   assert `plan_start_session(..., require_root=False)` raises
   `NoFreeKdamond`. These prove absence/unparseability never synthesizes a
   valid plan.
2. Make `_damon_root(..., slots=("off", "off"))` and a state marker directory
   containing both `kdamond-0.json` and malformed `kdamond-not-an-index.json`,
   each with `{}`. Plan against fixture cgroups with `require_root=False` and
   assert `plan.kdamond_idx == 1`. This proves index 0 is reserved while a
   malformed marker is ignored safely rather than crashing or reserving all
   slots.
3. Make `_damon_root(..., slots=("off",))` with no `contexts` directory and
   one marker JSON at `state/damon/kdamond-0.json` with owner `"topos"`,
   `kdamond_idx: 0`, and the exact `damon_root` string. Call
   `stop_owned_sessions(..., all_mine=True, require_root=False)`; assert it
   returns `1`, the marker is removed, and
   `damon_root / "0" / "contexts" / "nr_contexts"` contains `"0"`. This
   proves safe teardown recreates the sysfs layout after a partial/crashed
   session.

Use only existing imports/helpers and `json.dumps`. Do not mock any helper.
Stop after the edit and reply with test-name → behavior and the exact changed
file. Do not claim a test/gate ran. If impossible, reply `BLOCKED: <one
sentence>` and make no edit.

## Frozen source capsule

```python
230 procs = read_text(cgroup_path / "cgroup.procs")
231 if procs.value is None:
232     return ()
243 nr = parse_int_text(str(nr_text.value)) if nr_text.value is not None else None
244 if nr is None:
245     raise NoFreeKdamond(...)
247 for idx in range(nr):
248     if idx in owned_indexes:
249         continue
280 def _teardown_kdamond(...):
283     if contexts.exists():
284         shutil.rmtree(contexts)
285     _write_sysfs(contexts / "nr_contexts", "0")
317 def _marker_idx(marker):
318     try:
319         return int(marker.stem.rsplit("-", 1)[-1])
320     except ValueError:
321         return None
```
