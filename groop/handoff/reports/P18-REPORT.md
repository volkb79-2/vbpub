# P18 REPORT - Exact BPF Network Provider Read Side

**Branch:** `feat/groop-p18-bpf-provider`
**Base:** `6a9ebab` (docs(groop): record P21 merge evidence)
**Date:** 2026-07-09

## What was built

### `groop/src/groop/providers/net_bpf.py` - `BpfProvider`

A new network provider implementing the `Provider` protocol from
`CONTRACTS.md`. Key characteristics:

1. **Userspace-only snapshot reader.** Reads structured JSON snapshots
   representing pinned BPF `cgroup_skb` map state from
   `<bpf_root>/snapshot.json`. No kernel BPF operations, no root, no
   `bpftool`, no `cgroup_skb` program load or attach.

2. **Map format.** The snapshot contains:
   - `maps["groop_cgroup_skb"]`: list of entries with `cgroup_id` (int),
     `direction` ("ingress"/"egress"), `family`, `proto`, `bytes`, `packets`.
   - `cgroup_map`: `{str(cgroup_id): entity_key}` - the userspace cgroup-id
     to entity path mapping, built by the daemon/helper that walks the cgroup
     tree.

3. **Per-cgroup aggregation.** During `collect()`:
   - For each entity key in the request, the provider looks up all cgroup IDs
     belonging to that entity from the `cgroup_map`.
   - Aggregates all matching map entries (summing rx/tx bytes and packets
     across directions, families, and protocols).
   - Builds a `proto` dictionary with per-family, per-protocol breakdown.

4. **`NetSample` output.** Emits `source_label="net:BPF"`,
   `confidence="exact"`, `aggregation="exact"`, and collector rates use
   `MetricValue.src="bpf"`.

5. **Fallback and status.** When no snapshot is available (root `None`, file
   missing, JSON parse error), returns no samples so lower-ranked providers can
   fill the frame and populates `status()` with the error. Per-entity unmapped
   rows return `net:N/A` samples with descriptive `unavailable_reason`. Status
   includes `loaded`, `attached`, `last_read`, `errors`, `entities_seen`,
   `entities_with_bpf`, and `snapshot_path`. The read-side provider reports
   `attached=false`; live BPF attach status belongs to the future daemon writer.

### Tests - 9 BPF-specific test functions

All in `groop/tests/test_network_providers.py`:

| Test | What it covers |
|------|----------------|
| `test_bpf_provider_reads_snapshot_and_returns_net_bpf_samples` | Snapshot parse + aggregation for two mapped entities; checks rx/tx bytes and packets, source_label |
| `test_bpf_provider_entity_without_bpf_mapping_returns_unavailable` | Entity not in cgroup_map returns net:N/A with reason |
| `test_bpf_provider_missing_root_returns_unavailable` | `bpf_root=None` returns empty collect plus status error |
| `test_bpf_provider_nonexistent_snapshot_returns_unavailable` | Root dir with no `snapshot.json` returns empty collect plus status error |
| `test_bpf_provider_corrupt_json_returns_unavailable` | Invalid JSON returns empty collect plus status error |
| `test_bpf_provider_status_returns_snapshot_metadata` | Successful collect populates status with entity counts |
| `test_bpf_provider_ignores_malformed_entries` | Bad map rows do not crash or inflate counters |
| `test_bpf_provider_ranking_in_collector` | Full collector integration with all three providers (BPF, netns, host); BPF outranks and source_label is `net:BPF` |
| `test_bpf_provider_is_publicly_exported` | `groop.providers.BpfProvider` public export works |

### Fixtures

- `groop/tests/fixtures/bpf/working/snapshot.json` - valid BPF snapshot with 14
  map entries across 4 cgroup IDs
- `groop/tests/fixtures/bpf/corrupt/snapshot.json` - deliberately malformed JSON
- `groop/tests/fixtures/bpf/unavailable/` - empty directory (no snapshot file)

## Deviations from the handoff doc

- **No daemon/helper implementation.** The handoff doc describes the BPF path as
  a daemon-owned lifecycle (load, attach, pin, detach). P18 implements only the
  provider reading side. The daemon lifecycle remains future work (P16/P20
  foundation exists).

- **No live BPF attach.** As specified in the scope: "do not implement packet
  capture, shaping, tc/nftables, root attach, or command execution." The
  provider is deliberately userspace-only and reads pre-built snapshots.

- **Snapshot format is JSON, not BPF map fd reads.** Since we cannot load BPF
  maps on this host, the provider reads JSON files. The format mirrors the
  design doc's key/value shape. A future daemon would write these snapshots from
  actual pinned BPF maps.

## Contract changes

No frame-shape change. The existing `NetSample.source_label` already includes
`"net:BPF"`, and `sample_rank` already ranks `"net:BPF"` highest (3).
Controller review added the additive `MetricSource` value `"bpf"` plus
`provider:network-bpf` registry source references so BPF-backed rates are not
mislabelled as generic derived metrics.

## Test evidence

```bash
# Full suite
PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests -q
........................................................................ [ 48%]
........................................................................ [ 97%]
...                                                                      [100%]
147 passed in 25.14s

# Focused network provider tests
PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m pytest \
  groop/tests/test_network_providers.py -q
15 passed in 0.16s
# test_bpf_provider_reads_snapshot_and_returns_net_bpf_samples PASSED
# test_bpf_provider_entity_without_bpf_mapping_returns_unavailable PASSED
# test_bpf_provider_missing_root_returns_unavailable PASSED
# test_bpf_provider_nonexistent_snapshot_returns_unavailable PASSED
# test_bpf_provider_corrupt_json_returns_unavailable PASSED
# test_bpf_provider_status_returns_snapshot_metadata PASSED
# test_bpf_provider_ignores_malformed_entries PASSED
# test_bpf_provider_ranking_in_collector PASSED
# test_bpf_provider_is_publicly_exported PASSED
```

## Quality gates

### `py_compile`

```bash
cd /home/vb/volkb79-2/vbpub/.worktrees/-groop-p18-bpf-provider
/tmp/vbpub-groop-p17-venv/bin/python -m py_compile \
  groop/src/groop/providers/net_bpf.py groop/src/groop/providers/__init__.py \
  groop/src/groop/collect/collector.py groop/src/groop/model.py \
  groop/src/groop/registry.py groop/tests/test_network_providers.py
# (exit 0, no output)
```

### `groop --once --json` smoke using fixture root

```bash
PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m groop.cli \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --once --json
# schema=1 entities=8 host=36
```

### Full test suite

```bash
PYTHONPATH=groop/src /tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests -q
# 147 passed in 25.14s
```

## Known gaps

1. **Live BPF overhead not measured.** This host has no `bpftool`, the running
   uid is not root, and `/sys/fs/bpf/groop` is not writable. The P17 gate
   remains the authoritative preflight check.

2. **No daemon snapshot writer.** The provider reads JSON snapshots but there
   is no daemon component that writes them from live pinned BPF maps. The P16
   daemon foundation exists but the BPF attach/lifecycle code is not
   implemented.

3. **No `cgroup_skb` BPF program source.** The BPF C source and compiled
   objects do not exist in the repo. This is intentional: P18 is the
   provider reading side only.

4. **UI/help does not surface BPF status.** The `EntityFrame.network` block
   carries `source_label="net:BPF"` which the TUI could display, but there is
   no dedicated UI element for BPF provider status. This matches the existing
   pattern: host/netns provider status is also not surfaced in the TUI
   beyond the drill-down network metadata.

## Files changed

```
M groop/README.md                           (P18 row Done)
M groop/MEASUREMENTS.md                     (P18 evidence section)
M groop/docs/STATUS.md                      (BPF provider: Partially Implemented)
M groop/src/groop/collect/collector.py       (BPF metric source label)
M groop/src/groop/model.py                   (additive bpf MetricSource)
M groop/src/groop/providers/__init__.py      (BpfProvider export)
M groop/src/groop/registry.py                (provider:network-bpf references)
M groop/tests/test_network_providers.py      (9 BPF tests + imports)
A groop/src/groop/providers/net_bpf.py       (BpfProvider class, ~200 lines)
A groop/tests/fixtures/bpf/working/snapshot.json
A groop/tests/fixtures/bpf/corrupt/snapshot.json
A groop/tests/fixtures/bpf/unavailable/
A groop/handoff/reports/P18-REPORT.md        (this file)
A groop/handoff/reports/P18-LOG.md           (work log)
```
