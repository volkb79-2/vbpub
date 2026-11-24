"""Plain Docker-host start mode and rollback-launcher parity, at a fake Docker boundary."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from vm_lab import GENERIC_PROFILE, RunnerError
from vm_lab import core as vm_lab


QCOW2_BYTES = b"QFI\xfb" + b"\x00" * 60
IMAGE = "img:1"
LEVELS = [{
    "path": "/dev.slice/dev-gates.slice",
    "cpu_max": 4.0,
    "memory_high": 6 * 1024**3,
    "memory_max": 8 * 1024**3,
    "memory_current": 1024**3,
    "memory_psi_some_avg10": 0.0,
    "memory_psi_full_avg10": 0.0,
}]

DOCKER_OPTION_ARITY = {
    "--detach": False,
    "--init": False,
    "--read-only": False,
    "--privileged": False,
    "--name": True,
    "--cgroup-parent": True,
    "--cpus": True,
    "--cpu-shares": True,
    "--memory": True,
    "--memory-swap": True,
    "--pids-limit": True,
    "--cap-drop": True,
    "--cap-add": True,
    "--security-opt": True,
    "--tmpfs": True,
    "--network": True,
    "--user": True,
    "--label": True,
    "--mount": True,
    "--env": True,
    "--entrypoint": True,
    "--pid": True,
    "--ipc": True,
    "--uts": True,
    "--userns": True,
    "--cgroupns": True,
    "-v": True,
    "--volume": True,
    "--device": True,
    "--device-cgroup-rule": True,
    "--publish": True,
    "-p": True,
}

ALLOWED_DOCKER_OPTIONS = {
    "--detach",
    "--init",
    "--name",
    "--cgroup-parent",
    "--cpus",
    "--cpu-shares",
    "--memory",
    "--memory-swap",
    "--pids-limit",
    "--cap-drop",
    "--security-opt",
    "--read-only",
    "--tmpfs",
    "--network",
    "--user",
    "--label",
    "--mount",
    "--env",
    "--entrypoint",
}

FORBIDDEN_DOCKER_OPTIONS = {
    "--pid",
    "--ipc",
    "--uts",
    "--userns",
    "--cgroupns",
    "--volume",
    "-v",
    "--device",
    "--device-cgroup-rule",
    "--privileged",
    "--cap-add",
    "--publish",
    "-p",
}


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


class RunningDockerBoundary:
    """A stateful fake Docker/systemd boundary for a complete plain-host start."""

    def __init__(self, *, state_dir: Path, profile, state_hash: str, identity: dict[str, object]) -> None:
        self.state_dir = state_dir
        self.profile = profile
        self.state_hash = state_hash
        self.identity = identity
        self.created = False
        self.create_argv: list[str] | None = None
        self.systemctl_units: list[str] = []
        self.systemctl_properties: list[tuple[str, str]] = []

    def item(self) -> dict[str, object]:
        return {
            "Id": "b" * 64,
            "Config": {
                "Labels": {
                    self.profile.vm_label: self.profile.label_value,
                    self.profile.state_label: self.state_hash,
                }
            },
            "HostConfig": {
                "CgroupParent": self.identity["slice_name"],
                "NetworkMode": self.identity["network"],
                "NanoCpus": self.profile.nano_cpus,
                "Memory": self.profile.container_memory_bytes,
                "MemorySwap": self.profile.container_memory_bytes,
            },
            "NetworkSettings": {"Networks": {self.identity["network"]: {}}},
            "State": {"Running": True, "Pid": self.identity["pid"]},
        }

    def __call__(self, argv, **_kwargs):
        if argv[0] == "systemctl":
            unit = argv[-1]
            prop = argv[argv.index("--property") + 1]
            self.systemctl_units.append(unit)
            self.systemctl_properties.append((unit, prop))
            values = {
                ("dev-gates.slice", "LoadState"): "loaded",
                ("dev-gates.slice", "Transient"): "no",
                ("dev-gates.slice", "ControlGroup"): "/dev.slice/dev-gates.slice",
            }
            assert (unit, prop) in values, f"unexpected systemctl query: {argv!r}"
            return completed(argv, stdout=values[(unit, prop)] + "\n")
        if argv[:2] == ["docker", "network"]:
            return completed(argv, stdout=f"{self.identity['network']} bridge\n")
        if argv[:2] == ["docker", "run"]:
            self.created = True
            self.create_argv = list(argv)
            (self.state_dir / "qmp.sock").write_text("")
            return completed(argv)
        if argv[:2] == ["docker", "inspect"]:
            if not self.created:
                return completed(argv, 1, "", "absent")
            return completed(argv, stdout=json.dumps(self.item()))
        if argv[:3] == ["docker", "ps", "-q"]:
            return completed(argv)
        raise AssertionError(f"unexpected Docker boundary call: {argv!r}")


def write_local_cgroup(root: Path, control_group: str, *, cpus: float, memory_max: int) -> None:
    directory = root / control_group.lstrip("/")
    directory.mkdir(parents=True)
    (directory / "cpu.max").write_text(f"{int(cpus * 100000)} 100000\n")
    (directory / "memory.high").write_text(f"{memory_max}\n")
    (directory / "memory.max").write_text(f"{memory_max}\n")
    (directory / "memory.current").write_text("0\n")
    (directory / "memory.pressure").write_text(
        "some avg10=0.00 total=0\nfull avg10=0.00 total=0\n"
    )


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
    monkeypatch.setattr(vm_lab, "inspect_network", lambda name: (name, "bridge"))
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
    values: list[str] = []
    for index, item in enumerate(argv):
        if item == option and index + 1 < len(argv):
            values.append(argv[index + 1])
        elif item.startswith(option + "="):
            values.append(item.split("=", 1)[1])
    return values


def one_value(argv: list[str], option: str) -> str:
    values = option_values(argv, option)
    assert len(values) == 1
    return values[0]


def docker_options(argv: list[str]) -> dict[str, list[str]]:
    """Parse Docker options before the image, accepting `--opt=value` and `--opt value`."""
    assert argv[:2] == ["docker", "run"], argv
    image_index = argv.index(IMAGE, 2)
    options: dict[str, list[str]] = {}
    index = 2
    while index < image_index:
        token = argv[index]
        assert token.startswith("-"), f"unexpected positional before image: {token!r}"
        option, separator, attached = token.partition("=")
        assert option in DOCKER_OPTION_ARITY, f"unknown Docker option: {option!r}"
        options.setdefault(option, [])
        if separator:
            options[option].append(attached)
            index += 1
            continue
        if DOCKER_OPTION_ARITY[option]:
            assert index + 1 < image_index, f"{option} has no value"
            options[option].append(argv[index + 1])
            index += 2
        else:
            index += 1
    assert index == image_index
    return options


def docker_configuration(argv: list[str]) -> dict[str, object]:
    options = docker_options(argv)
    environment = dict(value.split("=", 1) for value in options.get("--env", []))
    return {
        "name": options["--name"][0],
        "cgroup_parent": options["--cgroup-parent"][0],
        "cpus": options["--cpus"][0],
        "cpu_shares": options.get("--cpu-shares", []),
        "memory": options["--memory"][0],
        "memory_swap": options["--memory-swap"][0],
        "pids_limit": options["--pids-limit"][0],
        "cap_drop": options["--cap-drop"],
        "cap_add": options.get("--cap-add", []),
        "security_opts": options["--security-opt"],
        "read_only": "--read-only" in options,
        "tmpfs": options["--tmpfs"],
        "network": options["--network"][0],
        "user": options["--user"][0],
        "mounts": options["--mount"],
        "environment": environment,
        "pid_namespace": options.get("--pid", []),
        "privileged": "--privileged" in options,
        "devices": options.get("--device", []),
        "publish": options.get("--publish", []) + options.get("-p", []),
        "entrypoint": options["--entrypoint"][0],
        "guest_script": one_value(argv, "-c"),
        "options": options,
    }


def assert_common_hardening(configuration: dict[str, object]) -> None:
    options = configuration["options"]
    assert set(options) == ALLOWED_DOCKER_OPTIONS
    assert not (set(options) & FORBIDDEN_DOCKER_OPTIONS)
    for option in FORBIDDEN_DOCKER_OPTIONS - {"--privileged"}:
        assert options.get(option, []) == []
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


def test_plain_host_start_uses_real_preflight_and_post_start_verification(
    monkeypatch, tmp_path, capsys
):
    profile = dataclasses.replace(GENERIC_PROFILE, guest_memory_mib=2048, cpus=2)
    arguments = start_arguments(tmp_path, "--cpu-shares", "64") + ["--plain-host"]
    state_dir = tmp_path / "state"
    state_hash = vm_lab.state_identity(state_dir)
    cgroup_root = tmp_path / "cgroup"
    proc_root = tmp_path / "proc"
    actual_cgroup = "/dev.slice/dev-gates.slice/docker-b.scope"
    write_local_cgroup(
        cgroup_root,
        "/dev.slice/dev-gates.slice",
        cpus=4.0,
        memory_max=8 * 1024**3,
    )
    write_local_cgroup(
        cgroup_root,
        actual_cgroup,
        cpus=float(profile.cpus),
        memory_max=profile.container_memory_bytes,
    )
    proc_cgroup = proc_root / "4321" / "cgroup"
    proc_cgroup.parent.mkdir(parents=True)
    proc_cgroup.write_text(f"0::{actual_cgroup}\n")

    boundary = RunningDockerBoundary(
        state_dir=state_dir,
        profile=profile,
        state_hash=state_hash,
        identity={
            "slice_name": "dev-gates.slice",
            "network": "p30-guest-net",
            "pid": 4321,
        },
    )
    monkeypatch.setattr(vm_lab, "docker_run", boundary)
    monkeypatch.setattr(vm_lab, "image_id", lambda _name: "sha256:image")
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
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
    monkeypatch.setattr(vm_lab, "HOST_CGROUP_ROOT", cgroup_root)
    monkeypatch.setattr(vm_lab, "HOST_PROC_ROOT", proc_root)
    monkeypatch.setattr(vm_lab, "qmp_command", lambda *_args, **_kwargs: "running")
    monkeypatch.setattr(
        vm_lab,
        "cleanup_failed_start",
        lambda *_args, **_kwargs: pytest.fail("a successful start invoked startup cleanup"),
    )
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")

    assert vm_lab.main(profile, arguments) == 0
    assert boundary.create_argv is not None
    configuration = docker_configuration(boundary.create_argv)
    assert configuration["cgroup_parent"] == "dev-gates.slice"
    assert_common_hardening(configuration)
    assert boundary.systemctl_units
    assert set(boundary.systemctl_units) == {"dev-gates.slice"}
    started_line = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("[started] ")
    ]
    assert len(started_line) == 1
    started = json.loads(started_line[0].removeprefix("[started] "))
    assert started["mode"] == "plain-host"
    assert started["host_cgroup"] == actual_cgroup
    assert started["cgroup_parent"] == "dev-gates.slice"


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
        "-.slice",
        "--x.slice",
        "a:b.slice",
        "/dev.slice/dev-gates.slice",
        "dev-gates.slice/child",
        "../dev-gates.slice",
        "dev-gates.slice:escaped",
    ],
)
def test_plain_host_start_refuses_a_non_unit_slice_name(monkeypatch, tmp_path, capsys, slice_name):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    arguments = start_arguments(tmp_path) + ["--plain-host", f"--cgroup-parent={slice_name}"]
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


def test_plain_host_start_refuses_a_network_id_that_resolves_to_host(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    monkeypatch.setattr(vm_lab, "inspect_network", lambda _name: ("host", "host"))
    arguments = start_arguments(tmp_path, "--network", "0123456789ab") + ["--plain-host"]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "resolved to Docker network 'host'" in capsys.readouterr().err


def test_plain_host_start_refuses_a_host_driver_even_with_an_exact_name(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    monkeypatch.setattr(vm_lab, "inspect_network", lambda name: (name, "host"))
    arguments = start_arguments(tmp_path) + ["--plain-host"]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "forbidden Docker network driver 'host'" in capsys.readouterr().err


def test_plain_host_start_requires_the_network_argument_to_be_the_exact_name(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(vm_lab, "docker_run", ForbiddenDocker())
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path.resolve() / "admission.lock")
    monkeypatch.setattr(vm_lab, "inspect_network", lambda _name: ("guest-network", "bridge"))
    arguments = start_arguments(tmp_path, "--network", "guest-net") + ["--plain-host"]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert "pass the exact network name, not an ID or prefix" in capsys.readouterr().err


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
            "Transient": "no",
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


@pytest.mark.parametrize("control_group", ["dev.slice/dev-gates.slice", "/dev.slice/../root.slice"])
def test_plain_host_preflight_refuses_an_unsafe_control_group(monkeypatch, control_group):
    monkeypatch.setattr(
        vm_lab,
        "plain_host_systemd_property",
        lambda _unit, prop: {
            "LoadState": "loaded",
            "Transient": "no",
            "ControlGroup": control_group,
        }[prop],
    )
    with pytest.raises(RunnerError, match="unsafe cgroup path"):
        vm_lab.plain_host_systemd_slice_path("dev-gates.slice")


def test_plain_host_preflight_refuses_a_transient_slice_without_a_fragment(monkeypatch):
    monkeypatch.setattr(
        vm_lab,
        "plain_host_systemd_property",
        lambda _unit, prop: {
            "LoadState": "loaded",
            "Transient": "yes",
            "FragmentPath": "",
            "ControlGroup": "/dev.slice/dev-gates.slice",
        }[prop],
    )
    with pytest.raises(RunnerError, match="no FragmentPath"):
        vm_lab.plain_host_systemd_slice_path("dev-gates.slice")


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


def test_local_cgroup_file_treats_an_absolute_control_group_as_relative_to_the_host_root(
    monkeypatch, tmp_path
):
    root = tmp_path / "cgroup"
    path = root / "dev.slice" / "dev-gates.slice" / "memory.max"
    path.parent.mkdir(parents=True)
    path.write_text("2684354560\n")
    monkeypatch.setattr(vm_lab, "HOST_CGROUP_ROOT", root)
    assert vm_lab.local_cgroup_file("/dev.slice/dev-gates.slice", "memory.max") == "2684354560"


def test_local_cgroup_file_refuses_an_oserror(monkeypatch, tmp_path):
    root = tmp_path / "cgroup"
    path = root / "dev.slice" / "dev-gates.slice" / "memory.max"
    path.mkdir(parents=True)
    monkeypatch.setattr(vm_lab, "HOST_CGROUP_ROOT", root)
    with pytest.raises(RunnerError, match="cannot read host cgroup file"):
        vm_lab.local_cgroup_file("/dev.slice/dev-gates.slice", "memory.max")


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


@pytest.mark.parametrize(
    "injection",
    [
        ["--pid=host"],
        ["--pid", "host"],
        ["--ipc=host"],
        ["--ipc", "host"],
        ["--uts=host"],
        ["--uts", "host"],
        ["--userns=host"],
        ["--userns", "host"],
        ["--cgroupns=host"],
        ["--cgroupns", "host"],
        ["--volume", "/:/host"],
        ["--volume=/:/host"],
        ["-v", "/:/host"],
        ["-v=/:/host"],
        ["--device-cgroup-rule", "c 1:3 rmw"],
        ["--device-cgroup-rule=c 1:3 rmw"],
        ["--privileged"],
        ["--cap-add=ALL"],
        ["--cap-add", "ALL"],
    ],
)
def test_docker_hardening_allowlist_rejects_an_injected_option(injection):
    argv = ["docker", "run", "--detach", "--read-only", *injection, IMAGE]
    options = docker_options(argv)
    assert set(options) != ALLOWED_DOCKER_OPTIONS
    assert set(options) & FORBIDDEN_DOCKER_OPTIONS


def test_runner_manifest_records_launcher_identity_fields(tmp_path):
    base = tmp_path / "base.qcow2"
    media = tmp_path / "seed.iso"
    config = tmp_path / "config.img"
    firmware_code = tmp_path / "code.fd"
    firmware_vars = tmp_path / "vars.fd"
    base.write_bytes(QCOW2_BYTES)
    media.write_bytes(b"seed")
    config.write_bytes(b"config")
    firmware_code.write_bytes(b"code")
    firmware_vars.write_bytes(b"vars")
    profile = dataclasses.replace(GENERIC_PROFILE, guest_memory_mib=2048, cpus=2)
    manifest = vm_lab.runner_manifest(
        profile,
        name="p30-guest",
        base_disk=base,
        firmware_code=firmware_code,
        firmware_vars=firmware_vars,
        image_id="sha256:image",
        network="p30-guest-net",
        cgroup_parent="dev-gates.slice",
        host_ports={forward.name: 2222 for forward in profile.forwards},
        readonly_media=media,
        config_disk=config,
        cpu_shares=64,
        boot_order="dc",
    )
    assert manifest["cpu_shares"] == 64
    assert manifest["boot_order"] == "dc"
    assert manifest["config_disk"]["sha256"] == vm_lab.file_identity(config)["sha256"]


def assert_resume_refused_before_docker(boundary, args, capsys, reason: str) -> None:
    arguments = list(args) + ["--plain-host", "--resume"]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    assert boundary.create_argv is None
    assert reason in capsys.readouterr().err


def test_resume_refuses_a_different_cpu_shares(monkeypatch, tmp_path, capsys):
    run_to_docker(tmp_path, monkeypatch, "--cpu-shares", "64")
    capsys.readouterr()
    boundary = DockerBoundary()
    install_start_fakes(monkeypatch, tmp_path, boundary, plain_host=True)
    assert_resume_refused_before_docker(
        boundary,
        start_arguments(tmp_path, "--cpu-shares", "32"),
        capsys,
        "resume identity differs",
    )


def test_resume_refuses_a_different_boot_order(monkeypatch, tmp_path, capsys):
    media = tmp_path / "seed.iso"
    media.write_bytes(b"seed")
    run_to_docker(tmp_path, monkeypatch, "--seed-iso", str(media), "--boot-order", "dc")
    capsys.readouterr()
    boundary = DockerBoundary()
    install_start_fakes(monkeypatch, tmp_path, boundary, plain_host=True)
    assert_resume_refused_before_docker(
        boundary,
        start_arguments(tmp_path, "--seed-iso", str(media), "--boot-order", "c"),
        capsys,
        "resume identity differs",
    )


def test_resume_refuses_a_changed_config_disk(monkeypatch, tmp_path, capsys):
    config = tmp_path / "config.img"
    config.write_bytes(b"first config")
    run_to_docker(tmp_path, monkeypatch, "--config-disk", str(config))
    capsys.readouterr()
    config.write_bytes(b"changed config")
    boundary = DockerBoundary()
    install_start_fakes(monkeypatch, tmp_path, boundary, plain_host=True)
    assert_resume_refused_before_docker(
        boundary,
        start_arguments(tmp_path, "--config-disk", str(config)),
        capsys,
        "resume identity differs",
    )


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
            "NetworkMode": "p30-guest-net",
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


def test_plain_host_container_verification_refuses_a_different_network_mode(monkeypatch):
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
            "NetworkMode": "host",
            "NanoCpus": profile.nano_cpus,
            "Memory": profile.container_memory_bytes,
            "MemorySwap": profile.container_memory_bytes,
        },
        "NetworkSettings": {"Networks": {"p30-guest-net": {}}},
        "State": {"Running": True, "Pid": 4321},
    }
    monkeypatch.setattr(vm_lab, "docker_inspect", lambda _name: item)
    with pytest.raises(RunnerError, match="unexpected Docker network mode 'host'"):
        vm_lab.verify_running_container(
            profile,
            "p30-guest",
            "state-hash",
            "p30-guest-net",
            "dev-gates.slice",
            None,
            "/dev.slice/dev-gates.slice",
        )
