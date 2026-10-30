# IO monitoring: findings from the first run (2026-10-27)

Measured results from the monitor described in `IO-MONITORING-SETUP.md`. Deliberately names no
host, server or container; the set-up document stays host-agnostic and this file is the dated
record. About 40 minutes of data, players connected.

* Device level: write await p50 about 1.3 ms, interval maximum 9 ms; read about 0.3 ms; util
  mostly 5-17%, peaks about 35%; in-flight peaks about 23 with `nr_requests=16` / mq-deadline.
  eBPF service time for the game servers: reads p50 <= 255 us, p99 <= 1 ms; writes p50 <= 1 ms,
  p99 <= 4-8 ms; no request over 100 ms from the game cgroups. One 120 ms probe outlier.
* The game servers' own disk IO is small (a few to ~110 read IOPS and ~25 write IOPS, under
  1 MB/s); most writes on the device come from other cgroups (an interactive dev container
  about 3 MB/s, journal commits about 0.9 MB/s, a gate, buildkit, journald).
* The dominant stall signal is **memory/swap, not disk**: host IO and memory PSI `full` spike
  together (peaks 20-47%, one 4-minute episode) while device latency stays low; at those times the
  game servers show major-fault bursts of thousands per second and swap-in; the host swaps
  continuously (reclaim by `kswapd0`; one server's cgroup holds about 8 GiB in swap, the other
  about 7 GiB).
* No D-state episode over 100 ms and no slow (>50 ms) `write()` from the game processes in the
  eBPF windows so far; their D-state wakeups are mostly 64-255 us with p99 about 2 ms.
* iocost: `vrate` sat at the QoS floor (50) throughout; adjustments were all "no change";
  `rq_wait_pct` exceeded 5% in roughly a third of the periods, and iocg activations churn
  (about 1700-2000 per five minutes) with some inuse shortages and debt forgiveness. No `io.max`
  applies to the game scopes; the governed slice's `io.max` was nowhere near binding (about
  2 MB/s write against a 250 MB/s limit).
* Writeback throttling (`balance_dirty_pages` pauses) occurred for a dev container, a few hundred
  ms per five minutes; none for the game servers.
* **eatmydata check**: with an `EATMYDATA` environment on both servers, runtime evidence differs.
  The process of one server maps both preload libraries and issued zero durability syscalls and
  zero O_SYNC opens over several minutes. The other server maps only the `ksm-optin` library, not
  `eatmydata`, and its log writer thread issues `fdatasync` regularly (about 72 in 5.5 minutes, each
  completing in under 32 us because it writes almost nothing). The configured intent is therefore
  not realised for that server. Other processes on the host issue fsync/fdatasync normally
  (python, gk, postgres, dockerd, git, a JS runtime), and `runc` opens files with O_DSYNC.
