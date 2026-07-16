# P5 Report

## What was built

- Added `textual` as a runtime dependency in `pyproject.toml`.
- Added a read-only Textual UI under `src/topos/ui/` only:
  - `ui/app.py`: injected `Frame` iterator app with fixture/replay/live support, worker-driven frame consumption, tree/container toggle, profile cycle, sort, filter, banner collapse, drill-down, glossary screen, and internal UI smoke path.
  - `ui/banner.py`: host verdict banner from frame host metrics plus TOP PRESSURE summary with `n/a` fallback before P6, and unprivileged-field notice counting `unavail_perm`.
  - `ui/table.py` / `ui/tree.py`: flat container view and hierarchical cgroup tree view, adaptive/profile column resolution using `REGISTRY` metadata in headers.
  - `ui/drill.py`: detail screen with grouped metrics, source chips, governance block, network block, findings placeholder, history sparklines, and process listing.
  - `ui/keys.py`: read-only key bindings only.
- Extended CLI wiring so:
  - `--once --json` remains on the non-Textual path.
  - `--replay FILE` routes through the same UI path when Textual is importable.
  - added hidden `--ui-smoke` for deterministic CLI smoke tests.
- Extended config parsing with additive UI-facing defaults (`default_view`, `default_column_profile`, raw `colors`/`columns`/`hotkeys` sections).
- Added tests for:
  - Textual import boundary outside `src/topos/ui/`
  - banner rendering and `unavail_perm` notice
  - Textual pilot toggle/profile/drill flows
  - replay CLI smoke through the UI path

## Deviations

- Replay transport controls from spec §3.8 are not implemented yet; replay currently works by feeding replay frames through the same UI source path, and the smoke/test path uses the hidden headless flag.
- Tree expand/collapse is not implemented yet; the tree view renders fully expanded and keeps read-only navigation/sort/filter behavior.
- The banner uses the host metrics available in current P1 frames. Per-device host disk/net lines from spec §3.0 still need collector/model support beyond the current host metric set.
- Column profile overrides currently consume configured explicit lists; custom width-tier overrides are not implemented yet.

## Proposed contract changes

- None required to the frozen frame/model/provider contracts.
- Additive config parsing was implemented inside the existing `topos.config.load()` path so UI defaults can come from config without changing frame or provider interfaces.

## Test evidence

Command:
`PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p5-ui/topos/src python3 -m pytest topos/tests -q`

Output tail:
`34 passed in 3.26s`

Command:
`PYTHONPATH=/tmp/vbpub-topos-p5-ui/topos/src python3 -m py_compile $(find topos/src/topos -name '*.py' | sort)`

Output tail:
`<no output>`

Command:
`PYTHONPATH=/tmp/vbpub-topos-p5-ui/topos/src python3 -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch`

Output tail:
`{"entities":{"":{"entity"...},"host":{...},"interval_s":5.0,"schema_version":1,"ts":...}`

Command:
`PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p5-ui/topos/src python3 -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke`

Output tail:
`ui smoke ok frames=1 view=tree profile=auto`

## Known gaps / open items

- Add replay transport controls and on-screen replay state in P7.
- Hook live mode to richer host summaries once per-device banner metrics exist in the frame schema.
- Implement tree expansion state and more complete filter UX if the UI shell is kept as the long-term front end.
- DAMON columns/profiles are placeholders until P8 supplies those metrics.
