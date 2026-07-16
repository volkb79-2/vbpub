# P28 - I/O Cap Saturation Metric

**Cut:** v1 diagnostics polish. **Depends:** P6, P27. Branch:
`feat/topos-p28-io-cap-saturation`. Follow `topos/README.md` workflow protocol
exactly.

## Goal

Make the existing dormant diagnostics input `io_cap_saturation_pct` real. When
a cgroup has finite `io.max` limits, topos should estimate how much of the
configured I/O budget is currently in use and expose that as a normal metric,
without changing host state or requiring privileged operations.

## Required Context

- `topos/README.md` workflow protocol.
- `topos/handoff/P6-diagnostics.md` and `topos/handoff/reports/P6-REPORT.md`.
- `topos/src/topos/collect/cgroup.py` for `io.max` and `io.stat` parsing.
- `topos/src/topos/collect/collector.py` for rate derivation.
- `topos/src/topos/registry.py`, `topos/src/topos/ui/table.py`,
  `topos/src/topos/diag/score.py`, `topos/src/topos/diag/rules.py`.
- `topos/tests/test_collector.py`, `topos/tests/test_diag.py`,
  `topos/tests/test_ui_table.py`.
- `topos/handoff/AGENT-LOG-TEMPLATE.md`.

## Scope - In

1. Parse finite `io.max` per-device caps for these kernel fields:
   - `rbps`, `wbps`, `riops`, `wiops`;
   - ignore `max` values and malformed tokens gracefully.
2. Derive `io_cap_saturation_pct` after rate computation:
   - compare `io_r_bps` to summed finite `rbps` caps;
   - compare `io_w_bps` to summed finite `wbps` caps;
   - compare `io_r_iops` to summed finite `riops` caps;
   - compare `io_w_iops` to summed finite `wiops` caps;
   - use the highest available ratio times 100;
   - clamp only the lower bound at 0; values above 100 are useful and should
     remain visible if counters exceed configured cap in a sample.
3. Degrade cleanly:
   - if `io.max` is missing/unreadable, source should reflect that;
   - if `io.max` has no finite caps, source should be `unlimited` or a clear
     non-alerting unavailable state;
   - if rates are unavailable or no matching cap exists, do not fabricate zero.
4. Add registry/table/profile support:
   - `REGISTRY["io_cap_saturation_pct"]`;
   - table header such as `IO_CAP%`;
   - include it in a sensible profile or width tier only if it does not crowd
     existing views. A focused table test is enough if not adding it to defaults.
5. Diagnostics:
   - `score.py` already has an `io_cap_saturation_pct` input. Keep or adjust the
     label/detail as needed.
   - Add a rule only if it is simple and non-noisy, for example an info/warn
     finding when saturation is high and `io.max` is capped. Avoid duplicating
     the existing PSI-plus-cap finding unless the message adds value.
6. Add focused tests proving:
   - `io.max` parsing accepts finite and `max` fields;
   - saturation is computed on a second sample from fixture counters;
   - unlimited/missing caps do not fabricate zero;
   - pressure breakdown includes a contribution when saturation exceeds the
     configured band;
   - table formatting/header is sane.
7. Update docs after implementation:
   - `README.md` P28 row should become Done;
   - `docs/ROADMAP.md` and `docs/STATUS.md` should remove or narrow the
     diagnostics gap;
   - `CONTRACTS.md` or `MEASUREMENTS.md` only if a real contract/evidence note
     changes.

## Scope - Out

- No host mutation, root operations, or live benchmark runs.
- No attempt to infer underlying disk hardware saturation.
- No network loss/retransmit work; keep that as the remaining diagnostics input
  gap unless directly touched by tests.
- No changes outside `topos/**`.

## Design Notes

- Prefer structured parsing helpers over ad hoc string checks in the collector
  path.
- The cgroup v2 `io.max` file is per-device; summing finite caps across devices
  is the least surprising read-only estimate for subtree rows.
- Keep `io_max_capped` as the existing boolean. `io_cap_saturation_pct` is a
  separate percent metric.
- If the first sample cannot have rates yet, the saturation metric should be
  unavailable until the second sample has deltas.

## Acceptance

- Full suite passes:

```bash
python3 -m pytest topos/tests -q
```

- Compile check passes for changed Python files.
- Focused tests cover parser, derived metric, diagnostics, and table display.
- `topos/handoff/reports/P28-LOG.md` and
  `topos/handoff/reports/P28-REPORT.md` are written and current.

## Handoff Requirements

- Keep `topos/handoff/reports/P28-LOG.md` current using
  `topos/handoff/AGENT-LOG-TEMPLATE.md`.
- Write `topos/handoff/reports/P28-REPORT.md` with implementation summary,
  deviations, tests, known gaps, and contract-change proposals.
- Commit the feature branch with a focused message.
