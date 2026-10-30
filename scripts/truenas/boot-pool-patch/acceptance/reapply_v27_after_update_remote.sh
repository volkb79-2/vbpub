#!/usr/bin/env bash
set -euo pipefail

bundle_root=${1:?usage: reapply_v27_after_update_remote.sh BUNDLE_ROOT EXPECTED_VERSION MODE PRIOR_BUNDLE_SHA256}
expected_version=${2:?expected TrueNAS build is required}
mode=${3:?expected mode is required}
prior_bundle_sha256=${4:?prior RC.1 bundle SHA-256 is required}
required_version=27.0.0-MASTER+20261027-173422
prior_version=27.0.0-RC.1
install_root=/usr/lib/python3/dist-packages
patch_dir="$bundle_root/v27"
marker=/root/v27-boot-pool-patch-install.json
verify_record="/root/v27-boot-pool-patch-verify-$required_version.json"
path_a_root=boot-pool/user-data
path_a_mountpoint=/mnt/boot-pool/user-data
usr_dataset=
usr_writable=0
transaction_started=0
transaction_committed=0
transaction_dir=
archive_record=

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

mount_path_a_tree() {
    zfs get -H -p -o value type,quota,mountpoint,canmount "$path_a_root" | python3 -c '
import sys
values = sys.stdin.read().splitlines()
if values != ["filesystem", "2147483648", "/mnt/boot-pool/user-data", "on"]:
    raise SystemExit(f"Path A root properties are unsafe: {values!r}")
'
    while IFS= read -r dataset; do
        canmount=$(zfs get -H -o value canmount "$dataset")
        [ "$canmount" = on ] || continue
        mountpoint=$(zfs get -H -o value mountpoint "$dataset")
        case "$mountpoint" in
            "$path_a_mountpoint"|"$path_a_mountpoint"/*) ;;
            *) fail "Path A descendant has an unsafe mountpoint: $dataset -> $mountpoint" ;;
        esac
        mounted=$(zfs get -H -o value mounted "$dataset")
        if [ "$mounted" = no ]; then
            zfs mount "$dataset"
        elif [ "$mounted" != yes ]; then
            fail "Path A dataset has an unknown mounted state: $dataset -> $mounted"
        fi
        source=$(findmnt -n -o SOURCE --target "$mountpoint")
        [ "$source" = "$dataset" ] || fail "Path A dataset is not mounted from its ZFS source: $dataset -> $source"
    done < <(zfs list -H -r -o name "$path_a_root")
}

restore_previous_source() {
    [ -n "$transaction_dir" ] || return 1
    printf '[rollback] restoring captured nightly sources and the stale RC.1 record\n' >&2
    systemctl stop middlewared.service >/dev/null 2>&1 || true
    if [ "$usr_writable" != 1 ]; then
        zfs set readonly=off "$usr_dataset" || return 1
        mount -o remount,rw /usr || return 1
        usr_writable=1
    fi
    cd "$install_root" || return 1
    while read -r _digest rel; do
        [ -n "$rel" ] || continue
        cp --archive -- "$transaction_dir/source-before/$rel" "$install_root/$rel" || return 1
    done < "$patch_dir/targets.sha256"
    rm -f middlewared/plugins/vm/virtualization_support.py \
        middlewared/plugins/boot_pool_user_data.py \
        middlewared/plugins/boot_disk_partition.py \
        middlewared/plugins/pool_create_schema_v27.json || return 1
    cp --archive -- "$transaction_dir/install-record-before.json" "$marker" || return 1
    if [ -n "$archive_record" ]; then rm -f -- "$archive_record" || return 1; fi
    systemctl start middlewared.service || return 1
    zfs set readonly=on "$usr_dataset" || return 1
    mount -o remount,ro /usr || return 1
    usr_writable=0
}

restore_usr_readonly() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$transaction_started" = 1 ] && [ "$transaction_committed" = 0 ]; then
        restore_previous_source || status=1
    fi
    if [ "$usr_writable" = 1 ]; then
        zfs set readonly=on "$usr_dataset" || status=1
        mount -o remount,ro /usr || status=1
        if findmnt -n -o OPTIONS --target /usr | tr ',' '\n' | grep -Fxq ro; then
            usr_writable=0
        else
            printf 'FAIL: /usr did not return to a read-only mount\n' >&2
            status=1
        fi
    fi
    exit "$status"
}

[ "$(id -u)" = 0 ] || fail 'run as root inside the disposable TrueNAS guest'
[ "$expected_version" = "$required_version" ] || fail "unsupported exact v27 recovery build: $expected_version"
case "$mode" in
    reapply-after-update|uninstall-stale-record) ;;
    *) fail "unsupported recovery mode: $mode" ;;
esac
[ -d "$patch_dir" ] && [ -f "$patch_dir/targets.sha256" ] || fail "adapter targets missing under $patch_dir"
[ -f "$patch_dir/adapter.json" ] && [ ! -L "$patch_dir/adapter.json" ] || fail 'nightly adapter manifest is missing or unsafe'
python3 - "$patch_dir/adapter.json" "$patch_dir/targets.sha256" "$expected_version" "$prior_bundle_sha256" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
manifest_path, targets_path, version, prior_digest = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text())
expected = {
    "id": "v27-nightly-20261027",
    "version": version,
    "train": "TrueNAS-27-Nightlies",
    "reapply_from": "27.0.0-RC.1",
    "reapply_from_bundle_sha256": prior_digest,
    "target_count": 19,
    "target_manifest_sha256": hashlib.sha256(Path(targets_path).read_bytes()).hexdigest(),
}
if manifest != expected:
    raise SystemExit(f"adapter manifest does not match the exact recovery contract: {manifest!r}")
PY
version=$(midclt call system.info | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')
[ "$version" = "$expected_version" ] || fail "expected $expected_version, found $version"
[ ! -e /dev/kvm ] || fail '/dev/kvm appeared; the recorded TCG target changed'
[ -f "$marker" ] && [ ! -L "$marker" ] || fail 'stale v27 install record is missing or unsafe'
[ ! -e "$verify_record" ] || fail "v27 target-build self-test record already exists: $verify_record"
python3 - "$marker" "$prior_version" "$prior_bundle_sha256" <<'PY'
import json
from pathlib import Path
import sys
record = json.loads(Path(sys.argv[1]).read_text())
expected = {
    "bundle_sha256": sys.argv[3],
    "kvm_device": False,
    "patch": "v27",
    "recommended_zvol_blocksize_boot_pool": "boot-pool-defaults-to-16k-v1",
    "root_create_guard": "root-excluded-from-descendant-readiness-v1",
    "root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1",
    "supported": True,
    "version": sys.argv[2],
}
if record != expected:
    raise SystemExit(f"stale v27 install record does not match the exact prior RC.1 install: {record!r}")
PY
for helper in middlewared/plugins/vm/virtualization_support.py \
    middlewared/plugins/boot_pool_user_data.py \
    middlewared/plugins/boot_disk_partition.py \
    middlewared/plugins/pool_create_schema_v27.json; do
    [ ! -e "$install_root/$helper" ] || fail "updated nightly still contains a patch helper: $helper"
done
pool_health=$(zpool list -H -o name,health boot-pool)
[ "$pool_health" = $'boot-pool\tONLINE' ] || fail "boot-pool is not ONLINE: $pool_health"
usr_dataset=$(findmnt -n -o SOURCE --target /usr)
case "$usr_dataset" in
    boot-pool/ROOT/*/usr) ;;
    *) fail "/usr source is not the active boot environment usr dataset: $usr_dataset" ;;
esac

cd "$install_root"
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
if [ "$mode" = uninstall-stale-record ]; then
    mount_path_a_tree
    archive_dir=/root/.truenas-v27-patch-records
    install -d -m 0700 "$archive_dir"
    archive_record="$archive_dir/rc1-stale-after-20261027-nightly-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
    cp --archive -- "$marker" "$archive_record"
    rm -- "$marker"
    [ ! -e "$marker" ] || fail 'stale v27 install record remains after guarded uninstall'
    printf '[result] PASS retired only the exact stale RC.1 record; nightly sources stayed stock; Path A tree is mounted\n'
    printf '[record] archived at %s\n' "$archive_record"
    exit 0
fi

for patch_file in middleware.patch storage.patch libvirt.patch boot-mount.patch zvol-blocksize.patch ui-pools.patch; do
    [ -f "$patch_dir/$patch_file" ] || fail "nightly adapter is missing $patch_file"
    patch --dry-run --batch --fuzz=0 -p1 < "$patch_dir/$patch_file"
done
bundle_archive="$bundle_root/bundle.tar.gz"
[ -f "$bundle_archive" ] && [ ! -L "$bundle_archive" ] || fail 'staged patch bundle archive is missing or unsafe'
transaction_root=/root/.truenas-v27-update-reapply
install -d -m 0700 "$transaction_root"
transaction_dir="$transaction_root/$(date -u +%Y%m%dT%H%M%SZ)-$$"
install -d -m 0700 "$transaction_dir/source-before"
while read -r _digest rel; do
    [ -n "$rel" ] || continue
    mkdir -p "$transaction_dir/source-before/$(dirname "$rel")"
    cp --archive -- "$install_root/$rel" "$transaction_dir/source-before/$rel"
done < "$patch_dir/targets.sha256"
cp --archive -- "$marker" "$transaction_dir/install-record-before.json"
transaction_started=1
trap restore_usr_readonly EXIT

zfs set readonly=off "$usr_dataset"
usr_writable=1
mount -o remount,rw /usr
systemctl stop middlewared.service
for patch_file in middleware.patch storage.patch libvirt.patch boot-mount.patch zvol-blocksize.patch ui-pools.patch; do
    patch --batch --fuzz=0 -p1 < "$patch_dir/$patch_file"
done
install -m 0644 "$bundle_root/shared/virtualization_support.py" middlewared/plugins/vm/
install -m 0644 "$bundle_root/shared/boot_pool_user_data.py" middlewared/plugins/
install -m 0644 "$bundle_root/shared/boot_disk_partition.py" middlewared/plugins/
install -m 0644 "$bundle_root/shared/pool_create_schema_v27.json" middlewared/plugins/
for patch_file in middleware.patch storage.patch libvirt.patch boot-mount.patch zvol-blocksize.patch ui-pools.patch; do
    patch --dry-run --batch --fuzz=0 --reverse -p1 < "$patch_dir/$patch_file"
done
python3 -m py_compile middlewared/plugins/vm/info.py middlewared/plugins/vm/virtualization_support.py \
    middlewared/plugins/boot_pool_user_data.py middlewared/plugins/boot_disk_partition.py \
    middlewared/plugins/disk_/availability.py middlewared/async_validators.py \
    middlewared/plugins/pool_/dataset_query_utils.py middlewared/plugins/pool_/dataset.py \
    middlewared/plugins/pool_/dataset_info.py middlewared/plugins/container/container.py \
    middlewared/plugins/container/dataset.py middlewared/plugins/container/utils.py \
    middlewared/plugins/container/image.py middlewared/plugins/container/lifecycle.py \
    middlewared/plugins/pool_/import_pool.py middlewared/plugins/pool_/pool.py \
    middlewared/plugins/pool_/export.py middlewared/plugins/zfs/utils.py \
    middlewared/plugins/vm/vm_devices.py middlewared/utils/libvirt/storage_devices.py \
    truenas_pylibvirt/domain/vm/xml.py
systemctl start middlewared.service
attempt=0
while [ "$attempt" -lt 120 ]; do
    if systemctl is-active --quiet middlewared.service \
        && midclt call vm.virtualization_details >/tmp/v27-update-reapply-virt.json 2>/dev/null; then
        break
    fi
    attempt=$((attempt + 1))
    [ "$attempt" -lt 120 ] || fail 'middlewared did not become ready after v27 update reapply'
    sleep 5
done
python3 - /tmp/v27-update-reapply-virt.json <<'PY'
import json
from pathlib import Path
import sys
details = json.loads(Path(sys.argv[1]).read_text())
if details.get("supported") is not True:
    raise SystemExit(f"patched TCG backend is not supported: {details!r}")
PY
[ ! -e /dev/kvm ] || fail '/dev/kvm appeared after v27 update reapply'
midclt call system.info | python3 -c 'import json,sys; info=json.load(sys.stdin); expected=sys.argv[1]; actual=info.get("version"); assert actual == expected, f"expected {expected}, got {actual!r}"' "$expected_version"
midclt call pool.dataset.query > /tmp/v27-update-reapply-datasets.json
python3 - <<'PY'
import json
from pathlib import Path
rows = json.loads(Path('/tmp/v27-update-reapply-datasets.json').read_text())
if not isinstance(rows, list):
    raise SystemExit(f'pool.dataset.query returned a non-list result: {rows!r}')
PY
mount_path_a_tree
archive_dir=/root/.truenas-v27-patch-records
install -d -m 0700 "$archive_dir"
archive_record="$archive_dir/rc1-stale-after-20261027-nightly-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
cp --archive -- "$marker" "$archive_record"
python3 - "$expected_version" "$bundle_archive" "$prior_bundle_sha256" "$marker" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
version, bundle_path, prior_digest, marker = sys.argv[1:]
record = {
    "bundle_sha256": hashlib.sha256(Path(bundle_path).read_bytes()).hexdigest(),
    "kvm_device": False,
    "patch": "v27",
    "reapplied_from_bundle_sha256": prior_digest,
    "reapplied_from_version": "27.0.0-RC.1",
    "recommended_zvol_blocksize_boot_pool": "boot-pool-defaults-to-16k-v1",
    "root_create_guard": "root-excluded-from-descendant-readiness-v1",
    "root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1",
    "supported": True,
    "version": version,
}
path = Path(marker)
fd, temporary = tempfile.mkstemp(prefix=".v27-patch-install.", dir=path.parent)
try:
    with os.fdopen(fd, "w") as stream:
        json.dump(record, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
zfs set readonly=on "$usr_dataset"
mount -o remount,ro /usr
usr_writable=0
findmnt -n -o OPTIONS --target /usr | tr ',' '\n' | grep -Fxq ro || fail '/usr did not return to a read-only mount after v27 update reapply'
python3 - "$expected_version" "$bundle_archive" "$patch_dir/targets.sha256" "$verify_record" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import sys

version, bundle_path, targets_path, record_path = sys.argv[1:]
targets_file = Path(targets_path)
target_before = {}
for line in targets_file.read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    target_before[name] = digest
root = Path('/usr/lib/python3/dist-packages')
target_after = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in target_before}
helper_names = (
    'middlewared/plugins/vm/virtualization_support.py',
    'middlewared/plugins/boot_pool_user_data.py',
    'middlewared/plugins/boot_disk_partition.py',
    'middlewared/plugins/pool_create_schema_v27.json',
)
helpers = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in helper_names}
record = {
    'version': version,
    'bundle_sha256': hashlib.sha256(Path(bundle_path).read_bytes()).hexdigest(),
    'target_manifest_sha256': hashlib.sha256(targets_file.read_bytes()).hexdigest(),
    'target_before': target_before,
    'target_after': target_after,
    'helpers': helpers,
    'reapplied_from_version': '27.0.0-RC.1',
}
destination = Path(record_path)
temporary = destination.with_name(destination.name + '.tmp')
temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
os.chmod(temporary, 0o600)
os.replace(temporary, destination)
PY
transaction_committed=1
printf '[result] PASS re-applied exact v27 nightly adapter after update; archived RC.1 record; Path A root and eligible descendants are mounted\n'
printf '[record] archived at %s\n' "$archive_record"
printf '[transaction] original nightly sources retained at %s\n' "$transaction_dir"
printf '[self-test] exact post-apply source/helper hashes recorded at %s\n' "$verify_record"
