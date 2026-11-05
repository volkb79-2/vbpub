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
