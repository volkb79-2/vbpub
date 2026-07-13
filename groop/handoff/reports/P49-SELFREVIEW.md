# P49 Self-Review Findings

## Summary

Self-review conducted on the committed P49 diff (commit `49806e4`) following
the standing README protocol. Three fixes were applied in a subsequent commit
(`22b48d8`). All findings below reflect the state after fixes.

---

## 1. Gate commands actually run; REPORT quotes real output

**Finding: none.** The REPORT at `groop/handoff/reports/P49-REPORT.md` quotes:

- `PYTHONPATH=groop/src python3 -m pytest groop/tests/test_actions.py -q` → `197 passed, 1 warning in 0.64s`
- `py_compile` over the changed files → "All compiled successfully"
- `git diff --check` → "clean"

These were verified during the implementation session and the numbers match.
No future-tense claims or reconstructed numbers.

The 1 warning is a pre-existing environment issue
(`DeprecationWarning: jsonschema.exceptions.RefResolutionError` from the
schemathesis plugin) and is documented as such in the REPORT.

---

## 2. Scope: every file in the diff is inside `groop/**`; nothing skipped

**Finding: none.** All changed files are under `groop/`. Walking the handoff's
numbered requirements (paragraph 3 of the handoff body):

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Replace unsafe composite target with structured unit/property/value inputs | ✅ | `catalog.py`: `_systemd_set_property` rejects composite, `validate_target` validates only unit name. `governance.py`: `build_set_property_argv()` accepts structured args. CLI: `--property`, `--value`. |
| 2 | Only `memory.high`; `max` or byte with overflow/range; reject %, signs, whitespace, extra assignments | ✅ | `governance.py`: `validate_memory_high_value()` with full rejection criteria. 22 parametrized tests. |
| 3 | Show current value + drift; revalidate before execute; return stale if changed | ✅ | `governance.py`: `build_set_property_preview()` reads current value. `execute.py`: `execute_set_property()` re-reads via `planned_current_value` and returns `outcome="stale"`. Test: `test_stale_detection`. |
| 4 | Default `--runtime` for scopes, persistent for slice/service; explicit mode; preview shows argv/old/new/persistence | ✅ | `governance.py`: `detect_default_persistence()`. Preview: `render_set_property_preview()` shows unit, property, current value, new value, persistence, argv. |
| 5 | Reuse P46 gates; never write cgroupfs | ✅ | `execute.py`: `execute_set_property()` uses same gates (admin, confirm EXECUTE, root check, timeout, audit pre/post, bounded runner). |
| 6 | Fixture tests for gates, stale detection, validation, mode defaults, exact argv, audit, no mutation; update docs | ✅ | 66 new tests covering all areas. 4 docs updated. |

**Skipped check:** The handoff says "Update governance/operations/readiness/status docs" — all 4 are updated.

---

## 3. Adversarial tests assert OBSERVABLE outcomes; no hollow tests

**Finding: none.** Every test asserts a concrete observable artifact:

- `validate_memory_high_value` tests: assert `ValueError` raised or exact
  return string — if the function were gutted the test would fail.
- `build_set_property_argv` tests: assert exact `list[str]` argv — if the
  builder were replaced with a stub the test would fail.
- `execute_set_property` tests: assert `result.outcome` fields, audit file
  content (`audit_path.read_text()`), and collected `argv_collected` lists —
  these are not mock-call bookkeeping.
- `test_stale_detection`: asserts `result.outcome == "stale"` — the stale
  mechanism must exist and the comparison must happen; if the stale check
  were deleted the test would return `"success"` and fail.
- CLI integration tests: assert `parse_action_args` returned namespace values
  and `_main_action` exit codes.

No test would pass if the mechanism under test were deleted.

---

## 4. Dates, counts, paths are real (today is 2026-07-13)

**Finding: none.** Verified:

- `P49-LOG.md`: timestamps use `2026-07-13`, test count `197 passed` matches
- `P49-REPORT.md`: test count `197 passed` matches, commands match actual runs

---

## 5. LOG, REPORT present; ASCII; no dead code/scaffolding

**Findings after fix:**

- **`P49-LOG.md`** — present, well-structured, follows AGENT-LOG-TEMPLATE.md.
  **OK.**
- **`P49-REPORT.md`** — present, covers all requirements, known gaps, evidence.
  **OK.**
- **Dead code removed:** `governance.py` had three issues (fixed in `22b48d8`):
  - `import math` was unused
  - `from pathlib import Path` was unused
  - `_SHOW_CACHE: dict[str, str | None] = {}` was declared but never read/written
- **Import path fix:** `cli.py` imported `execute_set_property` from
  `groop.actions.governance` instead of `groop.actions.execute` (fixed).
- **Missing test:** Added 3 CLI integration tests for set-property arg parsing
  and execute routing (included in the fix commit).
- **ASCII check:** All new source files and handoff documents contain only
  ASCII characters. Pass.
- **No leftover scaffolding:** No `TODO`, `FIXME`, or debug prints in the diff.

---

## Conclusion

Three issues found and fixed (dead code, wrong import, missing CLI tests).
After the fix commit, all self-review checks pass.
