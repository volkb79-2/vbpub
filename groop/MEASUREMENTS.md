# groop Measurements Ledger

This file records acceptance and overhead evidence required by `TUI-SPEC.md`.
Do not enable BPF by default, raise DAMON defaults, or make release performance
claims without updating this file.

The canonical gate map and copy-paste commands live in
`docs/RELEASE-READINESS.md`. This file remains the place for dated raw results.

## P43 Packaging Evidence (2026-07-10)

P43 replaces the historical pre-1.0 dependency range (`textual>=0.58,<1`) with
`textual>=8.2.8` and no artificial upper ceiling. Historical P40 evidence of the
old bound is preserved below; this entry supersedes it.

### Source metadata

```bash
grep textual groop/pyproject.toml
# dependencies = ["textual>=8.2.8"]
```

### Built wheel METADATA

```bash
unzip -p groop/dist/groop-0.1.0-py3-none-any.whl groop-0.1.0.dist-info/METADATA
# Requires-Dist: textual>=8.2.8
```

### Packaging-metadata regression tests

Two tests structurally parse `pyproject.toml` and prove a lower bound of at
least 8.2.8 with no `<` or `<=` upper ceiling. Wheel METADATA is checked from a
fresh build as the separate release gate above, avoiding stale ignored
artifacts in the normal suite.

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_packaging_metadata.py -q
# 2 passed in 0.03s
```

### Clean resolver installation

The local wheel was installed into an isolated virtualenv with no preinstalled
Textual. Pip resolved Textual 8.2.8:

```text
Successfully installed groop-0.1.0 ... textual-8.2.8 ...
```

Installed groop version verification and replay smoke:

```text
$ /tmp/p43-clean-venv/bin/groop --version
groop 0.1.0

$ /tmp/p43-clean-venv/bin/groop --replay ... --step --ui-smoke
ui smoke ok frames=1 view=tree profile=auto
```

### UI/acceptance/full suite

All required gates pass using `/tmp/p43-clean-venv/bin/python`, with the wheel's
normally resolved Textual 8.2.8:

- Full suite: `433 passed, 1 skipped in 47.31s`.
- Acceptance: `40 passed in 7.27s`.
- UI/Textual: `59 passed in 10.91s`.
- Direct replay UI smoke: exit `0`, one frame, tree view, auto profile.
- P38 TUI smoke: exit `0`, `ok: true`, one frame, tree view, auto profile,
  wall `0.4614s`, max RSS `46392 KB`.
- Full-source `py_compile`: clean.

## Current Status

P43 replaces the pre-1.0 Textual dependency with `>=8.2.8`, with source,
fresh-wheel, normal resolver, and clean-environment behavioral evidence above.

## Prior P41 Status

P41 adds a deterministic rendered replay fidelity gate that compares every
formatted table cell byte-for-byte across the record/replay cycle using
`RecordWriter`, `ReplayDriver.play(step=True)`, and the production table cell
builder at a fixed width/profile/sort/filter. The full suite now covers 383
passing tests plus one optional compressed-recording skip:

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# 383 passed, 1 skipped in 46.81s
```

Also validated:

- Focused table/record/fidelity tests: `19 passed, 1 skipped in 9.57s`.
- Focused rendered fidelity tests: `1 passed, 1 skipped in 0.27s`.
- Post-merge focused acceptance tests: `40 passed in 7.26s`.
- P41 fixture replay TUI smoke: `ok: true`, exit `0`, `frames: 1`,
  `view: tree`, `profile: auto`; wall `0.5304s`, user CPU `0.4143s`, system
  CPU `0.0625s`, max RSS `48056 KB`.
- Full-source `py_compile`.

## Prior P40 Status

P40 restored the full green suite under the managed devcontainer environment
(Python 3.14.6, Textual 8.2.8). Controller validation after P39/P40 merged on
main:

## Historical P38 Evidence

Most recent merged validation after P38:

```bash
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
# 382 passed in 41.48s
```

P38 adds 14 focused TUI smoke tests to the acceptance test file.
Also passed after P38 merge: focused acceptance tests (`40 passed in 7.05s`),
`py_compile` over P38 changed files, import-contract probe, and the P38 TUI
smoke command below.

P12 package evidence remains: sdist/wheel build, fresh wheel install, and
`groop --version` (`groop 0.1.0`).

Bounded once/json CPU/RSS smoke:

- Wall time: `0.189s`
- Child user CPU: `0.134s`
- Child sys CPU: `0.028s`
- Max RSS: `29984 KB`

### P33 acceptance smoke harness

The preferred rootless smoke evidence path (P33) runs all safe-path checks
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
  [OK] collect: Collected 1 frame with 8 entities (schema v1)
  [OK] serialize: frame_to_jsonable + frame_from_jsonable round-trip passed
  [OK] source_labels: Metric source distribution (572 total): ...
  [OK] replay: Replay loaded: 1 frame(s), first ts=100.000, last ts=100.000
  wall:   0.18s    user:   0.05s     sys:   0.01s     RSS:  23400 KB
  ALL CHECKS PASSED  (exit code 0)
```

Controller fixture evidence after P33 merge:

- Command:
  - `PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m groop.acceptance smoke --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json`
- Result:
  - exit `0`, `ok: true`
  - entities: `8`
  - metric source labels: `572`
  - replay frames: `1`
  - wall: `0.1794s`
  - user CPU: `0.0393s`
  - sys CPU: `0.0145s`
  - max RSS: `89256 KB`

### P35 acceptance steady harness

The preferred rootless collector steady-state evidence path (P35) extends the
P33 module with a repeatable multi-sample loop that measures CPU/RSS over time:

```bash
# Fixture-based (fast, deterministic):
PYTHONPATH=groop/src python3 -m groop.acceptance steady \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --samples 5 --interval-s 0 --pretty-json

# Live host (--samples 60 --interval-s 5.0 default):
PYTHONPATH=groop/src python3 -m groop.acceptance steady --json
```

Example output showing the measurement fields:

```text
  Collection: 5/5 samples completed
  Entity count: min=8, max=8, last=8
  wall:      X.XXXXs
  user:      X.XXXXs
   sys:      X.XXXXs
   RSS:      XXXXX KB
  avg sample: X.XXXXs
  cpu%:      XX.XX%  (of one core)
  ALL CHECKS PASSED  (exit code 0)
```

P35 fixture evidence:

- Command:
  - `PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m groop.acceptance steady --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --samples 2 --interval-s 0 --json`
- Result:
  - exit `0`, `ok: true`
  - samples: `2/2`
  - entity counts: `min=8, max=8, last=8`
  - wall: `0.5187s`
  - user CPU: `0.0537s`
  - sys CPU: `0.0103s`
  - avg sample wall: `0.2593s`
  - CPU: `12.34%` of one core
  - max RSS: `99616 KB`

### P38 TUI smoke evidence harness

The preferred rootless TUI smoke evidence path (P38) extends the acceptance
module with a rooted subprocess-based `tui-smoke` command that exercises the
existing `--ui-smoke` path from outside the UI process:

```bash
# Fixture-based (deterministic, no /sys/fs/cgroup dep):
PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json

# Text output with child CPU/RSS measurements:
PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl
```

P38 fixture evidence (post-merge main):

```text
groop acceptance tui-smoke  v0.1.0
  UI smoke: ui smoke ok frames=1 view=tree profile=auto
  exit code: 0
  wall:     0.3569s
  user:     0.2620s  (child)
   sys:     0.0430s  (child)
   RSS:      41656 KB  (child max)
```

- Command:
  - `PYTHONPATH=groop/src python3 -m groop.acceptance tui-smoke --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json`
- Result:
  - exit `0`, `ok: true`
  - smoke line: `ui smoke ok frames=1 view=tree profile=auto`
  - wall: `0.3569s`
  - child user CPU: `0.2620s`
  - child sys CPU: `0.0430s`
  - child max RSS: `41656 KB`

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

Required by spec §9 item 11, updated by P43.

The published dependency changed from `textual>=0.58,<1` to `textual>=8.2.8`
with no artificial upper bound. This is verified by:

1. Source metadata (`pyproject.toml` → `dependencies = ["textual>=8.2.8"]`).
2. Built wheel METADATA (`Requires-Dist: textual>=8.2.8`).
3. Packaging-metadata regression tests (2 tests, all pass).
4. Clean resolver installation into an isolated venv (Textual >=8.2.8 resolved).

Dependency policy: prefer current compatible upstream releases. Add an upper
bound only for a reproduced incompatibility, with a tracked condition for
removing that bound.

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
- pipx version: `1.15.0`
- Controller evidence (2026-07-10, main after P39/P40):
  - Build ran with `/home/vscode/.venv/bin/python -m build groop/` and an
    isolated temporary output directory.
  - Produced `groop-0.1.0.tar.gz` and
    `groop-0.1.0-py3-none-any.whl`.
  - `pipx install --force <temporary-wheel>` succeeded using isolated
    temporary `PIPX_HOME` and `PIPX_BIN_DIR`.
  - The isolated console script returned `groop 0.1.0`.
  - From an empty directory with no config, the installed console script ran
    fixture replay with `--step --ui-smoke` and printed
    `ui smoke ok frames=1 view=tree profile=auto`.
  - Temporary build and pipx state was removed after the check.
- Result: Pass for spec section 9 item 11.

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

### P42 Daemon BPF Snapshot Bridge (2026-07-10)

The P42 daemon BPF snapshot bridge was implemented as a fully fixture-tested
module with no privileged operations required for testing. The bridge reads
pinned BPF counter maps via ``bpftool --json map dump pinned PATH`` through an
argv-only injectable command runner, decodes the P17/P18 logical dimensions,
builds the ``cgroup_map``, and atomically writes the ``snapshot.json`` contract
consumed by the P18 ``BpfProvider``.

**Live BPF overhead was not measured.** Testing was done entirely with mock
command runners and fixture directories. The bridge requires ``bpftool``
installed, a writable BPF pin root, and already-pinned BPF maps to produce
live snapshots — none of which are available on this development host.

Controller fixture/unit evidence after review corrections:

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest \
  groop/tests/test_daemon_bpf_snapshot.py -q
# Post-merge: 48 passed, 1 warning in 0.29s
```

Full suite impact:

```bash
PYTHONPATH=groop/src /home/vscode/.venv/bin/python -m pytest groop/tests -q
# Post-merge: 431 passed, 1 skipped, 1 warning in 47.40s
```

Blocker for live BPF snapshot bridge measurement:

- ``bpftool`` is not installed on this host
- The current session is not a deliberate privileged BPF test host
- ``/sys/fs/bpf/groop`` is not writable
- No pinned BPF maps exist under ``/sys/fs/bpf/groop``
- The P17 BPF gate remains the authoritative preflight check
- No ``cgroup_skb`` BPF program is compiled or pinned in the repo

Key overhead characteristics (userspace-only, no kernel BPF):

- Snapshot parse: ~1ms for a 2KB JSON file with 14 map entries
- Per-entity aggregation: O(n_entries * n_mapped_entities), linear in snapshot size
- No kernel BPF program overhead (no packet-per-packet accounting)
- No cgroup_skb hook execution cost
- No per-CPU map contention
- No pin/unpin lifecycle

Blocker for live BPF overhead measurement:

- `bpftool` is not installed on this host
- This session is not a deliberate privileged BPF test-host run
- `/sys/fs/bpf/groop` is not writable
- No `cgroup_skb` BPF C source or compiled object is present in the repo
- The BPF gate (P17) remains the authoritative preflight check

## P45 Bounded Inspect-Files Content Reads Evidence (2026-07-10)

### Focused Tests

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_inspect_files.py -v
# 113 passed in 0.64s (controller correction focused gate)
```

The P45 suite covers:

- Read gating: disabled without --inspect-files, without --admin, without both
- Read disabled-via-CLI: exit codes for denied/error/success paths
- Read content: Docker JSON log and cgroup file reads with fixture roots
- Read bounds: max-bytes and max-lines truncation
- Read safety: no subprocess import in reader, no arbitrary path escape,
  short Docker ID rejection, absolute path/traversal rejection,
  unsupported kind rejection, error JSON format (no content echo)
- Read CLI integration: args parsing, custom bounds,
  JSON/text output, denied exit code

### Structural Safety

```bash
python3 -m py_compile groop/src/groop/inspect_files/reader.py
# clean, exit 0
```

The reader module:

- Never imports `subprocess`
- Uses `os.open()` with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW` for no-follow opens
- Verifies `stat.S_ISREG` on all opened descriptors (rejects symlinks, devices,
  FIFOs, sockets, directories)
- Traverses every path component descriptor-relatively under the allowlisted
  root with `O_NOFOLLOW`
- Decodes invalid bytes with replacement and sanitizes terminal control bytes
- Returns deterministic JSON/text output with explicit truncation flags
- Never echoes content on error/denied paths

### Fixture API Smoke

```bash
PYTHONPATH=groop/src python3 - <<'PY'
from pathlib import Path
from groop.inspect_files.reader import build_inspect_read
r = build_inspect_read(
    "docker-json-log", "a" * 64,
    inspect_files=True, admin=True,
    fixture_root=Path("groop/tests/fixtures/inspect_files/docker"),
    is_root=lambda: True,
)
assert r.mode == "content" and "container starting up" in r.content
print("P45 FIXTURE SMOKE OK")
PY
```

### Full Suite

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 623 passed, 1 skipped in 48.05s after rebasing onto P44/P46.
```

### Full-Source py_compile

All Python files under `groop/src/groop` and `groop/tests` compile cleanly:

```bash
mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
python3 -m py_compile "${pyfiles[@]}"
# clean, exit 0
```

### Known Limitations

- Production Docker log reads require a full 64-char hex container ID.
  Short IDs and container names are rejected.
- Systemd journal content reads are not implemented (requires subprocess).
- Only Docker JSON logs and cgroup files support content reads.
- No follow/stream mode or daemon integration.
- No TUI integration for file reads.

## P44 Daemon-Owned paddr Lifecycle (2026-07-10)

P44 adds a daemon lifecycle owner around the existing `damon/paddr.py` and
`damon/control.py` sources of truth. The lifecycle is fixture-tested and does
not require host DAMON sysfs.

### Fixture/unit evidence (no live DAMON mutation)

```bash
PYTHONPATH=groop/src python3 -m pytest \
  groop/tests/test_daemon_paddr_lifecycle.py -q
# 13 passed in 0.22s
```

### Config evidence

Default `paddr_enabled = False` confirmed by source default, TOML load
round-trip, and to_primitive() serialization. Explicit `paddr_enabled = true`
loaded correctly from `[damon]` TOML section.

### Covered scenarios

- Disabled lifecycle performs zero DAMON writes.
- Enabled lifecycle starts one owned paddr session and stops it on stop().
- Idempotent adoption: existing groop-owned marker is adopted without duplicate.
- Foreign session: non-groop markers and foreign kdamond slots untouched.
- No free slot: raises bounded PaddrLifecycleStartError.
- Root required: raises bounded PaddrLifecycleStartError.
- Stop returns 0 when nothing was started.

### Full suite impact

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 446 passed, 1 skipped in 49.25s
```

Full-source `py_compile` clean.

Blocker for live daemon paddr measurement:

- This development host is not a deliberate privileged test host.
- DAMON sysfs is not available.
- The daemon requires root to start DAMON sessions.
- P44 lifecycle testing uses injectable fixture seams (damon_root, state_dir,
  require_root=False, is_root=...) and does not mutate host sysfs.

## P50 Mouse Table Interactions Evidence (2026-07-10)

P50 replaces the non-interactive Rich-table `Static` body with a Textual-native
`MouseTable(DataTable)` subclass supporting clickable column headers and row
drill-down.

### P50 Focused Tests

- 12 P50-specific pilot tests pass in 4.98s
- Real pilot mouse events cover header sorting/direction reversal, one-click
  row drill-down, harmless empty-row clicks, and alias-backed canonical sorting.
- Live reorder and replay refresh tests preserve a selected nonzero entity key.
- Keyboard parity covers up/down, Enter, left/right tree behavior, and harmless
  container-view tree keys.

### P50 Full Suite

```bash
cd groop && python3 -m pytest tests/ -q
# 704 passed, 1 skipped in 58.25s after P51 reconciliation
```

### P50 Key Changes

- Replaced `Static(id="body")` with `MouseTable(id="body-table")` in compose
- Removed `rich.console.Group` and Rich `Table` body rendering
- `_refresh_view` calls `_populate_table` through the production
  `format_metric_value` path; stable rows update cells in place, reordered rows
  retain columns, and header-label changes preserve native column keys.
- Sort direction tracked via `sort_reverse: bool`; header click toggles it
- Column labels show `^` (ascending) or `v` (descending) indicator on
  active column
- Keyboard: up/down/Enter handled natively by DataTable; left/right delegated
  to app for tree collapse/expand; home/end consumed by app for replay
- P41 rendered replay fidelity now explicitly compares the visible DataTable
  extraction path with the legacy production-formatted cells for original and
  replayed frames. Only the legacy two-character selection marker is omitted,
  because DataTable provides native cursor highlighting.
- Acceptance: `40 passed in 7.31s`; replay TUI smoke exited 0 with one frame.

### Degradation

Mouse support degrades harmlessly: when the terminal sends no mouse events,
the DataTable falls back to keyboard-only operation. All 23 pre-P50 tests pass
with the same key presses.

## P47 Daemon Component Health (2026-07-10)

P47 adds a thread-safe component health registry, a read-only ``health``
protocol op, and ``groop daemon health [--json]`` CLI. It models collector, BPF
snapshot bridge, and paddr lifecycle with stable states, byte-bounded redacted
public detail, and strict `health-v1` response validation.

### Fixture/unit evidence (no live daemon required)

```bash
PYTHONPATH=groop/src python3 -m pytest \
  groop/tests/test_daemon_component_health.py -q
# 49 passed in 3.47s
```

### Full suite impact

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 672 passed, 1 skipped in 51.27s (controller review)
```

Full-source ``py_compile`` clean.

### Covered scenarios

- All 7 stable states (disabled, starting, healthy, degraded, failed, stopping,
  stopped) for three tracked components.
- Thread-safe deterministic snapshots during concurrent updates and shutdown.
- Bounded error detail (no tracebacks, env vars, paths, or secrets).
- Consecutive failure tracking with reset on healthy.
- Timestamp tracking for last attempt and last success.
- Protocol health op via FrameBroker and DaemonClient.
- Strict schema, capability, component, state, field, and response-size checks;
  component errors survive the client round trip.
- CLI ``groop daemon health --json`` and ``--pretty-json``.
- Missing/corrupt socket returns exit 2 with P31-style actionable guidance.
- Default-disabled components explicitly documented and tested.
- Actual daemon-serve wiring proves collector starting/success/failure, BPF
  initial failure with/without last-valid data, and shutdown-timeout truthfulness.
- P42 BPF bridge and P44 paddr lifecycle wired into health registry transitions.

Blocker for live daemon health measurement:

- This development host is not a deliberate test host with bpftool, BPF pins,
  or DAMON sysfs.
- The daemon health registry is fixture-tested and does not require live
  daemon state.

## P51 Daemon Sampling And Fan-Out (2026-07-10)

P51 replaces request-driven collection with one background producer, bounded
sequenced history, non-consuming current/cursor reads, explicit eviction gaps,
and typed persistent failure/exhaustion/shutdown state. Production sampling
sleep is interruptible; arbitrary blocked iterators produce a bounded typed
join-timeout rather than a false clean-shutdown claim. P47 collector health is
updated only by real collection outcomes.

```bash
PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python -m pytest \
  groop/tests/test_daemon_p51.py groop/tests/test_daemon_broker.py \
  groop/tests/test_daemon_client.py groop/tests/test_daemon_component_health.py \
  groop/tests/test_record.py -q -W error
# 90 passed in 16.84s

PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python -m pytest groop/tests -q -W error
# 692 passed, 1 skipped in 53.29s
```

Full-source `py_compile` and `git diff --check` clean. This is fixture and
concurrency evidence, not a live systemd-daemon performance certification.

## Release Signoff Template

- Release/tag:
- Commit:
- v1 CPU/RSS measured:
- Packaging measured:
- DAMON measured:
- BPF measured if applicable:
- Known exceptions:
