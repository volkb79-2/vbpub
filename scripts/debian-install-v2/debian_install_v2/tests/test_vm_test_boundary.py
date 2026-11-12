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
    assert 'VM_STATE_DIR="/vm-lab"' in runner
    assert 'VM_DOWNLOADS_DIR="/downloads"' in runner
    assert 'HOST_VM_LAB_DIR="$HOST_PROJECT_DIR/artifacts/vm-lab"' in runner
    assert 'HOST_DOWNLOADS_DIR="$HOST_REPO_ROOT/downloads"' in runner
    # No env overrides and no main-checkout/git resolution: the layout is fixed.
    for forbidden in ("MDT_VM_STATE_DIR", "MDT_DOWNLOADS_DIR", "MDT_VM_MAIN_IMAGES_DIR",
                      "git-common-dir", "main-vm-images", 'mkdir -p "$VM_LAB_NS"'):
        assert forbidden not in runner
    assert '--state-dir "$VM_STATE_DIR" --downloads-dir "$VM_DOWNLOADS_DIR"' in runner
    assert '--mount "type=bind,src=$HOST_VM_LAB_DIR,dst=$VM_STATE_DIR"' in runner
    assert 'docker exec -i --user "$RUNNER_OWNER"' in runner
    assert '--label "mdt.vm.layout=$RUNNER_LAYOUT"' in runner
    assert "show-toplevel" not in runner
    assert '--mount "type=bind,src=$HOST_DOWNLOADS_DIR,dst=$VM_DOWNLOADS_DIR"' in runner
    assert "MDT_VM_CACHE_DIR" not in runner
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


def _layout_copy(tmp_path):
    """Copy the scripts into a tmp repo layout with the tracked placeholder
    folders, so wrapper tests never touch this checkout's vm-lab/downloads."""
    import shutil

    repo = (tmp_path / "repo").resolve()
    project = repo / "scripts" / "debian-install-v2"
    shutil.copytree(TESTING, project / "testing", ignore=shutil.ignore_patterns("__pycache__"))
    (project / "artifacts" / "vm-lab").mkdir(parents=True)
    (repo / "downloads").mkdir()
    return repo, project, project / "testing" / "vm"


def test_vm_wrapper_maps_custom_hostname_to_physical_mount(tmp_path):
    """The cockpit hostname need not be its Docker name.

    This is a fake-Docker structural acceptance test: it exercises the
    wrapper's namespace-to-host mapping without starting Docker, QEMU, or a
    real-device operation.
    """
    repo, project, vm = _layout_copy(tmp_path)
    fake_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    physical = tmp_path / "physical" / "scripts" / "debian-install-v2"
    (physical / "testing").mkdir(parents=True)
    hostname = Path("/etc/hostname").read_text().strip()
    mount_command = f"printf '{project / 'testing'}\\t{physical / 'testing'}\\n'"
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
        [str(vm / "run-vm-harness.sh"), "--daemon"],
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
    # vm-lab and downloads follow the same mapping, by fixed depth.
    assert f"type=bind,src={physical / 'artifacts' / 'vm-lab'},dst=/vm-lab" in calls
    assert f"type=bind,src={tmp_path / 'physical' / 'downloads'},dst=/downloads" in calls
    assert "/main-vm-images" not in calls


def test_vm_wrapper_maps_an_explicit_project_source_to_the_physical_mount(tmp_path):
    """A consumer may reuse the harness while its source is a nested project.

    The override must follow Docker's namespace mapping and must become part
    of the worktree-specific runner identity; passing the cockpit path directly
    to the daemon would mount the wrong host path or create an empty source.
    """
    repo, project, vm = _layout_copy(tmp_path)
    fake_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    physical = tmp_path / "physical" / "scripts" / "debian-install-v2"
    (physical / "testing").mkdir(parents=True)
    (physical / "modern-debian-tools-python-debug").mkdir()
    hostname = Path("/etc/hostname").read_text().strip()
    mount_command = (
        f"printf '{project / 'testing'}\\t{physical / 'testing'}\\n/workspaces/vbpub\\t{physical}\\n'"
    )
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
        [str(vm / "run-vm-harness.sh"), "--daemon"],
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
        FAKE_SSH_LOG=str(fake_log),
    )
    multiple = subprocess.run(
        [str(VM / "vmctl"), "--state-dir", str(state), "ssh", "one", "--", "printf", "%s", "a b"],
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
        [str(VM / "vmctl"), "--state-dir", str(state), "ssh", "one", "--", script],
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
        FAKE_QEMU_ARGS=str(qemu_args),
        CLOUD_LOCALDS_CALLED=str(cloud_localds_called),
    )
    result = subprocess.run(
        [
            str(VM / "vmctl"), "--state-dir", str(state), "start", "custom-seed",
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
    result = subprocess.run(
        [str(VM / "vmctl"), "--state-dir", str(state), "start", "bad-seed", "--seed-iso", str(seed_link)],
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
        PATH=f"{fake_bin}:{env['PATH']}",
        FAKE_VM_LOG=str(log),
    )
    result = subprocess.run(
        [
            str(VM / "vmctl"), "--state-dir", str(state), "start", "disk-size-test",
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
        [str(VM / "vmctl"), "--state-dir", str(state), "start", "invalid-disk-size", "--disk-gb", "1.5"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode != 0
    assert "positive whole number of GiB" in invalid.stderr


# --- VM layout: ownership, runner reuse, main-checkout downloads and bases ----

_IN_COCKPIT = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()

_LAYOUT_FAKE_DOCKER = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "${1:-}" in
  ps) case " $* " in
        *' -q '*) printf '%s\\n' physical-id ;;
        *) [ -z "${FAKE_RUNNING:-}" ] || printf '%s\\n' "$FAKE_RUNNING" ;;
      esac ;;
  inspect)
    if [ -n "${FAKE_RUNNING:-}" ] && [ "${2:-}" = "$FAKE_RUNNING" ]; then
      case "$*" in
        *mdt.vm.layout*) printf '%s\\n' "${FAKE_LAYOUT:-}" ;;
        *mdt.vm.owner*) printf '%s\\n' "${FAKE_OWNER:-}" ;;
        *'"/vm-lab"'*) printf '%s\\n' "${FAKE_VMLAB_SRC:-}" ;;
        *'"/downloads"'*) printf '%s\\n' "${FAKE_DL_SRC:-}" ;;
        *'"/main-vm-images"'*) printf '%s\\n' "${FAKE_MAIN_SRC:-}" ;;
        *'"/work"'*) printf '%s\\n' "${FAKE_WORK_SRC:-}" ;;
        *) printf '%s\\n' image-id ;;
      esac
      exit 0
    fi
    case "$*" in
      *Config.Hostname*) printf '%s\\n' "$FAKE_HOSTNAME" ;;
      *HostConfig.CgroupParent*) printf '%s\\n' dev-interactive.slice ;;
      *Mounts*) printf '/\\t%s\\n' "$FAKE_ROOTFS" ;;
      *) printf '%s\\n' image-id ;;
    esac ;;
  image) case "$*" in
           *dockerfile-sha*) printf '%s\\n' "${FAKE_DFSHA:-}" ;;
           *'{{.Id}}'*) printf '%s\\n' image-id ;;
           *) : ;;
         esac ;;
  buildx) exit 0 ;;
  exec) case "$*" in
          *' stat '*' /vm-lab') printf '%s\\n' "${FAKE_STAT_VMLAB:-}" ;;
          *' stat '*' /downloads') printf '%s\\n' "${FAKE_STAT_DL:-}" ;;
        esac ;;
  run) case " $* " in *' -d '*) printf '%s\\n' runner-id ;; esac ;;
  *) : ;;
esac
"""


def _layout_env(tmp_path, **extra):
    (tmp_path / "docker").write_text(_LAYOUT_FAKE_DOCKER)
    (tmp_path / "docker").chmod(0o755)
    env = os.environ.copy()
    env.update(
        PATH=f"{tmp_path}:{env['PATH']}",
        FAKE_DOCKER_LOG=str(tmp_path / "docker.log"),
        FAKE_HOSTNAME=Path("/etc/hostname").read_text().strip(),
        FAKE_ROOTFS=str(tmp_path / "rootfs"),
        CGROUP_PARENT_DEV_GATES="dev-gates.slice",
        CGROUP_PARENT_DEV_INTERACTIVE="dev-interactive.slice",
        BUILDX_BUILDER="fake-builder",
    )
    env.update(extra)
    return env


def _host(tmp_path, path):
    return f"{tmp_path / 'rootfs'}{path}" if _IN_COCKPIT else str(path)


def test_vm_wrapper_runs_vmctl_as_the_vm_lab_owner_never_root(tmp_path):
    repo, project, vm = _layout_copy(tmp_path)
    # Env overrides do not exist: these are ignored, the fixed layout is used.
    env = _layout_env(tmp_path, MDT_VM_STATE_DIR="/elsewhere", MDT_DOWNLOADS_DIR="/elsewhere")
    result = subprocess.run(
        [str(vm / "run-vm-harness.sh"), "prepare-base", "case-b"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "docker.log").read_text()
    owner = f"{os.getuid()}:{os.getgid()}"
    assert f"--label mdt.vm.owner={owner}" in calls
    assert "--label mdt.vm.layout=vm-lab-3" in calls
    assert f"exec -i --user {owner} " in calls
    assert "./vmctl --state-dir /vm-lab --downloads-dir /downloads prepare-base case-b" in calls
    assert f"src={_host(tmp_path, project / 'artifacts/vm-lab')},dst=/vm-lab" in calls
    assert f"src={_host(tmp_path, repo / 'downloads')},dst=/downloads" in calls
    import hashlib
    default_fingerprint = hashlib.sha256(f"{project / 'testing'}\0".encode()).hexdigest()[:12]
    assert f"debian-install-vm:{default_fingerprint}" in calls
    assert f"debian-install-vm-harness-{default_fingerprint}" in calls
    assert "elsewhere" not in calls and "/main-vm-images" not in calls


def test_vm_wrapper_accepts_mdt_vm_project_dir(tmp_path):
    repo, project, vm = _layout_copy(tmp_path)
    consumer = repo / "scripts" / "mounttrace"
    cow_state = repo / "cow-state" / "mounttrace"
    cow_state.mkdir(parents=True)
    (consumer / "artifacts").mkdir(parents=True)
    (consumer / "artifacts" / "vm-lab").symlink_to(cow_state, target_is_directory=True)
    env = _layout_env(tmp_path, MDT_VM_PROJECT_DIR=str(consumer))
    result = subprocess.run(
        [str(vm / "run-vm-harness.sh"), "prepare-base", "case-b"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "docker.log").read_text()
    import hashlib
    expected = hashlib.sha256(
        f"{project / 'testing'}\0{consumer}\0{consumer}".encode()
    ).hexdigest()[:12]
    assert f"debian-install-vm:{expected}" in calls
    assert f"debian-install-vm-harness-{expected}" in calls
    assert f"src={_host(tmp_path, consumer)},dst=/source,readonly" in calls
    assert f"src={_host(tmp_path, cow_state)},dst=/vm-lab" in calls


def test_vm_wrapper_fingerprints_source_dir_separately_from_state_project(tmp_path):
    repo, project, vm = _layout_copy(tmp_path)
    source_dir = repo / "scripts" / "mounttrace"
    source_dir.mkdir(parents=True)
    env = _layout_env(tmp_path, MDT_VM_PROJECT_DIR=str(project), MDT_VM_SOURCE_DIR=str(source_dir))
    result = subprocess.run(
        [str(vm / "run-vm-harness.sh"), "prepare-base", "case-b"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "docker.log").read_text()
    import hashlib
    expected = hashlib.sha256(
        f"{project / 'testing'}\0{source_dir}\0{project}".encode()
    ).hexdigest()[:12]
    default = hashlib.sha256(f"{project / 'testing'}\0".encode()).hexdigest()[:12]
    assert expected != default
    assert f"debian-install-vm:{expected}" in calls
    assert f"debian-install-vm-harness-{expected}" in calls
    assert f"src={_host(tmp_path, source_dir)},dst=/source,readonly" in calls


@pytest.mark.parametrize("relative", ["scripts", "scripts/a/b", "outside/project"])
def test_vm_wrapper_rejects_invalid_mdt_vm_project_dir_shapes(tmp_path, relative):
    repo, _project, vm = _layout_copy(tmp_path)
    candidate = repo / relative
    candidate.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(vm / "run-vm-harness.sh"), "prepare-base", "case-b"],
        env=_layout_env(tmp_path, MDT_VM_PROJECT_DIR=str(candidate)),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "MDT_VM_PROJECT_DIR must be a project of this checkout" in result.stderr
    assert not (tmp_path / "docker.log").exists()


def test_vm_wrapper_refuses_a_checkout_missing_its_placeholder_folders(tmp_path):
    repo, project, vm = _layout_copy(tmp_path)
    (repo / "downloads").rmdir()
    result = subprocess.run(
        [str(vm / "run-vm-harness.sh"), "--daemon"],
        env=_layout_env(tmp_path), capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "tracked placeholder" in result.stderr
    assert not (repo / "downloads").exists()

    missing_lab = tmp_path / "missing-lab"
    _repo2, project2, vm2 = _layout_copy(missing_lab)
    (project2 / "artifacts" / "vm-lab").rmdir()
    result = subprocess.run(
        [str(vm2 / "run-vm-harness.sh"), "prepare-base", "case-b"],
        env=_layout_env(missing_lab), capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "owning project's vm-lab setup to create its CoW symlink" in result.stderr
    assert not (project2 / "artifacts" / "vm-lab").exists()


def _first_runner(tmp_path, vm):
    env = _layout_env(tmp_path)
    first = subprocess.run(
        [str(vm / "run-vm-harness.sh"), "--daemon"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert first.returncode == 0, first.stderr
    import re
    calls = (tmp_path / "docker.log").read_text()
    name = re.search(r"run -d --name (\S+)", calls).group(1)
    (tmp_path / "docker.log").write_text("")
    return name


def _reuse_env(tmp_path, layout, name, **overrides):
    import hashlib
    repo, project, vm = layout
    vmlab, dl = project / "artifacts" / "vm-lab", repo / "downloads"
    ids = {k: f"{os.stat(p).st_dev}:{os.stat(p).st_ino}" for k, p in (("vm", vmlab), ("dl", dl))}
    values = dict(
        FAKE_RUNNING=name,
        FAKE_LAYOUT="vm-lab-3",
        FAKE_OWNER=f"{os.getuid()}:{os.getgid()}",
        FAKE_VMLAB_SRC=_host(tmp_path, vmlab),
        FAKE_DL_SRC=_host(tmp_path, dl),
        FAKE_WORK_SRC=_host(tmp_path, project / "testing"),
        FAKE_DFSHA=hashlib.sha256((vm / "Dockerfile").read_bytes()).hexdigest(),
        FAKE_STAT_VMLAB=ids["vm"],
        FAKE_STAT_DL=ids["dl"],
    )
    values.update(overrides)
    return _layout_env(tmp_path, **values)


def test_vm_wrapper_reuses_a_runner_only_when_layout_mounts_and_inodes_match(tmp_path):
    layout = _layout_copy(tmp_path)
    vm = layout[2]
    name = _first_runner(tmp_path, vm)
    ok = subprocess.run(
        [str(vm / "run-vm-harness.sh"), "--daemon"],
        env=_reuse_env(tmp_path, layout, name), capture_output=True, text=True, check=False,
    )
    assert ok.returncode == 0, ok.stderr
    assert "run -d" not in (tmp_path / "docker.log").read_text()

    def refused(**overrides):
        result = subprocess.run(
            [str(vm / "run-vm-harness.sh"), "--daemon"],
            env=_reuse_env(tmp_path, layout, name, **overrides),
            capture_output=True, text=True, check=False,
        )
        assert result.returncode != 0
        assert "refusing to reuse a stale runner" in result.stderr
        assert "--stop-daemon" in result.stderr
        return result.stderr

    # A runner started by older code (no layout label / no new mounts).
    assert "older code" in refused(FAKE_LAYOUT="")
    assert "mount /vm-lab" in refused(FAKE_VMLAB_SRC="")
    assert "mount /downloads" in refused(FAKE_DL_SRC=_host(tmp_path, tmp_path / "elsewhere"))
    # Folder deleted/replaced/mounted over after the runner started.
    assert "deleted, replaced" in refused(FAKE_STAT_VMLAB="1:2")
    assert "runs as" in refused(FAKE_OWNER="0:0")


def _stub_download_script(testing_dir, tmp_path):
    """Replace the network downloader in a COPY with a stub recording its arg."""
    stub = testing_dir / "download-base-image.sh"
    stub.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"${1:-}\" > \"$STUB_ARG_LOG\"\n"
        "printf 'cloud image' > \"$1/stub.qcow2\"\n"
        "printf '%s\\n' \"$1/stub.qcow2\"\n"
    )
    stub.chmod(0o755)


def test_vmctl_derives_vm_lab_and_downloads_by_fixed_depth_and_stages_bases(tmp_path):
    repo, project, vm = _layout_copy(tmp_path)
    _stub_download_script(project / "testing", tmp_path)
    arg_log = tmp_path / "arg.log"
    env = os.environ.copy()
    env.update(STUB_ARG_LOG=str(arg_log), MDT_VM_STATE_DIR="/ignored", MDT_DOWNLOADS_DIR="/ignored")
    result = subprocess.run(
        [str(vm / "vmctl"), "prepare-base", "case-b"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    # Defaults: this checkout's <repo>/downloads and <project>/artifacts/vm-lab.
    assert arg_log.read_text().strip() == str(repo / "downloads")
    base = project / "artifacts/vm-lab/images/case-b-base.qcow2"
    assert base.read_bytes() == b"cloud image"
    assert not Path("/ignored").exists()

    # Explicit global options (what the in-runner wrapper passes).
    other = tmp_path / "other-state"
    result = subprocess.run(
        [str(vm / "vmctl"), "--state-dir", str(other), "--downloads-dir", str(tmp_path / "dl"),
         "prepare-base", "case-b"],
        env={**env, "STUB_ARG_LOG": str(arg_log)}, capture_output=True, text=True, check=False,
    )
    # The stub cannot write into a missing --downloads-dir; the arg is what matters.
    assert arg_log.read_text().strip() == str(tmp_path / "dl")

    # Staged bases are write-once: a second prepare-base leaves the base alone.
    base.write_bytes(b"in use by overlays")
    again = subprocess.run(
        [str(vm / "vmctl"), "prepare-base", "case-b"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert again.returncode == 0 and "already staged" in again.stderr
    assert base.read_bytes() == b"in use by overlays"

    missing = subprocess.run(
        [str(vm / "vmctl"), "start", "r1", "--case", "nope"],
        env=env, capture_output=True, text=True, check=False,
    )
    assert missing.returncode != 0 and "no base image" in missing.stderr


def test_download_script_takes_a_downloads_dir_argument_and_never_creates_it(tmp_path):
    missing = tmp_path / "no-such-downloads"
    result = subprocess.run(
        [str(TESTING / "download-base-image.sh"), str(missing)],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0
    assert "placeholder" in result.stderr
    assert not missing.exists()
    script = (TESTING / "download-base-image.sh").read_text()
    assert "flock 9" in script
    assert "MDT_DOWNLOADS_DIR" not in script and "git-common-dir" not in script


def test_vmctl_serializes_key_generation_and_rejects_overlong_socket_paths(tmp_path):
    script = (VM / "vmctl").read_text()
    assert "flock 8" in script
    assert "--reflink=auto" in script
    state = tmp_path / ("x" * 100)
    (state / "images").mkdir(parents=True)
    (state / "images" / "case-b-base.qcow2").write_bytes(b"b")
    seed = tmp_path / "seed.iso"
    seed.write_bytes(b"s")
    result = subprocess.run(
        [str(VM / "vmctl"), "--state-dir", str(state), "start", "r1", "--no-apt-cache",
         "--seed-iso", str(seed)],
        capture_output=True, text=True, check=False,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0
    assert "too long for the QMP socket" in result.stderr
