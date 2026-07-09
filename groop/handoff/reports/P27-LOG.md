# P27 Work Log

## Context

- Branch: feat/groop-p27-swap-refault-aliases
- Worktree: .worktrees/-groop-p27-swap-refault-aliases
- Base commit: e9ad906 (docs(groop): carve P27 swap refault aliases)
- Package: P27 - Swap/refault terminology aliases
- Current objective: Add centralized swap_dev/rf_dev profile aliases, backend-aware labels, diagnostic wording

## Timeline

```text
2026-07-09
- Action: Created worktree and branch from main
- Commands: git worktree add -b feat/groop-p27-swap-refault-aliases .worktrees/-groop-p27-swap-refault-aliases main
- Files changed: (worktree setup only)
- Result: Worktree at e9ad906, branch created

- Action: Read required context: COMPRESSED-SWAP.md, table.py, drill.py, score.py, rules.py, registry.py
- Files changed: (none)
- Result: Understood existing label/column/profile architecture and diagnostic/finding wording

- Action: Created centralized alias module src/groop/ui/aliases.py
- Files changed: groop/src/groop/ui/aliases.py
- Details: `_COLUMN_ALIASES` map, resolve_column(), is_alias(), known_aliases(),
  BACKEND_AWARE_LABELS
- Result: Alias layer exported

- Action: Integrated aliases into table.py
- Files changed: groop/src/groop/ui/table.py
- Details: Updated import, _LABELS entries for aliases/backend-aware display, header_label(),
  format_metric_value(), metric_sort_value(), _column_supported(), _dedupe() to use resolve_column()
- Result: Aliases work in profiles, display labels are backend-aware, metric/sort lookups resolve canonically

- Action: Updated score.py rf_d_per_s label/detail for backend-aware wording
- Files changed: groop/src/groop/diag/score.py
- Details: Changed label "Disk anon refaults" → "Device anon refaults"; added zram/mixed to detail

- Action: Updated rules.py protected_disk_refault finding message
- Files changed: groop/src/groop/diag/rules.py
- Details: Changed "from disk" / "touching real storage" → "from swap device / backend may be disk, zram, or mixed"

- Action: Added 20 alias-focused tests
- Files changed: groop/tests/test_aliases.py
- Details: Alias resolution, passthrough, column support, display labels, format/sort via alias,
  profile dedup, score wording, finding wording

- Action: Ran alias tests + existing UI/diag tests
- Commands: python3 -m pytest groop/tests/test_aliases.py -v
- Result: 20/20 passed
- Commands: python3 -m pytest groop/tests/test_ui_table.py groop/tests/test_diag.py -v
- Result: 19/19 passed (no regressions)

- Action: Updated documentation
- Files changed: groop/docs/COMPRESSED-SWAP.md, groop/README.md, groop/docs/ROADMAP.md, groop/docs/STATUS.md
- Details: Alias reference section in COMPRESSED-SWAP.md; P27 done in README/ROADMAP/STATUS

- Action: Ran full suite
- Commands: python3 -m pytest groop/tests -q
- Result: 201 passed in 28.69s
- Follow-up: Write P27-REPORT.md and commit

- Action: Controller review patched P27 before merge.
- Files changed: groop/src/groop/ui/aliases.py, groop/src/groop/ui/table.py,
  groop/tests/test_aliases.py, groop/docs/COMPRESSED-SWAP.md,
  groop/docs/STATUS.md, groop/handoff/reports/P27-LOG.md,
  groop/handoff/reports/P27-REPORT.md.
- Result: Added the required legacy `rf_d` alias, normalized configured alias
  columns to canonical `ProfileLayout.columns`, removed an unused typo helper,
  fixed report/log evidence, and reran focused plus full validation.

- Action: Controller merged P27 into `main` and reran validation from the main checkout.
- Commands: `git merge --no-ff feat/groop-p27-swap-refault-aliases -m "Merge groop P27 swap refault aliases"`,
  `PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests/test_aliases.py groop/tests/test_ui_table.py groop/tests/test_diag.py -q`,
  `PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m py_compile groop/src/groop/ui/aliases.py groop/src/groop/ui/table.py groop/src/groop/diag/score.py groop/src/groop/diag/rules.py groop/tests/test_aliases.py`,
  `PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q`.
- Files changed: groop/docs/STATUS.md, groop/handoff/reports/P27-LOG.md,
  groop/handoff/reports/P27-REPORT.md.
- Result: P27 merged and validated on `main`: focused alias/table/diag tests
  `39 passed in 0.44s`; full suite `201 passed in 29.44s`; compile check
  clean.
```

## Decisions

- Decision: Create separate aliases.py module rather than inline in table.py
  Reason: Keeps the alias map isolated and importable by any consumer; easy to extend
  Impact: A single tiny module with no dependencies outside stdlib
- Decision: Wire resolve_column() into format_metric_value/metric_sort_value
  Reason: If an alias column name appears in a profile, the sort/format should resolve to the canonical key
  Impact: Clean single-path lookup for all column operations

## Blockers

- None.

## Validation

```bash
py_compile groop/src/groop/ui/aliases.py groop/src/groop/ui/table.py groop/src/groop/diag/score.py groop/src/groop/diag/rules.py groop/tests/test_aliases.py
# (no output — clean)

python3 -m pytest groop/tests/test_aliases.py -v
# 20 passed in 0.06s

python3 -m pytest groop/tests/test_ui_table.py groop/tests/test_diag.py -v
# 19 passed in 0.34s

python3 -m pytest groop/tests -q
# 201 passed in 28.69s

/tmp/p25-venv/bin/python -m pytest groop/tests/test_aliases.py groop/tests/test_ui_table.py groop/tests/test_diag.py -q
# 39 passed in 0.31s after controller review

/tmp/p25-venv/bin/python -m pytest groop/tests -q
# 201 passed in 28.91s after controller review

PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
# 201 passed in 29.44s after merge to main
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
