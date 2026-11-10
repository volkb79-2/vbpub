# iobench ladder error study

This host-only controller reproduces the nested `veryquick`, `quick`, and
`standard` fio cell families from the approved IBX design (rev 4.1, section 5),
and the bounded schedule and analysis in sections 6.2–6.7. The controller uses Python's standard library;
fio is the only benchmark executable. It does not contain SSH code and this
change has not run against a host.

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
study at `t0+1h`, raw veryquick/quick at `t0+3h`, quick/veryquick/standard at
`t0+6h` (the second standard point establishes `V`), then raw and file
quick/veryquick by `t0+9h`. Up to two extra
quick/veryquick slots run only if the fourth or fifth observation moves `e` by
more than 20 percent. They require complete results for every planned cell in
the preceding slots. The optional slots must finish before the 12-hour elapsed
limit. A file-target standard set runs only when its quick result is borderline;
because rev 4.1 does not give that cutoff a number, the driver defines it as
`T/1.5 ≤ e ≤ T+C` and records that decision in the result report.

The meter reserves 500 GB total. It derives the base schedule's maximum from
the fio job caps (currently 495.12 GB) and leaves the remainder (currently
4.88 GB) for adaptive repeats. The driver derives both figures from the cell
families, so a repeat is refused before it starts if its full write caps do not
fit. Every writing job uses `io_size` and omits `time_based`;
fio's `time_based` mode ignores `io_size`. Reads are time-based. Each rung also
has its own write allotment (15/26/100 GB), and the study state meters actual
fio write bytes separately from read `io_bytes`. Before an attempt starts, the
state reserves that cell's full write cap and active-time allowance. A completed
result replaces those reservations with measured bytes and duration; an
interrupted attempt keeps its full reservation so resume cannot undercount it.
The controller stops before a cell that could exceed the
500 GB, 3-hour active-I/O, or 12-hour elapsed limits. It also stops on three
consecutive I/O or memory PSI samples over the supplied thresholds, low
`MemAvailable`, or free space below the supplied floor on the output or file
target filesystem. The PSI, memory, and
free-space thresholds are required run options because the design does not set
numeric values; select them from the study host's approved operating policy.

Raw ladder sets must use exactly `[0, 24 GiB)`; part (c) must use exactly
`[24, 32 GiB)`. File sets must cover exactly `[0, 24 GiB)` of their preallocated
file. The driver refuses a target extent that does not match its cell-set.

Part (c) records four sequential passes of the 8 GiB region, interleaved with
4 KiB probes. After the 10-minute idle, it runs both a 4 KiB probe and a bounded
4 GiB sequential probe; after discard, it repeats both probes at +0 and +10 min.
The design does not set a numeric threshold for “idle stays low”, so the driver
uses the documented recovery threshold as its explicit comparison: both idle
probes must stay below 90% of pass 1, and both +10 min probes must recover to at
least 90%. This comparison is recorded in the analysis output.

The controller snapshots `io.cost.qos`, `io.cost.model`, `io.max`, `io.weight`,
and `io.latency` for the benchmark cgroup and every ancestor before and after
each cell-set. It refuses if io.cost is enabled/configured or any ancestor has
an active IO control. It also refuses targets that are not the expected block
device or file type/extent and verifies that the target's backing device is the
same device monitored by diskstats. Raw partitions must be unmounted; file
targets must be on the selected ext4 or btrfs filesystem. Raw run sets issue a bounded `blkdiscard`
for the 24 GiB region before each rung. The rewrite test uses only its 8 GiB
region. The one optional repeat of (c) and both optional t+12 sets must also fit
the remaining write meter before they start.

## Study-day invocation

Use the existing host-root wrapper from the checked-out repository. The
wrapper's `mdt doctor` pass is part of its established behavior. The controller
expects the study checkout and its output directory to be visible through that
wrapper. It writes resumable state, one JSON file per fio cell, generated fio
job files, and one-second monitor JSONL under the ignored
`libraries/iobench/artifacts/ibx-study/` directory.

Set the four stop thresholds from the study host policy before starting; the
commands refuse if any are absent. No values are guessed by this controller.

```bash
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
: "${IBX_MAX_IO_PSI:?set the approved I/O PSI avg10 stop threshold (percent)}"
: "${IBX_MAX_MEMORY_PSI:?set the approved memory PSI avg10 stop threshold (percent)}"
: "${IBX_MIN_MEMAVAILABLE:?set the approved MemAvailable floor, for example 8GiB}"
: "${IBX_MIN_FREE:?set the approved output-filesystem free-space floor}"

# Stop only timers that were active beforehand. Keep this shell open so the
# restoration block below can restart exactly those timers after the study.
TIMER_UNITS=(fstrim.timer apt-daily.timer apt-daily-upgrade.timer)
ACTIVE_TIMER_UNITS=()
for unit in "${TIMER_UNITS[@]}"; do
  if host-escape -- systemctl is-active --quiet "$unit"; then
    ACTIVE_TIMER_UNITS+=("$unit")
  fi
done
printf 'Restore these previously active timers after the study: %s\n' "${ACTIVE_TIMER_UNITS[*]:-none}"
if ((${#ACTIVE_TIMER_UNITS[@]})); then
  host-escape -- systemctl stop "${ACTIVE_TIMER_UNITS[@]}"
fi

host-escape -- python3 "$STUDY" plan --out "$OUT"

# t0: run the raw ladder. The optional fsync collection wraps only the named
# standard rung cell and reuses mdt-cockpit/scripts/iomon-bpf/run.sh.
host-escape -- python3 "$STUDY" run raw-t0 --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE" \
  --fsync-cell standard:seq1m-write

# t0+1h: the bounded rewrite/fstrim cell set on region C.
host-escape -- python3 "$STUDY" run part-c --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 24GiB --size 8GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"

# t0+3h and t0+6h.
host-escape -- python3 "$STUDY" run raw-t3h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"

# t0+4h: repeat (c) once at another slot only if the first analysis says
# confirmed. The driver checks pass 4, the idle probe, and +10m recovery first.
host-escape -- python3 "$STUDY" run part-c-repeat --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 24GiB --size 8GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"

host-escape -- python3 "$STUDY" run raw-t6h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"

# t0+9h raw measurements, then format the study partition for each file target.
host-escape -- python3 "$STUDY" run raw-t9h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"

# These are deliberate study-day filesystem transitions on the dedicated target.
# Run each through the same host-root wrapper and only after raw-t9h has finished.
host-escape -- mkfs.ext4 -E nodiscard,lazy_itable_init=0,lazy_journal_init=0 /dev/vda5
host-escape -- mkdir -p /mnt/ibx-study
host-escape -- mount -o noatime /dev/vda5 /mnt/ibx-study
host-escape -- fallocate -l 24GiB /mnt/ibx-study/target.dat
host-escape -- python3 "$STUDY" run file-ext4 --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type ext4-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"
python3 "$LOCAL_STUDY" analyze "$LOCAL_OUT" --output "$LOCAL_OUT/rung-choice.md"
# Optional: runs only if the ext4 quick result is borderline; otherwise refuses.
host-escape -- python3 "$STUDY" run file-ext4-standard --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type ext4-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"
host-escape -- umount /mnt/ibx-study

host-escape -- wipefs -a /dev/vda5
host-escape -- mkfs.btrfs -K /dev/vda5
host-escape -- mount -o noatime /dev/vda5 /mnt/ibx-study
host-escape -- fallocate -l 24GiB /mnt/ibx-study/target.dat
host-escape -- python3 "$STUDY" run file-btrfs --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type btrfs-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"
python3 "$LOCAL_STUDY" analyze "$LOCAL_OUT" --output "$LOCAL_OUT/rung-choice.md"
# Optional: runs only if the btrfs quick result is borderline; otherwise refuses.
host-escape -- python3 "$STUDY" run file-btrfs-standard --target /mnt/ibx-study/target.dat --device vda5 \
  --target-type btrfs-file --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"
host-escape -- umount /mnt/ibx-study
host-escape -- wipefs -a /dev/vda5
host-escape -- blkdiscard /dev/vda5

# Optional extra repetitions: analyze after four quick/veryquick raw runs.
# Each set refuses unless the newest observation moved e by >20%.
host-escape -- python3 "$STUDY" run raw-t12h --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"
host-escape -- python3 "$STUDY" run raw-t12h2 --target /dev/vda5 --device vda5 \
  --target-type raw --confirm-quiet-window --offset 0B --size 24GiB --out "$OUT" \
  --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"

python3 "$LOCAL_STUDY" analyze "$LOCAL_OUT" --output "$LOCAL_OUT/rung-choice.md"
if ((${#ACTIVE_TIMER_UNITS[@]})); then
  host-escape -- systemctl start "${ACTIVE_TIMER_UNITS[@]}"
fi
```

The repeated optional set is expected to skip when its rule says no extra run is
needed; run it only if there is enough room before the elapsed deadline. After
the study, copy the output directory off the host to the designated report
location and remove the transient output after its receipt is verified. If the
study is interrupted, run the same timer-start command for the units listed in
`ACTIVE_TIMER_UNITS` before ending the host session.

`--fsync-cell RUNG:CELL` starts the existing `iomon-bpf/run.sh`, verifies its
background slice is loaded, watches the host-wrapper container, and stops the
exact collector it started after the selected fio cell finishes. For example,
`--fsync-cell standard:seq1m-write` collects `sync.bt` around that standard
cell; the JSONL files remain under `OUT/fsync-bpf/`. This mode requires the
approved `CGROUP_PARENT_DEV_BACKGROUND` value in the wrapper's environment and
a built `iomon-bpf:20261027` image.

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
