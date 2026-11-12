# Resource profiles, worker sizing and the MDT capacity boundary — cross-tool design (2026-11-03)

Status: design, ready to carve. Binding inputs: the operator's final interview (2026-11-03), decisions D1–D4 below.
Tools affected: cgprofile, assay, run-gate, mdt-cockpit host-setup, iobench (first consumer).
Per-tool backlog entries that implement this design are staged next to this file.

## 0. Decisions this design implements (verbatim intent)

| # | Decision | Consequence in this design |
|---|---|---|
| D1 | The cgprofile observer is the **single source** of per-candidate and per-worker resource numbers (memory peak, CPU, io.stat bytes and IOPS, PSI). Without cgprofile, assay records **no numbers** (explicit `NO_MEASUREMENT` with a reason). There is no fallback reader and no duplicated functionality. | Observer contract 3 adds measurement windows (§3). Assay's own sampled numbers (`peak_rss_bytes`, `cpu_seconds`, `write_bytes` from the liveness sidecar) stop being a number source (§4). Wall time is not a resource reading: each writer keeps its own monotonic clock. |
| D2 | Parallel R2 runs candidates **longest first**, but keeps the **sum of running RSS roughly constant** by mixing long with short and large with small, rather than strict LPT. Predictions come from the previous profile of the same candidate id, else from the file median or the operator median. **Only wall time changes, never results.** **Refined by the operator (update 2026-11-03): "two-ended matching"** with RAM as the scarce resource and a predictable RAM need as the goal. | §5 specifies two-ended matching (TEM), with longest-first among equals. It is evaluated by simulation on the recorded IOBench (1,220) and cli-extended (1,456) candidates for N = 2..6. §5 also gives the admission rule, the cold start, prediction-error recording, and the order-independence assumption. |
| D3 | MDT host-setup takes three operator numbers: the RAM, disk swap and CPU cores that are **unavailable to MDT** because other services on the host use them. Everything else belongs to MDT. RAM and CPU are overcommitted by design. About 500 MB of disk swap is kept for the host. Protection comes from weights, from `memory.min` on the named host cgroups, and from PSI as the control signal. The word "reserve" is not used. **Operator update (2026-11-03): no backwards alias.** The old `*_RESERVE_*` keys are renamed outright, and their old names are refused with a message that names the new key. | §7: new keys `MDT_EXCLUDED_RAM`, `MDT_EXCLUDED_SWAP`, `MDT_EXCLUDED_CPU_CORES` and `DEV_TIER_EXCLUDED_CPU_CORES`; a one-time documented rename; a live probe; a PSI guard. |
| D4 | Run-gate: "time stalled on memory" is a **contamination flag**, not a lane property. Per-worker numbers for plain pytest lanes come from a scaling run: 1 worker as the baseline, then xdist `-n N`. These are recorded as profiles. Lanes get a `workers` setting with an auto value, enforced caps, and a per-lane history of distributions. | §6. |

The operator also noted that IO delays are small thanks to the CoW setup, so duration variance is mostly CPU and test
runtime. The IOBench data confirms this: candidate CPU p50 is 89 s against elapsed p50 97 s.

## 1. Facts this design rests on (2026-11-03)

- **IOBench native R2 on r1002** (1,220 candidates, N=3 after measurement):
  - **Peak RSS** (MiB) min/p25/p50/p75/p90/p95/p99/max = 70/363/372/373/374/374/376/878. 84% of candidates fall in
    350–400 MiB, about 11% are below 150, and a few are at 600–878.
  - **Elapsed** (s) p50/p90/p99/max = 97/105/169/302, with 5 timeouts at about 300 s. CPU seconds p50 89, p90 95,
    max 296.
  - **Per-file timing.** Elapsed p50 per file ranges from 93 to 106 s. Every candidate runs essentially the whole
    suite.
- **cli-extended R2** (1,456 candidates): RSS p50 82, p99 230, max 256 MiB; elapsed p50 9, p90 12, max 44 s.
- **Spread.** Within one tool RSS is tight; across tools it differs about fivefold. The duration tail matters more than
  RSS.
- **Assay progress events today** carry `peak_rss_bytes`, `cpu_seconds`, `write_bytes`, `judge_rss_bytes` and a
  `resources` object, but no IO counts and no PSI. These numbers come from assay's own `/proc` sampling in
  `liveness.py` (resource tally → sidecar → `_measured_resources`, `assay/src/assay/mutation.py:3149`).
- **Run-gate.**
  - `select_r2_jobs` (`run-gate-project/run_gate.py:4650`) computes
    `jobs = min(jobs_max, cpu_cap, floor(memory_budget / candidate_ram_p95), remaining)` and forces 1 without cheap
    clones.
  - `candidate_ram_from_history` (`:10471`) takes the highest recent per-run p95. History stores only per-run p95s,
    "not raw observations".
  - The lane footprint line prints peak, p90, average cores, "s stalled on memory (full)" and hot set.
- **cgprofile observer.**
  - Contract 2 (`docs/RG55-INTERFACE-CONTRACT.md` §9) is a read-only `resource-limits` query bound to the peer's TID.
    It returns limits and event counters for the peer's cgroup ancestry, and no numbers.
  - The Summary schema 1 (§3) has memory, CPU and PSI per session, but no `io.stat`.
- **Assay records results in canonical order.** `_CandidateEventBuffer` (`mutation.py:1062`) emits candidate progress in
  index order, and `_record_ready_in_order` writes state in the same order. Submission order is currently the index
  order.
- **Shared host:** 8 cores, MemTotal 15.6 GiB, SwapTotal 69 GiB. `wings.slice` has `memory.min=6500M` and
  `memory.high=15000M`; its two game scopes have `memory.min` 4000M + 3000M. The services use about 13.8 GB of swap on
  average (soulmask monitor).
- **r1002:** 4 cores, 7.75 GB, no other load. Host setup there is partially installed (`R1002-MDT-COCKPIT-2026-11-02.md`).

## 2. The resource profile (`resource-profile/1`)

One JSON object per measured subject. A subject is an R2 candidate, an xdist worker, a lane run, or the xdist controller.
Units are part of the field names: `_bytes`, `_seconds`, `_ops`.

```json
{
  "schema": "resource-profile/1",
  "subject": {
    "tool": "iobench", "lane": "r2", "kind": "r2-candidate",
    "id": "<candidate_id | gw<N> | controller | run_id>",
    "worker_index": 2, "workers": 3,
    "commit": "<sha>", "tree": "<sha>"
  },
  "wall_seconds": 97.12,
  "status": "MEASURED",
  "reason": null,
  "observer": {"name": "cgprofile", "version": "1.2.0", "contract": 3,
               "mode": "process-tree", "window": "w-…", "sample_interval_seconds": 0.25},
  "metrics": {
    "rss_peak_bytes": 390692864,
    "memory_peak_bytes": null,
    "swap_peak_bytes": null,
    "cpu_seconds": 95.37, "cpu_user_seconds": 91.02, "cpu_system_seconds": 4.35,
    "io_read_bytes": 0, "io_write_bytes": 1732608,
    "io_read_ops": null, "io_write_ops": null,
    "psi_memory_some_seconds": null, "psi_memory_full_seconds": null,
    "psi_cpu_some_seconds": null, "psi_io_some_seconds": null, "psi_io_full_seconds": null
  },
  "missing": {
    "memory_peak_bytes": "REQUIRES_CGROUP_MODE", "swap_peak_bytes": "REQUIRES_CGROUP_MODE",
    "io_read_ops": "REQUIRES_CGROUP_MODE", "io_write_ops": "REQUIRES_CGROUP_MODE",
    "psi_memory_some_seconds": "REQUIRES_CGROUP_MODE", "psi_memory_full_seconds": "REQUIRES_CGROUP_MODE",
    "psi_cpu_some_seconds": "REQUIRES_CGROUP_MODE", "psi_io_some_seconds": "REQUIRES_CGROUP_MODE",
    "psi_io_full_seconds": "REQUIRES_CGROUP_MODE"
  },
  "sources": {"rss_peak_bytes": "sampled-sum-rss", "cpu_seconds": "proc-stat-exact",
              "io_write_bytes": "proc-io-lower-bound"},
  "context": {
    "container_psi_memory_full_seconds": 0.0,
    "host_psi_memory_full_seconds": 0.4,
    "container_memory_events_local": {"high": 0, "max": 0, "oom_kill": 0},
    "ancestors_memory_events_local": [{"path": "/dev.slice/dev-gates.slice", "high": 0, "max": 0}]
  }
}
```

### 2.1 Field semantics

- **`metrics` and `missing`.** Every key is always present in `metrics`. A `null` value has its reason in `missing`.
  Nothing is ever silently absent.
- **`wall_seconds`** is the subject's own monotonic duration, measured by the writer (assay or run-gate). It is not a
  cgprofile reading, so it is present even when `status` is `NO_MEASUREMENT`. Duration predictions depend only on it.
- **`status`** reflects only the cgprofile metrics:
  - `MEASURED` means every metric that the mode can supply is present.
  - `PARTIAL` means some metric the mode should supply is missing. Examples: `io.stat` is not readable because the IO
    controller is not enabled, or the kernel cannot reset `memory.peak`.
  - `NO_MEASUREMENT` means there are no cgprofile metrics at all; `reason` holds a single code.
- **Reason codes:**

  | Code | Meaning |
  |---|---|
  | `OBSERVER_UNAVAILABLE` | Socket absent or not mounted |
  | `OBSERVER_REFUSED` | `peer-refused` or `observer-refused` |
  | `OBSERVER_CONTRACT_UNSUPPORTED` | The daemon does not advertise contract 3 |
  | `OBSERVER_ERROR` | `daemon-error`, timeout, or malformed reply |
  | `WINDOW_LOST` | Daemon restart or window expiry during the window |
  | `NOT_STARTED` | The subject never ran |
  | `SHARED_CGROUP` | Cgroup mode was not exclusive (§3.2) |
  | `REQUIRES_CGROUP_MODE` | Metric has no meaning in process-tree mode |
  | `CONTROLLER_DISABLED` | `io` or `memory` controller not enabled on the leaf |
  | `PEAK_RESET_UNSUPPORTED` | Kernel lacks the per-file reset of `memory.peak` |

- **`rss_peak_bytes`** is the maximum, over samples, of the summed RSS of the subject's process tree. It is a lower
  bound on the true peak because short spikes between samples are missed. This is the quantity assay recorded until
  now, which keeps history comparable and makes it the worker-sizing input.
- **`memory_peak_bytes`** is the exact cgroup charge peak, page cache included. It exists in exclusive cgroup mode only.
- **`context`** is not attributed to the subject. It is the containing cgroup and host pressure during the window, and
  consumers use it to judge contamination (§6.1).
- **Who decides contamination.** A profile never carries a contamination verdict: run-gate is the single owner of that
  rule (§6.1). A run-gate record adds `"contamination": {"flagged": bool, "reasons": [...]}` when it stores the profile.

### 2.2 Writers and readers

| Producer | What | Where it is stored |
|---|---|---|
| cgprofile | numbers for one window (§3); it stores nothing per subject | the reply to `window-close` |
| assay | one profile per started R2 candidate | progress event field `profile`, mutation state record (optional key `profile`), `profile-<lane>.jsonl` next to the progress stream |
| run-gate pytest plugin (§6.3) | one profile per xdist worker and one for the controller | `$RUN_GATE_PROFILE_DIR/*.json`, collected into the lane-run record |
| run-gate | one lane-run record: per-worker distributions plus contamination, with raw subject profiles | `.run-gate/profiles/<lane>.jsonl` (append-only, bounded), plus a summary key in the `history.json` v2 entry |

| Consumer | Reads |
|---|---|
| run-gate R2 `jobs=auto` | clean (uncontaminated) `rss_peak_bytes` p95 of the **normal class** (§5.3), and `cpu_seconds/wall`, of recent candidate profiles |
| run-gate `workers=auto` | clean per-worker distributions from `.run-gate/profiles/<lane>.jsonl` |
| assay scheduler | `wall_seconds` and `rss_peak_bytes` per candidate id from the previous campaign's state and profiles |
| mdt-cockpit | none (its live probe reads host configuration, §7.4) |

## 3. cgprofile observer contract 3: measurement windows

### 3.1 Wire

These additions go on the existing observer socket, `/run/cgprofile/observer/cgprofile.sock` (the per-level layout; the earlier name was `resource-observer.sock`).
`resource-limits` keeps contract 2 unchanged, and the control socket stays at contract 1.

```json
{"verb":"window-open","args":{"thread_id":1234,"root_pid":5678,"mode":"process-tree","interval_ms":250},"contract":3}
→ {"ok":true,"contract":3,"window":"w-<128-bit hex>","mode":"process-tree","started_at":"…","kernel":{"peak_reset":true}}

{"verb":"window-close","args":{"thread_id":1234,"window":"w-…"},"contract":3}
→ {"ok":true,"contract":3,"window":"w-…","metrics":{…},"missing":{…},"sources":{…},"context":{…},
   "attribution":{"mode":"process-tree","foreign_pids_max":0,"tree_pids_max":7,"samples":389}}
```

- **Binding.** The peer, its PID and its UID come from `SO_PEERCRED` and the TID proof, exactly as in §9.2 of the
  contract.
  - `root_pid` is given in the peer's PID namespace. The daemon maps it through `NSpid`.
  - The daemon requires `root_pid` to be the peer itself or a live descendant of the peer, proven by the
    parent-process chain, sharing the peer's cgroup or one below it.
  - Start times are rechecked, as for contract 2.
  - A window belongs to the peer process that opened it. Closing it from another peer gives `peer-refused`.
- **Modes.**
  - `process-tree` works in a shared container. Every sampling interval the daemon walks the descendants of
    `root_pid`. It records:
    - the summed RSS, from `/proc/<pid>/statm`;
    - per-process `read_bytes` and `write_bytes` from `/proc/<pid>/io`. It keeps the last seen value of each exited
      process, so the sum is a lower bound and is labelled `proc-io-lower-bound`.
    - final CPU from the root's `utime + stime + cutime + cstime`. The consumer calls `window-close` after
      `waitid(WEXITED|WNOWAIT)` and before reaping the root, so the zombie's counters are complete. If the root was
      already reaped, CPU is the sampled lower bound, labelled `proc-stat-sampled`.

    IOPS and PSI have no per-process source, so they are `REQUIRES_CGROUP_MODE`. The containing cgroup's PSI and
    local memory-event deltas are returned in `context`.
  - `cgroup` takes exact numbers from the leaf that holds `root_pid`:
    - `memory.peak` and `memory.swap.peak`. The daemon opens them at window-open and writes to that open file to reset
      its local peak (Linux ≥ 6.12; M1 verifies the target kernels). Without that support these two metrics are
      `PEAK_RESET_UNSUPPORTED`; the daemon may additionally report its own sampled maximum, labelled `sampled-max`.
    - the `cpu.stat` usage delta;
    - `io.stat` `rbytes/wbytes/rios/wios` summed over devices;
    - PSI totals for memory, CPU and IO (`some`, plus `full` where it exists).
- **Exclusivity.** Cgroup mode is attributable only when the leaf holds nothing that could grow except the root's
  tree.
  - The daemon samples `cgroup.procs`. A PID counts as "foreign" unless it is in the root's tree, is the peer, or is
    one of the peer's ancestors within the same leaf: the container's init and the wrapper shell, which are idle
    while they wait. The daemon reports `foreign_pids_max`.
  - If `foreign_pids_max > 0`, every cgroup-only metric is null with `SHARED_CGROUP`.
  - With jobs=1 in a tester container this holds. The peer and its ancestors are present but idle, and their memory
    is the window's `baseline_bytes`, which is `memory.current` at open.
  - Consumers read `memory_peak_bytes − baseline_bytes` as the candidate's increment. It is an upper estimate, because
    the judge may grow slightly.
  - With jobs>1 exclusivity holds only once every worker has its own leaf (CP-23).
- **Bounds.**
  - At most 64 open windows per peer, and 4096 per daemon.
  - A window that is never closed expires after 6 h and returns `WINDOW_LOST` on a late close.
  - `interval_ms` is limited to 100–5000.
  - Every request keeps the existing 4096-byte line limit and 5 s deadline. `window-close` must answer within 5 s, so
    sampling is never done synchronously at close.
- **Version advertising.** `version.resource_observer` gains `"contracts": [2, 3]`, which is additive. The existing
  `"contract"` key stays at 2 until contract 2 is retired, so a 1.1.x consumer reads the same value as before.

### 3.2 Fallback behaviour (D1)

| Situation | Consumer behaviour |
|---|---|
| Socket path absent or unmounted | Profile `NO_MEASUREMENT/OBSERVER_UNAVAILABLE`; the lane runs normally |
| `contracts` lacks 3, or the reply is `bad-argument` for the window verbs | `NO_MEASUREMENT/OBSERVER_CONTRACT_UNSUPPORTED`; asked once per run, never retried per candidate |
| `window-open` refused or erroring for one subject | That subject `NO_MEASUREMENT/<code>`; the next subject tries again; after 3 consecutive failures the run stops asking (`OBSERVER_ERROR`) |
| Daemon restarted mid-window | `WINDOW_LOST` for the open windows |

- No consumer reads `/proc` or cgroup files to fill a gap.
- A missing measurement never changes a verdict, a bucket or an exit code. It only turns off parallel sizing, which
  falls back to 1 worker (§6.2).
- **Liveness is unaffected.** Assay's liveness watchdog keeps its internal CPU-progress sampling as a control loop: it
  decides hangs, it is not a measurement. Its numbers stop being recorded (§4.1, and REVIEW-NOTES O1).

### 3.3 Release

- **Version.** This ships as cgprofile **1.2.0**, a minor release: contract 3 is additive and contract 2 stays accepted
  for every 1.x release.
- **Golden fixtures.** `fixtures/rg55/observer-window-v3-{open,close}.json` are vendored byte-identical into the assay
  and run-gate tests, the same rule as for summary-v1.

## 4. Assay: profiles instead of its own numbers, and the schema plan

### 4.1 Number source

- **One window per candidate.** For every started native R2 candidate, the worker opens a window on the candidate's
  root process right after spawn and closes it after `waitid(WNOWAIT)`:
  - `cgroup` mode when jobs=1;
  - `process-tree` mode when jobs>1, until CP-23 provides leaves.
- **Orphans.** The candidate root sets `PR_SET_CHILD_SUBREAPER` so orphaned grandchildren stay in its tree.
- **What stops being recorded.** The liveness tally's `cpu_seconds`, `peak_rss_bytes` and `write_bytes` are no longer
  recorded anywhere, and its RSS and IO reads are deleted. Liveness keeps only its CPU-progress reading of
  `/proc/<pid>/stat`, as a hang-detection control loop that is never recorded (REVIEW-NOTES O1). The resource sidecar
  keeps only the spawn time.
- **No aliases; the old keys are removed outright in 12.0.** This matches the operator's no-alias rule for host-setup
  keys. The removed progress, state and analysis keys are `peak_rss_bytes`, `cpu_seconds`, `write_bytes`,
  `judge_rss_bytes` and the `resources` object. Their only reader outside assay is run-gate's
  `assay_progress_candidate_ram`, and run-gate 24.4.0, which reads both shapes, ships first (§8).
  - An older run-gate meeting assay 12 output finds no samples and falls back to its declared or default candidate
    RAM, which is conservative and never wrong.
  - `judge_rss_bytes` becomes the window's `baseline_bytes` in cgroup mode.
- **Observer availability.**
  - The required B145 limit-event observer and the new profile windows share the socket and the preflight.
  - A missing observer still makes R2 a `NO_MEASUREMENT` lane outcome where B145 requires counters. That is unchanged,
    and it concerns the limit events, not the numbers.
  - On lanes where B145 does not apply, a missing observer only produces `NO_MEASUREMENT` profiles.

### 4.2 Schema plan (assay verdict is v17)

| Artifact | Change | Kind | Why |
|---|---|---|---|
| Progress stream (`candidate` events) | New keys `profile` (resource-profile/1), `dispatch_position`, `prediction` and `prediction_error`. `candidate_index` keeps its canonical meaning. The old numeric keys are **removed**. | **Hard cut in 12.0** (no alias) | The only external reader is run-gate. 24.4.0 reads both shapes and is released before assay 12, so the window lives in the reader, not in assay. |
| Mutation state record | Optional key `profile`; the `_STATE_RESOURCE_KEYS` numbers are no longer written (`phase_seconds` and `startup_seconds` stay: they are assay's own clock); `MUTATION_STATE_SCHEMA_VERSION` stays 1 | Additive (optional keys) | Same reasoning as the existing comment at `mutation.py:263-275`. Resume treats an old record without `profile` as one without a measurement. |
| `profile-<lane>.jsonl` | New file, `resource-profile/1` lines | New | Lets a consumer read profiles without parsing progress. |
| Verdict | **v18, hard cut.** Adds `judgment.r2.profile` (counts by status, counts by reason code, observer name/version/contracts, modes used) and `judgment.r2.schedule` (policy, class thresholds and factors, admission budget, N, prediction-source counts, the error summary of §5.7). It carries B203 (per-record worker count and campaign maximum) and B205 (first failing test id per killed candidate, optional string). **No per-candidate numbers in the verdict.** | Hard cut | (1) The verdict schema is `additionalProperties: false`, so every addition already forces every validator to change. (2) Assay's established practice is a hard cut with refusal of the previous version (v16 → v17, B202). (3) B203 and B205 already need a verdict change; one bump costs consumers one migration instead of three. (4) Profiles are telemetry, not judgment, so they stay out of the per-candidate verdict and cannot destabilise it. |
| `analysis-campaign` schema | The candidate's `peak_rss_bytes` and `cpu_seconds` are replaced by `profile` | Analysis schema major, released with assay 12 | The analyzer still reads v17 verdicts and old progress for archived campaigns (read-only), and writes only the new shape. |

Release: **assay 12.0.0**, a major release because of the v18 cut. It requires run-gate ≥ 24.4.0, which reads v17 and
v18 verdicts.

## 5. Candidate scheduling for parallel R2 (D2, refined by the operator: two-ended matching)

**Goal (operator).** The RAM need of a parallel run must be **predictable**, so that it is known what parallelism a host
can run and what other load it can share the host with. RAM is the scarce resource. Wall time is secondary, and
**results never depend on the order** (§5.8).

### 5.1 Why longest-first reduces idle workers at the end

- **The idle cost is the last long job.** Workers pull the next candidate when they become free, and the run ends when
  the last candidate ends.
  - If the last candidate to start is long, the other N−1 workers finish their final jobs and sit idle for most of its
    duration, at a cost of about (N−1) × d_last.
  - Started early, long candidates overlap with everything else. The tail then consists only of short candidates.
    These fill the gaps: whichever worker frees up first takes the next short one, and all workers finish within about
    one short duration of each other.
- **The general bound.** List scheduling gives makespan ≤ W/N + (1 − 1/N)·d_last, where W is the total work and d_last
  is the duration of the job that finishes last. Longest-first makes d_last one of the shortest jobs, which guarantees
  (4/3 − 1/(3N)) × optimal (Graham), against up to 2 − 1/N for an arbitrary order.
- **Predictions only need the ranking roughly right.** A misprediction swaps neighbours; it does not put a 300 s job
  last.

### 5.2 Why the RSS term is a smoothing term, and why two-ended matching

- **The worst case of an unordered run is N co-running high candidates.** N × max RSS is 5,267 MiB for IOBench at
  N=6, against a typical 2,238 MiB.
- **Sizing for p95 with no ordering control** leaves that worst case to chance. The tail of the distribution then
  decides the cgroup limit, and with it whether the lane slows down in reclaim, or is OOM-killed, while other load
  shares the host.
- **Two-ended matching (TEM) controls the coincidence instead of the size.**
  - At most one **high** candidate runs at a time.
  - While it runs, freed slots prefer **low** candidates. These are the short ones as well: in the IOBench data the low
    class has a median of 35 s, against 98 s for normal and 110 s for high.
  - The running sum then stays near H + (N−1) × normal, instead of rising toward N × H.
  - Highs start **early**, as soon as no other high runs, so their worst case is bounded early and their duration does
    not form the tail.
  - Lows double as gap fillers at the end of the run.

### 5.3 Classes, derived from the data

- **Classes come from the *predicted* RSS of each candidate**, relative to the lane's own distribution; there are no
  hard-coded MiB values.

  ```
  mode         = the most populated 50 MiB bucket of predicted RSS ± one bucket
  high         : predicted RSS > 1.5 × p95(mode)
  low          : predicted RSS < 0.5 × median(mode)
  normal       : otherwise
  ```

- **The factors** 1.5 and 0.5 are lane-config parameters (`mutation.schedule_high_factor` and
  `mutation.schedule_low_factor`) that default to these values.

  | Data set | Mode median / p95 | High threshold | Highs | Low threshold | Lows |
  |---|---|---|---|---|---|
  | IOBench | 372 / 374 MiB | > 561 MiB | 3 of 1,220 | < 186 MiB | 116 |
  | cli-extended | 82 / 88 MiB | > 132 MiB | 51 of 1,456 (median 33 s against 9 s for normal) | < 41 MiB | 0 |

### 5.4 Algorithm (two-ended matching with longest-first among equals; recommended)

```
inputs : candidates C (canonical A-180 order), N workers, admission budget B bytes (both from run-gate)
         prediction p(c) = (d̂ wall, r̂ RSS, source) — 5.6
queues : Q_H, Q_N, Q_L = candidates of each predicted class, each sorted by (d̂ desc, canonical_index asc)
seq ← 0
on start and on every completion, while a worker is free:
    R ← Σ r̂(running)
    high_running ← any running candidate is in class H
    order ← [L, N] if high_running else [H, N, L]
    for class k in order with Q_k non-empty:
        c ← head(Q_k)
        if R + r̂(c) ≤ B or nothing is running:        # admission rule (hard)
            start c; dispatch_position(c) ← seq++; break
        elif k = H:
            stop filling: wait for completions           # the high slot: let the running sum drain
        else:
            try the next class
    if nothing started: wait for the next completion
```

- **The admission rule.** A candidate starts only if `Σ predicted RSS of running + predicted RSS of next ≤ B`, and at
  most one high candidate runs at a time.
  - **High slot, no starvation.** A queued high that does not fit stops further filling until enough candidates
    complete. Without this rule, a budget-limited run kept refilling with normals, and the highs waited until the tail.
    In the cli-extended simulation at N=6 with a 600 MiB budget, that cost 33% in makespan before the rule was added (3,636 s against 2,730 s).
  - **One candidate always runs**, even alone over B. It is then capped by the lane's enforced `memory.max` like any
    other.
- **Dispatch order is the drain key.** `_CandidateEventBuffer` and `_record_ready_in_order` drain contiguously by
  `dispatch_position` instead of canonical index. Progress and state are then written as soon as the dispatch prefix
  completes. Draining by canonical index would hold every event behind a low-index candidate that was dispatched late,
  stall the progress stream that liveness reads, and lose finished work on interruption.
- **`candidate_index` keeps its canonical meaning.** `dispatch_position`, `prediction` and `prediction_error` are new
  progress keys.
- **`schedule.json`** in the state directory records the policy, class thresholds, factors, B, N, the digest of the
  prediction inputs, the counts of each prediction source, and the error summary (§5.7).

### 5.5 Evaluation (simulation replaying the recorded per-candidate peak RSS and elapsed time)

- **Tool and full output.** `resource-profiles-sim/sched-sim.py` (stdlib), with the full tables in `resource-profiles-sim/sim-iobench.md`,
  `resource-profiles-sim/sim-cli-extended.md` and `resource-profiles-sim/sim-budget.md`.
- **Model.** Each candidate holds its recorded peak RSS for its whole recorded elapsed time. This is a conservative
  envelope, because real peaks are momentary.
- **Metrics.**
  - The running RSS sum is reported as its maximum and its time-weighted p99 and p95.
  - Tail idle is the sum over workers of (makespan − that worker's last finish).
  - The "oracle" predictions are the candidate's own recorded values. They stand for "the previous profile of the same
    candidate id".
  - The "file" predictions are per-file medians, the fallback for a new id.

**IOBench (1,220 candidates, 31.7 h of work), oracle predictions, no budget:**

| N | Policy | Makespan s (lower bound) | RSS sum max | p99 | p95 (MiB) | Tail idle worker-s |
|---|---|---|---|---|---|---|
| 3 | file order | 38,090 (38,074) | 1,624 | 1,391 | 1,120 | 47 |
| 3 | LPT | 38,080 | 1,377 | 1,145 | 1,121 | 17 |
| 3 | TEM | 38,134 | 1,167 | 1,130 | 1,120 | 180 |
| 3 | **TEM + LPT** | **38,080** | **1,182** | 1,128 | 1,120 | **17** |
| 6 | file order | 19,153 (19,037) | 2,741 | 2,725 | 2,238 | 697 |
| 6 | LPT | 19,048 | 2,658 | 2,394 | 2,239 | 68 |
| 6 | TEM | 19,187 | 2,287 | 2,252 | 2,237 | 898 |
| 6 | **TEM + LPT** | **19,046** | **2,286** | 2,248 | 2,239 | **55** |

**cli-extended (1,456 candidates, 4.0 h of work), oracle predictions, no budget:**

| N | Policy | Makespan s (lower bound) | RSS sum max | p99 | p95 (MiB) | Tail idle worker-s |
|---|---|---|---|---|---|---|
| 3 | file order | 4,856 (4,854) | 753 | 695 | 681 | 7 |
| 3 | LPT | 4,856 | 753 | 741 | 686 | 8 |
| 3 | TEM | 4,858 | 426 | 420 | 411 | 12 |
| 3 | **TEM + LPT** | **4,855** | 465 | 428 | 419 | **4** |
| 6 | file order | 2,431 (2,427) | 1,404 | 1,374 | 1,211 | 22 |
| 6 | LPT | 2,430 | 1,492 | 1,482 | 1,371 | 20 |
| 6 | TEM | 2,432 | 748 | 683 | 672 | 31 |
| 6 | **TEM + LPT** | **2,427** | **742** | 695 | 681 | **3** |

**The same admission rule applied to every policy (oracle predictions):**

| Case | File order | LPT | TEM | TEM + LPT | Lower bound |
|---|---|---|---|---|---|
| IOBench, N=3, B = 1,200 MiB: makespan s | 38,417 | 38,185 | 38,134 | **38,080** | 38,074 |
| cli-extended, N=6, B = 600 MiB: makespan s | 2,906 | 2,998 | **2,722** | 2,730 | 2,427 |

In every case the maximum running sum stays at or below B.

**With per-file-median predictions** (no per-candidate history):

- IOBench: TEM collapses to file order. Every file median is "normal": per-file medians are flat at 93–106 s and
  370–373 MiB, so no candidate is predicted high or low.
- cli-extended: TEM still lowers the p95 of the running sum at N=6 from 1,211 to 807 MiB (TEM + LPT), but not the
  maximum (1,149 against 1,404 MiB).
- Highs therefore have to be known **per candidate**; file medians cannot find them.

**Reading of the results:**

1. **TEM + LPT is the recommendation.**
   - It has the lowest or near-lowest running-RSS maximum in every row. The maximum falls by 17% for IOBench at N=6
     (2,741 → 2,286 MiB) and by 47% for cli-extended at N=6 (1,404 → 742 MiB). Within a few MiB of that, p99 equals
     p95: the running sum is flat.
   - It has the best or equal makespan, within 0.05% of the lower bound without a budget, and the lowest tail idle.
   - Pure TEM (file order inside each class) loses up to 898 worker-seconds at the tail because it does not front-load
     long candidates. Longest-first among equals removes that loss.
2. **LPT alone does not control RAM.** For cli-extended it raises the peak, because the long candidates are the high
   ones and LPT starts them together.
3. **Under an admission budget, TEM turns the budget into throughput.** It is −6% makespan against file order for
   cli-extended at N=6, where file order and LPT must idle workers until a high fits.
4. **The predictable RAM need of a lane is its admission budget B.** The scheduler never plans a running sum above B,
   so parallelism is N = ⌊B / p95(normal class)⌋, and a high runs alongside fewer normals or with lows.
   - The analytic envelope with at most one high is H_max + (N−1) × high threshold. For cli-extended at N=6 that is
     256 + 5 × 132 = 916 MiB, and the simulation's maximum was 742.
   - What the operator plans with is B, plus the enforced lane `memory.max` above it as the safety cap.

### 5.6 Predictions and the cold start

| Order | Source | Used when |
|---|---|---|
| 1 | The same candidate id's last profile: `wall_seconds`, and `rss_peak_bytes` if present and not flagged contaminated by run-gate | The id is unchanged. The id covers the mutated content, so it is stable while the file is unchanged. |
| 2 | Live file median of **this** run | At least 5 candidates of the file have completed in this run |
| 3 | File median of the latest campaign | The file was profiled before |
| 4 | Lane median of the latest campaign | Any profile exists |
| 5 | Operator medians in the lane config (`mutation.prediction_wall_seconds`, `mutation.prediction_rss_bytes`) | Declared |
| 6 | None | Canonical order. Without RSS predictions every candidate is "normal", so admission uses the declared or default candidate RAM, as `jobs=auto` does today. |

- **Cold start.** With no history at all, run-gate's `jobs=auto` already falls back to its conservative default
  estimate. IOBench attempt 01 ran N=1 with the 1 GiB default.
  - The first run, at N=1 or a conservative N, builds the per-candidate profiles. Live file medians (source 2) improve
    the order within that run.
  - The next run, or the resume, re-selects N from the measured normal-class p95. That is what IOBench attempt 02 did:
    N=3 after 234 samples.
  - Highs become schedulable as highs once they have been measured once (source 1).
- **Predictions never feed classification, buckets or budgets.**

### 5.7 Recording predicted-versus-actual error (so predictions improve)

- **Per candidate.** The progress event carries
  `prediction: {rss_peak_bytes, wall_seconds, class, source}` at dispatch. At completion it carries
  `prediction_error: {rss_ratio, wall_ratio, class_actual}`, where a ratio is actual / predicted and `class_actual`
  uses the same thresholds. The actual values are the cgprofile profile's (D1). Without a measurement, `rss_ratio` is
  null with the profile's reason.
- **Per run** (`schedule.json` and the verdict's `judgment.r2.schedule`):
  - p50 and p95 of `rss_ratio` and `wall_ratio` for each prediction source;
  - the class confusion matrix (predicted × actual);
  - admission overshoots: the number of moments at which the sum of the *actual* peaks of the running candidates, an
    upper envelope computed after the run, exceeded B.
- **Run-gate** prints the overshoot count and the rate of highs that were mispredicted as normal in the lane footprint.
  More than 0 overshoots in two runs in a row raises B's margin (RG-143's "repeated RAM-target overshoot").
- **How it improves.** The next run reads each candidate's actual values as source 1, so error falls to that of an
  unchanged id. The per-source error table shows when file medians are too coarse; for IOBench they are, see §5.5.

### 5.8 Results never depend on the order: assumption and isolation requirement

- **The assumption.** Every candidate is an independent experiment:
  - it runs in its own CoW clone of the tree, in a fresh process tree;
  - its outcome is a function of (tree + mutation, test command, environment) only.
- **The isolation requirement.** It holds for every lane with jobs > 1, and it is the same list as for xdist suites
  (§6.2):
  - no writes outside the clone, except per-candidate temporary directories;
  - caches (`.pytest_cache`, `__pycache__`, tool caches) inside the clone or disabled;
  - no shared fixed ports, container names, networks or files between concurrently running candidates;
  - no test reads another candidate's output;
  - tests are deterministic.
  - Suite flakiness violates the last point. It is a suite property that also breaks serial runs (B204), and the B204
    kill confirmation and the serial `--rejudge` are its remedies.
- **How it is verified.**
  - The scheduler acceptance test runs a fixture lane in canonical and scheduled order and requires identical
    judgments.
  - M4 (§9) repeats this on 150 real IOBench candidates.
  - Every N>1 R2 run in M2 is compared bucket-for-bucket with N=1.
- **State and verdict** are keyed by `candidate_id`, and the verdict's candidate list is sorted canonically,
  independent of dispatch.
- **The lane budget.** When it expires early, the pending set differs from the canonical one. The pending set is a
  property of the wall clock, not of the results, and resume completes it.

## 6. Run-gate

### 6.1 "Time stalled on memory" is a contamination flag (D4)

- **What the number measures.** `memory.pressure` `full` in a lane cgroup counts the time when all of the lane's tasks
  were stalled on memory. The stall can be caused by:
  - (a) the lane's own `memory.high`/`memory.max` (a lane property);
  - (b) reclaim driven by an ancestor's limit or global reclaim, which comes from parallel load and host pressure (not
    a lane property).
- **One rule, owned by run-gate.** For every lane run and every subject profile:

  ```
  own_limit = Δ memory.events.local (high + max + oom) of the lane leaf      # from Summary.events / profile.context
  anc_limit = Δ memory.events.local (high + max) of any ancestor up to and including dev.slice
              (the observer's contract-2 ancestry walk already returns these local counters)
  stall     = lane memory full stall seconds (Summary.pressure / profile psi or context)
  guard     = MDT PSI guard actions overlapping the window (§7.5 events file, if readable)

  self_limited  ← own_limit > 0                                        # lane property → capacity signal
  contaminated  ← anc_limit > 0                                        # reclaim driven from above the lane
                  or guard non-empty                                   # the host deliberately throttled dev tiers
                  or (stall ≥ max(2 s, 0.5% of wall) and not self_limited)   # stall not explained by the lane's own limit
  (the two flags are independent: a run can be both)
  host-wide /proc/pressure figures are recorded as context and printed, never a criterion
  ```

  - **Why host-wide PSI is not a criterion.** On the shared host, the production service's own swapping keeps
    host-wide memory `full` well above 1%. A host-wide criterion would flag every run there and leave no sizing
    basis.
  - **Ancestor events.** `anc_limit` needs the ancestor local events in the Summary and window context (cgprofile
    CP-40). Until then the criterion is skipped and recorded as `ancestor_events: unknown`: it neither flags nor
    clears a run.

  The thresholds are named constants (`CONTAMINATION_*`). M5 (§9) calibrates them.
- **Use.**
  - A contaminated sample or run is stored with `contamination.flagged = true` and its reasons. It is **excluded** from
    every sizing and estimate basis: `candidate_ram_from_history`, the R2 `jobs=auto` and lane `workers=auto` inputs,
    and the duration medians.
  - The footprint line prints it as `measurement contaminated (2.2 s memory stall under host pressure); excluded from
    sizing` instead of a stall figure next to the capacity numbers.
  - `self_limited` prints as `memory limit reached N times` and is a capacity signal ("raise the lane memory cap"). It
    is not contamination.
  - If fewer than the minimum number of clean samples remain, the basis falls back to the declared or default value,
    as when nothing has been measured. Contaminated data is never used as the basis.
- **R2 closeout** lists `budget_exceeded` candidates whose profile is contaminated. It prints the exact
  `run-gate r2 --jobs 1 --rejudge <id>` lines (the manual procedure used for the IOBench five), and with
  `--rejudge-contaminated` it runs them serially. This follows the 2026-09-14 rule: a run affected by pressure is
  inconclusive and retried, never silently counted as a product result.

### 6.2 `workers` for non-R2 lanes (xdist)

- **Lane keys.**

  ```toml
  [lanes.unit]
  kind = "command"
  environment = "tester-unified"
  argv = ["bash", "-c", "cd {worktree}/lib && exec python -m pytest -p xdist -n {workers} -q"]
  workers = "auto"        # int ≥ 1 | "auto"; absent = 1 (unchanged behaviour)
  workers_max = 4
  xdist_safe = true       # required for workers ≠ 1 (see isolation below)
  [lanes.unit.resources]
  memory = "4g"           # enforced caps REQUIRED when workers ≠ 1 (as for native R2)
  cpus = "3"
  ```

- **Argument and baseline.**
  - `{workers}` is a declared argv token. Run-gate never rewrites argv, and a lane with `workers` but no token is
    refused. `RUN_GATE_WORKERS` is exported as well.
  - `workers = 1` substitutes `1`: one xdist worker plus the controller, the same process structure as N workers.
    That makes it the scaling baseline.
  - `-n 0` (no xdist) is the legacy shape, and its numbers are not per-worker.
- **`auto` formula**, using only clean samples (§6.1), from the last ≥ 2 runs or the latest scaling run:

  ```
  w_rss  = p95 per-worker rss_peak_bytes        ctl_rss = p95 controller rss_peak_bytes
  w_core = p50 per-worker cpu_seconds / wall_seconds   (≥ 0.25: an IO-waiting worker frees its core — overcommit by design)
  w_wbps = p95 per-worker io_write_bytes / wall_seconds
  workers = max(1, min(workers_max,
                       floor(cpu_cap_cores / w_core),
                       floor((memory_cap × ram_target − ctl_rss) / w_rss),   # ram_target = R2's 0.75
                       floor(io_write_cap_bps / w_wbps)  if the lane container has an io write cap,
                       floor(tests_collected / 20)))                         # tests_collected from the last run
  ```

  - With no clean profile, `auto` selects 1 and prints why (consistent with D1: no numbers, no parallelism).
  - The selection, every input and the basis source are recorded in the run record, as `r2_jobs` is today.
- **Enforced caps.**
  - `workers ≠ 1` requires `[resources] memory` and `cpus` on ephemeral lanes. Docker enforces them, and they are the
    cap the auto formula divides.
  - Bare-host and exec lanes additionally require cgprofile placement (CP-9). Without it, `workers ≠ 1` is refused.
  - The same native R2 requirements apply unchanged.
- **Isolation requirements for an `xdist_safe` suite.** These are reviewed in code and checked at run time.

  | Shared resource | Requirement |
  |---|---|
  | Docker objects (containers, networks, volumes) | Names include `PYTEST_XDIST_WORKER` and the run id. Cleanup is by exact name only (estate rule). |
  | Ports | Bind port 0 and read back the assigned port, or use a range per worker derived from the worker index. No fixed ports. |
  | Temporary paths | `tmp_path` or `tmp_path_factory` only. No fixed `/tmp/<name>` and no writes to `$HOME` or `~/.cache` (`HOME` and `XDG_*` point into a per-worker temporary directory). |
  | Session-scoped external services | One instance per worker, or a `filelock`-guarded shared instance with an explicit, tested contract. |
  | Process-global state | No `os.environ` mutation without `monkeypatch`. No `chdir` without restoring it. No global git config writes. |
  | Order dependence | The suite passes with `-p randomly` (where available) and with `--dist loadfile`. |

  - **Checked at run time.** When a test fails at N > 1, run-gate re-runs the failed tests once with `-n 1 --lf`.
    - A failure that passes serially makes the lane `INCONCLUSIVE/XDIST_ISOLATION`, never PASS, and names the tests.
    - A failure that also fails serially is a real FAIL.
  - **First enablement.** `run-gate <lane> --xdist-parity` runs N=1 and N=k and compares per-test outcomes (junit
    XML). It stores the proof in history, keyed by the lane config digest.

### 6.2b Native R2 `jobs=auto` with two-ended matching

- **The selection rule.**

  ```
  B      = memory_cap × ram_target (0.75, unchanged)            # the lane's predictable RAM need
  r_norm = p95 rss_peak_bytes of the clean, normal-class candidate profiles (§5.3 thresholds)
  c_core = p50 cpu_seconds / wall_seconds per candidate (floor 0.25)
  jobs   = max(1, min(jobs_max, floor(cpu_cap / c_core), floor(B / r_norm), remaining))
  ```

  - Run-gate passes `--jobs` and **`--memory-budget-bytes B`** to assay, which uses B as the admission budget (§5.4).
  - The high candidates no longer lower N. They are handled by the at-most-one-high and admission rules: a high runs
    alongside fewer normals, or alongside lows.
- **Example (IOBench).** On a lane cap of 1,536 MiB, B is 1,152 MiB, and r_norm = 374 MiB still gives N = 3. A single
  878 MiB candidate is admitted once the running sum has drained to ≤ 274 MiB, or when it is alone.
- **Reporting.** The INFO line and the run record show B as "predictable RAM need", together with r_norm, the class
  counts and the last run's admission-overshoot count (§5.7).

### 6.3 Per-worker numbers from cgprofile (pytest plugin)

- **What ships.** The run-gate wheel provides a pytest plugin entry point, `run_gate_profile`, active only when
  `RUN_GATE_PROFILE_DIR` and the observer socket variable are set.
- **What it does.** In the controller and in each xdist worker it opens a `process-tree` window rooted at its own PID
  at `pytest_configure`, and closes it at `pytest_unconfigure`. It writes one resource-profile/1 file per process with
  `subject.kind = xdist-worker | controller`, `id = gw<N> | controller`, and the number of tests run.
- **Mounting.** Run-gate mounts the observer directory read-only (the existing §9.3 rules, extended to command lanes
  with `workers`), sets the variables, and collects the files after the lane.

### 6.4 Per-lane history record

- **Raw profiles.** Each run appends one record to `.run-gate/profiles/<lane>.jsonl`. The file is append-only and
  bounded to the last 200 runs or 20,000 subject profiles, rotated oldest-first. Raw samples are kept so that
  percentiles can be recomputed honestly across runs: the existing comment notes that stored p95s cannot be recombined.
- **Record shape.**

  ```json
  {"schema": "lane-profile/1", "lane": "unit", "run_id": "…", "commit": "…", "tree": "…",
   "workers": 3, "workers_basis": {"mode": "auto", "source": "scaling", "inputs": {…}},
   "wall_seconds": 212.4, "status": "MEASURED", "contamination": {"flagged": false, "reasons": []},
   "self_limited": {"memory_events_high": 0, "memory_events_max": 0},
   "per_worker": {
     "rss_peak_bytes": {"p50": …, "p95": …, "max": …, "n": 3},
     "cpu_seconds":    {"p50": …, "p95": …, "max": …, "n": 3},
     "io_read_bytes":  {"p50": …, "p95": …, "max": …, "n": 3},
     "io_write_bytes": {"p50": …, "p95": …, "max": …, "n": 3},
     "wall_seconds":   {"p50": …, "p95": …, "max": …, "n": 3}},
   "controller": {"rss_peak_bytes": …, "cpu_seconds": …},
   "lane": {"memory_peak_bytes": …, "cpu_seconds": …, "psi_memory_full_seconds": …},
   "subjects": [ <resource-profile/1>, … ]}
  ```

- **R2 runs** use the same record with `per_worker` replaced by `per_candidate`: the same distribution keys over the
  run's candidates, read from the assay profiles.
- **`history.json` stays schema 2.** Its entries gain the optional key `profile_summary`: `per_worker` or
  `per_candidate` without `subjects`, plus the contamination fields. Older run-gate versions must still read the store;
  this is tested against the released 24.3.0 wheel.
- **Existing keys.** `r2_candidate_ram_p95_bytes` and `r2_candidate_ram_samples` stay and are now computed from clean
  profiles only.

### 6.5 Scaling mode

- **Command.** `run-gate <lane> --scaling 1,2,3,4 [--repeat 1:3,*:2]` runs the lane at each worker count in sequence:
  serially, one gate container at a time, with an enforced cap at each step. It runs the baseline first.
- **Output.** It writes the lane-profile records, prints the scaling table of §9, T2/T3, and computes:
  - speedup S(N) = T1/TN;
  - efficiency S(N)/N;
  - per-worker overhead: per-worker RSS at N against the baseline worker.
- **Native R2** uses `--scaling` with `--candidates <file or N>` on a stratified subset (§9 M2).

### 6.6 Compatibility

**Run-gate 24.4.0:**

- reads assay v17 and v18 verdicts;
- reads both progress shapes (assay ≤ 11 numeric keys, or assay 12 `profile`);
- writes `profile_summary` into history v2;
- adds the new lane keys, which are refused unless declared, with no default behaviour change;
- passes the memory budget to assay (`--memory-budget-bytes`, honoured by assay 12 only).

Run-gate 25.0 drops the v17 verdict reader and the old progress-key reader once no supported assay writes them.

## 7. mdt-cockpit host-setup: the MDT capacity boundary (D3, extends MDT-033)

### 7.1 Inputs

The three operator numbers name capacity that MDT must treat as **unavailable to itself**.

| Key | Meaning | Grammar | Default |
|---|---|---|---|
| `MDT_EXCLUDED_RAM` | RAM that other services on this host use, unavailable to MDT | size, `0` | `0` |
| `MDT_EXCLUDED_SWAP` | Disk swap only: swap space that other services use on the swap device, unavailable to MDT | size, `0` | `0` |
| `MDT_EXCLUDED_CPU_CORES` | CPU cores other services use, unavailable to MDT | decimal ≥ 0, one decimal place (e.g. `1.5`) | `0` |
| `DEV_TIER_EXCLUDED_CPU_CORES` | Cores that no single dev tier may use, on top of `MDT_EXCLUDED_CPU_CORES` (tier quota = `CPU_MDT − this`) | decimal ≥ 0, one decimal place | `0`: every tier may use all of MDT's cores. This is overcommit by design; weights decide. |
| `MDT_PROTECTED_UNITS` | systemd units of the host or production services to verify and probe (e.g. `wings.slice`) | space-separated unit names | empty |
| `MDT_HOST_SWAP_MARGIN` | Disk swap kept for the host | size | `512M` |

**Outright rename, no alias (operator update 2026-11-03).** The installer, the wizard and `check.sh` **refuse** a
configuration that contains an old key. The message names the new key and the value that keeps today's behaviour, for
example:
`DEV_CPU_RESERVE_CORES is no longer read: rename it to MDT_EXCLUDED_CPU_CORES (same meaning; your value 1 → MDT_EXCLUDED_CPU_CORES=1). See host-setup/README.md "Renamed keys (2026-11)".`
There is no mapping layer and no fallback read. The migration is a one-time documented rename:

| Old key (refused) | New key | Value that keeps today's effective behaviour |
|---|---|---|
| `DEV_CPU_RESERVE_CORES=c` | `MDT_EXCLUDED_CPU_CORES` | `c` (same meaning: cores that `dev.slice`'s auto CPU quota leaves out) |
| `DEV_SUBSLICE_CPU_RESERVE_CORES=k` | `DEV_TIER_EXCLUDED_CPU_CORES` | `max(0, k − c)`. Today a tier gets nproc − k, and from now on it gets nproc − c − (k − c). The README recommends `0` (overcommit) as the new value. |
| `DEV_MEMORY_RESERVE` (proposed in the first MDT-033 text, never shipped) | `MDT_EXCLUDED_RAM` | not applicable (nothing to migrate) |

**What changes for the rename:**

- `host-setup.env.example` (sections 2.1 and 3);
- `mdt-host-setup-wizard.py`: validators at `:829-830`, the CPU table at `:2485-2512`, help texts at `:2566` and
  `:2603-2606` (which today say "reserve"), and the docstrings at `:1299-1317`;
- `install.sh`, `units/dev-buildkitd.slice.in`;
- `README.md` (a new section "Renamed keys (2026-11)" holding the table above), `cgroup-cpu-swap-redesign-HANDOFF.md`;
- `tests/test_hs02.py`, `tests/test_buildkit_governance.py`.

A repository test asserts that no host-setup key name, prompt or help text matches `\breserve` (word boundary, so
"preserve" is unaffected), apart from the refusal
message and the README rename table. The active host-setup source lives in `mdt-cockpit/host-setup/`. The live
`/etc/mdt/host-setup.env` on each host is renamed by the operator with the README table, before the next install.

### 7.2 Derived sizing

```
RAM_MDT  = MemTotal − MDT_EXCLUDED_RAM                         (refuse < 1G)
SWAP_MDT = SwapTotal − MDT_EXCLUDED_SWAP − MDT_HOST_SWAP_MARGIN (warn ≤ 0, see below)
CPU_MDT  = nproc − MDT_EXCLUDED_CPU_CORES                       (refuse < 0.5)

dev.slice      MemoryMax = RAM_MDT, MemoryHigh = 92% RAM_MDT, MemorySwapMax = SWAP_MDT,
               CPUQuota = CPU_MDT × 100% (e.g. 350% for 0.5 excluded of 4)
tiers          MemoryMax/MemoryHigh as fractions of RAM_MDT (overcommitted on purpose; the sum of Max exceeds RAM_MDT):
               interactive 60/45 %, background 70/50 %, gates 70/50 %, buildkitd 35/25 %; interactive MemoryLow 15 %
               MemorySwapMax = DEV_SWAP_CASCADE_PCT % of SWAP_MDT (existing cascade, unchanged)
               CPUQuota = (CPU_MDT − DEV_TIER_EXCLUDED_CPU_CORES) × 100%   (default: CPU_MDT, overcommitted across tiers)
explicit values in the config always win and are the prompt defaults
```

- **Disk swap only (operator decision, 2026-11-03).** The excluded swap is the swap space the other services use on
  the swap device, read as their `memory.swap.current`.
- **`SWAP_MDT ≤ 0` leaves MDT without disk swap.** The wizard and `check.sh` warn and require confirmation.
- **The tier fractions are initial values.** M6 (§9) validates them. They replace the fixed fractions sized for 16 GB.
- **Worked examples.**

  | Host | Inputs | Result |
  |---|---|---|
  | Shared host (16 GiB / 69 GiB swap / 8 cores) | `MDT_EXCLUDED_RAM=6500M`, `MDT_EXCLUDED_SWAP=14G`, `MDT_EXCLUDED_CPU_CORES` = operator value | `RAM_MDT` ≈ 9.2 GiB, `SWAP_MDT` ≈ 54.5 GiB |
  | r1002 | all `0` | `RAM_MDT` = 7.75 GB, `SWAP_MDT` = SwapTotal − 512M, `CPU_MDT` = 4 cores (400%) |

  On r1002, `r1002-gates.slice` sits outside `dev.slice`. If it stays, it is "another service", and its 6 GiB
  `MemoryMax` belongs in `MDT_EXCLUDED_RAM` (the operator decides per host).

### 7.3 Protection

The prompts describe this as "unavailable to MDT".

- **`memory.min` mirror.**
  - `check.sh` and the wizard summary verify that the effective `memory.min` summed over `MDT_PROTECTED_UNITS` is at
    least `MDT_EXCLUDED_RAM`. Otherwise they WARN: "RAM unavailable to MDT is not protected by memory.min on the named
    units".
  - They also WARN when the children's `memory.min` sum exceeds the parent's. On the shared host the scopes sum to
    4000M + 3000M = 7000M against 6500M on `wings.slice`, so the kernel scales the scopes' protection down to the
    parent's 6500M.
  - By default MDT **does not write** production units, because the operator owns them. `install.sh
    --apply-protection` (opt-in, interactive confirmation) writes a drop-in `50-mdt-protection.conf` with `MemoryMin=`
    (and the weights below) for the named units.
- **Weights.**
  - `dev.slice` and its tiers keep their `CPUWeight` and `IOWeight`.
  - `check.sh` WARNs when a protected unit's `CPUWeight` or `IOWeight` is below 4× that of `dev.slice` (default 100),
    and the wizard prints the drop-in it would write.
- **`memory.low` on host services** is not set by MDT; it is listed as an operator option in the README.

### 7.4 Live probe (optional, suggests the three numbers)

- **Command.** `mdt-host-setup-wizard.py --probe-excluded [--units "wings.slice ..."] [--seconds 600]`. It is
  read-only.
- **For each named unit and its child cgroups it reads:**
  - `memory.min`, `memory.low`, `memory.high`, `memory.max`, `memory.current`, `memory.swap.current`;
  - `memory.stat` (`anon`, `file`);
  - `cpu.stat` `usage_usec` (delta over `--seconds`);
  - `cpu.weight`, `io.weight`.
- **Suggestions.**
  - RAM is the effective `memory.min` sum, rounded up to 256M. If no `memory.min` is set, it suggests the p95 of
    `memory.current` over the window and says that it is unprotected.
  - Swap is the maximum of `memory.swap.current` over the window, rounded up to 512M.
  - CPU cores are the p95 of the usage rate over the window, rounded up to 0.5.
- **Output.** A table, the suggested values and the protection warnings of §7.3. Nothing is written unless the wizard
  accepts the values.
- **Scope.** The probe reads host configuration and service state once, to suggest configuration. It is not a
  per-candidate or per-worker resource profile, so D1 does not apply to it. It must also run before cgprofile is
  installed: host-setup installs cgprofile's observer directory.

### 7.5 PSI as the control signal: `mdt-psi-guard`

- **The service.** A new stdlib Python service. Its unit runs as root, in `system.slice`, outside `dev.slice`.
- **Triggers.** Kernel PSI triggers (`poll()` on the pressure files) on:
  - the protected units' `memory.pressure` `some`;
  - `/proc/pressure/memory` `full`;
  - the protected units' `cpu.pressure` `some`.

  Thresholds: `MDT_PSI_PROTECTED_MEMORY_SOME_PCT=10`, `MDT_PSI_HOST_MEMORY_FULL_PCT=5`,
  `MDT_PSI_PROTECTED_CPU_SOME_PCT=25`. A trigger fires above its threshold for 10 s, and releases after 60 s below
  half the threshold.
- **Actions in `enforce` mode, in stages, each reverted on release:**
  1. Set `CPUWeight` and `IOWeight` of `dev-gates`, `dev-background` and `dev-buildkitd` to 1, saving the old values.
  2. If the trigger is still above its threshold after 30 s, lower `memory.high` of `dev-gates` and `dev-background`
     to max(their floor, 0.9 × `memory.current`). That reclaims dev memory into zswap, not production memory.
  - It never freezes and never kills. Freezing would make wall-clock budgets expire and so change results.
- **Modes.** `MDT_PSI_GUARD = off | report | enforce`.
  - The first release ships **`report`**: it logs what it would do.
  - `enforce` becomes the default after acceptance M6 on r1002.
- **Events.** One JSON line per action, `{ts, action, unit, from, to, trigger: {file, avg10}}`, written to
  `/run/mdt/psi-guard/events.jsonl` (tmpfs, bounded). Run-gate reads it for contamination (§6.1). The devcontainer
  template mounts `/run/mdt/psi-guard` read-only, in the same way as `/run/cgprofile`.
- **Coordination with reconcile.** `mdt-dev-governance-reconcile` re-asserts configured values on its timer, which
  would undo the guard. While the guard's state file lists an active action, reconcile skips those keys. On guard stop
  and at boot, the guard restores the saved values and reconcile reasserts.

### 7.6 Wizard prompt texts

None of these use "reserve".

- `RAM unavailable to MDT — used by other services on this host (0 if none) [suggested: 6500M = memory.min of wings.slice]:`
- `Disk swap unavailable to MDT — swap space other services on this host occupy (0 if none) [suggested: 14G = their current swap use]:`
  The help text below the prompt reads: "Swap space on the swap device that these services use."
- `CPU cores unavailable to MDT — used by other services on this host, fractions allowed (0 on a host without other load) [suggested: 1.5]:`
- `Host/production units to verify (memory.min, weights) and probe (space-separated, empty if none) [wings.slice]:`
- The summary line after the inputs: `MDT may use: RAM 9.2G (overcommitted across tiers), swap 54.5G, CPU 6.5 cores.
  Everything else is unavailable to MDT.`

## 8. Sequencing and compatibility windows

| Step | Tool / release | Content | Depends on | Compatibility |
|---|---|---|---|---|
| 1 | cgprofile **1.2.0** | Observer contract 3 windows (CP-39); ancestor local memory events in the window context and the Summary (CP-40). Pairs with CP-29 for io PSI and `io.stat` in `ctl host`, which is not required. | — | Contract 2 accepted throughout 1.x. `contracts:[2,3]`. |
| 2 | run-gate **24.4.0** | Dual v17/v18 verdict reader, dual progress reader, two-ended `jobs=auto` and admission budget (RG-148); contamination (RG-144); profile sidecar and history summary (RG-145); passes the memory budget. The pytest profile plugin and `workers` (RG-146) and `--scaling` (RG-147) may ship here or in 24.5. | (1) for live numbers; builds and tests against fixtures without it | History stays v2 (optional key). Old run-gate reads the new store (tested). |
| 3 | tester-unified rebuild | Run-gate 24.4.0 wheel; `pytest-xdist` present (check) | 2 | The tester-unified image must carry the released run-gate wheel (the KI-111 lesson). |
| 4 | assay **12.0.0** | Profiles from cgprofile (B206), verdict v18 cut including B203 and B205 (B208), two-ended-matching scheduler (B207, may follow in 12.1) | 1, 2, 3 | Old progress keys removed (no alias); refuses v17 verdicts on write and reads them in analyze. |
| 5 | mdt-cockpit host-setup release | MDT-033 (keys, sizing, probe, protection checks, PSI guard in `report`) | — (independent); r1002 completion for M6 | Old keys accepted with deprecation notices for two releases. |
| 6 | Measurements M1–M6 on r1002 | §9 | 1–5 deployed on r1002 | — |
| 7 | Defaults flip | PSI guard to `enforce`; tier fractions adjusted from M6; contamination thresholds from M5 | 6 | — |
| 8 | run-gate 25.0 | Remove the v17 verdict reader and the old progress-key reader | ≥ 1 release cycle after 4 | — |

**iobench (IOB-R2-01)** does not wait for any of this to triage survivors. Its rerun benefits from steps 4 and 6.

## 9. Test and measurement plan (r1002 first, then the shared host)

- **Preconditions on r1002.**
  - The supported host setup is completed (the 2026-11-02 run stopped at the Docker restart).
  - cgprofile 1.2.0 runs with the observer directory provisioned.
  - Only one gate runs at a time.
  - Every run records the kernel version, cgprofile version, run-gate version and assay version.
- **The phases:**

| Phase | What | Output |
|---|---|---|
| M0 (now, no new code) | Offline scheduler simulation (`resource-profiles-sim/sched-sim.py`) on every available progress stream (IOBench done; cli-extended next) | Table S0, as in §5.4 |
| M1 | Calibration: 50 IOBench candidates at jobs=1, each measured in both modes (two windows on the same root) | T1: per metric, process-tree vs cgroup — CPU difference (target ≤ 1%), RSS ratio sampled/anon-peak (expect 0.85–1.0), IO lower bound vs `io.stat`. Kernel peak-reset support. |
| M2 | R2 scaling: a 150-candidate stratified IOBench subset (by file, plus the 10 longest; the 5 timeouts excluded), N = 1 (baseline, cgroup mode), 2, 3, 4 | T2 |
| M3 | xdist scaling on plain pytest lanes: cli-extended unit, iobench r0-r1 unit part, libraries/worktree tests, and one docker-using suite (ciu) as the isolation negative. N=1 three times, N = 2/3/4 twice each, with `--xdist-parity` at each N. | T3, T3b |
| M4 | Scheduler A/B on the M2 subset at N=3 and N=4 with B = 0.75 × the lane cap: file order, LPT, TEM and TEM + LPT, predictions from M2 (source 1) and from file medians (source 3). This checks the §5.5 simulation against real runs, where peaks are momentary rather than held. | T4: makespan, idle worker-seconds at the tail, maximum and p95/p99 over time of the running sum of RSS, buckets identical (yes/no) |
| M5 | Contamination: repeat one M3 configuration with a controlled memory hog in `dev-background` (sized to push host memory PSI `full` above 1%), and once without | T5: flagged runs, reasons, false positives over all M2/M3 runs (target 0), exclusion visible in the `auto` basis |
| M6 | MDT: wizard plus the probe on r1002 with all excluded values 0, and with a simulated profile (2G / 1G / 1 core); PSI guard in `report` during M5, then `enforce` for one M5 repeat | T6: rendered units, checks, guard events, and whether a gate lane's verdict and buckets stayed identical under enforce |

**Table formats.**

- **T2/T3 scaling:** lane | N | reps | wall p50 | speedup | efficiency | lane `memory_peak` | per-worker RSS p50/p95/max |
  per-worker CPU s p50 | per-worker IO write p95 | stall s | contaminated | buckets or tests identical to N=1.
- **T3b auto check:** lane | auto pick | limiting term (cpu / mem / io / tests) | measured peak vs cap (overshoot yes/no).
- **Pass rules:**
  - every N>1 run has the same R2 buckets or test outcomes as N=1 (B204-style suite flakes are reported separately,
    with a serial replay);
  - no run exceeds its enforced cap;
  - the `auto` pick never exceeds the measured safe N of T2/T3.

## 10. Acceptance criteria (summary; the full lists are in each backlog entry)

- **cgprofile:**
  - window verbs bound to the peer, refusing foreign, stale and reused identities;
  - exact cgroup-mode numbers on a fake cgroupfs, and process-tree numbers with exited-child accounting;
  - `SHARED_CGROUP` exactly when foreign PIDs appeared;
  - 64 and 4096 window bounds and 6 h expiry;
  - contract 2 golden fixtures unchanged;
  - new golden fixtures vendored byte-identical.
- **assay:**
  - with the observer absent, every candidate profile is `NO_MEASUREMENT/OBSERVER_UNAVAILABLE` and no numeric field
    anywhere is non-null;
  - no old numeric progress, state or analysis key is written;
  - verdict v18 validates, and v17 is refused on write and read by analyze;
  - the scheduler gives identical buckets for canonical and scheduled orders on the fixture lane;
  - the drain follows dispatch order;
  - `schedule.json` is written.
- **run-gate:**
  - the contamination rule is exercised by a fixture for each reason, and `self_limited` is distinguished from it;
  - flagged samples are absent from every sizing basis;
  - `workers=auto` picks 1 without clean profiles;
  - the caps-required refusal works;
  - `{workers}` token enforcement;
  - the serial re-run of N>1 failures gives `INCONCLUSIVE/XDIST_ISOLATION`;
  - the sidecar is bounded;
  - history v2 written by 24.4 is readable by the released 24.3.0;
  - v17 and v18 verdicts are both read.
- **mdt-cockpit:**
  - the new keys validate, including fractional cores;
  - every old `*_RESERVE_*` key is refused with the rename message (installer, wizard, check.sh), and no key, prompt or help text contains "reserve";
  - the derived sizing matches the worked examples;
  - the `SWAP_MDT ≤ 0` warning;
  - the protection warnings, including the children-exceed-parent case;
  - the probe runs on a fake cgroup tree;
  - the PSI guard stages and their revert, coordinated with reconcile.
- **iobench:** see IOB-R2-01.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Process-tree RSS is a sampled lower bound, so jobs can be sized too high | The `ram_target` 0.75 margin; M1 measures the gap against the cgroup peak; enforced caps make an underestimate an OOM or `self_limited` flag, never silent corruption |
| Without CP-23, jobs>1 never has exact per-candidate PSI or IOPS | This is accepted: these come from jobs=1 baselines (M2), and the profile says `REQUIRES_CGROUP_MODE` |
| D1 removes numbers that run-gate sizing uses today when cgprofile is absent (devcontainer without the observer) | `auto` falls back to 1 worker and says why. This is the deliberate consequence of D1, documented in the CHANGES of both tools. |
| The dispatch-order drain changes progress event order for consumers that assumed index order | `candidate_index` keeps its meaning; `dispatch_position` is new; analyzer and run-gate readers key by id (tested) |
| The PSI guard misfires on the shared production host | Ship `report` first; `enforce` only after M6; it never freezes or kills; it reverts on stop and at boot |
| Wizard defaults overcommit too aggressively on small hosts | Explicit values win; M6 validates the fractions; `MemoryHigh` per tier still throttles before `MemoryMax` |
| Contamination thresholds too strict (excluding everything) or too lax | Calibrated in M5; the fallback is the declared or default basis, never contaminated data |
| Contract 2/3 coexistence bugs in the observer | The fixtures for both contracts run in both the cgprofile and the consumer test suites |
| Kernel lacks the per-file `memory.peak` reset | `PEAK_RESET_UNSUPPORTED` plus the daemon's `sampled-max`; M1 records support |
