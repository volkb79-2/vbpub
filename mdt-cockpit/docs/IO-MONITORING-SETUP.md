# IO monitoring setup (sampler + eBPF collector)

Sibling of `IO-GOVERNOR-SECOND-OPINION-2026-10-27.md` and `IO-GOVERNOR-DESIGN.md`. It documents a
read-mostly monitor built to watch **two production game-server containers on a shared host** for
several hours while players are connected, and the reasoning behind its shape. Nothing here names a
host, server or container; all identifiers are parameters.

Code: `scripts/iomon.py` (sampler, `analyze`, `stop`), `scripts/iomon_bpf.py` (in-container
supervisor), `scripts/iomon-bpf/{Dockerfile,run.sh,blk.bt,stall.bt,sync.bt}`, tests in
`tests/test_iomon.py`.

## Why two collectors

| Source | Visible from the devcontainer | Visible only from the host / a root container |
|---|---|---|
| `/proc/diskstats`, `/proc/pressure/*`, `/proc/meminfo`, `/proc/vmstat`, `/sys/block/*/queue` | yes (host-global) | - |
| Docker Engine stats for an exact container ID (CPU, throttling, memory, `pgmajfault`, refaults, block bytes per device) | yes, via the daemon socket | - |
| Per-container `io.stat` (incl. iocost `cost.*`), `io.pressure`, `memory.pressure`, `memory.events`, `io.max`, `io.cost.qos/model`, `cost.vrate` | **no** (private cgroup namespace) | yes (`/sys/fs/cgroup` bind, read-only) |
| Per-request latency distributions, tail latency, per-cgroup attribution | **no** (needs tracepoints) | yes (bpftrace) |
| D-state stacks, fsync/write() latency, balance_dirty_pages, swap, iocost tracepoints, syscall counts | **no** | yes |
| `/proc/<hostpid>/maps` of another container's processes | no | yes (root + `--pid=host` + `CAP_SYS_PTRACE`) |

So the monitor is two layers. The **sampler** (`iomon.py run`) is a stdlib Python process in the
devcontainer: ~0.1% of one core, nice 10 and idle IO class, it writes JSONL and also runs a tiny
active probe. The **eBPF collector** is one extra container that exists only because the first layer
cannot see per-cgroup or per-request behaviour.

### Why not `host-escape`

`host-escape` runs the mdt doctor repair as a side effect, which can remount cgroup2 on a production
host. A dedicated, named, read-only-mounted observation container reaches the same signals without
touching host state, so `host-escape` is avoided for observation.

### Why root, capabilities and a cgroup parent

* **Root** inside the container: loading BPF programs and reading other users' `/proc` entries.
* **Capabilities** instead of `--privileged`: `SYS_ADMIN, BPF, PERFMON, SYS_RESOURCE, SYSLOG`
  (kernel stacks, kallsyms) and `SYS_PTRACE` (read-only `/proc/<pid>/maps`). `--pid=host` is needed
  because bpftrace's `pid/tid` helpers fail with EINVAL (and flood the output) for tasks outside the
  container's PID namespace.
* **`--cgroup-parent=dev-background.slice`** (`$CGROUP_PARENT_DEV_BACKGROUND`): the collector is a
  governed workload like the builds and gates, so it can never outrank the game servers, and it is
  visible in the same accounting the governor reasons about. It also has `--cpus 1 --memory 1g
  --pids-limit 256` and runs `nice -n 10`.
* Tracefs, debugfs, BTF and the host cgroup tree are **read-only** binds; the root filesystem is
  read-only (tmpfs `/tmp`, working directory `/tmp`: bpftrace crashes if its cwd is unwritable); the
  only writable bind is the output directory.

### Safety envelope

* **One container**, fixed name (`iomon-bpf-20261027` by default), `--rm`, started by `run.sh`,
  wrapped in `timeout` (8 h + margin) and the supervisor's own `--max-hours`. Removal is by exact
  name only.
* The watched containers are only ever resolved with `docker inspect --format '{{.Id}}' <exact>`
  and polled through the stats endpoint. They are never stopped, restarted, exec'd into or modified.
* **Overhead budget**: sampler ~0.1% CPU, 27 MB RSS, ~20 KB/min of JSONL; bpftrace 1-5% of one core
  (0.8% average, 4% peak in the quiet window; ~5% average / 21% peak during a busy window), all
  aggregation in kernel with maps printed every 30 s and thresholded per-event output
  (`write()`/fsync > 50 ms, D-state > 100 ms). Output is size capped (sampler 200 MB, eBPF 80 MB;
  actual use is ~10 MB and ~30 MB over 8 h).
* The active probe is the only IO the monitor issues: 4 KiB `O_DSYNC` write plus 4 KiB `O_DIRECT`
  read every 10 s on a preallocated 1 MiB file (about 0.8 KiB/s), file removed on exit. Its thread
  raises itself from the idle IO class to best-effort 7, because mq-deadline honours IO priority and
  an idle-class probe would measure starvation instead of what an ordinary service sees.

## Start, stop, analyze

```
# build the collector image once (tiny delta over the devcontainer Python/Debian base, no hub pull)
docker build --load -t iomon-bpf:20261027 scripts/iomon-bpf

# sampler (devcontainer, detached)
setsid nohup nice -n 10 ionice -c3 python3 scripts/iomon.py run --out OUT \
    --container ID --container ID --probe-dir /tmp/iomon-probe --max-hours 8 &

# eBPF collector (output must be on a path that is also a host bind; /tmp is)
scripts/iomon-bpf/run.sh --hours 8 ID_OR_NAME ID_OR_NAME
ln -s /tmp/iomon-bpf-20261027 OUT/bpf          # lets `analyze` and `stop` find it

python3 scripts/iomon.py analyze --out OUT --first 10m     # or --last 30m, --since/--until ISO
python3 scripts/iomon.py stop    --out OUT                 # writes OUT/STOP and OUT/bpf/STOP-BPF
```

`analyze` prints per-minute device and per-container tables, flagged stall episodes (latency above
N x median, PSI full spikes, throttling, probe outliers), cross-container correlation, and then the
eBPF sections (1)-(8) when `bpf-*.jsonl` exists.

## How to read the signals

* **await vs tail latency.** `/proc/diskstats` yields only an interval mean (`Δms/Δios`); a 100 ms
  stall among thousands of 1 ms requests vanishes in a 5 s mean. The per-minute p50/p95/max are
  percentiles *of interval means*, not of requests. Real tail latency comes from the eBPF log2
  histograms (`blk.bt`: upper bucket bounds, device service time from start to end of the request)
  and the O_DSYNC probe (ordinary-priority, ~6 samples/min).
* **PSI some/full.** `some`: at least one task stalled; `full`: all non-idle tasks stalled. The
  sampler converts `total` deltas to percent of the interval. IO and memory PSI rising together with
  low device latency means the stall is reclaim/swap, not the disk being slow.
* **Swap-in and major faults as the stall driver.** `pgmajfault`, `workingset_refault_anon`,
  `pswpin/out` and the eBPF `swap_read_folio` counts show a game server paging its own memory in;
  a major-fault burst in a game container that lines up with a host PSI-full episode is the
  signature of a swap-driven stall. Per-cgroup swap-out cannot be attributed from a hook (the folio's
  cgroup is gone by then); use `memory.swap.current` / `zswpout` deltas from the cgroup sampler.
* **D-state and fsync/write histograms.** `stall.bt` records every uninterruptible sleep of the
  watched tasks (histogram) and, above 100 ms, the kernel stack aggregated by comm. The stack says
  what the task waited on (tag allocation, `balance_dirty_pages`, journal, swap-in).
* **iocost.** `@vrate/@busy/@rqwait_pct` come from `iocost_ioc_vrate_adj`. `vrate` pinned at the
  QoS floor with `rq_wait_pct` > 5% in a third of the periods is the signature discussed in the
  governor doc: the deliberate small `nr_requests` makes iocost see saturation, so a floor `vrate`
  is not a capacity signal on this host. The tag-wait histogram (`blk_mq_get_tag`) measures the
  slot starvation directly. `io.max` binding is judged by comparing the capped slice's achieved
  rate (cgroup `io.stat` deltas) with its limit; `cost.wait/usage` deltas show iocost admission.
* **Who else writes.** Bytes are attributed to the *owner* cgroup of the bio (blkg), not to the
  flusher kworker that submitted them; the submitting comm (`jbd2`, `kworker`, `python3`, `git`) is
  reported separately. Cgroup ids are resolved from a periodic id-to-path map.
* **Durability syscalls.** `sync.bt` counts `fsync`, `fdatasync`, `sync_file_range`, `msync`,
  `syncfs` and `O_SYNC/O_DSYNC` opens per watched cgroup and comm, plus their latency; the
  supervisor additionally reports, per process in each watched scope, whether a mapping whose name
  contains `eatmydata` or `ksm-optin` exists (name match only, mappings are never printed).

## Blind spots and sampling limits

* 5 s sampling can miss sub-5 s stalls in the sampler; the eBPF layer is event-complete but is
  printed in 30 s windows (timestamps are the print time).
* Device latency is service time at the block layer, not what the application sees; time spent in
  `balance_dirty_pages`, journal waits and page-cache effects are only visible through (2), (3), (5).
* Container block bytes from the Docker stats endpoint have no IOPS or latency; use the cgroup
  `io.stat` deltas from the eBPF container.
* Log2 histograms give factor-2 resolution; percentiles are bucket upper bounds.
* Latency tables include only the `vda` request queue: device-mapper volumes are bio-based and have
  no request-level data of their own (their load shows up on the underlying disk).
* The active probe and the sampler's own writes are IO on the watched device (tiny, but nonzero);
  the filesystem backing the game volumes could not be confirmed from inside the devcontainer, only
  that it shares the same physical disk.
* The sync-flag classification of a request relies on the kernel's request flag bit layout; treat
  the "sync" percentage as indicative.
* Detection thresholds in `analyze` (5 x median, 10 ms floor, PSI full 5%) are heuristics.

## How it feeds the governor's open questions

* *What is the game servers' IO?* eBPF (1)/(6) and the cgroup `io.stat` give IOPS, size mix, sync
  share and owner-attributed bytes; (7) gives swap traffic.
* *What do long stalls wait on?* (2) stacks and (3)/(5) histograms, combined with the sampler's
  PSI and major-fault series, separate tag wait, writeback throttling, journal and swap-in.
* *Is iocost admission or `io.max` binding?* (4): `vrate`, `rq_wait_pct`, missed-latency ppm,
  shortages, per-cgroup `cost.usage`, and `io.max` rates versus limits. Whether the *ceiling*
  moved (the ~20x capability shifts) is a long-horizon question: the throughput ceiling is visible
  only when the workload pushes against it, so this monitor records the evidence (latency under
  load, `vrate`, device util) rather than estimating the ceiling.

## Findings so far (about 40 minutes of data, players connected)

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
