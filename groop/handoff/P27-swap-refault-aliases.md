# P27 - Swap/Refault Terminology Aliases

**Cut:** v1.5 polish. **Depends:** P19, P23. Branch:
`feat/groop-p27-swap-refault-aliases`. Follow `groop/README.md` workflow
protocol exactly.

## Goal

Preserve the existing frame/model metric keys (`swap_disk`, `rf_d_per_s`) while
making user-facing TUI/profile terminology backend-aware. Operators should be
able to use clearer aliases such as `swap_dev` and `rf_dev_per_s` in configured
profiles, while existing profiles and recorded frames continue to work.

## Required Context

- `groop/README.md` workflow protocol.
- `groop/docs/COMPRESSED-SWAP.md` policy, especially "Per-Cgroup Boundary" and
  "Refault Wording".
- `groop/handoff/P19-zram-swap-backend-awareness.md` and
  `groop/handoff/reports/P19-REPORT.md`.
- `groop/handoff/P23-zram-device-drilldown.md` and
  `groop/handoff/reports/P23-REPORT.md`.
- `groop/src/groop/ui/table.py`, `groop/src/groop/ui/drill.py`,
  `groop/src/groop/diag/score.py`, `groop/src/groop/diag/rules.py`.
- `groop/tests/test_ui_table.py`, `groop/tests/test_diag.py`, and relevant UI
  smoke tests.
- `groop/handoff/AGENT-LOG-TEMPLATE.md`.

## Scope - In

1. Add a small alias layer for user/profile-facing column names:
   - `swap_dev` resolves to canonical metric key `swap_disk`;
   - `rf_dev_per_s` and `rf_dev` resolve to canonical metric key
     `rf_d_per_s`;
   - existing `swap_disk`, `rf_d_per_s`, and `rf_d` behavior must keep working.
2. Prefer clearer table labels:
   - `swap_disk`/`swap_dev` should display as a short backend-aware label such
     as `SWAP_DEV`, not a physical-disk-only claim;
   - `rf_d_per_s`/aliases should display as `RF_DEV/S` or equivalent.
3. Preserve canonical frame/model/registry keys. Do **not** rename serialized
   metrics, history keys, thresholds, or diagnostic config keys in this package.
4. Make drill-down and pressure/finding wording avoid claiming physical disk on
   zram/mixed hosts. It is acceptable to say "non-zswap swap-device" or
   "backend may be disk, zram, or mixed according to host classification".
5. Add tests proving:
   - configured profiles using aliases resolve to canonical columns and do not
     appear as ignored columns;
   - legacy configured names still resolve;
   - table headers use backend-aware labels;
   - drill-down and diagnostic text no longer overclaim physical disk for
     `rf_d_per_s`/`swap_disk`.
6. Update docs after implementation:
   - `README.md` P27 row should become Done;
   - `docs/ROADMAP.md` P19/P27 text should remove the alias gap;
   - `docs/COMPRESSED-SWAP.md` should document the accepted aliases and
     canonical metric keys;
   - `docs/STATUS.md` should refresh snapshot/alias state and quality-gate
     evidence.

## Scope - Out

- No collector/model/schema metric rename.
- No migration of recorded JSONL fixtures unless a test truly requires it.
- No threshold/config-key rename; `rf_d_per_s` remains the threshold key.
- No root operations, host mutations, or live ZRAM/ZSWAP probing.
- No changes outside `groop/**`.

## Design Notes

- Keep alias handling centralized, ideally in `ui/table.py` or a tiny helper
  imported by table/drill code. Avoid scattered ad hoc string replacement.
- Distinguish "canonical key" from "display label". The canonical key should be
  what indexes `EntityFrame.metrics`, `REGISTRY`, history, and thresholds.
- If adding alias metadata to `registry.py`, keep the existing `REGISTRY`
  invariant `name == spec.name` intact.
- The strongest wording is backend-aware, not vague: "non-zswap swap-device"
  is better than "disk" when the kernel cannot attribute a cgroup to zram vs
  disk on mixed hosts.

## Acceptance

- Full suite passes:

```bash
python3 -m pytest groop/tests -q
```

- Compile check passes for changed Python files.
- Focused tests cover alias resolution and user-facing wording.
- `groop/handoff/reports/P27-LOG.md` and
  `groop/handoff/reports/P27-REPORT.md` are written and current.

## Handoff Requirements

- Keep `groop/handoff/reports/P27-LOG.md` current using
  `groop/handoff/AGENT-LOG-TEMPLATE.md`.
- Write `groop/handoff/reports/P27-REPORT.md` with implementation summary,
  deviations, tests, known gaps, and contract-change proposals.
- Commit the feature branch with a focused message.
