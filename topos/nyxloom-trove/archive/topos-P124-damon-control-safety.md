---
schema_version: 1
id: topos-P124-damon-control-safety
project: topos
title: "Cover DAMON ownership and stop-selection safety contracts"
tier: claude-haiku-high
input_revision: "c0eda90d"
source: {kind: product-goal, ref: "global-coverage-healing"}
stack: none
depends_on: [topos-P123-mousetable-negative-cursor]
session: fresh
scope:
  touch: ["topos/tests/test_damon_control.py", "topos/nyxloom-trove/handoffs/topos-P124-damon-control-safety.md"]
  forbid: ["topos/src/topos/damon/control.py", "topos/nyxloom-trove/nyxloom.toml", "topos/tools/coverage_gate.py", "topos/pyproject.toml"]
oracles:
  - id: O1
    observable: "a full Topos target limit and an existing exact marker both refuse a start before sysfs writes"
    negative: "a second target can exceed max_concurrent_targets or overwrite an ownership marker"
    gate: topos-suite
  - id: O2
    observable: "stop requires explicit selection, rejects a foreign marker, and leaves an owned marker from another DAMON root intact"
    negative: "stop has ambiguous scope, tears down a non-Topos marker, or crosses root ownership"
    gate: topos-suite
gates: [topos-suite]
review_focus: ["assert observable marker/state preservation, not merely exception types", "keep all filesystem state under tmp_path and use existing helpers"]
escalate_if: ["a supplied expectation contradicts this frozen capsule", "scope requires an out-of-scope source edit"]
advances: []
---

# P124 — DAMON control safety capsule

Read only this handoff and `topos/tests/test_damon_control.py`; append exactly
two tests to that file and add only imports those tests need. Do not alter
existing tests or any other file. No shell, network, gate, commit, source edit,
search/listing, or new file is authorized. The controller runs the tests/gate.

1. Import `OwnershipError` and `DamonControlError`. Using existing `_damon_root`,
   `_state_dir`, `GAME_KEY`, and fixture cgroup root, create a marker file at
   `state_dir / "damon" / "kdamond-99.json"` containing `{}`. Call
   `plan_start_session` with `DamonConfig(max_concurrent_targets=1)` and
   `require_root=False`; assert `NoFreeKdamond`. Then use a fresh state dir,
   build a normal plan, create `plan`'s exact marker path containing `{}`, and
   call `start_planned_session` with `confirmed_text=APPROVAL_TEXT` and
   `require_root=False`; assert `OwnershipError` and assert the marker remains
   `{}`. This proves capacity and marker ownership fail closed before writes.
2. With a fresh `_damon_root` and `_state_dir`, first call
   `stop_owned_sessions(..., all_mine=False, kdamond_idx=None,
   require_root=False)` and assert `DamonControlError`. Create a marker JSON
   `{"owner": "foreign", "kdamond_idx": 0, "damon_root": str(damon_root)}`;
   call with `all_mine=True` and assert `OwnershipError` and that the marker
   remains. Replace its JSON with
   `{"owner": "topos", "kdamond_idx": 0, "damon_root": str(tmp_path / "other-root")}`;
   call `all_mine=True`, assert return value `0`, and assert the marker remains.
   This proves explicit selection, foreign-owner refusal, and root isolation.

Use `json.dumps`/`json.loads` as already imported in this file; do not mock
filesystem helpers. Stop after the edit and reply with test-name → behavior and
the exact changed file. Do not claim a test/gate ran. If impossible, reply
`BLOCKED: <one sentence>` and make no edit.

## Frozen source capsule

```python
103 owned = _owned_markers(state_dir or default_state_dir())
104 if len(owned) >= config.max_concurrent_targets:
105     raise NoFreeKdamond(...)
135 marker = _marker_path(plan.state_dir, plan.kdamond_idx)
136 if marker.exists():
137     raise OwnershipError(...)
200 if not all_mine and kdamond_idx is None:
201     raise DamonControlError(...)
210 if payload.get("owner") != "topos":
211     raise OwnershipError(...)
212 marker_root = Path(str(payload.get("damon_root", damon_root)))
213 if marker_root != damon_root:
214     continue
```
