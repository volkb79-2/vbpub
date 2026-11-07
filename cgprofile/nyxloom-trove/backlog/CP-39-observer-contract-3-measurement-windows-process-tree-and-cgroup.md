---
kind: backlog-entry
schema_version: 1
id: CP-39
title: "Observer contract 3: per-subject measurement windows (process-tree and cgroup mode) as the single source of per-candidate and per-worker resource numbers"
status: fixed
type: "feature"
component: "resource-observer"
context_estimate: "large"
provenance: "Operator decisions 2026-11-03 (final interview: cgprofile observer is the single source of per-candidate/per-worker numbers; no fallback reader); cross-tool design docs/design/RESOURCE-PROFILES.md sec 2-3"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler resource observer"
priority: 1
filed_date: "2026-11-03"
---

## Problem

Observer contract 2 (`docs/RG55-INTERFACE-CONTRACT.md` §9) answers one read-only `resource-limits` query: the limits
and event counters of the peer's cgroup ancestry. It returns no resource numbers.

- **Per-candidate numbers today.** Assay samples `/proc` itself to get its per-candidate CPU, RSS and write bytes, and
  has no IO operation counts or PSI.
- **Per-worker numbers.** Run-gate has none for pytest-xdist workers.
- **The Summary.** The control-protocol Summary (contract 1, schema 1) covers a whole lane session. It has no `io.stat`
  (CP-29), and it cannot attribute numbers to a subject inside a shared container.

The operator decided that the cgprofile observer is the single source of these numbers. Without it, consumers record
an explicit `NO_MEASUREMENT`; they never read the files themselves.

## Decision record

- **Operator, 2026-11-03:** a single source, no fallback reader in consumers, no duplicated functionality.
- **Design choices made here:**
  - two modes, because exact cgroup numbers need an exclusive leaf, which jobs>1 does not have until CP-23;
  - additive contract 3 on the existing observer socket, with contract 2 kept for every 1.x release;
  - release as cgprofile 1.2.0.

## Design

**Wire.** These are new verbs on `/run/cgprofile/observer/resource-observer.sock`, with one newline-delimited JSON
request per connection and the existing 4096-byte and 5-second limits.

```json
{"verb":"window-open","args":{"thread_id":T,"root_pid":P,"mode":"process-tree"|"cgroup","interval_ms":250},"contract":3}
→ {"ok":true,"contract":3,"window":"w-<128-bit hex>","mode":"…","started_at":"…","kernel":{"peak_reset":true|false}}
{"verb":"window-close","args":{"thread_id":T,"window":"w-…"},"contract":3}
→ {"ok":true,"contract":3,"window":"w-…","metrics":{…},"missing":{…},"sources":{…},"context":{…},
   "attribution":{"mode":"…","foreign_pids_max":0,"tree_pids_max":7,"samples":389,"baseline_bytes":…}}
```

- **Metric keys**, defined by `resource-profile/1` in the design §2 and always all present:
  - `rss_peak_bytes`, `memory_peak_bytes`, `swap_peak_bytes`;
  - `cpu_seconds`, `cpu_user_seconds`, `cpu_system_seconds`;
  - `io_read_bytes`, `io_write_bytes`, `io_read_ops`, `io_write_ops`;
  - `psi_memory_some_seconds`, `psi_memory_full_seconds`, `psi_cpu_some_seconds`, `psi_io_some_seconds`,
    `psi_io_full_seconds`.

  Each null has its reason code in `missing`. `context` holds the containing cgroup's PSI and local `memory.events`
  deltas, the ancestors' local `memory.events` deltas up to `dev.slice` (CP-40), and the host's `/proc/pressure`
  deltas.
- **Binding.**
  - The peer's PID and UID come from `SO_PEERCRED`, with the TID proof of §9.2.
  - `root_pid` is mapped through `NSpid`. It must be the peer or a live descendant of it (parent chain), in the peer's
    cgroup or below, with its start time rechecked.
  - Windows belong to the opening peer. A close from another peer gets `peer-refused`, and an unknown window gets
    `bad-argument`.
- **`process-tree` mode** (works in shared containers):
  - Every interval it walks the descendants of `root_pid` and records the summed RSS (`/proc/<pid>/statm`), the source
    `sampled-sum-rss`.
  - IO bytes are the summed `/proc/<pid>/io` `read_bytes/write_bytes`, keeping exited processes' last values, labelled
    `proc-io-lower-bound`.
  - CPU is the root's `utime+stime+cutime+cstime` read at close, labelled:
    - `proc-stat-exact` when the root is an unreaped zombie;
    - `proc-stat-live` when the root is still running, which is the self-rooted window of the pytest worker plugin, exact up to the moment of close;
    - otherwise `proc-stat-sampled`.
  - IOPS, PSI and the memory, swap and cgroup peaks are `REQUIRES_CGROUP_MODE`.
- **`cgroup` mode** (exact):
  - `memory.peak` and `memory.swap.peak` come from file descriptors opened at window-open and reset by writing to that
    open file, which requires per-file reset (Linux ≥ 6.12). Without it they are `PEAK_RESET_UNSUPPORTED`, and the
    daemon's own `sampled-max` may be reported with that source label.
  - `cpu.stat` usage delta; `io.stat` `rbytes/wbytes/rios/wios` summed over devices (`CONTROLLER_DISABLED` without the
    io controller); PSI totals.
  - `rss_peak_bytes` is sampled as in process-tree mode. `baseline_bytes` = `memory.current` at open.
  - `foreign_pids_max` counts `cgroup.procs` members that are none of: the root's tree, the peer, or the peer's own
    ancestors within the same leaf (the container's init and the wrapper shell, which are idle while they wait). Any
    foreign PID makes every cgroup-only metric `SHARED_CGROUP`.
  - The idle peer and ancestors are covered by `baseline_bytes`.
- **Bounds.**
  - At most 64 open windows per peer, and 4096 per daemon (`window-refused:limit`).
  - A window expires after 6 h; a late close gets `WINDOW_LOST`.
  - `interval_ms` is 100–5000.
  - Sampling runs in the daemon's own thread, never synchronously in `window-close`, which reads the last state and the
    final counters within the 5 s deadline.
  - Memory per window is bounded: no sample series is kept, only running maxima and sums.
  - All sampling threads are bounded. This depends on the serial accept loop: the window verbs must not block other
    peers, which is CP-33 (concurrent accept), a prerequisite.
- **Daemon restart.** Open windows are lost, and consumers get `WINDOW_LOST` on close. No window state is persisted.
- **Version.** `version.resource_observer` gains `"contracts":[2,3]`. `"contract"` stays 2 for 1.x compatibility.
- **Fixtures.** `fixtures/rg55/observer-window-v3-open.json`, `…-close-process-tree.json`, `…-close-cgroup.json` and
  `…-close-shared.json`, vendored byte-identical into the assay and run-gate tests.
- **Documentation.** CONSUMERS, PROTOCOL and the compatibility matrix describe contract 3 before release.

## Acceptance criteria

- **Fake cgroupfs and procfs fixtures:**
  - cgroup mode returns exact deltas;
  - `memory.peak` reset works per file, and `PEAK_RESET_UNSUPPORTED` appears on a fake kernel without it;
  - `SHARED_CGROUP` appears exactly when a foreign PID appeared during the window;
  - process-tree mode counts the IO of an exited child (lower bound) and reports exact CPU for a zombie root.
- **Refusals.** A foreign `root_pid` (not a descendant), PID reuse (start-time mismatch), a close from another peer, an
  unknown window, more than 64 windows, and an expired window are each refused with the documented code.
- **Concurrency.** A slow window does not delay another peer's `resource-limits` answer (with CP-33).
- **Compatibility.** The contract-2 golden fixtures are unchanged and pass. A contract-2 client against 1.2.0 sees no
  difference.
- **Live.** Design §9 M1 on r1002: process-tree CPU is within 1% of cgroup mode, the RSS ratio is recorded, and
  kernel peak-reset support is recorded.

## Test plan

- Unit tests on fake trees for every metric and refusal.
- Socket integration tests with a real daemon in the cgprofile gate.
- The vendored fixture tests run in assay (B206) and run-gate (RG-145/RG-146).
- Live calibration M1.

## Dependencies and order

- **Before it:** CP-33 (concurrent accept loop) and CP-29 (io collectors, shares the `io.stat` reader).
- **This item:** cgprofile 1.2.0.
- **After it:** assay B206, run-gate RG-146 (the worker plugin).
- **Exactness at jobs>1** needs CP-23 (per-candidate leaves). Until then, jobs>1 uses process-tree mode, and exact
  PSI and IOPS come from jobs=1 baselines.
- **Note to add to CP-23:** a per-candidate leaf makes cgroup-mode windows exclusive at jobs>1. That is the payoff of
  CP-23 for resource attribution, in addition to the limit-event attribution it was filed for.

## Updates

**2026-11-03** — Implemented in branch fan-cgp-20261103 (lib/observer_windows.py, resource_observer.resolve_window_peer, serve.py dispatch, fixtures observer-window-v3-*.json, docs PROTOCOL section 9 and CONSUMERS). Unit suite: 2752 passed, coverage 100% line and branch. Pending: review, the registered r0-r1 gate on the exact tip, release as 1.2.0, and the live M1 calibration on r1002 (process-tree CPU vs cgroup mode, RSS ratio, kernel peak-reset support), which has NOT been run. CP-33 (concurrent accept) is not implemented: window sampling is on its own thread and the window verbs are bounded, but the observer accept loop is still serial for slow peers. CP-29's io.stat reader is not shared yet; the window code carries its own.

FIXED 2026-11-04 (2d1f0a6cf, merged to main; delta review fixes in fix-cgp-20261104c). Pre-release delta review: a child forked between the round's process scan and the `cgroup.procs` read no longer counts as foreign (live parent-chain check, `_descends_from`). The live M1 calibration on r1002 is still outstanding and belongs to the release record.
