# P56-REVIEW — Frontier Review Pass #2 (merge gate)

**Reviewer:** Frontier review + merge authority (Opus high), controller-workflow-v2 §6-§8
**Date:** 2026-07-13
**Verdict:** APPROVED (merged after review fixes)

## Findings

| # | Finding | Severity | flagged-by-pass-1 | Disposition |
|---|---|---|---|---|
| R1 | `topos/README.md` not updated — still listed P56 as **Queued**, and the handoff explicitly requires "Update README.md quickstart/CLI docs". README was absent from the diff entirely. | Medium | no | Fixed: flipped status row to **Done** (+ Report link) and added a `topos squeeze` quickstart line. |
| R2 | Hollow signal-safety test. `test_sigint_restores_memory_high` only exercised `__exit__` (`with guard: pass`); `test_restore_guard_signals_installed` only asserted handlers were registered. The mandated "simulate SIGINT mid-loop and assert the restore write happened" path — `_restore_on_signal` → `restore()` → `raise KeyboardInterrupt` — was never invoked. | Medium | no | Fixed: added `test_sigint_handler_restores_then_raises`, which captures the installed SIGINT handler via the injectable seam, fires it, and asserts (a) `KeyboardInterrupt` is raised and (b) `("memory.high","max")` was written — an observable restore. |
| R3 | Dead code the self-review's cleanup pass missed: unused `parse_size` import in `parse_squeeze_args` (cli.py) and write-only local `restored` in `run_squeeze` (squeeze.py). | Minor | yes (self-review claimed dead-code cleanup complete; both residuals independently caught) | Fixed: removed both. |
| R4 | P46 gate primitives re-implemented inline (`config.admin`, `config.confirm != "SQUEEZE"`, `os.geteuid()`) rather than reused, contrary to the handoff's "reuse those gate primitives ... citing the exact functions reused." | Minor | no | Accepted as deviation. `actions/execute.py` does not expose a single importable gate helper — it too performs the root check inline (`is_root = root_check() if ... else os.geteuid() == 0`), so there is no clean primitive to import. The gate is behaviorally equivalent and its refusal paths are covered by observable tests (gate-refusal asserts the typed error, not mock bookkeeping). Not a merge blocker; noted for a future gate-helper extraction across P46/P56. |
| R5 | JSONL log is hand-rolled `{"type":...}` rather than demonstrably `RecordWriter`-schema-compatible, with no `.zst` path and no replay/report consumption test; divergence not documented as deliberate. | Minor | no | Accepted. The handoff permits "define the minimal compatible envelope ... and document the deliberate divergence." The log is a self-describing header/step/summary audit stream, not a frame recording; documenting the divergence here suffices for v1. Noted as follow-up if a replay consumer is ever needed. |

Verified clean / genuinely good (independently reproduced): 32 squeeze tests pass; scope respected (all files under `topos/**`); stop-condition, memory.min-refusal/`--force`, gate-refusal, and log-shape tests assert observable outcomes (stop_reason, error text, on-disk JSONL); `test_no_subprocess_import` is a real AST assertion. Em-dashes appear in a couple of source strings but are consistent with existing topos docs — left as-is.

## Gate re-run (after fixes)

- Focused: `PYTHONPATH=topos/src python3 -m pytest topos/tests/test_squeeze.py -q` → `32 passed`.
- Full suite: recorded in the merge-evidence commit (below).
- Environment: system venv `/home/vscode/.venv` (Python 3.14, textual 8.2.8).

## flagged-by-pass-1 tally (P56)

SELFREVIEW findings: gates-real (yes), scope (yes), hollow-test audit (NO — self-review graded the signal test PASS; pass #2 found it hollow), dates/counts (yes), dead-code-cleanup (yes for listed items, but pass #2 found residuals it missed). Net: findings on gates/scope/dates caught by both; the untested signal path, the README omission, and the P46 gate re-implementation are net-new pass #2 findings (3 net-new; ~40% overlap on the substantive items).
