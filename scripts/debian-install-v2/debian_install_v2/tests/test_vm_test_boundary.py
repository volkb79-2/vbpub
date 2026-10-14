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
    assert '--mount "type=bind,src=$HOST_TESTING_DIR,dst=/work,readonly"' in runner
    assert '--mount "type=bind,src=$HOST_SOURCE_DIR,dst=/source,readonly"' in runner
    assert '-v "$HOST_TESTING_DIR:/work:ro"' not in runner
    assert '-v "$HOST_SOURCE_DIR:/source:ro"' not in runner
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
    physical = tmp_path / "physical"
    (physical / "testing").mkdir(parents=True)
    hostname = Path("/etc/hostname").read_text().strip()
    mount_command = f"printf '{TESTING}\\t{physical / 'testing'}\\n'"
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
    expected_testing = str(physical / "testing")
    expected_project = str(physical)
    assert f"type=bind,src={expected_testing},dst=/work,readonly" in calls
    assert f"type=bind,src={expected_project},dst=/source,readonly" in calls


def test_vm_wrapper_maps_an_explicit_project_source_to_the_physical_mount(tmp_path):
    """A consumer may reuse the harness while its source is a nested project.

    The override must follow Docker's namespace mapping and must become part
    of the worktree-specific runner identity; passing the cockpit path directly
    to the daemon would mount the wrong host path or create an empty source.
    """
    fake_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    physical = tmp_path / "physical"
    (physical / "testing").mkdir(parents=True)
    (physical / "modern-debian-tools-python-debug").mkdir()
    hostname = Path("/etc/hostname").read_text().strip()
    mount_command = f"printf '{TESTING}\\t{physical / 'testing'}\\n/workspaces/vbpub\\t{physical}\\n'"
    fake_docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "${1:-}" in
  ps) case " $* " in *' -q '*) printf '%s\\n' physical-id ;; esac ;;
  inspect)
    case "$*" in
      *Config.Hostname*) printf '%s\\n' "$FAKE_HOSTNAME" ;;
      *HostConfig.CgroupParent*) printf '%s\\n' dev-interactive.slice ;;
      *Mounts*) MOUNT_COMMAND ;;
      *) printf '%s\\n' image-id ;;
    esac ;;
  image) exit 1 ;;
  buildx) exit 0 ;;
  run) case " $* " in *' -d '*) printf '%s\\n' runner-id ;; esac ;;
  rm) : ;;
  *) : ;;
esac
""".replace("MOUNT_COMMAND", mount_command)
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        FAKE_DOCKER_LOG=str(fake_log),
        FAKE_HOSTNAME=hostname,
        MDT_VM_SOURCE_DIR="/workspaces/vbpub/modern-debian-tools-python-debug",
        CGROUP_PARENT_DEV_GATES="dev-gates.slice",
        CGROUP_PARENT_DEV_INTERACTIVE="dev-interactive.slice",
        BUILDX_BUILDER="fake-builder",
    )
    result = subprocess.run(
        [str(VM / "run-vm-harness.sh"), "--daemon"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr + "\nDocker calls:\n" + fake_log.read_text()
    calls = fake_log.read_text()
    assert f"type=bind,src={physical / 'modern-debian-tools-python-debug'},dst=/source,readonly" in calls


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


def test_vmctl_start_uses_a_supplied_seed_iso_without_rewriting_it(tmp_path):
    state = tmp_path / "state"
    images = state / "images"
    images.mkdir(parents=True)
    (images / "case-b-base.qcow2").write_bytes(b"base fixture")
    source_iso = tmp_path / "private-seed.iso"
    source_iso.write_bytes(b"NoCloud seed fixture")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    (fake_bin / "ssh-keygen").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "key=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-f\" ]; then shift; key=\"$1\"; fi\n"
        "  shift\n"
        "done\n"
        ": > \"$key\"\n"
        ": > \"$key.pub\"\n"
    )
    (fake_bin / "qemu-img").write_text("#!/bin/sh\nexit 0\n")
    qemu_args = tmp_path / "qemu-args.log"
    (fake_bin / "qemu-system-x86_64").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_QEMU_ARGS\"\n"
        "pidfile=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-pidfile\" ]; then shift; pidfile=\"$1\"; fi\n"
        "  shift\n"
        "done\n"
        "printf '999999\\n' > \"$pidfile\"\n"
    )
    cloud_localds_called = tmp_path / "cloud-localds-called"
    (fake_bin / "cloud-localds").write_text(
        "#!/bin/sh\n: > \"$CLOUD_LOCALDS_CALLED\"\nexit 91\n"
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)

    env = os.environ.copy()
    env.update(
        PATH=f"{fake_bin}:{env['PATH']}",
        MDT_VM_STATE_DIR=str(state),
        FAKE_QEMU_ARGS=str(qemu_args),
        CLOUD_LOCALDS_CALLED=str(cloud_localds_called),
    )
    result = subprocess.run(
        [
            str(VM / "vmctl"), "start", "custom-seed",
            "--case", "case-b", "--no-apt-cache",
            "--seed-iso", str(source_iso),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    run_dir = state / "runs" / "custom-seed"
    attached_iso = run_dir / "seed.iso"
    assert attached_iso.read_bytes() == source_iso.read_bytes()
    assert attached_iso.stat().st_mode & 0o777 == 0o600
    assert not cloud_localds_called.exists()
    assert f"file={run_dir}/seed.iso,format=raw,media=cdrom,readonly=on" in qemu_args.read_text()
    assert source_iso.read_bytes() == b"NoCloud seed fixture"


def test_vmctl_rejects_a_symlink_custom_seed_before_creating_a_run(tmp_path):
    target = tmp_path / "real-seed.iso"
    target.write_bytes(b"seed")
    seed_link = tmp_path / "seed.iso"
    seed_link.symlink_to(target)
    state = tmp_path / "state"
    env = os.environ.copy()
    env["MDT_VM_STATE_DIR"] = str(state)
    result = subprocess.run(
        [str(VM / "vmctl"), "start", "bad-seed", "--seed-iso", str(seed_link)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "not a symlink" in result.stderr
    assert not (state / "runs" / "bad-seed").exists()


def test_vmctl_grows_only_the_per_run_overlay_before_qemu_start(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    state = tmp_path / "state"
    images = state / "images"
    images.mkdir(parents=True)
    base = images / "case-b-base.qcow2"
    base.write_bytes(b"immutable-base-fixture")
    seed_iso = tmp_path / "private.iso"
    seed_iso.write_bytes(b"private-seed-fixture")

    (fake_bin / "qemu-img").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'qemu-img %s\\n' \"$*\" >> \"$FAKE_VM_LOG\"\n"
        "if [ \"$1\" = create ]; then\n"
        "  for arg do last=$arg; done\n"
        "  : > \"$last\"\n"
        "fi\n"
    )
    (fake_bin / "qemu-system-x86_64").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "printf 'qemu-system-x86_64 %s\\n' \"$*\" >> \"$FAKE_VM_LOG\"\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = -pidfile ]; then printf '99999999\\n' > \"$2\"; shift 2; else shift; fi\n"
        "done\n"
    )
    (fake_bin / "ssh-keygen").write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = -f ]; then key=$2; shift 2; else shift; fi\n"
        "done\n"
        ": > \"$key\"\n"
        "printf 'ssh-ed25519 fixture\\n' > \"$key.pub\"\n"
    )
    for executable in fake_bin.iterdir():
        executable.chmod(0o755)

    env = os.environ.copy()
    env.update(
        MDT_VM_STATE_DIR=str(state),
        PATH=f"{fake_bin}:{env['PATH']}",
        FAKE_VM_LOG=str(log),
    )
    result = subprocess.run(
        [
            str(VM / "vmctl"), "start", "disk-size-test",
            "--disk-gb", "20", "--seed-iso", str(seed_iso), "--no-apt-cache",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    calls = log.read_text().splitlines()
    resize = next(i for i, call in enumerate(calls) if call.startswith("qemu-img resize "))
    start = next(i for i, call in enumerate(calls) if call.startswith("qemu-system-x86_64 "))
    assert resize < start
    assert calls[resize].endswith(" 20G")
    assert base.read_bytes() == b"immutable-base-fixture"
    assert (state / "runs/disk-size-test/disk.qcow2").is_file()

    invalid = subprocess.run(
        [str(VM / "vmctl"), "start", "invalid-disk-size", "--disk-gb", "1.5"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode != 0
    assert "positive whole number of GiB" in invalid.stderr
