# iogov v1: a recommend-only recorder of long-term storage-capability shifts

Status: IMPLEMENTED in `mdt-cockpit/iogov/` (not installed, not enabled anywhere). Written 2026-10-27 from
[IO-GOVERNOR-SECOND-OPINION-2026-10-27.md](IO-GOVERNOR-SECOND-OPINION-2026-10-27.md) (blind rounds of two external models, the v1
`iogov-probe` spec, Astra's critique), [IO-GOVERNOR-DESIGN.md](IO-GOVERNOR-DESIGN.md) and the operator's constraints. Operator
README: [../iogov/README.md](../iogov/README.md). This note is host-agnostic: no server or host names.

## 1. What v1 does, and what it does not

**Goal.** Recognise long-term shifts (days to weeks) in what a virtual disk can deliver, when the disk's identity does not change
but the provider-side state does (one host saw sustained write 516 MB/s -> ~15 MB/s -> 340 MB/s, reads unaffected). It records
evidence and emits **recommendations** such as "capability shifted down ~20x on w_fresh_qd4, baseline reset suggested".

**v1 does:**

- 4 KiB `O_DIRECT` read probes at arbitrary offsets over the whole raw disk (read-only open), a witness-block lane, and a bounded
  sequential raw read.
- A mini write bench about every 6 h inside an adaptive scratch **file** (never a device): fresh sequential writes at queue depth 1
  then 4, 4 KiB `O_DSYNC` overwrites with a latency histogram. The file exists for one run only.
- Passive collectors: PSI (system and per cgroup), `/sys/block/<dev>/stat` and `inflight`, cgroup `io.stat` for the governed slices,
  the protected cgroups and named containers, and per-interval writes by every other top-level cgroup ("who else writes").
- Covariates stored per run (never a lookup table): filesystem fill, mount options, last fstrim time and trimmed bytes if a record
  exists, `discard_max_bytes`, lvm `issue_discards`, an optional provider signal file (used bytes, snapshot time), queue parameters
  (`nr_requests`, `max_sectors_kb`, scheduler), io.cost model/QoS, governed `io.max`/`io.weight`, io.prio availability, and free-form
  annotations (`iogov annotate`). The queue/iocost/limit values are hashed into a `settings_epoch` on every sample.
- Protected-side distress episodes (`iogov watch`): a 1 Hz sampler (no probe IO) records a time-series window around every episode
  (protected PSI stall share, threads in D state), and `analyze` reports how long distress persisted and recovered, also before and
  after operator-annotated actions: the doc's missing "actuator stopping distance".
- A recognizer (robust baseline, two-sided CUSUM, hysteresis) over per-lane window medians; an offline `analyze` (summary, shifts,
  text plots, harm, demand check) and a `status` that includes the report-only **prevention checker** (fill > 60 %, days since fstrim,
  discard mount option / lvm `issue_discards` / `discard_max_bytes`, snapshot age and provider usage when a signal file exists).
- A `quiet-experiment` **definition and plan printer** for an approved `nr_requests` / `io.max` test. It prints; it executes nothing.

**v1 does NOT:**

- change any cap, io.cost model or QoS value, `io.max`, `io.weight`, `nr_requests`, queue profile or cgroup file. There is no
  actuator and no code path that writes a control file (a static test asserts the package never opens a non-journal, non-scratch
  file for writing, imports only the stdlib and never executes a subprocess);
- write to any device, ever; discard, trim or punch holes; run fstrim; take snapshots;
- claim a capacity ceiling. Probe results are "this workload shape, this byte count, today"; a healthy cache may hide the true
  ceiling and the output says "capability shifted", not "the ceiling is X";
- treat low delivered throughput as a capacity drop (section 7);
- use a fill table, `ceiling_shift` as evidence, the 0.7x floor, or candidate A (vrate edge).

## 2. Decision log (what was dropped from the blind opinions, and why)

| Item | Decision | Reason |
|---|---|---|
| Automatic cap/model/profile changes (spec section 6) | dropped; recommend-only | Both external opinions: ship a recorder first. Astra: a lower bound is not a capacity estimate, "lowering takes the minimum" conflates protection with inference. |
| Candidate A (`vrate` edge), A+, F, D | dropped | vrate sits at the floor with healthy latency because of our own `nr_requests`; both models killed A independently. |
| Fill-bucket table, `ceiling_shift` as corroboration, 0.7x worst-plateau floor, best-plateau ceiling, 25 %/30 min constants | dropped | Two confounded data points; self-referential evidence; a floor poisoned by one window. Fill is a logged covariate and a prevention warning only. |
| Dedicated probe LV, BLKDISCARD re-arming, FRESH ring | dropped | Astra: discard changes the phenomenon, conflicts with the micro-probe scheduling, and is not a snapshot "re-arm". Instead the scratch file is created fresh each run, so each bench pays real allocation, and removed afterwards (no discard issued by us). |
| 1/s write sentinel (337 MiB/day, 86 400 flush-bearing ops/day) | replaced | Astra: operation count matters more than bytes. The durable-write latency lane is 256 samples per 6 h bench (~1 000 sync ops/day); the always-on lane is the cheap read witness. |
| "Raw reads cut from v1" (Astra) | **kept, by operator decision**, with the mitigations in section 6 | The operator wants arbitrary-offset raw reads; their confidentiality cost is acknowledged and bounded, not denied. |
| `fio` for the micro-probe | dropped | Stdlib only; threads with `os.pwrite` give queue depth up to 4 and the loop is testable against fakes. |
| One final `fdatasync` for both phases | replaced | Astra: no separate durable rate per phase. Each phase syncs every 8 MiB and has its own totals. |
| Claim "final sync makes the probe cache-size independent" | dropped | Astra: a durable provider journal can absorb it. Output is "write plus sync performance for this byte count". |
| `O_WRONLY` open to self-test write denial, exact `EACCES` | replaced by `os.access(W_OK)` | An open-for-write plus close makes udev re-probe the disk: a side effect. Optional `require_write_denied`. |
| HEALTHY/DEGRADED regimes and automated profile switching | dropped; shifts + flags only | Astra: regime logic mixed health with evidence availability. Recognizer state is per lane; classification is not a verdict. |
| `DISCARD_INEFFECTIVE` flag, zero fraction as "allocation share" | dropped | Equal fresh/over latency cannot tell cheap allocation from ineffective discard; zero fraction is the share of zero samples, no cause is assigned. |
| Weekly 512 MiB escalation | kept, scaled | `escalation_bytes_cap` 192 MiB, once a week, only if the region allows. |
| Probe slice at the game's weight | replaced by a LOW weight | Astra: same weight makes the probe a competitor. A probe that waits is merely `contended` and is excluded from the recognizer. |
| "model-free stall bound" from `nr_requests x max_sectors_kb` | not relied on | Astra: not a guarantee. The numbers in section 5 are stated as in-flight limits of OUR probe, with measured disturbance still to be validated. |
| CUSUM "alarms in 2 days on 2x" | corrected | With k=0.5 a sustained 2x needs 3 windows to reach h=1.5, and confirmation adds one (tested). |

## 3. Layout and the 'no write' property

Everything lives in `mdt-cockpit/iogov/` (python stdlib, systemd service/timer **templates** in `units/`). Writers: `journal.py`
(journal, state file), `scratch.py` and `bench.py` (the scratch file). Raw reads (`rawread.py`): only `os.open(..., O_RDONLY |
O_CLOEXEC | O_DIRECT)` guarded by an assertion against any write flag, only block devices (regular files only in tests), only disks
on the allow-list (auto-detected from `data_paths` through st_dev -> sysfs -> partitions/device-mapper slaves -> whole disk, or
configured; `loop`, `ram`, `zram`, `sr`, `fd`, `nbd` excluded), page-aligned buffers, buffer wiped after the zero check, contents never
logged. Offsets: `block_i = (start + i*stride) mod N` with a stride near 0.618 N coprime to N and a persisted counter, so every
block of the device is visited once before any repeat, consecutive probes land far apart, and a resized device restarts the walk.
All-zero blocks (unallocated or genuinely zero) are counted and excluded from latency statistics because a thin backend answers
them from a fast path; non-zero blocks found become witnesses (up to 16, re-read every tick).

## 4. Schedule

| Run | Period | What |
|---|---|---|
| `tick` | 5 min (timer) | per disk: 12 non-zero cold 4 KiB reads (at most 36 attempts), 2 witness reads, passive snapshot (about 1 s) |
| `bench` | timer every 15 min; due every 6 h +-10 % jitter | region 512 MiB default; probe 64 MiB: fresh sequential 64 KiB writes 16 MiB at qd1, 48 MiB at qd4 (fdatasync every 8 MiB), 256 x 4 KiB O_DSYNC overwrites, 8 MiB sequential raw read; weekly escalation to 192 MiB |
| `watch` | resident, 1 Hz | procfs/cgroup reads only |

Adaptive region: `min(512 MiB, 1 % of the disk, 25 % of (free - reserve), hard cap 1 GiB)`, reserve = max(2 GiB, 5 % of the filesystem), filesystem
must be ext4 or xfs; below 16 MiB the write lanes are off ("region none") and the journal and `analyze` say what is lost (no durable-
write latency, no fresh-write lane, no bandwidth probe outside passive windows). 32 GB disk: ~327 MiB region, 40 MiB probe. 128 MiB
region: 16 MiB probe. Sizes are decided at each run from current free space; the file is created O_EXCL, filled with random non-zero
data by the bench itself (no unwritten-extent fast path), removed in `finally` (also on abort/SIGTERM), stale files removed under an flock.

## 5. Cost budget (from `compute_budget`, asserted by tests; defaults)

| | typical | worst case | ceiling in code |
|---|---|---|---|
| raw reads / day | 4 032 x 4 KiB + 4 x 8 MiB = 48 MiB | 10 944 x 4 KiB + 32 MiB = 75 MiB | 128 MiB/day hard budget, 20 IOPS, queue depth 1 |
| writes / day | 4 x (64 + 1) MiB + 128 MiB/7 d = ~280 MiB | same | 1 GiB/day hard budget, 256 MiB/s |
| flush-bearing ops / day | ~1 100 | same | 4 KiB lane 100/s |
| in flight at any instant | 1 request, except 4 x 64 KiB during the qd4 phase (a few seconds per bench) | 256 KiB | `qd_high <= 8` (hard), unsynced <= 8 MiB |

Worst-case extra wait a neighbour sees behind our in-flight bytes (256 KiB): 0.74 ms at 340 MiB/s, 16.7 ms at 15 MiB/s, 41.7 ms at
6 MiB/s; a flush barrier covers at most 8 MiB: 23 ms / 533 ms / 1.3 s at those rates. These are bounds on our own submissions
under the assumption that the device drains in order; they are NOT a guarantee (provider pauses and flush stalls are unbounded),
so each IO is also bounded in time: any single IO slower than 250 ms stops further submissions (an already submitted IO cannot be recalled).

## 6. Failure modes, kill switches, safety

- **Kill switches** (checked before a run, and every 32 IOs): `/etc/iogov/disabled`, `<state_dir>/DISABLED`, `IOGOV_DISABLE=1`,
  `enabled=false`, unit `ConditionPathExists=!` on the flag file, `systemctl stop|mask`.
- **Load-aware gating, never run when IO pressure is high:** tick skips if system `io.pressure some avg10` > 10 % or load > 1.5/CPU. The bench
  needs `some` <= 1 %, `full` <= 0.1 %, protected cgroup pressure <= 1 %, governed writes <= 2 MB/s, nothing in flight on the disks.
  A gated run costs one 1 s pair of procfs reads, applies exponential backoff (5 min doubling to 1 h) and journals a deduplicated `skip`.
- **Mid-run abort:** stop flag (SIGTERM/SIGINT only set it), kill switch, pressure rising to 5x the bench limit, a single slow IO,
  a phase wall clock (20 s), the daily budgets. An aborted bench is journalled with partial lanes; the scratch file is always removed.
- **Raw-read risks:** read-only by construction (an assertion, a static test that nothing else issues write syscalls on it, and a
  unit with `DeviceAllow=<node> r`, `DevicePolicy=closed`, udev ACL `u:iogov:r` on one `ID_PATH`, no `disk` group, no CAP_SYS_RAWIO).
  The remaining risk is **confidentiality**: a compromised service could read every block, including swap and deleted data. Mitigation is
  confinement (user, `NoNewPrivileges`, `ProtectSystem=strict`, no network families, `MemoryDenyWriteExecute`, buffers wiped, contents never
  stored); it is not eliminated and is a documented cost the operator accepted.
- **Evidence loss is explicit:** skipped, gated and aborted runs are journalled; `status` flags lanes with no qualifying window (STALE);
  windows below the minimum sample count are "insufficient", never a fresh-looking healthy value.
- **Nothing here changes caps.** Recommendations are journal records; a human or a later, separately reviewed tool acts on them.

## 7. Recognizer

Pure function of the journal (`recognize(records, cfg)`), replayed hourly by the runner (events deduplicated by key) and on demand by `analyze`.

- Lanes: `r_cold@dev`, `r_witness@dev` (4 KiB read latency), `w_over_4k` (durable 4 KiB overwrite latency), `w_fresh_qd1`, `w_fresh_qd4`
  (durable write rate), `r_seq`. Only **quiet** samples enter (no inflight requests, governed writes < 1 MB/s at the tick, gate-passed bench).
- Window = 24 h. Value = median of pooled latency samples, or median rate. **Capability** = 1e6 / median latency (ops/s) or the rate;
  `x = log2(capability / reference)`.
- Reference = median of the first 5 valid windows (LEARNING until then), noise sigma = MAD-based with a 0.25 floor, **frozen while alarmed**, replaced
  only by `iogov annotate --baseline-reset` (median of the latest 3 windows).
- Two-sided CUSUM, `k_eff = max(0.5, sigma)`, `h_eff = max(1.5, 3 sigma)`, S capped at 2 h so a long collapse still unwinds. A window counts toward
  confirmation only if S >= h **and the window itself is beyond the slack** (a one-day outlier cannot be confirmed by normal days after it);
  2 consecutive such windows -> `shift_detected` (with factor, direction, "baseline reset suggested"). An alarm clears only after 2
  consecutive windows back within the slack, or flips explicitly. Delays (tested): 20x and 4x: 2 windows; 2x: 4 windows.
- `settings_epoch` changes between baseline and a window mark the event "settings changed, the shift may be self-inflicted".
- **Separate categories.** `recommendation` records are capability shifts only. Protected-workload distress is a different record type (`harm`)
  and a separate section in `analyze`/`status`. A pattern hint (throughput shifted, latency did not: bandwidth/drain-bound; the reverse: per-request
  latency) is labelled a hypothesis.
- **Demand-limited classification.** Probes generate their own load; passive delivered rates do not drive shifts. Per window `analyze` labels the passive
  peak write rate: `demand_limited` (probes healthy, delivered rate < 25 % of probe capability: idle capacity, not a capacity drop),
  `consistent_not_evidence` (probes degraded and delivered low), `load_present` (a lower bound), `no_probe_evidence` (no qualifying probe window: says nothing).

## 8. Journal schema (append-only JSONL, version 1)

`<state_dir>/journal/journal-YYYY-MM.jsonl`, one object per line, one `write(2)` per record (`O_APPEND`), never rewritten. Common: `v`
(schema version, 1), `ts` (UTC ISO-8601), `t` (epoch s), `type`, `run` (random run id). Readers skip torn lines and other versions and count them.

| type | required fields (besides common) |
|---|---|
| `run_start` | `kind` (tick/bench), `version`, `mode`, `settings_epoch`; also `settings`, `devices`, `notes`, `covariates` |
| `run_end` | `kind`, `status` (ok/aborted/budget), `duration_s`, `io` (+ `abort`) |
| `skip` | `kind`, `reasons[]` (+ `ctx`): kill switch, pressure, busy device, backoff class, no devices |
| `probe` | `lane`, `device`, `quiet`, `n`, `zero`, `lat_us[]` (<= 32 samples), `hist[]` (log2 us buckets), `direct`; also `p50 p90 p99 min max attempts errors ctx epoch` |
| `bench` | `status`, `region` (mode, sizes, fs, direct, escalated, disabled reason), `lanes{}` (per lane: bytes, qd, `t_write`, `t_sync`, `rate_write_bps`, `rate_durable_bps`, `at_cap`, `lat`, `sync_ms`, `valid`, `aborted`), `settings_epoch` |
| `passive` | `devices{}` (iops, bps, await ms, busy %), `pressure`; also `loadavg`, `cgroups{}` rates/pressure, `others_writing{}` (top writers outside the slices), `root_total_w_bps` |
| `recommendation` | `lane`, `kind` (baseline_established, shift_detected, shift_cleared, baseline_reset), `text`, `evidence` (`category`="capability", `key`, window, factor, direction, CUSUM state) |
| `harm` | `kind` (`episode`, `probe_latency`), `evidence` (episode: start/end, `duration_s`, `recovery_s`, peaks, `series[[t_rel, some%, full%, dstate, sys_some_avg10]]` <= 300 points, `settings_epoch`) |
| `note` | `text`; `kind` annotation / action / baseline_reset |

## 9. Assumptions to verify on the host (could not be verified here)

1. Whether raw reads of the whole disk are permitted in the intended runtime context (container vs host, udev ACL, `DeviceAllow`); `O_DIRECT` on the node works for 4 KiB aligned reads (tested only on a regular file / ext4 here).
2. Logical block size: reads use max(4096, `BLKSSZGET`) alignment; virtio-blk exposing 512 B sectors was assumed.
3. cgroup v2 paths: `/sys/fs/cgroup/<slice>/io.stat|io.pressure|cgroup.threads`, and that the root `io.stat` exists on this kernel (the "other writers" view degrades gracefully without it). Buffered-write attribution to cgroups on LVM+ext4 is unverified (flusher IO may land in the root cgroup).
4. `docker:` container name resolution reads `/var/lib/docker/containers/*/config.v2.json` (root-only by default): the service user needs read access or use cgroup paths.
5. That a low probe-slice IO weight is the right priority (Astra vs Fable disagree); that 4 x 64 KiB in flight and an 8 MiB flush barrier disturb the protected workload within budget at the current device rate (measure with `watch` episodes before and after enabling; run the first bench with the game idle).
6. That `O_DSYNC` reaches the backend as a flush; sub-100 us durable writes would mean the hypervisor ignores it (recorded in `w_over_4k`, not flagged automatically).
7. `scratch_dir` is on the same filesystem and disk as the data; ext4/xfs behaviour of O_DIRECT overwrites; free-space reserve is adequate on small hosts.
8. The systemd unit directives (device policy, ReadWritePaths, MemoryDenyWriteExecute with the interpreter) work with the packaged Python.
9. Provider signals: `provider.json` is written by something the operator provides; without it snapshot age and provider usage are "unknown".

## 10. Known blind spots

Large burst-credit limiters (credit > 64 MiB, escalation 192 MiB weekly); request sizes > 64 KiB; read bandwidth at depth; allocation-specific penalties on
snapshot-shared data (the fresh file measures allocation into free space, not a CoW overwrite of old data); time-of-day effects when the host is quiet mainly at night
(daily medians of quiet samples only); hosts that are never calm (bench skips: visible as STALE); a provider that ignores flush. The recognizer reports a
shift in what the probes measure; whether the game suffers is the `harm` section's job.
