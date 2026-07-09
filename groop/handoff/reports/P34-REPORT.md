# P34 - Host Device Banner - Report

## What was built

Add host-level per-device network and block-device rate summaries to the system
banner via `host_meta`, closing part of `TUI-SPEC.md` §3.0.

### Data collection (`groop/src/groop/collect/host.py`)

- **`_net_dev_counters(proc_root)`** - parses `/proc/net/dev` for per-interface
  byte/packet counters. Excludes interfaces matching `veth*`, `br-*`, `docker*`,
  `lo` by default. Returns a list of dicts with `name`, `rx_bytes`, `tx_bytes`,
  `rx_packets`, `tx_packets`, `src="host"`.
- **`_block_dev_counters(sys_root)`** - parses `/sys/block/*/stat` for per-device
  I/O counters. Excludes `loop*`, `ram*`, `zram*` by default. Returns a list of
  dicts with `name`, `rd_ios`, `rd_sectors`, `wr_ios`, `wr_sectors`, `src="host"`.
- **`collect_host_meta()`** updated to include `"net_device_counters"` and
  `"block_device_counters"` alongside existing `"zram_devices"`.

### Rate computation (`groop/src/groop/collect/collector.py`)

- **`_prev_device_counters`** field on `Collector` stores previous frame's raw
  device counters.
- **`_apply_host_device_rates(host_meta, interval_s)`** - computes rates from
  raw counter deltas. Replaces raw `"net_device_counters"` with rate
  `"net_devices"` (rx_bps, tx_bps, rx_pps, tx_pps) and `"block_device_counters"`
  with `"block_devices"` (read_bps, write_bps, read_iops, write_iops). Rates are
  `None` (collecting state) on the first frame and for new devices. Counter
  regression (reset) produces zero rates, not negative.
- `collect_once()` now calls `collect_host_meta(proc_root=proc_root, sys_root=sys_root)`
  and then `_apply_host_device_rates()`.

### Banner rendering (`groop/src/groop/ui/banner.py`)

- **`_host_device_lines(frame)`** - returns `NET` and `DISK` lines for the banner.
- **`_net_device_line(devices)`** - shows 2-3 busiest interfaces by total bytes/s,
  with byte rate and pps for each. Renders `"NET collecting..."` on first sample,
  `"NET n/a"` when absent.
- **`_block_device_line(devices)`** - shows 2-3 busiest block devices by total
  bytes/s, with byte rate and IOPS for each. Renders `"DISK collecting..."` on
  first sample, `"DISK n/a"` when absent.
- Lines are inserted after the SWAP backend line and before the DAMON heat line
  (if present) and TOP PRESSURE section.

## Deviations from handoff doc

None. Implementation follows the handoff spec precisely.

## Proposed contract changes

None. All data is additive `host_meta` only - no new registry metrics, no frame
schema changes.

## Test evidence

### Focused host device tests (14 passed)

```text
groop/tests/test_host_device.py::test_net_dev_counters_parses_fixture PASSED
groop/tests/test_host_device.py::test_net_dev_counters_empty_on_unreadable PASSED
groop/tests/test_host_device.py::test_net_dev_counters_skips_excluded_interfaces PASSED
groop/tests/test_host_device.py::test_net_dev_counters_shows_non_excluded_interface PASSED
groop/tests/test_host_device.py::test_block_dev_counters_parses_fixture PASSED
groop/tests/test_host_device.py::test_block_dev_counters_excludes_loop_ram_zram PASSED
groop/tests/test_host_device.py::test_block_dev_counters_empty_on_no_block_dir PASSED
groop/tests/test_host_device.py::test_net_dev_counters_malformed_line_skipped PASSED
groop/tests/test_host_device.py::test_block_dev_counters_short_stat_skipped PASSED
groop/tests/test_host_device.py::test_collect_host_meta_includes_device_counters PASSED
groop/tests/test_host_device.py::test_apply_host_device_rates_first_sample_none PASSED
groop/tests/test_host_device.py::test_apply_host_device_rates_second_sample_computes_rates PASSED
groop/tests/test_host_device.py::test_apply_host_device_rates_counter_reset_handled PASSED
groop/tests/test_host_device.py::test_apply_host_device_rates_new_device_none PASSED
```

### Focused banner tests (9 passed)

```text
groop/tests/test_ui_banner.py::test_banner_snapshot_renders_golden_fixture_summary PASSED
groop/tests/test_ui_banner.py::test_banner_counts_unavailable_permissions_and_shows_notice PASSED
groop/tests/test_ui_banner.py::test_banner_renders_swap_backend_line PASSED
groop/tests/test_ui_banner.py::test_banner_renders_net_and_disk_lines PASSED
groop/tests/test_ui_banner.py::test_banner_renders_collecting_line_on_first_sample PASSED
groop/tests/test_ui_banner.py::test_banner_renders_n_a_when_no_host_meta PASSED
groop/tests/test_ui_banner.py::test_banner_ignores_malformed_host_meta_device_entries PASSED
groop/tests/test_ui_banner.py::test_banner_shows_busiest_two_devices PASSED
groop/tests/test_ui_banner.py::test_frame_round_trip_preserves_net_and_block_devices PASSED
```

### Full suite (323 passed)

```bash
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
# 323 passed in 37.21s
```

### py_compile clean on all changed files

```bash
/tmp/p25-venv/bin/python -m py_compile \
  groop/src/groop/collect/host.py \
  groop/src/groop/collect/collector.py \
  groop/src/groop/ui/banner.py \
  groop/tests/test_host_device.py \
  groop/tests/test_ui_banner.py \
  groop/tests/test_p23_zram_drilldown.py
# exit 0
```

## Known gaps / open items

- **CPU breakdown sparklines** from spec §3.0 remain unimplemented (scope
  boundary of P34).
- **`[banner] net_device_exclude` / `[banner] disk_device_exclude` config keys**
  are not yet plumbed through `GroopConfig`; the exclude prefixes are hardcoded
  module-level tuples in `host.py`. Adding TOML config support is a straightforward
  follow-up.
- **Golden fixture topology:** the reference frame uses only fixture-owned
  network data (`eth0`) and an isolated empty sysroot for block devices, so UI
  golden tests do not depend on the controller host's block/network topology.

## Files changed

| File | Change |
|---|---|
| `groop/src/groop/collect/host.py` | Added `_net_dev_counters()`, `_block_dev_counters()`, updated `collect_host_meta()` |
| `groop/src/groop/collect/collector.py` | Added `_prev_device_counters`, `_apply_host_device_rates()`, updated `collect_once()` |
| `groop/src/groop/ui/banner.py` | Added `_host_device_lines()`, `_net_device_line()`, `_block_device_line()`, `_fmt_float_pps()` |
| `groop/tests/test_host_device.py` | New: 14 tests for parsing, rates, resets |
| `groop/tests/test_ui_banner.py` | Added 7 tests: NET/DISK rendering, collecting, malformed host_meta, round-trip |
| `groop/tests/test_collector.py` | Isolated golden collector fixture from the controller host sysfs |
| `groop/tests/test_p23_zram_drilldown.py` | Updated 2 tests for new host_meta shape |
| `groop/tests/fixtures/frames/gstammtisch-once.jsonl` | Updated host_meta shape with deterministic fixture-owned net/block device entries |
| `groop/docs/STATUS.md` | Updated system banner entry |
| `groop/handoff/reports/P34-LOG.md` | Work log |
| `groop/handoff/reports/P34-REPORT.md` | This report |
