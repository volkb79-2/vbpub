# P3 Report

## What Was Built

- Added `topos.providers` with the frozen P3 provider contract in
  `src/topos/providers/base.py`.
- Implemented `src/topos/providers/net_host.py`:
  - parses `/proc/net/dev` for host rx/tx bytes and packets;
  - parses `/proc/net/softnet_stat` for dropped/time_squeeze totals;
  - parses `/proc/net/snmp` and `/proc/net/netstat` for TCP/UDP health;
  - parses `tc -s qdisc show` through an injectable runner;
  - exposes root-host pseudo-samples as `net:HOST` and structured status.
- Implemented `src/topos/providers/net_netns.py`:
  - reads entity `cgroup.procs`;
  - resolves `/proc/<pid>/ns/net` inodes from an injectable proc root;
  - reads `/proc/<pid>/net/dev` counters from representative private netns PIDs;
  - dedupes same-netns PIDs;
  - labels host-netns entities `net:N/A` with reason `host netns`;
  - refuses ambiguous shared-private-netns attribution;
  - aggregates branch rows only when every contributing child is proven distinct
    private netns.
- Extended `Collector` to accept network providers and run their cumulative
  counters through the existing P1 reset/rate flow.
- Updated the network registry entries so `net_rx_bps` / `net_tx_bps` are now
  live provider-backed metrics rather than placeholders.
- Added config support for observe-only `[net.classes]` port metadata in
  `src/topos/config.py`.
- Added fixture-backed tests and updated the golden once-frame fixture.

## Deviations From Handoff

- `[net.classes]` is parsed and queryable from config, but not attached to
  `NetSample` or frame metrics. The current frozen contracts do not provide a
  field for observe-only network-class metadata.
- Controller review added `EntityFrame.network` metadata so recordings/replay
  retain `net:HOST` / `net:NS` / `net:N/A`, confidence, aggregation, reason, and
  protocol detail alongside the numeric `MetricValue` rates.
- Branch aggregation is intentionally conservative when a branch cgroup has its
  own direct processes. I only aggregate pure child-derived branches because the
  current frame model has no explicit place to document mixed direct+child proof.

## Proposed Contract Changes

1. Add an optional observe-only metadata slot to `NetSample` for future traffic
   class explanations sourced from `[net.classes]`.

## Validation Evidence

```text
$ PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p3-network/topos/src python3 -m pytest topos/tests -q
25 passed in 2.07s
```

```text
$ PYTHONPATH=/tmp/vbpub-topos-p3-network/topos/src python3 -m py_compile $(find topos/src/topos -name '*.py' | sort)
# exit 0, no output
```

```text
$ PYTHONPATH=/tmp/vbpub-topos-p3-network/topos/src python3 -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
{"schema_version":1,...,"entities":{"":{...,"metrics":{"net_rx_bps":[null,"host",...],"net_tx_bps":[null,"host",...]}}...}}
```

## Known Gaps / Open Items

- No BPF provider work in this package.
- No drill-down/network UI consumption yet; P5/P7 still need to surface provider
  status and the richer host/interface details.
- Host CLI runs against the real `/proc` even when `--cgroup-root` points at a
  fixture tree, so ad-hoc `--once --json` output is intentionally host-specific
  for the network fields unless tests inject fixture providers.
