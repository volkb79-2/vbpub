# groop Measurements Ledger

This file records acceptance and overhead evidence required by `TUI-SPEC.md`.
Do not enable BPF by default, raise DAMON defaults, or make release performance
claims without updating this file.

## Current Evidence

Most recent merged package validation after P22:

```bash
# isolated venv reused from P13/P14/P16/P17/P20/P22 review
/tmp/vbpub-groop-p13-venv/bin/python -m pytest groop/tests -q
# 108 passed in 24.82s
```

Also passed after P22 review: focused daemon deployment tests, `py_compile` over
the changed daemon/CLI files, `groop daemon preflight --socket <missing> --json`
with exit `1` for failed checks, and wheel build/package-data verification that
`groop.service` and `groop.tmpfiles` are included.

P12 package evidence remains: sdist/wheel build, fresh wheel install, and
`groop --version` (`groop 0.1.0`).

Bounded once/json CPU/RSS smoke:

- Wall time: `0.189s`
- Child user CPU: `0.134s`
- Child sys CPU: `0.028s`
- Max RSS: `29984 KB`

### P33 acceptance smoke harness

The preferred rootless smoke evidence path (P33) — runs all safe-path checks
in one command using standard-library resource measurements:

```bash
# With fixture cgroup root (deterministic, no /sys/fs/cgroup dep):
PYTHONPATH=groop/src python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json

# Live host smoke (reads /proc, no cgroup mutation):
PYTHONPATH=groop/src python3 -m groop.acceptance smoke --json
```

Example fixture evidence after P33 implementation:

```text
  ✓  collect: Collected 1 frame with 8 entities (schema v1)
  ✓  serialize: frame_to_jsonable + frame_from_jsonable round-trip passed
  ✓  source_labels: Metric source distribution (572 total): ...
  ✓  replay: Replay loaded: 1 frame(s), first ts=100.000, last ts=100.000
  wall:   0.18s    user:   0.05s     sys:   0.01s     RSS:  23400 KB
  ALL CHECKS PASSED  (exit code 0)
```

## v1 Acceptance Measurements

### CPU Steady State

Required by spec §9 item 1.

```bash
pidstat -p "$(pgrep -f 'groop')" 5 60
```

Record:

- Host:
- Kernel:
- CPU count:
- Entity count:
- Command:
- Result:
- Pass/fail against `<5%` of one CPU core:

### RSS

Required by spec §9 item 2.

```bash
ps -o pid,rss,cmd -p "$(pgrep -f 'groop')"
```

Record:

- Entity count:
- History settings:
- RSS:
- Pass/fail:

### Packaging

Required by spec §9 item 11.

```bash
python3 -m build groop/
pipx install ./groop/dist/groop-*.whl --force
groop --version
groop --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
```

Record:

- Build artifact:
- `groop-0.1.0.tar.gz`
- `groop-0.1.0-py3-none-any.whl`
- pipx version:
- Not measured; P12 used fresh venv wheel install instead.
- Result:
- Pass: wheel installed in a fresh venv and `groop --version` returned
  `groop 0.1.0`.

## DAMON Gate

Required before raising DAMON defaults or enabling persistent paddr.

Current P14 status: fixture-safe TUI modal/control tests exist, but live-root
acceptance was not run in this development session. Host sysfs mutation must be
performed deliberately on a selected test machine.

Fixture evidence:

- TUI vaddr modal requires exact `START` and starts only through
  `start_planned_session`.
- TUI paddr modal requires exact `START`, starts only through
  `start_planned_paddr_session`, and reports duplicate groop-owned paddr
  sessions.
- TUI stop surface calls `stop_owned_sessions(all_mine=True)` and leaves foreign
  kdamond slots untouched.

Measurement plan:

1. Baseline game/server session without groop-controlled DAMON.
2. Passive read-only groop TUI.
3. Controlled vaddr session against one entity.
4. Manual paddr host session.
5. Stop all groop-owned sessions and verify foreign sessions remain untouched.

Record for each:

- Workload:
- DAMON config:
- CPU/RSS overhead:
- Collection interval:
- Observed latency/stutter:
- Evidence:
  - vaddr start command/UI path:
  - vaddr observed hot/warm/cold columns:
  - paddr start command/UI path:
  - paddr banner heat after two aggregation windows:
  - stop command/UI path:
  - foreign sessions untouched:
- Result:

## BPF Gate

Required before enabling any BPF provider by default.

Measurement plan from spec §10 / Appendix B:

1. Baseline traffic without BPF.
2. Same traffic with BPF loaded.
3. Cgroup churn while BPF is attached.
4. High packet-rate traffic.
5. Many cgroups/containers.
6. Attach/detach failure recovery.
7. Reboot cleanup / pinned-object audit.

Record:

- BPF program version:
- Pin path:
- Map sizes:
- Traffic generator:
- Packet/byte rate:
- CPU overhead:
- Drop/error counters:
- Result:

P17 safe-run evidence (2026-07-09):

- Helper:
  - `/tmp/vbpub-groop-p17-venv/bin/groop bpf gate --proc-root groop/tests/fixtures/procfs/network --json`
- Result:
  - safe no-op
  - blocked live BPF load: `bpftool` missing, uid 1003 not root, `/sys/fs/bpf/groop` not writable
  - baseline: rx 15100 B / tx 27100 B / rx_pkts 151 / tx_pkts 191
  - no BPF maps were loaded or pinned
- Pin path: `/sys/fs/bpf/groop/`
- Commands:
  - `id -u`
  - `command -v bpftool`
  - `mount | grep " /sys/fs/bpf "`
  - `/tmp/vbpub-groop-p17-venv/bin/groop bpf gate --proc-root groop/tests/fixtures/procfs/network --json`

### P18 BPF Provider Implementation (2026-07-09)

The P18 BPF provider was implemented behind the existing provider boundary
without any privileged BPF operations (no root attach, no map pin, no
`cgroup_skb` program load). The provider reads pinned map snapshots from JSON
fixture files, parses numeric cgroup id to entity key mappings in userspace, and
produces `NetSample` values with `source_label="net:BPF"`.

**Live privileged BPF overhead was not measured.** This implementation is a
userspace-only provider that consumes daemon-produced snapshots. Testing was
done entirely with fixture JSON files.

Fixture/unit evidence:

```bash
/tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests/test_network_providers.py -v --no-header 2>&1 | grep bpf
# 9 BPF-specific tests pass:
#   test_bpf_provider_reads_snapshot_and_returns_net_bpf_samples
#   test_bpf_provider_entity_without_bpf_mapping_returns_unavailable
#   test_bpf_provider_missing_root_returns_unavailable
#   test_bpf_provider_nonexistent_snapshot_returns_unavailable
#   test_bpf_provider_corrupt_json_returns_unavailable
#   test_bpf_provider_status_returns_snapshot_metadata
#   test_bpf_provider_ignores_malformed_entries
#   test_bpf_provider_ranking_in_collector
#   test_bpf_provider_is_publicly_exported
```

Full suite impact (BPF tests add negligible overhead):

```bash
/tmp/vbpub-groop-p17-venv/bin/python -m pytest groop/tests -q
# 147 passed in 25.14s
```

Key overhead characteristics (userspace-only, no kernel BPF):

- Snapshot parse: ~1ms for a 2KB JSON file with 14 map entries
- Per-entity aggregation: O(n_entries * n_mapped_entities), linear in snapshot size
- No kernel BPF program overhead (no packet-per-packet accounting)
- No cgroup_skb hook execution cost
- No per-CPU map contention
- No pin/unpin lifecycle

Blocker for live BPF overhead measurement:

- `bpftool` is not installed on this host
- Current uid 1003 is not root
- `/sys/fs/bpf/groop` is not writable
- No `cgroup_skb` BPF C source or compiled object is present in the repo
- The BPF gate (P17) remains the authoritative preflight check

## Release Signoff Template

- Release/tag:
- Commit:
- v1 CPU/RSS measured:
- Packaging measured:
- DAMON measured:
- BPF measured if applicable:
- Known exceptions:
