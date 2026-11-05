# iogovmon backlog (IGM)

Source of the scope: `host-tuning-DESIGN.md` section 3.6 and the work packages WP-IGM-a (this extraction, done), WP-IGM-b, WP-IGM-ACC.
IGM-04..IGM-07 were MDT-028..MDT-031 in `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md` and moved here with the code (2026-11-05).

## IGM-01 - first release is gated on live acceptance (WP-IGM-ACC)

**Status:** OPEN, blocks the first `iogovmon-v*` tag. `cmru.toml` and the orchestration entry exist so the gate can run, but NO release may be cut.
Needed evidence (recorded in a report, then the default flips): the current `install.sh` on a real host and on a VM guest (later the wheel-only
`iogovmonctl install` verb); 48 h of ticks with the journal and
the byte/PSI budgets verified; the raw-read ACL rule reviewed (per disk by `ID_PATH`, read-only, never the `disk` group; if the review rejects the ACL, a
root-owned helper that hands a read-only fd replaces it); kill switches exercised (`/etc/iogovmon/disabled`, `<state_dir>/DISABLED`, `IOGOVMON_DISABLE`,
timers stopped). Until then host-tuning's and the installer's `services.iogovmon.install` stays `false`; it flips to `true` in the change that records this
acceptance. IOWeight in `iogovmon.slice` is advisory (effective only with io.cost or bfq): the real control is the PSI gating of the mini write bench
(starts only below the limits, aborts when they rise) plus the byte budgets.

## IGM-02 - Assay R2 (mutation) lane and the 100 % coverage floor

**Status:** OPEN. `run-gate.toml` declares `r0-r1` only: pytest with a branch-aware coverage floor of 88 % (measured 90.1 % at extraction; weakest modules:
`cli.py` 67 %, `report.py` 83 %, `scratch.py` 83 %, `safeio.py` 86 %). The estate target is 100 % line+branch with an `assay.toml` R2 lane
(`libraries/iobench/assay.toml` is the model). Needed: cover the CLI verbs that no test drives (`watch`, `devices`, `config`, `cleanup` dispatch), the
report branches, then declare the assay lane and raise the floor. Mutation survivors expected in the recognizer thresholds.

## IGM-03 - `iogovmonctl install` verb and units as package data (WP-IGM-b)

**Status:** OPEN. Today `install.sh` is a source-tree installer (it copies `src/iogovmon/*.py` and reads `units/*.in`); a wheel-only host cannot run it.
Replace it with `iogovmonctl install [--dry-run] [--destdir DIR]` (same idempotent steps, same validation, same tests) after moving `units/*.in` and
`iogovmon.toml.example` into the package as data so the wheel carries them; units run `iogovmond` from the root-owned package copy. Keep the staging
(`--destdir`) mode and the `systemd-analyze verify` step.

## IGM-04 - `watch`: per-cgroup swap, refault and memory.events counters in episodes and ticks (was MDT-028, 2026-10-27)

**Status:** OPEN. From the second (Fable) review of iogov v1 (7f38a7743). Live monitoring (`mdt-cockpit/docs/IO-MONITORING-SETUP.md`, iomon branch) found the
protected workload's stalls are driven by memory/swap reclaim (zswap writeback, swap-in, major-fault bursts of thousands per second) while the
disk is healthy. The review added `memory.pressure` to `watch` episodes and host-wide `/proc/vmstat` swap rates to the passive record, which
names the stall's resource but not its *per-cgroup* mechanism.

**Add (read-only files, no probe IO):** per protected cgroup, at every `watch` sample inside an episode and in every tick's passive snapshot:
`memory.stat` (`pgmajfault`, `workingset_refault_anon`, `zswpin`, `zswpout`, `pswpin`/`pswpout` where present), `memory.swap.current`,
`memory.current`, `memory.events` (`high`, `max`, `oom`, `oom_kill`) and `memory.events.local`; as rates/deltas, not cumulative values.
Episode evidence gets `peak_majfault_per_s`, `swap_in_bytes` (pages x page size) and the `memory.events` deltas; `analyze`'s harm section
prints them next to `driver`.

**Acceptance:** a fake-cgroup test in which `memory.pressure` is flat but `pgmajfault`/`zswpin` jump during an episode records the rates and
labels the episode `driver: memory`; a tick's passive record carries the per-cgroup deltas for the configured `protected_cgroups` and
`containers`; missing files yield null, never an exception; the journal record stays under `MAX_LINE_BYTES`; design note section 8 updated.

## IGM-05 - a protected-process-side canary ("what does the game wait on?") (was MDT-029, 2026-10-27)

**Status:** OPEN. iogovmon v1 measures the disk from the outside (raw reads, scratch bench) and the protected workload from PSI. Neither says
*what* the protected process waits on during a stall. The iomon eBPF collector (`stall.bt`: D-state kernel stacks over 100 ms by comm)
answers it but needs a root container with BPF capabilities and runs for hours at a time.

**Options, cheapest first:** (a) in `watch`, when an episode opens, sample `/proc/<tid>/wchan` (and `/proc/<tid>/syscall`, if readable) of the
protected cgroups' D-state threads once per second and histogram the wait symbols into the episode evidence (`swap_readpage`,
`folio_wait_bit`, `jbd2_log_wait_commit`, `blk_mq_get_tag`, ...; readability of `wchan` for another user's tasks depends on `hidepid` and
the ptrace scope, so this may need the `watch` unit to run with a read-only `CAP_SYS_PTRACE` or be documented as root-only);
(b) an operator-run attach of the existing iomon eBPF collector for N minutes when `status` shows episodes (documented procedure, no code);
(c) a tiny in-container canary (one 4 KiB `O_DSYNC` write + one `O_DIRECT` read every 10 s on a pre-allocated file inside the protected
volume, as iomon's sampler does) so that the *application's* latency path is measured, not the host's.

**Acceptance:** (a) a test with a fake `/proc/<tid>/wchan` tree shows the symbol histogram in an episode; the sampler never reads `/proc/<pid>/maps`
or `environ`; (c) is only acceptable if the file lives in a dedicated directory the operator names, is created `O_EXCL`, and the IO is
charged to iogovmon's daily budget. No automatic cap or setting change (unchanged v1 rule).

## IGM-06 - gate reachability on a never-calm host, and a bench gate on memory pressure (was MDT-030, 2026-10-27)

**Status:** OPEN. The write bench runs only when system io PSI `some` <= 1 %, `full` <= 0.1 %, protected pressure <= 1 %, governed writes <= 2 MB/s
and nothing is in flight. On a host that swaps continuously (the review's devcontainer host showed memory PSI `some` 37 % / `full` 17 % at the time)
the bench may rarely pass, so the write lanes stay STALE while the read lanes and passive records accumulate. That is the designed failure
mode (design note section 11.8), but it is invisible in `status` today, and a host in heavy reclaim is not "calm" even when its io PSI is low:
the bench's writes would compete with zswap writeback and the measurement would be contended without being labelled so.

**Do:** (1) `status` prints, per run kind, the gate pass/skip counts of the last 7 days by reason class (from the journal's `skip`, `passive`
(`gated`) and `run_start` records) and the age of the last valid window per write lane; (2) add `bench_memory_pressure_some_max` (default 5 %,
hard max 25 %) to the bench gate (memory PSI in the gate `ctx` is already recorded by the review); (3) document in the design note that a bench
gated for more than N days means "no write-capability evidence on this host", with the operator's options (a bench window at night, lower
thresholds justified by a measured `watch` comparison, or accept read-only evidence).

**Acceptance:** tests for the `status` lines and the new gate reason (`memory_pressure_high`); the design note's gate table and section 6 agree with
`config.py`; `plan` prints the new threshold.

## IGM-07 - iobench ceilings export (`ceilings.jsonl`, `source: governor`) (was MDT-031, 2026-10-27; superseded in shape by WP-IGM-b)

**Status:** OPEN. The host-tuning design (3.6) fixes the interface: `iogovmonctl export ceilings --since T --jsonl` prints lines shaped like iobench's
`ceilings.jsonl` observation, `{ts, dir, workload, plateau_bps, duration_s, source: "governor", note?}`; host-tuning ingests them with
`iobench ceilings add` (the adapter lives in host-tuning, so iogovmon needs neither iobench nor fio). It never writes outside its state dir and never runs
a subprocess (the static no-write test must stay green). The mapping of the 64 KiB sequential lanes to iobench's `seq1m|rand4k` workload names needs an
explicit decision (a new workload name, or a documented approximation carrying the `file`-kind label of IOB-TGT-09, since iogovmon measures a file, not
the device).

**Acceptance:** a test that a journal with a confirmed downward shift yields export lines whose `plateau_bps` equals the window's median
durable rate and whose `note` carries the recommendation key; `iobench ceilings add` accepts a printed line in a dry run (checked in host-tuning's
integration, WP-INT); the design note's "v1 does NOT" list points here.

## IGM-08 - WP-IGM-b feature additions for the quiet-window finder (WP-QUIET)

**Status:** OPEN, deliberately NOT part of the extraction (WP-IGM-a is a pure move and rename). (1) CPU PSI (`/proc/pressure/cpu`) in every passive snapshot;
(2) a "foreign writer" rate (all cgroups except iogovmon's own and host-tuning's) in each snapshot; (3) `iogovmonctl export busyness --days N --json`
returning, per bucket, the sample count and p50/p90 of each signal. Snapshots stay at most one per 15 min even when a tick is gated.

## IGM-09 - machine interfaces: `status --json`, `recommendations --json`, `export`

**Status:** OPEN (WP-IGM-b). `iogovmonctl status --json` (state, last runs, kill switch, recommendations, and iogovmon's own `io.stat` from its slice),
`iogovmonctl recommendations --json`, `iogovmonctl export ceilings` (IGM-07) and `export busyness` (IGM-08). No import coupling: consumers call the CLI.
Also the documents the design lists next to the guide: `docs/SPEC.md`, `docs/CONSUMERS.md`, `docs/CLI-SPEC.md`.

## IGM-10 - governed cgroups: auto-discovery of top-level slices

**Status:** OPEN (WP-IGM-b). The extraction made `governed_cgroups` default to an empty list (no `dev.slice` assumption). Without a list the governed series
are empty and the gates see "unknown governed write rate". Add auto-discovery of the top-level slices (explicit list optional, `protected_cgroups` stays
operator input) and decide how it interacts with "who else writes" (today every top-level cgroup except `exclude_others` is listed).

## IGM-11 - slice limits from the design: `MemoryHigh`, `TasksMax`

**Status:** OPEN. The design (3.6) lists `CPUWeight`, `IOWeight`, `MemoryHigh`, `TasksMax` for `iogovmon.slice`; the extraction kept the existing
settings (`IOWeight` token, `CPUWeight=20`, `MemoryMax=256M`). Add `MemoryHigh` below `MemoryMax` and a `TasksMax`, with the L2 guest assertions that the
limits appear on the live slice, and report the slice's own `io.stat` in `status`.

## IGM-12 - test hygiene carried over from mdt-cockpit

**Status:** OPEN. `test_prevention_checker_is_quiet_when_healthy_and_says_unknown_when_blind` was MDT-032 (CLOSED 6f669fa26: the fill result is injected); a
review of the remaining tests for live-host reads (`/proc`, `/sys`, the real filesystem fill) is still open. The unit-render tests call `systemd-analyze verify` on the host: a missing binary fails
the test (never skips) unless `IOGOVMON_ALLOW_MISSING_TEST_DEPS=1`.

## IGM-13 - daemon and control entry points

**Status:** CLOSED (WP-IGM naming split). The distribution `iogovmon` exposes `iogovmond` for `tick`, `watch`, `bench`, and `cleanup`, and
`iogovmonctl` for the operator and inspection commands. The old `iogovmon` console script is removed without an alias; `python -m iogovmon` stays
as a combined developer entry point. Systemd uses `iogovmond.service`, `iogovmond-tick.*`, and `iogovmond-bench.*`; the package, config, user,
environment variables, and `iogovmon.slice` keep their existing names.
