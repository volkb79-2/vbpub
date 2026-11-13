# IBX live host acceptance (IOB-BL-41 / IOB-BL-42 / IOB-BL-44 / IOB-BL-45 / IOB-BL-46)

Run only on the approved freshly reinstalled disposable compute host with its
dedicated `iobench-raw` partition. This runbook has **not** been run here.
Set `IBX_EXPECTED_HOST` from the approved run authorization before connecting;
do not derive the expected value from the live host. Initialize `STUDY`, `OUT`,
and the stop-policy variables as documented in the study README, but do not
execute its timer-control block or live study sequence. Run this target-read-only
preflight in the same shell. The dry-run does not require timers to be stopped;
keep the setup shell open for the later live-run EXIT trap that restores timers.

```bash
set -euo pipefail
assert_expected_host() {
  : "${IBX_EXPECTED_HOST:?set the exact short hostname from approved run authorization}"
  local actual_host
  actual_host=$(host-escape -- hostname -s)
  [[ $actual_host == "$IBX_EXPECTED_HOST" ]] || {
    printf 'refusing: expected host %s, connected to %s\n' "$IBX_EXPECTED_HOST" "$actual_host" >&2
    return 1
  }
}
assert_expected_host
# Accept either inactive/failed from a retained collected unit or unknown
# after systemd removes it. This read-only check starts no unit and writes
# nothing.
UNKNOWN_UNIT="ibx-acceptance-unknown-${BASHPID}.service"
if UNKNOWN_STATE=$(host-escape -- systemctl is-active "$UNKNOWN_UNIT"); then
  UNKNOWN_STATUS=0
else
  UNKNOWN_STATUS=$?
fi
case "$UNKNOWN_STATUS:$UNKNOWN_STATE" in
  4:unknown) ;;
  *:inactive|*:failed)
    ((UNKNOWN_STATUS != 0)) || {
      printf 'unexpected active systemctl result for missing unit %s: status=%s state=%s\n' \
        "$UNKNOWN_UNIT" "$UNKNOWN_STATUS" "$UNKNOWN_STATE" >&2
      exit 1
    }
    ;;
  *)
    printf 'unexpected systemctl is-active result for missing unit %s: status=%s state=%s\n' \
      "$UNKNOWN_UNIT" "$UNKNOWN_STATUS" "$UNKNOWN_STATE" >&2
    exit 1
    ;;
esac
printf 'missing-unit systemd status: %s (%s)\n' "$UNKNOWN_STATE" "$UNKNOWN_STATUS"
RAW=/dev/vda5
[[ $(host-escape -- lsblk -dn -o TYPE "$RAW") == part ]]
[[ $(host-escape -- lsblk -dn -o PARTLABEL "$RAW") == iobench-raw ]]
[[ $(host-escape -- blockdev --getsize64 "$RAW") == 34359738368 ]]
export IBX_TARGET_CONFIRMATION="$RAW:34359738368:$(host-escape -- blkid -s PARTUUID -o value "$RAW")"
```

On the known single-disk layout, `/dev/vda5` is a dedicated partition on the
same virtual disk as the running system and swap. The default raw dry-run must
still refuse this target with the existing byte-identical root/system-disk
message. A second dry-run with the explicit operator consent flag and a freshly
derived per-run drift-check token must pass, because all other target guards
remain active. The flag is consent; deriving the token only binds it to current
host and target facts. The output and later analysis explicitly record that
system and sibling-swap activity are part of the measurement context. This
remains target-read-only: do not run a live raw write, discard, or format in
this procedure. The initial liveness probe writes and fsyncs a temporary file
on the host root filesystem as its responsiveness check.

Round-two read-only verification found that the cockpit runner is the Docker
container `mdt-cockpit-runner`; `mdt-cockpit-runner.service` is not a systemd
unit on the host. The liveness probe therefore checks the container with
`docker exec` and does not query that unit. The SSH banner probe defaults to
`127.0.0.1:22`; on r1002, sshd was verified listening on `0.0.0.0:22`. If a
study host uses another SSH endpoint, pass `--liveness-ssh-host` and
`--liveness-ssh-port`; the runner name can be changed with
`--liveness-cockpit-container`.

The saved read-only IO snapshot records the study disk scheduler as
`[none] mq-deadline`; all 13 captured cgroups at depth two had no
`io.bfq.weight` file. With any known non-BFQ scheduler, including `none` or
`mq-deadline`, peer BFQ weights are inert and do not block the study. If the
target disk uses `bfq`, active peer BFQ weights refuse with the cgroup
filename. A missing, unreadable, or malformed scheduler file is reported as
unknown in the dry-run
manifest and causes peer BFQ weights to be checked conservatively. The target
and root disk scheduler facts are included in the before/after IO snapshot.
For a passing acknowledged dry-run, retain the printed
`run_manifest.io_schedulers.raw` fields with the acceptance result; an unknown
target scheduler includes a message that peer BFQ weights were checked
conservatively.

The acknowledged dry-run below intentionally omits `--fsync-cell`. That option
requires the driver process to have exactly one `docker-<64-hex-id>.scope`
identity in `/proc/self/cgroup`; a root SSH session does not meet this
requirement. When `--fsync-cell` is selected, dry-run checks the Docker cgroup
scope, loaded background slice, wrapper, and collector image before it can
pass. Run that mode from the required Docker scope, or omit the optional fsync
collector.

## 1. Target-read-only inventory and raw dry-run

```bash
set -euo pipefail
RAW=/dev/vda5
MOUNT=/mnt/ibx-study
FILE=$MOUNT/target.dat

[[ $(host-escape -- lsblk -dn -o PARTLABEL "$RAW") == iobench-raw ]]
RAW_BYTES=$(host-escape -- blockdev --getsize64 "$RAW")
RAW_PARTUUID=$(host-escape -- blkid -s PARTUUID -o value "$RAW")
[[ $RAW_BYTES == 34359738368 ]]
IBX_TARGET_CONFIRMATION="$RAW:$RAW_BYTES:$RAW_PARTUUID"
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
confirm_study_target
host-escape -- lsblk -o NAME,TYPE,SIZE,PARTLABEL,FSTYPE,MOUNTPOINTS /dev/vda
FIO_VERSION=$(host-escape -- fio --version)
FIO_HELP=$(host-escape -- fio --cmdhelp=allow_mounted_write)
[[ $FIO_HELP == *allow_mounted_write* ]]
printf 'fio=%s; allow_mounted_write option present\n' "$FIO_VERSION"

raw_dry_run() {
  local raw=$1 disk=$2 confirmation=$3
  shift 3
  host-escape -- python3 "$STUDY" run raw-t0 --target "$raw" --device "$disk" \
    --target-type raw --confirm-target "$confirmation" "$@" --confirm-quiet-window \
    --offset 0B --size 24GiB --out "$OUT" \
    --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
    --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
    --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
    --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE" \
    --dry-run
}
current_system_disk_ack() {
  host-escape -- python3 "$STUDY" system-disk-ack --target "$RAW" --device vda5 \
    --confirm-target "$IBX_TARGET_CONFIRMATION"
}
expect_refusal() {
  local expected=$1 status output
  shift
  if output=$("$@" 2>&1); then
    status=0
  else
    status=$?
  fi
  if ((status != 2)) || [[ $output != *"$expected"* ]]; then
    printf 'FAIL: wanted exit 2 and %s; got exit %s:\n%s\n' \
      "$expected" "$status" "$output" >&2
    return 1
  fi
  printf 'PASS refusal: %s\n' "$expected"
}
CHECK_COUNT=0
FAIL_COUNT=0
record_check() {
  local label=$1 status
  shift
  CHECK_COUNT=$((CHECK_COUNT + 1))
  if "$@"; then
    printf 'PASS check: %s\n' "$label"
  else
    status=$?
    FAIL_COUNT=$((FAIL_COUNT + 1))
    printf 'FAIL check: %s (status %s)\n' "$label" "$status" >&2
  fi
}
check_acknowledged_dry_run() {
  local acknowledgement result status
  if acknowledgement=$(current_system_disk_ack 2>&1); then
    :
  else
    status=$?
    printf 'could not derive system-disk acknowledgement (exit %s):\n%s\n' \
      "$status" "$acknowledgement" >&2
    return 1
  fi
  if result=$(raw_dry_run "$RAW" vda5 "$IBX_TARGET_CONFIRMATION" \
      --allow-system-disk-partition --system-disk-ack "$acknowledgement" 2>&1); then
    :
  else
    status=$?
    printf 'acknowledged dry-run failed (exit %s):\n%s\n' "$status" "$result" >&2
    return 1
  fi
  if [[ $result != *'"dry_run": true'* ]]; then
    printf 'acknowledged dry-run did not report dry_run=true:\n%s\n' "$result" >&2
    return 1
  fi
  printf 'PASS acknowledged system-disk dry-run, IO-control and liveness smoke: %s\n' "$result"
}
check_whole_disk_refusal() {
  local disk_bytes status
  if disk_bytes=$(host-escape -- blockdev --getsize64 /dev/vda); then
    :
  else
    status=$?
    printf 'could not read whole-disk size (exit %s): %s\n' "$status" "$disk_bytes" >&2
    return 1
  fi
  expect_refusal "raw target must be a dedicated partition; whole disks are refused" \
    raw_dry_run /dev/vda vda "/dev/vda:$disk_bytes:00000000-0000-0000-0000-000000000000"
}

# Without opt-in, the system-disk refusal text must remain unchanged.
record_check "default system-disk refusal" expect_refusal \
  "raw target is on the root disk or contains a running system filesystem" \
  raw_dry_run "$RAW" vda5 "$IBX_TARGET_CONFIRMATION"

# The explicit flag records the operator's consent. The token derived here is
# a drift check for this host, disk, root device and target PARTUUID. The
# acknowledged dry-run must pass, does not create output files, and does not
# require timer state changes; it does require the IO-control snapshot,
# monitor sample, and first host-liveness probe to pass.
record_check "acknowledged system-disk dry-run, IO-control and liveness smoke" check_acknowledged_dry_run
record_check "wrong system-disk acknowledgement refusal" expect_refusal \
  "system-disk acknowledgement does not match current host facts" \
  raw_dry_run "$RAW" vda5 "$IBX_TARGET_CONFIRMATION" \
    --allow-system-disk-partition --system-disk-ack "wrong:disk:root:partuuid"

# Confirm identity mismatches and a whole-disk target also refuse before effects.
record_check "size mismatch refusal" expect_refusal \
  "raw target size does not match --confirm-target" \
  raw_dry_run "$RAW" vda5 "$RAW:$((RAW_BYTES - 512)):$RAW_PARTUUID"
record_check "PARTUUID mismatch refusal" expect_refusal \
  "raw target PARTUUID does not match --confirm-target" \
  raw_dry_run "$RAW" vda5 "$RAW:$RAW_BYTES:00000000-0000-0000-0000-000000000000"
record_check "whole-disk refusal" check_whole_disk_refusal
printf 'SECTION 1 summary: %s checks, %s failures\n' "$CHECK_COUNT" "$FAIL_COUNT"
if ((FAIL_COUNT != 0)); then
  printf 'SECTION1-FAIL\n'
  exit 1
fi
printf 'SECTION1-PASS\n'
```

Expected: the default system-disk attempt and the SIZE/PARTUUID/whole-disk
cases refuse; the explicit consent flag with the exact derived drift-check
token permits only the dry-run and provides the IO-control snapshot smoke; a
wrong token refuses. Each check records its own result, later checks still run
after a failure, and the final summary exits non-zero if any check failed. No
raw live run, discard, or raw write is part of this acceptance. Do not
manufacture swap or holder state to probe those branches; fake-sysfs tests
cover them.

## 2. Btrfs file and real fio-log acceptance

The following destructive format is permitted only after the exact inventory
checks pass, the partition is unused, and `wipefs -n` reports no signatures.
If any check differs, stop before `mkfs`.

```bash
assert_raw_idle() {
  assert_expected_host
  [[ $(host-escape -- lsblk -dn -o TYPE "$RAW") == part ]]
  [[ $(host-escape -- lsblk -dn -o PARTLABEL "$RAW") == iobench-raw ]]
  [[ $(host-escape -- blockdev --getsize64 "$RAW") == 34359738368 ]]
  [[ $(host-escape -- blkid -s PARTUUID -o value "$RAW") == "$RAW_PARTUUID" ]]
  host-escape -- python3 -c 'import os,stat,subprocess,sys
p=sys.argv[1]; st=os.stat(p); dev=f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
for name in ("/proc/self/mountinfo", "/proc/1/mountinfo"):
    for line in open(name, encoding="utf-8"):
        fields=line.split()
        if fields[2] == dev:
            raise SystemExit(f"refusing mounted target in {name}")
        if "-" not in fields:
            raise SystemExit(f"cannot parse mount facts in {name}")
        sep=fields.index("-")
        if fields[sep+1] != "btrfs":
            continue
        source=fields[sep+2].split("[",1)[0]
        if source.startswith("UUID="):
            result=subprocess.run(["findfs",source],text=True,capture_output=True,check=False)
            if result.returncode:
                raise SystemExit(f"cannot resolve mounted Btrfs source in {name}")
            source=result.stdout.strip()
        try:
            mounted=os.stat(source)
        except OSError:
            raise SystemExit(f"cannot establish mounted Btrfs source in {name}")
        if stat.S_ISBLK(mounted.st_mode) and mounted.st_rdev == st.st_rdev:
            raise SystemExit(f"refusing Btrfs target mounted in {name}")
for line in open("/proc/swaps", encoding="utf-8").read().splitlines()[1:]:
    try:
        swap=os.stat(line.split()[0])
    except OSError:
        raise SystemExit("cannot establish active swap identity")
    swapdev=swap.st_rdev if stat.S_ISBLK(swap.st_mode) else swap.st_dev
    if f"{os.major(swapdev)}:{os.minor(swapdev)}" == dev:
        raise SystemExit("refusing swap-active target")
if (os.listdir("/sys/class/block/vda5/holders") or
        os.listdir("/sys/class/block/vda/holders")):
    raise SystemExit("refusing target or parent disk with sysfs holders")' "$RAW"
}

assert_raw_idle
SIGNATURES=$(host-escape -- wipefs -n --noheadings --output TYPE "$RAW")
[[ -z $SIGNATURES ]]
host-escape -- python3 -c 'import os,sys
p=sys.argv[1]
if os.path.lexists(p):
    raise SystemExit("refusing existing mountpoint path")
for name in ("/proc/self/mountinfo", "/proc/1/mountinfo"):
    for line in open(name, encoding="utf-8"):
        fields=line.split()
        if len(fields)<5:
            raise SystemExit(f"cannot parse mount facts in {name}")
        if fields[4] == p:
            raise SystemExit(f"refusing mounted path in {name}")' "$MOUNT"
host-escape -- mkfs.btrfs -K "$RAW"
BTRFS_UUID=$(host-escape -- blkid -s UUID -o value "$RAW")
[[ -n $BTRFS_UUID ]]
host-escape -- mkdir "$MOUNT"
host-escape -- mount -o noatime "$RAW" "$MOUNT"

assert_btrfs_mount() {
  [[ $(host-escape -- findmnt -n -o TARGET --target "$MOUNT") == "$MOUNT" ]]
  [[ $(host-escape -- findmnt -n -o FSTYPE --target "$MOUNT") == btrfs ]]
  [[ $(host-escape -- findmnt -n -o UUID --target "$MOUNT") == "$BTRFS_UUID" ]]
  [[ $(host-escape -- find "/sys/fs/btrfs/$BTRFS_UUID/devices" \
      -mindepth 1 -maxdepth 1 -printf '%f\n') == vda5 ]]
}
assert_btrfs_mount
printf 'raw_partuuid=%s btrfs_uuid=%s btrfs_member=vda5\n' "$RAW_PARTUUID" "$BTRFS_UUID"
host-escape -- python3 -c 'import os,sys
p=sys.argv[1]
if os.path.lexists(p):
    raise SystemExit("refusing existing target file path")' "$FILE"
host-escape -- fallocate -l 24GiB "$FILE"

# Dry-run checks the mounted Btrfs UUID/member and renders all 33 fio jobs.
host-escape -- python3 "$STUDY" run file-btrfs --target "$FILE" --device vda5 \
  --target-type btrfs-file --confirm-quiet-window --offset 0B --size 24GiB \
  --out "$OUT" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE" --dry-run

# Only after dry-run returns {"dry_run":true,"jobs_rendered":33,...}, run fio.
host-escape -- python3 "$STUDY" run file-btrfs --target "$FILE" --device vda5 \
  --target-type btrfs-file --confirm-quiet-window --offset 0B --size 24GiB \
  --out "$OUT" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --max-probe-latency-seconds "$IBX_MAX_PROBE_LATENCY" \
  --probe-consecutive-samples "$IBX_PROBE_CONSECUTIVE_SAMPLES" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE"

# Require both file rungs (33 fio cells) and one non-empty bandwidth and IOPS
# log per cell. This verifier only reads host output and prints a summary.
host-escape -- python3 -c 'import csv,json,pathlib,sys
out=pathlib.Path(sys.argv[1]); rows=sorted((out/"cells").glob("btrfs-file_t9h_*.json"))
data=[(p,json.loads(p.read_text(encoding="utf-8"))) for p in rows]
fio=[(p,row) for p,row in data if row.get("cell") != "trim-state"]
assert len(data)==35 and len(fio)==33, (len(data),len(fio))
assert all(row.get("cell_set")=="file-btrfs" and row.get("target_type")=="btrfs-file" for _,row in data)
for path,record in fio:
    for kind in ("bw","iops"):
        log=out/"fio-logs"/(path.stem+"_"+kind+".1.log")
        assert log.is_file() and log.stat().st_size, str(log)
        samples=list(csv.reader(log.open(encoding="utf-8")))
        assert samples and all(len(row)>=2 and float(row[0])>=0 and float(row[1])>=0
                               for row in samples), str(log)
        assert record.get("fio_series", {}).get(kind), (path, kind)
print("PASS: 33 Btrfs fio cells; 66 non-empty 1s bandwidth/IOPS logs; 2 trim-state records")' "$OUT"
host-escape -- python3 "$STUDY" analyze "$OUT" --output "$OUT/btrfs-acceptance.md"
```

Expected live result: `file-btrfs` returns `"results":35`; the verifier prints
the stated PASS; the analysis names both Btrfs rungs. Preserve the state,
cell JSON, fio logs, analysis, fio version, Btrfs UUID and sysfs member
(`vda5`) in the approved evidence store. Keep the acceptance record free of
hostnames and addresses.

## Rollback

Copy and verify the evidence first. Then unmount only the exact Btrfs target;
rerun `assert_raw_idle` (including its authorized-host pin, identity, mount,
swap, and holder guards) before erasing the filesystem signature to restore the
fresh-install raw partition.

```bash
assert_expected_host
assert_btrfs_mount
host-escape -- umount "$MOUNT"
assert_raw_idle
[[ $(host-escape -- lsblk -dn -o PARTLABEL "$RAW") == iobench-raw ]]
[[ $(host-escape -- blockdev --getsize64 "$RAW") == 34359738368 ]]
[[ $(host-escape -- blkid -s PARTUUID -o value "$RAW") == "$RAW_PARTUUID" ]]
host-escape -- wipefs -a "$RAW"
SIGNATURES=$(host-escape -- wipefs -n --noheadings --output TYPE "$RAW")
[[ -z $SIGNATURES ]]
host-escape -- rmdir "$MOUNT"
```

Expected rollback: the partition retains its GPT `iobench-raw` label, 32 GiB
size and original PARTUUID, has no filesystem signature, is unmounted and is
not swap-active. Keep the verified `OUT` evidence; do not delete it as part of
rollback.
