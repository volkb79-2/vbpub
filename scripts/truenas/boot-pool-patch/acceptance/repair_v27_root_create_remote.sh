#!/usr/bin/env bash
# Repair the narrowly identified v27 Path A root-create source on a clean guest.
# Both modes preserve a rollback copy under the persistent boot environment.
set -euo pipefail

expected_version=${1:?expected TrueNAS version is required}
prior_bundle_sha256=${2:?prior v27 bundle SHA-256 is required}
new_bundle_sha256=${3:?replacement v27 bundle SHA-256 is required}
storage_profile=${4:?storage profile is required}
stage=${5:?staging directory is required}
repair_stage=${6:?repair stage is required}
prior_dataset_sha256=${7:-}

dist_root=/usr/lib/python3/dist-packages
dataset_py="$dist_root/middlewared/plugins/pool_/dataset.py"
marker=/root/v27-boot-pool-patch-install.json
prior_bundle="$stage/prior/bundle.tar.gz"
new_bundle="$stage/bundle.tar.gz"
replacement_root=/root/.truenas-v27-patch-replacements
usr_dataset=
transaction_dir=
transaction_started=0
transaction_committed=0
old_file_sha256=
old_marker_sha256=

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    restore_previous_source 1
}

restore_previous_source() {
    local rc=$1 rollback_ok=1
    set +e
    if [ "$transaction_started" -eq 1 ] && [ "$transaction_committed" -eq 0 ]; then
        systemctl stop middlewared.service >/dev/null 2>&1 || rollback_ok=0
        zfs set readonly=off "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
        mount -o remount,rw /usr >/dev/null 2>&1 || rollback_ok=0
        cp --archive -- "$transaction_dir/dataset.py" "$dataset_py" || rollback_ok=0
        install -o root -g root -m 0600 "$transaction_dir/install-record.json" "$marker.restore.$$" || rollback_ok=0
        mv -f "$marker.restore.$$" "$marker" || rollback_ok=0
        [ "$(sha256sum "$dataset_py" 2>/dev/null | awk '{print $1}')" = "$old_file_sha256" ] || rollback_ok=0
        [ "$(sha256sum "$marker" 2>/dev/null | awk '{print $1}')" = "$old_marker_sha256" ] || rollback_ok=0
        systemctl start middlewared.service >/dev/null 2>&1 || rollback_ok=0
        systemctl is-active --quiet middlewared.service || rollback_ok=0
        api_ready=0
        for attempt in $(seq 1 120); do
            if midclt call system.info >/dev/null 2>&1; then api_ready=1; break; fi
            sleep 1
        done
        [ "$api_ready" -eq 1 ] || rollback_ok=0
        zfs set readonly=on "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
        mount -o remount,ro /usr >/dev/null 2>&1 || rollback_ok=0
        [ "$(zfs get -H -o value readonly "$usr_dataset" 2>/dev/null)" = on ] || rollback_ok=0
        findmnt -n -o OPTIONS --target /usr 2>/dev/null | tr ',' '\n' | grep -Fxq ro || rollback_ok=0
        if [ "$rollback_ok" -eq 1 ]; then
            echo "[repair-rollback] restored the prior v27 source; recovery snapshot: $transaction_dir" >&2
        else
            echo "[repair-rollback] FAILED; recovery snapshot is at $transaction_dir" >&2
            rc=125
        fi
    fi
    exit "$rc"
}
trap 'restore_previous_source $?' ERR
trap 'restore_previous_source 130' INT
trap 'restore_previous_source 143' TERM

[ "$expected_version" = "27.0.0-RC.1" ] || fail "unsupported TrueNAS version: $expected_version"
[ "$storage_profile" = "path-a" ] || fail "root-create repair only supports Path A: $storage_profile"
[[ "$prior_bundle_sha256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid prior bundle SHA-256'
[[ "$new_bundle_sha256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid replacement bundle SHA-256'
case "$repair_stage" in
    root-create)
        [ -z "$prior_dataset_sha256" ] || fail 'root-create repair does not accept a prior dataset digest'
        ;;
    parent-mountpoint)
        [[ "$prior_dataset_sha256" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid prior dataset SHA-256'
        ;;
    *) fail "unsupported repair stage: $repair_stage" ;;
esac
[ -f "$prior_bundle" ] && [ ! -L "$prior_bundle" ] || fail 'prior v27 bundle is missing or unsafe'
[ "$(sha256sum "$prior_bundle" | awk '{print $1}')" = "$prior_bundle_sha256" ] || fail 'prior bundle digest mismatch'
[ -f "$new_bundle" ] && [ ! -L "$new_bundle" ] || fail 'replacement v27 bundle is missing or unsafe'
[ "$(sha256sum "$new_bundle" | awk '{print $1}')" = "$new_bundle_sha256" ] || fail 'replacement bundle digest mismatch'
[ "$(midclt call system.info | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')" = "$expected_version" ] || fail 'guest version differs from the selected patch'
[ ! -e /dev/kvm ] || fail '/dev/kvm exists; refusing this TCG-specific repair'
[ -f "$marker" ] && [ ! -L "$marker" ] || fail 'v27 install record is missing or unsafe'
python3 - "$marker" "$repair_stage" "$prior_bundle_sha256" "$prior_dataset_sha256" <<'PY'
import json, pathlib, sys
marker, stage, prior_bundle, prior_dataset = sys.argv[1:]
record = json.loads(pathlib.Path(marker).read_text())
fresh_record = {
    "version": "27.0.0-RC.1",
    "patch": "v27",
    "supported": True,
    "kvm_device": False,
}
if stage == "root-create":
    if record != fresh_record:
        raise SystemExit(f"v27 install record differs from the expected fresh-install record: {record!r}")
elif stage == "parent-mountpoint":
    expected = {
        **fresh_record,
        "bundle_sha256": prior_bundle,
        "root_create_guard": "root-excluded-from-descendant-readiness-v1",
        "repaired_from_bundle_sha256": "61331d7a4f2afd02fbe4e91e13436db70c2028639ba8d7b99a5659a2e9974b7e",
        "repaired_dataset_py_sha256": prior_dataset,
    }
    if record != expected:
        raise SystemExit(f"v27 install record differs from the exact prior root-create repair: {record!r}")
else:
    raise SystemExit(f"unsupported repair stage {stage!r}")
PY
! zfs list -H -o name boot-pool/user-data >/dev/null 2>&1 || fail 'boot-pool/user-data already exists; refusing repair after storage setup'
[ ! -e /mnt/boot-pool/user-data ] || fail 'the Path A user-data mountpoint already exists; refusing repair'
! zpool list -H -o name | grep -Fxq boot-disk-data || fail 'boot-disk-data exists; refusing repair after storage setup'
containers=$(midclt call container.query)
python3 -c 'import json,sys; rows=json.loads(sys.argv[1]); assert isinstance(rows,list) and not rows, f"expected no containers, got {rows!r}"' "$containers" || fail 'container records exist; refusing repair'
vms=$(midclt call vm.query)
python3 -c 'import json,sys; rows=json.loads(sys.argv[1]); assert isinstance(rows,list) and not rows, f"expected no VMs, got {rows!r}"' "$vms" || fail 'VM records exist; refusing repair'
[ "$(zpool list -H -o health boot-pool)" = ONLINE ] || fail 'boot-pool is not ONLINE'
systemctl is-active --quiet middlewared.service || fail 'middlewared is not active'

usr_dataset=$(findmnt -n -o SOURCE --target /usr)
case "$usr_dataset" in
    boot-pool/ROOT/*/usr) ;;
    *) fail "unexpected active /usr source: $usr_dataset" ;;
esac
[ "$(zfs get -H -o value readonly "$usr_dataset")" = on ] || fail '/usr dataset is not ZFS read-only'
findmnt -n -o OPTIONS --target /usr | tr ',' '\n' | grep -Fxq ro || fail '/usr is not mounted read-only'
root_source=$(findmnt -n -o SOURCE --target /root)
case "$root_source" in
    boot-pool/ROOT/*) ;;
    *) fail "repair backup path is not on the persistent boot environment: $root_source" ;;
esac

cd "$dist_root"
tar -tzf "$prior_bundle" >/dev/null
tar -xzf "$prior_bundle" -C "$stage/prior"
for unchanged in \
    v27/middleware.patch v27/libvirt.patch v27/boot-mount.patch v27/targets.sha256 \
    shared/boot_pool_user_data.py shared/virtualization_support.py; do
    cmp -s "$stage/prior/$unchanged" "$stage/$unchanged" || fail "repair bundle changed unrelated patch input: $unchanged"
done
if [ "$repair_stage" = root-create ]; then
    grep -Fq '+        if user_data_root and is_user_data_namespace_path(' \
        "$stage/prior/v27/storage.patch" || fail 'prior bundle does not contain the expected root-readiness condition'
else
    grep -Fq "+        if user_data_root and not is_user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID) and is_user_data_namespace_path(" \
        "$stage/prior/v27/storage.patch" || fail 'prior bundle does not contain the installed root-create exemption'
fi
grep -Fq "+        if user_data_root and not is_user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID) and is_user_data_namespace_path(" \
    "$stage/v27/storage.patch" || fail 'replacement bundle does not contain the root-create exemption'
grep -Fq '+            parent_mp = None' "$stage/v27/storage.patch" || fail 'replacement bundle does not skip the root parent mountpoint=none stat'
cmp -s "$stage/prior/shared/boot_pool_user_data.py" "$dist_root/middlewared/plugins/boot_pool_user_data.py" || fail 'installed shared v27 policy differs from the prior bundle'
cmp -s "$stage/prior/shared/virtualization_support.py" "$dist_root/middlewared/plugins/vm/virtualization_support.py" || fail 'installed shared virtualization helper differs from the prior bundle'
python3 - "$dataset_py" "$repair_stage" "$prior_dataset_sha256" <<'PY'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
stage, prior_dataset = sys.argv[2:]
source = path.read_text()
if stage == "root-create":
    old_root = """        user_data_root = user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID)
        if user_data_root and is_user_data_namespace_path(
            data['name'], BOOT_POOL_NAME_VALID, INTERNAL_DATASETS
        ) and not await self.boot_pool_user_data_root_is_ready(data['name']):"""
    if source.count(old_root) != 1:
        raise SystemExit(f"installed v27 dataset source has {source.count(old_root)} copies of the exact old root guard")
elif stage == "parent-mountpoint":
    old_root = """        user_data_root = user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID)
        if user_data_root and not is_user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID) and is_user_data_namespace_path(
            data['name'], BOOT_POOL_NAME_VALID, INTERNAL_DATASETS
        ) and not await self.boot_pool_user_data_root_is_ready(data['name']):"""
    if old_root not in source:
        raise SystemExit("installed v27 dataset source lacks the expected root-create exemption")
    actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_digest != prior_dataset:
        raise SystemExit(f"installed dataset.py digest differs from the expected prior source: {actual_digest}")
else:
    raise SystemExit(f"unsupported repair stage {stage!r}")
old_parent = """        parent_mp = parent_ds['mountpoint']
        if parent_ds['locked'] or not parent_mp:"""
if source.count(old_parent) != 1:
    raise SystemExit(f"installed v27 dataset source has {source.count(old_parent)} copies of the expected parent mountpoint block")
if "zprops['mountpoint'] = f\"/mnt/{data['name']}\"" not in source:
    raise SystemExit("installed v27 dataset source lacks the pinned Path A root mountpoint")
PY

if [ -e "$replacement_root" ]; then
    [ -d "$replacement_root" ] && [ ! -L "$replacement_root" ] || fail 'repair archive root is unsafe'
else
    install -o root -g root -m 0700 -d "$replacement_root"
fi
transaction_dir=$(mktemp -d "$replacement_root/${repair_stage}.XXXXXXXX")
chmod 0700 "$transaction_dir"
install -o root -g root -m 0600 "$dataset_py" "$transaction_dir/dataset.py"
install -o root -g root -m 0600 "$marker" "$transaction_dir/install-record.json"
old_file_sha256=$(sha256sum "$dataset_py" | awk '{print $1}')
old_marker_sha256=$(sha256sum "$marker" | awk '{print $1}')
printf '%s\n' "$old_file_sha256" > "$transaction_dir/dataset.py.sha256"
printf '%s\n' "$old_marker_sha256" > "$transaction_dir/install-record.json.sha256"

transaction_started=1
systemctl stop middlewared.service
zfs set readonly=off "$usr_dataset"
mount -o remount,rw /usr
python3 - "$dataset_py" "$new_bundle_sha256" "$prior_bundle_sha256" "$repair_stage" <<'PY'
import hashlib, json, os, pathlib, stat, sys, tempfile
path = pathlib.Path(sys.argv[1])
new_bundle, prior_bundle, stage = sys.argv[2:]
old_root = """        user_data_root = user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID)
        if user_data_root and is_user_data_namespace_path(
            data['name'], BOOT_POOL_NAME_VALID, INTERNAL_DATASETS
        ) and not await self.boot_pool_user_data_root_is_ready(data['name']):"""
new_root = """        user_data_root = user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID)
        if user_data_root and not is_user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID) and is_user_data_namespace_path(
            data['name'], BOOT_POOL_NAME_VALID, INTERNAL_DATASETS
        ) and not await self.boot_pool_user_data_root_is_ready(data['name']):"""
old_parent = """        parent_mp = parent_ds['mountpoint']
        if parent_ds['locked'] or not parent_mp:"""
new_parent = """        parent_mp = parent_ds['mountpoint']
        if is_user_data_namespace_root(data['name'], BOOT_POOL_NAME_VALID):
            # boot-pool uses mountpoint=none; it is not a parent path to stat.
            parent_mp = None
        if parent_ds['locked'] or not parent_mp:"""
source = path.read_text()
if stage == "root-create":
    if source.count(old_root) != 1:
        raise SystemExit("installed v27 root-readiness guard changed after preflight")
    updated = source.replace(old_root, new_root, 1)
elif stage == "parent-mountpoint":
    if new_root not in source:
        raise SystemExit("installed v27 root-create exemption changed after preflight")
    updated = source
else:
    raise SystemExit(f"unsupported repair stage {stage!r}")
if updated.count(old_parent) != 1:
    raise SystemExit("installed v27 parent mountpoint block changed after preflight")
updated = updated.replace(old_parent, new_parent, 1)
metadata = path.stat()
fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
    os.fchown(fd, metadata.st_uid, metadata.st_gid)
    with os.fdopen(fd, "w") as stream:
        stream.write(updated)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
record_path = pathlib.Path("/root/v27-boot-pool-patch-install.json")
record = json.loads(record_path.read_text())
dataset_digest = hashlib.sha256(path.read_bytes()).hexdigest()
record["bundle_sha256"] = new_bundle
record["root_create_guard"] = "root-excluded-from-descendant-readiness-v1"
record["root_parent_mountpoint_guard"] = "root-create-skips-parent-mountpoint-none-v1"
if stage == "root-create":
    record["repaired_from_bundle_sha256"] = prior_bundle
    record["repaired_dataset_py_sha256"] = dataset_digest
else:
    record["parent_mountpoint_repaired_from_bundle_sha256"] = prior_bundle
    record["parent_mountpoint_repaired_dataset_py_sha256"] = dataset_digest
record_fd, record_temporary = tempfile.mkstemp(prefix=".v27-patch-install.", dir=record_path.parent)
try:
    os.fchmod(record_fd, 0o600)
    with os.fdopen(record_fd, "w") as stream:
        json.dump(record, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(record_temporary, record_path)
    directory_fd = os.open(record_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
except BaseException:
    try:
        os.unlink(record_temporary)
    except FileNotFoundError:
        pass
    raise
PY
python3 -m py_compile "$dataset_py"
systemctl start middlewared.service
for attempt in $(seq 1 120); do
    systemctl is-active --quiet middlewared.service && break
    sleep 1
done
systemctl is-active --quiet middlewared.service || fail 'middlewared did not restart after root-create repair'
for attempt in $(seq 1 120); do
    if midclt call system.info >/dev/null 2>&1; then break; fi
    sleep 1
done
midclt call system.info >/dev/null
python3 - <<'PY'
import json, subprocess
details = json.loads(subprocess.run(["midclt", "call", "vm.virtualization_details"], check=True, text=True, capture_output=True).stdout)
if details.get("supported") is not True:
    raise SystemExit(f"patched TCG backend is not supported: {details!r}")
flags = json.loads(subprocess.run(["midclt", "call", "vm.flags"], check=True, text=True, capture_output=True).stdout)
if not isinstance(flags, dict) or any(flags.values()):
    raise SystemExit(f"TCG patch exposed hardware virtualization flags: {flags!r}")
PY
[ ! -e /dev/kvm ] || fail '/dev/kvm appeared after root-create repair'
zfs set readonly=on "$usr_dataset"
mount -o remount,ro /usr
[ "$(zfs get -H -o value readonly "$usr_dataset")" = on ] || fail '/usr dataset did not return to read-only'
findmnt -n -o OPTIONS --target /usr | tr ',' '\n' | grep -Fxq ro || fail '/usr did not return to a read-only mount'
transaction_committed=1
trap - ERR INT TERM
echo "[repair] applied v27 $repair_stage root-create correction; prior source retained at $transaction_dir"
