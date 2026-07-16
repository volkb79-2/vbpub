# P49 — Frontier Review (pass #2, merge gate)

Reviewer: frontier review + merge-authority session (Opus high), per
`docs/controller-workflow-v2.md` §6-§8. Date: 2026-07-13.

Verdict: **APPROVED — merged `--no-ff`** (after review-fix of stale test count).

## Scope / contract check

Diff touches only `topos/**` (14 files). Walked the handoff's 6 numbered
requirements; all met:

1. Composite `"UNIT KEY=VALUE"` target format removed; `catalog._systemd_set_property`
   and `validate_target` now reject `=`/whitespace and accept only a bare unit
   name, forcing the structured `--property`/`--value` governance path.
2. `validate_memory_high_value` accepts only `max` or a canonical positive
   integer, with 2^63-1 overflow ceiling; rejects `%`, signs, whitespace,
   decimals, hex, commas, quotes, zero, negatives (parametrized: 5 valid /
   17 invalid / non-string).
3. Current value read via injectable `_systemctl_show_reader`; stale detection
   re-reads before the runner and returns `outcome="stale"` (observable test
   asserts outcome + "value changed" stderr).
4. `.scope` → `--runtime`, service/slice → persistent; explicit `--mode`
   override validated; preview renders argv/old/new/persistence.
5. Reuses the P46 gate chain — admin, `EXECUTE` confirmation, root check,
   timeout validation, absolute-audit-path, pre/post fail-closed audit, bounded
   runner. `set-property` stays out of `EXECUTION_ALLOWLIST`, so the only
   execution path is `execute_set_property`, gated on `--property`+`--value`.
   No cgroupfs write anywhere.
6. 69 tests (validation, unit, persistence, argv, preview, execute gates, audit,
   stale, runtime argv, CLI arg parsing/routing) — all assert observable
   artifacts (argv lists, audit file re-parsed, outcome fields), no mock
   bookkeeping. Docs updated (STATUS, ROADMAP, OPERATIONS, RELEASE-READINESS).

Fail-closed ordering verified: gates → property/value/unit validation → argv
build → pre-audit → stale re-read → runner → post-audit. Consistent with P46
(pre-runner refusals are not audited; execution attempts always are).

## Review-fix applied

- **Stale test count.** REPORT and STATUS claimed `197` focused tests, but the
  self-review fix commit (`22b48d8`) added 3 CLI integration tests afterward;
  reviewer rerun observed **200 passed**. Corrected `STATUS.md` (×2) to 200 and
  `P49-REPORT.md` to "69 new tests (200 total)". LOG timeline entries left as
  historical record of the 197-count moment.

## Pass #1 (self-review) overlap — trial metric

| Finding | Source | flagged-by-pass-1 |
|---|---|---|
| Dead code in `governance.py` (`import math`, `from pathlib import Path`, `_SHOW_CACHE`) removed | pass #1 (fixed in 22b48d8) | **yes** |
| `cli.py` imported `execute_set_property` from wrong module | pass #1 (fixed in 22b48d8) | **yes** — pass #2 py_compile/import check would also catch |
| Missing CLI arg-parse/routing tests | pass #1 (added in 22b48d8) | **yes** |
| Stale test count 197 vs actual 200 in REPORT/STATUS | pass #2 | **no** — pass #1 itself added the 3 tests but did not reconcile the count in the live docs |
| `-W error` full-suite contract unmeetable in this environment (repo-wide `jsonschema`/`schemathesis` deprecation; fails identically on `main`) | pass #2 | **no** |

Pass-#1 overlap on P49: 3/5 findings.

## Minor notes (not blocking)

- CLI does not thread `planned_current_value` between the separate preview and
  execute invocations, so stale detection is API-only for now. Honestly
  disclosed in the REPORT known-gaps; the mechanism exists and is tested.
- P49-REPORT/SELFREVIEW markdown contain a few non-ASCII glyphs (checkmark,
  arrow); source code is ASCII-clean. Not corrected (repo reports already use
  em-dashes); noted for accuracy since the self-review claimed ASCII-clean docs.

## Gate results (reviewer rerun)

Environment: `/home/vscode/.venv` (pytest 8.4.2, textual 8.2.8), Python 3.14.6,
`PYTHONPATH=topos/src`.

```
# focused
python -m pytest topos/tests/test_actions.py -q
200 passed, 1 warning in 0.75s

# full suite (plain, in-worktree)
python -m pytest topos/tests -q
897 passed, 2 skipped, 1 warning in 122.84s

# git diff --check main...HEAD
diff-check-clean
```

`-W error` note identical to P48: unmodified `main` also fails the suite under
`-W error` because `schemathesis` imports a deprecated
`jsonschema.RefResolutionError`. Pre-existing environment condition, not a P49
regression. Post-merge validation from `main` recorded separately.
