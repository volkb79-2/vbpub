# P54-REVIEW — Frontier Review Pass #2 (merge gate)

**Reviewer:** Frontier review + merge authority (Opus high), controller-workflow-v2 §6-§8
**Date:** 2026-07-13
**Verdict:** APPROVED (merged after one review fix)

## Scope / gate re-run

- Focused: `PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -W error::RuntimeWarning` → `57 passed`.
- Full suite: `PYTHONPATH=groop/src timeout 300 python3 -m pytest groop/tests/ -q -p no:asyncio -p no:schemathesis -W error` → `971 passed, 2 skipped in 122.60s` (the 2 skips require the `zstandard` extra, absent in this env — same baseline as P53).
- Environment: system venv `/home/vscode/.venv` (Python 3.14, textual 8.2.8, zstandard not installed).

## Findings

| # | Finding | Severity | flagged-by-pass-1 | Disposition |
|---|---|---|---|---|
| R1 | `test_live_v_used_as_is` was a hollow test (bare `pass`, asserted nothing). | Minor | no | Fixed in commit 3da67fc — now asserts `_group_frames` uses a live `v` verbatim rather than re-deriving from the raw counter. |

Everything else verified clean:
- Nearest-rank percentile pinned per the 2026-07-12 amendment, with a genuine divergence oracle (`test_nearest_rank_vs_interpolation_oracle`) that would fail under linear interpolation.
- Float determinism (6-dp rounding) + sorted keys, asserted via real byte-equality (`test_cli_deterministic_output`).
- `.zst`-without-zstandard exit-2 path tested via a real subprocess with zstd magic bytes.
- Rate derivation tolerates gaps/churn/resets; cold-vs-warm parity asserted on observable rate samples.
- Reuses `RecordReader`/parent-chain ancestry — no new file parsing, no cgroup-path reimplementation. Scope: all 8 files under `groop/**`.

## flagged-by-pass-1 tally (P54)

All five SELFREVIEW findings were mechanical (date, unused import, redundant condition, missing .zst test, stale counts) — a rigorous pass #2 would independently catch all five (**yes ×5**). Pass #2 additionally found one item the self-review missed: the hollow `test_live_v_used_as_is` (**R1, no**). Its own adversarial-test table listed that test's neighbours but skipped the empty one.

Overlap: 5/6 findings would be caught by both passes; pass #2 found 1 net-new.
