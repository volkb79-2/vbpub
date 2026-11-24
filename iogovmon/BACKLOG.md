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
**Evidence 2026-11-24:** r1002 has a recommend-only journal of more than 5 days (2026-11-19T04:08Z..11-24T05:40Z, 1,320 ticks, 107 bench runs with `r_seq` valid in 70, see IGM-17) after
acceptance-2. It is kept as the "48 h of ticks" candidate. The byte/PSI budgets are still to be verified from it. The timers were stopped and disabled
2026-11-24T05:40Z. See `docs/evidence/ACCEPT2-STAGE-2026-11-19.md`.

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
the device). Since iobench's disk-scope identity change (iobench-wp0), `iobench ceilings add --source governor` REQUIRES `--kind` (`file` for these
observations; `raw`/`probe` are not valid for iogovmon's scratch-file measurements), and old iobench records without a `disk_scope` are stale: the
exporter must emit the kind and never seed history from before iobench schema minor 1.

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

## IGM-14 - source-install version provenance (review M1/M2)

**Status:** CLOSED. `__version__` checks installed wheel metadata, then `iogovmon/_version_installed.py`, then `0+source`. The source installer generates the marker from
`[tool.iogovmon.install].version` in `pyproject.toml`, adding `+g<short-sha>` when Git can resolve the checkout. Tests cover all resolution paths and the installer's
`--destdir` staging output. M2's source-install version policy is resolved.

## IGM-15 - M0/M1, the installer adapter, and the first release still need the operator-approved write-mode safety acceptance

**Status:** OPEN, blocks the first `iogovmon-v*` tag and the default-on decision. Filed 2026-11-10. Source: the 2026-11-10 session audit T-006; `reports/iogovmon-DESIGN.md` rev 9; operator decision 2026-11-10.

**Problem.** The recorder/observation code exists, but the design's M0/M1 actuation work, the installer adapter, and the first release are not implemented or accepted. The project remains recommend-only and unreleased. A recorder-only branch or design is not the promised bounded service, and the conflicting default-on/recommend-only history cannot be resolved by inference.

**Operator decision (2026-11-10, record verbatim in substance).** Acceptance runs in WRITE mode on r1002 and v1001; those hosts are the acceptance environment. Rev 9 stays recommend-only until the named safety acceptance passes. After it passes, the service becomes default-on with bounds, rollback, audit, and opt-out.

**Minimal solution.** Complete the M0/M1 actuation path and the installer adapter as one gated implementation: bounded apply/revert engine, host-tune client protocol only if the selected rev 9 design requires it, installation from the built wheel, explicit rollback journal, audit records, and opt-out/kill switches. Register host-setup or installer integration at `services.iogovmon.install = false` until acceptance. Then run the named WRITE-mode acceptance on r1002 and v1001: induced distress under a protected cgroup, out-of-bounds and disabled-client refusals, failed verification rollback, provider-capability-shift freeze, repeated-regression backoff, opt-out, kill switch, and reboot/baseline restoration. Cross-reference IGM-01 (release acceptance) and IGM-03 (wheel install verb); do not duplicate their current contracts.

**Where.**

- `iogovmon/src/iogovmon/`: the bounded actuation, verification, rollback, and audit modules selected by rev 9.
- `iogovmon/src/iogovmon/cli.py`, `install.sh`, `iogovmon.toml.example`, and `units/`.
- Host-setup and debian-install-v2 installer adapters, with `services.iogovmon.install` remaining false until acceptance.
- `iogovmon/run-gate.toml`, `iogovmon/cmru.toml`, `iogovmon/docs/` and `iogovmon/CHANGES.md` for the release boundary.

**Acceptance idea.** FakeHost fixtures cover every bound, rollback, audit, opt-out, wrong-client and kill-switch branch. A VM/guest acceptance proves apply/revert and reboot restoration without touching the controller. The named r1002/v1001 WRITE-mode safety acceptance then records one induced episode, a bounded adjustment, a deliberate verification failure with automatic rollback, a capability-shift freeze, and complete audit/opt-out evidence. Hand-removing a bound, rollback, audit, or opt-out guard fails its focused case.

**Priority.** Major. This is the release and default-on safety gate for a host-writing service.

**Close when.** The M0/M1 and installer-adapter implementation passes its registered gates, the named WRITE-mode acceptance passes on both designated hosts with rollback/audit/opt-out evidence, and the first release records the resulting default-on decision with bounds.

## IGM-16 - `install.sh` ends with an ACL check that races the asynchronous udev trigger

**Status:** OPEN. Filed 2026-11-15 from the r1002 48 h acceptance install (source `vbpub@04c7af43c`, iogovmon 0.1.0).

**Problem.** Step 7 runs `udevadm trigger --action=change ... --sysname-match=<disk>`, which returns before udev has applied the rule, and the closing "Next, by hand" text tells the operator to verify the read-only ACL at once with `sudo -u iogovmon dd if=<node> ... iflag=direct`. On a reinstall right after an uninstall the first `dd` failed with `Permission denied`; `udevadm settle` and a second `dd` then succeeded and `getfacl` showed `user:iogovmon:r--`. An operator reads that first failure as a rejected ACL review (IGM-01) instead of a timing effect.

**Do.** Run `udevadm settle --timeout=30` right after the trigger, and have the installer perform the read-only verification itself (read one 4 KiB block as the service user, assert `test -w` is false, print the `getfacl` line) so the "Next" text no longer asks for it by hand. Cover the new step in `tests/` for the staging (`--destdir`) mode as a skipped step.

**Acceptance.** A real install followed by an immediate uninstall/install cycle never prints a failed ACL read; the staging test asserts the skip line.

**Priority.** Minor.

## IGM-17 - one empty-offset sequential-read lane marks an otherwise valid bench `aborted`

**Status:** OPEN. Filed 2026-11-15 from the same r1002 install.

**Problem.** `bench.seq_read` picks 8 random aligned offsets and requires a non-zero first block. On a mostly unallocated disk (r1002: 512 GiB virtual disk, roughly 25 GB of data, the rest unwritten or swap) all 8 picks can be zero, so the lane sets `aborted = "no_nonzero_offset_found"`. The caller turns any lane abort into a `LaneAbort`, so the run is journalled with `status: "aborted"` and `abort: no_nonzero_offset_found` although `w_fresh_qd1`, `w_fresh_qd4` and `w_over_4k` all finished valid (the first bench on r1002: 3 valid write lanes, 65 MiB written, then `aborted`). `status` then shows `bench: aborted no_nonzero_offset_found`, which says nothing about the disk or the safety gates (only a crashed run counts toward `bench_max_failures`, so there is no backoff), and a weekly escalation only follows an `ok` run.

**Do.** Treat "no allocated offset found" as a lane result (`valid: false`, reason `thin_disk`) instead of a run abort, and bias the search toward allocated space (more probes at the same 4 KiB cost, or probe the offsets of the witness blocks that `tick` already found non-zero). Keep real aborts (`budget:*`, `unsafe_path`, gate stops) as run aborts.

**Acceptance.** A fake disk that is all zero except a few blocks yields a bench with `status: "ok"` and `r_seq.valid == false`; the budget numbers in `plan` still hold (a probe is charged per attempt).

**Priority.** Minor; it makes the r_seq series sparse on thin disks and holds back the weekly escalation (it follows an `ok` run only).

**Update 2026-11-24.** On r1002 after acceptance-2 (2026-11-19T04:08Z..11-24), `r_seq` was lost to `no_nonzero_offset_found` in 36 of 107 bench runs (34 %); it was valid in 70 and invalid without a recorded reason in 1.

## IGM-18 - the bench's own write load aborts the bench, so a thin disk never reaches the read lane and the lane windows never fill

**Status:** OPEN. Filed 2026-11-16 from the r1002 0.1.0 acceptance (+12 h check).

**Problem.** The safety gate and the bench's own load are in conflict on a
slow/thin disk. Two limits govern a bench run:

- the pre-run gate (`Gate.check("bench")`, `gating.py:104-112`) requires
  system `io.pressure some avg10 <= cfg.bench_pressure_some_max` — default
  **1.0** (`config.py`, the dataclass field; `HARD_MAX` clamps it to 5.0);
- the MID-run guard (`RunGuard.check()`, `gating.py:143`) allows up to
  `bench_pressure_some_max * 5` in strict mode — default **5.0** — and raises
  `StopRequested(f"io_pressure_rose:{some}>{limit}")` when a recheck crosses
  that.

So a bench may legitimately start at `some avg10 <= 1.0`, and then abort as
soon as ITS OWN writes drive the system-wide `some avg10` above 5.0. Measured
on r1002 (512 GiB thin virtio disk, host idle, pre-run context quiet: io PSI
`some avg10` 0.0, load 0.05/0.26, 0 inflight, protected harm none):

| bench | result | what it wrote before it stopped |
|---|---|---|
| install-time | `aborted no_nonzero_offset_found` (IGM-17) | 3 write lanes valid (16 + 48 + 1 MiB) |
| scheduled 20:37Z | `aborted io_pressure_rose:5.25>5.0` after 2.06 s | lane `w_fresh_qd4`: 28 of 144 MiB |
| scheduled 02:37Z | `aborted io_pressure_rose:7.66>5.0` after 3.9 s | lane `w_over_4k`: 0.25 of 1 MiB |

2 of 2 scheduled benches stopped themselves; nobody else was hurt, so this is
the gate working as designed, not an unsafe event. The consequences are the
finding:

1. **The read lane is starved.** `run_bench` runs the write lanes first and
   raises `LaneAbort` on the first aborted write lane (`bench.py:263-300`), so
   `r_seq` never runs in an aborted bench. With this disk the `r_seq` series
   gets samples only from a bench that survives all three write lanes.
2. **The lane windows stay effectively empty.** Every lane is `LEARNING(1/5)`
   after three benches (only the install-time bench produced the full write
   set), so `recognize` produces no qualifying window and the weekly
   escalation (which follows an `ok` run only, `_bench_bookkeeping`) never
   triggers. The acceptance then cannot reach "full benches as designed".
3. **Nothing corrects it, by design of the abort path.** A PSI stop is
   `status: "aborted"` with `crashed: false`, so `bench_fail` stays 0: cadence
   stays ~6 h and the same abort repeats on the same disk indefinitely.

Two things make the disagreement sharper than they look. The knob that
governs it is clamped (`HARD_MAX["bench_pressure_some_max"] = 5.0`), so the
strict limit's own ceiling is 25 — an operator could already raise the field
to 5.0 and get 25; nothing documents that, and a value that only exists
through a 5x multiplier inside `RunGuard` is not discoverable. And the two
numbers measure different things: the pre-run gate asks "is the system calm
now", the mid-run guard asks "is `some` still under 5x the bench limit" —
an absolute threshold on a quantity the bench itself is responsible for
raising on a slow device.

**Do (choose one disposition; the item is the decision, not a specific
mechanism).**
1. **Delta rather than absolute (preferred).** Sample `some avg10` immediately
   before the bench's first IO, and run the mid-run guard against
   `max(cfg.bench_pressure_some_max, baseline_some + allowance)` — the bench is
   then judged on the pressure IT adds, not on a level it inherits. Record the
   baseline and the allowance in the bench record so the decision is auditable.
2. **Separate the write-phase limit from others.** A per-lane limit
   (`bench_pressure_some_max_write`) that admits the bench's own write
   concurrency, with the strict global limit reserved for the read lane and
   the 4 KiB sync lane (which is what "another workload is being hurt" is
   about).
3. **Accept partial benches, disclosed.** Treat a self-caused PSI stop as a
   lane disposition rather than a run abort: journal `status: "ok"` with
   `r_seq.valid: false` and a `pressure_self` reason (the shape IGM-17
   recommends for the read lane), so the write-lane windows fill and
   `recognize` can proceed, while the missing read lane stays visible.
   Whichever is chosen: keep the real aborts (`budget:*`, `unsafe_path`,
   the kill switch, a stop flag) as run aborts, and do not raise any HARD_MAX
   silently.

**Where.** `iogovmon/src/iogovmon/gating.py` (`RunGuard.check`,
`Gate.check("bench")`), `iogovmon/src/iogovmon/config.py` (the dataclass fields
and `HARD_MAX`), `iogovmon/src/iogovmon/bench.py` (`run_bench`'s lane order and
`LaneAbort` handling) and `iogovmon/src/iogovmon/runner.py` (the bench record
and `_bench_bookkeeping`), `iogovmon/docs/DESIGN-GUIDE.md` §6 ("Mid-run abort:
... pressure rising to 5x the bench limit") and `iogovmon/README.md`'s gate
description. Tests live with the existing gating/bench tests.

**Acceptance.** With a simulated thin disk (real `io.pressure` files injected
through the collector's root, as the existing fixtures do) whose write lanes
raise `some avg10` above the strict limit mid-lane: option 1 completes the
bench, records the baseline and the allowance, and reaches `r_seq`; option 3
journals `status: "ok"` with `r_seq.valid == false` and `pressure_self`, and
the write-lane windows are eligible. A real abort (`budget:write`,
`unsafe_path`, kill switch) still journals `status: "aborted"` and still
counts toward `bench_max_failures`. A mutation that reverts the disposition
makes the thin-disk fixture red.

**Priority.** Should-fix for the acceptance: as measured, a thin disk never
reaches a full bench, so the bench lane's windows and the weekly escalation
cannot be reached at all, and IGM-01's 48 h evidence can only ever show
`aborted` benches on this hardware.

**Close when.** A simulated thin disk produces either a complete bench that
reaches `r_seq` or a disclosed partial bench with an eligible write-lane window;
the chosen disposition, its knob (and any changed clamp) is documented in the
DESIGN-GUIDE and README; the real-abort cases are unchanged; and the r1002
bench record after the fix shows at least one completed window per write lane.

**Update 2026-11-17 (end of acceptance-1 on r1002, 55.9 h).** 10 benches ran:
9 aborted `io_pressure_rose` at 5.25 to 8.56 (each started on an idle host, io
PSI `some avg10` 0.0 to 0.05, and stopped after 2 to 4 s and 65 to 200 MB
written), 1 aborted `no_nonzero_offset_found` (IGM-17); none completed. The 636
ticks (to 22:31Z) were all fine (max gap 5.6 min, budgets respected), so only the bench
lane is affected. Interim, config only: `bench_pressure_some_max = 3.0` on
r1002 from 2026-11-17T22:59Z (pre-run gate 3.0, in-run limit 15.0; the clamp
allows up to 5.0); acceptance-2 runs on it. The code-level fix is still the
decision of this entry; one more option beside those above: on a host with no
protected workload the strict guard measures the bench's own load, so make the
`5 x` factor a named key and let the in-run guard read pressure that excludes
the bench's own cgroup, or the protected cgroups' pressure only.

## IGM-19 - `r_cold` yield on a thin disk: windows go insufficient at the default attempts, and the population shifts with fill

**Status:** OPEN. Filed 2026-11-24 from the r1002 acceptance-2 fill stage (`docs/evidence/ACCEPT2-STAGE-2026-11-19.md`).

**Problem.** `r_cold` reads random blocks of the device and keeps only non-zero blocks. On a thin disk the non-zero fraction is whatever data
exists. On r1002 it was 7-10 % at baseline. With the per-tick defaults (`read_samples_per_tick=12`, `read_max_attempts=36`, 300 s ticks) and
the condensed `window_hours=1`, a 1 h window gets 22-43 non-zero reads against `min_samples["r_cold"]=30`, so windows go `insufficient` on a
lightly used disk. (The default `window_hours` is 24: a 24 h window gets 518-1037 reads at a 5-10 % yield and goes `insufficient` only below
about 0.3 %.) Acceptance-2 ran with `read_max_attempts=128` (`.cond-v2`) to avoid this. Fill changes the population itself: the yield rose
from 10.4 % (A0) to 75 % (B4/B5a) and fell back to 10.3 % only after `fstrim`. The lane then measured fresh fill data instead of the
original data. A capability change and a population change cannot be told apart from the lane's median. That confound applies at any window
length. IGM-17 (`r_seq` finds no non-zero offset) has the same root cause: probing a thin disk with random offsets.

**Do (the item is the decision).**
1. Derive `read_max_attempts` from a measured yield: probe once at install or plan time and record it, or make the attempts adaptive up to
   the HARD_MAX of 128. Either way, a 1 h window must reach `min_samples` at a 5 % yield.
2. Record the yield (non-zero / attempts) per window in the journal and the recognizer state. A window whose yield differs from the baseline
   windows by more than a stated factor is marked `population_shift`, not judged. (Not `confounded`: the recognizer already uses that name for
   its settings-epoch flag.)

**Where.** `src/iogovmon/rawread.py` (offset sampling), `config.py` (`read_max_attempts`, `min_samples`), `recognizer.py` (window qualification).

**Close when.** A fixture with a 5 % non-zero device fills a 1 h `r_cold` window at the defaults. A fixture whose yield jumps from 10 % to
70 % yields a `population_shift` window instead of a shift or no-shift verdict. Both are documented in the DESIGN-GUIDE.

## IGM-20 - the recognizer is blind to tail-only shifts and to median drifts under ~1.4x

**Status:** OPEN. Filed 2026-11-24 from the r1002 acceptance-2 fill stage (`docs/evidence/ACCEPT2-STAGE-2026-11-19.md`).

**Problem.** The recognizer judges each window by its median, through log2 CUSUM with `cusum_k = 0.5` (ignores drift under ~1.4x) and
`noise_floor_log2 = 0.25`. During the fill stage it raised no event at all, beyond `baseline_established`. That held while `r_seq` fell
25 % and `w_fresh_qd4` fell 20-30 % at the median, while `w_over_4k` rose 15-20 %. These shifts stayed under the allowance. Earlier
synthetic checks (`synth_shift_check.py`) also showed tail-only x8 and x1.5 median shifts unseen.

**Do (choose one disposition).**
1. Add one tail statistic per latency lane (window p95 or p99, with its own baseline and CUSUM), judged separately from the median, with its
   own `min_samples`.
2. Document the limit: the recognizer detects median capability changes of about 1.4x asymptotically (with `k_eff=0.5`, `h=2` and
   `confirm_windows=3`, a 1.5x shift takes ~24 windows to build S and alarms at about window 26, while a 2x shift alarms at about window 6).
   Tail regressions are out of scope, and README, DESIGN-GUIDE and the `recommendations` text say so.
3. Idea, not a decision: the acceptance-2 run saw an ~80 ms p95/p99 tail on btrfs file reads (refprobe `read-fill`, after the B3 churn) that no
   iogovmon lane observes, because `r_cold` reads the raw device. A btrfs file-level cold-read lane would be the only way iogovmon could see it.

**Where.** `src/iogovmon/recognizer.py`, `config.py` (`cusum_*`, `noise_floor_log2`), `docs/DESIGN-GUIDE.md`.

**Close when.** Option 1: a fixture with a tail-only x8 shift raises `shift_detected` on the tail statistic, and a quiet fixture raises
none. Option 2: the limit is stated in all three places, and `synth_shift_check.py` (carried by the acceptance tooling import, `iogovmon/tools/accept/`) asserts it.

## IGM-21 - the HARD_MIN/HARD_MAX clamps are code constants, not a documented limits profile

**Status:** OPEN. Filed 2026-11-24 from the r1002 acceptance-2 fill stage.

**Problem.** `config.py` clamps every safety value into `HARD_MIN`/`HARD_MAX`. Examples: tick ≥30 s, bench ≥1 h, read 256 MiB/day,
write 2 GiB/day, bench pressure some 5 / tick pressure some 25, `read_max_attempts` ≤128. The clamps are right as safety rails. But they
are code constants, and nothing documents them as a profile. Acceptance-2 had to run on a config that already sat at several maxima
(`read_max_attempts=128`, 256 MiB/day). Changing a clamp for a condensed acceptance would mean editing code, and the only record of a
clamp is `Config.clamped` at load time (and the clamps are printed by `plan` and `config`).

**Do.** Move the clamps into one documented limits profile, for example a `LIMITS` table in `config.py` rendered into the README by the
existing docs check. Each limit carries its reason. An explicit operator override (a named file or key, e.g. `[limits.override]` with a
mandatory `reason`) is journaled as an `action` record at load time. Without an override, the clamps behave exactly as today.

**Where.** `src/iogovmon/config.py`, `README.md`, `docs/DESIGN-GUIDE.md` (the safety section).

**Close when.** The limits table is the single source that the README shows. A test shows that an override without `reason` is refused,
and that an override with `reason` is applied and journaled. The default clamps are unchanged, and a test pins them by behaviour: a
too-large value is clamped and reported.
