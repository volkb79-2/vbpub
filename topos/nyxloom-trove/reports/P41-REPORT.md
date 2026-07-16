# P41 Report - Rendered Replay Fidelity

## Outcome

P41 closes `TUI-SPEC.md` section 9 item 10 with a deterministic production-path
test. Three changing, diagnostics-annotated container frames exercise numeric
bytes/rates/percentages, unavailable and unlimited values, network labels,
selection markers, row identity, and sort order.

The frames are written with `RecordWriter`, loaded and played with
`ReplayDriver.play(step=True)`, and compared at a fixed width, profile, sort,
filter, and selection. Row keys, column identities, and every plain formatted
cell must be identical for every tick. Terminal wrapping and ANSI layout remain
outside this gate as required by the specification.

## Production Test Seam

`snapshot_container_table` exposes deterministic pre-layout content. It calls
the same profile resolution, visibility filtering, sorting, and `_row_cells`
formatter used by `render_container_table`; it is not a parallel formatter.

JSONL always runs. The same parametrized assertion covers compressed JSONL when
the optional zstandard dependency is installed.

## Controller Correction

The initial agent result compared direct `RecordReader` output with separately
formatted metrics and checked `ReplayDriver` only for metadata. Controller
review replaced it because it did not prove exact replay-rendered cell identity
and omitted row-cell decorations. The final test follows the live annotation
order and the real replay driver and rendered-row paths.

## Validation

```text
focused fidelity:       1 passed, 1 skipped in 0.27s
table/record/fidelity: 19 passed, 1 skipped in 9.57s
full suite:           383 passed, 1 skipped in 46.81s (post-merge)
acceptance:            40 passed in 7.26s (post-merge)
TUI smoke:             exit 0, ok=true, frames=1, max RSS=48056 KB
py_compile:            clean
```

The skip is only the optional compressed JSONL case because zstandard is not
installed in the managed environment.

## Files

- `topos/src/topos/ui/table.py`
- `topos/tests/test_rendered_fidelity.py`
- `topos/README.md`
- `topos/docs/ROADMAP.md`
- `topos/docs/STATUS.md`
- `topos/docs/RELEASE-READINESS.md`
- `topos/MEASUREMENTS.md`
