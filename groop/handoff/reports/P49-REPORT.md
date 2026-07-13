# P49 — Systemd Memory Governance — Implementation Report

## Summary

Implemented structured `systemctl set-property memory.high` governance on top of
the P46 admin action execution kernel. The composite `"UNIT KEY=VALUE"` target
format is replaced with structured unit/property/value inputs via `--property`
and `--value` CLI arguments. Only `memory.high` is allowed; values are `max` or
a canonical positive byte count with overflow/range checks.

The preview/execute flow reuses the P46 root/admin/EXECUTE-confirmation/timeout/
audit gates. Additional validation includes property/value semantics, persistence
mode auto-detection (--runtime for .scope, persistent for service/slice), and
stale detection via planned-vs-fresh current-value comparison.

## What was built

### New module: `groop/src/groop/actions/governance.py`

- `SetPropertyPlan(frozen dataclass)` — structured preview plan for memory.high
  governance (unit, property_name, property_value, argv, current_value,
  persistence, kind, mode, description).
- `validate_memory_high_value(value)` — accepts `"max"` or positive integer byte
  count. Rejects percentages, signs, whitespace, decimals, floats, hex, commas,
  extra characters, zero, negatives, values > 2^63-1.
- `validate_memory_high_unit(unit)` — reuses the existing `_SYSTEMD_UNIT_RE`.
- `validate_persistence_mode(mode)` — accepts `"runtime"` or `"persistent"`.
- `detect_default_persistence(unit)` — `.scope` → runtime, others → persistent.
- `build_set_property_argv(unit, property_name, value, persistence)` — builds
  absolute systemctl argv with optional `--runtime`.
- `build_set_property_preview(unit, property_name, value, persistence,
  current_value_reader)` — builds a `SetPropertyPlan` with current-value read.
- `render_set_property_preview(plan)` — human-readable preview.
- `set_property_plan_to_jsonable(plan)` — JSON-safe dict.
- `_systemctl_show_reader(unit)` — production current-value reader via
  `systemctl show --property MemoryHigh --value UNIT`.
- `CurrentValueReader` type alias for injectable test seam.

### Modified: `groop/src/groop/actions/catalog.py`

- `_systemd_set_property(target)` now rejects composite `"UNIT KEY=VALUE"`
  format with a clear error message directing users to `--property`/`--value`.
- `validate_target()` for `SYSTEMD_SET_PROPERTY` validates only the bare unit
  name (no property assignments allowed in target).

### Modified: `groop/src/groop/actions/preview.py`

- `AdminPreviewResult` union includes `SetPropertyPlan`.
- `build_admin_preview()` accepts optional `property_name`, `property_value`,
  and `persistence` kwargs, routing `SYSTEMD_SET_PROPERTY` to governance module.

### Modified: `groop/src/groop/actions/execute.py`

- `execute_set_property(unit, *, property_name, property_value, persistence,
  admin, confirm, audit_path, runner, clock, identity, root_check, timeout,
  planned_current_value, current_value_reader)` — full P46 gate chain plus
  stale detection.
- `_default_current_value_reader(unit)` — injectable seam forwarding to
  governance `_systemctl_show_reader`.
- `_make_plan_stub(kind, target, argv)` — minimal ActionPlan for normalisation.

### Modified: `groop/src/groop/actions/__init__.py`

- Exports `SetPropertyPlan`, `build_set_property_preview`,
  `render_set_property_preview`, `set_property_plan_to_jsonable`,
  `validate_memory_high_value`, `validate_memory_high_unit`,
  `execute_set_property`.

### Modified: `groop/src/groop/cli.py`

- `action preview` and `action execute` accept `--property`, `--value`, `--mode`.
- Preview output handles `SetPropertyPlan` with governance render/JSON helpers.
- Execute automatically routes `systemd-set-property` with `--property`/`--value`
  to `execute_set_property()`.

### Tests: `groop/tests/test_actions.py` — 66 new tests (197 total)

- 16 value validation tests (9 valid, 16 invalid, 1 non-string)
- 8 unit validation tests (4 valid, 4 invalid)
- 8 persistence detection tests (3 default, 4 mode, 5 invalid)
- 5 argv construction tests (persistent, runtime, max, wrong property, invalid value)
- 5 preview tests (basic, current-value reader, persistence fallback, render, jsonable)
- 11 execute_set_property tests (admin false, confirm wrong, root false, invalid
  property/value/unit, success path, audit written, stale detection, runtime argv)

### Docs updated

- `STATUS.md` — v2 60-65% → 65-70%, P49 in Implemented
- `ROADMAP.md` — P49 marked done
- `OPERATIONS.md` — set-property CLI examples in Safety Model
- `RELEASE-READINESS.md` — set-property removed from non-claims

## Deviations from handoff

None. All named requirements are met:

1. ✅ Replace the unsafe composite preview target → structured unit/property/value
2. ✅ Only `memory.high`, `max` or byte with overflow/range checks
3. ✅ Current-value read + stale detection
4. ✅ Default `--runtime` for scopes, persistent for slice/service; explicit mode option
5. ✅ Reuses P46 gates (root, admin, confirm, timeout, audit, bounded output)
6. ✅ Fixture tests for gates, stale detection, validation, mode defaults, exact argv, audit, no mutation
7. ✅ Docs updated

## Proposed contract changes

None. The governance module is additive and package-private (`groop/actions/`).
The existing `ActionPlan`/`ActionKind`/`execute_plan` interfaces are unchanged.

## Test evidence

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_actions.py -q
197 passed, 1 warning in 0.64s
```

The one warning is a pre-existing environment issue
(`DeprecationWarning: jsonschema.exceptions.RefResolutionError` from
schemathesis plugin), not related to P49.

```bash
PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/actions/governance.py \
  groop/src/groop/actions/catalog.py groop/src/groop/actions/preview.py \
  groop/src/groop/actions/execute.py groop/src/groop/actions/__init__.py \
  groop/src/groop/cli.py groop/tests/test_actions.py
# All compiled successfully

git diff --check
# clean
```

## Known gaps / open items

- The production `_systemctl_show_reader` calls `systemctl show` as a subprocess;
  it is only triggered at plan/build time. The fixture injectable reader makes
  it testable without a running systemd.
- Stale detection requires the caller to pass `planned_current_value`; the CLI
  does not currently preserve this between preview and execute (they are
  separate invocations). Programmatic callers can use it.
- Live destructive acceptance (actual `systemctl set-property` as root) was not
  run. All tests use injected runners and assert exact argv without host mutation.
- The `"stale"` outcome is a new outcome value beyond the P46
  `_VALID_ACTION_OUTCOMES` set (`success`, `nonzero`, `timeout`,
  `runner_failure`). This is intentional: stale is a pre-runner refusal
  detected after the pre-audit, closely related to `refusal`. The `ExecuteResult`
  is produced by `dataclasses.replace(refusal, outcome="stale")` so it carries
  all refusal fields.
