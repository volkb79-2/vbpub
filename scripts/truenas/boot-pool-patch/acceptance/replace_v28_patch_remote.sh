#!/bin/bash
# Replace the single previously registered v28 adapter transactionally.
set -euo pipefail

expected_version=${1:?expected TrueNAS version is required}
new_bundle_sha256=${2:?new bundle SHA-256 is required}
stage=${3:?staging directory is required}
storage_profile=${4:?storage profile is required}
expected_prior_bundle_sha256=${5:?expected prior bundle SHA-256 is required}
patch_dir="$stage/patch"
dist_root=/usr/lib/python3/dist-packages
case "$storage_profile" in
    path-a)
        storage_dataset=boot-pool/user-data
        storage_mount=/mnt/boot-pool/user-data
        ;;
    path-b)
        storage_dataset=boot-disk-data
        storage_mount=/mnt/boot-disk-data
        ;;
    *) echo "unsupported storage profile: $storage_profile" >&2; exit 1 ;;
esac
manifest="$storage_mount/.truenas-boot-pool-patch-v28.json"
backup_root="$storage_mount/.truenas-boot-pool-patch-backups"
replacement_root="$storage_mount/.truenas-boot-pool-patch-replacements"
usr_dataset=""
transaction_dir=""
transaction_started=0
transaction_committed=0
prior_manifest_sha256=""
patched_snapshot_sha256=""

resolve_usr_dataset() {
    local source
    source=$(findmnt -n -o SOURCE --target /usr)
    case "$source" in
        boot-pool/ROOT/*/usr) ;;
        *) echo "unexpected active /usr source: $source" >&2; return 1 ;;
    esac
    [ "$(zfs get -H -o value mountpoint "$source")" = /usr ]
    [ "$(zfs get -H -o value mounted "$source")" = yes ]
    printf '%s\n' "$source"
}

verify_previous_adapter() {
    python3 - "$manifest" "$dist_root" "$backup_root" "$patch_dir/targets.sha256" "$storage_profile" "$expected_prior_bundle_sha256" <<'PY'
import hashlib
import json
import pathlib
import re
import sys
import tarfile

manifest_path, root, backup_root, targets_path = map(pathlib.Path, sys.argv[1:5])
profile, expected_bundle = sys.argv[5:]
record = json.loads(manifest_path.read_text())
if record.get("schema") != 1 or record.get("version") != "28.0.0-MASTER+20261014-230204":
    raise SystemExit("installed manifest is not the registered v28 nightly adapter")
if record.get("storage_profile", "path-a") != profile:
    raise SystemExit("installed adapter storage profile does not match the selected guest")
if record.get("bundle_sha256") != expected_bundle:
    raise SystemExit("installed bundle does not match the expected prior bundle digest")
targets = {}
for line in targets_path.read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    path = pathlib.PurePosixPath(name)
    if (not re.fullmatch(r"[0-9a-f]{64}", digest) or name in targets
            or path.is_absolute() or ".." in path.parts or not path.parts
            or path.parts[0] not in {"middlewared", "truenas_pylibvirt"}):
        raise SystemExit(f"invalid or unsafe target fingerprint: {name!r}")
    targets[name] = digest
previous_targets = record.get("target_before")
if (len(targets) != 15 or not isinstance(previous_targets, dict)
        or len(previous_targets) not in {14, 15}
        or any(name not in targets or targets[name] != digest for name, digest in previous_targets.items())):
    raise SystemExit("installed target baseline is not the exact prior 14-file adapter subset")
helpers = {
    "middlewared/plugins/boot_pool_user_data.py",
    "middlewared/plugins/vm/virtualization_support.py",
    "middlewared/plugins/pool_/boot_pool_mount.py",
}
after, helper_hashes = record.get("target_after"), record.get("helpers")
if not isinstance(after, dict) or set(after) != set(previous_targets):
    raise SystemExit("installed target hash inventory is malformed")
if not isinstance(helper_hashes, dict) or set(helper_hashes) != helpers:
    raise SystemExit("installed helper hash inventory is malformed")
for name, expected in (after | helper_hashes).items():
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"installed file is missing or unsafe: {name}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit(f"installed file changed since patching: {name}")

backup_value = record.get("backup")
if not isinstance(backup_value, str):
    raise SystemExit("manifest has no original-file backup path")
backup = pathlib.Path(backup_value)
if backup.parent != backup_root or not re.fullmatch(r"v28\.[A-Za-z0-9]+", backup.name):
    raise SystemExit("original-file backup path is outside the managed backup root")
if backup.is_symlink() or not backup.is_dir():
    raise SystemExit("original-file backup directory is missing or unsafe")
archive = backup / "targets.tar.gz"
if archive.is_symlink() or not archive.is_file():
    raise SystemExit("original-file backup archive is missing or unsafe")
with tarfile.open(archive, "r:gz") as stream:
    members = stream.getmembers()
    if {member.name for member in members} != set(previous_targets) or len(members) != len(previous_targets):
        raise SystemExit("original-file archive members do not match the target allowlist")
    for member in members:
        if not member.isfile() or member.issym() or member.islnk():
            raise SystemExit(f"original-file archive has a non-regular member: {member.name}")
        content = stream.extractfile(member)
        if content is None or hashlib.sha256(content.read()).hexdigest() != previous_targets[member.name]:
            raise SystemExit(f"original-file archive hash mismatch: {member.name}")
print(backup)
PY
}

verify_guest_workloads_stopped() {
    python3 - <<'PY'
import json
import subprocess


def call_json(*args):
    result = subprocess.run(["midclt", "call", *args], text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(f"cannot verify guest state with midclt {args!r}: {result.stderr.strip()}")
    return json.loads(result.stdout)


for method, label in (("vm.query", "VM"), ("container.query", "container")):
    rows = call_json(method)
    if not isinstance(rows, list):
        raise SystemExit(f"cannot verify {label} state: {method} did not return a list")
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit(f"cannot verify malformed {label} query row: {row!r}")
        identifier, status = row.get("name", row.get("id")), row.get("status")
        if method == "vm.query" and (not isinstance(status, dict) or not isinstance(status.get("state"), str)):
            vm_id = row.get("id")
            if isinstance(vm_id, bool) or not isinstance(vm_id, int) or vm_id <= 0:
                raise SystemExit(f"cannot resolve nullable VM status: {row!r}")
            status = call_json("vm.status", str(vm_id))
        if not isinstance(status, dict) or not isinstance(status.get("state"), str):
            raise SystemExit(f"cannot determine {label} {identifier!r} runtime state: {status!r}")
        if status["state"].strip().upper() != "STOPPED":
            raise SystemExit(f"refusing patch replacement while {label} {identifier!r} is {status['state']!r}")
PY
}

restore_previous_adapter() {
    local rc=$1 rollback_ok=1 temporary_manifest
    set +e
    if [ "$transaction_started" -eq 1 ] && [ "$transaction_committed" -eq 0 ]; then
        systemctl stop middlewared.service >/dev/null 2>&1 || rollback_ok=0
        zfs set readonly=off "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
        mount -o remount,rw /usr >/dev/null 2>&1 || rollback_ok=0
        if [ "$(sha256sum "$transaction_dir/manifest.json" 2>/dev/null | awk '{print $1}')" = "$prior_manifest_sha256" ] \
                && [ "$(sha256sum "$transaction_dir/patched-files.tar.gz" 2>/dev/null | awk '{print $1}')" = "$patched_snapshot_sha256" ] \
                && python3 - "$transaction_dir/rollback-files.json" "$transaction_dir/patched-files.tar.gz" <<'PY'
import hashlib, json, pathlib, sys, tarfile
expected = json.loads(pathlib.Path(sys.argv[1]).read_text())
if not isinstance(expected, dict) or not expected:
    raise SystemExit("rollback hash inventory is malformed")
with tarfile.open(sys.argv[2], "r:gz") as archive:
    members = archive.getmembers()
    if {m.name for m in members} != set(expected) or len(members) != len(expected):
        raise SystemExit("rollback archive member set mismatch")
    for member in members:
        if not member.isfile() or member.issym() or member.islnk():
            raise SystemExit(f"rollback archive has unsafe member: {member.name}")
        content = archive.extractfile(member)
        if content is None or hashlib.sha256(content.read()).hexdigest() != expected[member.name]:
            raise SystemExit(f"rollback archive hash mismatch: {member.name}")
PY
        then
            tar -xzf "$transaction_dir/patched-files.tar.gz" --no-same-owner -C "$dist_root" >/dev/null 2>&1 || rollback_ok=0
        else
            rollback_ok=0
        fi
        temporary_manifest="$manifest.restore.$$"
        install -o root -g root -m 0600 "$transaction_dir/manifest.json" "$temporary_manifest" || rollback_ok=0
        mv -f "$temporary_manifest" "$manifest" || rollback_ok=0
        python3 - "$transaction_dir/rollback-files.json" "$dist_root" <<'PY' || rollback_ok=0
import hashlib, json, pathlib, sys
expected_files = json.loads(pathlib.Path(sys.argv[1]).read_text())
root = pathlib.Path(sys.argv[2])
for name, expected in expected_files.items():
    path = root / name
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit(f"rollback hash mismatch: {name}")
PY
        systemctl start middlewared.service >/dev/null 2>&1 || rollback_ok=0
        systemctl is-active --quiet middlewared.service || rollback_ok=0
        api_ready=0
        for attempt in $(seq 1 60); do
            if midclt call system.info >/dev/null 2>&1; then api_ready=1; break; fi
            sleep 1
        done
        [ "$api_ready" -eq 1 ] || rollback_ok=0
        zfs set readonly=on "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
        mount -o remount,ro /usr >/dev/null 2>&1 || rollback_ok=0
        [ "$(zfs get -H -o value readonly "$usr_dataset" 2>/dev/null)" = on ] || rollback_ok=0
        findmnt -n -o OPTIONS /usr 2>/dev/null | tr ',' '\n' | grep -Fxq ro || rollback_ok=0
        if [ "$rollback_ok" -eq 1 ]; then
            echo "[replace-rollback] restored the previous adapter; recovery snapshot: $transaction_dir" >&2
        else
            echo "[replace-rollback] FAILED; recovery snapshot is at $transaction_dir" >&2
            rc=125
        fi
    fi
    exit "$rc"
}
trap 'restore_previous_adapter $?' ERR
trap 'restore_previous_adapter 130' INT
trap 'restore_previous_adapter 143' TERM

actual_version=$(midclt call system.info | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')
[ "$actual_version" = "$expected_version" ]
[ "$expected_version" = "28.0.0-MASTER+20261014-230204" ]
[[ "$new_bundle_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$expected_prior_bundle_sha256" =~ ^[0-9a-f]{64}$ ]]
[ ! -e /dev/kvm ]
if [ "$storage_profile" = path-a ]; then
    [ ! -e /dev/vda4 ]
else
    [ -b /dev/vda4 ]
fi
systemctl is-active --quiet middlewared.service
verify_guest_workloads_stopped
if [ "$storage_profile" = path-a ]; then
    [ "$(zfs get -H -o value mountpoint "$storage_dataset")" = "$storage_mount" ]
    if [ "$(zfs get -H -o value mounted "$storage_dataset")" != yes ]; then zfs mount "$storage_dataset"; fi
else
    [ "$(zpool list -H -o health boot-disk-data)" = ONLINE ]
    [ "$(zfs get -H -o value mountpoint "$storage_dataset")" = "$storage_mount" ]
    [ "$(zfs get -H -o value mounted "$storage_dataset")" = yes ]
fi
mountpoint -q "$storage_mount"
python3 - "$storage_profile" <<'PY'
import json
import subprocess
import sys

if sys.argv[1] == "path-b":
    result = subprocess.run(["midclt", "call", "pool.query"], text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(f"cannot verify Path B pool through middleware: {result.stderr.strip()}")
    rows = json.loads(result.stdout)
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == "boot-disk-data"]
    if len(matches) != 1 or matches[0].get("status") != "ONLINE":
        raise SystemExit(f"Path B pool must be uniquely registered ONLINE: {matches!r}")
    data = (matches[0].get("topology") or {}).get("data")
    if (not isinstance(data, list) or len(data) != 1
            or data[0].get("path") != "/dev/vda4" or data[0].get("status") != "ONLINE"):
        raise SystemExit(f"Path B pool must be ONLINE on /dev/vda4: {data!r}")
PY
[ ! -L "$manifest" ] && [ -f "$manifest" ]
[ ! -L "$backup_root" ]
usr_dataset=$(resolve_usr_dataset)
[ "$(zfs get -H -o value readonly "$usr_dataset")" = on ]
findmnt -n -o OPTIONS /usr | tr ',' '\n' | grep -Fxq ro
original_backup=$(verify_previous_adapter)
prior_manifest_sha256=$(sha256sum "$manifest" | awk '{print $1}')

if [ -e "$replacement_root" ]; then
    [ -d "$replacement_root" ] && [ ! -L "$replacement_root" ]
else
    install -d -o root -g root -m 0700 "$replacement_root"
fi
transaction_dir=$(mktemp -d "$replacement_root/v28-replace.XXXXXXXX")
chmod 0700 "$transaction_dir"
install -o root -g root -m 0600 "$manifest" "$transaction_dir/manifest.json"
sed -n 's/^[0-9a-f]\{64\}  //p' "$patch_dir/targets.sha256" > "$transaction_dir/files.list"
printf '%s\n' \
    middlewared/plugins/boot_pool_user_data.py \
    middlewared/plugins/vm/virtualization_support.py \
    middlewared/plugins/pool_/boot_pool_mount.py >> "$transaction_dir/files.list"
python3 - "$dist_root" "$transaction_dir/files.list" "$transaction_dir/rollback-files.json" <<'PY'
import hashlib, json, pathlib, sys
root, list_path, output_path = map(pathlib.Path, sys.argv[1:])
expected = {}
for name in list_path.read_text().splitlines():
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise SystemExit(f"unsafe rollback path: {name!r}")
    source = root / name
    if source.is_symlink() or not source.is_file():
        raise SystemExit(f"rollback source is missing or unsafe: {name}")
    expected[name] = hashlib.sha256(source.read_bytes()).hexdigest()
output_path.write_text(json.dumps(expected, sort_keys=True, indent=2) + "\n")
PY
chmod 0600 "$transaction_dir/rollback-files.json"
tar -czf "$transaction_dir/patched-files.tar.gz" -C "$dist_root" -T "$transaction_dir/files.list"
chmod 0600 "$transaction_dir/patched-files.tar.gz"
sha256sum "$transaction_dir/patched-files.tar.gz" > "$transaction_dir/patched-files.tar.gz.sha256"
patched_snapshot_sha256=$(sha256sum "$transaction_dir/patched-files.tar.gz" | awk '{print $1}')
transaction_started=1

systemctl stop middlewared.service
zfs set readonly=off "$usr_dataset"
mount -o remount,rw /usr
tar -xzf "$original_backup/targets.tar.gz" --no-same-owner -C "$dist_root"
rm -f \
    "$dist_root/middlewared/plugins/boot_pool_user_data.py" \
    "$dist_root/middlewared/plugins/vm/virtualization_support.py" \
    "$dist_root/middlewared/plugins/pool_/boot_pool_mount.py" \
    "$manifest"
systemctl start middlewared.service
for attempt in $(seq 1 120); do
    systemctl is-active --quiet middlewared.service && break
    sleep 1
done
systemctl is-active --quiet middlewared.service
for attempt in $(seq 1 120); do
    if midclt call system.info >/dev/null 2>&1; then break; fi
    sleep 1
done
midclt call system.info >/dev/null
zfs set readonly=on "$usr_dataset"
mount -o remount,ro /usr
[ "$(zfs get -H -o value readonly "$usr_dataset")" = on ]
findmnt -n -o OPTIONS /usr | tr ',' '\n' | grep -Fxq ro
echo "[replace] verified previous bundle and restored stock source; rollback snapshot: $transaction_dir"

bash "$patch_dir/apply_v28_patch_remote.sh" "$expected_version" "$new_bundle_sha256" "$stage" "$storage_profile"
transaction_committed=1
trap - ERR INT TERM
echo "[replace] new bundle applied; previous patched state retained at $transaction_dir"
