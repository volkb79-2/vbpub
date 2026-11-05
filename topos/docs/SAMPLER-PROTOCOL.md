# topos-sampler wire protocol, version 1

The contract between `topos-sampler` (Rust, `topos/sampler/`) and its clients: the Python
CLI/MCP/report code and both TUIs. Decision trail: D-024 (envelope), D-025 (tiers), D-026
(Rust owns collection), D-027 (hard events). Implemented by `sampler/src/serve.rs`; tested in
`sampler/src/tests.rs`; semantics cross-checked against the Python collector by
`tests/test_sampler_conformance.py`.

## Transport

- Unix stream socket, path from `sampler.socket_path` (production: `/run/topos/sampler.sock`),
  mode 0600. One JSON object per line (`\n` terminated), request and response alike.
- A connection serves requests sequentially and stays open across errors. Idle connections
  are closed after `protocol.client_idle_ms`; a client that stops reading is dropped after
  `protocol.client_write_ms`. At most `protocol.max_clients` connections; the next one gets a
  `too_many_clients` error and is closed. A slow or stalled client never blocks sampling.
- A request line over `protocol.max_request_bytes` gets `request_too_large` and the
  connection is closed.

## Envelope

Request: `{"req":"<name>", "id": <string|number, optional>, ...}`. Response:

    {"ok":true,  "req":"<name>", "id":<echo>, "protocol":1, "epoch":"<str>", ...fields, "truncated":false}
    {"ok":false, "id":<echo>, "protocol":1, "epoch":"<str>", "error":{"code":"<code>","message":"<text>"}}

- `epoch` is unique per sampler start (start time, pid, sequence). A client that sees the
  epoch change knows the sampler restarted: counters, ids and gaps are not comparable across
  it. Every response carries it.
- Error codes: `bad_json`, `bad_request`, `unknown_req`, `unknown_tier`, `unknown_entity`,
  `unknown_metric`, `store_disabled`, `request_too_large`, `too_many_clients`.
- Every successful response is bounded by `protocol.max_response_bytes` (reported in
  `hello.limits`). When the answer had to stop early, `truncated` is `true`; the data present
  is complete and valid JSON (entities, procs, points or events are dropped whole, never cut).
  `truncated:false` means nothing was left out by the cap.

## Value states

No value is ever a silent zero. A metric value is an object with `s`:

| `s` | meaning | fields |
|---|---|---|
| `value` | measured | `v`; counters also `rate` (per second, over `dt_ms`) and `raw` (kernel reading) |
| `not_applicable` | does not exist here, expected | `why` |
| `unavailable` | should exist, could not be obtained | `why`; `first_sample` states also carry `raw` |

`why` values (code in parentheses, listed in `hello.state_codes`; the table is append-only):
`no_activity_in_interval` (1: e.g. await on a device with no IO), `controller_or_file_absent`
(2), `feature_absent` (3: no KSM, no io.cost, no zswap), `task_delayacct_off` (4),
`no_full_line` (5), `permission_denied` (6), `read_error` (7), `debugfs_unreadable` (8),
`parse_error` (9), `first_sample` (10: a counter without a previous reading, so no delta yet),
`not_read_this_tick` (11: process io/schedstat not read between sweeps).

A zero delta is `{"s":"value","v":0,...}`. A metric that was not measured in a tick is absent
from that tick (series skip it; gaps are visible by timestamp), never zero. A counter that
decreases is a restart: the reading becomes the delta and `counter_resets` increments.
Limit values use `-1` for `max` (unlimited). Percentages are 0..100.

## Entities and metrics

Entity names: `cg:/<path>` (every cgroup under `cgroup_root`, `cg:/` the root), `host`,
`cpu:all`, `cpu:<n>`, `dev:<name>`, `sampler`. Metric names are stable strings; counters are
reported as deltas. Main groups:

- `cg:*`: `mem.current`, `mem.*` (memory.stat keys; `pg*`, `thp_*`, `zswp*`, `workingset_*`
  are counters), `memev.*` (memory.events counters), `cpu.usage_usec` and the rest of cpu.stat
  (counters), `io.rbytes|wbytes|rios|wios|dbytes|dios` (bottom-level devices only: stacked dm/md
  devices with a non-empty `slaves` are excluded), `io.cost.usage|wait|indelay` (counters,
  `not_applicable` when io.cost is off), `io.cost.vrate` (root), `psi.<cpu|memory|io>.<some|full>.avg10`
  (gauge) and `.total` (stall counter), `pids.current`, `procs.d_state` (D-state tasks whose
  owning cgroup is this one; `procs.d_state_unattributed` on the root counts those beyond the
  attribution bound), and the limits `limit.memory.{min,low,high,max,swap.max,zswap.max}`,
  `limit.cpu.max.{quota,period}`, `limit.cpu.weight`, `limit.io.max.capped` (1 if any device is
  capped), `limit.io.weight`. Limits are read every `slow_every_s`, spread across cgroups, and are
  sticky in `current`. The root cgroup's PSI comes from `/proc/pressure/*`.
- `cpu:*`: counters `cpu.{user,nice,system,idle,iowait,irq,softirq,steal}` (jiffies) and gauges
  `cpupct.{us,sy,io,st,irq,si,id}` (us = user+nice), `cpu.busy_pct`.
- `host`: `host.{procs_running,procs_blocked,load1,load5,load15}`, counters `host.ctxt`,
  `host.forks`, `meminfo.*` (bytes), the stacked breakdown `memstack.{anon,file_cache,shmem,buffers,
  slab_reclaimable,slab_unreclaimable,kernel_stack,pagetables,percpu,zswap_pool,free,other}` (sums
  to MemTotal), vmstat counters `vm.*` (major faults `vm.pgmajfault`, reclaim `vm.pgscan_*`,
  `vm.pgsteal_*`, `vm.allocstall_normal`, swap, zswap, workingset), zswap `zswap.*`
  (debugfs counters incl. `reject_*`, `pool_limit_hit`, `written_back_pages`; `zswap.debugfs_ok` is 1
  or `unavailable debugfs_unreadable`, in which case `zswap.pool_bytes` and `zswap.stored_bytes` fall
  back to meminfo `Zswap`/`Zswapped`), `zswap.enabled`, `zswap.max_pool_percent`, and KSM `ksm.*` from
  `/sys/kernel/mm/ksm` (`not_applicable feature_absent` when absent).
- `dev:*`: `disk.*` counters, `disk.inflight`, `disk.await_ms`, `disk.r_await_ms`, `disk.w_await_ms`
  (`not_applicable no_activity_in_interval` when no IO of that kind), `disk.util_pct`, `disk.aqu_sz`.
- `sampler`: `sampler.work_us`, `sampler.dt_ms`, `sampler.rss_bytes`, `sampler.cpu_ticks`,
  `sampler.overruns`, `sampler.skipped_ticks`. Stored in the tiers like any metric, so collection
  gaps are visible historically.

CLK_TCK and the page size come from `sysconf`; `hello` reports them and their source.

## Requests

### `hello`
Returns `schema` (metric/field schema version, 1), `version`, `pid`, `started_ms`, `tick_ms`,
`clk_tck`, `clk_tck_source` (`sysconf`|`fallback`), `page_size`, `page_size_source`, `limits`
(`max_request_bytes`, `max_response_bytes`, `max_clients`), `process` (scan configuration),
`tiers` (name, step_s, keep_s, segment_s, medium), `requests`, `state_codes`, and
`cgroup_view`.

`cgroup_view` says whether the sampler sees the whole host tree or only a cgroup-namespaced slice
(running in a container): `{"mode":"host"|"namespaced"|"unknown","because":"...","self_path":"/..",
"cgroup_root":"/sys/fs/cgroup"}`. `namespaced` when the sampler's own cgroup path is `/` or its cgroup
namespace differs from PID 1's (when visible); the heuristic and its evidence are reported, not
hidden. In a namespaced view, entity paths are relative to the namespace root and host totals
(`cpu:*`, `host`, `/proc`) may still be the container's view of the host: clients must show the mode.

### `current`
`{"req":"current","prefix":"cg:/system.slice","procs":true}` (both optional). Latest tick:
`tick`, `ts_ms`, `dt_ms` (measured), `work_us`, `cgroups`, `counter_resets`, `pids`
(`total`, `d_state`, `io_denied`, `io_read` = pids whose io/schedstat were read this tick,
`full_sweep`, `d_unattributed`, `scanned`), `entities` (`{name: {metric: value-object}}`) and `procs`.

Process rows (the enriched union: all D-state, then top CPU and top IO interleaved, hard cap
`process.cap`): `pid`, `start` (start time ticks; identity is pid + start), `comm`, `state`,
`why` (`D`|`cpu`|`io`), `cgroup` (entity name, joins with `entities`), `cmdline` (read only for the
union, truncated to `cmdline_max`), `rss_bytes`, `threads`, and value-objects `cpu_pct`,
`majflt_per_s`, `io_read_bps`, `io_write_bps`, `run_delay_ms_per_s` (schedstat wait of the main
thread), `blkio_delay_ms_per_s` (`delayacct_blkio_ticks`; `not_applicable task_delayacct_off` when
`kernel.task_delayacct` is 0; main thread).

### `series`
`{"req":"series","entity":"cg:/x","metric":"mem.current","tier":"hot","window_s":60}`; all four
fields required. `window_s` is clamped to the tier's `keep_s + segment_s` (the response reports
the effective `window_s`). Response: `entity`, `metric`, `tier`, `step_s`, `kind`
(`gauge`|`counter`), `fields`, `points`. Points are arrays `[ts_ms, ...]`:

| tier / kind | fields |
|---|---|
| raw (step = tick), gauge | `ts_ms, v` |
| raw, counter | `ts_ms, delta, dt_ms` |
| rollup, gauge | `ts_ms, avg, max, min, p95` |
| rollup, counter | `ts_ms, sum, dt_ms` |

A bucket whose samples were all states is `[ts_ms, {"s":..,"why":..}]` (counters: with `dt_ms` last).
Missing frames are skipped. `ts_ms` is the end of the bucket. Hot and warm live on tmpfs (lost at
reboot), cold on disk (survives restart; the epoch tells a client the process restarted).

### `events`
`{"req":"events","window_s":3600,"entity":"cg:/x","limit":1000}`; `window_s` required. Hard events
(D-027), oldest first, newest `limit`: `{"ts_ms","mono_ms","type","entity","detail"}` with `type` one of
`oom_kill` (detail: counter, delta, total), `cgroup_vanished`, `limit_changed` (file, old, new),
`psi_cross` (resource, avg10, threshold, direction `up`|`down`). Stored in `events.events_path`,
rotated to `.1` above `events.max_bytes`. Interpretive findings are computed by Python at read time.

### `status`
The operator's view of collection gaps. `uptime_s`, `ticks`, `tick_ms`; `self.cpu_pct_recent_60s`,
`self.cpu_pct_since_start`, `self.rss_bytes` (value-objects); `timing` (`last_tick_us`,
`tick_budget_us`, `tick_overruns` = ticks whose work exceeded the period, `skipped_ticks` = periods
missed because a tick ran late, `last_stage_us` per stage); `throttle` (`nr_throttled`,
`throttled_usec`, `memory_high_events`, `memory_max_events` of the sampler's own cgroup, value-objects);
`tiers` (per tier: `segments`, `bytes`, `oldest_s`, `newest_s`, `keep_s`, `fill` 0..1); `disk`
(`cold_bytes`, `cold_cap_bytes`); `tmpfs.bytes`; `dict` (`metrics`, `entity_slots`,
`ids_freed_total`); `counter_resets`.

## Process scan cost and discovery delay

Reading `stat` for every PID is cheap and always done. `io` and `schedstat` are read only for
candidates: the previous tick's enriched union, PIDs whose CPU time rose, D-state PIDs, new PIDs,
plus every PID on a full sweep every `process.full_sweep_every_s` (and on the first tick). An
IO-hot process that burns no CPU is therefore discovered **at most `full_sweep_every_s` late**
(default 10 s); a CPU-hot or D-state process is discovered on the next tick. After discovery the
process stays a candidate while it is in the union. Its IO rate is computed over the time since its
own last `io` read (so the first value after a sweep is an average over the gap). A process that
starts and exits between sweeps without using CPU or entering D state can be missed.

## Compatibility

`protocol` changes on incompatible wire changes; `schema` changes when metric names, field order or
state codes change meaning. Adding metrics, entities, response fields and state codes is compatible;
clients ignore unknown fields and treat an unknown state code as `unavailable`.

## Hardening and additions (S2, 2026-11-05; wire-compatible, `protocol` 1, `schema` 1)

Everything below is additive: new fields, one new error code, two new state codes (append-only
table). A client that ignores unknown fields and treats unknown states as `unavailable` is unaffected.

- **Counter that goes backwards (S-04).** The tick carries state `counter_reset` (code 12, `unavailable`;
  `raw` = the new reading, no `v`, no rate) and the reading becomes the baseline. This replaces the
  earlier "the reading becomes the delta" rule, which turned kernel glitches into spikes. `/proc/stat`
  `iowait` going backwards is clamped to a delta of 0 (a value, not a reset). A value that does not
  parse is `unavailable parse_error`, never 0. Code 13 `read_timeout`: a bounded helper did not answer.
- **Errors.** `busy` (at most `limits.max_heavy_requests` = 2 `series`/`events` run at once; retry),
  `internal` (a handler panicked; the daemon is unaffected).
- **Stored history (S-01).** Frames carry magic, length, crc32 and timestamp. `series` adds
  `corrupt_frames` (number of corrupt frames hit; reading a segment stops at the first one, so that
  is a gap). `series` reads one frame at a time (at most 24 MiB decoded) and the entity/metric tables of
  the live snapshot. `status.store`: `enabled`, `queue_len`, `dropped_frames` (frames the writer could not
  take: the disk stalled), `writer_lag_ms`, `last_job_us`, `write_errors`, `last_error`,
  `frames_skipped` (not written because the dictionary was not durable), `torn_frames_seen` (cut at
  start-up), `corrupt_frame_reads`. `status.disk.cold_other_bytes` counts dictionaries and events
  against `cold_cap_bytes`. Sampling never does disk IO; `last_stage_us.store` is the writer's last job.
- **Throttling (S-06).** `status.throttle` reads `cpu.stat`/`memory.events` of the first ancestor of the
  sampler's cgroup that carries a limit (`cpu.max` set; `memory.max`/`memory.high` set) and names it in
  `throttle.cpu_cgroup` / `throttle.memory_cgroup` (`null` + `not_applicable` when none). `status.cmdline`:
  `pending`, `timeouts`.
- **OOM events (S-05).** One `oom_kill` event per kill, on the owning cgroup, from `memory.events.local`;
  without that file, on the deepest cgroup whose hierarchical counter rose. `memevl.*` (counters) are the
  non-hierarchical `memory.events.local` values.
- **Processes.** `cmdline` is read by a helper pool that may time out: rows carry `cmdline_state`
  (`value` when `cmdline` is there; otherwise `unavailable not_read_this_tick` or `read_timeout`) and
  `d_tasks`. **Threads (S-10):** `state` of the leader is shown; `d_tasks`, `blkio_delay_ms_per_s` and
  `run_delay_ms_per_s` cover all threads (at most 1024 per process, 4096 task-file reads per tick) for
  candidates (union, CPU rose, D, new). A process whose leader sleeps while a worker is in D and that is
  no candidate is not seen by the cheap pass: `pids.d_unseen` = `host.procs_blocked` minus the D tasks
  counted; while it is positive, multi-threaded processes are scanned in a budgeted rotating slice, so a
  hidden D thread is found within a few ticks. `pids.task_scans` is the number of processes scanned
  per thread this tick. `procs.d_state` and `pids.d_state` count tasks.
- **Cgroup identity (S-12).** The same path with another kernel directory id is a new cgroup:
  `cgroup_vanished` (`detail.reason` = `recreated`) and fresh counters. `psi_cross` has hysteresis:
  `down` only below 80% of the threshold.
- **Views (additions).** `limit.pids.max` (`-1` = max), `mem.swap_current` per cgroup (`memory.swap.current`),
  and for the root cgroup, which has neither `memory.current` nor `memory.swap.current`: `mem.current` =
  MemTotal - MemAvailable and `mem.swap_current` = SwapTotal - SwapFree from meminfo. `vm.*`: every key
  listed for `host` is always present; a key the kernel does not list is `not_applicable feature_absent`
  (an unreadable `/proc/vmstat`: `unavailable read_error`). (Bug fixed: seq_file files such as
  `/proc/vmstat` return one chunk per `read(2)`; the reader stopped after the first, dropping every key
  past about 4 KiB, among them `zswpin`, `zswpout`, `zswpwb`.)
- **`current` shaping.** `{"req":"current","fields":["mem.current","cpu.*"],"compact":true}`. `fields`: exact
  metric names, an entry ending in `*` is a prefix; entities without a requested metric are omitted;
  absent = all. `compact`: no `raw`; a gauge is a bare number, a counter `[delta, rate_per_s]`, an
  `unavailable` state stays `{"s","why"}`, `not_applicable` states are left out. Default unchanged.
- **Transport.** Socket mode stays 0600 (root only): `current` carries full command lines. One sampler per
  socket (`<socket>.lock`); a second one fails to start. A reply has an overall write deadline of
  4 x `client_write_ms`.
- **Limits that remain.** Rollup bucket boundaries are tick counts since start, not wall-aligned; a
  counter bucket's `dt_ms` includes state ticks. `memory.stat` keys unknown to the classifier default to
  gauge; the classification is stored in `mets.dict` at first sight.
