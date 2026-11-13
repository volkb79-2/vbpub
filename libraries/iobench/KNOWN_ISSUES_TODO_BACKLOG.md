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
by default. The operator may acknowledge that layout for a dry-run or study
invocation using a value derived from the current hostname, target disk, root
filesystem device(s), and target PARTUUID. This exception is separately
recorded with observed system mounts and sibling swap devices; it does not
relax target identity, mount, swap, holder, exclusive-claim, or whole-disk
guards. The registered fake-sysfs lane covers these branches, but this change
has no study-host access and makes no claim that the live namespace and device
layout have accepted the guard.

The command-by-command live probe is in
[`studies/ibx/ACCEPTANCE.md`](studies/ibx/ACCEPTANCE.md). The saved fresh-install
layout puts the dedicated raw partition on the disk that also holds the running
system and swap. The live acceptance now verifies both the byte-identical
default refusal and a passing dry-run with the exact derived acknowledgement;
no live raw write is part of acceptance.

**Close-when:** on the approved study host, read-only acceptance confirms the
dedicated partition identity and expected live sysfs/mount facts; the correct
acknowledgement passes `run --dry-run`; changed size/PARTUUID, a mounted or
swap-active target, a holder, a whole disk, and a wrong host/disk/root/partition
acknowledgement refuse before any write or discard; the acceptance record names
host-independent facts without publishing a hostname or address.

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
transient `system.slice` service outside the benchmark cgroup.

**Close when.** Fake proc/clock/latency coverage proves each stop and advisory
path; the registered R0–R1 lane passes on the final head.
