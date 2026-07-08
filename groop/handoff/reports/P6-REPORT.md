# P6 Report

## What Was Built

- Added `groop/src/groop/diag/` with:
  - `score.py`: deterministic pressure scoring with shipped defaults, tier-aware threshold normalization, and per-input breakdown generation.
  - `rules.py`: the eight v1 diagnostics rules as data-driven rule objects.
  - `__init__.py`: public `annotate(frame, config, ...)` entry point.
- Integrated diagnostics into live collection after network + governance annotation.
- Integrated replay backfill so recordings missing diagnostics get `pressure` and findings recomputed, while pre-existing findings are preserved.
- Updated the collector/registry to emit the real supporting metrics that are representable today:
  - `sock`
  - `net_rx_pps`
  - `net_tx_pps`
  - `io_max_capped`
- Updated the drill-down to show a pressure contribution breakdown without changing the frozen frame contract.
- Regenerated `groop/tests/fixtures/frames/gstammtisch-once.jsonl` with diagnostics included.

## Deviations / Gaps

- I did **not** add a serialized `diagnostics` metadata block to `EntityFrame`. That would have been a contract change. The drill-down recomputes the pressure breakdown from the current frame instead.
- Two optional score inputs from the spec are still unavailable in emitted frames:
  - `io_cap_saturation_pct`
  - attributable network drops / retransmits (`network_loss_pct`)
- Because those metrics do not exist in frames today, their score weights ship as `0.0` and the breakdown reports them as unavailable rather than inventing data.

## Proposed Contract Changes

- None required for P6 delivery.
- A future additive `EntityFrame.diagnostics` metadata block would let replay/JSON carry the exact breakdown instead of recomputing it in the UI.

## Validation

1. `PYTHONPATH=/tmp/groop-pytest:/tmp/vbpub-groop-p6-diagnostics/groop/src python3 -m pytest groop/tests -q`
   - Tail: `49 passed in 3.50s`

2. `PYTHONPATH=/tmp/vbpub-groop-p6-diagnostics/groop/src python3 -m py_compile $(find groop/src/groop -name '*.py' | sort)`
   - Tail: no output, exit 0

3. `PYTHONPATH=/tmp/vbpub-groop-p6-diagnostics/groop/src python3 -m groop.cli --once --json --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch`
   - Parsed evidence:
     - `/ pressure=[1, 'derived'] findings=[]`
     - `system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope pressure=[1, 'derived'] findings=['governance_drift']`
     - `soulmask.slice/soulmask-paks.slice pressure=[0, 'derived'] findings=['governance_drift']`

4. `PYTHONPATH=/tmp/groop-pytest:/tmp/vbpub-groop-p6-diagnostics/groop/src python3 -m groop.cli --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke`
   - Tail: `ui smoke ok frames=1 view=tree profile=auto`

## Known Open Items

- Rule 5 is implemented with `io_max_capped`, but not a true saturation percentage yet.
- Score input coverage for attributable network loss/retransmit remains deferred until those counters are emitted as real frame metrics.
