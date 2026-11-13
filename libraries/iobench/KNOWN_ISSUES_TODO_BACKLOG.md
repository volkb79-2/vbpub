# iobench known issues and TODO backlog

Entries IOB-BL-01..38 are retained in [BACKLOG.md](BACKLOG.md). New entries continue here.

## IOB-BL-40 — 2026-11-08 fill-rate figures are invalid (FIXED, IB-FILL)

The fill MB/s values in the 2026-11-08 r1002 and controller reports (`ib-vq-REPORT.md`, `ib-vq-rerun-REPORT.md`, `ib-vq-controller-REPORT.md`) are not valid device or filesystem measurements. In 23 of 23 runs, `fill_bps / R_est` was `10/41 = 0.244`: `devio.py` always sent `--time_based`, so fio repeated the `io_size`-bounded fill until `--runtime` instead of completing the planned chunk once. The same argv mistake affected every other `io_size`-bounded write job and `rearm-prep`; dependent PLA-09 comparisons from those runs are invalid too.

The builder now emits `--time_based` only when `io_size` is absent. FakeFio repeats an `io_size` job when the actual argv contains `--time_based`, and the regression replays the `10/41` signature before proving a corrected 10 MiB chunk writes 10 MiB in about 10 seconds at 1 MiB/s. README, DESIGN-GUIDE, CONSUMERS and SPEC explain the behavior and mark the historical rates invalid.

**Close-when:** the regression reproduces `10/41` from legacy argv and rejects that signature from the generated bounded-fill argv; reported bytes match `io_size` within tolerance and elapsed time is about `io_size / rate`; the project R0–R1 gate passes on the clean final head; the current user docs label the 2026-11-08 figures invalid.

## IOB-BL-41 — Live acceptance of the IBX raw-partition safety boundary

The IBX study's raw target path now checks a manually acknowledged exact path,
size, and PARTUUID; sysfs partition identity; holders; both the caller and PID 1
mount namespaces; swap; active exclusive claims; and root/system filesystems
before any discard. A dedicated partition on the running system disk refuses
by default. The operator consents to that layout by pairing
`--allow-system-disk-partition` with the per-run `--system-disk-ack` drift-check
token derived from the current hostname, target disk, root filesystem device(s),
and target PARTUUID. This exception is separately
recorded with observed system mounts and sibling swap devices; it does not
relax target identity, mount, swap, holder, exclusive-claim, or whole-disk
guards. The registered fake-sysfs lane covers these branches, but this change
has no study-host access and makes no claim that the live namespace and device
layout have accepted the guard.

The command-by-command live probe is in
[`studies/ibx/ACCEPTANCE.md`](studies/ibx/ACCEPTANCE.md). The study-day sequence
in [`studies/ibx/README.md`](studies/ibx/README.md) also requires the approved
`IBX_EXPECTED_HOST` before each driver run and rechecks host plus target identity
before each direct filesystem operation. The saved fresh-install
layout puts the dedicated raw partition on the disk that also holds the running
system and swap. The live acceptance now verifies both the byte-identical
default refusal and a passing dry-run with the exact derived acknowledgement;
no live raw write is part of acceptance.

**Close-when:** on the approved study host, read-only acceptance confirms the
dedicated partition identity and expected live sysfs/mount facts; the correct
acknowledgement passes `run --dry-run`; changed size/PARTUUID, a mounted or
swap-active target, a holder, a whole disk, a wrong expected host, or a wrong
host/disk/root/partition acknowledgement refuse before any write or discard;
the acceptance record names host-independent facts without publishing a
hostname or address.

## IOB-BL-42 — Live acceptance of IBX btrfs membership and fio logs

IBX now resolves btrfs backing members by filesystem UUID in sysfs and creates
`OUT/fio-logs` before the real fio process starts. Fake sysfs and fake fio
exercise these paths, but no actual btrfs file run or fio log-open has been
performed in this change.

The guarded Btrfs and log probe is in
[`studies/ibx/ACCEPTANCE.md`](studies/ibx/ACCEPTANCE.md); it is a procedure,
not live acceptance evidence.

**Close-when:** on the approved study host, a single-device btrfs file run
resolves to the partition reported by sysfs and produces the expected fio JSON
and bandwidth/IOPS logs under `OUT/fio-logs`; a mismatched or multidevice btrfs
member set refuses before fio; the evidence records the observed filesystem
UUID/member mapping and fio version without a hostname or address.

## IOB-BL-43 — tests must scope process-wide `os.*` syscall fakes

**Status:** OPEN (filed 2026-11-12); the observed hazards are fixed in the
pytest 9.2 audit branch. Pytest 9.2 calls `os.fstat` during capture and
teardown. The raw-target tests now scope `os.open` and `os.close` to the
measured target or synthetic descriptor and delegate unrelated calls to the
saved real functions.

**Contract.** Future process-wide `os.*`, `builtins.open`, or `io.open`
patches must match the fd/path under test and preserve the original
O_EXCL/busy/close-refusal oracle.

**Close when.** No non-delegating exact-symbol test patch remains and the
registered R0/R1 lane passes on a clean final commit.

## IOB-BL-44 — IBX dry-run must not require timer changes

**Status:** FIXED in this package. `run --dry-run` used to assert the fstrim,
apt-daily, and governance timer quiet window before it knew that the invocation
was read-only. The timer check now runs only for live executions; dry-run still
performs target, identity, monitor-threshold, free-space, schedule, fio
rendering, and the initial host-liveness probe without creating output or
changing the target.

**Close when.** Fake-host coverage proves an active timer does not block dry-run
and live run still checks it; the registered R0–R1 lane passes on the final
head.

## IOB-BL-45 — IBX I/O PSI stop rule is self-triggered by fio

**Status:** FIXED in this package. The former I/O PSI threshold could reach
100% from the saturating benchmark cell itself. IBX now records I/O PSI as
advisory and protects host responsiveness with memory PSI `full avg10`,
`MemAvailable`, free-space floors, hard byte/time caps, and a configurable
consecutive-sample host-liveness stop. A successful initial probe is required
before target I/O; later probes use a recorded configurable interval (10 s by
default). Each bounded root-filesystem fsync and SSH/cockpit check runs in a
transient `system.slice` service outside the benchmark cgroup. The cockpit
runner liveness check is `docker exec` against the configured container name;
it does not require a matching systemd unit.

**Close when.** Fake proc/clock/latency coverage proves each stop and advisory
path; the registered R0–R1 lane passes on the final head.

## IOB-BL-46 — live acceptance catches host-layout assumptions fixtures missed

**Status: OPEN; guards and regression tests added in `ibx-io-snapshot-fix` and
its round-two fix.**
The 2026-11-13 read-only acceptance reached the IO-control snapshot and showed
that its fixture had hidden real-kernel details: root has no `io.max`,
`io.weight`, or `io.latency`; `system.slice/io.max` exists but is empty;
`system.slice/io.weight` is `default 100`; and this kernel has no
`io.latency` file anywhere (`CONFIG_BLK_CGROUP_IOLATENCY` is unset). The
kernel-managed `ctrl=auto model=linear` io.cost model is inactive. The fixture
now records observed present, empty, and absent states separately and cites
the evidence files by SHA-256. Exact process leaf membership was not saved;
the fixture follows the observed `system.slice` transient slice, with a
separate synthetic leaf test for the deeper ancestry case.

The snapshot treats missing controls as no setting, refuses existing but
unreadable or malformed controls and active limits that can affect the study,
and compares the same observed values before and after a cell-set. It checks
`io.max`, `io.weight`, and `io.bfq.weight` along the benchmark path. The
selected scheduler is read from the study disk's `/sys/block/<disk>/queue/scheduler`
and recorded in the manifest and IO snapshot. `io.latency` can throttle peers,
so direct peers at each path level are checked for active targets on every
snapshot. Peer `io.bfq.weight` is checked only when the target scheduler is
`bfq`; any known non-BFQ scheduler, including `none` and `mq-deadline`, is
inert. If the scheduler cannot
be read or parsed, the manifest reports it as unknown and peer BFQ weights are
checked conservatively. Peer `io.weight` is not judged. Peer membership is
excluded from before/after equality because systemd units and containers
change during a long study. The host's active
`dev.slice/io.max` is a sibling ceiling and is not inherited by the study
process. The saved host snapshot records `[none] mq-deadline` for the study
disk and absent BFQ controller files in all 13 captured cgroups at depth two.
Those observations make peer BFQ weights inert on that layout; the fixture
does not claim that a BFQ kernel or peer BFQ files are absent on other hosts.

The captured layout is checked in at
[`tests/fixtures/ibx-real-cgroup-layout.json`](tests/fixtures/ibx-real-cgroup-layout.json)
with evidence provenance; tests materialize it under `tmp_path`. Keep that
fixture tied to a reviewed live evidence record when extending the driver, so
synthetic trees do not reintroduce files the host kernel omits. The acceptance
runbook also had `expect_refusal` restore `set -e`, aborting at its first
unexpected result. It now records every check and prints one final summary
before returning non-zero when any check fails. Review also found that a
partition's sysfs directory does not own the disk `queue/`; the run2 acceptance
did not reach that probe. The driver now resolves the partition's parent disk
before reading `queue/discard_max_bytes`; r1002 live confirmation remains open.

Round two found and fixed the next host-layout assumption before another host
run: the cockpit runner is a Docker container and has no
`mdt-cockpit-runner.service` unit. The liveness probe now uses `docker exec`
only, keeps the root-filesystem fsync and SSH banner checks, and configures the
SSH host/port and runner name with defaults `127.0.0.1:22` and
`mdt-cockpit-runner`. The read-only check verified sshd listening on
`0.0.0.0:22`. Missing pressure files now fail as path-specific `StudyError`s.
An optional `--fsync-cell` now checks its Docker cgroup scope, loaded
`CGROUP_PARENT_DEV_BACKGROUND` slice, wrapper, and image during dry-run; a
direct SSH root session without the required Docker cgroup scope refuses
before study work. The round-two report inventories every driver invocation
and `/proc`, `/sys`, and `/dev` assumption with its verification status.

**Fixture guard (proposed):** keep a cgroup layout captured from a real host in
`tests/fixtures/` with evidence provenance and explicit present/empty/absent
states. Every host-liveness, monitoring, or optional collector assumption that
was not verified on that host must either be exercised by the dry-run or
refuse there with a clear `StudyError`, never surface first during a live cell.

**Close when.** The registered R0–R1 lane passes on the final reviewed head,
and the next approved read-only acceptance confirms the acknowledged dry-run
passes on the real cgroup layout while every active-limit and target-safety
refusal still fires. Run 2 verified target identity, partition, mount, swap,
holder, system-disk acknowledgement, root/system.slice control values, the
cost files, fio 3.39 and `allow_mounted_write`, and the captured timer state.
Round two additionally verified the cockpit container vs. systemd unit, sshd
listener, pressure-file presence, and root BFQ-weight absence. It did not reach
diskstats values, the trim sysfs read, file-target backing-device checks, or
the live systemd timer guard. The package report records every driver host
assumption as VERIFIED-ON-HOST or UNVERIFIED, with dry-run behavior for the
latter.

Round three corrected peer BFQ judging to use the target disk scheduler:
`bfq` and unknown scheduler state check active peer weights; any known
non-BFQ scheduler, including `none` and `mq-deadline`, treats them as inert.
The scheduler and its availability are
included in the run manifest and before/after snapshot. The new scheduler
fixture uses the recorded `none` layout and evidence digest, with synthetic
`bfq`, `mq-deadline`, missing, malformed, and no-BFQ-controller cases on the
same sysfs tree shape. The new read-only evidence digest also confirms the
captured 13-cgroup BFQ-file absence; no new host was accessed by this fix.

The 2026-11-13 run3 acceptance then passed 6/6 checks, including the
acknowledged dry-run, successful initial liveness probe, and IO-control
snapshot. It also verified the raw partition's discard and diskstats path,
fio 3.39's command-line JSON option and rendered 1 MiB writer, live quiet-window
timer checks, and persistent-unit timer restoration. These facts are marked
`VERIFIED-ON-HOST` in the run3 and follow-up reports. The complete post-t0 study
is not included in this verification; see IOB-BL-47.

## IOB-BL-47 — Complete IBX study live acceptance after the fio cap repair

**Status: OPEN.** Run3 stopped after `raw-t0 standard seq1m-write` because the
driver rejected fio's whole-block write count. The cap repair, accounting
retry, analysis, discard, probe, sampler, and file-target paths have fixture
and fake-host coverage in the registered iobench lane, and the rendered cap
classes have separate real fio 3.39 regular-file evidence. No full study was
run during this code fix.

Run3's raw fio JSON was not retained. The report records 64,000,884,736 bytes
written against the old 64,000,000,000-byte reservation. The repaired driver
cannot treat that rejected cell as a valid measurement, and the complete
remaining base schedule plus its already charged attempt does not fit under
the same 500 GB meter. A full rerun therefore needs a new authorized output
budget; the driver accounts a parseable failed attempt at its observed bytes
and refuses before a later cell if the complete remaining schedule no longer
fits.

The unexecuted live schedule includes the remaining raw-t0 cells, part (c)'s
interleaved rewrite/idle/discard/recovery probes and analysis, later raw slots
and conditional t+6h rung choice, ext4 and Btrfs format/mount/preallocate and
file legs, Btrfs output verifier, final target wipe, and the final rung-choice
analysis over the complete dataset. Their failure modes are guarded by the
driver, but fixture success does not prove host-specific commands, timings,
filesystem transitions, or output shape on the approved study host.

**Close when.** A new approved study lease has enough time for the full schedule
from t0 and an authorized fresh output budget. The run finishes the base
schedule without a driver stop, verifies the Btrfs file leg and monitor logs,
produces rung-choice analysis, and restores timers and the target to the
authorized final state. Preserve the full evidence and update the host-
assumption table; do not rerun a destructive step outside that lease.
