# P61-REVIEW — Frontier Review Pass #2 (merge gate)

**Reviewer:** Frontier review + merge authority (Opus high), controller-workflow-v2 §6-§8
**Date:** 2026-07-13
**Verdict:** APPROVED (merged with no code changes)

## Scope / gate re-run

- Focused: `PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python -m pytest groop/tests/test_report.py -q` → `91 passed in 3.02s`.
- Full suite: `PYTHONPATH=groop/src timeout 400 /tmp/p43-clean-venv/bin/python -m pytest groop/tests -q -W error` → `1037 passed, 2 skipped in 122.79s`.
- Environment: `/tmp/p43-clean-venv` (Python 3.14, textual 8.2.8, zstandard NOT installed — matches the "report area needs no zstandard, 2 known skips" claim; pytest_asyncio NOT installed).

## Findings

| # | Finding | Severity | flagged-by-pass-1 | Disposition |
|---|---|---|---|---|
| R1 | REPORT "Known Gaps" claims `-W error` triggers a pre-existing pytest-asyncio INTERNALERROR, so the required `-W error` gate was not run green. | Info | no | Not reproduced. In the controller validation venv (no pytest_asyncio) the full suite passes clean under `-W error`. The claimed failure was an agent-environment artifact (their env carried pytest_asyncio, whose deprecation warning `-W error` promoted). Required gate is genuinely green; no code change. |
| R2 | `parse_assert_spec`: the `except ValueError` around `float(value_str)` is unreachable — `value_str` has already matched the numeric regex, so `float()` cannot raise. | Trivial | no | Left as defensive code; noted only. The `math.isinf` guard IS reachable (e.g. `1e999`), so the finite-check is not dead. |

Everything else verified clean:
- `evaluate_assertions` is a pure helper (no argparse/file I/O), consumes the already-computed `GroupProfile` list, and does NOT recompute or re-read frames — satisfies the core P54-consumer contract. Module docstring states this.
- Exit-code contract correct: 0 all-pass/none, 1 breach, 2 malformed/usage — verified via real subprocess tests asserting exact `returncode`.
- Absent-group, absent-metric, and null-STAT are all breaches (exit 1) with distinct reasons — not silent passes, not usage errors. Covered by both unit and CLI tests.
- Assertions block is sorted (group, metric, stat, op), 6-dp float-rounded, byte-deterministic across two runs (`test_byte_determinism_two_runs`), preserving the existing determinism contract. `test_no_assert_no_change` proves the profiles block is untouched when no `--assert` is given.
- No hollow tests: each named test would fail if the mechanism under test were removed. The one honest limitation (`test_null_stat_exit_1` exercises the absent-metric path because the single-frame fixture has no rates) is documented and the true null-STAT path is covered by `test_null_stat_breach` on synthetic profiles.
- Scope: all 8 files under `groop/**`. Docs (README, OPERATIONS) updated with the gate example and the new exit-1 semantics.

## flagged-by-pass-1 tally (P61)

SELFREVIEW recorded 5 mechanical findings it fixed itself (LOG dates 2026-07-18→-13, two test-count corrections, `_VALID_STATS`/`_VALID_OPS` dead code) — all mechanical, all independently catchable by pass #2 (**yes ×5**). Pass #2 found 2 net-new items pass #1 missed (R1 the un-run `-W error` gate that actually passes clean; R2 the unreachable float except) — both **no**, both non-blocking. No substantive correctness defect on either pass.
