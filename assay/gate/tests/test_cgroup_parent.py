"""The gate refuses an absent or unverified host cgroup tier."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "tools" / "cgroup-parent.sh"
GATE_DRIVER = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"
NYXLOOM_TOML = PROJECT_ROOT / "nyxloom-trove" / "nyxloom.toml"
KERNEL_HOSTNAME = Path("/proc/sys/kernel/hostname").read_text(encoding="utf-8").strip()


def run_probe(
    tmp_path: Path,
    verdict: str,
    *,
    slice_name: str | None,
    systemd_output: str | None = None,
    probe_systemd_output: str | None = None,
    systemctl_exit: int = 0,
    cgroup_values: dict[str, str | None] | None = None,
    cgroup_transport_exit: int | None = None,
    memory_reserve_bytes: int | None = None,
    probe_parent: str | None = "dev-interactive.slice",
    container_parent: str = "dev-interactive.slice",
    remote_mnt_namespace: str | None = None,
    remote_pid_namespace: str | None = None,
    unit_probe_run_mode: str = "owned",
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    argv_log = tmp_path / "docker.argv"
    call_order = tmp_path / "call-order"
    docker = fake_bin / "docker"
    docker.write_text(
        r'''#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["DOCKER_ARGV_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
is_unit_probe = bool(args and args[0] == "run" and any(
    "source=/run/dbus/system_bus_socket" in arg for arg in args
))
if args and args[0] == "inspect":
    target = args[1] if len(args) > 1 else ""
    if target == os.environ["HOST_CONFIGURED_HOSTNAME"]:
        with Path(os.environ["CALL_ORDER"]).open("a", encoding="utf-8") as stream:
            stream.write("parent\n")
        print("|".join((
            "b" * 64,
            os.environ["HOST_CONFIGURED_HOSTNAME"],
            os.environ["HOST_CGROUP_PARENT"],
            os.environ["HOST_CONTAINER_RUNNING"],
        )))
        raise SystemExit(int(os.environ["HOST_INSPECT_EXIT"]))
    with Path(os.environ["CALL_ORDER"]).open("a", encoding="utf-8") as stream:
        stream.write("unit-owner\n")
    try:
        probe = json.loads(Path(os.environ["UNIT_PROBE_STATE"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SystemExit(1)
    if target not in (probe["id"], probe["name"]):
        raise SystemExit(1)
    print("|".join((probe["id"], "/" + probe["name"], probe["owner"])))
    raise SystemExit(0)
if args and args[0] == "exec":
    with Path(os.environ["CALL_ORDER"]).open("a", encoding="utf-8") as stream:
        stream.write("identity\n")
    print("|".join((
        os.environ["HOST_REMOTE_MNT_NS"],
        os.environ["HOST_REMOTE_PID_NS"],
    )))
    raise SystemExit(int(os.environ["HOST_EXEC_EXIT"]))
if args and args[0] == "wait":
    # docker wait returns the container's own status on stdout, independently
    # of whether the Docker CLI transport itself succeeded.
    print(os.environ["SYSTEMCTL_EXIT"])
    raise SystemExit(int(os.environ.get("CGROUP_TRANSPORT_EXIT", "0")))
if args and args[0] == "logs":
    print(os.environ["SYSTEMD_OUTPUT"])
    raise SystemExit(0)
if args and args[0] == "rm":
    raise SystemExit(0)
if is_unit_probe:
    with Path(os.environ["CALL_ORDER"]).open("a", encoding="utf-8") as stream:
        stream.write("unit\n")
    name_arg = next(arg for arg in args if arg.startswith("--name="))
    label_arg = next(arg for arg in args if arg.startswith("--label=io.assay.cgroup-parent.owner="))
    name = name_arg.split("=", 1)[1]
    owner = label_arg.split("=", 2)[2]
    mode = os.environ["UNIT_PROBE_RUN_MODE"]
    probe = {"id": "f" * 64, "name": name, "owner": owner}
    if mode == "collision":
        probe.update({"id": "a" * 64, "owner": "another-container"})
    Path(os.environ["UNIT_PROBE_STATE"]).write_text(json.dumps(probe), encoding="utf-8")
    if mode in ("collision", "created-failure"):
        raise SystemExit(125)
    print(probe["id"])
    raise SystemExit(0)
raise SystemExit(2)
''',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    resource_values = {
        "ControlGroup": "/dev.slice/dev-gates.slice",
        "MemoryMax": "8589934592",
        "MemoryHigh": "18446744073709551615",
        "MemorySwapMax": "8589934592",
        "MemoryCurrent": "1073741824",
        "CPUQuotaPerSecUSec": "18446744073709551615",
        "CPUWeight": "100",
        "IOWeight": "100",
    }
    if verdict.startswith("UNCONFIGURED"):
        resource_values.update({
            "MemoryMax": "18446744073709551615",
            "MemorySwapMax": "18446744073709551615",
        })
    legacy_names = {
        "memory.max": "MemoryMax",
        "memory.high": "MemoryHigh",
        "memory.swap.max": "MemorySwapMax",
        "memory.current": "MemoryCurrent",
        "cpu.weight": "CPUWeight",
        "io.weight": "IOWeight",
    }
    for key, value in (cgroup_values or {}).items():
        name = legacy_names[key]
        if value is None:
            resource_values.pop(name, None)
            continue
        value = value.strip()
        if value == "max":
            value = "18446744073709551615"
        if key == "io.weight":
            value = value.split()[-1] if value else ""
        resource_values[name] = value

    if systemd_output is None:
        if verdict.startswith("MISSING"):
            systemd_output = (
                "Id=dev-gates.slice\nLoadState=not-found\nFragmentPath="
            )
        else:
            systemd_output = "\n".join(
                [
                    "Id=dev-gates.slice",
                    "LoadState=loaded",
                    "FragmentPath=/etc/systemd/system/dev-gates.slice",
                    *(f"{key}={value}" for key, value in resource_values.items()),
                ]
            )
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_ARGV_LOG": str(argv_log),
        "CALL_ORDER": str(call_order),
        "UNIT_PROBE_STATE": str(tmp_path / "unit-probe-state.json"),
        "UNIT_PROBE_RUN_MODE": unit_probe_run_mode,
        "HOSTNAME": "assay-probe-test",
        "HOST_CONFIGURED_HOSTNAME": KERNEL_HOSTNAME,
        "HOST_CGROUP_PARENT": container_parent,
        "HOST_CONTAINER_RUNNING": "true",
        "HOST_INSPECT_EXIT": "0",
        "HOST_REMOTE_MNT_NS": remote_mnt_namespace or os.readlink("/proc/self/ns/mnt"),
        "HOST_REMOTE_PID_NS": remote_pid_namespace or os.readlink("/proc/self/ns/pid"),
        "HOST_EXEC_EXIT": "0",
        "SYSTEMD_OUTPUT": "\n".join((
            _unit_output(
                "dev-gates.slice",
                "Id=dev-gates.slice\n"
                "LoadState=loaded\n"
                "FragmentPath=/etc/systemd/system/dev-gates.slice"
                if systemd_output is None else systemd_output,
            ),
            _unit_output(
                "dev-interactive.slice",
                "Id=dev-interactive.slice\n"
                "LoadState=loaded\n"
                "FragmentPath=/etc/systemd/system/dev-interactive.slice"
                if probe_systemd_output is None else probe_systemd_output,
            ),
        )),
        "SYSTEMCTL_EXIT": str(systemctl_exit),
        "CGROUP_TRANSPORT_EXIT": str(cgroup_transport_exit or 0),
    }
    if slice_name is not None:
        env["CGROUP_PARENT_DEV_GATES"] = slice_name
    if probe_parent is not None:
        env["CGROUP_PARENT_DEV_INTERACTIVE"] = probe_parent
    if cgroup_transport_exit is not None:
        env["CGROUP_TRANSPORT_EXIT"] = str(cgroup_transport_exit)
    argv = [str(SCRIPT)]
    if memory_reserve_bytes is not None:
        argv.extend(["--memory-reserve-bytes", str(memory_reserve_bytes)])
    proc = subprocess.run(
        argv, capture_output=True, text=True, env=env, check=False
    )
    return proc, argv_log


def _unit_output(unit: str, properties: str) -> str:
    return f"ASSAY_UNIT_BEGIN={unit}\n{properties}\nASSAY_UNIT_END={unit}"


def test_unset_gates_tier_refuses_before_docker(tmp_path: Path):
    proc, argv_log = run_probe(tmp_path, "OK", slice_name=None)

    assert proc.returncode != 0
    assert "CGROUP_PARENT_DEV_GATES is unset" in proc.stderr
    assert not argv_log.exists()


def test_verified_configured_slice_is_the_only_stdout_value(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path, "OK", slice_name="dev-gates.slice"
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "dev-gates.slice\n"
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 8
    parent_call, identity_call, unit_call, owner_check_call, wait_call, logs_call, remove_check_call, rm_call = calls
    assert parent_call[:2] == ["inspect", KERNEL_HOSTNAME]
    assert (
        "{{.Id}}|{{.Config.Hostname}}|{{.HostConfig.CgroupParent}}|{{.State.Running}}"
        in parent_call
    )
    assert identity_call[:3] == ["exec", KERNEL_HOSTNAME, "sh"]
    assert "readlink /proc/self/ns/mnt" in identity_call[-1]
    assert "readlink /proc/self/ns/pid" in identity_call[-1]
    assert unit_call[0] == "run" and "--detach" in unit_call
    assert any(arg.startswith("--label=io.assay.cgroup-parent.owner=") for arg in unit_call)
    assert "--cgroup-parent=dev-interactive.slice" in unit_call
    assert "dev-gates.slice" in unit_call
    assert "dev-interactive.slice" in unit_call
    assert "source=/run/dbus/system_bus_socket" in " ".join(unit_call)
    assert "--cgroupns=private" in unit_call
    assert "--cgroupns=host" not in unit_call
    assert "source=/sys/fs/cgroup" not in " ".join(unit_call)
    assert owner_check_call[0:2] == ["inspect", "f" * 64]
    assert remove_check_call[0:2] == ["inspect", "f" * 64]
    assert wait_call == ["wait", "f" * 64]
    assert logs_call == ["logs", "f" * 64]
    assert rm_call == ["rm", "f" * 64]
    assert all(call[0] != "run" for call in calls[3:])


def test_unit_probe_name_collision_never_removes_the_existing_container(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        unit_probe_run_mode="collision",
    )

    assert proc.returncode != 0
    assert "could not start Docker-host systemd query container" in proc.stderr
    assert "could not verify probe ownership" in proc.stderr
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert [call[0] for call in calls] == ["inspect", "exec", "run", "inspect"]
    assert calls[-1][0:2] == ["inspect", next(
        arg.split("=", 1)[1] for arg in calls[2] if arg.startswith("--name=")
    )]
    assert all(call[0] != "rm" for call in calls)


def test_probe_created_before_run_error_is_removed_by_verified_id(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        unit_probe_run_mode="created-failure",
    )

    assert proc.returncode != 0
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert [call[0] for call in calls] == ["inspect", "exec", "run", "inspect", "rm"]
    assert calls[-1] == ["rm", "--force", "f" * 64]


def test_installed_systemd_slice_is_checked_before_docker_probe(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
    )

    assert proc.returncode == 0, proc.stderr
    assert argv_log.exists()
    assert (tmp_path / "call-order").read_text(encoding="utf-8").splitlines() == [
        "parent",
        "identity",
        "unit",
        "unit-owner",
        "unit-owner",
    ]


def test_transient_or_missing_systemd_slice_is_refused_before_docker(tmp_path: Path):
    for unit_properties in (
        "Id=dev-gates.slice\nLoadState=loaded\nFragmentPath=",
        "Id=dev-gates.slice\nLoadState=not-found\nFragmentPath=",
    ):
        case = tmp_path / str(len(list(tmp_path.iterdir())))
        case.mkdir()
        proc, argv_log = run_probe(
            case,
            "OK",
            slice_name="dev-gates.slice",
            systemd_output=unit_properties,
        )
        assert proc.returncode != 0
        assert "not a loaded installed slice" in proc.stderr
        calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
        assert len(calls) == 8
        assert (case / "call-order").read_text(encoding="utf-8").splitlines() == [
            "parent", "identity", "unit", "unit-owner", "unit-owner"
        ]


def test_systemd_verification_failure_is_refused_before_docker(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        systemd_output="",
        systemctl_exit=1,
    )

    assert proc.returncode != 0
    assert "systemd query container exited with status 1" in proc.stderr
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 8
    assert (tmp_path / "call-order").read_text(encoding="utf-8").splitlines() == [
        "parent", "identity", "unit", "unit-owner", "unit-owner"
    ]


def test_truncated_systemd_output_with_zero_container_exit_is_refused(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        systemd_output=(
            "ASSAY_UNIT_BEGIN=dev-gates.slice\n"
            "Id=dev-gates.slice\n"
            "LoadState=loaded\n"
            "FragmentPath=/etc/systemd/system/dev-gates.slice"
        ),
        systemctl_exit=0,
    )

    assert proc.returncode != 0
    assert "returned incomplete or out-of-order frames" in proc.stderr
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert [call[0] for call in calls] == [
        "inspect", "exec", "run", "inspect", "wait", "logs", "inspect", "rm"
    ]
    assert calls[4] == ["wait", "f" * 64]


def test_missing_or_unconfigured_slice_is_refused(tmp_path: Path):
    cases = (
        (
            "MISSING",
            "not a loaded installed slice",
        ),
        (
            "UNCONFIGURED",
            "has only systemd-default resource controls",
        ),
    )
    for verdict, expected_message in cases:
        case = tmp_path / verdict.lower()
        case.mkdir()
        proc, _ = run_probe(case, verdict, slice_name="dev-gates.slice")
        assert proc.returncode != 0
        assert expected_message in proc.stderr


def test_non_slice_name_is_refused_before_docker(tmp_path: Path):
    proc, argv_log = run_probe(tmp_path, "OK", slice_name="dev-background")

    assert proc.returncode != 0
    assert "not a valid systemd slice unit name" in proc.stderr
    assert not argv_log.exists()


def test_host_unit_probe_requires_trusted_interactive_parent(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path, "OK", slice_name="dev-gates.slice", probe_parent=None
    )

    assert proc.returncode != 0
    assert "CGROUP_PARENT_DEV_INTERACTIVE is unset" in proc.stderr
    assert not argv_log.exists()


def test_runtime_generated_host_unit_is_refused_before_target_cgroup_probe(
    tmp_path: Path,
):
    for fragment_path in (
        "/run/systemd/transient/dev-gates.slice",
        "/run/systemd/generator/dev-gates.slice",
        "/run/systemd/system/dev-gates.slice",
    ):
        case = tmp_path / str(len(list(tmp_path.iterdir())))
        case.mkdir()
        proc, argv_log = run_probe(
            case,
            "OK",
            slice_name="dev-gates.slice",
            systemd_output=f"Id=dev-gates.slice\nLoadState=loaded\nFragmentPath={fragment_path}",
        )

        assert proc.returncode != 0
        assert "is runtime-generated, not an installed slice" in proc.stderr
        assert (case / "call-order").read_text(encoding="utf-8").splitlines() == [
            "parent", "identity", "unit", "unit-owner", "unit-owner"
        ]
        assert len(argv_log.read_text(encoding="utf-8").splitlines()) == 8


def test_uninstalled_interactive_probe_parent_refuses_before_target_cgroup_probe(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        probe_systemd_output=(
            "Id=dev-interactive.slice\nLoadState=loaded\n"
            "FragmentPath=/run/systemd/transient/dev-interactive.slice"
        ),
    )

    assert proc.returncode != 0
    assert (
        'Docker-host unit "dev-interactive.slice" is runtime-generated'
        in proc.stderr
    )
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 8
    assert (tmp_path / "call-order").read_text(encoding="utf-8").splitlines() == [
        "parent", "identity", "unit", "unit-owner", "unit-owner"
    ]


def test_systemd_unit_id_must_match_requested_slice(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        systemd_output=(
            "Id=other.slice\nLoadState=loaded\n"
            "FragmentPath=/etc/systemd/system/dev-gates.slice"
        ),
    )

    assert proc.returncode != 0
    assert 'returned a different unit ID (Id=other.slice)' in proc.stderr
    assert len(argv_log.read_text(encoding="utf-8").splitlines()) == 8


def test_hostname_match_with_a_different_container_namespace_is_refused(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        remote_mnt_namespace="mnt:[9999999999]",
    )
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert calls[0][:2] == ["inspect", KERNEL_HOSTNAME]
    assert calls[1][:2] == ["exec", KERNEL_HOSTNAME]

    assert proc.returncode != 0
    assert "resolved to a different container namespace" in proc.stderr


def test_inspected_cockpit_parent_must_match_configured_interactive_tier(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        container_parent="dev-other.slice",
    )

    assert proc.returncode != 0
    assert "does not match the running container's Docker CgroupParent" in proc.stderr
    assert (tmp_path / "call-order").read_text(encoding="utf-8").splitlines() == [
        "parent", "identity"
    ]
    assert len(argv_log.read_text(encoding="utf-8").splitlines()) == 2


def test_running_container_inspect_failure_refuses_before_unit_probe(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    docker.chmod(0o755)
    proc = subprocess.run(
        [str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
            "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
            "HOSTNAME": "assay-probe-test",
        },
    )

    assert proc.returncode != 0
    assert "could not inspect the running container" in proc.stderr


def test_docker_probe_nonzero_is_not_certified_by_success_output(tmp_path: Path):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_transport_exit=7,
    )

    assert proc.returncode != 0
    assert "docker wait exit 7" in proc.stderr


def test_systemd_resource_property_must_be_present_and_numeric(tmp_path: Path):
    for value, expected in (
        (None, "incomplete MemoryCurrent"),
        ("", "malformed current-usage or weight property"),
    ):
        case = tmp_path / str(len(list(tmp_path.iterdir())))
        case.mkdir()
        proc, _argv_log = run_probe(
            case,
            "OK",
            slice_name="dev-gates.slice",
            cgroup_values={"memory.current": value},
        )
        assert proc.returncode != 0
        assert expected in proc.stderr


@pytest.mark.parametrize("property_name", ["memory.max", "memory.current"])
def test_ram_admission_refuses_values_outside_safe_shell_arithmetic(
    tmp_path: Path, property_name: str
):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={property_name: "1000000000000000000"},
        memory_reserve_bytes=1,
    )

    assert proc.returncode != 0
    assert "exceeds the safe RAM-admission arithmetic range" in proc.stderr


def test_oversized_ram_reservation_is_rejected_before_docker(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        memory_reserve_bytes=10**18,
    )

    assert proc.returncode != 0
    assert "memory reservation exceeds the safe RAM-admission arithmetic range" in proc.stderr
    assert not argv_log.exists()


def test_b105_requires_finite_point_in_time_ram_headroom(tmp_path: Path):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        memory_reserve_bytes=2147483648,
    )

    assert proc.returncode == 0, proc.stderr
    assert "RAM admission OK" in proc.stderr
    assert proc.stdout == "dev-gates.slice\n"


def test_b105_refuses_insufficient_or_unknown_ram_headroom(tmp_path: Path):
    cases = [
        ({"memory.current": "7516192768\n"}, "has 1073741824 bytes"),
        (
            {"memory.max": "max\n", "cpu.weight": "500\n"},
            "cannot establish finite RAM headroom",
        ),
    ]
    for values, message in cases:
        case = tmp_path / str(len(list(tmp_path.iterdir())))
        case.mkdir()
        proc, _argv_log = run_probe(
            case,
            "OK",
            slice_name="dev-gates.slice",
            cgroup_values=values,
            memory_reserve_bytes=2147483648,
        )
        assert proc.returncode != 0
        assert message in proc.stderr


def test_nyxloom_gate_uses_verified_value_without_a_literal_slice():
    """The trove gate is a thin pointer at the run-gate SSOT lane (D-110/D-111);
    the lane invokes the driver, and the driver still derives and verifies the
    cgroup tier itself — no literal slice anywhere in the chain.

    The pointer itself is parsed STRUCTURALLY (RG-2): exactly one cd into this
    project, an exact-token run-gate invocation carrying --worktree {worktree},
    naming a lane that exists in the SSOT — not substrings, which a broken
    pointer (`echo run-gate`; `tester-unified-typo`) satisfies trivially.
    `run-gate validate-pointers nyxloom-trove/nyxloom.toml` enforces the
    same contract estate-wide; this pins THIS project's specific shape."""
    document = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))
    argv = document["gates"]["tester-unified"]["argv"]
    gate_cfg = tomllib.loads(
        (PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8")
    )
    lane = gate_cfg["lanes"]["tester-unified"]
    driver = GATE_DRIVER.read_text(encoding="utf-8")

    pointer = argv[-1]
    assert re.findall(r"\bcd\s+(\S+)", pointer) == ["{worktree}/assay"]
    invocation = re.search(r"\brun-gate\s+([^&;]*)$", pointer)
    assert invocation is not None, "no installed run-gate invocation in pointer"
    assert invocation.group(1).split() == [
        "--worktree", "{worktree}", "tester-unified"
    ]
    assert "tester-unified" in gate_cfg["lanes"], (
        "pointer names a lane the SSOT no longer declares")
    # `bare-host`, not `host`: run-gate rev 36 made plain `host` a CONTAINER
    # default with no docker-socket mount, and vbpub's RG-43 estate-wide
    # sweep (`f62642c6`) moved this driver -- which launches its own nested
    # build container and therefore needs real docker -- onto `bare-host`.
    # This assertion was not updated in that sweep and had been red on `main`
    # ever since; found and repaired by the B068 quick-wins wave.
    assert lane["environment"] == "bare-host"
    assert lane["argv"][0] == "bash"
    assert lane["argv"][1] == "{worktree}/assay/tools/tester-unified-gate.sh"
    assert '"$worktree/assay/tools/cgroup-parent.sh"' in driver
    assert '--cgroup-parent="$cgroup_parent"' in driver
    assert "--cgroupns=host" not in driver
    assert 'run_b145_bounded_wait_acceptance_probe "$host_repo_root" "$cgroup_parent"' in driver
    assert "run_b145_low_pids_probe \"$worktree\" \"$host_repo_root\" \"$cgroup_parent\"" in driver
    assert "nyxloom-gates.slice" not in driver
    assert "dev-background.slice" not in driver
    assert "ASSAY_GATE_HOST_REPO_ROOT" in driver
    assert '--mount "type=bind,src=$host_repo_root,dst=/workspaces/vbpub"' in driver


def test_outer_gate_canonicalizes_cmru_relative_worktree_before_validation():
    """CMRU runs the registered project step from ``assay/`` with ``..``."""
    driver = GATE_DRIVER.read_text(encoding="utf-8")
    canonicalize = 'worktree="$(cd -- "$1" && pwd -P)" || die "cannot resolve worktree $1"'
    outer = driver.split("[[ $# -eq 1 ]] || die 'outer mode requires exactly one worktree argument'", 1)[1]
    assert canonicalize in outer
    assert outer.index(canonicalize) < outer.index('validate_worktree "$worktree"')
