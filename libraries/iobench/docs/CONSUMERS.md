# iobench CONSUMERS (how to adopt it)

Every block below is pasteable. The docs-sync test (`IOB-DOC-02`) parses each `json` block with the shipped loader and each `bash` `iobench` invocation with the CLI's own
parser in `--dry-run` mode. WHAT is in the [README](../README.md); WHY in the [DESIGN-GUIDE](DESIGN-GUIDE.md); the contract in the [SPEC](SPEC.md).

## 0. Install

The measurement core needs only Python 3 and `fio` >= 3.30 (JSON output with `clat_ns` percentiles). The **command line** (`iobench.cli`) adopts `cli-extended`, so it also needs that
library on `PYTHONPATH` (or installed). In this repo:

```bash
install -d /usr/local/lib/iobench && cp -r libraries/iobench/src/iobench libraries/cli-extended/src/cli_extended /usr/local/lib/iobench/
PYTHONPATH=/usr/local/lib/iobench python3 -m iobench --version
```

Out of repo: the cmru-released `iobench` wheel (GitHub Releases only, never PyPI; its `cli-extended` dependency comes from the same releases). There is no zipapp; in-repo consumers copy the source as shown above.

## 1. Prepare the scratch target (once per host)

iobench writes to a **dedicated** block device or partition and refuses anything else. The prepared target carries an iobench marker in its **last** MiB (nothing else is ever written
there). iobench never creates, mounts or unmounts a filesystem: the work dir for the matrix is a directory on an **existing** filesystem.

```bash
lsblk -b -o NAME,SIZE,PARTUUID,FSTYPE,MOUNTPOINTS /dev/vda
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0007
iobench target show /dev/vda7 --json
```

`target init` refuses a mounted (in the caller's or PID 1's mount namespace), swap-active, signature-bearing (`blkid -p`) or root-disk target; the override requires `--i-own-this-device`
**and** the matching `--expect-size` and `--expect-partuuid` (or `--expect-serial` when the device reports one). A device with neither a PARTUUID nor a serial is refused. Mounted, swap and
root refusals can never be overridden. Without a marker no `calibrate` or `matrix` command opens the device.

## 2. Calibrate (once per host or storage class)

```bash
iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile standard --confirm-quiet-window --protect-cgroup game-server.slice --abort-psi 40
```

* Hard budgets: `veryquick` 3.5 min (173 s planned), `quick` 12 min, `standard` 60 min (3400 s planned), `thorough` 75 min (4270 s planned). Skipped stages are recorded, never shortened silently.
* Standard and thorough include an optional direct-read cache-evidence stage after E-cold: 30 s idle + two 15 s reads in standard, 60 s idle + two 15 s reads in thorough. Set `--cache-idle-delay-s 0` to skip it; the maximum is 230 s for standard and 290 s for thorough so the planned stage total stays within its hard budget. Quick and veryquick omit it. These are indirect hints: the guest cannot see which hypervisor/provider cache layer served a read, and the hints do not supply proposals. See the [design rationale](DESIGN-GUIDE.md#73-cache-and-burst-observations).
* A live host REQUIRES `--confirm-quiet-window`, `--protect-cgroup` and `--abort-psi` (no defaults; the run refuses without them). Unlisted systemd units are not detected: you are the guard.
* Output: `/var/lib/iobench/calibration.json`. Re-run on an identity change (`check` exit 3) or when `check` warns `recalibrate_recommended`.
* `quick` reports every cache-dependent value as `unproven: quick_profile` (honest, not absent).
* `veryquick` (about 3 minutes, for an installer that wants a binding and a plausibility check, not tuning values): S1, a 20 s fill, a cold probe, one 30 s write series and four 12 s cells. **Every cap and model value is `unproven: veryquick_profile`**; the model is a coarse estimate (`metering: coarse`, also `formula_only`: bps = the 128 KiB sequential rate, randiops = the 4 KiB random rate), QoS latencies and writeback are `absent: stage_skipped`, and it adds nothing to the ceiling history. Use it for: the disk binding (`identity.disk_scope`), `quality.plausibility.verdict` (`pass`, `fail`, `inconclusive`: refuse an operator model that contradicts a `fail`), and `calibration.veryquick.write_burst_seen`. Prove nothing from it: a `proven` value needs `standard` or `thorough`. If the default state directory already contains a fuller result, use a separate path:

  ```bash
  iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile veryquick --state-dir /var/lib/iobench/veryquick --confirm-quiet-window --protect-cgroup game-server.slice --abort-psi 40
  ```

  `veryquick` refuses to replace `calibration.json` or `result.json` if either contains a non-veryquick or unreadable record.
* Every profile that has sequential read and write cells records IOB-PLA-19 using matching block size, queue depth and region. A ratio up to 20x passes; above 20x through 100x is inconclusive unless a hit-like cold probe corroborates it; above 100x fails without corroboration. The thresholds are inferred and IOB-ACC-01 must calibrate them. Only veryquick carries the ratio status into its existing plausibility-verdict semantics; a veryquick `pass` describes only its available evidence and does not prove that no cache exists.

Read the append-only cache/burst history with:

```bash
iobench capability --json
iobench capability --since 2026-11-01T00:00:00Z --json
```

Calibration rows are keyed to the whole-disk scope and carry their `raw` or `file` evidence kind. `check --json` also includes the count and latest matching row. These records describe observations only: they never feed the io.cost coefficients or other proposal values. Veryquick stores only its measured `write_burst_seen` boolean; numeric capability fields are null because that short profile does not measure them.

## 3. Matrix iteration (the fast path)

```bash
iobench matrix --target /dev/vda7 --work-dir /bench/iobench-work --confirm-quiet-window --protect-cgroup game-server.slice --abort-psi 40
iobench matrix --target /dev/vda7 --work-dir /bench/iobench-work --knob max_sectors_kb --candidates 64,128,256 --repeat --confirm-quiet-window --protect-cgroup game-server.slice --abort-psi 40
```

The first form runs 6 cells (typical 11 min, worst case 15 min) and writes `/var/lib/iobench/result.json`; the second compares one knob (A B A B, typical 4 min, worst case 8 min). Every runtime
setting it changes is journalled in `/run/iobench/restore.json` and restored on exit, signal or crash (and by the next start); a knob someone else changed meanwhile is left alone. It
never writes `io.max`.

## 4. Read the result (the wizard and `check.sh` use only this)

```bash
iobench check --json; echo "exit=$?"
iobench check --section caps --json
iobench show --proposals --json
```

Exit codes of `check`: `0` every requested proposal is `proven`; `3` stale (disk identity differs); `4` no result; `5` unreadable (`reasons`: `corrupt`, `schema_unsupported`,
`unreadable`, `calibration_missing`, `calibration_superseded`, `damaged_proposals`); `6` only an aborted or failed record exists; `7` identity current but at least one requested proposal is `unproven` or `absent`.
Also from `iobench`: `1` runtime failure, `2` refusal or usage, `8` run aborted (restored), `9` restore or `io.max` verification failure.

```json
{"state": "current_unproven", "reasons": [], "warnings": ["scheduler_changed"], "limits": ["bucket_exclusion_s<=240"], "identity_strength": "weak",
 "sections": {"caps": {"proven": 0, "unproven": [{"key": "RIOPS", "reasons": ["cache_model_undetermined"]}], "absent": []}},
 "result": "/var/lib/iobench/result.json", "calibration_id": "cal-20261021T020000Z-7b2d0c1e", "capability": {"count": 0, "latest": null}}
```

The `proposals` object (schema 1). Every value has its own status, rule and inputs:

```json
{
  "writeback": {
    "dirty_bytes": {"value": 16777216, "status": "proven", "rule": "R1", "inputs": {"W_s": 2.0, "R_drain_bps": 10500000, "inflight_bytes": 1048576, "T_eff_bytes": 1717986918}, "basis": "confirmed-by-sweep", "flags": []},
    "dirty_background_bytes": {"value": 4194304, "status": "proven", "rule": "R1b", "inputs": {"dirty_bytes": 16777216}, "basis": "ratio-inferred", "flags": []},
    "nr_requests": {"value": 16, "status": "proven", "rule": "R2", "inputs": {"qd90": 8}, "basis": "confirmed-by-sweep", "flags": []},
    "max_sectors_kb": {"value": 64, "status": "proven", "rule": "R3", "inputs": {"R_drain_bps": 10500000, "read_ratio": 0.21, "F": 0.25}, "basis": "measured", "flags": ["read_floor_violated"]}
  },
  "caps": {"dev_aggregate": {
    "RIOPS": {"value": 2829, "status": "unproven", "reasons": ["cache_model_undetermined", "worst_from_single_run"], "rule": "R5", "inputs": {"U": 0.7, "basis_iops": 9400, "worst_over_median": 0.43}, "basis": "measured", "flags": []},
    "WIOPS": {"value": 1100, "status": "unproven", "reasons": ["cache_model_undetermined", "worst_from_single_run"], "rule": "R5", "inputs": {"U": 0.7, "basis_iops": 3596, "worst_over_median": 0.43}, "basis": "measured", "flags": []},
    "RBPS": {"value": 0, "status": "absent", "reason": "bps_basis_missing", "rule": "R5", "inputs": {}, "basis": "measured", "flags": []},
    "WBPS": {"value": 0, "status": "absent", "reason": "bps_basis_missing", "rule": "R5", "inputs": {}, "basis": "measured", "flags": []}}},
  "iocost_model": {"wbps": {"value": 6291456, "status": "unproven", "reasons": ["worst_from_single_run"], "rule": "R6", "inputs": {"worst_bps": 6291456, "metering": "byte"}, "basis": "single-run", "flags": []}},
  "iocost_qos": {"min": {"value": 100, "status": "proven", "rule": "R6", "inputs": {}, "basis": "measured", "flags": []},
                 "max": {"value": 330, "status": "unproven", "reasons": ["worst_from_single_run"], "rule": "R6", "inputs": {"best_over_worst": 3.0}, "basis": "single-run", "flags": []}}
}
```

A value is a number or the word `kernel` ("leave the kernel's own value; the device is fast enough"). Absent keys carry `value: 0` only as a placeholder; their `status` is the truth. iobench persists
nothing; host-setup renders and verifies the persistent files.

## 5. What the statuses mean and what to do

| You see | Meaning | What to do |
|---|---|---|
| `proven` | the basis is steady and any cache dependence is excluded by independent evidence | usable; the wizard may take it as the proposal |
| `unproven` + reasons | a value is shown, but the listed condition means it may be too high or is not yet verified | the wizard/installer shows the value **with the reasons** and applies it only after an explicit operator accept; otherwise use the configured value |
| `absent` + reason | nothing to propose | run the missing stage or keep the configured value; nothing can be applied |
| exit `3` stale | the disk identity changed | recalibrate |
| exit `7` | valid result, some requested proposal is not proven | read `sections` in `check --json`, accept or rerun with a better profile |
| exit `8` aborted | the protect-cgroup pressure rule or `/run/iobench/abort` stopped the run; knobs were restored; no result replaced | rerun in a quieter window |
| exit `9` | a restore or `io.max` verification failed | inspect the named file; `iobench restore` is idempotent; do not run another benchmark until it is clean |

### 5.1 Unproven reasons

| Reason | Meaning |
|---|---|
| `cache_model_undetermined` | the cold-region probe was bimodal, overlapping or indeterminate, or the latency-split cross-check disagreed with it (rerun `thorough`, or accept) |
| `cache_residual_possible` | a figure from the measured largest rung is shown and no cold-region probe exists |
| `fresh_not_measured` | the fresh-block write penalty could not be measured on a standard run (no discard support, no fresh extent, or the stage failed): the write cap lacks the fresh half of `min(rewrite, fresh)` |
| `cache_not_excluded` | the cold region is as fast as the warm one with no cliff (`no_cache_observed`): equally consistent with no cache and with a cache at least as large as the written extent; a larger fill or the ceiling history can separate them |
| `no_cliff_hit_like_mode` | no cliff, and the cold-region probe is hit-like: the cache may hold the whole fill |
| `device_smaller_than_cache_margin` | the largest rung is below 1.2x the cliff, or the cold probe is hit-like with a cliff: the target is too small to see past the cache |
| `ceiling_moved` | the provider rate changed during a comparison; both values are in the evidence |
| `budget_limited` / `short_observation` / `quick_profile` | the time budget limited the evidence / a write series was not observed long enough after its knee / the `quick` profile |
| `veryquick_profile` | the `veryquick` profile measured it (every cap and model value of that profile; the model also says `formula_only`) |
| `worst_from_single_run` | worst/best plateaus come from this run only (no ceiling history yet) |
| `metering_undetermined` / `operator_override` | S6 did not pass both metering tests / you typed `--metering` |
| `shape_fit_poor` | the per-IO + per-byte linear fit is poor |
| `noise_floor_high` / `target_missed` / `drain_below_ladder` / `formula_only` / `cross_disk` / `identity_weak` | sweep noise > 4 / the chosen settings missed the sync-latency target / drain rate disagrees with the ladder / sweep skipped / work dir on another disk / disk has no serial |

### 5.2 Absent reasons

`stage_skipped`, `stage_failed`, `basis_unsteady`, `basis_missing`, `bps_basis_missing`, `no_calibration`, `missing_from_document` (`check` only: the schema requires this section or key and the file lacks it).

### 5.3 Cache state, metering, fit, limits

* `cache_state` (reads): `cold_measured` (the earliest-written region read as cache-free: proven), `rate_cap` (the cold-region probe itself is flat at a provider cap: proven; a flat line elsewhere is only a recorded fact), `no_cache_observed` (the cold region runs as fast as the warm one, no cliff, unimodal: **unproven**, `cache_not_excluded`, because it cannot be told from a cache at least as large as the written extent), `cold_hit_like` (hit-like with a cliff, on a file target, or a cold rate below the warm reference: unproven), `undetermined`. Writes have no cache state: they are proven by the time axis (>= 120 s after the knee, bytes after >= 2x the burst).
* `metering`: `byte`, `iops`, `mixed`, `undetermined`, `coarse` (veryquick: a two-cell estimate, no class determined); E1 `fit` (informational): `good`, `poor`, `insufficient`, `plateau`.
* `limits`: `quick-profile`, `very-quick-profile`, `limited-by-budget`, `short-observation`, `single-run`, `stage_skipped:<Sn>`, `filesystem_layer` (file target: extent mapping and journal overhead are inside every figure), `fresh_semantics_filesystem` (file target: the "fresh" write is `fresh_fs_alloc` and counts toward the cap only on a `discard` mount), `bucket_exclusion_s<=<N>` (the longest observation: bursts longer than that are not excluded).
* Write caps are proven by one series: the observation runs at QD32 (240 s, `thorough` 900 s) and bounds `cap_basis.write.value` (`inputs` lists every figure the minimum was taken over); a cap without the fresh half says `fresh_not_measured`
  (or `budget_limited`); this also covers a file target on a mount without `discard`, where the measured fresh penalty is excluded by design. The write BPS cap is `unproven: short_observation` until the sequential series is itself time-proven (backlog IOB-BL-02). BPS caps and the read model keys carry the real reasons of the basis they rest on. **Exit 0 of `check` for the BPS caps and the model needs a ceiling history for `seq1m`**: nothing iobench writes
  populates it yet (the S6 sequential cells are 30 s, below the 60 s plateau rule), so BPS caps and the model keys need `iobench ceilings add` (or a later P4-derived history) to reach exit 0; exit 7 is the honest answer meanwhile (backlog IOB-BL-03).
* `quality.checks[]` now includes the run-level plausibility checks PLA-07, -09, -17 and -18 (and PLA-06/-13 per run); `restore.warnings[]` carries `inflight_not_drained`; `evidence.history_rotated` names a mismatched or unreadable
  `ceilings.jsonl` moved aside as `.superseded-<timestamp>`; `calibration.reference.moved_by_dir` says which direction's reference moved.
* Check ids in `quality.checks[]`: IOB-PLA-01 tail flat, IOB-PLA-02 working-set rule, IOB-PLA-03 cached-tier factor, IOB-PLA-04 mean vs steady, IOB-PLA-05 flat-line, IOB-PLA-06 Little's law, IOB-PLA-07 bytes consistency, IOB-PLA-08 read-only-written, IOB-PLA-09 fill vs sequential, IOB-PLA-10 cache state, IOB-PLA-11 shape fit, IOB-PLA-12 noise floor, IOB-PLA-13 fresh vs rewrite, IOB-PLA-14 data integrity, IOB-PLA-15 identity stable, IOB-PLA-16 extent integrity, IOB-PLA-17 outside history, IOB-PLA-18 fresh write bytes/s (info; also records whether a discard re-arms the fresh-block penalty, hypothesis). A `fail` degrades only the groups it lists; -14, -15 and -16 fail the whole run.

### 5.4 Basis, check states and warnings

* `basis` (how a value was reached): `measured`, `confirmed-by-sweep`, `ratio-inferred`, `formula-only` (the sweep was skipped, so the formula was never confirmed), `single-run`.
* `check` states and their exit codes: `current` 0, `current_unproven` 7, `stale` 3, `absent` 4, `unreadable` 5, `aborted_only` 6 (only an aborted or failed record exists).
* `check` warnings (advisory, never change the exit code): `scheduler_changed`, `ram_changed`, `kernel_changed`, `root_source_changed`, `workdir_fs_changed` (the host drifted since the run; a work dir that was removed after the run is not a change), `target_changed` (the partition or file that was measured is gone, formatted, replaced or resized: the disk it was on is what the result describes, so this is not `stale`), `older_than_180_days`, `recalibrate_recommended` (the ceiling history moved away from the calibrated plateau), `last_run_aborted` (a newer run was aborted; the good result is kept).

## 6. mdt-cockpit wizard (MDT-HS-05 and the io.cost wizard, D-IOC-4)

* The wizard **asks whether to run the benchmark, default yes (a configurable key)**; it never starts one silently. It calls `iobench calibrate` if `iobench check --calibration` reports no current calibration, then `iobench matrix`.
* Precedence for each key: **benchmark (a `proven` value, or an `unproven` one the operator explicitly accepted with its reasons shown) > the configured value > `kernel`**. An `absent` value is never applied.
* Latency source (D-IOC-3): the governor's histogram when one runs (`--governor-histogram`), otherwise `proposals.iocost_qos` (S7).
* `check.sh` uses `iobench check --json` and reports OK / MISMATCH / "not managed" / "could not read" distinctly.

## 7. Ceiling history (governor or timer probe)

```bash
iobench ceilings add --dir write --workload seq1m --bps 14680064 --duration-s 600 --source governor --kind file
iobench ceilings summary --window-days 30 --json
iobench ceilings summary --kind probe --json
```

The history belongs to the **disk**, not to the partition: its key is the disk scope (model, serial, wwn and size of the whole disk; a disk without a usable serial or wwn is also bound to its host), so a throwaway partition that is deleted after a calibration, or a file benchmark and a raw one of the same disk, share one history. Each observation says what it was measured on (`--kind`): `raw` (a device or partition), `file` (a file on a filesystem, e.g. a governor's scratch-file write bench) or `probe` (a read-only raw probe). Source/kind pairs are constrained: benchmark and governor accept `raw` or `file`; probe accepts `probe` only. `--kind` may be left out for a benchmark (the calibrated target's kind) and a probe (`probe`); a governor must say. **Stability evidence:** writes count raw and file rows; reads count raw and probe rows. **Proposal values:** raw rows only supply model and cap values. Thus the default `ceilings summary` and `check.history` report stability-eligible rows, while the history used to derive model/cap values is filtered to raw rows; `by_kind` reports each class separately, and `--kind K` summarizes that one class regardless of eligibility. A calibration made before disk scope existed (`schema_minor` 0) is `stale` for `check` and refused by `ceilings add` (`identity_legacy`): recalibrate.

```json
{"history": "recorded", "window_days": 30, "write": {"seq1m": {"worst": 6291456, "median": 14680064, "best": 18874368, "n": 41}}, "read": {"seq1m": {"worst": 1300000000, "median": 1400000000, "best": 1400000000, "n": 12}}}
```

For value derivation, `worst` and `best` come from raw rows and feed `min = 100` and `max = ceil(110 x best / worst)` (here 330). The stability summary can also include file write and probe read rows, but those never lower or supply proposal values. With no stability-eligible records the summary says `"history": "single-run"`; when raw history is absent, history-backed values use the run's own measurements and carry `unproven: worst_from_single_run`.
Until a probe or governor appends observations (D-011), the history is `single-run` on hosts that only ran the benchmark.

Weak scopes treat QEMU's indexed `QM00001`-style serials, the `QEMU HARDDISK` model, VirtualBox `VB` serials with two eight-hex groups, `vdisk` plus a decimal index, all-zero/repeated-pattern strings and common placeholders as absent. If the target path is gone, `find_disk` requires the remembered whole-disk name, equal model and size, and a candidate still without a usable serial or wwn. A non-null stored machine-id hash must match a live non-null hash; a known hash that goes missing or becomes `uninitialized` makes the record stale. A chroot-made identity with a null stored hash binds no host, so it can remain current after first boot even if its target partition is deleted; `iobench check --json` reports the missing measured target as the advisory `target_changed` warning. Because the live scope key can differ after first boot, consumers read `disk_scope.key` from the stored identity rather than recompute it. A same-name, same-model, same-size weak disk swap remains indistinguishable by construction (IOB-BL-21). Machine-id uniqueness is assumed; cloned templates can violate it (IOB-BL-25).

## 8. A host with no spare partition: file targets

The wizard's default (D-IOC-4) must be able to run on production hosts that have no raw partition to give. A **file target** is a preallocated file on an existing filesystem, written once in
full and accessed with `O_DIRECT`. Its identity is (filesystem UUID, inode, size) with the marker in its last MiB and a sidecar `.iobench-target`; every writing stage re-verifies it. Refused:
tmpfs/ramfs/overlay, symlinks, hard links, too little free space, and on the root filesystem a `--size` above 10 % of the free space (`--allow-large-root-target` overrides; `--size` is required and is the hard upper bound). Free space is re-checked while the fill runs. The "fresh" write is reported as `fresh_fs_alloc` with the filesystem's `discard` mount option and enters the write cap only on a `discard` mount; iobench never runs `fstrim`. Every figure carries the `filesystem_layer` limit and is never mixed with device-kind history.

```bash
iobench target init --file /var/lib/iobench/target.img --size 6442450944 --i-own-this-file --expect-fs-uuid 0b1c2d3e-4f50-4a61-8b72-93a4b5c6d7e8 --confirm-quiet-window
iobench calibrate --target /var/lib/iobench/target.img --work-dir /var/lib/iobench/work --profile standard --confirm-quiet-window --protect-cgroup game-server.slice --abort-psi 40
```

With a few GiB of free space the cliff is unreachable, so read caps come out `unproven: device_smaller_than_cache_margin` (reads are cheap there); the write cap is what matters and is `proven` by
the time axis when the observation run is long enough. If the first seconds are a provider burst, the fill re-plans from its own measured rate and says `fill_replanned`.

## 8b. debian-install-v2 (installer profile)

The installer owns the throwaway partition lifecycle. iobench needs a raw target **and** an existing filesystem for the work dir (open item OI-1 in the SPEC: two partitions, or a
calibrate-then-mkfs sequence). `--installer` only waives the live-host refusals; every target guard still applies.

```bash
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0007
iobench calibrate --target /dev/vda7 --work-dir /mnt/iobench-work --profile quick --installer
iobench calibrate --target /dev/vda7 --work-dir /mnt/iobench-work --profile veryquick --installer
```

The second run is the 3-minute profile: a binding and a plausibility verdict, no tuning values. A partition that is wiped, formatted or deleted afterwards does not invalidate the result: `check` finds the disk by its serial / wwn / model and size (`target_changed` is the only trace), and `ceilings add` keeps joining the same history. Replace or resize the disk and `check` says `stale` (exit 3).

## 9. Dry run and planning

```bash
iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile standard --dry-run
```

prints the stage plan, each stage's allotted seconds, the exact fio argv and the written-byte bound, and writes nothing.

A few more refusals you may meet (each names its condition and a remedy; all exit 2):

* `container_pid_namespace` / `mount_namespace_mismatch` / `namespace_unprovable`: iobench is not running in the host's initial PID/mount namespace (or cannot prove it): "run on the host, not in a container".
* `probe_failed`: `blkid`, `wipefs`, `docker ps`, `systemctl` or `fio --version` failed or is not installed (not "found nothing"); fix the tool (binary on PATH, run as root) and rerun. `unit_not_found`: a `--production-unit` that does not exist; `production_pattern_invalid`: a `--production-pattern` that is not a regular expression.
* `workdir_disk_unknown` (`matrix` only): the work dir is on a filesystem with no single block device behind it (overlay, multi-device, network); the matrix needs the device's queue knobs and `/proc/diskstats` row. Use a directory on ext4/xfs on a partition.
* Exit 8 (`aborted`) also when the protected cgroup's `io.pressure` becomes unreadable during the run (the cgroup vanished): a run that cannot be protected does not continue; the knobs are restored.
* `abort_psi_invalid` (`--abort-psi` must be a finite percentage in (0, 100]), `protect_cgroup_unreadable` (no `io.pressure` for that cgroup), `abort_guard_incomplete` (`--installer` with one guard flag), `stale_abort_file`
  (remove `/run/iobench/abort` yourself), `orphan_fio` (an fio from a dead run still runs), `journal_corrupt` (inspect `/run/iobench/restore.json`).
* `holder_active` (an md/dm holder), `path_colon`, `exists_without_sidecar` (the sidecar must match the file's inode, size and filesystem UUID), `dry_run_rate_other_target` / `dry_run_rate_unusable`
  (`--dry-run` plans only with `--assume-r-est` or the current calibration of the same target).
* `fio_option_unsupported`: the installed fio does not document `exclusive_open` (device jobs are opened with `--exclusive_open=1`, so the kernel refuses a mounted, swap-active or claimed device; iobench never runs a device job without it; probed at start with `fio --cmdhelp=exclusive_open`). A device that is claimed at marker time is `identity_changed` (exit 2).
* A dry run is a plan, not an acceptance: its output says `preflight: not_run`; the same invocation without `--dry-run` may still be refused.
* `check --section writeback --calibration` is a usage error (a calibration carries no writeback); `check` exit 5 also covers `damaged_proposals` and a `schema_minor` newer than the installed iobench.

## Repository gate

The registered Run-Gate configuration keeps the R2 judgment in Assay's
native lane so Run-Gate can apply its worker policy, record the selection,
and keep Assay's resumable state outside an ephemeral worktree. Run the
registered lanes from the IOBench project directory:

```bash
run-gate r0-r1
run-gate r2
```

The R2 lane opts into automatic worker selection. Assay's
`[lanes.iobench.judge.mutation].jobs_max = 4` is its ceiling; the former
`jobs = 1` setting was the legacy alias for that ceiling, so it limited every
run to one worker. Run-Gate now reads the ceiling and passes the selected
count. With no candidate history it uses a conservative 1 GiB per candidate;
without Assay's supported btrfs cheap-clone workspace, auto remains at one.
Use `run-gate r2 --jobs 2` to request a specific parallel count when the
workspace supports it. A request above the ceiling or without the required
workspace fails before mutation candidates start.

<!-- run-gate-config -->
```toml
schema_version = 1

[environments.tester-unified]
mode = "ephemeral"
image = "tester-unified:local"

[lanes.r2]
kind = "assay"
assay_lane = "iobench"
environment = "tester-unified"
jobs = "auto"
resource_observer = "cgprofile"

[lanes.r2.resources]
cpus = "3"
memory = "1536m"
pids = 8192
```

Run-Gate writes the Assay verdict and progress to
`.assay/verdict-iobench.json` and `.assay/progress-iobench.jsonl`. Its
resumable mutation state is stored under the repository's ignored
`.run-gate/assay-state/` area; rerun the same lane on the same tree after a
budget stop. The former command lane used `.assay/mutation-state-r2` and
`.assay/progress-r2.jsonl`; those files are not imported, so an in-progress
campaign from that configuration must be restarted under the native lane.

## 10. What a refusal looks like

`error: refused: /dev/vda4 is swap-active (swapon); no iobench marker. iobench target init needs --i-own-this-device with --expect-size and --expect-partuuid.` (exit 2).
`error: refused: production containers are running (3); rerun with --confirm-quiet-window in an agreed window. Units not named with --production-unit are not detected.` (exit 2).
`error: refused: live-host runs need --protect-cgroup and --abort-psi.` (exit 2).
