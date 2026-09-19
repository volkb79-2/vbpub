"""Structural tests for the VM-only boundary around real device tests.

These tests run in the ordinary tester-unified lane and must remain safe there.
They inspect the launch contract; they never invoke Docker, QEMU, losetup, or
swap commands. The real-device behavior is exercised only by the explicit
``r1-vm-real-commit`` lane.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
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
    assert 'required_env = ["CGROUP_PARENT_DEV_GATES", "CGROUP_PARENT_DEV_INTERACTIVE", "BUILDX_BUILDER"]' in gate
    assert "testing/vm/run-vm-tests.sh" in gate
    assert "[lanes.r1-privileged-commit]" not in gate
    assert "run-privileged-tests.sh" not in gate


def test_vm_runner_is_governed_and_has_no_host_device_passthrough():
    runner = (VM / "run-vm-harness.sh").read_text()
    assert 'VM_CGROUP_PARENT="${CGROUP_PARENT_DEV_GATES:-}"' in runner
    assert 'VM_PROBE_CGROUP_PARENT="${CGROUP_PARENT_DEV_INTERACTIVE:-}"' in runner
    assert 'BUILD_BUILDER="${BUILDX_BUILDER:-}"' in runner
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
    assert 'docker buildx inspect --builder="$BUILD_BUILDER"' in runner
    assert 'docker buildx build \\' in runner
    assert '--builder="$BUILD_BUILDER"' in runner
    assert '--load' in runner
    assert 'DOCKERFILE_SHA="$(sha256sum "$HERE/Dockerfile"' in runner
    assert 'mdt.vm.dockerfile-sha=$DOCKERFILE_SHA' in runner
    assert "runner_image=\"$(docker inspect \"$NAME\" --format '{{.Image}}'" in runner
    assert "refusing to reuse a stale runner" in runner
    ensure = runner[runner.index("ensure_runner() {"):]
    assert ensure.index("verify_build_environment") < ensure.index("verify_cgroup_parent")
    assert ensure.index("verify_cgroup_parent") < ensure.index("docker buildx build")

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
    assert 'printf "%s\\t%s\\n" .Destination .Source' in runner


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
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_vm_wrapper_maps_custom_hostname_to_physical_mount(tmp_path):
    """The cockpit hostname need not be its Docker name.

    This is a fake-Docker structural acceptance test: it exercises the
    wrapper's namespace-to-host mapping without starting Docker, QEMU, or a
    real-device operation.
    """
    fake_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    hostname = Path("/etc/hostname").read_text().strip()
    mount_command = f"printf '{TESTING}\\t/home/vb/physical/testing\\n'"
    fake_docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "${1:-}" in
  ps)
    case " $* " in
      *' -q '*) printf '%s\\n' physical-id unrelated-id ;;
      *) : ;;
    esac
    ;;
  inspect)
    case "$*" in
      *Config.Hostname*)
        case "${2:-}" in
          physical-id) printf '%s\\n' "$FAKE_HOSTNAME" ;;
          *) printf '%s\\n' another-hostname ;;
        esac
        ;;
      *HostConfig.CgroupParent*) printf '%s\\n' dev-interactive.slice ;;
      *Mounts*) MOUNT_COMMAND ;;
      *) printf '%s\\n' image-id ;;
    esac
    ;;
  image) exit 1 ;;
  buildx) exit 0 ;;
  run)
    case " $* " in
      *' -d '*) printf '%s\\n' runner-id ;;
      *) : ;;
    esac
    ;;
  rm) : ;;
  *) : ;;
esac
"""
        .replace("MOUNT_COMMAND", mount_command)
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        FAKE_DOCKER_LOG=str(fake_log),
        FAKE_HOSTNAME=hostname,
        CGROUP_PARENT_DEV_GATES="dev-gates.slice",
        CGROUP_PARENT_DEV_INTERACTIVE="dev-interactive.slice",
        BUILDX_BUILDER="fake-builder",
    )
    result = subprocess.run(
        [str(VM / "run-vm-harness.sh"), "--daemon"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + "\nDocker calls:\n" + fake_log.read_text()
    calls = fake_log.read_text()
    assert "inspect physical-id" in calls
    assert "inspect unrelated-id" in calls
    expected_testing = "/home/vb/physical/testing"
    expected_project = "/home/vb/physical"
    assert f"{expected_testing}:/work:ro" in calls
    assert f"{expected_project}:/source:ro" in calls


def test_vmctl_quotes_multiple_ssh_arguments_but_preserves_one_script(tmp_path):
    state = tmp_path / "state"
    run_dir = state / "runs" / "one"
    run_dir.mkdir(parents=True)
    (run_dir / "ssh_port").write_text("2222\n")
    fake_log = tmp_path / "ssh.log"
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text(
        """#!/bin/sh
set -eu
for arg in "$@"; do printf '<%s>\\n' "$arg" >> "$FAKE_SSH_LOG"; done
"""
    )
    fake_ssh.chmod(0o755)
    env = os.environ.copy()
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        MDT_VM_STATE_DIR=str(state),
        FAKE_SSH_LOG=str(fake_log),
    )
    multiple = subprocess.run(
        [str(VM / "vmctl"), "ssh", "one", "--", "printf", "%s", "a b"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert multiple.returncode == 0, multiple.stderr
    assert "<printf %s a\\ b>" in fake_log.read_text()

    fake_log.write_text("")
    script = 'set -eu; printf "%s\\n" "$HOME"'
    single = subprocess.run(
        [str(VM / "vmctl"), "ssh", "one", "--", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert single.returncode == 0, single.stderr
    assert f"<{script}>" in fake_log.read_text()
