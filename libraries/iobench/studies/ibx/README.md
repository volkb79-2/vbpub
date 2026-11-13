# iobench ladder error study

This host-only controller reproduces the nested `veryquick`, `quick`, and
`standard` fio cell families from the approved IBX design (rev 4.1, section 5),
and the bounded schedule and analysis in sections 6.2–6.7. The controller uses Python's standard library;
fio is the only benchmark executable. It does not contain SSH code and this
change has not run against a host.

The driver also works from a flat handoff copy when `driver.py` and
`job-template.fio` stay together. Repository discovery is optional for the
normal study and analysis paths. `--fsync-cell` needs the driver under
`libraries/iobench/studies/ibx/` because its collector wrapper belongs to that
checkout; a flat copy refuses that option with a layout-specific error.

`veryquick` leaves 4 KiB sequential cells unmeasured; the table marks those
numbers unavailable and explains that the profile only has a coarse random
IOPS proxy. `quick` and `standard` run the 4 KiB sequential read and write cells.
Analysis records each run's nearest raw-standard `x_ref` and derives the
read/write bandwidth and IOPS ratios plus read/write random-to-sequential
ratios. Ratios that need the 4 KiB sequential cells are unavailable for
`veryquick`.

## Plan and limits

From the repository root:

```bash
python3 libraries/iobench/studies/ibx/driver.py plan
```

The plan is relative to `t0`: raw standard/quick/veryquick at `t0`, rewrite
study at `t0+1h`, raw veryquick/quick at `t0+3h`, then quick/veryquick at
`t0+6h`. The `t0+6h` standard set runs only when a quick/veryquick error is
within 1.5 times its tolerance boundary or the observed movement means `V`
needs a second standard point. Raw quick/veryquick and file quick/veryquick
follow by `t0+9h`. The analysis rule can select up to two extra
quick/veryquick slots if the fourth or fifth observation moves `e` by more
than 20 percent, but the current 500 GB cap cannot reserve either complete
set, so both run commands report `skipped`. A file-target standard set runs
only when its quick result is borderline, its remaining 100 GB cap fits, and
the unpaid required sets still ahead also fit;
because rev 4.1 does not give that cutoff a number, the driver defines it as
`T/1.5 ≤ e ≤ T+C`. **Study decision D-IBX-001:** this file-standard cutoff is
an explicit controller policy, not a rev 4.1 requirement; the selected cutoff
is recorded in the analysis report.

The part (c) repeat reports `skipped` when the complete first assessment does
not confirm degradation. The current 500 GB schedule also skips the part (c)
repeat and both optional raw repeats because none fits after the maximum base
schedule; incomplete first-run evidence is still an error when deciding whether
the part (c) result is complete.

The meter derives the base schedule's maximum from the block-aligned fio job
caps: 495.122866176 GB. Its hard write cap is 500 GB decimal (500,000,000,000
bytes), leaving 4.877133824 GB after the maximum base schedule. The optional
part (c) repeat costs 49.123688448 GB, and each optional raw repeat costs
40.999899136 GB, so none is reserved or run under this cap. These are the
effective sums after every writer cap is rounded down to a whole block. The
nominal rung budgets remain 15/26/100 GB; their aligned writer sums are
14.999912448/25.999986688/99.999891456 GB. Each optional file-standard set has
a nominal 100 GB budget and an effective 99.999891456 GB cap; it runs only
when its remaining cap and every required cell-set still ahead fit; the ext4
standard therefore reserves the required Btrfs quick/veryquick set. Otherwise
it exits successfully with
`skipped` before creating jobs or starting fio. The t0+6h raw standard cap is
included in the base schedule even
though that rung runs only when its design condition selects it. Every writing
job uses `io_size` aligned down to its `bs`, and omits `time_based`;
fio's `time_based` mode ignores `io_size`. Reads are time-based. Each rung also
has its nominal write allotment (15/26/100 GB), and the study state meters
actual fio write bytes separately from read `io_bytes`. Before an attempt
starts, the state reserves that cell's aligned write cap and active-time
allowance. A completed result replaces those reservations with measured bytes
and duration. Rejected or failed attempts save their fio JSON and replace the
write reservation with reported bytes when available; a failure before fio
starts releases it. If fio started but its output cannot establish actual
bytes, the full cap stays charged. On resume, any such outstanding reservation
is recorded as an interrupted attempt and retained as conservative spent bytes,
then a retry gets a fresh reservation. Every retry remains inside the same
500 GB meter. Before starting any base cell-set, the driver also checks that
bytes already charged plus all still-pending base caps fit the hard budget,
including the conditional t+6h standard rung. A failed earlier attempt can
make the complete remaining schedule unaffordable; the driver refuses before
the next cell instead of silently shrinking later caps.
The controller stops before a cell that could exceed the
500 GB, 3-hour active-I/O, or 12-hour elapsed limits. It also stops on three
consecutive memory PSI `full avg10` samples over the supplied threshold, low
`MemAvailable`, low free space on the output or file-target filesystem, or the
configured number of consecutive slow or failed host-liveness probes. I/O PSI
is recorded as advisory. The memory, liveness, and free-space thresholds are
required run options because the design does not set numeric values; select
them from the study host's approved policy. `--min-output-free` compares free
space on the filesystem containing `--out`. `--min-target-free` compares free
space on the filesystem containing a file target and is not sampled for raw
partition runs. With approved policy values, a 20 GiB output floor and a 1 GiB
target floor leave headroom on the repository filesystem while allowing the
32 GiB study partition to hold its preallocated 24 GiB file. Both values are
saved as one resume policy and must be reused unchanged.

Raw ladder sets must use exactly `[0, 24 GiB)`; part (c) must use exactly
`[24, 32 GiB)`. File sets must cover exactly `[0, 24 GiB)` of their preallocated
file. Every read job is clipped to the byte extent that the rung's fill actually
wrote, as reported by fio. The driver refuses a target extent that does not
match its cell-set.

Part (c) records four sequential passes of the 8 GiB region, interleaved with
4 KiB probes. After the 10-minute idle, it runs both a 4 KiB probe and a bounded
4 GiB sequential probe; after discard, it repeats both probes at +0 and +10 min.
The design does not set a numeric threshold for “idle stays low”, so the driver
uses the documented recovery threshold as its explicit comparison: both idle
probes must stay below 90% of pass 1, and both +10 min probes must recover to at
least 90%. This comparison is recorded in the analysis output.

The controller snapshots root `io.cost.qos` and `io.cost.model`, plus
`io.max`, `io.weight`, and `io.bfq.weight` for the benchmark cgroup and every
ancestor before and after each cell-set. A missing control file means there is
no setting on that kernel or cgroup; the snapshot records that absence so a
control appearing or changing before/after a run is still detected. Existing
unreadable or malformed files refuse. The guard refuses numeric `io.max`
limits, non-default `io.weight` or `io.bfq.weight`, enabled `io.cost.qos`, and
a user/custom `io.cost.model`; the kernel-managed `ctrl=auto model=linear`
model is allowed. `io.latency` can throttle peers, so the driver enumerates
direct peers at each level of its cgroup path and refuses an active latency
target on every check. Peer `io.weight` is not judged. Peer `io.bfq.weight` is
judged when the target disk's selected scheduler is `bfq`, because sibling
weights then affect its proportional bandwidth. The scheduler is read from
`/sys/block/<disk>/queue/scheduler`, recorded in the manifest and compared with
the IO-control snapshots. With any known non-BFQ scheduler, peer BFQ weights
are inert, including `none` and `mq-deadline`. If the scheduler file is
missing, unreadable, or malformed, the manifest reports that state and peer
BFQ weights are checked conservatively; any non-default value refuses with
the cgroup control path.
Peer membership is not included in before/after equality because services and
containers can come and go during a study. Other siblings' `io.max` caps are
not inherited by the benchmark cgroup. This lets the study run on kernels that
omit optional controls while retaining refusals for active limits that can
affect the study process. It also refuses targets that are not the expected block device or
file type/extent and verifies that the target's backing device is the same
device monitored by diskstats. A raw target must be a partition whose
size and PARTUUID exactly match the operator's `--confirm-target` acknowledgement.
The driver refuses whole disks, mounted or swap-active target partitions, active
exclusive claims, and holders. A raw partition sharing the running system disk
also refuses by default; the only exception requires both
`--allow-system-disk-partition` and a current acknowledgement derived by
`system-disk-ack` from hostname, disk, root filesystem device(s), and target
PARTUUID. The acknowledgement and observed system mounts and sibling swap
devices are recorded in state, each attempt and cell, JSON/text plan output,
and the analysis report. Resume compares stable host/root/disk/PARTUUID identity;
it records changing mount and sibling-swap observations per attempt. Changing
the stop policy refuses resume. A state with the former `min_free_bytes` field
is migrated only when both supplied split floors equal its saved value; that
preserves the previous minimum-free comparison and is recorded in the manifest.
Other policy changes still refuse. Keep the existing state and resume only with
the policy reported by the refusal and recorded in its run manifest; if that
policy cannot be used, keep the state and obtain an operator decision. Starting
with a new `--out` directory resets the byte and elapsed-time meters and
requires explicit approval as a new budget.
The refusal prints the saved policy as JSON. Map its fields back to the run
options: `memory_psi_full_avg10_max_percent` to `--max-memory-psi`,
`min_memavailable_bytes` to `--min-memavailable`,
`min_output_free_bytes` to `--min-output-free`,
`min_target_free_bytes` to `--min-target-free`,
`max_probe_latency_seconds` to `--max-probe-latency-seconds`,
`probe_consecutive_samples` to `--probe-consecutive-samples`, and
`probe_interval_seconds` to `--probe-interval-seconds`,
`liveness_ssh_host` to `--liveness-ssh-host`,
`liveness_ssh_port` to `--liveness-ssh-port`, and
`liveness_cockpit_container` to `--liveness-cockpit-container`.
The study result is therefore explicitly marked as including system and
sibling-swap activity. A swap-active target partition, mounted partition,
changed SIZE/PARTUUID, or any other safety failure still refuses. Resume state
stores the size, PARTUUID, and system-disk facts and refuses a replaced or
changed target. A btrfs file target resolves
its backing member through `/sys/fs/btrfs/<uuid>/devices`; an anonymous mount
device number is not treated as a member identity. Raw partitions must be
unmounted; file targets must be on the selected ext4 or btrfs filesystem. Raw run sets issue a bounded `blkdiscard`
for the 24 GiB region before each rung. The rewrite test uses only its 8 GiB
region. The optional repeat sets are listed in the plan, but return `skipped`
under the 500 GB cap because their full job caps do not fit after the base
schedule.

`run --dry-run` performs target, identity, schedule, IO-control, memory and
free-space checks and a successful first host-liveness probe, and renders every
fio job, then exits without creating output files, starting fio, discarding, or
changing the target. It reads diskstats, both pressure files, and meminfo;
missing required monitoring files produce a path-specific `StudyError`. It
also reads the target's discard capability and verifies the required fio and
target-operation tools before a pending run can pass. It does not check or require the fstrim, apt-daily,
apt-daily-upgrade, or governance timers to be stopped. Live runs require those timers inactive and record I/O PSI
as advisory; they stop on three consecutive memory PSI `full avg10` samples
above policy, low `MemAvailable`, low free space, or the configured number of
consecutive slow/failed host-liveness probes. The initial liveness probe must
succeed before any target discard or fio write. Subsequent probes run at the
recorded 10-second default cadence (`--probe-interval-seconds` can set a
different interval). Each bounded `system.slice` probe writes and fsyncs a
temporary file on `/`, checks an SSH banner at the configured host and port
(`127.0.0.1:22` by default), and verifies the configured Docker container with
`docker exec` (`mdt-cockpit-runner` by default), independently of the fio
cgroup. It does not require a systemd unit for the cockpit runner. One timed-out
probe remains failed until its transient probe unit is confirmed stopped; its
later samples still count toward the configured consecutive-failure limit.
Wall-time and write-byte caps remain hard stops.
The separately scoped live probes for the real raw target and Btrfs fio logs
are in [ACCEPTANCE.md](ACCEPTANCE.md); they start with raw dry-run validation
and never issue a raw write.

**Study decision D-IBX-002:** the root-disk exception is controller policy,
requested by the operator on 2026-11-13 because the approved disposable compute
hosts have only one virtual disk. It relaxes only the refusal for a dedicated
target partition sharing that disk; the driver retains every other target
guard and records the acknowledgement and system-disk context in the result.
Supplying `--allow-system-disk-partition` is the operator's consent to run on
the shared system disk. The per-run `--system-disk-ack` value is a drift check
that binds that consent to the current hostname, target disk, root filesystem
device(s), and target PARTUUID; deriving the value in a wrapper does not by
itself grant permission to write.
The study remains a root-disk study with system and sibling-swap activity, not
a quiet-disk measurement.

## Study-day invocation

Use the existing host-root wrapper from the checked-out repository. The
wrapper's `mdt doctor` pass is part of its established behavior. The controller
expects the study checkout and its output directory to be visible through that
wrapper. It writes resumable state, one JSON file per fio cell, generated fio
job files, and one-second monitor JSONL under the ignored
`libraries/iobench/artifacts/ibx-study/` directory.

Set the stop thresholds from the study host policy before starting; the
commands refuse if any are absent. The probe cadence is a separately recorded
controller default of 10 seconds and can be changed with
`--probe-interval-seconds`.
I/O PSI is recorded but is advisory because the saturating fio cell itself can
drive it to 100%. The independent host-liveness threshold and consecutive
sample count are configurable policy inputs alongside the memory, output-space,
and target-space floors. Set `IBX_EXPECTED_HOST` from the approved run authorization;
the sequence checks it before every driver run and checks both host and target
identity immediately before each direct filesystem operation. Do not derive
the expected hostname from the connected host.

```bash
set -euo pipefail

REPO=$(git rev-parse --show-toplevel)
HOST_REPO=$(python3 - "$REPO" <<'PY'
import os
import sys

path = os.path.realpath(sys.argv[1])
best = None
for line in open("/proc/self/mountinfo", encoding="utf-8"):
    fields = line.split()
    root, mountpoint = fields[3], fields[4]
    if (path == mountpoint or path.startswith(mountpoint.rstrip("/") + "/")) and (
        best is None or len(mountpoint) > len(best[1])
    ):
        best = (root, mountpoint)
if best is None:
    raise SystemExit("cannot map the checkout to its host-visible path")
print(os.path.normpath(best[0] + "/" + os.path.relpath(path, best[1])))
PY
)
LOCAL_STUDY="$REPO/libraries/iobench/studies/ibx/driver.py"
LOCAL_OUT="$REPO/libraries/iobench/artifacts/ibx-study"
STUDY="$HOST_REPO/libraries/iobench/studies/ibx/driver.py"
OUT="$HOST_REPO/libraries/iobench/artifacts/ibx-study"
: "${IBX_MAX_MEMORY_PSI:?set the approved memory PSI full avg10 stop threshold (percent)}"
: "${IBX_MAX_PROBE_LATENCY:?set the approved independent host-probe latency limit in seconds}"
: "${IBX_PROBE_CONSECUTIVE_SAMPLES:?set the approved consecutive slow-probe sample count}"
: "${IBX_MIN_MEMAVAILABLE:?set the approved MemAvailable floor, for example 8GiB}"
: "${IBX_MIN_OUTPUT_FREE:?set the approved free-space floor for the output filesystem}"
: "${IBX_MIN_TARGET_FREE:?set the approved free-space floor for the study target filesystem}"
: "${IBX_EXPECTED_HOST:?set the exact short hostname from approved run authorization}"
# Read the partition size and PARTUUID and compare them with the approved
# study-target inventory. Set this exact literal only after that check.
: "${IBX_TARGET_CONFIRMATION:?after checking the dedicated partition, set /dev/vda5:SIZE_BYTES:PARTUUID}"

assert_expected_host() {
  : "${IBX_EXPECTED_HOST:?set the exact short hostname from approved run authorization}"
  local actual_host
  actual_host=$(host-escape -- hostname -s)
  [[ $actual_host == "$IBX_EXPECTED_HOST" ]] || {
    printf 'refusing: expected host %s, connected to %s\n' "$IBX_EXPECTED_HOST" "$actual_host" >&2
    return 1
  }
}

confirm_study_target() {
  local actual_size actual_partuuid actual_identity
  actual_size=$(host-escape -- blockdev --getsize64 /dev/vda5)
  actual_partuuid=$(host-escape -- blkid -s PARTUUID -o value /dev/vda5)
  actual_identity="/dev/vda5:${actual_size}:${actual_partuuid}"
  if [[ "$actual_identity" != "$IBX_TARGET_CONFIRMATION" ]]; then
    printf 'study partition identity changed: expected %s, read %s\n' \
      "$IBX_TARGET_CONFIRMATION" "$actual_identity" >&2
    return 1
  fi
}

assert_study_host_and_target() {
  assert_expected_host || return 1
  confirm_study_target || return 1
}

study_transition() {
  assert_study_host_and_target || return 1
  host-escape -- "$@"
}

current_system_disk_ack() {
  assert_study_host_and_target || return 1
  host-escape -- python3 "$STUDY" system-disk-ack --target /dev/vda5 --device vda5 \
    --confirm-target "$IBX_TARGET_CONFIRMATION"
}

run_study() {
  assert_expected_host || return 1
  host-escape -- python3 "$STUDY" run "$@"
}

# Plan and target-read-only dry-run can run before checking or stopping timers.
assert_study_host_and_target || exit 1
host-escape -- python3 "$STUDY" plan --out "$OUT" || exit 1
run_study raw-t0 --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" --dry-run \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1

# Stop only timers that were active beforehand. Keep this shell open so the
# restoration block below can restart exactly those timers after live runs.
TIMER_UNITS=(fstrim.timer apt-daily.timer apt-daily-upgrade.timer mdt-dev-governance-reconcile.timer)
ACTIVE_TIMER_UNITS=()
for unit in "${TIMER_UNITS[@]}"; do
  state=$(host-escape -- systemctl show "$unit" --property=LoadState --property=ActiveState --value)
  mapfile -t values <<<"$state"
  if ((${#values[@]} != 2)); then
    printf 'cannot establish timer state for %s\n' "$unit" >&2
    exit 1
  fi
  case "${values[0]}:${values[1]}" in
    loaded:active) ACTIVE_TIMER_UNITS+=("$unit") ;;
    loaded:inactive|loaded:dead|not-found:inactive|not-found:dead) ;;
    *) printf 'unexpected timer state for %s: %s\n' "$unit" "$state" >&2; exit 1 ;;
  esac
done
restore_timers() {
  local prior_status=$? restore_status=0
  trap - EXIT
  if ((${#ACTIVE_TIMER_UNITS[@]})); then
    host-escape -- systemctl start "${ACTIVE_TIMER_UNITS[@]}" || restore_status=$?
  fi
  if ((restore_status != 0)); then
    printf 'failed to restore previously active timers (status %d)\n' \
      "$restore_status" >&2
    if ((prior_status == 0)); then prior_status=$restore_status; fi
  fi
  exit "$prior_status"
}
trap restore_timers EXIT
printf 'Restore these previously active timers after the study: %s\n' "${ACTIVE_TIMER_UNITS[*]:-none}"
if ((${#ACTIVE_TIMER_UNITS[@]})); then
  host-escape -- systemctl stop "${ACTIVE_TIMER_UNITS[@]}"
fi

# t0: run the raw ladder. This SSH-host sequence leaves optional fsync
# collection disabled because the caller does not have a Docker cgroup scope.
run_study raw-t0 --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1

# t0+1h: the bounded rewrite/fstrim cell set on region C.
run_study part-c --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 24GiB --size 8GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1

# t0+3h, then quick/veryquick at t0+6h; raw-t6h adds standard only if its 6.2 rule fires.
run_study raw-t3h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1

# t0+4h: the driver reports this optional repeat as skipped under the 500 GB
# cap, even if the first analysis reports degradation.
run_study part-c-repeat --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 24GiB --size 8GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1

run_study raw-t6h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1

# t0+9h raw measurements, then format the study partition for each file target.
run_study raw-t9h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1
study_transition mkfs.ext4 -E nodiscard,lazy_itable_init=0,lazy_journal_init=0 /dev/vda5 || exit 1
study_transition mkdir -p /mnt/ibx-study || exit 1
study_transition mount -o noatime /dev/vda5 /mnt/ibx-study || exit 1
study_transition fallocate -l 24GiB /mnt/ibx-study/target.dat || exit 1

# These are deliberate study-day filesystem transitions on the dedicated target.
# Run each through the same host-root wrapper and only after raw-t9h has finished.
run_study file-ext4 --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type ext4-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1
python3 "$LOCAL_STUDY" analyze "$LOCAL_OUT" --output "$LOCAL_OUT/rung-choice.md"
# Optional: runs only if the ext4 quick result is borderline; otherwise reports skipped.
run_study file-ext4-standard --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type ext4-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1
study_transition umount /mnt/ibx-study || exit 1
study_transition wipefs -a /dev/vda5 || exit 1
study_transition mkfs.btrfs -K /dev/vda5 || exit 1
study_transition mount -o noatime /dev/vda5 /mnt/ibx-study || exit 1
study_transition fallocate -l 24GiB /mnt/ibx-study/target.dat || exit 1
run_study file-btrfs --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type btrfs-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1
python3 "$LOCAL_STUDY" analyze "$LOCAL_OUT" --output "$LOCAL_OUT/rung-choice.md"
# Optional: runs only if the btrfs quick result is borderline; otherwise reports skipped.
run_study file-btrfs-standard --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type btrfs-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1
study_transition umount /mnt/ibx-study || exit 1
study_transition wipefs -a /dev/vda5 || exit 1
study_transition blkdiscard /dev/vda5 || exit 1

# The optional raw repeats are listed for traceability, but report skipped under
# the 500 GB cap because their complete job caps do not fit after the base set.
run_study raw-t12h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1
run_study raw-t12h2 --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-target "$IBX_TARGET_CONFIRMATION" \
  --allow-system-disk-partition --system-disk-ack "$(current_system_disk_ack)" \
  --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" \
  --min-output-free "$IBX_MIN_OUTPUT_FREE" --min-target-free "$IBX_MIN_TARGET_FREE" || exit 1

python3 "$LOCAL_STUDY" analyze "$LOCAL_OUT" --output "$LOCAL_OUT/rung-choice.md"
```

The adaptive repeat sets return a successful `skipped` result under the current
500 GB cap. Optional file-standard sets also return `skipped` when their
remaining 100 GB set plus the unpaid required remainder does not fit. After the
study, copy the output directory off the
host to the designated report location and remove the transient output after
its receipt is verified. The EXIT trap starts exactly the timers that were
active before the run, including when a command fails or the shell exits.

`--fsync-cell RUNG:CELL` starts the existing `iomon-bpf/run.sh`, verifies its
background slice and image are available, watches the host-wrapper container,
and stops the exact collector it started after the selected fio cell finishes.
For example, `--fsync-cell standard:seq1m-write` collects `sync.bt` around that
standard cell; the JSONL files remain under `OUT/fsync-bpf/`. This mode requires
the driver itself to run inside a Docker cgroup scope whose membership contains
exactly one `docker-<64-hex-id>.scope`, the approved
`CGROUP_PARENT_DEV_BACKGROUND` value, and a built `iomon-bpf:20261027` image.
The dry-run checks those requirements and refuses clearly if they are absent;
root sessions reached directly over SSH do not have the required Docker cgroup
identity. Omit `--fsync-cell` when running outside that Docker scope.

## Resume and analysis

The state file is `ibx-study-state.json`. Completed cell JSON is adopted on
resume only if its cell key matches the schedule; cells with state but missing
result files are refused. Re-running a completed cell-set does not rerun fio.
The state meters completed writes plus outstanding conservative reservations
and stores before/after IO-control snapshots. `monitor.jsonl` carries one-second
diskstats (including discard and flush counters), IO and memory PSI, and the
parsed `/proc/meminfo` fields.

```bash
python3 libraries/iobench/studies/ibx/driver.py analyze \
  libraries/iobench/artifacts/ibx-study --output \
  libraries/iobench/artifacts/ibx-study/rung-choice.md
```

Analysis uses the median raw `standard` result at the closest time slot as the
reference, reports `e`, bias, spread, and the raw-standard moving-target spread
`V`, and adds file bias against raw at the same slot. It reports section 6.6
noise-band actions (`proven`, `repeat-once`, `repeat`, `small`, or `no-effect`)
and applies the four-run, >20% movement rule to optional raw repeats. It renders one table for
raw, ext4-file, and btrfs-file, with `✓`, `⚠`, `✗`, or `—` and the required
usable/correct/not-usable summary for each rung.

The `dirty_bytes` row reports the measured `wbps × 1 s` basis before the
host-specific clamp. Rev 4.1 specifies the clamp operation but does not provide
the study host's effective floor and ceiling inputs, so this row does not claim
the final absolute writeback setting.
