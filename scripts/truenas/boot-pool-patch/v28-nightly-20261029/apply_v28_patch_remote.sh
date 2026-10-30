#!/bin/bash
# Runs on the disposable TrueNAS guest after the hash-checked bundle is staged.
set -euo pipefail

expected_version=${1:?expected TrueNAS version is required}
bundle_sha256=${2:?bundle SHA-256 is required}
stage=${3:?staging directory is required}
storage_profile=${4:?storage profile is required}
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
usr_dataset=""
readonly_before=""
backup_dir=""
patch_started=0
middleware_stopped=0
usr_mutation_started=0

resolve_usr_dataset() {
    local source
    source=$(findmnt -n -o SOURCE --target /usr) || return 1
    case "$source" in
        boot-pool/ROOT/*/usr) ;;
        *) echo "unexpected active /usr source: $source" >&2; return 1 ;;
    esac
    [ "$(zfs get -H -o value mountpoint "$source")" = /usr ] || {
        echo "active /usr ZFS dataset has unexpected mountpoint: $source" >&2
        return 1
    }
    [ "$(zfs get -H -o value mounted "$source")" = yes ] || {
        echo "active /usr ZFS dataset is not mounted: $source" >&2
        return 1
    }
    printf '%s\n' "$source"
}

rollback() {
    rc=$?
    if [ "${1:-}" = 130 ] || [ "${1:-}" = 143 ]; then
        rc=$1
    fi
    trap - ERR INT TERM
    set +e
    rollback_ok=1
    if [ "$patch_started" -eq 1 ] && [ -n "$backup_dir" ] && [ -s "$backup_dir/targets.tar.gz" ]; then
        systemctl stop middlewared.service >/dev/null 2>&1 || rollback_ok=0
        if [ -n "$usr_dataset" ]; then
            zfs set readonly=off "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
            mount -o remount,rw /usr >/dev/null 2>&1 || rollback_ok=0
            tar -xzf "$backup_dir/targets.tar.gz" -C "$dist_root" >/dev/null 2>&1 || rollback_ok=0
            rm -f "$dist_root/middlewared/plugins/boot_pool_user_data.py" "$dist_root/middlewared/plugins/vm/virtualization_support.py" "$dist_root/middlewared/plugins/pool_/boot_pool_mount.py" "$dist_root/middlewared/plugins/boot_disk_partition.py" "$dist_root/middlewared/plugins/pool_create_schema_v28.json" || rollback_ok=0
            (cd "$dist_root" && sha256sum --check "$patch_dir/targets.sha256") >/dev/null 2>&1 || rollback_ok=0
            [ ! -e "$dist_root/middlewared/plugins/boot_pool_user_data.py" ] || rollback_ok=0
            [ ! -e "$dist_root/middlewared/plugins/vm/virtualization_support.py" ] || rollback_ok=0
            [ ! -e "$dist_root/middlewared/plugins/pool_/boot_pool_mount.py" ] || rollback_ok=0
        elif [ "$middleware_stopped" -eq 1 ]; then
            rollback_ok=0
        fi
    elif [ "$patch_started" -eq 1 ]; then
        rollback_ok=0
    fi
    if [ "$middleware_stopped" -eq 1 ] || [ "$patch_started" -eq 1 ]; then
        systemctl start middlewared.service >/dev/null 2>&1 || rollback_ok=0
        systemctl is-active --quiet middlewared.service || rollback_ok=0
        api_ready=0
        for attempt in $(seq 1 30); do
            if midclt call system.info >/dev/null 2>&1; then api_ready=1; break; fi
            sleep 1
        done
        [ "$api_ready" -eq 1 ] || rollback_ok=0
    fi
    if [ -n "$usr_dataset" ] && [ "$readonly_before" = on ] && [ "$usr_mutation_started" -eq 1 ]; then
        zfs set readonly=on "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
        mount -o remount,ro /usr >/dev/null 2>&1 || rollback_ok=0
        [ "$(zfs get -H -o value readonly "$usr_dataset" 2>/dev/null)" = on ] || rollback_ok=0
        findmnt -n -o OPTIONS /usr 2>/dev/null | tr ',' '\n' | grep -Fxq ro || rollback_ok=0
    elif [ -n "$usr_dataset" ] && [ "$readonly_before" = on ]; then
        [ "$(zfs get -H -o value readonly "$usr_dataset" 2>/dev/null)" = on ] || rollback_ok=0
        findmnt -n -o OPTIONS /usr 2>/dev/null | tr ',' '\n' | grep -Fxq ro || rollback_ok=0
    elif [ -n "$usr_dataset" ] && [ -n "$readonly_before" ]; then
        zfs set readonly="$readonly_before" "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
    fi
    if [ "$rollback_ok" -eq 1 ]; then
        if [ "$patch_started" -eq 1 ] || [ "$usr_mutation_started" -eq 1 ] || [ "$middleware_stopped" -eq 1 ]; then
            printf '[rollback] restored and verified original files, middlewared state, and /usr protection\n' >&2
        else
            printf '[rollback] no source or mount changes had started; original state remains intact\n' >&2
        fi
    else
        printf '[rollback] FAILED to verify complete restoration; inspect the durable backup at %s\n' "$backup_dir" >&2
        rc=125
    fi
    exit "$rc"
}
trap rollback ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

actual_version=$(midclt call system.info | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')
[ "$actual_version" = "$expected_version" ] || { echo "version mismatch: $actual_version" >&2; exit 1; }
python3 - "$patch_dir/adapter.json" "$patch_dir/targets.sha256" "$expected_version" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
manifest_path, targets_path, version = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text())
expected = {
    "id": "v28-nightly-20261029",
    "version": "28.0.0-MASTER+20261029-175210",
    "train": "TrueNAS-28-Nightlies",
    "target_count": 19,
    "target_manifest_sha256": hashlib.sha256(Path(targets_path).read_bytes()).hexdigest(),
}
if version != expected["version"] or manifest != expected:
    raise SystemExit(f"adapter manifest does not match the exact supported v28 build: {manifest!r}")
PY
[ ! -e /dev/kvm ] || { echo 'this test requires /dev/kvm to be absent' >&2; exit 1; }
if [ "$storage_profile" = path-b ]; then
    [ -b /dev/vda4 ] || { echo 'Path B test is missing partition /dev/vda4' >&2; exit 1; }
fi
systemctl is-active --quiet middlewared.service || { echo 'middlewared is not active' >&2; exit 1; }

python3 - <<'PY'
import json
import subprocess


def call_json(*args):
    result = subprocess.run(["midclt", "call", *args], text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(f"cannot verify guest state with midclt {args!r}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"midclt {args!r} returned invalid JSON: {exc}") from exc


for method, label in (("vm.query", "VM"), ("container.query", "container")):
    rows = call_json(method)
    if not isinstance(rows, list):
        raise SystemExit(f"cannot verify {label} state: {method} did not return a list")
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit(f"cannot verify malformed {label} query row: {row!r}")
        identifier = row.get("name", row.get("id"))
        status = row.get("status")
        if (
            method == "vm.query"
            and (not isinstance(status, dict) or not isinstance(status.get("state"), str))
        ):
            vm_id = row.get("id")
            if isinstance(vm_id, bool) or not isinstance(vm_id, int) or vm_id <= 0:
                raise SystemExit(f"cannot resolve nullable VM status without a valid id: {row!r}")
            status = call_json("vm.status", str(vm_id))
        if not isinstance(status, dict) or not isinstance(status.get("state"), str):
            raise SystemExit(f"cannot determine {label} {identifier!r} runtime state: {status!r}")
        state = status["state"].strip().upper()
        if state != "STOPPED":
            raise SystemExit(f"refusing to restart middleware while {label} {identifier!r} is {state!r}")
PY

if [ "$storage_profile" = path-a ]; then
    [ "$(zfs get -H -o value mountpoint "$storage_dataset")" = "$storage_mount" ] || {
        echo 'Path A boot-pool/user-data has an unexpected mountpoint' >&2; exit 1;
    }
    if [ "$(zfs get -H -o value mounted "$storage_dataset")" != yes ]; then
        zfs mount "$storage_dataset"
    fi
else
    [ "$(zpool list -H -o health boot-disk-data)" = ONLINE ] || {
        echo 'Path B boot-disk-data is not ONLINE' >&2; exit 1;
    }
    [ "$(zfs get -H -o value mountpoint "$storage_dataset")" = "$storage_mount" ] || {
        echo 'Path B boot-disk-data has an unexpected mountpoint' >&2; exit 1;
    }
    [ "$(zfs get -H -o value mounted "$storage_dataset")" = yes ] || {
        echo 'Path B boot-disk-data is not mounted' >&2; exit 1;
    }
fi
mountpoint -q "$storage_mount" || { echo "$storage_profile storage root is not mounted" >&2; exit 1; }
python3 - "$storage_profile" <<'PY'
import json
import subprocess
import sys

profile = sys.argv[1]
if profile == "path-b":
    result = subprocess.run(["midclt", "call", "pool.query"], text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(f"cannot verify Path B pool through middleware: {result.stderr.strip()}")
    rows = json.loads(result.stdout)
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == "boot-disk-data"]
    if len(matches) != 1 or matches[0].get("status") != "ONLINE":
        raise SystemExit(f"Path B pool must be uniquely registered ONLINE: {matches!r}")
    data = (matches[0].get("topology") or {}).get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise SystemExit(f"Path B pool must have one data vdev: {data!r}")
    if data[0].get("path") != "/dev/vda4" or data[0].get("status") != "ONLINE":
        raise SystemExit(f"Path B pool is not ONLINE on /dev/vda4: {data[0]!r}")
PY
[ ! -L "$manifest" ] || { echo 'patch manifest must not be a symlink' >&2; exit 1; }
[ ! -L "$backup_root" ] || { echo 'patch backup directory must not be a symlink' >&2; exit 1; }
[ ! -e "$backup_root" ] || [ -d "$backup_root" ] || { echo 'patch backup path exists but is not a directory' >&2; exit 1; }

if [ -f "$manifest" ]; then
    python3 - "$manifest" "$dist_root" "$expected_version" "$bundle_sha256" "$patch_dir/targets.sha256" "$storage_profile" <<'PY'
import hashlib
import json
import pathlib
import sys
manifest_path, root, version, bundle_hash, targets_path, profile = sys.argv[1:]
record = json.loads(pathlib.Path(manifest_path).read_text())
assert record.get("schema") == 1 and record.get("version") == version
assert record.get("storage_profile", "path-a") == profile
assert record.get("bundle_sha256") == bundle_hash
targets = {}
for line in pathlib.Path(targets_path).read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    targets[name] = digest
assert record.get("target_before") == targets
after = record.get("target_after")
helpers = record.get("helpers")
expected_helpers = {
    "middlewared/plugins/boot_pool_user_data.py",
    "middlewared/plugins/vm/virtualization_support.py",
    "middlewared/plugins/pool_/boot_pool_mount.py",
    "middlewared/plugins/boot_disk_partition.py",
    "middlewared/plugins/pool_create_schema_v28.json",
}
assert isinstance(after, dict) and set(after) == set(targets)
assert isinstance(helpers, dict) and set(helpers) == expected_helpers
for name, digest in (after | helpers).items():
    actual = hashlib.sha256((pathlib.Path(root) / name).read_bytes()).hexdigest()
    assert actual == digest, f"installed file hash changed: {name}"
print("installed manifest and all patched/helper hashes verified")
PY
    systemctl is-active --quiet middlewared.service
    usr_dataset=$(resolve_usr_dataset)
    [ "$(zfs get -H -o value readonly "$usr_dataset")" = on ]
    findmnt -n -o OPTIONS /usr | tr ',' '\n' | grep -Fxq ro
    echo '[patch] already installed; recorded release, bundle, target, and helper hashes match'
    trap - ERR INT TERM
    rm -rf "$stage"
    exit 0
fi

usr_dataset=$(resolve_usr_dataset)
readonly_before=$(zfs get -H -o value readonly "$usr_dataset")
[ "$readonly_before" = on ] || { echo "/usr dataset is not read-only: $readonly_before" >&2; exit 1; }
findmnt -n -o OPTIONS /usr | tr ',' '\n' | grep -Fxq ro || { echo '/usr is not mounted read-only' >&2; exit 1; }

cd "$dist_root"
python3 - "$patch_dir/targets.sha256" <<'PY'
import hashlib
from pathlib import Path, PurePosixPath
import sys

manifest = Path(sys.argv[1])
root = Path.cwd()
issues = []
seen = set()
for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
    fields = line.split(maxsplit=1)
    if len(fields) != 2:
        issues.append(f"INVALID {manifest}:{number}")
        continue
    expected, name = fields
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] not in {"middlewared", "truenas_pylibvirt"}:
        issues.append(f"UNSAFE {name} expected=<relative middleware target> actual=<invalid path>")
        continue
    if name in seen:
        issues.append(f"DUPLICATE {name} expected=<one target> actual=<duplicate>")
        continue
    seen.add(name)
    path = root.joinpath(*relative.parts)
    current = root
    unsafe = False
    for part in relative.parts:
        current /= part
        unsafe = unsafe or current.is_symlink()
    if unsafe or not path.is_file():
        issues.append(f"MISSING {name} expected={expected} actual=<missing-or-symlink>")
        continue
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        issues.append(f"HASH {name} expected={expected} actual={actual}")
if issues:
    print("pre-patch source fingerprint mismatch; refusing every patch hunk:", file=sys.stderr)
    print("\n".join(issues), file=sys.stderr)
    raise SystemExit(1)
PY
sha256sum --check --strict "$patch_dir/targets.sha256"
for helper in middlewared/plugins/boot_pool_user_data.py middlewared/plugins/vm/virtualization_support.py middlewared/plugins/pool_/boot_pool_mount.py middlewared/plugins/boot_disk_partition.py middlewared/plugins/pool_create_schema_v28.json; do
    [ ! -e "$helper" ] || { echo "unrecorded helper already exists: $helper" >&2; exit 1; }
done
for patch_file in storage.patch middleware.patch boot-mount.patch ui-pools.patch; do
    patch --dry-run --batch --fuzz=0 -p1 < "$patch_dir/$patch_file"
done

install -d -m 0700 "$backup_root"
backup_dir=$(mktemp -d "$backup_root/v28.XXXXXXXX")
chmod 0700 "$backup_dir"
tar -czf "$backup_dir/targets.tar.gz" -T "$patch_dir/targets.list"
chmod 0600 "$backup_dir/targets.tar.gz"
usr_mutation_started=1
zfs set readonly=off "$usr_dataset"
mount -o remount,rw /usr
patch_started=1
systemctl stop middlewared.service
middleware_stopped=1
for patch_file in storage.patch middleware.patch boot-mount.patch ui-pools.patch; do
    patch --batch --fuzz=0 -p1 < "$patch_dir/$patch_file"
done
install -o root -g root -m 0644 "$stage/shared/boot_pool_user_data.py" middlewared/plugins/
install -o root -g root -m 0644 "$stage/shared/virtualization_support.py" middlewared/plugins/vm/
install -o root -g root -m 0644 "$patch_dir/boot_pool_mount.py" middlewared/plugins/pool_/
install -o root -g root -m 0644 "$stage/shared/boot_disk_partition.py" middlewared/plugins/
install -o root -g root -m 0644 "$stage/shared/pool_create_schema_v28.json" middlewared/plugins/
for patch_file in storage.patch middleware.patch boot-mount.patch ui-pools.patch; do
    patch --dry-run --batch --fuzz=0 --reverse -p1 < "$patch_dir/$patch_file"
done
mapfile -t python_targets < <(sed -n 's/^[0-9a-f]\{64\}  //p' "$patch_dir/targets.sha256")
python3 -m py_compile "${python_targets[@]}" middlewared/plugins/boot_pool_user_data.py middlewared/plugins/vm/virtualization_support.py middlewared/plugins/pool_/boot_pool_mount.py middlewared/plugins/boot_disk_partition.py
systemctl start middlewared.service
middleware_stopped=0
for attempt in $(seq 1 150); do
    systemctl is-active --quiet middlewared.service && break
    sleep 2
done
systemctl is-active --quiet middlewared.service
api_ready=0
for attempt in $(seq 1 150); do
    if midclt call system.info >/dev/null 2>&1; then api_ready=1; break; fi
    sleep 2
done
[ "$api_ready" -eq 1 ] || { echo 'middleware API did not return after patch restart' >&2; exit 1; }
midclt call system.info | python3 -c 'import json,sys; value=json.load(sys.stdin); expected=sys.argv[1]; actual=value.get("version"); assert actual == expected, f"expected {expected}, got {actual!r}"' "$expected_version"
midclt call vm.virtualization_details | python3 -c 'import json,sys; value=json.load(sys.stdin); assert value.get("supported") is True, f"TCG capability not advertised: {value!r}"'
midclt call vm.flags | python3 -c 'import json,sys; flags=json.load(sys.stdin); assert not any(flags.values()), f"hardware virtualization flags changed: {flags!r}"'
selected_pool=boot-pool
if [ "$storage_profile" = path-b ]; then selected_pool=boot-disk-data; fi
midclt call container.pool_choices | python3 -c 'import json,sys; choices=json.load(sys.stdin); expected=sys.argv[1]; assert expected in choices, f"{expected} omitted from container choices: {choices!r}"' "$selected_pool"
zfs set readonly=on "$usr_dataset"
mount -o remount,ro /usr

python3 - "$manifest" "$dist_root" "$expected_version" "$bundle_sha256" "$backup_dir" "$patch_dir/targets.sha256" "$storage_profile" <<'PY'
from datetime import datetime, timezone
import hashlib
import json
import os
import pathlib
import sys
import tempfile
manifest_path, root, version, bundle_hash, backup, targets_path, profile = sys.argv[1:]
root_path = pathlib.Path(root)
targets = {}
for line in pathlib.Path(targets_path).read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    targets[name] = digest
helpers = {
    "middlewared/plugins/boot_pool_user_data.py",
    "middlewared/plugins/vm/virtualization_support.py",
    "middlewared/plugins/pool_/boot_pool_mount.py",
    "middlewared/plugins/boot_disk_partition.py",
    "middlewared/plugins/pool_create_schema_v28.json",
}
after = {name: hashlib.sha256((root_path / name).read_bytes()).hexdigest() for name in targets}
helper_hashes = {name: hashlib.sha256((root_path / name).read_bytes()).hexdigest() for name in helpers}
record = {"schema": 1, "version": version, "storage_profile": profile, "bundle_sha256": bundle_hash, "target_before": targets, "target_after": after, "helpers": helper_hashes, "backup": backup, "installed_at": datetime.now(timezone.utc).isoformat()}
destination = pathlib.Path(manifest_path)
fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", dir=destination.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(record, output, sort_keys=True, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
except BaseException:
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
PY
trap - ERR INT TERM
rm -rf "$stage"
echo "[patch] v28 adapter applied; original files backed up at $backup_dir; /usr is read-only"
