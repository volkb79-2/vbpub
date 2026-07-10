# P41 Report — Rendered Replay Fidelity

## What Was Built

Created `groop/tests/test_rendered_fidelity.py` — a deterministic multi-tick
rendered replay fidelity test suite that proves every recorded tick and its
replayed frame produce byte-identical formatted table cell values at a fixed
profile and width.

### Key Components

1. **Multi-tick frame builder** (`_build_fidelity_frames()`) — builds a
   3-tick frame sequence with Docker-metadata entities exercising all production
   formatting paths: numeric rates/bytes/percentages (`45.2%`, `256.0MiB`,
   `12.5/s`), unavailable values (`-` dim for `unavail_perm`/`unavail_kernel`),
   unlimited limits (`max` yellow), network labels (`eth0`, `net:NS`,
   `docker0:netns`), and at least one value change per tick.

2. **RecordWriter → RecordReader round-trip** — frames are serialized to JSONL
   via RecordWriter, read back via RecordReader, and compared row-key by
   row-key, column by column, cell by cell using `format_metric_value(...).plain`
   for every tick.

3. **ReplayDriver loading** — frames loaded via `ReplayDriver.from_path()` are
   compared for metadata equivalence (ts, entity keys, host keys) against
   RecordReader-loaded frames. ReplayDriver's diagnostic annotation is preserved
   as a production behavior.

4. **Compressed JSONL coverage** — when `zstandard` is installed, the same
   round-trip and cell-text identity tests run on `.jsonl.zst` files.

5. **Fixed comparison inputs** — width=140, profile="triage", sort_by="name",
   filter_text="", selected_key=None eliminate terminal layout from the
   comparison.

6. **Container-only filtering** — `render_container_table` correctly excludes
   non-Docker entities; verified by injecting a bare `system.slice` entity.

7. **Column identity validation** — column headers are checked for correct
   branch-policy suffixes (`RAM[subtree]`, `CPU%[local]`, `NET_RX[agg]`, etc.)

### Test Summary

| Test | Status |
|---|---|
| `test_fidelity_frame_sequence_round_trips` | JSONL write→read preserves all frames |
| `test_fidelity_replay_driver_round_trips` | ReplayDriver loads correct frame count and ts |
| `test_fidelity_round_trip_compressed_zst` | SKIP (no zstandard) — round-trip via compressed JSONL |
| `test_fidelity_all_ticks_produce_identical_row_keys` | Row key tuples are deterministic per tick |
| `test_fidelity_all_ticks_produce_identical_columns` | Triage profile at WIDTH=140 resolves to 12 expected columns |
| `test_fidelity_cell_text_matches_after_record_replay` | Every cell's `.plain` text identical for original vs replayed |
| `test_fidelity_driver_preserves_frame_metadata` | RecordReader vs ReplayDriver frames share ts/entity/host keys |
| `test_fidelity_rendered_containers_use_only_docker_entities` | Non-Docker entity excluded from container view |
| `test_fidelity_compressed_cell_text_matches` | SKIP (no zstandard) — cell text identical via compressed round-trip |
| `test_fidelity_specific_cell_values_are_correct` | 25 assertions spot-checking exact formatted values across 3 ticks |
| `test_fidelity_record_replay_row_key_identity` | Row keys identical for every tick after record→replay |
| `test_fidelity_column_identities_are_constant` | Column headers match expected branch-policy suffixed labels |

Results: **10 passed, 2 skipped**, 1 warning (external deprecation).

## Deviations from Handoff

None. All functional requirements are met.

- Production RecordWriter/RecordReader/ReplayDriver used (no parallel formatter).
- Cell text compared via `format_metric_value(...).plain` (public function).
- Width/profile/sort/filter fixed; terminal layout explicitly outside comparison.
- JSONL covered; compressed JSONL covered conditionally without making
  zstandard mandatory.
- Existing record/replay/schema behavior fully compatible.

## Proposed Contract Changes

None. All new code is additive in `tests/test_rendered_fidelity.py`.

## Test Evidence

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests/test_rendered_fidelity.py -v --tb=short
# 10 passed, 2 skipped (zstandard) in 0.30s
```

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# 392 passed, 2 skipped
```

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests/test_acceptance.py -q
# 40 passed
```

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m groop.acceptance tui-smoke --json
# {"ok": true, "exit_code": 0, "frames": 1, ...}
```

```bash
find groop/src groop/tests/test_rendered_fidelity.py -name '*.py' -print0 | xargs -0 python3 -m py_compile
# No errors
```

## Spec §9 Item 10 Status

| State | Before P41 | After P41 |
|---|---|---|
| RELEASE-READINESS.md | Partial (model equality only) | Pass (P41 multi-tick cell-text comparison) |
| Specification gap | No byte-for-byte rendered cell comparison | 10 focused tests proving identity |

## Files Changed

- `groop/tests/test_rendered_fidelity.py` (new, 22.9 KB)
- `groop/MEASUREMENTS.md` (updated with P41 evidence)
- `groop/docs/RELEASE-READINESS.md` (item 10 → Pass)
- `groop/docs/STATUS.md` (v1 note, item 10, quality gate)
- `groop/docs/ROADMAP.md` (P41 → done; remaining estimate)
- `groop/handoff/reports/P41-LOG.md` (new)
- `groop/handoff/reports/P41-REPORT.md` (new)
