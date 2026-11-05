# topos hot-sampler PoC (Rust) and its lean-Python yardstick

Proof of concept for the topos redesign: a Rust process that owns collection, tiered
history, hard events and a Unix-socket query API, measured against a lean Python
equivalent on real hosts. Design inputs: the shared DESIGN-BRIEF; kernel semantics follow
`src/topos/collect/{cgroup,collector,host}.py` and `src/topos/procs/procfs.py`.
Measured results and verdict: `SAMPLER-POC.md` (kept in the review scratchpad, not in the repo).

## What it does
- Per tick, no process spawns: walks `cgroup_root`; per cgroup reads cpu.stat, memory.current,
  memory.stat, memory.events, io.stat, cpu/memory/io.pressure, pids.current; limit files
  (memory.min/low/high/max/swap.max, cpu.max/weight, io.max/weight) every `slow_every_s`,
  spread across cgroups by a serial number so there is no 60 s spike.
- Host: `/proc/stat` per CPU plus procs_running/blocked, meminfo, vmstat, loadavg,
  diskstats with await and util, root io.stat `cost.vrate`.
- Process scan over all PIDs (stat, io, schedstat; identity = pid + start time) with a bounded
  enriched union: all D state, then top CPU and top IO interleaved, hard cap.
- io.stat totals count only bottom-level devices (`/sys/dev/block/<maj:min>/slaves` empty).
- Counters become deltas with reset handling (decrease = restart, reading is the delta);
  dt comes from the monotonic clock.
- Tiers from TOML, no defaults in code; hot and warm segments in a tmpfs directory; cold zstd
  frames (pure-Rust `ruzstd`) on disk with a byte cap, oldest segment removed first.
  Gauges roll up to avg/max/min/p95 (stored as `m`, `m:max`, `m:min`, `m:p95`), counters to sums.
- Hard events (JSON lines in `events_path`, wall and monotonic timestamps): `oom_kill`,
  `cgroup_vanished`, `limit_changed`, `psi_cross` (up and down, per resource threshold).
- Socket (JSON lines): `{"req":"current"}` and
  `{"req":"series","entity":"cg:/x","metric":"mem.current","tier":"hot","window_s":60}`.
  Metric `@events` on `series` returns the events in the window.

## Files
- `src/` Rust sources (`collect.rs` kernel reads, `store.rs` tiers and queries, `serve.rs` socket).
- `sampler.example.toml` every key is required. `Cargo.toml` / `Cargo.lock` (committed).
- `Dockerfile` builds a static musl binary; `lean_python_sampler.py` the yardstick (stdlib only).
- `bench.sh` one measurement run on a host; `crosscheck.py` the hand cross-check.

## Build (no toolchain in the cockpit)
    docker build -t topos-sampler-poc-build .
    docker create --name topos-sampler-poc-extract topos-sampler-poc-build
    docker cp topos-sampler-poc-extract:/topos-sampler-poc ./topos-sampler-poc
    docker rm topos-sampler-poc-extract
The binary is not committed. Run: `./topos-sampler-poc sampler.example.toml`.

## Dependencies (each at least 14 days old on 2026-11-05)
serde 1.0.229, serde_json 1.0.154, toml 0.9.8, ruzstd 0.9.1 (pure Rust; no C compiler needed).
Two transitive crates are pinned below their newest release because that was under 14 days
old: toml_writer 1.1.3 (newest 1.2.0, 2026-11-04) and winnow 1.0.4 (newest 1.0.5, 2026-11-05).

## Known limits of the PoC
- x86_64 uses raw `openat/read/close` syscalls (musl's `open` adds an `fcntl` per open).
- Zero counter deltas are not stored; a missing counter in a stored entity reads as 0.
- Process series are not stored, only served in `current`. Page size 4096 and CLK_TCK 100 assumed.
- Dictionary (entity and metric ids) only grows; there is no id garbage collection.
- A single segment is never deleted for the byte cap, so the cap can be exceeded by one segment.
- Python yardstick has no events, tiers, socket or compression (that is the "lean" definition).
