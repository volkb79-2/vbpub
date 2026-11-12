# IBX live host acceptance (IOB-BL-41 / IOB-BL-42)

Run only on the freshly reinstalled r1002 configured with
`partitioning.iobench_raw_gb: 32`. This runbook has **not** been run here.
Before the study README's setup block (which requires the acknowledgment), run
this read-only preflight in the same shell. Then use its host-root wrapper and
threshold/timer setup, keep that shell open so its EXIT trap restores timers,
and set `OUT` to the approved host-visible evidence directory. Do not run the
README's study-day sequence.

```bash
RAW=/dev/vda5
[[ $(host-escape -- lsblk -dn -o TYPE "$RAW") == part ]]
[[ $(host-escape -- hostname) == r1002 ]]
[[ $(host-escape -- lsblk -dn -o PARTLABEL "$RAW") == iobench-raw ]]
[[ $(host-escape -- blockdev --getsize64 "$RAW") == 34359738368 ]]
export IBX_TARGET_CONFIRMATION="$RAW:34359738368:$(host-escape -- blkid -s PARTUUID -o value "$RAW")"
```

The saved reinstall layout places `/dev/vda5` (PARTLABEL `iobench-raw`) on
`/dev/vda`, the disk that also contains the running system and swap. The raw
probe below must therefore refuse before any write or discard. That is the
expected fail-closed result for this layout; do not override it. It means the
positive raw-target acceptance in BL-41 remains open until the reserved
partition is on a disk separate from system and swap.

## 1. Read-only inventory and raw dry-run

```bash
set -euo pipefail
RAW=/dev/vda5
MOUNT=/mnt/ibx-study
FILE=$MOUNT/target.dat

[[ $(host-escape -- hostname) == r1002 ]]
[[ $(host-escape -- lsblk -dn -o PARTLABEL "$RAW") == iobench-raw ]]
RAW_BYTES=$(host-escape -- blockdev --getsize64 "$RAW")
RAW_PARTUUID=$(host-escape -- blkid -s PARTUUID -o value "$RAW")
[[ $RAW_BYTES == 34359738368 ]]
IBX_TARGET_CONFIRMATION="$RAW:$RAW_BYTES:$RAW_PARTUUID"
confirm_study_target
host-escape -- lsblk -o NAME,TYPE,SIZE,PARTLABEL,FSTYPE,MOUNTPOINTS /dev/vda
FIO_VERSION=$(host-escape -- fio --version)
FIO_HELP=$(host-escape -- fio --cmdhelp=allow_mounted_write)
[[ $FIO_HELP == *allow_mounted_write* ]]
printf 'fio=%s; allow_mounted_write option present\n' "$FIO_VERSION"

raw_dry_run() {
  host-escape -- python3 "$STUDY" run raw-t0 --target "$1" --device "$2" \
    --target-type raw --confirm-target "$3" --confirm-quiet-window \
    --offset 0B --size 24GiB --out "$OUT" \
    --max-io-psi "$IBX_MAX_IO_PSI" --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
    --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE" \
    --dry-run
}
expect_refusal() {
  local expected=$1 status output
  shift
  set +e
  output=$("$@" 2>&1)
  status=$?
  set -e
  if ((status != 2)) || [[ $output != *"$expected"* ]]; then
    printf 'FAIL: wanted exit 2 and %s; got exit %s:\n%s\n' \
      "$expected" "$status" "$output" >&2
    return 1
  fi
  printf 'PASS refusal: %s\n' "$expected"
}

# Current r1002 layout: this real-device dry-run refuses before any effect.
expect_refusal "raw target is on the root disk or contains a running system filesystem" \
  raw_dry_run "$RAW" vda5 "$IBX_TARGET_CONFIRMATION"

# Confirm identity mismatches and a whole-disk target also refuse before effects.
expect_refusal "raw target size does not match --confirm-target" \
  raw_dry_run "$RAW" vda5 "$RAW:$((RAW_BYTES - 512)):$RAW_PARTUUID"
expect_refusal "raw target PARTUUID does not match --confirm-target" \
  raw_dry_run "$RAW" vda5 "$RAW:$RAW_BYTES:00000000-0000-0000-0000-000000000000"
DISK_BYTES=$(host-escape -- blockdev --getsize64 /dev/vda)
expect_refusal "raw target must be a dedicated partition; whole disks are refused" \
  raw_dry_run /dev/vda vda "/dev/vda:$DISK_BYTES:00000000-0000-0000-0000-000000000000"
```

Expected: the four refusals above print `PASS refusal`; the first names the
system-disk guard. A successful raw dry-run on this recorded layout is a
stop-and-review condition. No raw live run, discard, or raw write is part of
this acceptance. Do not manufacture swap or holder state to probe those
branches; fake-sysfs tests cover them.

## 2. Btrfs file and real fio-log acceptance

The following destructive format is permitted only after the exact inventory
checks pass, the partition is unused, and `wipefs -n` reports no signatures.
If any check differs, stop before `mkfs`.

```bash
assert_raw_idle() {
  [[ $(host-escape -- hostname) == r1002 ]]
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
  --out "$OUT" --max-io-psi "$IBX_MAX_IO_PSI" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
  --min-memavailable "$IBX_MIN_MEMAVAILABLE" --min-free "$IBX_MIN_FREE" --dry-run

# Only after dry-run returns {"dry_run":true,"jobs_rendered":33,...}, run fio.
host-escape -- python3 "$STUDY" run file-btrfs --target "$FILE" --device vda5 \
  --target-type btrfs-file --confirm-quiet-window --offset 0B --size 24GiB \
  --out "$OUT" --max-io-psi "$IBX_MAX_IO_PSI" \
  --max-memory-psi "$IBX_MAX_MEMORY_PSI" \
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
repeat the identity, mount, swap, and holder guards before erasing its
filesystem signature to restore the fresh-install raw partition.

```bash
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
