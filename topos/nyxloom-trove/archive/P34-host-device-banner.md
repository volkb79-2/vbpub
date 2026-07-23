# P34 - Host Device Banner

## Goal

Add host-level per-device network and block-device rate summaries to the system
banner, closing part of `TUI-SPEC.md` §3.0:

- per-device network rates;
- per-device disk rates;
- explainable source/availability behavior.

Keep this a host-surface feature. Do not try to attribute per-device disk or
network rates to individual cgroups in this package.

## Workflow

Follow `topos/README.md` "Workflow protocol" exactly.

- Branch: `feat/topos-p34-host-device-banner`
- Worktree: `.worktrees/-topos-p34-host-device-banner`
- Branch from local `main`
- Touch only `topos/**`
- Keep `topos/handoff/reports/P34-LOG.md` updated while working
- Finish with `topos/handoff/reports/P34-REPORT.md` and a focused commit

## Required Context

Read before coding:

- `topos/README.md`
- `topos/CONTRACTS.md`
- `topos/TUI-SPEC.md` §3.0 and §6.3
- `topos/docs/STATUS.md`
- `topos/src/topos/collect/collector.py`
- `topos/src/topos/collect/host.py`
- `topos/src/topos/model.py`
- `topos/src/topos/ui/banner.py`
- `topos/tests/test_ui_banner.py`
- `topos/tests/test_host_swap.py`
- `topos/tests/conftest.py`

## Functional Requirements

Add host metadata and banner rendering for device-rate summaries.

Data collection:

- Read network interface counters from `/proc/net/dev`.
- Read block-device counters from `/sys/block/*/stat`.
- Compute rates in `Collector` using previous host-device counters and the frame
  interval. First frame may report no rates or a collecting/unavailable state.
- Store dynamic per-device data in `Frame.host_meta`, not as metric-registry
  keys. This matches the existing zram per-device drill-down pattern and avoids
  dynamic registry names.
- Suggested `host_meta` shape:

```python
{
  "net_devices": [
    {
      "name": "eth0",
      "rx_bps": 123.4,
      "tx_bps": 456.7,
      "rx_pps": 1.2,
      "tx_pps": 3.4,
      "src": "host",
    }
  ],
  "block_devices": [
    {
      "name": "nvme0n1",
      "read_bps": 123.4,
      "write_bps": 456.7,
      "read_iops": 1.2,
      "write_iops": 3.4,
      "src": "host",
    }
  ],
}
```

Banner rendering:

- Add concise `NET ...` and `DISK ...` lines near the existing host/PSI/swap
  lines.
- Show the busiest 2-3 devices by total bytes/sec.
- Keep line lengths reasonable for TUI display.
- If counters are unavailable or first-sample rates are not ready, render a
  clear degraded/collecting line rather than throwing.
- Do not count device-rate unavailability as an unprivileged field unless the
  source is genuinely permission-related.

Scope boundaries:

- Do not implement per-cgroup attribution.
- Do not add Textual-specific code outside `src/topos/ui/`.
- Do not add persistent storage.
- Do not mutate host state.

## Tests

Add focused tests covering:

- parsing `/proc/net/dev` fixture counters;
- parsing `/sys/block/*/stat` fixture counters;
- second collector sample computes host device rates from deltas;
- counter resets are handled without negative rates;
- banner renders `NET` and `DISK` lines when rates are present;
- banner renders a graceful collecting/unavailable line on first frame or
  missing fixture paths;
- frame JSON round-trip preserves `host_meta` device lists.

Use temporary fixture roots in tests. Avoid live host assertions.

## Documentation

Update:

- `topos/docs/STATUS.md` partial/implemented banner notes as appropriate.
- `topos/docs/ARCHITECTURE.md` or `topos/docs/OPERATIONS.md` only if needed to
  explain the new `host_meta` device summaries.

Do not update merge evidence in `docs/STATUS.md`; the controller does that after
review and merge.

## Out Of Scope

- Per-cgroup disk/network attribution.
- BPF network lifecycle.
- Network retransmit/loss diagnostics.
- CPU sparklines.
- New registry metric keys for each device.
