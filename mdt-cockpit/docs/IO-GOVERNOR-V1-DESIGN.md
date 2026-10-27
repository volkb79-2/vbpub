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
  then 4, 4 KiB overwrites each followed by `fdatasync` with a latency histogram. The file exists for one run only.
- Passive collectors: PSI (system io AND memory, per cgroup io), the swap/reclaim counters of `/proc/vmstat` (`pswpin`, `pswpout`,
  `pgmajfault`, `zswpin`, `zswpout`, `workingset_refault_anon`, as rates over the snapshot pair), `/sys/block/<dev>/stat` and `inflight`,
  cgroup `io.stat` for the governed slices, the protected cgroups and named containers, and per-interval writes by every other top-level
  cgroup ("who else writes"). A tick whose gate fails (or that is backing off) still journals this snapshot pair (`passive` with
  `gated: true`): the probes are what a busy host must be spared, not the evidence of the busy moment.
- Covariates stored per run (never a lookup table): filesystem fill, mount options, last fstrim time and trimmed bytes if a record
  exists, `discard_max_bytes`, lvm `issue_discards`, an optional provider signal file (used bytes, snapshot time), queue parameters
  (`nr_requests`, `max_sectors_kb`, scheduler), io.cost model/QoS, governed `io.max`/`io.weight`, io.prio availability, and free-form
  annotations (`iogov annotate`). The queue/iocost/limit values are hashed into a `settings_epoch` on every sample.
- Protected-side distress episodes (`iogov watch`): a 1 Hz sampler (no probe IO) records a time-series window around every episode
  (protected `io.pressure` AND `memory.pressure` stall shares, threads in D state; the same thresholds trigger on either resource), labels
  which stall share dominated (`driver`: memory / io / dstate, a hint, not a diagnosis: a swap-in wait raises both), and `analyze` reports
  how long distress persisted and recovered, also before and after operator-annotated actions: the doc's missing "actuator stopping
  distance". Live monitoring showed the stalls on the first host are memory/swap-driven with a healthy disk, so an episode recorder that
  only saw `io.pressure` would have mislabelled the operator's actual problem.
- A recognizer (robust baseline, two-sided CUSUM, hysteresis) over per-lane window medians; an offline `analyze` (summary, shifts,
  text plots, harm, demand check) and a `status` that includes the report-only **prevention checker** (fill > 60 %, days since fstrim,
  discard mount option / lvm `issue_discards` / `discard_max_bytes`, snapshot age and provider usage when a signal file exists).
- Crash-safe operation: everything that must survive a crash (budget reservation, next bench time, failure counters) is persisted before the IO it
  protects; a crashing bench backs off exponentially and disables itself after 5 consecutive crashes until `iogov annotate --baseline-reset`.
- Bounded memory and retention: the journal is write-mostly; the recognizer keeps compact per-lane aggregates; `iogov prune` (and a daily automatic prune)
  deletes monthly files older than 400 days or beyond 512 MiB (oldest first, never the current month).
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
| `O_DSYNC` second descriptor opened by name | replaced by `pwrite` + explicit `fdatasync` on the one descriptor | Review H3: re-opening the scratch file by name follows a swapped symlink. The 4 KiB lane now times `pwrite`+`fdatasync`; the file is never re-opened. |
| Validating (erroring on) over-limit config values | replaced by CLAMPING to `HARD_MAX`/`HARD_MIN` | Review M2: a config can never exceed the budgets in this note; clamps are reported. `read_block_bytes` removed (it never changed the real read size). |
| Whole-journal replay by the runner every hour; 120-day sliding lookback | replaced by persisted per-lane aggregates (`agg.json`) updated incrementally | Review H2/H4: unbounded memory against `MemoryMax=256M`, and a baseline re-derived from a sliding window silently adopted a degraded state after 120 days. |
| `confirm_windows=2`, `h=1.5`, prior 0.35 | `confirm_windows=3`, `h=2.0`, prior 0.5 | Review L6: measured false-alarm budget (section 7). The price is one more day of detection delay. |
| Budget saved only at the end of the run | budget RESERVED and persisted before any IO, refunded in `finally` | Review H1: a crash before the end-of-run save lost the charge and the next-due time. |
| Buffered-read fallback with `posix_fadvise(DONTNEED)` for a raw disk that refuses `O_DIRECT` | removed | Review L4: a raw disk is never read through the page cache; the lane is unsupported and skipped. |
| CUSUM "alarms in 2 days on 2x" | corrected | With k=0.5 a sustained 2x needs 3 windows to reach h=1.5, and confirmation adds one (tested). |
| Gated tick journals only a `skip` | now also journals the gate's passive snapshot pair | Second review (R5): the moments probes must not run are the moments whose passive evidence is wanted; no probe IO is added. |
| Episodes from `io.pressure` only | `io.pressure` + `memory.pressure`, `driver` label | Second review (R6): live data showed memory/swap reclaim is the stall driver with a healthy disk. |
| `analyze`/`status` verified file OWNER = running uid | read-only opens accept any owner (still no-follow); unreadable files are named | Second review (R1): run by root/an operator they silently reported "0 records" or crashed. Writers keep the owner check. |
| Bare `fdatasync` in the 4 KiB lane | `timed_flush` everywhere | Second review (R3): the note promised a watchdog on every flush; `abort_latency_ms` alone bounded it. |
| Readers opened by name, type checked after | lstat before every open, `O_NONBLOCK` on the open, type verified through the fd before the first read; every read bounded | Third review (S1/S2): a FIFO or device node planted as `journal-*.jsonl`/`state.json` hung a root `analyze`/`status`; a deeply nested or multi-GB file crashed or exhausted it. |
| Reports indexed state/journal fields untyped | schema types for every top-level field, `clean_state` for `status`, nested values read defensively | Third review (S2): `TypeError` tracebacks on a damaged file instead of "corrupt state: <file>". |
| Kill switch between the runner's check and the gate's | the gate's early return carries no snapshot; the runner journals the skip; `_guarded` turns any unexpected exception into a one-line error (exit 3) after `exclusive` persisted state and released the lock | Third review (S3). |
| Backed-off tick ran the gate every 5 min | at most one passive-on-skip record (and gate read) per `skip_passive_interval_s` (15 min; 1-60 min), reason `backoff:until=<t>` | Third review (S4): the evidence of a busy host at bounded cost. |
| "Any user may run analyze/status" | root or the service user | Third review (S5): the state directory is 0700; the docs now say what the install makes true. |
| Removed config keys were errors | deprecation warning for one release (`DEPRECATED_KEYS`), ignored and listed by `plan`/`config` | Third review (S7): an upgraded host must not stop ticking over an old key. |
| `RuntimeMaxSec=` as the hung-syscall backstop | `TimeoutStartSec=` (tick 10 min, bench 30 min); rendered units pass `systemd-analyze verify` in a test | Fourth review (T1): `RuntimeMaxSec=` is ignored on `Type=oneshot` units; the claimed backstop did nothing. |
| Refused state load still saved `{}` | `exclusive` persists only after a successful load; a refused state file is left untouched | Fourth review (T2): a wrong-mode/owner, symlinked or FIFO `state.json` was replaced by an empty one, resetting budget and backoffs exactly when the state was suspect. |
| `resets.log` decoded strictly | `errors="replace"`, bad lines skipped | Fourth review (T3): one stray byte made every run exit 3. |
| Config path must be a regular file | a ROOT-owned symlink is accepted when every hop and every directory above every hop and the target are root-owned and not group/other-writable, and the target passes every file check | Fourth review: managed-config layouts symlink `/etc/<tool>/<file>`; fifth review: a root-owned link into a service-writable directory is not a trusted config. |
| `_finite` guarded three persisted fields | one schema for `state.json` (`state.py`), validated once after load; invalid fields dropped to defaults and journalled (`note`, `kind: state_repair`); budget, backoff and schedule code re-check their own slots | Fifth review: a NaN in `budget.read_bytes` bypassed the daily cap (`min(n, cap - NaN)` = n), a NaN in `last_full.t`/`last_escalation` froze the hourly record / the escalation, a string in `last_prune` made every run exit 3 and re-save the bad state forever. |
| Journal limits counted characters | bytes, from the binary layer | Fourth review. NaN/inf in persisted numbers (`skip_passive_t`, `next_bench`, `backoff.until`) are treated as absent; an internal error also leaves a journal `note` (`kind: error`). |

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

### Identity, files and locks (never root by default)

- **Identity.** `tick`, `bench`, `watch`, `annotate` refuse to run as root unless `allow_root` / `--allow-root` is set; then every directory must be root-owned
  0700 (the scratch directory always must be 0700). The shipped units run as an unprivileged user.
- **Directories** (`state_dir`, its `journal/`, `scratch_dir`) must be real directories (not symlinks) owned by the running uid and not group/world writable;
  otherwise the run refuses (exit 2, nothing written).
- **Files** are opened `O_NOFOLLOW` (`O_EXCL` when created) and verified through the descriptor: regular file, our uid, mode 0600 for state/lock/scratch
  files (journal: no group/world write), and the path still names that inode. The scratch file is used through the one descriptor and removed by name only if
  the name still refers to our inode. State and aggregate files are written to a random-named `O_EXCL|O_NOFOLLOW` temp file, fsynced and atomically replaced.
- **Locks.** `run.lock` (non-blocking, held for a whole `tick` or `bench`) makes the two exclude each other, and state is loaded only after it is held, so
  a second run can neither overlap nor clobber state; `watch.lock` allows one `watch`; `state.lock` serialises each atomic state write. `annotate` only appends
  to the journal (and, for a baseline reset, to `resets.log`), so it needs none.
- **Config file** must be root- or self-owned and not writable by group/others; `/etc/iogov` and the kill-switch file should be root-owned (README).
- **Read-only reports.** `analyze` and `status` are run by root or the service user (the state directory is 0700). They open the journal,
  `state.json` and `agg.json` no-follow and accept any owner (root reading the service user's files); a file that cannot be read is listed as
  `UNREADABLE`, a damaged state file as `corrupt state: <file>` (never silently skipped, never a traceback). Every writer keeps the owner check.
- **Every open is type-checked before it can block or read.** `lstat` before the open refuses anything but a regular file (naming the path
  and its type: fifo, socket, device, directory, symlink), the open carries `O_NONBLOCK` so a FIFO swapped in after the lstat cannot block it,
  and the type is verified again through the descriptor before the first read; the collectors' procfs/sysfs/cgroup/operator-file reads go
  through the same `read_text`. The config file is checked the same way.
- **Read bounds** (constants, documented here and asserted by tests): `state.json`/`agg.json`/`resets.log` at most `MAX_JSON_BYTES` 16 MiB
  (parse errors and `RecursionError` are "corrupt state" for the reports and a fresh start for the runner); collector text files at most
  `MAX_TEXT_BYTES` 4 MiB; config at most 1 MiB; a journal line beyond `MAX_LINE_READ` 1 MiB is `oversized` and discarded in bounded chunks,
  a monthly file is read up to `MAX_FILE_BYTES` 256 MiB (`TRUNCATED`), a read stops at `MAX_RECORDS` 2 000 000 records (`LIMIT`, said in the
  report). Every top-level journal field has a schema type; a record with a wrong type is `invalid`; nested values are read defensively.
- **The persisted state has one schema** (`state.py`: every epoch field a finite number in [0, 1e11], every counter a non-negative int,
  `budget`/`backoff`/`bench_fail`/`last_full`/`skip_logged`/`devices` shaped exactly). The runner validates the whole file once after loading
  it, drops every invalid or unknown field to its default and journals what it dropped (`note`, `kind: state_repair`; the cleaned state is
  saved at the end of the run); `status` validates the same way and prints the dropped names; the budget, the backoff and the schedules
  re-check the slot they own, so a NaN/inf/string/bool/negative/absurd value can never widen a cap, freeze a schedule or crash a run.

## 4. Schedule

| Run | Period | What |
|---|---|---|
| `tick` | 5 min (timer) | per disk: 12 non-zero cold 4 KiB reads (at most 36 attempts), 2 witness reads, passive snapshot (about 1 s) |
| `bench` | timer every 15 min; due every 6 h +-10 % jitter | region 512 MiB default; probe 64 MiB: fresh sequential 64 KiB writes 16 MiB at qd1, 48 MiB at qd4 (fdatasync every 8 MiB), 256 x (4 KiB overwrite + fdatasync), 8 MiB sequential raw read; weekly escalation to 192 MiB |
| `watch` | resident, 1 Hz | procfs/cgroup reads only |

Adaptive region: `min(512 MiB, 1 % of the disk, 25 % of (free - reserve), region_hard_cap)` with `region_hard_cap` 1 GiB by default and
at most 2 GiB (`HARD_MAX`), reserve = max(2 GiB, 5 % of the filesystem), filesystem
must be ext4 or xfs; below 16 MiB the write lanes are off ("region none") and the journal and `analyze` say what is lost (no durable-
write latency, no fresh-write lane, no bandwidth probe outside passive windows). 32 GB disk: ~327 MiB region, 40 MiB probe. 128 MiB
region: 16 MiB probe. Sizes are decided at each run from current free space; the file is created O_EXCL, filled with random non-zero
data by the bench itself (no unwritten-extent fast path), removed in `finally` (also on abort/SIGTERM), stale files removed under an flock.

## 5. Cost budget (from `compute_budget`, asserted by tests; defaults)

| | typical | worst case | default cap (hard maximum a config can reach, `config.py HARD_MAX`) |
|---|---|---|---|
| raw reads / day | 4 032 x 4 KiB + 4 x 8 MiB = 48 MiB | 10 944 x 4 KiB + 32 MiB = 75 MiB | 128 MiB/day (max 256 MiB), 20 IOPS (max 200), queue depth 1 |
| writes / day | 4 x (64 + 1) MiB + 128 MiB/7 d = ~280 MiB | same | 1 GiB/day (max 2 GiB), 256 MiB/s (max 512 MiB/s) |
| flush-bearing ops / day | ~1 100 | same | 4 KiB lane 100/s (max 500/s) |
| in flight at any instant | 1 request, except 4 x 64 KiB during the qd4 phase (a few seconds per bench) | 256 KiB | `qd_high` 4 (max 8), unsynced <= 8 MiB (max) |

Worst-case extra wait a neighbour sees behind our in-flight bytes (256 KiB): 0.74 ms at 340 MiB/s, 16.7 ms at 15 MiB/s, 41.7 ms at
6 MiB/s; a flush barrier covers at most 8 MiB: 23 ms / 533 ms / 1.3 s at those rates. **Reservation semantics.** A run reserves its worst-case bytes before any IO (counted against the day and persisted), per-IO charges draw from the
reservation, and `finish()` refunds the unused part. A run killed before it can refund keeps the whole reservation charged (conservative). Tested: six
consecutive failing benches stay under the daily write cap, and only the first one performs IO (the failure backs off the next attempt exponentially:
6 h x 2^n, at most 7 days, persisted).

**Flush bound.** Every `fdatasync` (the sequential phases' periodic flushes and the 4 KiB lane's per-sample flush; `timed_flush` is the only call site, asserted by
a static test) runs under a watchdog: a flush longer than `flush_limit_s` (2 s, hard max 10 s) raises the stop flag from a monitor thread and the lane
ends as `slow_flush`; the flush size also adapts so that one flush costs about 250 ms at the write rate just measured (never above `sync_every_bytes` <= 8 MiB).
Every offset-probe read in the sequential-read lane is guarded, paced and charged like the others.

These are bounds on our own submissions
under the assumption that the device drains in order; they are NOT a guarantee (provider pauses and flush stalls are unbounded),
so each IO is also bounded in time: any single IO slower than 250 ms stops further submissions (an already submitted IO cannot be recalled).

**What the watchdog cannot do, and the backstop.** The flush watchdog only sets the stop flag: a `fdatasync` (or a raw read) blocked in the
kernel is not interrupted, and until it returns the process cannot refund its budget or remove the scratch file. The hard backstop is the
unit: `TimeoutStartSec` (tick 10 min, bench 30 min; for a `Type=oneshot` service this IS the runtime bound, `RuntimeMaxSec=` is ignored
there, which `systemd-analyze verify` reports and a test checks) makes systemd send SIGTERM, `TimeoutStopSec` (30 s / 60 s) later SIGKILL. A killed run is
handled by design, not by luck (tested): the reservation stays charged (it was persisted before the IO; nothing refunds it), `next_bench`
and the failure counter were persisted before the IO (so a hung bench backs off, and disables itself after `bench_max_failures`), and the
leftover scratch file is removed by `ExecStopPost=cleanup` or by the next run under the scratch lock.

## 6. Failure modes, kill switches, safety

- **Kill switches** (checked before a run, and every 32 IOs): `/etc/iogov/disabled`, `<state_dir>/DISABLED`, `IOGOV_DISABLE=1`,
  `enabled=false`, unit `ConditionPathExists=!` on the flag file, `systemctl stop|mask`.
- **Load-aware gating, never run when IO pressure is high:** tick skips if system `io.pressure some avg10` > 10 % or load > 1.5/CPU. The bench
  needs `some` <= 1 %, `full` <= 0.1 %, protected cgroup pressure <= 1 %, governed writes <= 2 MB/s, nothing in flight on the disks.
  A gated run costs one 1 s pair of procfs reads, applies exponential backoff (5 min doubling to 1 h) and journals a deduplicated `skip`.
  A gated or backing-off **tick** still journals that snapshot pair as a `passive` record (`gated: true`, `gate_reasons`, incl.
  `backoff:until=<t>` while backing off): the busy moment's pressure (io and memory), swap counters and "who else writes" are kept; no probe IO
  is issued. Cost bound: at most one such record, and one gate read (1 s pair of procfs reads), per `skip_passive_interval_s` (default 15 min,
  clamped to 1-60 min); a backed-off tick that is not due returns at once. The bench journals only the skip.
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

Pure function of the journal (`recognize(records, cfg)`), folded in incrementally by the runner (persisted in `agg.json`) and replayed by `analyze` through the same `Engine`.

- Lanes: `r_cold@dev`, `r_witness@dev` (4 KiB read latency), `w_over_4k` (durable 4 KiB overwrite latency), `w_fresh_qd1`, `w_fresh_qd4`
  (durable write rate), `r_seq`. Only **quiet** samples enter (no inflight requests, governed writes < 1 MB/s at the tick, gate-passed bench).
- Window = 24 h. Value = median of pooled latency samples, or median rate. **Capability** = 1e6 / median latency (ops/s) or the rate;
  `x = log2(capability / reference)`.
- **Closed windows only.** A window is evaluated when a record of a later window arrives or when `close_up_to(now)` passes its end; a partial day never
  produces a verdict. Events are keyed by `lane|kind|window`.
- **Persisted state.** Each lane's reference, sigma, phase, CUSUM sums, the last 60 rows, and the open window (<= 4096 values, decimated) live in `agg.json`;
  the runner updates it incrementally and `analyze` streams the journal through the very same `Engine`, so they agree (tested). A baseline changes only through
  an operator reset (`resets.log` + journal note; never expires). Pruning the journal does not change the persisted baseline; `analyze` replays only what is retained.
- Reference = median of the first 5 valid windows (LEARNING until then), noise sigma = MAD-based with a 0.25 floor, **frozen while alarmed**, replaced
  only by `iogov annotate --baseline-reset` (median of the latest 3 windows).
- Two-sided CUSUM, `k_eff = max(0.5, sigma)`, `h_eff = max(2.0, 3 sigma)`, sigma = MAD estimate shrunk toward a prior of 0.5 (weight 5 windows), S capped at 2 h so a long collapse still unwinds. A window counts toward
  confirmation only if S >= h **and the window itself is beyond the slack** (a one-day outlier cannot be confirmed by normal days after it);
  3 consecutive such windows (windows missing in between break the streak) -> `shift_detected` (with factor, direction, "baseline reset suggested"). An alarm clears only after 2
  consecutive windows back within the slack, or flips explicitly. Delays (tested): 20x: 3 windows, 4x: 4 windows, 2x: 6 windows after the step.
- **False-alarm budget (measured, 300 seeds x 365 days per case, tested):** at most 1 false `shift_detected` per lane-year for Gaussian day-to-day noise up to
  sigma = 0.5 log2 units (about +-41 %) and for 15 % random 3x slow days on sigma 0.2 (measured 0.8 and 0.5 per year; sigma 0.3: ~0; sigma 0.7 is OUTSIDE the
  supported range, ~2.4 per year). A lane noisier than sigma 0.5 should not be trusted for shifts smaller than ~4x.
- **Qualified factors.** A probe sitting at its own rate ceiling (`at_cap`, write_bps_max 256 MiB/s) only shows a lower bound: such shifts say "by at least ~Nx"
  and `evidence.factor_is_lower_bound` is true. Samples that did not bypass the page cache (`direct` false) never enter the series.
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
| `run_start` | `kind` (tick/bench), `version`, `mode`, `settings_epoch`, `full`; `settings`, `devices`, `notes`, `covariates` only when `full` (settings epoch changed, or hourly): ~2.2 KB per tick, ~0.65 MB/day |
| `run_end` | `kind`, `status` (ok/aborted/budget), `duration_s`, `io` (+ `abort`) |
| `skip` | `kind`, `reasons[]` (+ `ctx`): kill switch, pressure, busy device, backoff class, no devices |
| `probe` | `lane`, `device`, `quiet`, `n`, `zero`, `lat_us[]` (<= 32 samples), `hist[]` (log2 us buckets), `direct`; also `p50 p90 p99 min max attempts errors ctx epoch` |
| `bench` | `status`, `region` (mode, sizes, fs, direct, escalated, disabled reason), `lanes{}` (per lane: bytes, qd, `t_write`, `t_sync`, `rate_write_bps`, `rate_durable_bps`, `at_cap`, `lat`, `sync_ms`, `valid`, `aborted`), `settings_epoch` |
| `passive` | `devices{}` (iops, bps, await ms, busy %), `pressure`; also `pressure_memory`, `vmstat_per_s` (swap-in/out, major faults, zswap, anon refaults per second; null when unavailable), `loadavg`, `cgroups{}` rates/pressure, `others_writing{}` (top writers outside the slices), `root_total_w_bps`; on a gated tick `gated` true and `gate_reasons[]` |
| `recommendation` | `lane`, `kind` (baseline_established, shift_detected, shift_cleared, baseline_reset), `text`, `evidence` (`category`="capability", `key`, window, factor, direction, CUSUM state) |
| `harm` | `kind` (`episode`, `probe_latency`), `evidence` (episode: start/end, `duration_s`, `recovery_s`, peaks incl. `peak_mem_some_pct`/`peak_mem_full_pct`, `driver` (memory / io / dstate), `series[[t_rel, some%, full%, dstate, sys_some_avg10, mem_some%, mem_full%]]` <= 300 points, `settings_epoch`) |
| `note` | `text`; `kind` annotation / action / baseline_reset / retention / state_repair / error. The tool's own notes (retention, state repairs, internal errors) name files and directories of the state directory and may quote an exception message: the journal is 0640 inside a 0700 directory, readable by root and the service user only, and nothing in it is a secret, but it is not host-agnostic. |

Other files in `state_dir`: `state.json` (schedule, budget, backoff, failure counters, walk positions), `agg.json` (recognizer aggregates), `resets.log`
(baseline-reset times), `run.lock`, `watch.lock`, `state.lock`, `scratch/` (one file during a bench, `iogov-scratch.lock`).

## 9. Assumptions to verify on the host (could not be verified here)

1. Whether raw reads of the whole disk are permitted in the intended runtime context (container vs host, udev ACL, `DeviceAllow`); `O_DIRECT` on the node works for 4 KiB aligned reads (tested only on a regular file / ext4 here).
2. Logical block size: reads use max(4096, `BLKSSZGET`) alignment; virtio-blk exposing 512 B sectors was assumed.
3. cgroup v2 paths: `/sys/fs/cgroup/<slice>/io.stat|io.pressure|cgroup.threads`, and that the root `io.stat` exists on this kernel (the "other writers" view degrades gracefully without it). Buffered-write attribution to cgroups on LVM+ext4 is unverified (flusher IO may land in the root cgroup).
4. `containers = [...]` name resolution reads `/var/lib/docker/containers/*/config.v2.json` (root-only by default, and it contains container environment variables, i.e. secrets): prefer explicit `container_cgroups` paths. The parse is cached by mtime and discovery is cached for 60 s.
5. That a low probe-slice IO weight is the right priority (Astra vs Fable disagree); that 4 x 64 KiB in flight and an 8 MiB flush barrier disturb the protected workload within budget at the current device rate (measure with `watch` episodes before and after enabling; run the first bench with the game idle).
6. That `fdatasync` reaches the backend as a flush; sub-100 us durable writes would mean the hypervisor ignores it (recorded in `w_over_4k`, not flagged automatically).
7. `scratch_dir` is on the same filesystem and disk as the data; ext4/xfs behaviour of O_DIRECT overwrites; free-space reserve is adequate on small hosts.
8. The systemd unit directives (device policy, ReadWritePaths, MemoryDenyWriteExecute with the interpreter) work with the packaged Python.
9. Provider signals: `provider.json` is written by something the operator provides; without it snapshot age and provider usage are "unknown".

## 9a. Unit hardening, directive by directive

`ProtectKernelTunables=yes` (the tool only reads `/sys` and `/proc`), `ProtectControlGroups=yes` (only reads the cgroup tree; verified: no cgroup file is
ever written), `ProtectKernelModules=yes`, `CapabilityBoundingSet=` (empty: no capability is needed to `read(2)` an ACL-granted node or write the state dir),
`PrivateNetwork=yes` with `RestrictAddressFamilies=AF_UNIX` (no network at all), `SystemCallFilter=@system-service`, `StateDirectory=iogov` +
`StateDirectoryMode=0700` + `UMask=0077` (systemd creates the 0700 state dir for the service user, so the directory checks pass at first start),
`ProtectSystem=strict`/`ReadWritePaths=` (state and scratch only), `DevicePolicy=closed` + `DeviceAllow=<node> r` (read-only access to exactly one disk),
`MemoryDenyWriteExecute`, `NoNewPrivileges`, `LimitCORE=0`. No `RuntimeDirectory=`: locks live in the state directory, which must persist across runs.
These directives are untested on the real host (assumption 8).

## 10. Known blind spots

Large burst-credit limiters (credit > 64 MiB, escalation 192 MiB weekly); request sizes > 64 KiB; read bandwidth at depth; allocation-specific penalties on
snapshot-shared data (the fresh file measures allocation into free space, not a CoW overwrite of old data); time-of-day effects when the host is quiet mainly at night
(daily medians of quiet samples only); hosts that are never calm (bench skips: visible as STALE); a provider that ignores flush. The recognizer reports a
shift in what the probes measure; whether the game suffers is the `harm` section's job.

## 11. Operator decisions before enabling

1. **Run identity.** A dedicated unprivileged user (`iogov`); root only with `allow_root` and root-owned 0700 directories. Decide the user and who may read its journal.
2. **`/etc/iogov` ownership.** Root-owned 0755, the config file root-owned 0644 (refused if writable by others), the kill-switch file root-owned. Anyone who can create the
   kill-switch file can disable the tool; anyone who can edit the config can only move values inside the hard clamps. Keys removed by a later
   release (`config.DEPRECATED_KEYS`) are ignored with a warning for one release; an unknown new key is an error.
3. **Container access.** Explicit `container_cgroups` paths (recommended) versus giving the service read access to the daemon's `config.v2.json` files (secrets exposure).
4. **Probe slice weight** (`@IOGOV_IO_WEIGHT@`, default 50 against the game's 1000): low weight protects the game but makes probes `contended` more often (those samples are
   excluded from the recognizer); measure with `watch` episodes before and after the first benches.
5. **Retention.** Defaults 400 days / 512 MiB (about 240 MiB per year at one tick per 5 minutes). Shorter retention shortens what `analyze` can replay; the persisted baseline is unaffected.
6. **Raw reads.** Accept the confidentiality cost of read access to one whole disk (section 6), or do not grant the udev ACL (the tick then reports "cannot open read-only" and only
   the scratch-file bench and passive collectors run).
7. **First bench** with the protected workload idle: enable `iogov-tick.timer` first, run `iogov bench --force` by hand in an idle window and compare
   `watch` episodes around it, and only then enable `iogov-bench.timer` (its first run is otherwise whenever the gate first passes). Also decide the daily
   write budget (default 1 GiB, hard maximum 2 GiB).
8. **A host that is never calm.** The bench gate (system io `some` <= 1 %, `full` <= 0.1 %, nothing in flight) may rarely pass on a host that swaps
   continuously; the write lanes then show as STALE and only the read lanes and passive evidence accumulate. That is the designed failure mode, not a
   bug, but it must be visible: check the skip counts in `analyze` after the first days (backlog MDT-030 for a gate pass-rate line in `status`).
