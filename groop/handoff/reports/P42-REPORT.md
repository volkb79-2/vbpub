# P42 Report - Daemon BPF Snapshot Bridge

**Branch:** `feat/groop-p42-daemon-bpf-snapshot-bridge`
**Base:** `fba1d89` (docs(groop): carve P42 daemon BPF snapshot bridge)
**Date:** 2026-07-10 (updated after controller review)

## What Was Built

### `groop/src/groop/daemon/bpf_snapshot.py` - `BpfSnapshotBridge`

A daemon-side module that reads one explicitly configured pinned counter map
using `bpftool --json map dump pinned PATH` through an argv-only, injectable
command runner. Key characteristics:

1. **Path confinement.** The map pin path is validated to be beneath the
   configured groop BPF root. Uses ``Path.is_relative_to`` (not string prefix
   matching) so a sibling-prefix symlink escape is correctly rejected.
   Traversal (`..`) is also rejected with `BpfSnapshotError`.

2. **Safe bpftool execution.** Command execution uses `subprocess.check_output`
   with argv-only invocation (no shell). ``subprocess.CalledProcessError`` and
   ``subprocess.TimeoutExpired`` are caught and converted to bounded
   ``BpfSnapshotError`` without dumping unbounded output. Output exceeding
   the 1 MB limit, nonzero exit codes, and missing ``bpftool`` are all caught
   and reported.

3. **Decoding.** The JSON output from `bpftool` is parsed and validated.
   Entries are checked for required fields (`cgroup_id`, `direction`), valid
   direction (`ingress`/`egress`), valid family (`ipv4`/`ipv6`/`other`), valid
   proto (`tcp`/`udp`/`icmp`/`other`), and non-negative counters. Both
   nested `key`/`value` and top-level field formats are supported.

4. **Raw byte array rejection.** Entries where ``key`` or ``value`` is a plain
   string (hex-encoded bytes) are explicitly rejected with a clear error
   message. Per-CPU array values (the ``values`` key) are also rejected.
   BTF-typed structured output is required.

5. **Cgroup mapping.** An injectable cgroup-id resolver (`_walk_cgroup_ids`)
   walks the cgroup-v2 tree and maps `st_ino` (cgroup inode) to relative
   entity key paths. The kernel identity assumption is documented as
   **kernel-version dependent and not verified by this function**; no specific
   kernel version is claimed.

6. **Snapshot assembly.** Produces the P18 `snapshot.json` contract with
   `schema_version`, `generated_at` (time.time()), `source` metadata (bpf_root,
   map name, bridge version), `maps` (decoded entries), and `cgroup_map`.

7. **Atomic write to separate state_dir.** Writes via a private
   ``.snapshot.json.<PID>.tmp`` file in the **state directory** (default
   ``/run/groop/bpf``, separate from the bpffs pin root), flushes and fsyncs
   the file, sets permissions to ``0o644`` (non-world-writable), then
   atomically renames with `os.replace()`. Temp files are cleaned up on failure.

8. **Last-good preservation.** The bridge keeps the last valid snapshot in
   memory. Transient `bpftool` failures do not invalidate the previous snapshot.
   ``restore_last_known_good()`` loads a valid on-disk snapshot if no in-memory
   one is available.

9. **Immediate pre-thread refresh.** ``refresh_and_write()`` performs an
   immediate refresh before the periodic thread starts. If it fails, the
   periodic loop continues.

### Integration into `groop daemon serve`

The bridge is integrated into `groop daemon serve` via:
- `--bpf-root PATH` CLI argument (disabled by default, default `None`)
- `--bpf-interval SECONDS` CLI argument (default `30.0`, minimum `5.0`)
- `--bpf-state-dir PATH` CLI argument (default `/run/groop/bpf`)
- `[bpf_snapshot]` TOML config section with `enabled`, `root`, `interval`,
  `map_name`, `state_dir` fields (all disabled by default)

When enabled:
- ``BpfProvider`` is integrated at **highest rank** (index 0) into the
  daemon's ``Collector.network_providers`` tuple. Existing host/netns
  providers remain as fallback for entities without BPF mapping.
- The last known good snapshot is restored from disk.
- An immediate ``refresh_and_write()`` occurs before the thread starts
  (best-effort, failures logged).
- A daemon ``threading.Thread`` runs the periodic refresh loop and
  writes the snapshot to ``state_dir/snapshot.json``.
- Refresh failures log a message but preserve the last good snapshot.
- On daemon shutdown, the thread is cleanly stopped via ``threading.Event``.

### Configuration (`src/groop/config.py`)

Added `BpfSnapshotConfig` dataclass:
- `enabled: bool = False`
- `root: Path | None = None`
- `interval: float = 30.0`
- `map_name: str = "groop_cgroup_skb"`
- `state_dir: Path = Path("/run/groop/bpf")`

Integrated into `GroopConfig.to_primitive()` and `load()`.

### Tests - 44 test functions (was 29)

All in `groop/tests/test_daemon_bpf_snapshot.py`:

**New tests added during controller review:**

| Test | What it covers |
|------|----------------|
| `test_parse_bpftool_rejects_raw_byte_array_key` | Raw hex string key is rejected |
| `test_parse_bpftool_rejects_raw_byte_array_value` | Raw hex string value is rejected |
| `test_parse_bpftool_rejects_percpu_array_values` | Per-CPU `values` key is rejected |
| `test_validate_map_path_rejects_sibling_prefix_escape` | Sibling-prefix symlink escape (``is_relative_to``) |
| `test_run_bpftool_called_process_error` | ``CalledProcessError`` converted to ``BpfSnapshotError`` |
| `test_run_bpftool_timeout` | ``TimeoutExpired`` converted to ``BpfSnapshotError`` |
| `test_restore_last_known_good_loads_from_disk` | On-disk snapshot restoration |
| `test_restore_last_known_good_skips_if_already_present` | Doesn't overwrite in-memory |
| `test_restore_last_known_good_missing_file` | Silent skip on missing file |
| `test_refresh_and_write` | Convenience refresh+write method |
| `test_daemon_bpf_state_dir_from_config` | Config-specified state_dir |
| `test_daemon_bpf_state_dir_default_when_not_set` | Default state_dir |
| `test_bpf_provider_with_state_dir` | BpfProvider reads from state_dir |
| `test_bpf_provider_falls_back_to_bpf_root_as_state_dir` | BpfProvider fallback when no state_dir |
| `test_bpf_snapshot_bridge_is_publicly_exported` (restored) | Public export |
| `test_bpf_snapshot_error_is_publicly_exported` (restored) | Public export |

## Deviations from the Handoff Doc

- **Snapshot written to separate state_dir, not bpffs pin root.** The bridge
  writes ``snapshot.json`` to ``state_dir`` (default ``/run/groop/bpf``)
  rather than directly under the bpffs ``bpf_root``. This prevents writing
  regular JSON files into bpffs.

- **Cgroup ID resolver uses `st_ino` for fixture tests.** The production
  ``_walk_cgroup_ids`` reads cgroup directories on real ``/sys/fs/cgroup``,
  but fixture tests use a mock resolver. The kernel identity assumption is
  documented as kernel-version dependent; no specific kernel version is claimed.

- **The periodic refresh loop is a daemon `threading.Thread`, not a managed
  timer service.** For the current daemon spike this is sufficient; a
  production daemon (future work) may prefer `asyncio` or a dedicated service
  manager.

- **``BpfProvider`` integrated at highest rank when enabled.** The original
  implementation did not inject the BPF provider into the Collector. Now when
  the bridge is enabled, ``BpfProvider`` is inserted at index 0 of
  ``network_providers`` so that served/current frames can contain ``net:BPF``
  samples.

## Controller Review Disclosure

This report reflects changes made in response to a controller review of commit
``e8b9249``. The review identified nine categories of improvements:

1. Separate snapshot ``state_dir`` (``/run/groop/bpf``) distinct from bpffs pin root
2. ``BpfProvider`` integration at highest rank into daemon ``Collector``
3. ``CalledProcessError``/``TimeoutExpired`` bounded conversion
4. ``Path.is_relative_to`` for path confinement (sibling-prefix symlink escape)
5. Immediate pre-thread ``refresh_and_write``
6. Integration-level daemon construction/refresh tests
7. Tightened cgroup-v2 identity docs (no unverified kernel version claims)
8. Explicit raw byte array rejection; BTF-typed structured output required
9. Docs/reports/test count corrections

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
# 48 passed in 0.35s
```

### Full suite

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# 431 passed, 1 skipped, 1 warning in 47.90s
```

### py_compile

```bash
cd /workspaces/vbpub/.worktrees/-groop-p42-daemon-bpf-snapshot-bridge
python3 -m py_compile \
  groop/src/groop/daemon/bpf_snapshot.py \
  groop/src/groop/daemon/__init__.py \
  groop/src/groop/config.py \
  groop/src/groop/cli.py \
  groop/src/groop/providers/net_bpf.py \
  groop/tests/test_daemon_bpf_snapshot.py
# (exit 0, no output)
```

### Acceptance regression

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest \
  groop/tests/test_acceptance.py -q
# 40 passed in 6.99s

PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m groop.acceptance \
  tui-smoke --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
# exit 0; ok=true; frames=1; view=tree; profile=auto
```

## Quality Gates

- [x] Full test suite green (431 passed, 1 skipped)
- [x] `py_compile` clean on all new/changed Python files
- [x] Fixture tests cover decoding, cgroup mapping, atomic replacement,
      last-good preservation, path confinement, output bounds, command
      failure, cleanup, raw byte array rejection, and subprocess errors
- [x] Daemon config tests prove disabled by default and config-enabled
- [x] P18 `BpfProvider` existing tests remain green
- [x] Integration-level daemon construction/refresh tests added
- [x] Focused BPF snapshot tests: 48 passed

Post-merge controller validation on merge `8e48498` supersedes the pre-merge
timings above: 48 focused tests passed in 0.29s; the full suite reported 431
passed and one optional skip in 47.40s; acceptance reported 40 passed in 7.41s;
TUI smoke exited zero with one tree/auto frame; full-source `py_compile` was
clean.

## Known Gaps / Open Items

1. **Live `bpftool` execution not tested.** This host has no `bpftool`
   installed, no writable `/sys/fs/bpf/groop`, and no pinned BPF maps. All
   bpftool interaction is tested via mock command runners. A privileged host
   is needed for end-to-end bpftool execution validation.

2. **Cgroup ID kernel assumption documented as unverified.** The production
   `_walk_cgroup_ids` uses `st_ino` which may or may not match the BPF cgroup
   ID on a given kernel. The docstring states this is kernel-version dependent
   and not verified.

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
M groop/docs/STATUS.md                        (P42 added, test counts updated)
M groop/src/groop/daemon/__init__.py           (BpfSnapshotBridge/BpfSnapshotError exports)
M groop/src/groop/config.py                    (BpfSnapshotConfig with state_dir)
M groop/src/groop/cli.py                       (--bpf-root/--bpf-interval/--bpf-state-dir,
                                                 BpfProvider integration, immediate refresh)
A groop/src/groop/daemon/bpf_snapshot.py       (BpfSnapshotBridge module, ~500 lines
                                                 with controller review fixes)
M groop/src/groop/providers/net_bpf.py         (state_dir parameter, fallback to bpf_root)
A groop/tests/test_daemon_bpf_snapshot.py      (44 focused tests, +15 for review fixes)
A groop/handoff/reports/P42-LOG.md             (work log, updated)
A groop/handoff/reports/P42-REPORT.md          (this file, updated)
```
