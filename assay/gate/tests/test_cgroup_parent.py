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
    memory_stat: str | None = None,
    memory_stat_read_error: bool = False,
    memory_stat_mount_failure: bool = False,
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

def load_probe_state():
    try:
        state = json.loads(Path(os.environ["UNIT_PROBE_STATE"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"containers": {}}
    if not isinstance(state, dict) or not isinstance(state.get("containers"), dict):
        return {"containers": {}}
    return state

def save_probe_state(state):
    Path(os.environ["UNIT_PROBE_STATE"]).write_text(json.dumps(state), encoding="utf-8")

def find_probe(state, target):
    for probe in state["containers"].values():
        if target in (probe["id"], probe["name"]):
            return probe
    return None

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
    state = load_probe_state()
    probe = find_probe(state, target)
    if probe is None:
        print(f"Error: No such object: {target}", file=sys.stderr)
        raise SystemExit(1)
    if probe.get("inspect_ambiguous_once"):
        probe["inspect_ambiguous_once"] = False
        save_probe_state(state)
        print("Error: Docker daemon unavailable", file=sys.stderr)
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
    output = os.environ["SYSTEMD_OUTPUT"]
    state = load_probe_state()
    probe = find_probe(state, args[1]) if len(args) > 1 else None
    probe = probe or {}
    if probe.get("memory_stat_mount"):
        memory_stat_output = "\nASSAY_MEMORY_STAT_BEGIN\n"
        if os.environ["MEMORY_STAT_READ_ERROR"] == "1":
            memory_stat_output += "ASSAY_MEMORY_STAT_READ_ERROR\n"
        else:
            memory_stat_output += os.environ["MEMORY_STAT_DATA"]
            if not memory_stat_output.endswith("\n"):
                memory_stat_output += "\n"
        memory_stat_output += "ASSAY_MEMORY_STAT_END\n"
        output = output.replace(
            "ASSAY_UNIT_END=dev-gates.slice\n",
            memory_stat_output + "ASSAY_UNIT_END=dev-gates.slice\n",
            1,
        )
    print(output)
    raise SystemExit(0)
if args and args[0] == "rm":
    state = load_probe_state()
    target = args[-1]
    for name, probe in list(state["containers"].items()):
        if target in (probe["id"], probe["name"]):
            del state["containers"][name]
            break
    save_probe_state(state)
    raise SystemExit(0)
if is_unit_probe:
    with Path(os.environ["CALL_ORDER"]).open("a", encoding="utf-8") as stream:
        stream.write("unit\n")
    name_arg = next(arg for arg in args if arg.startswith("--name="))
    label_arg = next(arg for arg in args if arg.startswith("--label=io.assay.cgroup-parent.owner="))
    name = name_arg.split("=", 1)[1]
    owner = label_arg.split("=", 2)[2]
    mode = os.environ["UNIT_PROBE_RUN_MODE"]
    memory_stat_mounts = [
        arg for arg in args
        if arg.startswith("--mount=type=bind,source=/sys/fs/cgroup")
    ]
    state = load_probe_state()
    container_id = chr(ord("f") - len(state["containers"])) * 64
    probe = {"id": container_id, "name": name, "owner": owner}
    if len(memory_stat_mounts) == 1:
        probe["memory_stat_mount"] = memory_stat_mounts[0]
    if mode == "inspect-ambiguous-once" and memory_stat_mounts:
        probe["inspect_ambiguous_once"] = True
    if mode == "owner-mismatch":
        probe["owner"] = "another-container"
    if mode == "collision":
        probe.update({"id": "a" * 64, "owner": "another-container"})
    absent_mount_failure = bool(memory_stat_mounts and mode == "mount-failure-absent")
    if not absent_mount_failure:
        state["containers"][name] = probe
        save_probe_state(state)
    if mode in ("collision", "created-failure") or (
        memory_stat_mounts and mode in (
            "mount-failure-created", "inspect-ambiguous-once", "owner-mismatch"
        )
    ) or (memory_stat_mounts and os.environ["MEMORY_STAT_MOUNT_FAILURE"] == "1"):
        raise SystemExit(125)
    if absent_mount_failure:
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
        "ControlGroup": "ControlGroup",
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
        "MEMORY_STAT_DATA": memory_stat or "",
        "MEMORY_STAT_READ_ERROR": "1" if memory_stat_read_error else "0",
        "MEMORY_STAT_MOUNT_FAILURE": "1" if memory_stat_mount_failure else "0",
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


def _unit_probe_script(argv_log: Path) -> str:
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    unit_run = next(call for call in calls if call[0] == "run")
    command_index = unit_run.index("-c")
    assert unit_run[command_index - 1] == "sh"
    return unit_run[command_index + 1]


def _execute_unit_probe_script(
    tmp_path: Path,
    script: str,
    memory_stat_path: Path,
    *,
    reader_mode: str = "normal",
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    busctl = fake_bin / "busctl"
    busctl.write_text(
        r'''#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
paths = {
    "dev-gates.slice": "/org/freedesktop/systemd1/unit/dev_gates_slice",
    "dev-interactive.slice": "/org/freedesktop/systemd1/unit/dev_interactive_slice",
}
if args[0] == "call":
    value = paths[args[-1]]
elif args[0] == "get-property":
    unit = next(unit for unit, path in paths.items() if path == args[-3])
    prop = args[-1]
    values = {
        "Id": unit,
        "LoadState": "loaded",
        "FragmentPath": f"/etc/systemd/system/{unit}",
        "ControlGroup": "/dev.slice/dev-gates.slice",
        "MemoryMax": 8589934592,
        "MemoryHigh": 18446744073709551615,
        "MemorySwapMax": 8589934592,
        "MemoryCurrent": 7516192768,
        "CPUQuotaPerSecUSec": 18446744073709551615,
        "CPUWeight": 100,
        "IOWeight": 100,
    }
    value = values[prop]
else:
    raise SystemExit(2)
print(json.dumps({"data": value}))
''',
        encoding="utf-8",
    )
    busctl.chmod(0o755)
    timeout = fake_bin / "timeout"
    timeout.write_text(
        r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["TIMEOUT_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
while args and args[0].startswith("--"):
    args.pop(0)
if args and args[0].endswith("s"):
    args.pop(0)
if not args:
    raise SystemExit(2)
if args[0] == "awk":
    if os.environ["READER_MODE"] == "timeout":
        raise SystemExit(124)
    if os.environ["READER_MODE"] == "fail":
        raise SystemExit(2)
os.execvpe(args[0], args, os.environ)
''',
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    executable_script = script.replace("/usr/bin/busctl", "busctl").replace(
        "/tmp/host-memory.stat", str(memory_stat_path)
    )
    timeout_log = tmp_path / "timeout.argv"
    proc = subprocess.run(
        [
            "sh",
            "-c",
            executable_script,
            "assay-cgroup-unit-probe",
            "dev-gates.slice",
            "dev-interactive.slice",
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TIMEOUT_LOG": str(timeout_log),
            "READER_MODE": reader_mode,
        },
    )
    return proc, timeout_log


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
    assert "host-memory.stat" not in " ".join(unit_call)
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


def test_b105_admits_when_reclaimable_cache_makes_reservation_fit(tmp_path: Path):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "7516192768"},
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert "raw_headroom=1073741824" in proc.stderr
    assert "adjusted_headroom=3221225472" in proc.stderr


def test_b105_reclaimable_mount_is_one_read_only_file(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "7516192768"},
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
    )

    assert proc.returncode == 0, proc.stderr
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    unit_run = next(call for call in calls if call[0] == "run")
    memory_mounts = [
        arg for arg in unit_run
        if arg.startswith("--mount=type=bind,source=/sys/fs/cgroup")
    ]
    assert memory_mounts == [
        "--mount=type=bind,source=/sys/fs/cgroup/dev.slice/dev-gates.slice/memory.stat,"
        "target=/tmp/host-memory.stat,readonly"
    ]
    assert "--cgroupns=private" in unit_run
    assert "--cgroupns=host" not in unit_run


@pytest.mark.parametrize(
    "memory_stat",
    [
        "inactive_file 1073741824\nslab_reclaimable 0\nfile_dirty 0\n",
        "inactive_file 1073741824\ninactive_file 1\nslab_reclaimable 0\nfile_dirty 0\nfile_writeback 0\n",
        "inactive_file 1073741824\nslab_reclaimable 0\nfile_dirty many\nfile_writeback 0\n",
        "inactive_file 1000000000000000000\nslab_reclaimable 0\nfile_dirty 0\nfile_writeback 0\n",
    ],
    ids=["missing-key", "duplicate-key", "non-numeric", "out-of-range"],
)
def test_invalid_memory_stat_uses_raw_headroom(
    tmp_path: Path, memory_stat: str
):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "5368709120"},
        memory_reserve_bytes=2147483648,
        memory_stat=memory_stat,
    )

    assert proc.returncode == 0, proc.stderr
    assert "raw_headroom=3221225472" in proc.stderr
    assert "adjusted_headroom=3221225472" in proc.stderr
    assert "basis=raw" in proc.stderr


def test_unreadable_memory_stat_uses_raw_headroom(tmp_path: Path):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "5368709120"},
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
        memory_stat_read_error=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "raw_headroom=3221225472" in proc.stderr
    assert "adjusted_headroom=3221225472" in proc.stderr
    assert "basis=raw" in proc.stderr


def test_memory_stat_mount_failure_retries_unmounted_and_cleans_owned_probe(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "5368709120"},
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
        memory_stat_mount_failure=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "retrying the unmounted systemd probe" in proc.stderr
    assert "raw_headroom=3221225472" in proc.stderr
    assert "adjusted_headroom=3221225472" in proc.stderr
    assert "basis=raw" in proc.stderr
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    run_indexes = [index for index, call in enumerate(calls) if call[0] == "run"]
    assert len(run_indexes) == 2
    first_run, fallback_run = (calls[index] for index in run_indexes)
    first_name = next(arg.split("=", 1)[1] for arg in first_run if arg.startswith("--name="))
    fallback_name = next(arg.split("=", 1)[1] for arg in fallback_run if arg.startswith("--name="))
    first_owner = next(
        arg.split("=", 1)[1]
        for arg in first_run
        if arg.startswith("--label=io.assay.cgroup-parent.owner=")
    )
    fallback_owner = next(
        arg.split("=", 1)[1]
        for arg in fallback_run
        if arg.startswith("--label=io.assay.cgroup-parent.owner=")
    )
    assert first_name != fallback_name
    assert first_owner != fallback_owner
    assert any(
        arg.startswith("--mount=type=bind,source=/sys/fs/cgroup")
        for arg in first_run
    )
    assert not any(
        arg.startswith("--mount=type=bind,source=/sys/fs/cgroup")
        for arg in fallback_run
    )
    assert calls[run_indexes[0] + 1][0:2] == ["inspect", first_name]
    assert calls[run_indexes[0] + 2] == ["rm", "--force", "f" * 64]
    assert any(
        "io.assay.cgroup-parent.owner" in arg
        for arg in calls[run_indexes[0] + 1]
    )
    state = json.loads((tmp_path / "unit-probe-state.json").read_text(encoding="utf-8"))
    assert state["containers"] == {}


def test_mount_failure_with_absent_container_retries_without_removal(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "5368709120"},
        memory_reserve_bytes=2147483648,
        unit_probe_run_mode="mount-failure-absent",
    )

    assert proc.returncode == 0, proc.stderr
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    run_indexes = [index for index, call in enumerate(calls) if call[0] == "run"]
    assert len(run_indexes) == 2
    first_run, fallback_run = (calls[index] for index in run_indexes)
    first_name = next(arg.split("=", 1)[1] for arg in first_run if arg.startswith("--name="))
    assert any(
        arg.startswith("--mount=type=bind,source=/sys/fs/cgroup")
        for arg in first_run
    )
    assert not any(
        arg.startswith("--mount=type=bind,source=/sys/fs/cgroup")
        for arg in fallback_run
    )
    assert calls[run_indexes[0] + 1][0:2] == ["inspect", first_name]
    assert not any(
        call[0] == "rm" for call in calls[run_indexes[0] + 1:run_indexes[1]]
    )
    state = json.loads((tmp_path / "unit-probe-state.json").read_text(encoding="utf-8"))
    assert state["containers"] == {}


def test_ambiguous_mount_failure_inspect_refuses_then_exit_trap_removes_probe(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "5368709120"},
        memory_reserve_bytes=2147483648,
        unit_probe_run_mode="inspect-ambiguous-once",
    )

    assert proc.returncode != 0
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    run_calls = [call for call in calls if call[0] == "run"]
    assert len(run_calls) == 1
    probe_name = next(arg.split("=", 1)[1] for arg in run_calls[0] if arg.startswith("--name="))
    assert probe_name in proc.stderr
    assert [call for call in calls if call[0] == "rm"] == [
        ["rm", "--force", "f" * 64]
    ]
    state = json.loads((tmp_path / "unit-probe-state.json").read_text(encoding="utf-8"))
    assert state["containers"] == {}


def test_owner_mismatch_after_mount_failure_refuses_without_removal(
    tmp_path: Path,
):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        memory_reserve_bytes=2147483648,
        unit_probe_run_mode="owner-mismatch",
    )

    assert proc.returncode != 0
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    run_calls = [call for call in calls if call[0] == "run"]
    assert len(run_calls) == 1
    probe_name = next(arg.split("=", 1)[1] for arg in run_calls[0] if arg.startswith("--name="))
    assert probe_name in proc.stderr
    assert not any(call[0] == "rm" for call in calls)
    state = json.loads((tmp_path / "unit-probe-state.json").read_text(encoding="utf-8"))
    assert list(state["containers"].values())[0]["owner"] == "another-container"


def test_generated_probe_script_reads_only_gates_memory_stat_with_a_bound(
    tmp_path: Path,
):
    reserved = tmp_path / "reserved"
    reserved.mkdir()
    proc, argv_log = run_probe(
        reserved,
        "OK",
        slice_name="dev-gates.slice",
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
    )
    assert proc.returncode == 0, proc.stderr
    script = _unit_probe_script(argv_log)
    stat_file = tmp_path / "memory.stat.fixture"
    stat_file.write_text(
        "anon 4096\n"
        "inactive_file 2147483648\n"
        "slab_reclaimable 0\n"
        "file_dirty 0\n"
        "file_writeback 0\n",
        encoding="utf-8",
    )

    valid_dir = tmp_path / "valid-execution"
    valid_dir.mkdir()
    valid, timeout_log = _execute_unit_probe_script(valid_dir, script, stat_file)
    assert valid.returncode == 0, valid.stderr
    lines = valid.stdout.splitlines()
    gates_start = lines.index("ASSAY_UNIT_BEGIN=dev-gates.slice")
    gates_end = lines.index("ASSAY_UNIT_END=dev-gates.slice")
    interactive_start = lines.index("ASSAY_UNIT_BEGIN=dev-interactive.slice")
    interactive_end = lines.index("ASSAY_UNIT_END=dev-interactive.slice")
    gates_frame = lines[gates_start:gates_end + 1]
    interactive_frame = lines[interactive_start:interactive_end + 1]
    assert "ASSAY_MEMORY_STAT_BEGIN" in gates_frame
    assert "ASSAY_MEMORY_STAT_END" in gates_frame
    assert "ASSAY_MEMORY_STAT_BEGIN" not in interactive_frame
    begin = gates_frame.index("ASSAY_MEMORY_STAT_BEGIN")
    end = gates_frame.index("ASSAY_MEMORY_STAT_END")
    assert gates_frame[begin + 1:end] == [
        "inactive_file 2147483648",
        "slab_reclaimable 0",
        "file_dirty 0",
        "file_writeback 0",
    ]
    reader_timeout_calls = [
        json.loads(line)
        for line in timeout_log.read_text(encoding="utf-8").splitlines()
        if '"awk"' in line
    ]
    assert len(reader_timeout_calls) == 1
    assert reader_timeout_calls[0][:2] == ["--kill-after=1s", "3s"]

    absent_dir = tmp_path / "absent-execution"
    absent_dir.mkdir()
    absent_path = tmp_path / "absent-memory.stat"
    absent, _ = _execute_unit_probe_script(absent_dir, script, absent_path)
    assert absent.returncode == 0, absent.stderr
    assert "ASSAY_MEMORY_STAT_BEGIN" not in absent.stdout

    unreadable_dir = tmp_path / "unreadable-execution"
    unreadable_dir.mkdir()
    unreadable_path = tmp_path / "unreadable-memory.stat"
    unreadable_path.mkdir()
    unreadable, _ = _execute_unit_probe_script(unreadable_dir, script, unreadable_path)
    assert unreadable.returncode == 0, unreadable.stderr
    assert "ASSAY_MEMORY_STAT_READ_ERROR" in unreadable.stdout

    failed_reader_dir = tmp_path / "failed-reader-execution"
    failed_reader_dir.mkdir()
    failed_reader, _ = _execute_unit_probe_script(
        failed_reader_dir, script, stat_file, reader_mode="timeout"
    )
    assert failed_reader.returncode == 0, failed_reader.stderr
    assert "ASSAY_MEMORY_STAT_READ_ERROR" in failed_reader.stdout

    unreserved_dir = tmp_path / "unreserved"
    unreserved_dir.mkdir()
    _, unreserved_argv = run_probe(
        unreserved_dir,
        "OK",
        slice_name="dev-gates.slice",
    )
    unreserved_script = _unit_probe_script(unreserved_argv)
    unreserved_exec = tmp_path / "unreserved-execution"
    unreserved_exec.mkdir()
    unreserved, _ = _execute_unit_probe_script(
        unreserved_exec, unreserved_script, stat_file
    )
    assert unreserved.returncode == 0, unreserved.stderr
    assert "ASSAY_MEMORY_STAT_BEGIN" not in unreserved.stdout


def test_dirty_and_writeback_subtract_reclaimable_memory_down_to_zero(
    tmp_path: Path,
):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "7516192768"},
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 1073741824\n"
            "slab_reclaimable 1073741824\n"
            "file_dirty 1073741824\n"
            "file_writeback 1073741824\n"
        ),
    )

    assert proc.returncode != 0
    assert "has 1073741824 bytes of RAM headroom" in proc.stderr
    assert "raw_headroom=1073741824" in proc.stderr
    assert "adjusted_headroom=1073741824" in proc.stderr
    assert "basis=memory.stat-adjusted" in proc.stderr


def test_adjusted_headroom_is_clamped_at_memory_max(tmp_path: Path):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "1073741824"},
        memory_reserve_bytes=8053063680,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 2147483648\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
    )

    assert proc.returncode == 0, proc.stderr
    assert "raw_headroom=7516192768" in proc.stderr
    assert "adjusted_headroom=8589934592" in proc.stderr
    assert "basis=memory.stat-adjusted" in proc.stderr


def test_reservation_refuses_when_adjusted_headroom_is_still_short(tmp_path: Path):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "7516192768"},
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 536870912\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
    )

    assert proc.returncode != 0
    assert "has 1610612736 bytes of RAM headroom" in proc.stderr
    assert "raw_headroom=1073741824" in proc.stderr
    assert "adjusted_headroom=1610612736" in proc.stderr
    assert "basis=memory.stat-adjusted" in proc.stderr


def test_memory_stat_is_not_used_when_control_group_does_not_match_rel(
    tmp_path: Path,
):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={
            "ControlGroup": "/dev.slice/dev-other.slice",
            "memory.current": "7516192768",
        },
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
    )

    assert proc.returncode != 0
    assert "unexpected ControlGroup" in proc.stderr
    assert "RAM admission OK" not in proc.stderr


def test_memory_current_above_max_still_refuses_with_reclaimable_cache(
    tmp_path: Path,
):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_values={"memory.current": "9663676416"},
        memory_reserve_bytes=2147483648,
        memory_stat=(
            "inactive_file 2147483648\n"
            "slab_reclaimable 0\n"
            "file_dirty 0\n"
            "file_writeback 0\n"
        ),
    )

    assert proc.returncode != 0
    assert "reports memory.current above memory.max" in proc.stderr


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
