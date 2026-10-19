#!/usr/bin/env bash
# Repair the exact v27 Path A zvol-form failure without changing its UI-created root.
set -euo pipefail

version=${1:?expected TrueNAS version is required}
prior_bundle_sha=${2:?prior bundle SHA-256 is required}
new_bundle_sha=${3:?replacement bundle SHA-256 is required}
profile=${4:?storage profile is required}
stage=${5:?staging directory is required}
prior_source_sha=${6:?prior dataset_info.py SHA-256 is required}
dist_root=/usr/lib/python3/dist-packages
source_file="$dist_root/middlewared/plugins/pool_/dataset_info.py"
marker=/root/v27-boot-pool-patch-install.json
prior_bundle="$stage/prior/bundle.tar.gz"
new_bundle="$stage/bundle.tar.gz"
archive_root=/root/.truenas-v27-patch-replacements
usr_dataset=
transaction_dir=
transaction_started=0
transaction_committed=0
old_source_sha=
old_marker_sha=

restore_previous_source() {
    local rc=$1 rollback_ok=1
    set +e
    if [ "$transaction_started" -eq 1 ] && [ "$transaction_committed" -eq 0 ]; then
        systemctl stop middlewared.service >/dev/null 2>&1 || rollback_ok=0
        zfs set readonly=off "$usr_dataset" >/dev/null 2>&1 || rollback_ok=0
        mount -o remount,rw /usr >/dev/null 2>&1 || rollback_ok=0
        cp --archive -- "$transaction_dir/dataset_info.py" "$source_file" || rollback_ok=0
        install -o root -g root -m 0600 "$transaction_dir/install-record.json" "$marker.restore.$$" || rollback_ok=0
        mv -f "$marker.restore.$$" "$marker" || rollback_ok=0
        [ "$(sha256sum "$source_file" 2>/dev/null | awk '{print $1}')" = "$old_source_sha" ] || rollback_ok=0
        [ "$(sha256sum "$marker" 2>/dev/null | awk '{print $1}')" = "$old_marker_sha" ] || rollback_ok=0
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
            echo "[repair-rollback] restored the prior source; recovery snapshot: $transaction_dir" >&2
        else
            echo "[repair-rollback] FAILED; recovery snapshot: $transaction_dir" >&2
            rc=125
        fi
    fi
    exit "$rc"
}

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    restore_previous_source 1
}
trap 'restore_previous_source $?' ERR
trap 'restore_previous_source 130' INT
trap 'restore_previous_source 143' TERM

[ "$(id -u)" = 0 ] || fail 'run as root inside the disposable TrueNAS guest'
[ "$version" = "27.0.0-RC.1" ] || fail "unsupported TrueNAS version: $version"
[ "$profile" = "path-a" ] || fail "zvol blocksize repair only supports Path A: $profile"
[[ "$prior_bundle_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid prior bundle SHA-256'
[[ "$new_bundle_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid replacement bundle SHA-256'
[[ "$prior_source_sha" =~ ^[0-9a-f]{64}$ ]] || fail 'invalid prior dataset_info.py SHA-256'
[ -f "$prior_bundle" ] && [ ! -L "$prior_bundle" ] || fail 'prior bundle is missing or unsafe'
[ "$(sha256sum "$prior_bundle" | awk '{print $1}')" = "$prior_bundle_sha" ] || fail 'prior bundle digest mismatch'
[ -f "$new_bundle" ] && [ ! -L "$new_bundle" ] || fail 'replacement bundle is missing or unsafe'
[ "$(sha256sum "$new_bundle" | awk '{print $1}')" = "$new_bundle_sha" ] || fail 'replacement bundle digest mismatch'
[ "$(midclt call system.info | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')" = "$version" ] || fail 'guest version differs from the selected patch'
[ ! -e /dev/kvm ] || fail '/dev/kvm exists; refusing this TCG-specific repair'
[ -f "$marker" ] && [ ! -L "$marker" ] || fail 'v27 install record is missing or unsafe'
python3 - "$marker" "$prior_bundle_sha" <<'PY'
import json, pathlib, sys
marker, prior_bundle = sys.argv[1:]
record = json.loads(pathlib.Path(marker).read_text())
expected = {
    "version": "27.0.0-RC.1",
    "patch": "v27",
    "supported": True,
    "kvm_device": False,
    "bundle_sha256": prior_bundle,
    "root_create_guard": "root-excluded-from-descendant-readiness-v1",
    "repaired_from_bundle_sha256": "61331d7a4f2afd02fbe4e91e13436db70c2028639ba8d7b99a5659a2e9974b7e",
    "repaired_dataset_py_sha256": "1cd5b385da5175980c96cdf1697888eb8cccd580bf97de935d7cfb889f021232",
    "root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1",
    "parent_mountpoint_repaired_from_bundle_sha256": "4c72faac25ce748b197d118e0b036642518bf44d23d06e6f24ac738b3ed8bd05",
    "parent_mountpoint_repaired_dataset_py_sha256": "82e997cefaaac4e20623c11808d8b30ef3b2372fe2b11eeb86bebc5a785115cf",
}
if record != expected:
    raise SystemExit(f"install record differs from the exact r4 Path A state: {record!r}")
PY
[ "$(zpool list -H -o health boot-pool)" = ONLINE ] || fail 'boot-pool is not ONLINE'
pool_names=$(zpool list -H -o name)
if printf '%s\n' "$pool_names" | grep -Fxq boot-disk-data; then
    fail 'boot-disk-data exists; refusing Path A repair'
fi
root_properties=$(zfs get -H -p -o value quota,mountpoint,canmount boot-pool/user-data)
[ "$root_properties" = $'2147483648\n/mnt/boot-pool/user-data\non' ] || fail "UI-created root has unexpected properties: $root_properties"
[ "$(findmnt -n -o SOURCE --target /mnt/boot-pool/user-data)" = boot-pool/user-data ] || fail 'UI-created root is not mounted from its ZFS dataset'
root_datasets=$(zfs list -H -r -o name boot-pool/user-data)
[ "$root_datasets" = boot-pool/user-data ] || fail "UI-created root has descendants; refusing repair: $root_datasets"
all_snapshots=$(zfs list -H -t snapshot -o name)
if printf '%s\n' "$all_snapshots" | grep -Eq '^boot-pool/user-data@'; then
    fail 'UI-created root has snapshots; refusing repair'
fi
if files=$(find /mnt/boot-pool/user-data -mindepth 1 -print -quit 2>&1); then
    [ -z "$files" ] || fail "UI-created root contains files; refusing repair: $files"
else
    fail "could not inventory UI-created root contents: $files"
fi
containers=$(midclt call container.query)
python3 -c 'import json,sys; rows=json.loads(sys.argv[1]); assert isinstance(rows,list) and not rows, f"expected no containers, got {rows!r}"' "$containers" || fail 'container records exist; refusing repair'
vms=$(midclt call vm.query)
python3 -c 'import json,sys; rows=json.loads(sys.argv[1]); assert isinstance(rows,list) and not rows, f"expected no VMs, got {rows!r}"' "$vms" || fail 'VM records exist; refusing repair'
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
target_sha=$(awk '$2 == "middlewared/plugins/pool_/dataset_info.py" {print $1}' "$stage/v27/targets.sha256")
[ "$target_sha" = "$prior_source_sha" ] || fail 'replacement bundle does not bind the inspected original dataset_info.py fingerprint'
actual_source_sha=$(sha256sum "$source_file" | awk '{print $1}')
[ "$actual_source_sha" = "$prior_source_sha" ] || fail "installed dataset_info.py digest mismatch: $actual_source_sha"
cd "$dist_root"
patch --dry-run --batch --fuzz=0 -p1 < "$stage/v27/zvol-blocksize.patch"

if [ -e "$archive_root" ]; then
    [ -d "$archive_root" ] && [ ! -L "$archive_root" ] || fail 'repair archive root is unsafe'
else
    install -o root -g root -m 0700 -d "$archive_root"
fi
transaction_dir=$(mktemp -d "$archive_root/zvol-blocksize.XXXXXXXX")
chmod 0700 "$transaction_dir"
install -o root -g root -m 0600 "$source_file" "$transaction_dir/dataset_info.py"
install -o root -g root -m 0600 "$marker" "$transaction_dir/install-record.json"
old_source_sha=$(sha256sum "$source_file" | awk '{print $1}')
old_marker_sha=$(sha256sum "$marker" | awk '{print $1}')
printf '%s\n' "$old_source_sha" > "$transaction_dir/dataset_info.py.sha256"
printf '%s\n' "$old_marker_sha" > "$transaction_dir/install-record.json.sha256"

transaction_started=1
systemctl stop middlewared.service
zfs set readonly=off "$usr_dataset"
mount -o remount,rw /usr
patch --batch --fuzz=0 -p1 < "$stage/v27/zvol-blocksize.patch"
python3 -m py_compile "$source_file"
systemctl start middlewared.service
for attempt in $(seq 1 120); do
    if systemctl is-active --quiet middlewared.service && midclt call system.info >/dev/null 2>&1; then break; fi
    sleep 1
done
systemctl is-active --quiet middlewared.service || fail 'middlewared did not restart after zvol blocksize repair'
midclt call system.info >/dev/null
recommended=$(midclt call pool.dataset.recommended_zvol_blocksize boot-pool)
python3 - "$recommended" <<'PY'
import json, sys
raw = sys.argv[1]
try:
    result = json.loads(raw)
except json.JSONDecodeError:
    result = raw.strip()
if result != "16K":
    raise SystemExit(f"boot-pool zvol blocksize recommendation is {result!r}, not '16K'")
PY
[ ! -e /dev/kvm ] || fail '/dev/kvm appeared after zvol blocksize repair'
python3 - "$marker" "$new_bundle_sha" "$prior_bundle_sha" "$source_file" <<'PY'
import hashlib, json, os, pathlib, stat, sys, tempfile
marker_path = pathlib.Path(sys.argv[1])
new_bundle, prior_bundle, source_path = sys.argv[2:]
record = json.loads(marker_path.read_text())
record["bundle_sha256"] = new_bundle
record["recommended_zvol_blocksize_boot_pool"] = "boot-pool-defaults-to-16k-v1"
record["zvol_blocksize_repaired_from_bundle_sha256"] = prior_bundle
record["zvol_blocksize_repaired_dataset_info_py_sha256"] = hashlib.sha256(pathlib.Path(source_path).read_bytes()).hexdigest()
fd, temporary = tempfile.mkstemp(prefix=".v27-patch-install.", dir=marker_path.parent)
try:
    os.fchmod(fd, stat.S_IMODE(marker_path.stat().st_mode))
    with os.fdopen(fd, "w") as stream:
        json.dump(record, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, marker_path)
    directory_fd = os.open(marker_path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
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
PY
[ "$(zfs get -H -p -o value quota,mountpoint,canmount boot-pool/user-data)" = $'2147483648\n/mnt/boot-pool/user-data\non' ] || fail 'UI-created root properties changed during repair'
[ "$(findmnt -n -o SOURCE --target /mnt/boot-pool/user-data)" = boot-pool/user-data ] || fail 'UI-created root mount changed during repair'
zfs set readonly=on "$usr_dataset"
mount -o remount,ro /usr
[ "$(zfs get -H -o value readonly "$usr_dataset")" = on ] || fail '/usr dataset did not return to read-only'
findmnt -n -o OPTIONS --target /usr | tr ',' '\n' | grep -Fxq ro || fail '/usr did not return to a read-only mount'
transaction_committed=1
trap - ERR INT TERM
echo "[repair] applied boot-pool zvol blocksize recommendation; prior source retained at $transaction_dir"
