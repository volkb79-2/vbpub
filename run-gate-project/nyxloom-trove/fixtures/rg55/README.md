# RG-55 golden fixtures — chosen values and hand-checkable arithmetic

Frozen alongside `RG55-INTERFACE-CONTRACT.md` §6/§7 by the P0 controller
package. Both implementations (P1 daemon, P2 run-gate client) compute their
summaries from `cgroupfs/frames/<k>/` (a fake cgroup v2 + `/proc` tree, one
snapshot per second) and MUST reproduce `summary-v1.json`,
`summary-container-v1.json` and `summary-basic-v1.json` byte-for-byte from
those frames using the §7 computation rules. This file is the by-hand proof:
every number below is small, round and independently re-derivable.

Session window: `started_at = 2026-09-12T10:15:00Z`,
`ended_at = 2026-09-12T10:15:04Z` → `duration_seconds = 4.0`.
`interval_seconds = 1.0`, 5 samples (`s_0..s_4`, frames `0..4`).
Container id (64 hex, arbitrary):
`deadbeefcafebabefeedfacefeedbeadf00dbabe1234567890abcdef00112233`.
Cgroup: `dev.slice/dev-background.slice/docker-<id>.scope`, slice
`dev-background.slice`. `1 MiB = 1048576 bytes` throughout; every byte value
below is `N * 1048576` for a whole number of MiB N — check by multiplying.

## Container-level memory (`memory.current`, `memory.peak`)

| frame | memory.current (MiB / bytes) | memory.peak (MiB / bytes) |
|---|---|---|
| 0 | 500 / 524288000  | 500 / 524288000  (no spike yet) |
| 1 | 560 / 587202560  | 760 / 796917760  (spike between s0 and s1, missed by sampling) |
| 2 | 700 / 734003200  | 760 / 796917760 |
| 3 | 640 / 671088640  | 760 / 796917760 |
| 4 | 600 / 629145600  | 760 / 796917760 |

- `baseline_bytes` = `memory.current` at s0 = **524288000** (500 MiB exactly).
- Scope **`container`**: `memory.peak_bytes` = LAST read of `memory.peak` =
  **796917760** (760 MiB). `peak_over_baseline_bytes` = 796917760 − 524288000
  = **272629760** (260 MiB exactly).
- Scope **`container-shared`**: `memory.peak_bytes` = max of sampled
  `memory.current` = max(500,560,700,640,600) = **734003200** (700 MiB).
  `peak_over_baseline_bytes` = 734003200 − 524288000 = **209715200**
  (200 MiB exactly).
- **p90/median** (both scopes — always computed from sampled `memory.current`,
  never from `memory.peak`): sort the 5 values ascending →
  `[524288000, 587202560, 629145600, 671088640, 734003200]` (500, 560, 600,
  640, 700 MiB). Nearest-rank, N=5: `median` (p50) rank = `ceil(0.5*5)` = 3 →
  3rd element = **629145600** (600 MiB). `p90` rank = `ceil(0.9*5)` = 5 → 5th
  (= largest) element = **734003200** (700 MiB).
  **Gotcha, worth remembering**: with exactly 5 samples, `ceil(0.9*5) == 5 ==
  N`, so p90 always equals the sample maximum — that is why
  `p90_bytes == peak_bytes` for the `container-shared` scope here (both come
  from the same 5-sample max); it is NOT a fixture bug, and it is also why
  the `container` scope's `peak_bytes` (796917760, from the real
  `memory.peak` file) differs from its own `p90_bytes` (734003200, from the
  5 samples) — that difference is the whole point of `container` scope
  trusting the kernel's exact high-water mark instead of a sampled max.

## Swap / anon / file peaks

`memory.swap.current` = `0` at every frame → `swap_peak_bytes = 0`.
`memory.stat anon` per frame (MiB): 400, 450, 550, 500, 470 → peak = 550 MiB
= **576716800**. `memory.stat file` per frame (MiB): 80, 90, 120, 110, 100 →
peak = 120 MiB = **125829120**. Both peak at frame 2, same frame as the
sampled `memory.current` peak.

## CPU (`cpu.stat usage_usec`, cumulative)

| frame | usage_usec | Δ from previous frame |
|---|---|---|
| 0 | 0 | — |
| 1 | 1200000 | 1200000 (1.2 s) |
| 2 | 3200000 | 2000000 (2.0 s) |
| 3 | 3700000 |  500000 (0.5 s) |
| 4 | 4000000 |  300000 (0.3 s) |

- `cpu.seconds` = (4000000 − 0) / 1e6 = **4.0**.
- `cores_avg` = 4.0 / duration_seconds(4.0) = **1.0**.
- `cores_max` = max of the four per-second deltas above (Δt = 1 s each) =
  max(1.2, 2.0, 0.5, 0.3) = **2.0**.
- `throttled_usec`: 0, 0, 100000, 200000, 300000 → Δ = 300000 − 0 =
  **0.3** s `throttled_seconds`. `nr_throttled`: 0,0,1,2,3 → Δ = **3**.

## Pressure (container `memory.pressure` / `cpu.pressure` / `io.pressure`, `total=` field, microseconds)

| metric | total @ s0 | total @ s4 | Δ (seconds) |
|---|---|---|---|
| memory some | 1000000 | 7100000 | **6.1** |
| memory full |  500000 | 5300000 | **4.8** |
| cpu some    | 2000000 |14000000 | **12.0** |
| cpu full    | constant 0 | 0 | 0 (not reported) |
| io some     |  300000 | 1500000 | **1.2** |
| io full     |  100000 | 1000000 | **0.9** |

Intermediate frames carry monotonically increasing totals (never used in
the delta calc — only s0 and s4 matter per §7 — but a real counter never
decreases, so they are filled in for realism: memory some
1000000→2500000→4500000→6200000→7100000, memory full
500000→1800000→3300000→4500000→5300000, cpu some
2000000→5000000→9000000→11500000→14000000, io some
300000→600000→900000→1200000→1500000, io full
100000→300000→600000→800000→1000000).

## Faults (container `memory.stat`, cumulative counters)

`pgmajfault`: 1000, 1010, 1025, 1030, 1032 → Δ = 1032 − 1000 = **32**.
`workingset_refault_anon`: constant 0 → Δ = **0**.
`workingset_refault_file`: 2000, 2050, 2150, 2180, 2210 → Δ = 2210 − 2000 =
**210**.

## Limit drift and events

`memory.max` = **1073741824** (1 GiB) constant across all 5 frames → no
drift from this field. `memory.high`: 734003200 (700 MiB) at frames 0–2,
**changes** to 786432000 (750 MiB) at frames 3–4 → exactly one sample-pair
transition (s2→s3) → `limit_drift = **1**`. `memory.events.local high`:
10,10,10,11,11 → Δ = 11 − 10 = **1** (`memory_high_breach`), landing on the
same s2→s3 transition as the `memory.high` bump (the daemon raised the
ceiling right after the one breach — a deliberately readable story).
`oom_kill`: constant 0 at every frame → Δ = **0**.

## pids

`pids.current`: 3, 4, 5, 4, 4 (matching `cgroup.procs` growing from 3 pids to
5 at frame 2, then settling to 4). `pids.peak` (monotonic high-water mark):
3, 4, 5, 5, 5 → last read = **5**.

## DAMON classified bytes (`damon.json` per frame, hot/warm/cold/idle, MiB)

| frame | hot | warm | cold | idle | sum (= memory.current MiB, by design) |
|---|---|---|---|---|---|
| 0 | 150 | 100 | 150 | 100 | 500 |
| 1 | 180 | 110 | 170 | 100 | 560 |
| 2 | 220 | 140 | 200 | 140 | 700 |
| 3 | 190 | 130 | 190 | 130 | 640 |
| 4 | 170 | 120 | 180 | 130 | 600 |

Each frame's four classes sum exactly to that frame's `memory.current` —
deliberate, so the classification total is hand-checkable against the
memory table above. Peak/p90/median per class (same nearest-rank, N=5, so
p90 again always equals the peak — see the gotcha above):

- **hot** [150,180,220,190,170] → sorted [150,170,180,190,220] → peak=p90=
  220 MiB = **230686720**, median (3rd) = 180 MiB = **188743680**.
- **warm** [100,110,140,130,120] → sorted [100,110,120,130,140] → peak=p90=
  140 MiB = **146800640**, median = 120 MiB = **125829120**.
- **cold** [150,170,200,190,180] → sorted [150,170,180,190,200] → peak=p90=
  200 MiB = **209715200**, median = 180 MiB = **188743680**.
- **idle** [100,100,140,130,130] → sorted [100,100,130,130,140] → peak=p90=
  140 MiB = **146800640**, median = 130 MiB = **136314880**.

`damon.samples = 5` (one DAMON aggregation sample per cgroup sample, for
this fixture). `damon.targets_seen = 5` (the peak pid count over the
session, same value used for the top-level `target.targets_seen` — this
fixture does not fabricate per-pid `/proc/<pid>/environ` files, so no
`--token` is exercised; `target.token = null` in every non-basic summary,
and both `targets_seen` fields report "how many pids were ever found in the
cgroup" rather than a token-filtered subtree).

## Host PSI (`/proc/pressure/memory`, host-wide, `total=` microseconds)

`some`: 50000000 (s0) → 53000000 (s4) → Δ = 3000000 usec = **3.0** s
(`host.memory_some_stall_seconds`). `full`: 10000000 (s0) → 11800000 (s4) →
Δ = 1800000 usec = **1.8** s (`host.memory_full_stall_seconds`). `loadavg[0]`
rises 3.00 (s0) → 3.10 → 3.30 → 3.35 → 3.40 (s4), giving
`host.start.loadavg1 = 3.00`, `host.end.loadavg1 = 3.40`.

## Slice (`dev-background.slice`, the slice owning the container)

`memory.current` per frame (MiB): 2000, 2100, 2300, 2200, 2150 → peak = 2300
MiB = **2411724800** (`host.slice.memory_peak_bytes`). `memory.pressure
full total`: 2000000 (s0) → 2700000 (s4) → Δ = 700000 usec = **0.7** s
(`host.slice.memory_full_stall_seconds`). `dev.slice` and
`dev-interactive.slice` carry plausible constant values (3200 MiB and 800
MiB respectively) for the `status`/`host` fixtures only — they are not
inputs to any Summary computation. All three slices declare
`memory.max = memory.high = "max"` (unlimited) → `memory_max_bytes` and
`memory_high_bytes` are `null` in every `HostSnapshot`/`slices` entry;
slices carry no `memory.swap.current` file in this fixture (deliberately
absent, not zero) → `memory_swap_current_bytes` is `null` throughout, per
§1.7 ("absent is never zero").

## Headline numbers (for quick cross-checking against the controller's report)

peak (container-shared) = 734003200 B (700 MiB); peak (container) =
796917760 B (760 MiB); p90 = 734003200 B (700 MiB); median = 629145600 B
(600 MiB); cores_avg = 1.0; cores_max = 2.0; memory_full_stall_seconds
(container) = 4.8 s; host memory_full_stall_seconds = 1.8 s; slice
memory_full_stall_seconds = 0.7 s; limit_drift = 1; memory_high_breach = 1;
oom_kill = 0; pids.peak = 5; damon hot p90/peak = 230686720 B (220 MiB).

## Method variants

- `summary-v1.json`: `method: "daemon"`, `scope: "container-shared"` — the
  numbers above as computed.
- `summary-container-v1.json`: identical inputs, `scope: "container"` — only
  `memory.peak_bytes`/`source`/`peak_over_baseline_bytes` change (see the
  memory section above); everything else byte-identical.
- `summary-basic-v1.json`: `method: "basic"`, `session`/`daemon`/`damon`/
  `host.slice` all `null`, `target.targets_seen: null` — everything else
  (including the pressure/faults/events numbers, since the basic path reads
  the same cgroup and `/proc/pressure` files via `docker exec ... cat`)
  identical to `summary-v1.json`. Production's basic path samples every
  `PROFILE_SAMPLE_SECONDS = 5`; this fixture feeds it the same five
  1-second frames instead — the §7 rules are interval-agnostic, so the
  arithmetic is unaffected; only a real basic-path run would see
  `interval_seconds: 5.0` and a different sample count for the same wall
  time.

## Other golden responses

`host-v1.json` = the `HostSnapshot` read at frame 4 (`ctl host`).
`status-v1.json` = `ctl status` called at frame 2 (`elapsed_seconds: 2.0`,
`samples: 3`, `cpu_cores_recent: 2.0` = the frame1→frame2 CPU delta computed
above). `start-v1.json` = the `ctl start` response at frame 0
(`baseline_memory_bytes: 524288000`, `pids_at_start: 3`). `stop-v1.json`
wraps `summary-v1.json` verbatim plus `session_dir`/`series`. `version-v1.json`
is a static daemon self-description. `error-v1.json` is the `unknown-session`
exit-2 shape for an id nobody started.
