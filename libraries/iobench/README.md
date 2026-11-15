# iobench

A cache-proof, time-resolved disk benchmark for vbpub hosts. It measures what a disk (especially a hoster's VM disk) *sustains*, not what its caches
absorb, and turns the measurements into **proposals with the rule that produced each and a proof status** (`proven`, `unproven` with reasons, or `absent`): the io.cost model and QoS,
the writeback and queue settings (`dirty_bytes`, `dirty_background_bytes`, `nr_requests`, `max_sectors_kb`), and the dev IO caps.

The measurement core is Python 3 standard library plus `fio` >= 3.30 for its latency fields; startup also checks that fio documents
`--allow_mounted_write`. Raw-device write and discard stages are refused because fio and `blkdiscard` cannot use the same held kernel
`O_EXCL` descriptor as iobench. The command line adopts `cli-extended` (so the CLI needs that library
on `PYTHONPATH`; the core does not).
See [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md) for why it works this way, [docs/CONSUMERS.md](docs/CONSUMERS.md) for how to use it, and [docs/SPEC.md](docs/SPEC.md) for the normative
requirements and the result schema.

## What it does

| Capability | One line | Spec |
|---|---|---|
| Calibration (once per host or storage class; `veryquick` 3, `quick` 12, `standard` 60, `thorough` 75 min; `veryquick` values are unproven) | cold-region probe (primary, direct) cross-checked by a latency-mode split, working-set ladder, time-axis proof for writes, fresh vs rewritten blocks, ceiling, cost-shape grid, latency-vs-load curve; the fill writes each planned byte once and other byte-bounded jobs stop at `io_size` ([why](docs/DESIGN-GUIDE.md#52-fio-byte-limits-and-runtime-ceilings)); veryquick refuses to replace a fuller result in the same state directory ([why](docs/DESIGN-GUIDE.md#72-the-veryquick-profile)) | `IOB-CAL`, `IOB-CCH`, `IOB-SHP`, `IOB-LAT` |
| Cache and burst observations | calibration appends disk-scoped capability observations; `iobench capability --kind raw|file|probe` filters them and the default summary lists each kind separately; `check --json` reports the same per-kind summary; schema 1 minor 5 records cache diagnostics, incomplete runs and per-rung Little's-law latency evidence; none supply io.cost values ([why](docs/DESIGN-GUIDE.md#73-cache-and-burst-observations)) | `IOB-CAP`, `IOB-CCH-09` |
| Matrix iteration (6 cells: typical 11, worst case 15 min; one knob A/B typical 4 min) | reuses the calibration; interference cells with a sync-latency probe under buffered writeback load, the read cost of small requests, a noise floor | `IOB-MAT`, `IOB-SWP`, `IOB-RDC` |
| Proof model | every proposed value is `proven`, `unproven` (with reasons) or `absent`; `check` exit 0 only if every requested proposal is proven, exit 7 if valid but some are not | `IOB-STA`, `IOB-CHK` |
| Plausibility | PLA-06 checks Little's law with total fio latency and retains each rung's IOPS, QD and mean completion/submission/total latency; failures keep the cap effects of every failed dependent rung. Veryquick's cold class is never used to corroborate its 20x–100x PLA-19 band; that band remains inconclusive and only >100x fails. Sequential read/write ratio thresholds remain provisional ([why](docs/DESIGN-GUIDE.md#72-the-veryquick-profile)); never keys on the `rotational` flag | `IOB-PLA`, `IOB-REF` |
| Proposals | `proposals.writeback`, `proposals.caps`, `proposals.iocost_model`, `proposals.iocost_qos`, each value with `rule`, `inputs`, `status` | `IOB-DER` |
| Ceiling history | append-only disk history; raw rows supply proposal values, while file/probe rows can confirm stability only; weak disks require the remembered device name and model, and a known stored machine-id must still match; a chroot record with no stored id stays unbound after first boot ([why](docs/DESIGN-GUIDE.md#71-the-key-is-the-disk-not-the-partition-host-tuning-review-b2-2026-11-05)) | `IOB-CEI` |
| Safety | a raw-device target can be initialized for its marker, but RealHost refuses raw-device write and discard stages until a writer can use a held kernel `O_EXCL` descriptor; the marker's `O_EXCL` open protects only marker creation; fio's `--allow_mounted_write=0` is an additional mounted-device guard, not a process-holder claim; file targets are preallocated files on existing filesystems; refuses mounted, swap, signature-bearing or root-disk targets; refuses multi-device btrfs work dirs and targets; Host binds file writers and discards to the authorised extent (plain `run`/`spawn` cannot reach a target); never touches any cgroup `io.max`; restores every changed runtime knob on every exit; never creates or mounts a filesystem | `IOB-TGT`, `IOB-SAF`, `IOB-ARC-05` |
| IBX ladder error study | a separate, root-run fio controller measures rung spread and bias under a 500 GB decimal write cap; a shared-system-disk raw partition refuses by default and needs the operator's consent flag plus a per-run drift-check token whose risk context is recorded; `run --dry-run` validates without study-output or target writes or timer changes and requires an initial liveness probe; `rehearse` runs planner-scaled real fio across scheduled job shapes, using `--test-scale` only with an output-contained regular file under `/tmp`; absent kernel IO-control files mean no setting, while present unreadable or active controls refuse; I/O PSI is advisory and independent liveness checks protect the host ([why](docs/DESIGN-GUIDE.md#ibx-ladder-error-study)) | [study-day procedure](studies/ibx/README.md); [host acceptance](studies/ibx/ACCEPTANCE.md); [consumer example](docs/CONSUMERS.md#ibx-host-study) |

Device target identity uses a PARTUUID or a usable whole-disk serial. For virtio-blk, iobench reads that serial from `/sys/block/<disk>/serial` ([design](docs/DESIGN-GUIDE.md#71-the-key-is-the-disk-not-the-partition-host-tuning-review-b2-2026-11-05)).

Both device and file targets must be at least **1 GiB + 1 MiB**: 1 GiB usable for calibration plus the reserved marker tail. A run's work dir must already exist, be empty, and have at least `max(4 GiB, 3 × planned cell volume)` free after target initialization. See the [guard design](docs/DESIGN-GUIDE.md#72-the-veryquick-profile) and [consumer setup](docs/CONSUMERS.md#2-calibrate-once-per-host-or-storage-class).

Raw-device marker initialization is available, but RealHost refuses raw-device
write and discard stages: fio opens the path itself and `blkdiscard` does not
accept iobench's held descriptor. `--allow_mounted_write=0` remains an additional
mounted-device guard, but it does not claim exclusion from ordinary process
openers. Use a preallocated file on an existing filesystem for benchmark runs
until a writer can perform I/O through the same held `O_EXCL` descriptor. For
install-time measurement used by iogovmon, raw-partition measurement is currently
unavailable. A file on btrfs is supported only when its filesystem resolves to
one backing disk; multi-device btrfs is refused for both file targets and work
dirs, including when a work-dir path equals the btrfs mount point. File results include the
filesystem, extent mapping and copy-on-write layers, so they do not mean the
same thing as a raw-partition result ([target choice and rationale](docs/DESIGN-GUIDE.md#72-the-veryquick-profile)).
`target init --file` preallocates the file, but only the fill and later jobs write real data. The rest can remain
filesystem-allocated but unwritten; reads of those regions may be served as zeros without device I/O.
`target init` and `target show` therefore report `written_bytes` and `unwritten_bytes` within `usable_bytes` when the filesystem type is
ext4, XFS, or single-device Btrfs. The value is logical data-extent coverage, not physical allocation, nonzero content, or a count of fio-written
bytes. The query uses about two metadata seeks per reported extent, so its cost follows extent count. Other filesystem types report a `null`
number and an explicit `written_bytes_status`; raw devices and dry runs also have no measurable file extent map.

A saturating `standard` run can trip the io-full PSI stop threshold above 25
within its first minute on an otherwise idle host: stalled fio tasks from the
benchmark itself can cause it. Run install-time calibration before workloads
start, or have the operator explicitly choose a relaxed `--abort-psi` threshold
for that run ([guard details](docs/CONSUMERS.md#2-calibrate-once-per-host-or-storage-class)).

## Quick use

```bash
# prepare a file target on an existing disk filesystem
FS_UUID="$(findmnt -n -o UUID -T /var/lib/iobench)"
iobench target init --file /var/lib/iobench/target.img --size 6442450944 --i-own-this-file --expect-fs-uuid "$FS_UUID" --confirm-quiet-window

# calibrate once per host / storage class
install -d -m 0750 /var/lib/iobench/work
iobench calibrate --target /var/lib/iobench/target.img --work-dir /var/lib/iobench/work --profile standard --confirm-quiet-window --protect-cgroup game-server.slice --abort-psi 40

# ask whether the result is usable, and read the proposals
iobench check --json; echo "exit=$?"
iobench show --proposals --json
iobench capability --json
```

## Layout

```
libraries/iobench/
  README.md  docs/{DESIGN-GUIDE,CONSUMERS,SPEC,CLI-SPEC}.md  docs/reports/IOBENCH-R2-R1002-2026-11-02.md
  docs/{cli-surface.json,cli-review.toml,cli-review-findings.toml}
  pyproject.toml  cmru.toml  run-gate.toml  assay.toml  .coveragerc
  src/iobench/   pure core (planning, reduction, derivation, schema) behind an injected Host; cli.py is the thin shell
  tests/         fake-fio and fake-sysfs scenarios, recorded-evidence replays, controlled-wrong plausibility cases
```

## Status

Implemented (IOB-03 P3, reviewed through wave 2026-10-27) and partially accepted live on 2026-11-09: the r1002 Debian 13 QEMU/TCG guest verified target refusals, fio 3.39 compatibility, file extents on ext4/XFS/btrfs, and veryquick run/restore mechanics. TCG performance numbers and cache conclusions are not evidence about disk performance. IOB-ACC-01 still owes the mounted-filesystem raw-boundary case, `systemd-run --wait`, a second SIGTERM during file `fallocate -p`, a real 150 s fresh observation, cross-direction/plausibility checks, and a disk with a known cache. The parent-death cleanup guarantee covers asynchronously spawned benchmark commands through `_ProcHandle` (`Host.spawn`/`Host.write_range`), and has a no-device forked-process regression; a live re-probe is still owed. Synchronous `Host.run` clients are outside that guarantee: killing a `systemd-run --wait` client leaves its unit running until explicit stop or `RuntimeMaxSec`. See the [design rationale](docs/DESIGN-GUIDE.md#9-safety-design) and [adoption notes](docs/CONSUMERS.md#2-calibrate-once-per-host-or-storage-class). The first cmru release remains open (IOB-REL-01; wheel plus sha256). The CLI review cases in `docs/cli-review.toml` record real invocations and
the outcomes observed on the fake host; an independent semantic review still has to confirm their rationales. The decisions behind it are IOB-D-001..014 and the rulings
R-1..R-10 in the DESIGN-GUIDE. Nothing here is wired into `mdt-cockpit` or `debian-install-v2` yet (migration phases M1-M4 in the DESIGN-GUIDE).

The historical R2 mutation campaign cited in IOB-R2-01 is documented in the
[2026-11-02 campaign report](docs/reports/IOBENCH-R2-R1002-2026-11-02.md) and
the [raw evidence summary](docs/reports/IOBENCH-R2-R1002-EVIDENCE-SUMMARY-2026-11-13.md).

The registered repository gate runs R0–R1 and native Assay R2 through
Run-Gate. Its R2 lane opts into `jobs = "auto"` with an Assay ceiling of
four workers; it runs serially when the prepared workspace does not support
Assay's btrfs cheap-clone requirement. See the
[gate consumer guide](docs/CONSUMERS.md#repository-gate) for how to run it and
the [design guide](docs/DESIGN-GUIDE.md#registered-repository-gate) for why
the R2 lane uses native Assay dispatch. Campaigns started with the former
command lane do not resume after this storage change; see the consumer guide
for the state paths.
