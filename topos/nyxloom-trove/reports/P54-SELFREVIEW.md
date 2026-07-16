# P54-SELFREVIEW — Pass #1 Self-Review Findings

**Reviewer:** implementation agent (self-review pass)
**Date:** 2026-07-13
**Status:** Fixed

## Checklist Walk

### 1. Gate commands in REPORT have real output

| Gate | REPORT quotes real output? | Verdict |
|---|---|---|
| Focused tests | `56 passed in 0.99s` (now 57 in 1.20s after fix) | ✅ Real (rerun confirmed) |
| Full suite | `970 passed, 2 skipped in 122.84s` | ✅ Real (rerun confirmed: 122.15s, minor timing variation) |
| py_compile | 3 commands listed without PYTHONPATH prefix | ✅ py_compile does not need PYTHONPATH; commands are correct |
| Smoke test | `{"metrics_version":1,"profiles":[...]}` (truncated) | ✅ Real (verified by rerun) |
| git diff --check | `# clean` | ✅ Real |

**Finding:** None. All commands were actually run and output is quoted from real runs.

### 2. Scope: every file inside `topos/**`; nothing skipped

All 8 changed files are under `topos/**`:
```
topos/README.md
topos/docs/ARCHITECTURE.md
topos/docs/OPERATIONS.md
topos/handoff/reports/P54-LOG.md
topos/handoff/reports/P54-REPORT.md
topos/src/topos/cli.py
topos/src/topos/report.py
topos/tests/test_report.py
```

**Numbered requirements walk (handoff):**
1. `--json` required ✅ (`argparse required=True`, exit 2 via argparse)
2. FILE via RecordReader ✅ (`RecordReader` in `compute_report`)
3. `--window` parsing, reject malformed ✅ (`parse_window_spec`, tests for bad/empty/negative)
4. `--group-by slice|entity` ✅ (tested, slice ancestor via parent chain)
5. Fixed gauge set p50/p95/max ✅ (`REPORT_GAUGES`, `_nearest_rank_percentile`)
6. Rate derivation from raw counters ✅ (`_derive_rate`, tolerates gaps)
7. Deterministic JSON ✅ (sorted keys, 6-digit rounding)
8. Degenerate windows handled ✅ (zero/single frame → empty/null, no raise)
9. Tests: p50/p95/max, rate, window, slice, cold-vs-warm, malformed args ✅
10. README/docs updated ✅

**Amendments:**
- Nearest-rank (2026-07-12) ✅ (`_nearest_rank_percentile`, oracle test)
- Float rounding ✅ (`_round_float`, `test_float_rounding`)
- `.zst` without zstandard exit 2 ✅ (`_main_report` catches `RuntimeError("zstandard")`, `test_zst_without_zstandard_exits_2`)
- Gates with `-W error` and `timeout` ✅

**Finding:** None. All requirements covered.

### 3. Adversarial tests exist and assert observable outcomes

| Test | Observes | Hollow? |
|---|---|---|
| `test_nearest_rank_vs_interpolation_oracle` | 5 samples where nr=3.0, li=2.5, asserts nr != li | No — calls `_nearest_rank_percentile` and asserts exact value + divergence |
| `test_small_even` (p50 of [1,2,3,4] = 2) | Nearest-rank index math | No — asserts exact 2.0, not interpolation 2.5 |
| `test_p95` (p95 of [1..100] = 95) | 100-element edge case | No — asserts exact 95.0 |
| `test_deterministic_output` | `format_report` byte equality | No — asserts actual byte equality |
| `test_cli_deterministic_output` | Subprocess stdout byte equality | No — actual subprocess output compared |
| `test_float_rounding` | JSON serialization precision | No — asserts via `pytest.approx` |
| `test_zst_without_zstandard_exits_2` | Exit code 2 + "zstandard" in stderr | No — creates real .zst file, runs subprocess |
| `test_bad_window_spec_exits_2` | Exit 2 + error message | No — subprocess with real malformed arg |
| `test_warm_vs_cold_rate_parity` | Same max rate across mechanisms | No — compares derived vs live rates |

**Finding:** None. All tests assert observable outcomes. No hollow tests found.

### 4. Dates, counts, paths are real

| Item | Check | Verdict |
|---|---|---|
| LOG date: 2026-07-13 | `date -u` confirms today is 2026-07-13 | ✅ Fixed (was 2026-07-14) |
| REPORT test count: 57 | `grep -c "def test_" test_report.py` = 57 | ✅ Fixed (was 56 before .zst test added) |
| REPORT full suite: 970 passed, 2 skipped | Rerun confirmed | ✅ Real (122.15s vs 122.84s, minor timing variation) |
| Commit date: 2026-07-13 | `git log -1 --format=%ai` = 2026-07-13 | ✅ |
| File paths in LOG/REPORT | All relative, match actual files | ✅ |

### 5. LOG, REPORT present; ASCII; no dead code/scaffolding

| Check | Verdict |
|---|---|
| `P54-LOG.md` present | ✅ |
| `P54-REPORT.md` present | ✅ |
| All files ASCII only | ✅ `grep -n "[^\x00-\x7F]"` on all changed files → empty |
| No TODO/FIXME/XXX/HACK | ✅ |
| No `if __name__ == "__main__"` scaffolding | ✅ |
| No unused imports | ✅ `MetricValue` removed; `annotations` is standard boilerplate |

**Finding:** ✅ Clean.

## Summary

| # | Finding | Severity | Fixed? |
|---|---|---|---|
| 1 | LOG date was 2026-07-14 (should be 2026-07-13) | Medium | ✅ Fixed in commit 2285b4c |
| 2 | Unused `MetricValue` import in report.py | Low | ✅ Fixed in commit 2285b4c |
| 3 | Redundant `and mv.v is not None` condition in `_group_frames` | Cosmetic | ✅ Fixed in commit 2285b4c |
| 4 | Missing .zst error exit test | Medium | ✅ Added `test_zst_without_zstandard_exits_2` in commit 2285b4c |
| 5 | Test counts stale (56 → 57) in LOG/REPORT | Medium | ✅ Updated in commit 2285b4c |

All findings resolved. No remaining issues.
