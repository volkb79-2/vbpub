# P23 — ZRAM Per-Device Drill-Down: Implementation Report

## Summary

P23 adds structured host-level metadata (`Frame.host_meta`) for per-device ZRAM
details, serializes it through the existing frame round-trip, and renders it in
the host-memory screen. This closes the P19 drill-down gap.

## What was built

### 1. `Frame.host_meta` — additive structured metadata field

- `model.py`: Added `host_meta: dict[str, object] | None = None` to `Frame`.
- `frame_to_jsonable()`: serializes `host_meta` when non-None.
- `frame_from_jsonable()`: deserializes `host_meta`, tolerating old frames
  without the field (returns `None`).
- Follows the same additive pattern as `EntityFrame.damon` and
  `EntityFrame.governance`.

### 2. Per-device ZRAM collection (`collect/host.py`)

- `_zram_device_details(sys_root)` — per-device collection returning:
  `name`, `orig_bytes`, `compr_bytes`, `mem_used_bytes`, `mem_limit_bytes`,
  `mem_used_max_bytes`, `same_pages`, `huge_pages`, `failed_reads`,
  `failed_writes`, `writeback_bytes`, `ratio`, `efficiency`.
- `collect_host_meta(sys_root)` — returns `{"zram_devices": [...]}`.
- Graceful degradation: missing/malformed stat files produce zero/None values
  without crashing, consistent with P19 behavior.

### 3. Collector wiring (`collect/collector.py`)

- Added `sys_root` parameter to `Collector.__init__` (default `Path("/sys")`).
- `collect_once()` now calls `collect_host_meta(sys_root=self.sys_root)` and
  passes it to the `Frame` constructor.

### 4. Host-memory rendering (`ui/hostmem.py`)

- `render_host_memory_text()` now appends a `ZRAM DEVICES` section.
- Columnar table: device name, orig, compr, mem_used, ratio, failed reads,
  failed writes, writeback bytes.
- No-device state: `(no zram devices)` when none are present.
- Attribution caveat: `per-cgroup zram compression/cost attribution is
  unavailable in the kernel.`
- Uses `_fmt_bytes()` for human-readable byte rendering (shared with paddr).

### 5. Documentation

- `docs/COMPRESSED-SWAP.md`: marked per-device drill-down as implemented.
- `docs/STATUS.md`: compressed-swap no longer in Partially Implemented gap
  status; removed from Not Implemented; v1.5 bumped to 90-95%.
- `docs/ROADMAP.md`: P23 changed from "planned" to "done".
- `README.md`: work packages table P23 marked "Done".

### 6. Tests (`tests/test_p23_zram_drilldown.py`)

14 focused tests covering:

| Test | What it verifies |
|---|---|
| `test_frame_serialization_round_trip_with_zram_metadata` | Frame with two zram devices round-trips through JSON |
| `test_frame_serialization_old_frame_compat` | Dict without host_meta deserializes cleanly |
| `test_frame_serialization_old_frame_compat_with_entities` | Old frame with entities deserializes cleanly |
| `test_host_memory_text_renders_zram_devices` | Rendered text contains device names, bytes, ratio, IO errors, caveat |
| `test_host_memory_text_renders_no_zram_devices` | No-device line shown when host_meta=None |
| `test_host_memory_text_renders_no_zram_devices_empty_list` | No-device line shown when devices list empty |
| `test_host_memory_text_handles_missing_host_meta_key` | No-device line shown when zram_devices key absent |
| `test_host_memory_text_handles_malformed_replay_metadata` | Malformed replay metadata does not crash rendering |
| `test_collect_host_meta_with_devices` | Two devices collected with correct fields |
| `test_collect_host_meta_malformed_stats` | Malformed stat files produce zero/None values, not crashes |
| `test_collect_host_meta_no_zram_devices` | Empty list when /sys/block has no zram* dirs |
| `test_collect_host_aggregate_metrics_unchanged` | P19 aggregate metrics are unchanged by host_meta addition |
| `test_collector_default_host_uses_configured_sys_root` | Collector `sys_root` feeds aggregate host metrics and host metadata |
| `test_zram_device_lines_ratio_none_on_zero_compr` | Ratio is None when compr=0; efficiency is 0.0 |

### 7. Updated golden fixture

`tests/fixtures/frames/gstammtisch-once.jsonl` regenerated to include the
`host_meta` field (with empty zram_devices for the gstammtisch fixture).

## Deviations from handoff

None. All scope items implemented as specified.

## Contract changes

`CONTRACTS.md` now documents the additive `Frame.host_meta` field for
host-level non-metric details. Existing frames without `host_meta` still read
cleanly, and `Frame.host` remains strictly registry-backed.

## Test evidence

```bash
$ /tmp/vbpub-topos-p17-venv/bin/python -m pytest topos/tests -q
161 passed in 24.83s after merge

$ python3 -m py_compile topos/src/topos/model.py topos/src/topos/collect/host.py topos/src/topos/collect/collector.py topos/src/topos/ui/hostmem.py topos/tests/test_p23_zram_drilldown.py
# clean

$ PYTHONPATH=topos/src /tmp/vbpub-topos-p17-venv/bin/python -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# schema 1; host_meta keys ["zram_devices"]; zram_devices 0; entities 8
```

## Merge evidence

P23 merged to `main` with:

```bash
git merge --no-ff feat/topos-p23-zram-device-drilldown -m "Merge topos P23 zram device drilldown"
```

Post-merge validation from the main checkout: py_compile clean, full suite
`161 passed in 24.83s`, and fixture JSON smoke reported schema 1 with
`host_meta["zram_devices"]`.

## Known gaps / open items

1. No zram devices exist on this test host — the drill-down rendering was
   verified via unit tests with synthetic data. A live host with real zram
   devices should visually confirm the rendered table in the TUI.
2. The `host_meta` field is serialized as-is into JSON (it carries device
   detail dicts). This is fine for recording/replay but consumes more space
   per frame than a purely numeric schema. If the frame rate is high and many
   zram devices exist, consider whether the metadata should be recorded
   less frequently. At 5s intervals with a handful of devices, the overhead
   is negligible.
3. No daemon protocol version bump was needed — the additive field flows
   through the existing frame serializer without schema changes.
