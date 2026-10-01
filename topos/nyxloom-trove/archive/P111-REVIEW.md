# P111-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P111-update-ops-coverage
**HEAD:** c676689af142c1c7e13ce2343c9bc8ae64e3ef72 (confirmed)
**Verdict:** **APPROVED**

## Preflight

- `pwd`: `/workspaces/vbpub/.worktrees/feat/topos-P111-update-ops-coverage`
- Branch: `feat/topos-P111-update-ops-coverage` ✓
- HEAD: full sha matches required ✓
- `git status`: clean ✓

## Gate evidence (LOG/REPORT, mechanically consistent)

Two complete xdist runs from exact clean commit: **2,135 passed, exit 0**
(70s / 69s).

```
update_ops.py: missing_lines=[], missing_branches=[]
target_record_sha256=a48772803e64446ac7b90be20102b056f5feb29ee19dcba90e885c72dcfb0dc7
Changed-line floor: 4/4, 100% >= 100%
```

Both runs: literal intersections empty, whole-file record empty, record hash
identical. O1/O4 satisfied. 25 collected cases, 2,110 + 25 = 2,135. ✓

## Source edit audit (4 changed executable lines)

### BaseException→Exception repair (current lines 140, 297)
Two `except BaseException:` narrowed to `except Exception:`. Ordinary
failures remain fail-closed; `KeyboardInterrupt`/`SystemExit` propagate.

- **Resolution reader** (line 140): `test_default_reader_ordinary_
  resolution_failure_returns_none` proves `RuntimeError` → `None`.
  `test_default_reader_does_not_swallow_keyboard_interrupt` proves
  `KeyboardInterrupt` propagates with exactly one `load(None)` call.
- **Preview reader** (current line 297; baseline line 293):
  `test_preview_unreadable_usage_has_
  exact_fail_closed_error` proves `RuntimeError` → fail-closed
  `ValueError` with complete message. `test_preview_does_not_swallow_
  keyboard_interrupt` proves `KeyboardInterrupt` propagates with
  exactly one reader call. ✓

### Bool numeric rejection (current lines 179, 181–185)
- **Memory** (line 179): `type(memory) is not int` — `True` is rejected
  because `type(True) is bool`, not `int`. `test_update_argv_rejects_
  invalid_typed_memory` parametrized with `True`. ✓
- **CPU** (expression starts at line 181; bool clause is line 182):
  `isinstance(cpus, bool)` — explicit bool rejection
  before `isinstance(cpus, (int, float))`. `test_update_argv_rejects_
  invalid_typed_cpus` parametrized with `True`. ✓

The changed-line evaluator classifies the multiline CPU condition as executable
at line 181, not each continuation line. Its exact `4/4` denominator is current
lines 140, 179, 181, and 297.

## Literal line verification (baseline → current `nl -ba`)

All 28 baseline lines were mapped to source at HEAD:

| Baseline | Current | Source |
|------|------|--------|
| 58 | 58 | `raise ValueError(f"cpus must be a finite number: {value!r}")` |
| 63 | 63 | `raise ValueError(` |
| 94 | 94 | `raise ValueError("memory must be a non-empty string")` |
| 103 | 103 | `raise ValueError(f"memory value {parsed} exceeds maximum {upper}")` |
| 130 | 130 | `key = target` |
| 131 | 131 | `if "/" not in target:` |
| 132 | 132 | `try:` |
| 133 | 133 | `from topos.collect.collector import Collector` |
| 134 | 134 | `from topos.collect.dockerjoin import resolve_container_key` |
| 135 | 135 | `from topos.config import load` |
| 137 | 137 | `frame = Collector(config=load(None)).collect_once()` |
| 138 | 138 | `entities = {k: ef.entity for k, ef in frame.entities.items()}` |
| 139 | 139 | `key = resolve_container_key(target, entities)` |
| 140 | 140 | `except Exception:` (was `BaseException`) |
| 141 | 141 | `return None` |
| 142–146 | 142–146 | Cgroup read + OSError/ValueError handling |
| 174 | 174 | `raise ValueError("target must be a non-empty string")` |
| 178 | 178 | `raise ValueError("at least one of --memory or --cpus is required")` |
| 180 | 180 | `raise ValueError("memory must be a positive integer")` |
| 182 | 186 | `raise ValueError("cpus must be a positive number")` |
| 293 | 297 | `except Exception:` (was `BaseException`) |
| 294 | 298 | `current_usage = None` |
| 298 | 302 | fail-closed `raise ValueError(` for unreadable usage |
| 339 | 343 | `parts.append(f"CPUs: {plan.cpus}")` |

All 14 branch pairs covered.

## Test quality audit (16 functions, 6 parametrized, 25 collected cases)

| Test | Cases | Assertion |
|------|:-----:|-----------|
| `test_cpu_value_must_be_finite_exactly` | 3 | Exact ValueError per non-finite (nan, inf, -inf) |
| `test_cpu_value_must_not_exceed_explicit_limit` | 1 | Exact ValueError with ratio |
| `test_memory_value_must_be_a_nonempty_string` | 2 | Exact ValueError (None, "") |
| `test_memory_value_must_not_exceed_explicit_limit` | 1 | Exact ValueError with byte count |
| `test_default_reader_resolves_name_once_and_reads_exact_cgroup_path` | 1 | Exact integer + full call dict (load, init, collect, resolve) + exact cgroup path |
| `test_default_reader_direct_key_failures_return_none_exactly` | 2 | `is None` + exact read path (OSError, ValueError) |
| `test_default_reader_ordinary_resolution_failure_returns_none` | 1 | `is None` + `calls == [None]` |
| `test_default_reader_does_not_swallow_keyboard_interrupt` | 1 | `pytest.raises(KeyboardInterrupt)` + `calls == [None]` |
| `test_update_argv_requires_nonempty_string_target` | 2 | Exact ValueError (None, "") |
| `test_update_argv_requires_at_least_one_resource` | 1 | Exact ValueError |
| `test_update_argv_rejects_invalid_typed_memory` | 3 | Exact ValueError ("1024", 0, True) |
| `test_update_argv_rejects_invalid_typed_cpus` | 3 | Exact ValueError ("2", 0, True) |
| `test_preview_unreadable_usage_has_exact_fail_closed_error` | 1 | Exact ValueError with complete safety message + `calls == ["worker"]` |
| `test_preview_does_not_swallow_keyboard_interrupt` | 1 | `pytest.raises(KeyboardInterrupt)` + `calls == ["worker"]` |
| `test_render_cpus_only_plan_is_complete` | 1 | Complete multiline text |
| `test_render_memory_plan_without_current_usage_is_complete` | 1 | Complete multiline text (no current-usage line) |

All 25 cases use complete values, call structures, rendered output, or exact
exception text/type. Zero substring, membership, non-None, range, len-only,
or assertion-free bodies. Zero duplicates. Zero `pass`.

Only collector/config/resolver/Path/current-reader seams are injected; no
target function (`build_update_argv`, `build_update_preview`,
`_default_current_memory_reader`, `validate_cpus`, `validate_memory`) is
mocked.
