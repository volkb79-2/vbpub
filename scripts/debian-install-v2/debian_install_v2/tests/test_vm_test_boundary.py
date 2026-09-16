"""Structural tests for the VM-only boundary around real device tests.

These tests run in the ordinary tester-unified lane and must remain safe there.
They inspect the launch contract; they never invoke Docker, QEMU, losetup, or
swap commands. The real-device behavior is exercised only by the explicit
``r1-vm-real-commit`` lane.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT = Path(__file__).resolve().parents[2]
TESTING = PROJECT / "testing"
VM = TESTING / "vm"


def test_legacy_privileged_container_runner_is_gone():
    assert not (TESTING / "Dockerfile").exists()
    assert not (TESTING / "docker-entrypoint.sh").exists()
    assert not (TESTING / "run-privileged-tests.sh").exists()


def test_gate_routes_real_commit_lane_to_qemu_guest():
    gate = (PROJECT / "run-gate.toml").read_text()
    assert "[lanes.r1-vm-real-commit]" in gate
    assert 'description = "Real loop/swap commit tests inside an isolated QEMU/TCG guest"' in gate
    assert 'required_env = ["CGROUP_PARENT_DEV_BACKGROUND", "CGROUP_PARENT_DEV_INTERACTIVE"]' in gate
    assert "testing/vm/run-vm-tests.sh" in gate
    assert "[lanes.r1-privileged-commit]" not in gate
    assert "run-privileged-tests.sh" not in gate


def test_vm_runner_is_governed_and_has_no_host_device_passthrough():
    runner = (VM / "run-vm-harness.sh").read_text()
    assert 'VM_CGROUP_PARENT="${CGROUP_PARENT_DEV_BACKGROUND:-}"' in runner
    assert 'VM_PROBE_CGROUP_PARENT="${CGROUP_PARENT_DEV_INTERACTIVE:-}"' in runner
    assert 'VM_STATE_DIR="/var/lib/mdt-debian-install-vm/$RUNNER_FINGERPRINT"' in runner
    assert 'MDT_VM_CACHE_DIR=$VM_CACHE_DIR' in runner
    assert '--cgroup-parent="$VM_CGROUP_PARENT"' in runner
    assert "        --init \\\n" in runner
    assert '"$HOST_TESTING_DIR:/work:ro"' in runner
    assert "--privileged" not in "\n".join(
        line for line in runner.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--device=" not in runner
    assert "/dev/kvm" not in runner
    assert "/dev/net/tun" not in runner

    vmctl = (VM / "vmctl").read_text()
    assert "-accel tcg" in vmctl
    assert "-nic \"user," in vmctl
    assert 'Acquire::https::Proxy \\\"DIRECT\\\"' in vmctl
    assert "--privileged" not in "\n".join(
        line for line in vmctl.splitlines() if not line.lstrip().startswith("#")
    )


def test_vm_lane_fails_closed_and_proves_real_tests():
    lane = (VM / "run-vm-tests.sh").read_text()
    assert 'flock -n 9' in lane
    assert 'run_with_timeout 10m' in lane
    assert 'run_with_timeout 5m' in lane
    assert 'run_with_timeout 15m' in lane
    assert 'run_with_timeout 15m "$HERE/run-vm-harness.sh" prepare-base case-b' in lane
    assert 'run_with_timeout 2m "$HERE/run-vm-harness.sh" start' in lane
    assert 'run_with_timeout 10m "$HERE/run-vm-harness.sh" wait' in lane
    assert "systemd-detect-virt --container" in lane
    assert "systemd-detect-virt --vm" in lane
    assert "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" in lane
    assert "for tool in sfdisk blockdev partx losetup mkswap swapon swapoff" in lane
    assert "--junitxml=/tmp/debian-install-v2-r1.xml" in lane
    assert "VM lane did not prove both real tests" in lane


def test_cgroup_probe_requires_both_unit_file_and_live_cgroup():
    runner = (VM / "run-vm-harness.sh").read_text()
    assert 'test -f "/hostunits/$parent" &&' in runner
    assert 'inside_container=0' in runner
    assert 'refusing a namespace-wrong bind mount' in runner


def test_real_swap_tests_require_guest_virtualization_and_explicit_opt_in(monkeypatch):
    source = PROJECT / "debian_install_v2/tests/test_inuse_partition_editor_r1.py"
    spec = importlib.util.spec_from_file_location("r1_boundary_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    monkeypatch.setenv("VBPUB_ALLOW_VM_GLOBAL_SWAP_TEST", "1")
    monkeypatch.setattr(module, "_running_in_container", lambda: False)
    def qemu_probe(argv, **kwargs):
        if argv[-1] == "--container":
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout="qemu\n")

    monkeypatch.setattr(module.subprocess, "run", qemu_probe)
    assert module._is_disposable_vm_guest() is True
    monkeypatch.setattr(module, "_running_in_container", lambda: True)
    assert module._is_disposable_vm_guest() is False
    monkeypatch.setattr(module, "_running_in_container", lambda: False)

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout="docker\n")
        if argv[-1] == "--container"
        else SimpleNamespace(returncode=0, stdout="qemu\n"),
    )
    assert module._is_disposable_vm_guest() is False

    monkeypatch.delenv("VBPUB_ALLOW_VM_GLOBAL_SWAP_TEST")
    assert module._is_disposable_vm_guest() is False


@pytest.mark.parametrize("path", [VM / "run-vm-harness.sh", VM / "run-vm-tests.sh", VM / "vmctl"])
def test_vm_shell_entrypoints_parse(path):
    import subprocess

    subprocess.run(["bash", "-n", str(path)], check=True)
