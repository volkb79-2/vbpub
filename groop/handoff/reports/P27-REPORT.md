# P27 Report — Swap/Refault Terminology Aliases

## What Was Built

- Created centralized alias module `groop/src/groop/ui/aliases.py` with:
  - `_COLUMN_ALIASES` map: `swap_dev → swap_disk`, `rf_dev_per_s → rf_d_per_s`, `rf_dev → rf_d_per_s`, `rf_d → rf_d_per_s`
  - `resolve_column(name)` — resolves alias to canonical key, returns unknown names unchanged
  - `is_alias(name)` — returns True for registered aliases
  - `known_aliases(canonical)` — reverse lookup for doc generation
  - `BACKEND_AWARE_LABELS` — `swap_disk → SWAP_DEV`, `rf_d_per_s → RF_DEV/S`
- Wired `resolve_column()` into every consumer function in `groop/src/groop/ui/table.py`:
  - `_LABELS` — added alias entries and updated `swap_disk`/`rf_d_per_s` labels to `SWAP_DEV`/`RF_DEV/S`
  - `header_label()` — resolves alias before looking up spec
  - `format_metric_value()` — resolves alias before metric lookup
  - `metric_sort_value()` — resolves alias before metric lookup
  - `_column_supported()` — accepts aliases and resolves to canonical for registry check
  - `_dedupe()` — prevents alias+canonical duplicate in profiles
- Updated diagnostic wording in `groop/src/groop/diag/score.py`:
  - `rf_d_per_s` label: "Disk anon refaults" → "Device anon refaults"
  - `rf_d_per_s` detail: added "backend may be disk, zram, or mixed according to host classification"
- Updated finding wording in `groop/src/groop/diag/rules.py`:
  - `_protected_disk_refault` message: "from disk" / "touching real storage" → "from swap device" with backend classification note
- Updated documentation:
  - `docs/COMPRESSED-SWAP.md` — added "Alias Reference" section and updated "Per-Cgroup Semantics"
  - `README.md` — P27 marked Done
  - `docs/ROADMAP.md` — P27 marked done, P19 alias gap closed
  - `docs/STATUS.md` — added alias layer to Implemented, updated compressed swap partially-implemented note, updated Quality Gate
- Added 20 focused tests in `tests/test_aliases.py`:
  1. `test_resolve_column_known_aliases` — 4 aliases -> canonical
  2. `test_resolve_column_passthrough_canonical` — canonical pass-through
  3. `test_resolve_column_passthrough_unknown` — unknown pass-through
  4. `test_is_alias` — True for aliases, False for canonical/unknown
  5. `test_known_aliases` — reverse lookup
  6. `test_backend_aware_labels` — BACKEND_AWARE_LABELS values
  7. `test_canonical_columns_are_supported` — _column_supported for canonical
  8. `test_alias_columns_are_supported` — _column_supported for aliases
  9. `test_unknown_column_not_supported` — unknown not supported
  10. `test_header_label_backend_aware_swap` — SWAP_DEV label
  11. `test_header_label_backend_aware_refault` — RF_DEV/S label
  12. `test_header_label_aliases_show_same_label` — alias & canonical same label
  13. `test_header_label_legacy_swap_disk_still_works` — non-empty, contains SWAP
  14. `test_format_metric_via_alias` — format via alias reads canonical metric
  15. `test_sort_value_via_alias` — sort via alias reads canonical metric
  16. `test_custom_profile_with_aliases_resolve` — alias profile not ignored
  17. `test_custom_profile_with_mixed_aliases_and_canonical_deduplicates` — dedup
  18. `test_score_rf_d_label_is_backend_aware` — score label is "Device anon refaults"
  19. `test_score_rf_d_detail_is_backend_aware` — detail mentions zram/mixed
  20. `test_protected_disk_refault_message_is_backend_aware` — no "from disk" claim

## Deviations

- None significant. The handoff suggested the alias layer could live in `table.py`; I chose a dedicated `aliases.py` module for clarity and testability.
- `rf_dev` and `rf_d` were added as aliases for `rf_d_per_s` alongside
  `rf_dev_per_s`; `rf_d` preserves the legacy short form used in older profile
  examples.

## Contract Changes

- None. Canonical registry/metric keys, threshold keys, and serialized frame keys are unchanged.

## Test Evidence

```bash
python3 -m py_compile groop/src/groop/ui/aliases.py groop/src/groop/ui/table.py groop/src/groop/diag/score.py groop/src/groop/diag/rules.py groop/tests/test_aliases.py
# (no output — clean)

python3 -m pytest groop/tests/test_aliases.py -v
# 20 passed in 0.06s

python3 -m pytest groop/tests/test_ui_table.py groop/tests/test_diag.py -v
# 19 passed in 0.34s (no regressions)

python3 -m pytest groop/tests -q
# 201 passed in 28.69s

# Controller review after adding required `rf_d` alias and canonicalizing
# configured alias columns.
/tmp/p25-venv/bin/python -m pytest groop/tests/test_aliases.py groop/tests/test_ui_table.py groop/tests/test_diag.py -q
# 39 passed in 0.31s

/tmp/p25-venv/bin/python -m pytest groop/tests -q
# 201 passed in 28.91s
```

## Known Gaps

- The alias layer covers profile/UI boundary only. Recorded JSONL and threshold config keys still use canonical names — this is by design.
- No custom profile config migration tool exists for users who already have `swap_disk`/`rf_d_per_s` in their TOML configs (they don't need one — canonical keys still work).
- Drill-down (`_metric_groups`) does not display alias names because entity_frame.metrics are always canonical keys; this is correct behavior.
- The alias layer intentionally normalizes configured profile aliases to
  canonical column names in `ProfileLayout.columns`; headers still display the
  backend-aware labels.

## Controller Merge Review

- Feature commit(s) on `feat/groop-p27-swap-refault-aliases`.
- Pre-merge validation:
  - `python3 -m pytest groop/tests/test_aliases.py -v` → `20 passed in 0.06s`
  - `python3 -m pytest groop/tests/test_ui_table.py groop/tests/test_diag.py -v` → `19 passed in 0.34s`
  - `python3 -m pytest groop/tests -q` → `201 passed in 28.69s`
  - `python3 -m py_compile ...` → clean
