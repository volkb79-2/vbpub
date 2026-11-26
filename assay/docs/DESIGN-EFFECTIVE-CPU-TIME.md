# Design: effective CPU time, resource source, normalized bounds (assay A1)

Status: DESIGN (doc only, no code). Package A1 of `.controller-state/assay/PLAN-spec-followups.md`.
Base: origin/main b0d171b8d. Implements in A9 (resource source), A4 (CPU time, bounds, summary) and
the one v21 amendment A2. Binding inputs: operator decisions of 2026-11-26 (00:0xZ..02:2xZ), the
plan's binding notes N1-N4 and the vbpub-hostobs reply (03:2xZ), TIM-030 as on branch
`assay-20261126_031753-spec-tim030` @ 01524eb6a.

Notation: **[V]** = verified in source or docs at the cited place; **[U]** = unverified (reasoned,
not measured or not read). Citations `@14f81bb97` are P4's unmerged branch
`assay-20261125_230232-p4-stall`.

## 0. Decisions in one screen

| # | Decision | Reason |
|---|---|---|
| D1 | No verdict, bucket or rung reads the resource source. Every bound reads assay's own process-tree CPU reader, identical in `none` and `hostobs` mode; hostobs data feeds the summary only. | A verdict that changed with the presence of a daemon would break judge identity and resume. `none` is the working default (hostobs reply). |
| D2 | Source interface `hostobs` \| `none`, default `none`; `hostobs` imports `hostobs_client` lazily from `PYTHONPATH=hostobs/python`; any failure = disclosed `unknown`, never a refusal, never zero. | Operator 01:1xZ; hostobs reply; keep-simple rule. |
| D3 | `effective_cpu_s = cpu_s`. No freeze subtraction from CPU. Freeze data is for the WALL-based clocks (idle and session-finish windows, `avg_cores`'s denominator) and for attribution of a hung candidate. | A frozen cgroup's tasks do not run, so they accrue no CPU time (thrashguard §4b, M4). |
| D4 | Freeze intervals only from hostobs M3 `events`; absent, lost or unmatched = `null` + reason, never 0; `null` propagates to every value derived from it. | Binding note N1; thrashguard §4. |
| D5 | Bounds derive from the baseline's **CPU** seconds, not its wall seconds (changes P4's derivation; new policy id). | Rule 2: wall may not decide a candidate. P4 derives a CPU bound from baseline wall (§6.1). |
| D6 | Inside one run on one host, normalized and same-host bounds are the same number; the score matters only when a bound crosses hosts (resume, shards). Cross-host transfer needs major-7 records on both hosts, else the baseline is re-measured. | §6.2: the factor cancels. |
| D7 | Score input: `/var/lib/mdt/host-facts/cpu-benchmark.json` (`mdt-host-cpu-benchmark/1`), bind-mounted read-only by the launcher; accepted only for major 7 and a matching CPU model and count. | TIM-030; operator 02:2xZ. |
| D8 | A timeout kill is carried on resume only when the stored `bound_policy` equals the current one; otherwise the candidate re-runs. | Residual answer 4. |
| D9 | One additive v21 block (`cpu_time`), three per-candidate fields and one `bound_policy` object; all nullable so P-17 can ship them empty before A4. | Plan §1 rule 1 and §3 risk row. |

## 1. Resource-source interface (A9)

### 1.1 Interface

One module, `assay/resource_source.py` (A9). A source is chosen once per run, before the baseline.

```python
class ResourceSource(Protocol):
    name: Literal["hostobs", "none"]
    def describe(self) -> SourceState: ...            # name, state, reason, client/protocol version
    def lane_window_open(self) -> LaneWindow: ...      # at baseline start
    def lane_window_close(self, w: LaneWindow) -> LaneReading: ...   # at run end
    def freeze_intervals(self, start_mono_ns: int, end_mono_ns: int) -> FreezeResult: ...
```

- `SourceState.state` is one of `ok`, `absent` (not selected or client not importable),
  `lost` (worked, then failed) or `refused` (server refused level or protocol), with `reason`
  (closed set: `not-selected`, `client-missing`, `socket-missing`, `protocol-mismatch`,
  `verb-unserved`, `connection-lost`, `cgroup-unknown`, `boot-mismatch`).
- `LaneReading`: the gate container's cgroup counters over the run (`cpu_s`, `peak_cores`,
  `io_wait_share`, `cpu_pressure_share`, `cpu_cap_cores`), each `float | None` with a quality.
- `FreezeResult`: `frozen_s: float | None`, `intervals`, `quality` (`ok`/`absent`/`partial`), reason.
- **Per-candidate CPU is not part of the interface.** It always comes from assay's own reader
  (§4.2). Reason: D1, and per-candidate cgroups do not exist (§1.4).

### 1.2 Selection and configuration

- Lane key `judge.resources.source = "none" | "hostobs"`, default `"none"`. No `required` flag:
  nothing depends on it, so a missing source cannot justify refusing a run.
- Socket path: hostobs_client's own resolution (`profiling.environment_variable(level)` exists
  [V `hostobs/python/hostobs_client/profiling.py:61`]; its exact variable name [U]). assay adds no
  path key of its own.
- The verdict names the source (`cpu_time.source`, `cpu_time.source_state`), replacing the
  `observer: cgprofile` naming for these fields (item 22).

### 1.3 `none` (working default)

Works with no hostobs, no thrashguard, no socket. Gives: per-candidate and baseline CPU seconds
(§4.2), processor model and online CPU count from `/proc/cpuinfo` and
`/sys/devices/system/cpu/online` inside the container, the benchmark score (§5), `avg_cores` over
raw wall. Everything that needs host data (`peak_cores` at lane level, IO wait share, CPU pressure
share, freeze) is `null` with reason `not-selected`.

### 1.4 What must exist before `hostobs` works inside a gate container

| Prerequisite | Owner | State |
|---|---|---|
| HOB-90 guard (M3 B1) before any socket is mounted into a gate container | hostobs | not done (hostobs reply) |
| Socket bind-mount + group membership for the gate user | core (run-gate launcher) | not designed |
| The gate container's **host** cgroup path and `cgroup_id` passed in (the container sees a namespaced path) | core | not designed |
| Per-cgroup `cpu.usage_usec` readable by `current`/`subscribe` for that cgroup | hostobs | `current`/`subscribe` served [V `hostobs/crates/serve/src/verbs.rs:67-68`]; `cpu.usage_usec` registered [V `crates/model/src/metric.rs:669`]; selection by an arbitrary cgroup [U] |
| `events` verb and `annotate` intake | hostobs M3 | not served [V `verbs.rs:73`, `:76`] |
| thrashguard emits `thrashguard.freeze`/`.thaw` | thrashguard | designed (thrashguard §4), not shipped [U] |
| CONSUMERS Boundaries rule 2 amendment (§2.3) | hostobs | to add to the M3 plan (hostobs reply) |

Per-candidate cgroups are **not** a prerequisite: freeze is applied to the whole gate container
scope (thrashguard §4), so a candidate's frozen time is the intersection of its monotonic window
with the container's freeze intervals. Lane-level CPU from the container cgroup is what scheduling
needs (shape). Rejected: run-gate reading hostobs from outside and writing the summary — it is also
dependency-free under rule 2 and does not know candidate windows.

The existing cgprofile observer path (`observer_windows.py`, `resource-profile/1`, TIM-015) is left
as is until hostobs serves its replacement (`window-open`/`window-close` are reserved verbs
[V `verbs.rs:79-80`]); A1/A4 add no new reader of cgprofile. (Open question Q1.)

## 2. hostobs client contract

### 2.1 What assay calls

1. `Connection.open(...)` at a telemetry level (`observe`; served verbs there are `hello`,
   `status`, `current`, `subscribe`, `unsubscribe` [V `verbs.rs:244-247`]). Check `PROTOCOL ==
   "hostobs/1"` [V `__init__.py:25`] and that `current` is in `conn.verbs`.
2. Record `conn.boot_id` [V `conn.py:337`]; compare with the container's
   `/proc/sys/kernel/random/boot_id` (host kernel's [U for every runtime]). Mismatch = `boot-mismatch`.
3. Lane window: `current` for the container cgroup at open and at close; the exact delta of the
   `cpu.usage_usec` counter is the lane CPU (no sampling error). `subscribe` at 1000 ms for
   `peak_cores` and pressure shares only.
4. Later (M3): `events` filtered to `thrashguard.freeze`/`thrashguard.thaw` and the container's
   `cgroup_id`, gated on `"events" in conn.verbs`.

### 2.2 Data and quality consumed

- Counter `cpu.usage_usec`; PSI `cpu.some`, `io.some` totals for the shares; `cpu.max` for
  `cpu_cap_cores`. Which of the PSI totals and `cpu.max` hostobs registers for a cgroup: [U].
- Every sample carries `quality` from `QUALITY_NAMES` (`ok`, `absent`, `unreadable`, `stale`,
  `reset`, `not-sampled`, `unsupported`, `unknown-topology`) [V `wire.py:31-40`]. Only `ok`
  contributes. Rates go through `hostobs_client.rate`, which returns `counter-reset` instead of a
  number [V `rates.py:57-59`].
- Version skew: the client is strict (unknown keys raise `EnvelopeError` [V `envelope.py:163`]).
  assay treats any `HostobsError`/`EnvelopeError` as `lost` for the run, keeps the values read so
  far only if the close read succeeded, and never crashes the judge. A verb missing from
  `hello.verbs` = `verb-unserved`, not an error.

### 2.3 Boundary rule conflict and the amendment

CONSUMERS Boundaries rule 2 says the judges "stay dependency-free and write that line themselves"
[V `hostobs/docs/CONSUMERS.md:396-397`]. It keeps holding for `annotate`. Amendment (hostobs to
add, M3 plan):

> 2a. assay and run-gate may import `hostobs_client` for read verbs (`current`, `subscribe`,
> `events`) behind an optional resource source whose default is off. The import is lazy and its
> absence changes no verdict. Spans are still written as raw lines (rule 2).

## 3. Freeze events

- Source: hostobs M3 `events` only; never a thrashguard socket (N1). Interval fields per
  thrashguard §4: `boot_id`, `cgroup`, `cgroup_id`, `seq`, `seconds`, monotonic start/end.
- Match: same `boot_id` and `cgroup_id` as the gate container; union the intervals; intersect
  with each candidate's `[start_mono_ns, end_mono_ns]` from `time.monotonic_ns()` in the
  container. Same clock as the host's `CLOCK_MONOTONIC` unless the container has a time namespace
  [U: assumed none for gate containers].
- `quality`: `ok` (served, contiguous `seq`, run fully covered), `partial` (seq gap, an interval
  open at close, store ring overrun), `absent` (no source, verb unserved). `frozen_s` is a number
  only for `ok`; `partial` and `absent` give `null`.
- Propagation: `frozen_s = null` makes `unfrozen_wall_s`, `avg_cores` (when wall is the
  denominator) and the lane total `frozen_s` `null`; a lane total is a number only when every
  candidate's is. CPU values never depend on it.
- Use: (a) the wall-based liveness clocks must exclude frozen time; (b) a candidate that ended
  `hung` (idle rule) and overlaps a freeze is re-run (thrashguard §4b refinement);
  (c) scheduling hint. A gate container that is frozen also freezes assay's own monitor, so after
  a thaw the monitor sees a monotonic jump with no CPU growth. Whether liveness already treats
  its own oversleep as unobserved time is [U] (no handling found by name @14f81bb97); A4 must test
  it, because without events this is the only protection against a freeze read as idle.

## 4. Effective CPU time

### 4.1 Formula

`effective_cpu_s = cpu_s(window)`, for a candidate, the baseline or a test. No freeze term: a
cgroup-v2-frozen task is not scheduled and accrues no CPU time (thrashguard §4b; kernel freezer
semantics [U: not measured here]). The freeze transition is not instant (tasks stop at their next
kernel exit); CPU in that tail is real work and correctly counted.

Normalized: `ref_cpu_s = effective_cpu_s * single_core / baseline_score` (TIM-030), only from a
valid major-7 record (§5).

### 4.2 Reader

- Live bound check (both modes): P4's `tree_cpu_seconds` (sum of utime+stime over the live
  process tree, `/proc/<pid>/stat`) made monotonic by banking drops [V `liveness.py:2297-2304`,
  `:2501-2509` @14f81bb97]. Polled about every second (TIM-200).
- Gap: CPU of a descendant that starts and exits between two polls is lost, and the banked peak
  only preserves what was seen. A4 adds `cutime+cstime` of each live tree member (CPU of reaped,
  waited-for children) [U: effect size not measured].
- Final value per candidate: `max(banked series, wait4 rusage of the root)`. `os.wait4` gives
  the root's CPU including every waited-for descendant, exactly, at reap. Whether assay reaps with
  `wait4` today: [U] (A4 to check; `Popen` uses `waitpid`).
- Per test: the liveness plugin records `getrusage(RUSAGE_SELF) + getrusage(RUSAGE_CHILDREN)` at
  each phase boundary in-process (exact, cheap), instead of pytest's wall `duration`.

### 4.3 Contention, resolution, resets

- **Contention.** CPU time is load-independent to first order: waiting for a CPU is not charged.
  It is not exact: SMT siblings, cache and memory-bandwidth sharing and frequency scaling raise
  CPU time per unit of work under load [U: size on our hosts not measured]. Covered by the bound
  multiple (§6.3), never by a correction term. CPU throttling (`cpu.max`) does not accrue CPU.
- **Resolution.** The live bound fires at most one poll late (about 1 s × the tree's cores). The
  final value is exact via `wait4` (§4.2). hostobs' 1 s sampling does not resolve short
  candidates (hostobs reply); that is why hostobs never supplies per-candidate CPU and why lane
  CPU uses the counter delta of two `current` reads.
- **Resets and non-monotonic series.** Process-tree series: drops are banked (P4). hostobs
  counter: a sample of quality `reset`, a decreasing `cpu.usage_usec`, a changed `cgroup_id` or
  `boot_id` makes the lane value `null` with reason `counter-reset`; never clamped to 0, never
  summed across the reset. A negative or non-finite value from any reader = `null` + `invalid`.

## 5. Benchmark input

- Record: `/var/lib/mdt/host-facts/cpu-benchmark.json`, schema `mdt-host-cpu-benchmark/1`,
  written by debian-install-v2 `run_cpu_benchmark` or `mdt host-setup cpu-benchmark`
  (plan §5). Fields used: `tool`, `tool_major`, `tool_version`, `baseline_score`, `single_core`,
  `cpu_model`, `cpus_online`. Never copied: `result_url` (private in the fleetd fixture [V
  `fleetd/tests/fixtures/facts-root/var/lib/mdt/host-facts/cpu-benchmark.json`]), `conditions`,
  `reason`, `multi_core`, `hypervisor` (kept out until a consumer needs them).
- Into the gate container: the launcher bind-mounts the file read-only at the same path (core).
  assay reads only that path; env `ASSAY_CPU_BENCHMARK_FILE` overrides it for tests.
- Validation (A4), each failure = no score with one reason: missing file `no-record`; unparsable
  or wrong schema `invalid`; `tool != "geekbench"` or `tool_major != 7` `other-major`;
  `single_core` or `baseline_score` not a positive integer `invalid`; `cpu_model` (whitespace-
  normalized) or `cpus_online` differ from the container's view `host-mismatch` (same rule as
  cgprofile `machine.py:250-265` [V], not shared code). No assay score constant (TIM-030).
- Unit (confirmed 02:2xZ): factor 1.0 = Geekbench 7 reference machine (2500 single-core, AMD
  Ryzen 7 7700). GB7 stdout parsing is ASSUMED-SHAPE upstream (plan §5); assay trusts only the
  validated record. Whether v1001, r1002 or the controller host have a record: [U].
- `cpus_online` in a container with a restricted cpuset: `/sys/devices/system/cpu/online` shows
  the host's CPUs [U], the cpuset is reported separately as `cores_available`.

## 6. Bound rule

### 6.1 What changes in P4's bound

P4 derives the CPU-without-progress bound as `max(3 × B, B + 60 s)` CPU seconds with `B` the
baseline's **wall** seconds [V `liveness.py:1462`, `:1478`, `derived_bound_from_baseline`
@14f81bb97], and the per-test bound as `max(10 × b, 60 s)` with `b` the baseline's pytest phase
durations (wall) [V `per_test_cpu_bound_from_baseline`]. Baseline wall depends on load and on
parallelism, so a wall number still sets a candidate's kill threshold. A4: `B` = baseline tree CPU
seconds (§4.2), `b` = the test's in-process CPU (§4.2); multiples and floors unchanged. New policy
id `cpu-since-completed-event-v2` (P4's is `-v1` [V `liveness.py:837`]); shipped with v21 so the
policy changes once.

### 6.2 With and without a score

Within one run, baseline and candidates run on the same host, so
`k × B_ref / factor = k × B_raw`: normalization cancels. Therefore:

- **Same host** (same `host_key`, §6.4): bound in raw CPU seconds from this host's baseline.
  Score irrelevant. This is the "same-host baseline-relative CPU time" of Q3.
- **Different host, both with valid major-7 records** (resume elsewhere, ntb §4 shards): bound
  `= m_x × k × B_ref / factor_here`, `m_x = 1.5`, because `ref_cpu_s` is an estimate for a test
  suite (the score averages Geekbench's workload mix).
- **Different host, any record missing or of another major**: no transfer; re-measure the
  baseline on this host. Never cross-major.

### 6.3 Margins

`k = 3`, floor 60 s raw CPU (interpreter start-up, not scaled), per-test `10×` with a 60 s floor
(P4 values). Contention (§4.3) is inside `k`. `m_x = 1.5` applies only to cross-host transfer;
A4 records the observed `candidate_cpu / baseline_cpu` spread of surviving candidates per run so
`k` and `m_x` can be checked against data later [U: no data yet].

### 6.4 Host key and resume

- `host_key` = sha256 of (`ASSAY_HOST_ID` from the launcher, `cpu_model`, `cpus_online`). Without
  `ASSAY_HOST_ID`, `host_key = null` and every resume counts as a different host (core, Q3).
- `bound_policy` (stored per run and per store record): `{policy, k, floor_s, per_test_k,
  per_test_floor_s, basis: "same-host" | "ref-gb7", host_key, ref_factor, m_x}`.
- Resume (D8): a stored `timeout_kind` kill is carried iff `policy`, `k`, `floor_s`, `per_test_*`
  and `basis` are equal and, for `same-host`, `host_key` is equal and not null. Otherwise the
  candidate re-runs. Survived and test-killed results do not depend on time and carry as today.
  Records without `bound_policy` (before v21) re-run their timeout kills.
- Wall time: the lane and campaign deadlines may stop a run; what they leave is the A2
  non-judgement result (e.g. `INCONCLUSIVE` + `WALL_STOP`, A2 fixes the name), never a candidate
  or rung decision.

## 7. Summary and shape fields

Run level, `judgment.r2.cpu_time` (A2 fixes the path), object, scheduling HINT only:

| Field | Type | Meaning | Today | `unknown` form |
|---|---|---|---|---|
| `source`, `source_state`, `source_reason` | str | §1.1 | new | `none`/`absent`/`not-selected` |
| `processor_model` | str | `/proc/cpuinfo` model name | readable, not recorded | `null` |
| `cpus_online`, `cores_available` | int | online CPUs; cpuset/`cpu.max` cores | readable, not recorded | `null` |
| `baseline_cpu_s` | float | raw baseline CPU (§4.2) | P4 reads tree CPU, not recorded | `null` |
| `candidates_cpu_s_total`, `candidates_cpu_s_max` | float | raw sums over judged candidates | no | `null` if any candidate `null` |
| `wall_s`, `frozen_s` | float | raw lane wall; frozen total (§3) | wall yes; frozen no | `frozen_s: null` |
| `benchmark` | obj | `tool_major`, `tool_version`, `single_core`, `baseline_score` | no | `null` + `benchmark_reason` (§5) |
| `ref_factor`, `baseline_ref_cpu_s`, `candidates_ref_cpu_s_total` | float | §4.1 | no | `null` |
| `avg_cores` | float | `cpu / (wall − frozen)`; `cpu / wall` with `avg_cores_basis: "raw-wall"` when frozen is `null` | no | `null` |
| `peak_cores` | float | max 1 s CPU rate of the container cgroup | needs hostobs | `null` |
| `io_wait_share`, `cpu_pressure_share` | float 0..1 | PSI io/cpu `some` time / wall | needs hostobs | `null` |
| `limited_by` | enum | `cpu-cap`, `io-wait`, `contention`, `serial`, `unknown` | no | `unknown` |

`limited_by` (why not more cores): `cpu-cap` if `avg_cores ≥ 0.9 × cores_available`;
else `io-wait` if `io_wait_share ≥ 0.2`; else `contention` if `cpu_pressure_share ≥ 0.2`; else
`serial` if `avg_cores < 1.5`; else `unknown`. Thresholds are hint-level, never a gate.

Per candidate (store record and verdict): `cpu_s` (float|null), `cpu_quality` (`exact` via
`wait4`, `sampled`, `unknown`), `frozen_s` (float|null). No per-candidate `ref_cpu_s`: one run is
one host, so it is `cpu_s × ref_factor`; storing it would only add a value that can disagree.

## 8. Packages

- **A2** (the single v21 amendment), all additive in meaning and nullable, but closed-set keys,
  so they go in the v21 cut: the `cpu_time` block of §7; per-candidate `cpu_s`, `cpu_quality`,
  `frozen_s`; `bound_policy` (§6.4) in run and store records; evidence policy
  `cpu-since-completed-event-v2` accepted by the store validator; `per-test-cpu-bound` evidence
  gains `per_test_baseline_cpu_s`. None of these needs P-13 [U: P-13 design not read].
- **A9**: `resource_source.py`, `none` and `hostobs` sources (§1, §2), lane key, lazy import,
  freeze-interval matching (§3) behind `"events" in verbs`; SPEC TIM-015/020 rewrite (Opus).
- **A4**: §4.2 reader changes (`cutime/cstime`, `wait4`, in-process per-test CPU), §6 derivation
  from baseline CPU and the cross-host rule, `host_key`, resume rule, benchmark loader and
  validation (§5), summary computation (§7), monitor-oversleep test (§3).
- **hostobs**: rule 2a amendment; HOB-90 guard; `events` + `annotate` (M3); per-cgroup selection
  of `cpu.usage_usec`, PSI totals, `cpu.max`; freeze state metric (thrashguard §4a).
- **thrashguard**: emit freeze/thaw per its §4. Its §4b "1500-point basis" paragraph is
  superseded by TIM-030 (unit = major-7 reference, 2500) and should say so.
- **cgprofile**: nothing for assay; CP-59 remains a fallback for other readers only.
- **core (run-gate)**: bind-mount the benchmark record; pass `ASSAY_HOST_ID`; later the hostobs
  socket, group, host cgroup path and `cgroup_id`; read the A2 wall-stop result as non-judgement.
- **SPEC** (Opus): TIM-020 becomes "`effective_cpu_s = cpu_s`; freeze for wall clocks only".

## 9. Open questions (owner, recommendation)

- **Q1 (operator)** The cgprofile observer path (`resource-profile/1`) during the transition:
  recommend keep it frozen as is, not a third source value, removed when hostobs serves
  `window-open`/`window-close`.
- **Q2 (hostobs)** Rule 2a wording (§2.3) and whether arbitrary-cgroup selection by `cgroup_id`
  is in M3: recommend yes, both in M3.
- **Q3 (core)** Host identity: recommend the launcher sets `ASSAY_HOST_ID` (stable host name or
  machine-id hash) and bind-mounts the benchmark record read-only.
