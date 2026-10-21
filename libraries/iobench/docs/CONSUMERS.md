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
iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile standard --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40
```

* Hard budgets: `quick` 12 min, `standard` 60 min, `thorough` 75 min. Skipped stages are recorded, never shortened silently.
* A live host REQUIRES `--confirm-quiet-window`, `--protect-cgroup` and `--abort-psi` (no defaults; the run refuses without them). Unlisted systemd units are not detected: you are the guard.
* Output: `/var/lib/iobench/calibration.json`. Re-run on an identity change (`check` exit 3) or when `check` warns `recalibrate_recommended`.
* `quick` reports every cache-dependent value as `unproven: quick_profile` (honest, not absent).

## 3. Matrix iteration (the fast path)

```bash
iobench matrix --target /dev/vda7 --work-dir /bench/iobench-work --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40
iobench matrix --target /dev/vda7 --work-dir /bench/iobench-work --knob max_sectors_kb --candidates 64,128,256 --repeat --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40
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
`unreadable`, `calibration_missing`, `damaged_proposals`); `6` only an aborted or failed record exists; `7` identity current but at least one requested proposal is `unproven` or `absent`.
Also from `iobench`: `1` runtime failure, `2` refusal or usage, `8` run aborted (restored), `9` restore or `io.max` verification failure.

```json
{"state": "current_unproven", "reasons": [], "warnings": ["scheduler_changed"], "limits": ["bucket_exclusion_s<=240"], "identity_strength": "weak",
 "sections": {"caps": {"proven": 0, "unproven": [{"key": "RIOPS", "reasons": ["cache_model_undetermined"]}], "absent": []}},
 "result": "/var/lib/iobench/result.json", "calibration_id": "cal-20261021T020000Z-7b2d0c1e"}
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
| `worst_from_single_run` | worst/best plateaus come from this run only (no ceiling history yet) |
| `metering_undetermined` / `operator_override` | S6 did not pass both metering tests / you typed `--metering` |
| `shape_fit_poor` | the per-IO + per-byte linear fit is poor |
| `noise_floor_high` / `target_missed` / `drain_below_ladder` / `formula_only` / `cross_disk` / `identity_weak` | sweep noise > 4 / the chosen settings missed the sync-latency target / drain rate disagrees with the ladder / sweep skipped / work dir on another disk / disk has no serial |

### 5.2 Absent reasons

`stage_skipped`, `stage_failed`, `basis_unsteady`, `basis_missing`, `bps_basis_missing`, `no_calibration`.

### 5.3 Cache state, metering, fit, limits

* `cache_state` (reads): `cold_measured` (the earliest-written region read as cache-free: proven), `rate_cap` (the cold-region probe itself is flat at a provider cap: proven; a flat line elsewhere is only a recorded fact), `no_cache_observed` (the cold region runs as fast as the warm one, no cliff, unimodal: **unproven**, `cache_not_excluded`, because it cannot be told from a cache at least as large as the written extent), `cold_hit_like` (hit-like with a cliff, on a file target, or a cold rate below the warm reference: unproven), `undetermined`. Writes have no cache state: they are proven by the time axis (>= 120 s after the knee, bytes after >= 2x the burst).
* `metering`: `byte`, `iops`, `mixed`, `undetermined`; E1 `fit` (informational): `good`, `poor`, `insufficient`, `plateau`.
* `limits`: `quick-profile`, `limited-by-budget`, `short-observation`, `single-run`, `stage_skipped:<Sn>`, `filesystem_layer` (file target: extent mapping and journal overhead are inside every figure), `fresh_semantics_filesystem` (file target: the "fresh" write is `fresh_fs_alloc` and counts toward the cap only on a `discard` mount), `bucket_exclusion_s<=<N>` (the longest observation: bursts longer than that are not excluded).
* Check ids in `quality.checks[]`: IOB-PLA-01 tail flat, IOB-PLA-02 working-set rule, IOB-PLA-03 cached-tier factor, IOB-PLA-04 mean vs steady, IOB-PLA-05 flat-line, IOB-PLA-06 Little's law, IOB-PLA-07 bytes consistency, IOB-PLA-08 read-only-written, IOB-PLA-09 fill vs sequential, IOB-PLA-10 cache state, IOB-PLA-11 shape fit, IOB-PLA-12 noise floor, IOB-PLA-13 fresh vs rewrite, IOB-PLA-14 data integrity, IOB-PLA-15 identity stable, IOB-PLA-16 extent integrity, IOB-PLA-17 outside history, IOB-PLA-18 fresh write bytes/s (info; also records whether a discard re-arms the fresh-block penalty, hypothesis). A `fail` degrades only the groups it lists; -14, -15 and -16 fail the whole run.

### 5.4 Basis, check states and warnings

* `basis` (how a value was reached): `measured`, `confirmed-by-sweep`, `ratio-inferred`, `formula-only` (the sweep was skipped, so the formula was never confirmed), `single-run`.
* `check` states and their exit codes: `current` 0, `current_unproven` 7, `stale` 3, `absent` 4, `unreadable` 5, `aborted_only` 6 (only an aborted or failed record exists).
* `check` warnings (advisory, never change the exit code): `scheduler_changed`, `ram_changed`, `kernel_changed`, `root_source_changed`, `workdir_fs_changed` (the host drifted since the run), `older_than_180_days`, `recalibrate_recommended` (the ceiling history moved away from the calibrated plateau), `last_run_aborted` (a newer run was aborted; the good result is kept).

## 6. mdt-cockpit wizard (MDT-HS-05 and the io.cost wizard, D-IOC-4)

* The wizard **asks whether to run the benchmark, default yes (a configurable key)**; it never starts one silently. It calls `iobench calibrate` if `iobench check --calibration` reports no current calibration, then `iobench matrix`.
* Precedence for each key: **benchmark (a `proven` value, or an `unproven` one the operator explicitly accepted with its reasons shown) > the configured value > `kernel`**. An `absent` value is never applied.
* Latency source (D-IOC-3): the governor's histogram when one runs (`--governor-histogram`), otherwise `proposals.iocost_qos` (S7).
* `check.sh` uses `iobench check --json` and reports OK / MISMATCH / "not managed" / "could not read" distinctly.

## 7. Ceiling history (governor or timer probe)

```bash
iobench ceilings add --dir write --workload seq1m --bps 14680064 --duration-s 600 --source governor
iobench ceilings summary --window-days 30 --json
```

```json
{"history": "recorded", "window_days": 30, "write": {"seq1m": {"worst": 6291456, "median": 14680064, "best": 18874368, "n": 41}}, "read": {"seq1m": {"worst": 1300000000, "median": 1400000000, "best": 1400000000, "n": 12}}}
```

`worst` and `best` feed `min = 100` and `max = ceil(110 x best / worst)` (here 330). With no records the summary says `"history": "single-run"` and every dependent value is `unproven: worst_from_single_run`.
Until a probe or governor appends observations (D-011), the history is `single-run` on hosts that only ran the benchmark.

## 8. A host with no spare partition: file targets (gstammtisch-class hosts)

The wizard's default (D-IOC-4) must be able to run on production hosts that have no raw partition to give. A **file target** is a preallocated file on an existing filesystem, written once in
full and accessed with `O_DIRECT`. Its identity is (filesystem UUID, inode, size) with the marker in its last MiB and a sidecar `.iobench-target`; every writing stage re-verifies it. Refused:
tmpfs/ramfs/overlay, symlinks, hard links, too little free space, and on the root filesystem a `--size` above 10 % of the free space (`--allow-large-root-target` overrides; `--size` is required and is the hard upper bound). Free space is re-checked while the fill runs. The "fresh" write is reported as `fresh_fs_alloc` with the filesystem's `discard` mount option and enters the write cap only on a `discard` mount; iobench never runs `fstrim`. Every figure carries the `filesystem_layer` limit and is never mixed with device-kind history.

```bash
iobench target init --file /var/lib/iobench/target.img --size 6442450944 --i-own-this-file --expect-fs-uuid 0b1c2d3e-4f50-4a61-8b72-93a4b5c6d7e8 --confirm-quiet-window
iobench calibrate --target /var/lib/iobench/target.img --work-dir /var/lib/iobench/work --profile standard --confirm-quiet-window --protect-cgroup wings.slice --abort-psi 40
```

With a few GiB of free space the cliff is unreachable, so read caps come out `unproven: device_smaller_than_cache_margin` (reads are cheap there); the write cap is what matters and is `proven` by
the time axis when the observation run is long enough. If the first seconds are a provider burst, the fill re-plans from its own measured rate and says `fill_replanned`.

## 8b. debian-install-v2 (installer profile)

The installer owns the throwaway partition lifecycle. iobench needs a raw target **and** an existing filesystem for the work dir (open item OI-1 in the SPEC: two partitions, or a
calibrate-then-mkfs sequence). `--installer` only waives the live-host refusals; every target guard still applies.

```bash
iobench target init /dev/vda7 --i-own-this-device --expect-size 435853320192 --expect-partuuid 3e1f7a52-5b6d-4c1e-9a0d-7b2d0c1e0007
iobench calibrate --target /dev/vda7 --work-dir /mnt/iobench-work --profile quick --installer
```

## 9. Dry run and planning

```bash
iobench calibrate --target /dev/vda7 --work-dir /bench/iobench-work --profile standard --dry-run
```

prints the stage plan, each stage's allotted seconds, the exact fio argv and the written-byte bound, and writes nothing.

## 10. What a refusal looks like

`error: refused: /dev/vda4 is swap-active (swapon); no iobench marker. iobench target init needs --i-own-this-device with --expect-size and --expect-partuuid.` (exit 2).
`error: refused: production containers are running (3); rerun with --confirm-quiet-window in an agreed window. Units not named with --production-unit are not detected.` (exit 2).
`error: refused: live-host runs need --protect-cgroup and --abort-psi.` (exit 2).
