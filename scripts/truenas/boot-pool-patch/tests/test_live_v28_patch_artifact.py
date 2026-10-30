from __future__ import annotations

import gzip
import importlib.util
import io
from pathlib import Path
import sys
import tarfile
import tomllib


TRUENAS_ROOT = Path(__file__).parents[2]
PATCH_ROOT = TRUENAS_ROOT / "boot-pool-patch"
ACCEPTANCE_DIR = PATCH_ROOT / "acceptance"
SCRIPT = ACCEPTANCE_DIR / "live_v28_patch.py"
REMOTE_SCRIPT = ACCEPTANCE_DIR / "apply_v28_patch_remote.sh"
RUN_GATE = TRUENAS_ROOT / "run-gate.toml"
NETWORKED_RUNNER = PATCH_ROOT / "run-live-gate.py"


def load_applicator():
    sys.path.insert(0, str(ACCEPTANCE_DIR))
    spec = importlib.util.spec_from_file_location("truenas_live_v28_patch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_live_api_probe():
    sys.path.insert(0, str(ACCEPTANCE_DIR))
    spec = importlib.util.spec_from_file_location("truenas_live_api_probe", ACCEPTANCE_DIR / "live_api_probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v28_target_hash_manifest_is_closed_and_rejects_paths(tmp_path: Path) -> None:
    applicator = load_applicator()
    hashes = applicator.read_target_hashes()
    assert len(hashes) == 15
    assert all(name.startswith(("middlewared/", "truenas_pylibvirt/")) for name in hashes)

    bad = tmp_path / "targets.sha256"
    bad.write_text("0" * 64 + "  middlewared/../../etc/passwd\n")
    try:
        applicator.read_target_hashes(bad)
    except applicator.RemoteError as exc:
        assert "unsafe v28 target path" in str(exc)
    else:
        raise AssertionError("a traversal target must be rejected")


def test_v28_latest_nightly_has_its_own_exact_version_adapter() -> None:
    applicator = load_applicator()
    version = "28.0.0-MASTER+20261027-175229"
    adapter_dir = applicator.adapter_directory(version)
    assert adapter_dir == PATCH_ROOT / "v28-nightly-20261027"
    targets = applicator.read_target_hashes(version=version)
    assert len(targets) == 19
    assert {
        "middlewared/plugins/pool_/pool.py",
        "middlewared/plugins/disk_/availability.py",
        "middlewared/plugins/pool_/export.py",
        "middlewared/plugins/pool_/dataset_query_utils.py",
    } <= set(targets)
    bundle, digest, bundled_targets = applicator.deterministic_bundle(version=version)
    assert digest and bundled_targets == targets
    with gzip.GzipFile(fileobj=io.BytesIO(bundle), mode="rb") as compressed:
        with tarfile.open(fileobj=compressed, mode="r:") as archive:
            names = {member.name for member in archive.getmembers()}
            assert {
                "patch/ui-pools.patch",
                "shared/boot_disk_partition.py",
                "shared/pool_create_schema_v28.json",
            } <= names
            ui_patch = archive.extractfile("patch/ui-pools.patch")
            assert ui_patch is not None
            assert b"middlewared/plugins/pool_/pool.py" in ui_patch.read()
            apply_script = archive.extractfile("patch/apply_v28_patch_remote.sh")
            assert apply_script is not None
            assert b"ui-pools.patch" in apply_script.read()
            replace_script = archive.extractfile("patch/replace_v28_patch_remote.sh")
            assert replace_script is not None
            replacement = replace_script.read()
            assert b"28.0.0-MASTER+20261027-175229" in replacement
            assert b"len(targets) != 19" in replacement
            assert b"len(previous_targets) not in {18, 19}" in replacement
            assert b'expected_extension = {"middlewared/plugins/pool_/dataset_query_utils.py"}' in replacement
            assert b"set(targets) - set(previous_targets) not in (set(), expected_extension)" in replacement
            assert b"middlewared/plugins/boot_disk_partition.py" in replacement
    try:
        applicator.adapter_directory("28.0.0-MASTER+unknown")
    except applicator.RemoteError as exc:
        assert "no v28 adapter is registered" in str(exc)
    else:
        raise AssertionError("an unregistered v28 build must not select an adapter")


def test_v28_path_a_install_uses_the_live_mount_as_backup_evidence() -> None:
    applicator = load_applicator()
    version = "28.0.0-MASTER+20261027-175229"

    class Target:
        def __init__(self, statfs_source: str = "boot-pool/user-data") -> None:
            self.statfs_source = statfs_source
            self.queries: list[str] = []

        def call(self, method, *args, **kwargs):
            if method == "system.info":
                return {"version": version}
            if method == "filesystem.statfs":
                return {"source": self.statfs_source, "dest": "/mnt/boot-pool/user-data"}
            raise AssertionError(f"unexpected middleware call: {method}")

        def shell(self, command, **kwargs):
            if "test ! -e /dev/kvm" in command:
                return "absent"
            if "systemctl is-active" in command:
                return "active"
            if "test ! -e /dev/vda4" in command:
                return "absent"
            if "zfs get -H -o value mountpoint boot-pool/user-data" in command:
                return ""
            raise AssertionError(f"unexpected shell command: {command}")

        def query(self, method, *args, **kwargs):
            self.queries.append(method)
            assert method in {"vm.query", "container.query"}
            return []

    target = Target()
    applicator.check_target_preconditions(target, version, "path-a")
    assert "pool.dataset.query" not in target.queries

    wrong_mount = Target(statfs_source="boot-pool")
    try:
        applicator.check_target_preconditions(wrong_mount, version, "path-a")
    except applicator.RemoteError as exc:
        assert "persistent backup mount is unavailable" in str(exc)
    else:
        raise AssertionError("Path A install must refuse a mount sourced from another dataset")


def test_v28_path_a_can_coexist_with_an_unused_reserved_partition() -> None:
    applicator = load_applicator()
    version = "28.0.0-MASTER+20261027-175229"

    class Target:
        def call(self, method, *args, **kwargs):
            if method == "system.info":
                return {"version": version}
            if method == "filesystem.statfs":
                return {"source": "boot-pool/user-data", "dest": "/mnt/boot-pool/user-data"}
            raise AssertionError(f"unexpected middleware call: {method}")

        def shell(self, command, **kwargs):
            if "test ! -e /dev/kvm" in command:
                return "absent"
            if "systemctl is-active" in command:
                return "active"
            if "zfs get -H -o value mountpoint boot-pool/user-data" in command:
                return ""
            if "/dev/vda4" in command:
                raise AssertionError("Path A preflight must not depend on the unrelated reserved partition")
            raise AssertionError(f"unexpected shell command: {command}")

        def query(self, method, *args, **kwargs):
            assert method in {"vm.query", "container.query"}
            return []

    applicator.check_target_preconditions(Target(), version, "path-a")

    adapter = PATCH_ROOT / "v28-nightly-20261027"
    for filename in ("apply_v28_patch_remote.sh", "replace_v28_patch_remote.sh"):
        source = (adapter / filename).read_text()
        assert "test ! -e /dev/vda4" not in source
        assert "[ ! -e /dev/vda4 ]" not in source
        assert "[ -b /dev/vda4 ]" in source


def test_v28_latest_dataset_query_exposes_only_the_opted_in_boot_namespace() -> None:
    patch = (PATCH_ROOT / "v28-nightly-20261027" / "storage.patch").read_text()
    assert "--- a/middlewared/plugins/pool_/dataset_query_utils.py" in patch
    assert "from middlewared.plugins.boot_pool_user_data import is_user_data_namespace_path" in patch
    assert "if name in BOOT_POOL_NAME_VALID:" in patch
    assert "if is_user_data_namespace_path(name, BOOT_POOL_NAME_VALID, INTERNAL_PATHS):" in patch
    assert "return has_internal_path(name)" in patch


def test_v28_latest_install_manifest_records_both_partition_shim_helpers() -> None:
    source = (PATCH_ROOT / "v28-nightly-20261027" / "apply_v28_patch_remote.sh").read_text()
    assert source.count("\nhelpers = {") == 1
    record_block = source.rsplit("\nhelpers = {", 1)[1].split("}\nafter =", 1)[0]
    assert '"middlewared/plugins/boot_disk_partition.py"' in record_block
    assert '"middlewared/plugins/pool_create_schema_v28.json"' in record_block
    assert "helper_hashes = {name: hashlib.sha256((root_path / name).read_bytes()).hexdigest() for name in helpers}" in source
    assert "installed manifest and all patched/helper hashes verified" in source
    replacement = (PATCH_ROOT / "v28-nightly-20261027" / "replace_v28_patch_remote.sh").read_text()
    assert 'legacy_helpers = {' in replacement
    assert 'set(helper_hashes) not in (legacy_helpers, helpers)' in replacement


def test_v28_20261027_replacement_restores_the_prior_adapter_after_apply_failure() -> None:
    adapter = PATCH_ROOT / "v28-nightly-20261027"
    apply_script = (adapter / "apply_v28_patch_remote.sh").read_text()
    replace_script = (adapter / "replace_v28_patch_remote.sh").read_text()

    assert "trap rollback ERR" in apply_script
    assert 'python3 -m py_compile "${python_targets[@]}"' in apply_script
    assert "[rollback] restored and verified original files, middlewared state, and /usr protection" in apply_script
    assert "trap 'restore_previous_adapter $?' ERR" in replace_script
    assert "transaction_started=1" in replace_script
    assert 'bash "$patch_dir/apply_v28_patch_remote.sh" "$expected_version" "$new_bundle_sha256"' in replace_script
    assert replace_script.index("transaction_started=1") < replace_script.index("\nsystemctl stop middlewared.service\n")
    assert replace_script.index('bash "$patch_dir/apply_v28_patch_remote.sh"') < replace_script.index(
        "transaction_committed=1",
    )
    assert 'tar -xzf "$transaction_dir/patched-files.tar.gz"' in replace_script
    assert 'install -o root -g root -m 0600 "$transaction_dir/manifest.json"' in replace_script
    assert 'systemctl start middlewared.service >/dev/null 2>&1 || rollback_ok=0' in replace_script
    assert 'zfs set readonly=on "$usr_dataset"' in replace_script
    assert "[replace-rollback] restored the previous adapter" in replace_script


def test_v28_partition_api_probe_uses_the_exact_build_schema() -> None:
    probe = load_live_api_probe()
    version = "28.0.0-MASTER+20261027-175229"
    assert probe.pool_create_schema_path(version) == PATCH_ROOT / "v28-nightly-20261027" / "pool_create_schema_v28.json"
    assert probe.pool_create_schema_path("27.0.0-MASTER+20261027-173422") == (
        PATCH_ROOT / "shared" / "pool_create_schema_v27.json"
    )
    try:
        probe.pool_create_schema_path("28.0.0-MASTER+unknown")
    except probe.RemoteError as exc:
        assert "no recorded pool.create schema" in str(exc)
    else:
        raise AssertionError("an unregistered build must not inherit another build's pool.create schema")


def test_v28_guest_guard_resolves_nullable_vm_state_and_rejects_unknowns() -> None:
    applicator = load_applicator()
    assert applicator.require_guests_stopped([
        {"id": 1, "status": None},
    ], "VM", lambda vm_id: {"state": "STOPPED"}) is None
    try:
        applicator.require_guests_stopped(
            [{"id": 6, "status": {}}], "VM", lambda vm_id: {"state": "RUNNING"}
        )
    except applicator.RemoteError as exc:
        assert "is 'RUNNING'" in str(exc)
    else:
        raise AssertionError("a direct VM status of RUNNING must be refused")
    for row in (
        {"id": 2, "status": None},
        {"id": 3, "status": {}},
        {"id": 4, "status": {"state": "RUNNING"}},
        {"id": 5, "status": {"state": "SUSPENDED"}},
    ):
        try:
            applicator.require_guests_stopped([row], "container")
        except applicator.RemoteError:
            pass
        else:
            raise AssertionError(f"unsafe or unknown guest state was accepted: {row!r}")


def test_v28_bundle_is_deterministic_and_contains_only_reviewed_members() -> None:
    applicator = load_applicator()
    first, first_hash, targets = applicator.deterministic_bundle()
    second, second_hash, _ = applicator.deterministic_bundle()
    assert first == second
    assert first_hash == second_hash
    assert len(targets) == 15

    with gzip.GzipFile(fileobj=io.BytesIO(first), mode="rb") as compressed:
        with tarfile.open(fileobj=compressed, mode="r:") as archive:
            members = {member.name for member in archive.getmembers()}
    assert members == {
        "patch/adapter.json",
        "patch/storage.patch",
        "patch/middleware.patch",
        "patch/boot-mount.patch",
        "patch/targets.sha256",
        "patch/targets.list",
        "patch/boot_pool_mount.py",
        "patch/apply_v28_patch_remote.sh",
        "patch/replace_v28_patch_remote.sh",
        "shared/boot_pool_user_data.py",
        "shared/virtualization_support.py",
    }


def test_bundle_transfer_checksum_record_has_one_printf_newline() -> None:
    applicator = load_applicator()
    command = applicator.bundle_transfer_command("/tmp/stage", "QUJD", "a" * 64)
    checksum_format = command.split("printf '", 1)[1].split("'", 1)[0]
    assert checksum_format == "%s  %s\\n"
    assert "sha256sum --check -" in command
    try:
        applicator.bundle_transfer_command("/tmp/stage", "QUJD", "not-a-digest")
    except applicator.RemoteError as exc:
        assert "lowercase SHA-256" in str(exc)
    else:
        raise AssertionError("invalid bundle digest must be rejected before transfer")


def test_remote_v28_patch_checks_source_then_backs_up_and_rolls_back() -> None:
    source = REMOTE_SCRIPT.read_text()
    assert "set -euo pipefail" in source
    assert "set -Eeuo pipefail" not in source
    assert "source=$(findmnt -n -o SOURCE --target /usr)" in source
    assert 'zfs list -H -o name,mountpoint | awk' not in source
    assert "trap rollback ERR" in source
    assert "trap 'rollback 130' INT" in source
    assert "trap 'rollback 143' TERM" in source
    assert "sha256sum --check \"$patch_dir/targets.sha256\"" in source
    assert "patch --dry-run --batch --fuzz=0 -p1" in source
    assert 'tar -czf "$backup_dir/targets.tar.gz" -T "$patch_dir/targets.list"' in source
    assert 'systemctl stop middlewared.service' in source
    assert 'zfs set readonly=on "$usr_dataset"' in source
    assert "target_after" in source
    assert "installed manifest and all patched/helper hashes verified" in source
    assert "no source or mount changes had started" in source
    assert 'midclt call system.info >/dev/null 2>&1' in source
    assert 'storage_profile=${4:?storage profile is required}' in source
    assert 'record.get("storage_profile", "path-a") == profile' in source
    assert 'Path B boot-disk-data is not ONLINE' in source
    assert 'selected_pool=boot-pool' in source
    assert 'selected_pool=boot-disk-data' in source
    assert 'expected=sys.argv[1]; assert expected in choices' in source
    assert source.index("sha256sum --check") < source.index("tar -czf \"$backup_dir/targets.tar.gz\"")
    backup_index = source.index('tar -czf "$backup_dir/targets.tar.gz"')
    stop_index = source.index("systemctl stop middlewared.service", backup_index)
    apply_index = source.index("patch --batch --fuzz=0 -p1", stop_index)
    assert backup_index < stop_index < apply_index
    assert "FAILED to verify complete restoration" in source
    assert "vm.status" in source


def test_v28_patch_selects_a_closed_storage_root_for_both_profiles() -> None:
    applicator = load_applicator()
    assert applicator.storage_root_for_profile("path-a") == (
        "boot-pool/user-data", "/mnt/boot-pool/user-data",
    )
    assert applicator.storage_root_for_profile("path-b") == (
        "boot-disk-data", "/mnt/boot-disk-data",
    )
    try:
        applicator.storage_root_for_profile("/mnt/arbitrary")
    except applicator.RemoteError as exc:
        assert "must be path-a or path-b" in str(exc)
    else:
        raise AssertionError("an unregistered storage profile must be rejected")


def test_v28_patch_postcheck_requires_selected_pool_and_mount() -> None:
    source = SCRIPT.read_text()
    assert '"pool.dataset.query", [["id", "=", dataset]]' in source
    assert 'target.call("filesystem.statfs", mountpoint' in source
    assert 'selected_pool = "boot-pool" if profile == "path-a" else "boot-disk-data"' in source
    assert 'target.call("container.pool_choices", timeout=120)' in source
    assert 'check_path_b_pool(target)' in source


def test_v28_remote_patch_records_profile_and_keeps_profile_backup_roots() -> None:
    source = REMOTE_SCRIPT.read_text()
    replacement = (ACCEPTANCE_DIR / "replace_v28_patch_remote.sh").read_text()
    for text in (source, replacement):
        assert 'storage_dataset=boot-pool/user-data' in text
        assert 'storage_dataset=boot-disk-data' in text
        assert 'storage_profile=${4:?storage profile is required}' in text
    assert '"storage_profile": profile' in source
    assert 'record.get("storage_profile", "path-a") != profile' in replacement
    assert 'expected_prior_bundle_sha256=${5:?expected prior bundle SHA-256 is required}' in replacement


def test_v28_patch_is_a_guarded_registered_networked_lane() -> None:
    config = tomllib.loads(RUN_GATE.read_text())
    lane = config["lanes"]["live-v28-patch"]
    required = set(lane["required_env"])
    assert lane["environment"] == "networked-tester"
    assert {"TRUENAS_TEST_ALLOW_MUTATIONS", "TRUENAS_EXPECTED_VERSION", "TRUENAS_STORAGE_PROFILE"} <= required
    assert "live_v28_patch.py" in lane["argv"][-1]
    assert "TRUENAS_TEST_ALLOW_REBOOT" not in required
    assert "TRUENAS_TEST_ALLOW_UPDATE" not in required
    assert '"live-v28-patch"' in NETWORKED_RUNNER.read_text()
