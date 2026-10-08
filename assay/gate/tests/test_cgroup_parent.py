"""The gate refuses an absent or unverified host cgroup tier."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

from gate.tests.support import PROJECT_ROOT

SCRIPT = PROJECT_ROOT / "tools" / "cgroup-parent.sh"
GATE_DRIVER = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"
NYXLOOM_TOML = PROJECT_ROOT / "nyxloom-trove" / "nyxloom.toml"


def run_probe(
    tmp_path: Path,
    verdict: str,
    *,
    slice_name: str | None,
    systemd_output: str | None = None,
    systemctl_exit: int = 0,
    cgroup_values: dict[str, str | None] | None = None,
    cgroup_transport_exit: int | None = None,
    memory_reserve_bytes: int | None = None,
    probe_parent: str | None = "dev-interactive.slice",
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
is_unit_probe = any("source=/run/dbus/system_bus_socket" in arg for arg in args)
with Path(os.environ["CALL_ORDER"]).open("a", encoding="utf-8") as stream:
    stream.write("unit\n" if is_unit_probe else "cgroup\n")
if is_unit_probe:
    print(os.environ["SYSTEMD_OUTPUT"])
    raise SystemExit(int(os.environ["SYSTEMCTL_EXIT"]))

script = sys.stdin.read().replace("/sys/fs/cgroup", os.environ["FAKE_CGROUP_ROOT"])
docker_env = os.environ.copy()
for index, arg in enumerate(args[:-1]):
    if arg == "-e" and args[index + 1].startswith("CG_REL="):
        docker_env["CG_REL"] = args[index + 1].split("=", 1)[1]
    if arg == "-e" and args[index + 1].startswith("CG_MEMORY_RESERVE_BYTES="):
        docker_env["CG_MEMORY_RESERVE_BYTES"] = args[index + 1].split("=", 1)[1]
proc = subprocess.run(["sh", "-s"], input=script, capture_output=True,
                      text=True, env=docker_env, check=False)
sys.stdout.write(proc.stdout)
sys.stderr.write(proc.stderr)
if os.environ.get("CGROUP_TRANSPORT_EXIT") is not None:
    raise SystemExit(int(os.environ["CGROUP_TRANSPORT_EXIT"]))
raise SystemExit(proc.returncode)
''',
        encoding="utf-8",
    )
    docker.chmod(0o755)
    cgroot = tmp_path / "cgroupfs"
    cgdir = cgroot / "dev.slice" / "dev-gates.slice"
    if not verdict.startswith("MISSING"):
        cgdir.mkdir(parents=True)
        defaults = {
            "memory.max": "8589934592\n",
            "memory.high": "max\n",
            "memory.swap.max": "8589934592\n",
            "memory.current": "1073741824\n",
            "cpu.weight": "100\n",
            "io.weight": "default 100\n",
        }
        if verdict.startswith("UNCONFIGURED"):
            defaults.update({
                "memory.max": "max\n",
                "memory.swap.max": "max\n",
            })
        defaults.update(cgroup_values or {})
        for filename, value in defaults.items():
            path = cgdir / filename
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(value, encoding="ascii")
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_ARGV_LOG": str(argv_log),
        "CALL_ORDER": str(call_order),
        "FAKE_CGROUP_ROOT": str(cgroot),
        "SYSTEMD_OUTPUT": (
            "LoadState=loaded\nFragmentPath=/etc/systemd/system/dev-gates.slice"
            if systemd_output is None else systemd_output
        ),
        "SYSTEMCTL_EXIT": str(systemctl_exit),
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
    assert len(calls) == 2
    unit_call, cgroup_call = calls
    assert "--cgroup-parent=dev-interactive.slice" in unit_call
    assert "source=/run/dbus/system_bus_socket" in " ".join(unit_call)
    assert "DBUS_SYSTEM_BUS_ADDRESS=unix:path=/tmp/host-system-bus" in " ".join(unit_call)
    assert "--cgroupns=host" in cgroup_call
    assert "--cgroup-parent=dev-gates.slice" in cgroup_call
    assert "--network=none" in cgroup_call
    assert "CG_REL=/dev.slice/dev-gates.slice" in " ".join(cgroup_call)


def test_installed_systemd_slice_is_checked_before_docker_probe(tmp_path: Path):
    proc, argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        systemd_output="LoadState=loaded\nFragmentPath=/etc/systemd/system/dev-gates.slice",
    )

    assert proc.returncode == 0, proc.stderr
    assert argv_log.exists()
    assert (tmp_path / "call-order").read_text(encoding="utf-8").splitlines() == [
        "unit",
        "cgroup",
    ]


def test_transient_or_missing_systemd_slice_is_refused_before_docker(tmp_path: Path):
    for unit_properties in (
        "LoadState=loaded\nFragmentPath=",
        "LoadState=not-found\nFragmentPath=",
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
        assert len(calls) == 1
        assert (case / "call-order").read_text(encoding="utf-8").splitlines() == [
            "unit"
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
    assert "could not query Docker-host systemd unit" in proc.stderr
    calls = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 1
    assert (tmp_path / "call-order").read_text(encoding="utf-8").splitlines() == ["unit"]


def test_missing_or_unconfigured_slice_is_refused(tmp_path: Path):
    for verdict in (
        "MISSING /sys/fs/cgroup/dev.slice/typo.slice",
        "UNCONFIGURED /sys/fs/cgroup/dev.slice/dev-gates.slice",
    ):
        proc, _ = run_probe(
            tmp_path, verdict, slice_name="dev-gates.slice"
        )
        assert proc.returncode != 0
        assert "could not verify host cgroup slice" in proc.stderr


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
            systemd_output=f"LoadState=loaded\nFragmentPath={fragment_path}",
        )

        assert proc.returncode != 0
        assert "is runtime-generated, not an installed slice" in proc.stderr
        assert (case / "call-order").read_text(encoding="utf-8").splitlines() == [
            "unit"
        ]
        assert len(argv_log.read_text(encoding="utf-8").splitlines()) == 1


def test_docker_probe_nonzero_is_not_certified_by_success_output(tmp_path: Path):
    proc, _argv_log = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cgroup_transport_exit=7,
    )

    assert proc.returncode != 0
    assert "probe exit 7" in proc.stderr
    assert "ASSAY_CGROUP_PARENT_PROBE=OK" in proc.stderr


def test_cgroup_control_read_must_succeed_and_be_nonempty(tmp_path: Path):
    for value, expected in ((None, "absent or unreadable"), ("", "is empty")):
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
    pointer (`echo run-gate.py`; `tester-unified-typo`) satisfies trivially.
    `./run-gate.py validate-pointers nyxloom-trove/nyxloom.toml` enforces the
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
    invocation = re.search(r"run-gate\.py\s+([^&;]*)$", pointer)
    assert invocation is not None, "no run-gate.py invocation in pointer"
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
    assert "--cgroupns=host" in driver
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
