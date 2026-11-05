# topos-sampler

The Rust hot sampler of topos (D-026): it owns collection (cgroup tree, host files, process
scan), counter-to-rate conversion, the three history tiers (D-025), hard events (D-027) and the
Unix-socket API. Python keeps query, report, MCP, actions and squeeze, and talks to it over
the versioned protocol in `../docs/SAMPLER-PROTOCOL.md`. Envelope: D-024 (`topos.slice`).

## Layout
- `src/collect.rs` cgroup and host reads, typed value states; `src/procs.rs` staged process scan;
  `src/store.rs` tiers, rollups, series and events queries; `src/serve.rs` socket protocol;
  `src/selfmon.rs` the sampler watching itself and the cgroup-namespace view; `src/dict.rs`
  interned names with entity id garbage collection; `src/cfg.rs` configuration (every key
  required, validated); `src/tests.rs` unit and protocol tests.
- `sampler.example.toml` production-shaped configuration. `Dockerfile` builds and tests in
  containers only (no Rust toolchain in the cockpit). `bench.sh` is the host measurement
  harness used for `../MEASUREMENTS.md`. `../tools/poc/` keeps the retired PoC yardsticks.

## Build and test (from `topos/`)
    docker build -f sampler/Dockerfile --target test  -t topos-sampler-test .     # cargo test
    docker build -f sampler/Dockerfile --target artifact -o type=local,dest=sampler .   # sampler/topos-sampler
    TOPOS_SAMPLER_BIN=sampler/topos-sampler python -m pytest tests/test_sampler_conformance.py
The binary is not committed (`.gitignore`). `topos-sampler --once <config>` samples twice and prints the
`current` response (the single collector for `topos --once`, no socket, no store).

## Dependencies (each at least 14 days old on 2026-11-05; `Cargo.lock` committed)
serde 1.0.229, serde_json 1.0.154, toml 0.9.8, ruzstd 0.9.1 (pure Rust, no C compiler needed).
Two transitive crates are pinned below their newest release because that was under 14 days old:
toml_writer 1.1.3 (newest 1.2.0, 2026-11-04) and winnow 1.0.4 (newest 1.0.5, 2026-11-05).

## Known limits
- x86_64 uses raw `openat/read/close` syscalls (musl's `open` adds an `fcntl` per open); other
  targets use std (untested). Tested on kernel 7.2.11 only.
- Process `schedstat` and `delayacct_blkio_ticks` are the main thread's, not the process total.
- Process series are not stored, only served in `current`; counters of cgroup-level `io.max` are
  reduced to a "capped" flag.
- Entity ids are reclaimed only after the longest tier's retention; metric names are never collected
  (bounded by the kernel's key set).
- The CPU/RSS under the D-024 envelope and at 500 cgroups / 5,000 PIDs is unmeasured
  (backlog TOP-SAMPLER-ENVELOPE).
