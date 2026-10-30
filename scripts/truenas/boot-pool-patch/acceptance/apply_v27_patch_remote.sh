#!/usr/bin/env bash
set -euo pipefail

bundle_root=${1:?usage: apply_v27_patch_remote.sh BUNDLE_ROOT}
expected_version=${2:-27.0.0-RC.1}
install_root=/usr/lib/python3/dist-packages
patch_dir="$bundle_root/v27"
usr_dataset=
usr_writable=0

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    exit 1
}

restore_usr_readonly() {
    status=$?
    trap - EXIT
    if [ "$usr_writable" = 1 ]; then
        zfs set readonly=on "$usr_dataset" || status=1
        mount -o remount,ro /usr || status=1
        if ! findmnt -n -o OPTIONS --target /usr | tr ',' '\n' | grep -Fxq ro; then
            printf 'FAIL: /usr did not return to a read-only mount\n' >&2
            status=1
        fi
    fi
    exit "$status"
}

if [ "$(id -u)" != 0 ]; then
    fail 'run as root inside the disposable TrueNAS guest'
fi
if [ ! -d "$patch_dir" ] || [ ! -f "$patch_dir/targets.sha256" ]; then
    fail "patch bundle is missing v27 targets under $patch_dir"
fi

version=$(midclt call system.info | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version", ""))')
[ "$version" = "$expected_version" ] || fail "expected $expected_version, found $version"
case "$expected_version" in
    27.0.0-RC.1|27.0.0-MASTER+20261027-173422) ;;
    *) fail "no exact v27 fresh-install adapter for $expected_version" ;;
esac
[ ! -e /dev/kvm ] || fail '/dev/kvm exists; this is not the intended TCG target'

if zfs list -H -o name boot-pool/user-data >/dev/null 2>&1; then
    fail 'boot-pool/user-data already exists; refusing a non-fresh Path A target'
fi
if zpool list -H -o name | grep -Fxq boot-disk-data; then
    fail 'boot-disk-data already exists; refusing a non-fresh Path B target'
fi
if [ -e /root/v27-boot-pool-patch-install.json ]; then
    fail 'a v27 patch install record already exists'
fi

containers=$(midclt call container.query)
python3 -c 'import json,sys; rows=json.loads(sys.argv[1]); assert isinstance(rows,list) and not rows, f"expected no containers, got {rows!r}"' "$containers" \
    || fail 'fresh-target check found existing container records'
vms=$(midclt call vm.query)
python3 -c 'import json,sys; rows=json.loads(sys.argv[1]); assert isinstance(rows,list) and not rows, f"expected no VMs, got {rows!r}"' "$vms" \
    || fail 'fresh-target check found existing VM records'

usr_dataset=$(findmnt -n -o SOURCE --target /usr)
case "$usr_dataset" in
    boot-pool/ROOT/*/usr) ;;
    *) fail "/usr source is not the active boot environment usr dataset: $usr_dataset" ;;
esac

cd "$install_root"
sha256sum --check "$patch_dir/targets.sha256"
patch --dry-run --batch -p1 < "$patch_dir/middleware.patch"
patch --dry-run --batch -p1 < "$patch_dir/storage.patch"
patch --dry-run --batch -p1 < "$patch_dir/libvirt.patch"
patch --dry-run --batch -p1 < "$patch_dir/boot-mount.patch"
patch --dry-run --batch --fuzz=0 -p1 < "$patch_dir/zvol-blocksize.patch"
patch --dry-run --batch --fuzz=0 -p1 < "$patch_dir/ui-pools.patch"

zfs set readonly=off "$usr_dataset"
usr_writable=1
trap restore_usr_readonly EXIT
mount -o remount,rw /usr

systemctl stop middlewared.service
patch --batch -p1 < "$patch_dir/middleware.patch"
patch --batch -p1 < "$patch_dir/storage.patch"
patch --batch -p1 < "$patch_dir/libvirt.patch"
patch --batch -p1 < "$patch_dir/boot-mount.patch"
patch --batch --fuzz=0 -p1 < "$patch_dir/zvol-blocksize.patch"
patch --batch --fuzz=0 -p1 < "$patch_dir/ui-pools.patch"
install -m 0644 "$bundle_root/shared/virtualization_support.py" middlewared/plugins/vm/
install -m 0644 "$bundle_root/shared/boot_pool_user_data.py" middlewared/plugins/
install -m 0644 "$bundle_root/shared/boot_disk_partition.py" middlewared/plugins/
install -m 0644 "$bundle_root/shared/pool_create_schema_v27.json" middlewared/plugins/

python3 -m py_compile \
    middlewared/plugins/vm/info.py \
    middlewared/plugins/vm/virtualization_support.py \
    middlewared/plugins/boot.py \
    middlewared/plugins/boot_pool_user_data.py \
    middlewared/plugins/boot_disk_partition.py \
    middlewared/plugins/disk_/availability.py \
    middlewared/async_validators.py \
    middlewared/plugins/pool_/dataset_query_utils.py \
    middlewared/plugins/pool_/dataset.py \
    middlewared/plugins/pool_/dataset_info.py \
    middlewared/plugins/container/container.py \
    middlewared/plugins/container/dataset.py \
    middlewared/plugins/container/utils.py \
    middlewared/plugins/container/image.py \
    middlewared/plugins/container/lifecycle.py \
    middlewared/plugins/pool_/import_pool.py \
    middlewared/plugins/pool_/pool.py \
    middlewared/plugins/pool_/export.py \
    middlewared/plugins/zfs/utils.py \
    middlewared/plugins/vm/vm_devices.py \
    middlewared/utils/libvirt/storage_devices.py \
    truenas_pylibvirt/domain/vm/xml.py

systemctl start middlewared.service
attempt=0
while [ "$attempt" -lt 120 ]; do
    if systemctl is-active --quiet middlewared.service \
        && midclt call vm.virtualization_details >/tmp/v27-virt-details.json 2>/dev/null; then
        break
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -eq 120 ]; then
        fail 'middlewared did not become ready after the v27 patch install'
    fi
    sleep 5
done

python3 - <<'PY'
import json
from pathlib import Path

details = json.loads(Path('/tmp/v27-virt-details.json').read_text())
if details.get('supported') is not True:
    raise SystemExit(f'patched vm.virtualization_details is not supported: {details!r}')
PY
[ ! -e /dev/kvm ] || fail '/dev/kvm appeared after patch install'
midclt call vm.flags > /tmp/v27-vm-flags.json
python3 - <<'PY'
import json
from pathlib import Path

flags = json.loads(Path('/tmp/v27-vm-flags.json').read_text())
if not isinstance(flags, dict) or any(flags.values()):
    raise SystemExit(f'TCG patch exposed hardware virtualization flags: {flags!r}')
PY

python3 - "$version" "$bundle_root" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

version, bundle_root = sys.argv[1:]
root = Path(bundle_root)
patch_text = (root / "v27/storage.patch").read_text()
expected_guard = (
    "+        if user_data_root and not is_user_data_namespace_root(data['name'], "
    "BOOT_POOL_NAME_VALID) and is_user_data_namespace_path("
)
if expected_guard not in patch_text:
    raise SystemExit("v27 patch bundle lacks the root-create readiness exemption")
bundle = root / "bundle.tar.gz"
if bundle.is_symlink() or not bundle.is_file():
    raise SystemExit("staged v27 patch bundle archive is missing or unsafe")
record = {
    "version": version,
    "patch": "v27",
    "supported": True,
    "kvm_device": False,
    "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
    "root_create_guard": "root-excluded-from-descendant-readiness-v1",
    "root_parent_mountpoint_guard": "root-create-skips-parent-mountpoint-none-v1",
    "recommended_zvol_blocksize_boot_pool": "boot-pool-defaults-to-16k-v1",
}
Path("/root/v27-boot-pool-patch-install.json").write_text(
    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
)
PY
printf '[result] PASS patched fresh TrueNAS %s; virtualization is supported by QEMU TCG; /dev/kvm remains absent\n' \
    "$version"
