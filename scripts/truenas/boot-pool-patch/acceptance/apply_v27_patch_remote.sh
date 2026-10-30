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
adapter_manifest="$patch_dir/adapter.json"
[ -f "$adapter_manifest" ] && [ ! -L "$adapter_manifest" ] || fail 'exact-build adapter manifest is missing or unsafe'
python3 - "$adapter_manifest" "$patch_dir/targets.sha256" "$expected_version" <<'PY'
import hashlib
import json
from pathlib import Path
import re
import sys

manifest_path, targets_path, version = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text())
target_digest = hashlib.sha256(Path(targets_path).read_bytes()).hexdigest()
targets = Path(targets_path).read_text().splitlines()
if version == "27.0.0-RC.1":
    expected = {
        "id": "v27-rc1", "version": version, "train": "TrueNAS-27-RC",
        "target_count": len(targets), "target_manifest_sha256": target_digest,
    }
elif re.fullmatch(r"27\.0\.0-MASTER\+(\d{8})-\d{6}", version):
    day = version.split("+", 1)[1].split("-", 1)[0]
    expected = {
        "id": f"v27-nightly-{day}", "version": version,
        "train": "TrueNAS-27-Nightlies", "reapply_from": "27.0.0-RC.1",
        "reapply_from_bundle_sha256s": manifest.get("reapply_from_bundle_sha256s"),
        "target_count": len(targets), "target_manifest_sha256": target_digest,
    }
    prior_digests = manifest.get("reapply_from_bundle_sha256s")
    if (not isinstance(prior_digests, list) or not prior_digests
            or not all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
                       for item in prior_digests)):
        raise SystemExit("nightly adapter manifest has no valid exact RC.1 bundle digest list")
else:
    raise SystemExit(f"no exact v27 fresh-install adapter for {version}")
if manifest != expected:
    raise SystemExit(f"adapter manifest does not match the exact supported build: {manifest!r}")
PY
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
verify_record="/root/v27-boot-pool-patch-verify-$expected_version.json"
[ ! -e "$verify_record" ] || fail "a v27 patch self-test record already exists for $expected_version"

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
for patch_file in middleware.patch storage.patch libvirt.patch boot-mount.patch zvol-blocksize.patch ui-pools.patch; do
    [ -f "$patch_dir/$patch_file" ] || fail "v27 adapter is missing $patch_file"
    patch --dry-run --batch --fuzz=0 -p1 < "$patch_dir/$patch_file"
done

zfs set readonly=off "$usr_dataset"
usr_writable=1
trap restore_usr_readonly EXIT
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
midclt call system.info | python3 -c 'import json,sys; info=json.load(sys.stdin); expected=sys.argv[1]; actual=info.get("version"); assert actual == expected, f"expected {expected}, got {actual!r}"' "$version"
midclt call pool.dataset.query > /tmp/v27-datasets-self-test.json
python3 - <<'PY'
import json
from pathlib import Path

rows = json.loads(Path('/tmp/v27-datasets-self-test.json').read_text())
if not isinstance(rows, list):
    raise SystemExit(f'pool.dataset.query returned a non-list result: {rows!r}')
PY

python3 - "$version" "$bundle_root" "$verify_record" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

version, bundle_root, verify_record = sys.argv[1:]
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
targets_path = root / "v27/targets.sha256"
target_before = {}
for line in (root / "v27/targets.sha256").read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    target_before[name] = digest
install_root = Path("/usr/lib/python3/dist-packages")
target_after = {
    name: hashlib.sha256((install_root / name).read_bytes()).hexdigest()
    for name in target_before
}
helper_names = (
    "middlewared/plugins/vm/virtualization_support.py",
    "middlewared/plugins/boot_pool_user_data.py",
    "middlewared/plugins/boot_disk_partition.py",
    "middlewared/plugins/pool_create_schema_v27.json",
)
helpers = {
    name: hashlib.sha256((install_root / name).read_bytes()).hexdigest()
    for name in helper_names
}
verification = {
    "version": version,
    "bundle_sha256": record["bundle_sha256"],
    "target_manifest_sha256": hashlib.sha256(targets_path.read_bytes()).hexdigest(),
    "target_before": target_before,
    "target_after": target_after,
    "helpers": helpers,
}
verify_path = Path(verify_record)

def write_private_json(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)

write_private_json(verify_path, verification)
write_private_json(Path("/root/v27-boot-pool-patch-install.json"), record)
PY
printf '[result] PASS patched fresh TrueNAS %s; virtualization is supported by QEMU TCG; /dev/kvm remains absent\n' \
    "$version"
printf '[self-test] exact post-apply source/helper hashes recorded at %s\n' "$verify_record"
