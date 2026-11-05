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

## Start-up checks (exit 2 with `refused to start [code]: reason`, nothing changed)
Order: instance lock (`<socket>.lock`, `instance_running`), then every data directory (socket dir,
`store.tmpfs_dir`, `store.cold_dir`, the events directory). Each must be a real directory (never a
symlink: `dir_symlink`), owned by the running uid and not group/world-writable (`dir_unsafe`), and
its parent must be owned by that uid or by root and not sticky, and not group/world-writable
(`dir_parent_unsafe`; `/tmp` itself is refused). A missing leaf is created 0700, a missing parent is
an error. A symlink found inside a data directory is refused (`symlink_in_data_dir`); all files
are opened `O_NOFOLLOW` relative to the directory fd and must be regular files. `store.require_tmpfs`
(shipped config: true) refuses a `tmpfs_dir` that is not tmpfs (`tmpfs_required`); with false it
warns on stderr and in `status.store.tmpfs_dir_is_tmpfs` / `notes`. A dictionary whose count header
exceeds its lines is refused (`dict_short`). Orphan segments are deleted only when they start with
the frame magic; look-alikes are left and listed in `status.store.notes`.

## Try it on a host
Use a root-owned 0700 directory on tmpfs, a resource-limited scope, and no predictable names in
`/tmp`. Run from the worktree's `topos/` with the binary and config prepared (`sudo` needed):

    # 1. data directory: root-owned, 0700, tmpfs (/run is tmpfs on systemd hosts; check it)
    D=/run/topos-try
    sudo install -d -m 0700 -o root -g root "$D" "$D/tiers"
    findmnt -no FSTYPE -T "$D"                 # must print: tmpfs
    # 2. a cold directory, also root-owned 0700 (disk; 1 GiB cap below)
    sudo install -d -m 0700 -o root -g root /var/lib/topos-try
    # 3. config: copy the example, point every path at the two directories
    sed -e 's#^socket_path = .*#socket_path = "/run/topos-try/sampler.sock"#' \
        -e 's#^tmpfs_dir = .*#tmpfs_dir = "/run/topos-try/tiers"#' \
        -e 's#^cold_dir = .*#cold_dir = "/var/lib/topos-try"#' \
        -e 's#^events_path = .*#events_path = "/var/lib/topos-try/events.jsonl"#' \
        sampler/sampler.example.toml | sudo tee "$D/sampler.toml" >/dev/null
    sudo chmod 0600 "$D/sampler.toml"
    # 4. run it in a transient scope with the D-024 envelope (foreground; Ctrl-C stops it)
    sudo systemd-run --scope --unit=topos-sampler-try.scope \
        -p MemoryMax=128M -p MemorySwapMax=0 -p CPUQuota=25% -p IOWeight=20 -p TasksMax=32 \
        nice -n 10 /path/to/topos-sampler "$D/sampler.toml"

Check first that no `topos-sampler.service` or other sampler uses these paths (a second instance
exits at the lock and changes nothing). `sha256sum` the binary after copying it to the host and
run it from a root-owned directory, not a user-writable one.

Attach a TUI (same root-only socket, so also as root): `sudo topos --source sampler` (default
socket) or `sudo <tui> --socket /run/topos-try/sampler.sock`.

Stop and remove everything (Ctrl-C first; if it does not exit within about 25 s the hard stop
ends it):

    sudo systemctl stop topos-sampler-try.scope      # no-op if the foreground run already ended
    sudo rm -rf /run/topos-try /var/lib/topos-try

`systemd-run`, its `--scope`, `--unit=` and `-p` options and the property names were checked
against the systemd 257 man pages (`systemd-run(1)`, `systemd.resource-control(5)`); this command
was not executed on a host. `--source sampler` is the spelling given for the TUI; it does not
exist in this branch's `topos` CLI (the TUI is built elsewhere), so check `--help` of the build you attach.

## Dependencies (each at least 14 days old on 2026-11-05; `Cargo.lock` committed)
serde 1.0.229, serde_json 1.0.154, toml 0.9.8, ruzstd 0.9.1 (pure Rust, no C compiler needed).
Two transitive crates are pinned below their newest release because that was under 14 days old:
toml_writer 1.1.3 (newest 1.2.0, 2026-11-04) and winnow 1.0.4 (newest 1.0.5, 2026-11-05).

## Threads and durability
Sampling thread (reads, rates, snapshot; no disk IO), writer thread (tiers, dictionary with fsync,
events; bounded queue, oldest frame dropped and counted), command-line helper pool (3 threads, a
D-state task's `cmdline` read may hang), serving threads. Frames on disk are
`TSF1 | len | crc32 | ts_ms | payload`; a torn tail is cut at start-up. SIGTERM/SIGINT drain the
writer. Segments of tiers that are not configured are deleted at start-up. Files are mode 0600;
the socket is root-only (0600), one instance per socket (flock on `<socket>.lock`).
Panics unwind (`panic = "unwind"`): a request that panics gets an `internal` error.

## Known limits
- x86_64 uses raw `openat/read/close` syscalls (musl's `open` adds an `fcntl` per open); other
  targets use std (untested). Tested on kernel 7.2.11 only.
- Process `state`, `schedstat` and `delayacct_blkio_ticks` are per task; threads are read for
  candidates only (see the protocol document, "Threads").
- Not done from the S2 review: S-21 (sticky map clone cost), a separate OOM-event file and
  per-entity event rate limit (S-14), versioned dictionary flag (S-20). The memory-stat
  classification is explicit now.
- Command lines are redacted (`--password x`, `--token=x`, `KEY_SECRET=x`, `scheme://u:pw@host`),
  by name heuristics: a secret passed in a form without a telling name (`-p hunter2`, a bare
  positional) is not recognised. The socket therefore stays root-only.
- The sampling thread still reads some cgroup and `/proc/<pid>/io` files inline; a hang there
  shows as `status.sampling.stalled`, it is not prevented. A second SIGTERM/SIGINT exits at once
  (code 1) and the first arms a 20 s hard stop.
- Process series are not stored, only served in `current`; counters of cgroup-level `io.max` are
  reduced to a "capped" flag.
- Entity ids are reclaimed only after the longest tier's retention; metric names are never collected
  (bounded by the kernel's key set).
- The CPU/RSS under the D-024 envelope and at 500 cgroups / 5,000 PIDs is unmeasured
  (backlog TOP-SAMPLER-ENVELOPE).
