"""Plain Docker-host start mode and rollback-launcher parity, at a fake Docker boundary."""

from __future__ import annotations

import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from vm_lab import GENERIC_PROFILE, RunnerError
from vm_lab import core as vm_lab


QCOW2_BYTES = b"QFI\xfb" + b"\x00" * 60
LEVELS = [{
    "path": "/dev.slice/dev-gates.slice",
    "cpu_max": 4.0,
    "memory_high": 6 * 1024**3,
    "memory_max": 8 * 1024**3,
    "memory_current": 1024**3,
    "memory_psi_some_avg10": 0.0,
    "memory_psi_full_avg10": 0.0,
}]


def completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class DockerBoundary:
    """Capture `docker run` and stop before any container or daemon work."""

    def __init__(self) -> None:
        self.create_argv: list[str] | None = None

    def __call__(self, argv, **_kwargs):
        if argv[:2] == ["docker", "run"]:
            self.create_argv = list(argv)
            raise RunnerError("stop after capturing the Docker container")
        if argv[:2] == ["docker", "inspect"]:
            return completed(argv, 1, "", "absent")
        if argv[:3] == ["docker", "ps", "-q"]:
            return completed(argv, 0, "", "")
        raise AssertionError(f"unexpected Docker call: {argv!r}")


class ForbiddenDocker:
    def __call__(self, argv, **_kwargs):
        raise AssertionError(f"Docker was called before admission completed: {argv!r}")


def start_arguments(
    root: Path,
    *extra: str,
    include_slice: bool = True,
) -> list[str]:
    state = root / "state"
    state.mkdir(exist_ok=True)
    base = root / "base.qcow2"
    code = root / "OVMF_CODE_4M.fd"
    variables = root / "OVMF_VARS_4M.fd"
    if not base.exists():
        base.write_bytes(QCOW2_BYTES)
    code.write_bytes(b"code")
    variables.write_bytes(b"vars")
    arguments = [
        "start",
        "--name", "p30-guest",
        "--base-disk", str(base),
        "--ovmf-code", str(code),
        "--ovmf-vars", str(variables),
        "--state-dir", str(state),
        "--image", "img:1",
        "--network", "p30-guest-net",
        "--memory-mib", "2048",
        "--cpus", "2",
        "--forward", "2222:22",
        "--display-device", "std",
        *extra,
    ]
    if include_slice:
        arguments.extend(["--cgroup-parent", "dev-gates.slice"])
    return arguments


def install_start_fakes(monkeypatch, root: Path, boundary, *, plain_host: bool) -> None:
    root = root.resolve()
    monkeypatch.setattr(vm_lab, "docker_run", boundary)
    monkeypatch.setattr(vm_lab, "inspect_network", lambda _name: None)
    monkeypatch.setattr(vm_lab, "image_id", lambda _name: "sha256:image")
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: root / "admission.lock")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    if plain_host:
        monkeypatch.setattr(
            vm_lab,
            "inspect_current_container",
            lambda: pytest.fail("plain-host start called cockpit self-inspection"),
        )
        monkeypatch.setattr(
            vm_lab,
            "find_cgroup_inspector",
            lambda: pytest.fail("plain-host start looked for a cgroup inspector container"),
        )
        monkeypatch.setattr(
            vm_lab,
            "plain_host_cgroup_preflight",
            lambda *_args, **_kwargs: (LEVELS, "/dev.slice/dev-gates.slice"),
        )
    else:
        mounts = [{"Destination": str(root), "Source": "/host" + str(root), "RW": True}]
        monkeypatch.setattr(
            vm_lab,
            "inspect_current_container",
            lambda: ("cockpit-id", {"Mounts": mounts}),
        )
        monkeypatch.setattr(
            vm_lab,
            "cgroup_preflight",
            lambda *_args, **_kwargs: ("inspector-id", LEVELS, "/dev.slice/dev-gates.slice"),
        )


def run_to_docker(root: Path, monkeypatch, *extra: str, plain_host: bool = True):
    boundary = DockerBoundary()
    install_start_fakes(monkeypatch, root, boundary, plain_host=plain_host)
    arguments = start_arguments(root, *extra)
    if plain_host:
        arguments.append("--plain-host")
    result = vm_lab.main(GENERIC_PROFILE, arguments)
    assert boundary.create_argv is not None
    return result, boundary.create_argv


def option_values(argv: list[str], option: str) -> list[str]:
    return [
        argv[index + 1]
        for index, item in enumerate(argv[:-1])
        if item == option
    ]


def one_value(argv: list[str], option: str) -> str:
    values = option_values(argv, option)
    assert len(values) == 1
    return values[0]


def docker_configuration(argv: list[str]) -> dict[str, object]:
    environment = dict(value.split("=", 1) for value in option_values(argv, "--env"))
    return {
        "name": one_value(argv, "--name"),
        "cgroup_parent": one_value(argv, "--cgroup-parent"),
        "cpus": one_value(argv, "--cpus"),
        "cpu_shares": option_values(argv, "--cpu-shares"),
        "memory": one_value(argv, "--memory"),
        "memory_swap": one_value(argv, "--memory-swap"),
        "pids_limit": one_value(argv, "--pids-limit"),
        "cap_drop": option_values(argv, "--cap-drop"),
        "cap_add": option_values(argv, "--cap-add"),
        "security_opts": option_values(argv, "--security-opt"),
        "read_only": "--read-only" in argv,
        "tmpfs": option_values(argv, "--tmpfs"),
        "network": one_value(argv, "--network"),
        "user": one_value(argv, "--user"),
        "mounts": option_values(argv, "--mount"),
        "environment": environment,
        "pid_namespace": option_values(argv, "--pid"),
        "privileged": "--privileged" in argv,
        "devices": option_values(argv, "--device"),
        "publish": option_values(argv, "--publish") + option_values(argv, "-p"),
        "entrypoint": one_value(argv, "--entrypoint"),
        "guest_script": one_value(argv, "-c"),
    }


def assert_common_hardening(configuration: dict[str, object]) -> None:
    assert configuration["cap_drop"] == ["ALL"]
    assert configuration["cap_add"] == []
    assert configuration["security_opts"] == ["no-new-privileges"]
    assert configuration["read_only"] is True
    assert configuration["tmpfs"] == ["/tmp:rw,noexec,nosuid,size=64m"]
    assert configuration["pids_limit"] == "128"
    assert configuration["pid_namespace"] == []
    assert configuration["privileged"] is False
    assert configuration["devices"] == []
    assert configuration["publish"] == []


def test_plain_host_start_places_a_hardened_guest_in_the_named_slice(monkeypatch, tmp_path):
    result, argv = run_to_docker(tmp_path, monkeypatch, "--cpu-shares", "64")
    assert result == 1  # the fake boundary stops at `docker run`
    configuration = docker_configuration(argv)
    assert configuration["name"] == "p30-guest"
    assert configuration["cgroup_parent"] == "dev-gates.slice"
    assert configuration["network"] == "p30-guest-net"
    assert configuration["cpus"] == "2"
    assert configuration["cpu_shares"] == ["64"]
    assert configuration["memory"] == str(2560 * 1024 * 1024)
    assert configuration["memory_swap"] == configuration["memory"]
    assert configuration["user"] == f"{os.getuid()}:{os.getgid()}"
    assert_common_hardening(configuration)
    assert f"type=bind,src={tmp_path.resolve() / 'state'},dst=/state" in configuration["mounts"]
    base_mount = (
        f"type=bind,src={tmp_path.resolve() / 'base.qcow2'},"
        "dst=/base/disk.qcow2,readonly"
    )
    assert base_mount in configuration["mounts"]
    assert "VM_CONFIG_DISK" not in configuration["environment"]
    assert "vmlabdatacfg" not in configuration["guest_script"]


def test_cockpit_mode_resolves_local_mounts_through_self_inspection(monkeypatch, tmp_path):
    result, argv = run_to_docker(
        tmp_path,
        monkeypatch,
        "--cpu-shares", "64",
        plain_host=False,
    )
    assert result == 1  # the fake boundary stops at `docker run`
    configuration = docker_configuration(argv)
    root = tmp_path.resolve()
    assert f"type=bind,src=/host{root / 'state'},dst=/state" in configuration["mounts"]
    base_mount = (
        f"type=bind,src=/host{root / 'base.qcow2'},"
        "dst=/base/disk.qcow2,readonly"
    )
    assert base_mount in configuration["mounts"]
    assert configuration["cgroup_parent"] == "dev-gates.slice"
    assert configuration["cpu_shares"] == ["64"]
    assert configuration["user"] == f"{os.getuid()}:{os.getgid()}"
    assert_common_hardening(configuration)


@pytest.mark.parametrize("include_slice", [False])
def test_plain_host_start_without_a_slice_refuses_before_docker(monkeypatch, tmp_path, capsys, include_slice):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
    arguments = start_arguments(tmp_path, include_slice=include_slice) + ["--plain-host"]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "pass --cgroup-parent UNIT or set CGROUP_PARENT_DEV_BACKGROUND" in capsys.readouterr().err


@pytest.mark.parametrize(
    "slice_name",
    [
        ".slice",
        "/dev.slice/dev-gates.slice",
        "dev-gates.slice/child",
        "../dev-gates.slice",
        "dev-gates.slice:escaped",
    ],
)
def test_plain_host_start_refuses_a_non_unit_slice_name(monkeypatch, tmp_path, capsys, slice_name):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    arguments = start_arguments(tmp_path) + ["--plain-host", "--cgroup-parent", slice_name]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "expected a plain systemd *.slice unit name" in capsys.readouterr().err


def test_explicit_empty_slice_refuses_instead_of_falling_back(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    arguments = start_arguments(tmp_path) + ["--plain-host", "--cgroup-parent", ""]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "refusing an unplaced VM" in capsys.readouterr().err


@pytest.mark.parametrize("network", ["bridge", "host", "none", "container:peer"])
def test_plain_host_start_refuses_a_fixed_network_before_docker(monkeypatch, tmp_path, capsys, network):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    arguments = start_arguments(tmp_path) + ["--plain-host", "--network", network]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "must name a dedicated user-defined Docker network" in capsys.readouterr().err


def test_plain_host_preflight_refuses_an_unloaded_slice(monkeypatch):
    monkeypatch.setattr(
        vm_lab,
        "plain_host_systemd_property",
        lambda _unit, prop: "not-found" if prop == "LoadState" else "/dev.slice/dev-gates.slice",
    )
    with pytest.raises(RunnerError, match="LoadState='not-found'; refusing auto-created slices"):
        vm_lab.plain_host_systemd_slice_path("dev-gates.slice")


def test_plain_host_preflight_reads_the_loaded_slice_and_local_cgroup(monkeypatch):
    monkeypatch.setattr(
        vm_lab,
        "plain_host_systemd_property",
        lambda _unit, prop: {
            "LoadState": "loaded",
            "ControlGroup": "/dev.slice/dev-gates.slice",
        }[prop],
    )
    monkeypatch.setattr(vm_lab, "local_cgroup_levels", lambda _path: LEVELS)
    levels, control_group = vm_lab.plain_host_cgroup_preflight(
        "dev-gates.slice",
        requested_cpus=2.0,
    )
    assert levels == LEVELS
    assert control_group == "/dev.slice/dev-gates.slice"


def test_plain_host_systemd_property_refuses_a_systemctl_failure(monkeypatch):
    monkeypatch.setattr(
        vm_lab,
        "docker_run",
        lambda argv, **_kwargs: completed(argv, 1, "", "systemd is unavailable"),
    )
    with pytest.raises(RunnerError, match="cannot read systemd property LoadState"):
        vm_lab.plain_host_systemd_property("dev-gates.slice", "LoadState")


def test_plain_host_systemd_property_refuses_an_empty_value(monkeypatch):
    monkeypatch.setattr(
        vm_lab,
        "docker_run",
        lambda argv, **_kwargs: completed(argv, 0, "", ""),
    )
    with pytest.raises(RunnerError, match="systemd returned no LoadState value"):
        vm_lab.plain_host_systemd_property("dev-gates.slice", "LoadState")


def test_local_pid_cgroup_reads_the_direct_proc_entry(monkeypatch, tmp_path):
    proc = tmp_path / "proc"
    cgroup = proc / "4321" / "cgroup"
    cgroup.parent.mkdir(parents=True)
    cgroup.write_text("0::/dev.slice/dev-gates.slice/docker-container.scope\n")
    monkeypatch.setattr(vm_lab, "HOST_PROC_ROOT", proc)
    assert vm_lab.local_pid_cgroup(4321) == "/dev.slice/dev-gates.slice/docker-container.scope"


@pytest.mark.parametrize("pid", [0, -1, True])
def test_local_pid_cgroup_refuses_an_invalid_docker_pid(pid):
    with pytest.raises(RunnerError, match="invalid host PID"):
        vm_lab.local_pid_cgroup(pid)


def test_plain_host_start_uses_the_local_daemon_namespace(monkeypatch, tmp_path):
    media = tmp_path / "seed.iso"
    config = tmp_path / "config.img"
    media.write_bytes(b"seed")
    config.write_bytes(b"config")
    _, argv = run_to_docker(
        tmp_path,
        monkeypatch,
        "--seed-iso", str(media),
        "--boot-order", "dc",
        "--config-disk", str(config),
        "--cpu-shares", "64",
        "--data-disk-size", "8589934592",
    )
    configuration = docker_configuration(argv)
    assert f"type=bind,src={media.resolve()},dst=/media/vm-lab-readonly.iso,readonly" in configuration["mounts"]
    assert f"type=bind,src={config.resolve()},dst=/media/config.img,readonly" in configuration["mounts"]
    assert configuration["environment"]["VM_READONLY_MEDIA"] == "/media/vm-lab-readonly.iso"
    assert configuration["environment"]["VM_CONFIG_DISK"] == "/media/config.img"
    assert configuration["environment"]["VM_EXTRA_DISK_SIZES_BYTES"] == "8589934592"
    script = configuration["guest_script"]
    assert "-boot order=dc,menu=on" in script
    assert "id=vmlabdatacfg,format=raw,readonly=on" in script
    assert "serial=VMLABCFG,addr=0x6" in script


def _start_with_media_alias(monkeypatch, tmp_path, option: str) -> dict[str, object]:
    media = tmp_path / "seed.iso"
    media.write_bytes(b"seed")
    _, argv = run_to_docker(tmp_path, monkeypatch, option, str(media), "--cpu-shares", "64")
    return docker_configuration(argv)


@pytest.mark.parametrize("option", ["--readonly-media", "--iso", "--seed-iso"])
def test_every_cdrom_alias_attaches_the_same_read_only_media(monkeypatch, tmp_path, option):
    configuration = _start_with_media_alias(monkeypatch, tmp_path, option)
    media_mount = (
        f"type=bind,src={(tmp_path / 'seed.iso').resolve()},"
        "dst=/media/vm-lab-readonly.iso,readonly"
    )
    assert media_mount in configuration["mounts"]
    assert configuration["environment"]["VM_READONLY_MEDIA"] == "/media/vm-lab-readonly.iso"


def test_conflicting_cdrom_aliases_are_a_usage_error(tmp_path, capsys):
    arguments = start_arguments(tmp_path)
    arguments.extend(["--iso", "one.iso", "--seed-iso", "two.iso"])
    with pytest.raises(SystemExit) as exit_info:
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(arguments)
    assert exit_info.value.code == 2
    assert "specify only one of --readonly-media, --iso, and --seed-iso" in capsys.readouterr().err


def test_boot_order_dc_without_media_refuses_before_docker(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    monkeypatch.setattr(vm_lab, "resolve_qemu_image", lambda *_a, **_k: "img:1")
    arguments = start_arguments(tmp_path, "--boot-order", "dc") + ["--plain-host"]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "--boot-order dc requires --readonly-media, --iso, or --seed-iso" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--boot-order", "--cpu-shares"])
def test_malformed_launcher_options_are_usage_errors(tmp_path, capsys, option):
    arguments = start_arguments(tmp_path, option, "d" if option == "--boot-order" else "0")
    with pytest.raises(SystemExit) as exit_info:
        vm_lab.build_parser(GENERIC_PROFILE).parse_args(arguments)
    assert exit_info.value.code == 2
    assert option in capsys.readouterr().err


def test_a_config_disk_that_is_not_a_file_refuses_before_docker(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    monkeypatch.setattr(vm_lab, "resolve_qemu_image", lambda *_a, **_k: "img:1")
    directory = tmp_path / "not-a-file"
    directory.mkdir()
    arguments = start_arguments(tmp_path, "--config-disk", str(directory)) + ["--plain-host"]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "config disk is not a regular file" in capsys.readouterr().err


def test_plain_host_container_verification_reads_the_host_pid_cgroup(monkeypatch):
    profile = dataclasses.replace(GENERIC_PROFILE, guest_memory_mib=2048, cpus=2)
    item = {
        "Id": "a" * 64,
        "Config": {
            "Labels": {
                profile.vm_label: profile.label_value,
                profile.state_label: "state-hash",
            }
        },
        "HostConfig": {
            "CgroupParent": "dev-gates.slice",
            "NanoCpus": profile.nano_cpus,
            "Memory": profile.container_memory_bytes,
            "MemorySwap": profile.container_memory_bytes,
        },
        "NetworkSettings": {"Networks": {"p30-guest-net": {}}},
        "State": {"Running": True, "Pid": 4321},
    }
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: item)
    monkeypatch.setattr(
        vm_lab,
        "local_pid_cgroup",
        lambda _pid: "/dev.slice/dev-gates.slice/docker-container.scope",
    )
    monkeypatch.setattr(
        vm_lab,
        "local_cgroup_levels",
        lambda path: [{
            "path": path,
            "memory_max": profile.container_memory_bytes,
            "cpu_max": 2.0,
        }],
    )
    result = vm_lab.verify_running_container(
        profile,
        "p30-guest",
        "state-hash",
        "p30-guest-net",
        "dev-gates.slice",
        None,
        "/dev.slice/dev-gates.slice",
    )
    assert result["host_pid"] == 4321
    assert result["host_cgroup"] == "/dev.slice/dev-gates.slice/docker-container.scope"
    assert result["memory_max_bytes"] == profile.container_memory_bytes
