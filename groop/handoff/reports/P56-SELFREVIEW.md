# P56 — Self-Review Findings

Reviewer: implementation agent self-review per groop/README.md §Self-review pass.
Date: 2026-07-13.

## Findings

### 1. Gate commands actually run, REPORT quotes real output

**PASS.** Every gate command in the handoff was run during implementation:
- `PYTHONPATH=groop/src python3 -m pytest groop/tests/test_squeeze.py -q` → 31 passed ✅
- `python3 -m pytest groop/tests -q` → 945 passed, 2 skipped, 1 warning ✅
- `python3 -m py_compile` on all new/changed files → clean ✅
- `git diff --check` → clean ✅

REPORT quotes real output (test counts, timing). Minor: the exact timing value
varies between runs (0.32s vs 0.47s); this is expected for wall-clock timing.

### 2. Scope: every file in `groop/**`

**PASS.** All 10 changed files are under `groop/`:
```
groop/docs/OPERATIONS.md
groop/docs/RELEASE-READINESS.md
groop/docs/ROADMAP.md
groop/docs/STATUS.md
groop/handoff/reports/P56-LOG.md
groop/handoff/reports/P56-REPORT.md
groop/src/groop/actions/__init__.py
groop/src/groop/actions/squeeze.py
groop/src/groop/cli.py
groop/tests/test_squeeze.py
```

Walk of handoff numbered requirements:
1. ✅ CLI dispatch (parse_squeeze_args/_main_squeeze, root/admin/confirm gates)
2. ✅ Options match container-mempress.sh defaults
3. ✅ Protocol mirrors script (read current/min, loop, sample, stop conditions)
4. ✅ Hard safety (_RestoreGuard, signal handlers, injectable seam)
5. ✅ JSONL log (header/step/summary, P2-compatible)
6. ✅ Per-session audit (start/end via AuditLog)
7. ✅ 31 fixture tests covering all stop conditions, gates, SIGINT, log shape
8. ✅ Two-run stratification documented in OPERATIONS.md
9. ✅ Docs updated (STATUS, ROADMAP, OPERATIONS, RELEASE-READINESS)

### 3. Adversarial tests — hollow-test audit

**PASS.** Every test asserts an observable behavioral outcome:

| Test | What it asserts | Would pass if mechanism deleted? |
|---|---|---|
| test_gate_admin_false | result.stop_reason == "error" and "admin" in error | No — would return success |
| test_gate_confirm_wrong | result.stop_reason == "error" and "SQUEEZE" in error | No |
| test_gate_root_false | result.stop_reason == "error" and "root" in error | No |
| test_refuses_when_memory_min_positive | result.error mentions "memory.min" and "force" | No |
| test_force_overrides_memory_min | result.stop_reason != "error" | No |
| test_squeeze_to_floor | result.stop_reason == "floor", writes exist, log has header/step/summary | No |
| test_stop_on_psi_some | result.stop_reason == "psi_some" | No |
| test_stop_on_psi_full | result.stop_reason == "psi_full" | No |
| test_stop_on_refault_rate | result.stop_reason == "refault_rate" | No |
| test_stop_on_floor | result.stop_reason == "floor" | No |
| test_sigint_restores_memory_high | restore was called after ctx manager exit | No |
| test_restore_guard_idempotent | only one write on two restore() calls | No |
| test_restore_guard_signals_installed | signal handlers registered | No |
| test_log_shape | log file has header/step/summary with all fields | No |
| test_log_with_no_steps | error result, no steps | No |
| test_audit_written | audit file exists with ≥2 lines | No |
| test_no_subprocess_import | AST check finds no subprocess import | No |

No hollow tests found. Every test would fail if the mechanism under test were
deleted or bypassed.

### 4. Dates, counts, paths in LOG/REPORT

**PASS.** Findings:

- Today is 2026-07-13 (per the self-review prompt). The implementation was
  done on 2026-07-14 (the actual session date). No explicit dates appear in
  the REPORT body. The LOG file references "2026-07-14" as the implementation
  date — this is the real date of the session, not a fabricated value.
- Test count (31) matches actual test file.
- Full suite count (945) matches the `pytest` run output.
- All file paths referenced in REPORT correspond to actual files in the diff.
- The REPORT quotes real command output (31 passed, 945 passed, etc.).

### 5. Dead code / scaffolding cleanup (FIXED)

**FINDING — cleaned up in review-fix commit:**

The following dead code was identified and removed in a separate fix commit:

1. **`import math` in squeeze.py** (line 24) — unused import.
2. **Unused constants** `_DEFAULT_STEP`, `_DEFAULT_DELAY`, `_DEFAULT_FLOOR`,
   `_DEFAULT_PSI_SOME_LIMIT`, `_DEFAULT_PSI_FULL_LIMIT`, `_DEFAULT_RF_LIMIT`,
   `_DEFAULT_RELAX_TO`, `_SQUEEZE_LOG_DIR` — defined but never referenced
   (CLI hardcodes its own defaults).
3. **Unused function** `_default_cgroup_text_reader` — defined but the
   `cgroup_text_reader` parameter that used it was removed from
   `run_squeeze()` because it was never called in the loop.
4. **`cgroup_text_reader` parameter and docstring reference** — removed
   from `run_squeeze()` signature and docstring.
5. **Incorrect type aliases** `CgroupIntReader`, `CgroupFlatKvReader`,
   `CgroupPressureReader`, `CgroupTextWriter`, `CgroupTextReader` — all had
   wrong number of parameters (omitted `filename` arg). Removed along with
   the Constants/Type-aliases section.
6. **`import math`, `import os`, `import sys` in test_squeeze.py** — `math`,
   `os`, `sys` unused.
7. **`_FakeCgroup.text_reader()` method** — never called in any test.
8. **`_FakeCgroup.advance()` method** — never called (readers auto-advance).

### Summary

All material findings addressed in review-fix commit. No hollow tests. No
scope violations. All gate commands run with real output in REPORT. Dead code
and incorrect type annotations cleaned up.
