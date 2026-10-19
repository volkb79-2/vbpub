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
