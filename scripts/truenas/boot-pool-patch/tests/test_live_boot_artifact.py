from __future__ import annotations

from pathlib import Path
import sys
import tomllib

import pytest


TRUENAS_ROOT = Path(__file__).parents[2]
ACCEPTANCE = TRUENAS_ROOT / "boot-pool-patch" / "acceptance" / "live_boot.py"
RUN_GATE = TRUENAS_ROOT / "run-gate.toml"
sys.path.insert(0, str(ACCEPTANCE.parent))
from live_boot import check_no_running_guests, unmount_mounted_user_data_tree
from remote import RemoteError


def test_live_boot_test_requires_two_explicit_mutation_guards_before_connecting() -> None:
    source = ACCEPTANCE.read_text()
    target_connect = source.index("target = Target.from_environment()")
    assert source.index('TRUENAS_TEST_ALLOW_MUTATIONS') < target_connect
    assert source.index('TRUENAS_TEST_ALLOW_REBOOT') < target_connect
    assert 'TRUENAS_STORAGE_PROFILE") != "path-a"' in source
    assert source.index('TRUENAS_TEST_ALLOW_REBOOT') < source.index('target.run(["systemctl", "reboot", "--no-block"]')


def test_live_boot_test_checks_both_mount_ownership_and_marker_survival() -> None:
    source = ACCEPTANCE.read_text()
    assert 'statfs.get("source") == dataset' in source
    assert 'statfs.get("dest") == mountpoint' in source
    assert 'mounted == "yes"' in source
    assert 'after == before' in source
    assert 'target.query("pool.dataset.query"' in source
    assert 'zpool status -x boot-pool' in source
    assert 'root_initially_mounted' in source
    assert 'target.call("container.pool_choices", timeout=120)' in source
    assert 'report("recovery", "boot hook mounted user-data' in source
    assert 'zfs list -H -r -t filesystem -o name,mounted' in source
    assert 'zfs unmount -r boot-pool/user-data' not in source


def test_live_boot_unmounts_only_mounted_user_data_filesystems_deepest_first() -> None:
    class FakeTarget:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def shell(self, script: str, *, timeout: int = 45, check: bool = True) -> str:
            self.calls.append((script, timeout))
            if script.startswith("zfs list "):
                return (
                    "boot-pool/user-data\tyes\n"
                    "boot-pool/user-data/child\tyes\n"
                    "boot-pool/user-data/child/grandchild\tyes\n"
                    "boot-pool/user-data/unmounted\tno\n"
                )
            return ""

    target = FakeTarget()
    unmounted = unmount_mounted_user_data_tree(target, "boot-pool/user-data")

    assert unmounted == [
        "boot-pool/user-data/child/grandchild",
        "boot-pool/user-data/child",
        "boot-pool/user-data",
    ]
    assert len(target.calls) == 2
    commands = target.calls[1][0]
    assert commands.index("zfs unmount boot-pool/user-data/child/grandchild") < commands.index(
        "zfs unmount boot-pool/user-data/child\n"
    )
    assert commands.index("zfs unmount boot-pool/user-data/child\n") < commands.index(
        "zfs unmount boot-pool/user-data\n"
    )
    assert "boot-pool/user-data/unmounted" not in commands
    assert "-f" not in commands


def test_live_boot_resolves_nullable_vm_state_and_refuses_unknown_or_running_guests() -> None:
    class FakeTarget:
        def __init__(self, vm_status: object, containers: list[dict[str, object]] | None = None) -> None:
            self.vm_status = vm_status
            self.containers = containers or []

        def query(self, method: str, *, timeout: int = 90) -> list[dict[str, object]]:
            if method == "vm.query":
                return [{"id": 7, "status": None}]
            assert method == "container.query"
            return self.containers

        def call(self, method: str, *params: object, timeout: int = 90) -> object:
            assert method == "vm.status"
            assert params == (7,)
            return self.vm_status

    check_no_running_guests(FakeTarget({"state": "STOPPED"}))

    with pytest.raises(RemoteError, match="cannot determine VM 7 runtime state"):
        check_no_running_guests(FakeTarget(None))

    with pytest.raises(RemoteError, match="refusing host reboot while VM 7 is 'RUNNING'"):
        check_no_running_guests(FakeTarget({"state": "RUNNING"}))

    with pytest.raises(RemoteError, match="cannot determine container 'busy' runtime state"):
        check_no_running_guests(FakeTarget({"state": "STOPPED"}, [{"name": "busy", "status": None}]))


def test_live_boot_reports_while_waiting_for_slow_tcg_startup() -> None:
    source = ACCEPTANCE.read_text()
    assert "next_progress = time.monotonic() + 30" in source
    assert 'report("reboot", f"waiting for SSH and middleware readiness' in source


def test_live_boot_lane_forwards_reboot_guard_and_requires_it() -> None:
    config = tomllib.loads(RUN_GATE.read_text())
    environment = config["environments"]["networked-tester"]
    lane = config["lanes"]["live-boot"]
    assert "TRUENAS_TEST_ALLOW_REBOOT" in environment["forward_env"]
    assert "TRUENAS_TEST_ALLOW_REBOOT" in lane["required_env"]
    assert lane["environment"] == "networked-tester"
    assert environment["mode"] == "exec"
    assert environment["container_name"] == "truenas-live-acceptance-runner"
    assert config["lanes"]["live-storage"]["environment"] == "networked-tester"
