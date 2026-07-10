# P42 Report - Daemon BPF Snapshot Bridge

**Branch:** `feat/groop-p42-daemon-bpf-snapshot-bridge`
**Base:** `fba1d89` (docs(groop): carve P42 daemon BPF snapshot bridge)
**Date:** 2026-07-09

## What Was Built

### `groop/src/groop/daemon/bpf_snapshot.py` - `BpfSnapshotBridge`

A daemon-side module that reads one explicitly configured pinned counter map
using `bpftool --json map dump pinned PATH` through an argv-only, injectable
command runner. Key characteristics:

1. **Path confinement.** The map pin path is validated to be beneath the
   configured groop BPF root. Traversal (`..`) and symlink escape are rejected
   with `BpfSnapshotError`.

2. **Safe bpftool execution.** Command execution uses `subprocess.check_output`
   with argv-only invocation (no shell). Output exceeding the 1 MB limit,
   nonzero exit codes, and missing `bpftool` are all caught and reported as
   `BpfSnapshotError`.

3. **Decoding.** The JSON output from `bpftool` is parsed and validated.
   Entries are checked for required fields (`cgroup_id`, `direction`), valid
   direction (`ingress`/`egress`), valid family (`ipv4`/`ipv6`/`other`), valid
   proto (`tcp`/`udp`/`icmp`/`other`), and non-negative counters. Both
   nested `key`/`value` and top-level field formats are supported.

4. **Cgroup mapping.** An injectable cgroup-id resolver (`_walk_cgroup_ids`)
   walks the cgroup-v2 tree and maps `st_ino` (cgroup inode) to relative
   entity key paths. The kernel identity assumption (`st_ino ==
   bpf_get_current_cgroup_id()` on cgroup-v2 >= 4.18) is documented in the
   docstring.

5. **Snapshot assembly.** Produces the P18 `snapshot.json` contract with
   `schema_version`, `generated_at` (time.time()), `source` metadata (bpf_root,
   map name, bridge version), `maps` (decoded entries), and `cgroup_map`.

6. **Atomic write.** Writes via a private `.snapshot.json.<PID>.tmp` file in the
   destination directory, flushes and fsyncs the file, sets permissions to
   `0o644` (non-world-writable), then atomically renames with `os.replace()`.
   Temp files are cleaned up on failure.

7. **Last-good preservation.** The bridge keeps the last valid snapshot in
   memory. Transient `bpftool` failures do not invalidate the previous snapshot.

### Integration into `groop daemon serve`

The bridge is integrated into `groop daemon serve` via:
- `--bpf-root PATH` CLI argument (disabled by default, default `None`)
- `--bpf-interval SECONDS` CLI argument (default `30.0`, minimum `5.0`)
- `[bpf_snapshot]` TOML config section with `enabled`, `root`, `interval`,
  `map_name` fields (all disabled by default)

When enabled, a daemon `threading.Thread` runs the periodic refresh loop and
writes the snapshot to `bpf_root/snapshot.json`. Refresh failures log a message
but preserve the last good snapshot. On daemon shutdown, the thread is cleanly
stopped via `threading.Event`.

### Configuration (`src/groop/config.py`)

Added `BpfSnapshotConfig` dataclass:
- `enabled: bool = False`
- `root: Path | None = None`
- `interval: float = 30.0`
- `map_name: str = "groop_cgroup_skb"`

Integrated into `GroopConfig.to_primitive()` and `load()`.

### Tests - 29 test functions

All in `groop/tests/test_daemon_bpf_snapshot.py`:

| Test | What it covers |
|------|----------------|
| `test_decode_bpftool_entry_valid` | Valid nested key/value entry decodes correctly |
| `test_decode_bpftool_entry_top_level_fields` | Top-level field format also decodes |
| `test_decode_bpftool_entry_default_family_proto` | Missing family/proto default to "other" |
| `test_decode_bpftool_entry_rejects_negative_counters` | Negative bytes/packets raises error |
| `test_decode_bpftool_entry_rejects_invalid_direction` | Invalid direction raises error |
| `test_decode_bpftool_entry_rejects_invalid_family` | Invalid family raises error |
| `test_validate_map_path_inside_root` | Valid path passes confinement |
| `test_validate_map_path_rejects_traversal` | `..` traversal is rejected |
| `test_validate_map_path_rejects_escape` | Symlink escape is rejected |
| `test_run_bpftool_success` | Successful bpftool returns parsed output |
| `test_run_bpftool_nonzero_exit` | Nonzero exit raises error |
| `test_run_bpftool_oversized_output` | Oversized output raises error |
| `test_refresh_returns_valid_snapshot` | Full refresh cycle returns valid P18 snapshot |
| `test_write_snapshot_atomic_replace` | Atomic replace produces correct content |
| `test_write_snapshot_permissions_not_world_writable` | Permissions are 0o644 |
| `test_write_snapshot_cleanup_temp_on_failure` | Temp file cleaned on failure |
| `test_last_valid_snapshot_preserved_on_failure` | Last valid snapshot preserved after failure |
| `test_last_valid_snapshot_none_initially` | Initially None |
| `test_walk_cgroup_ids` | Mock cgroup id resolver returns mapping |
| `test_bridge_reports_missing_bpftool` | Missing bpftool raises error |
| `test_parse_bpftool_malformed_json` | Malformed JSON raises error |
| `test_parse_bpftool_non_array_output` | Non-array output raises error |
| `test_parse_bpftool_entry_non_dict` | Non-dict entry raises error |
| `test_daemon_bpf_disabled_by_default` | Config defaults to disabled |
| `test_daemon_bpf_config_disabled_by_default` | Empty `[bpf_snapshot]` stays disabled |
| `test_daemon_bpf_config_enabled` | Config can enable the bridge |
| `test_write_snapshot_cleans_tmp_on_rename` | No .tmp files remain after write |
| `test_bpf_snapshot_bridge_is_publicly_exported` | Public export works |
| `test_bpf_snapshot_error_is_publicly_exported` | Public export works |

## Deviations from the Handoff Doc

- **No separate sync thread with configurable destination dir.** The bridge
  writes directly under `bpf_root` (the pin root) rather than accepting a
  separate write destination. This matches the expected daemon deployment where
  the pinned map and snapshot live under the same `bpf_root`.

- **Cgroup ID resolver uses `st_ino` for fixture tests.** The production
  `_walk_cgroup_ids` reads cgroup directories on real `/sys/fs/cgroup`, but
  fixture tests use a mock resolver. The kernel identity assumption is
  documented in the `_walk_cgroup_ids` docstring.

- **The periodic refresh loop is a daemon `threading.Thread`, not a managed
  timer service.** For the current daemon spike this is sufficient; a
  production daemon (future work) may prefer `asyncio` or a dedicated service
  manager.

## Proposed Contract Changes

None. The bridge produces the existing P18 `snapshot.json` contract format
consumed by `BpfProvider` without any schema adapter. No frame-shape or
provider-interface changes are needed.

## Test Evidence

### Focused tests

```bash
cd /workspaces/vbpub/.worktrees/-groop-p42-daemon-bpf-snapshot-bridge
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest \
  groop/tests/test_daemon_bpf_snapshot.py -q
# 29 passed in 0.27s
```

### Full suite

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# 412 passed, 1 skipped in 49.71s
```

### py_compile

```bash
cd /workspaces/vbpub/.worktrees/-groop-p42-daemon-bpf-snapshot-bridge
python3 -m py_compile \
  groop/src/groop/daemon/bpf_snapshot.py \
  groop/src/groop/daemon/__init__.py \
  groop/src/groop/config.py \
  groop/src/groop/cli.py \
  groop/tests/test_daemon_bpf_snapshot.py
# (exit 0, no output)
```

## Quality Gates

- [x] Full test suite green (412 passed, 1 skipped)
- [x] `py_compile` clean on all new/changed Python files
- [x] Fixture tests cover decoding, cgroup mapping, atomic replacement,
      last-good preservation, path confinement, output bounds, command
      failure, and cleanup
- [x] Daemon config tests prove disabled by default and config-enabled
- [x] P18 `BpfProvider` existing tests remain green
- [x] Focused BPF snapshot tests: 29 passed

## Known Gaps / Open Items

1. **Live `bpftool` execution not tested.** This host has no `bpftool`
   installed, no writable `/sys/fs/bpf/groop`, and no pinned BPF maps. All
   bpftool interaction is tested via mock command runners. A privileged host
   is needed for end-to-end bpftool execution validation.

2. **Cgroup ID kernel assumption unverified.** The production `_walk_cgroup_ids`
   assumes `cgroup_dir.stat().st_ino` matches the cgroup ID from
   `bpf_get_current_cgroup_id()`. This holds on cgroup-v2 >= 4.18 but is not
   verified on this host.

3. **No live BPF overhead evidence.** The P17 BPF gate remains the authoritative
   preflight check. P42 adds no new source of live overhead measurement.

4. **Daemon refresh thread is a basic polling loop.** For production use, the
   refresh loop could benefit from proper backoff, health metrics, and
   integration with the daemon's status command.

## Files Changed

```
M groop/README.md                             (P42 row: Planned -> Done)
M groop/MEASUREMENTS.md                       (P42 evidence section)
M groop/docs/BPF-NETWORK-ACCOUNTING.md        (P42 implementation status)
M groop/docs/OPERATIONS.md                    (bpf_snapshot config example)
M groop/docs/ROADMAP.md                       (P42 status: done)
M groop/docs/STATUS.md                        (P42 added to partially implemented)
M groop/src/groop/daemon/__init__.py           (BpfSnapshotBridge/BpfSnapshotError exports)
M groop/src/groop/config.py                    (BpfSnapshotConfig dataclass + integration)
M groop/src/groop/cli.py                       (--bpf-root/--bpf-interval + integration)
A groop/src/groop/daemon/bpf_snapshot.py       (BpfSnapshotBridge module, ~360 lines)
A groop/tests/test_daemon_bpf_snapshot.py      (29 focused tests)
A groop/handoff/reports/P42-LOG.md             (work log)
A groop/handoff/reports/P42-REPORT.md          (this file)
```
