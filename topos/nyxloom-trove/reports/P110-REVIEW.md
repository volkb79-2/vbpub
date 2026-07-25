# P110-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, independent of implementer)
**Branch:** feat/topos-P110-action-policy-coverage
**HEAD:** 82ff97a42c066c0605369a6dfb359e98b9294d92 (confirmed)
**Verdict:** **APPROVED**

## Preflight

- `pwd`: `/workspaces/vbpub/.worktrees/feat/topos-P110-action-policy-coverage`
- Branch: `feat/topos-P110-action-policy-coverage` ✓
- HEAD: full sha matches required ✓
- `git status`: clean ✓

## Gate evidence (LOG/REPORT, mechanically consistent)

Two complete xdist runs: **2,110 passed, exit 0** (63s / 64s).

```
catalog.py:     missing_lines=[], missing_branches=[]
governance.py:  missing_lines=[], missing_branches=[]
target_record_sha256=374dd7751da55ddfd3de60c47a98443b1579177754795798f02621f6898ebcfd
Changed-line floor: 1/1, 100% >= 100%
```

Both runs: literal intersections empty, whole-file records empty, record hash
identical. O1/O4 satisfied. 22 collected cases, 2,088 + 22 = 2,110. ✓

## Source edit audit

### Catalog dead-guard removal (O3)
Lines 188–189 (`if not target: / raise ValueError(...)`) were removed. The
shared guard at line 156 (`if not isinstance(target, str) or not target:`)
rejects every non-string/empty target BEFORE kind dispatch at line 176.
The set-property-specific empty check could never execute — **provably dead
code**. The public empty-target error behavior is preserved by the shared
guard and covered by `test_catalog_set_property_rejects_an_empty_target_exactly`. ✓

### Governance BaseException→Exception repair (O3)
Line 332 changed from `except BaseException:` to `except Exception:`.
`KeyboardInterrupt` and `SystemExit` are BaseException subclasses but NOT
Exception subclasses — they now propagate. `test_preview_does_not_swallow_
keyboard_interrupt` proves `KeyboardInterrupt` propagates with exactly one
reader call. `test_preview_ordinary_reader_failure_produces_complete_
fallback_plan` proves `RuntimeError` (an Exception) still produces the
complete fallback plan. ✓

## Literal line verification (nl -ba)

All lines verified against source at HEAD:

| File | Line | Source |
|------|------|--------|
| catalog.py | 76 | `msg = "systemd-set-property target must be a bare unit name"` |
| catalog.py | 77 | `raise ValueError(msg)` |
| catalog.py | 79 | `msg = (` |
| catalog.py | 83 | `raise ValueError(msg)` |
| catalog.py | 183 | `raise ValueError(f"execution not allowed...")` |
| catalog.py | 189 | Comment (was dead guard, now invariant note) |
| catalog.py | 191 | `raise ValueError(f"systemd set-property target must not...")` |
| catalog.py | 193 | `raise ValueError(f"invalid systemd unit name for set-property...")` |
| catalog.py | 198 | `raise ValueError(f"target must not contain whitespace...")` |
| catalog.py | 200 | `raise ValueError(f"invalid Docker container identifier...")` |
| catalog.py | 205 | `raise ValueError(f"target must not contain whitespace...")` |
| catalog.py | 207 | `raise ValueError(f"invalid systemd unit name...")` |
| governance.py | 102 | `except (TypeError, ValueError) as exc:` |
| governance.py | 103 | `raise ValueError(` |
| governance.py | 189 | `except (OSError, subprocess.TimeoutExpired) as exc:` |
| governance.py | 190 | `return None` |
| governance.py | 195 | `raw = proc.stdout.strip()` |
| governance.py | 196 | `if not raw or raw in {"", "(null)", "infinity"}:` |
| governance.py | 197 | `return None` |
| governance.py | 199 | `return raw` |
| governance.py | 236 | `raise ValueError("unit must be a non-empty string")` |
| governance.py | 321 | `persistence = validate_persistence_mode(persistence)` |
| governance.py | 332 | `except Exception:` |
| governance.py | 333 | `current_value = None` |

Lines 188–189 removed (dead guard). All retained lines + arcs covered.

## Test quality audit (11 functions, 22 collected cases)

| Test | Cases | Assertion |
|------|:-----:|-----------|
| `test_catalog_set_property_rejects_an_empty_target_exactly` | 1 | Exact ValueError text |
| `test_catalog_set_property_rejects_composite_targets_exactly` | 2 | Exact ValueError text (both "=" and whitespace forms) |
| `test_validate_target_defensively_rejects_unhandled_allowlisted_kind` | 1 | Exact ValueError with kind repr |
| `test_specialized_target_validation_errors_are_exact` | 6 | Exact ValueError per kind (whitespace, unit name, identifier) |
| `test_digit_limit_conversion_failure_has_exact_error_and_cause` | 1 | Exact error + `type(exc.__cause__) is ValueError`; saves/restores `sys.int_max_str_digits` in `finally` |
| `test_systemctl_reader_transport_failures_return_none_exactly` | 2 | `is None` + exact subprocess call (OSError, TimeoutExpired) |
| `test_systemctl_reader_result_shapes_are_exact` | 5 | Exact return value (None or string) + exact subprocess call (nonzero, empty, null, infinity, valid) |
| `test_set_property_argv_rejects_empty_unit_exactly` | 1 | Exact ValueError text |
| `test_explicit_preview_persistence_produces_complete_plan` | 1 | Complete `SetPropertyPlan` dataclass + `calls == ["demo.slice"]` |
| `test_preview_ordinary_reader_failure_produces_complete_fallback_plan` | 1 | Complete `SetPropertyPlan` with `current_value=None` + `calls == ["demo.scope"]` |
| `test_preview_does_not_swallow_keyboard_interrupt` | 1 | `pytest.raises(KeyboardInterrupt)` + `calls == ["demo.slice"]` |

All 22 cases use complete values, dataclasses, exact exception text, or exact
subprocess call structures. Zero substring, membership, non-None, range,
len-only, or assertion-free bodies. Zero duplicates. Zero `pass`.

Only subprocess and current-reader seams are injected; no target function
(`build_set_property_preview`, `validate_target`, `_systemctl_show_reader`,
`validate_memory_high_value`) is mocked.

## Specific checks

### Digit-limit isolation
`test_digit_limit_conversion_failure_has_exact_error_and_cause` saves
`sys.get_int_max_str_digits()` before setting to 640 and restores in a
`finally` block. This is proper worker-local isolation — no global leakage. ✓

### Discarded attempt labeling
The LOG truthfully documents: an uncommitted run that passed 2,110 but
produced `0/0` changed-line (not authoritative), and a hash-mismatch
preflight that exited 97 before pytest (not evidence). Only the two
immutable receipts are presented as gate evidence. ✓

### Changed-line evidence
The governance `BaseException→Exception` change is 1 executable line
(catalog comment additions are non-executable). The report correctly
shows `1/1, 100% ≥ 100%`. ✓
