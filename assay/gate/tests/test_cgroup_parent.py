"""The gate verifies a loaded, resource-bounded host slice without host namespaces."""

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
PROBE_ID = "a" * 64


def run_probe(
    tmp_path: Path,
    output: str,
    *,
    slice_name: str | None,
    probe_parent: str | None = "dev-interactive.slice",
    cockpit_parent: str = "dev-interactive.slice",
    wait_status: str = "0",
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    calls_path = tmp_path / "docker-calls.jsonl"
    state_path = tmp_path / "docker-state.json"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["DOCKER_CALLS"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
if args[:2] == ["image", "inspect"]:
    raise SystemExit(0)
if args[0] == "inspect":
    fmt = args[args.index("--format") + 1]
    if "HostConfig.CgroupParent" in fmt and "Config.Labels" not in fmt:
        print(os.environ["COCKPIT_PARENT"])
    else:
        state = json.loads(pathlib.Path(os.environ["DOCKER_STATE"]).read_text())
        if "NanoCpus" in fmt:
            print("|".join((state["id"], "/" + state["name"], state["owner"], "3000000000", state["parent"])))
        else:
            print("|".join((state["id"], "/" + state["name"], state["owner"])))
    raise SystemExit(0)
if args[:2] == ["run", "-d"]:
    cidfile = args[args.index("--cidfile") + 1]
    name = args[args.index("--name") + 1]
    owner = args[args.index("--label") + 1].split("=", 1)[1]
    parent = args[args.index("--cgroup-parent") + 1].split("=", 1)[1]
    pathlib.Path(cidfile).write_text(os.environ["PROBE_ID"])
    pathlib.Path(os.environ["DOCKER_STATE"]).write_text(json.dumps({
        "id": os.environ["PROBE_ID"], "name": name, "owner": owner, "parent": parent
    }))
    print(os.environ["PROBE_ID"])
    raise SystemExit(0)
if args[:2] == ["update", "--cpus=3"]:
    raise SystemExit(0)
if args[0] == "wait":
    print(os.environ["WAIT_STATUS"])
    raise SystemExit(0)
if args[0] == "logs":
    print(os.environ["PROBE_OUTPUT"])
    raise SystemExit(0)
if args[0] == "rm":
    raise SystemExit(0)
raise SystemExit("unexpected docker argv: " + repr(args))
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls_path),
        "DOCKER_STATE": str(state_path),
        "PROBE_ID": PROBE_ID,
        "PROBE_OUTPUT": output,
        "WAIT_STATUS": wait_status,
        "COCKPIT_PARENT": cockpit_parent,
        "HOSTNAME": "devcontainer",
    }
    if slice_name is not None:
        env["CGROUP_PARENT_DEV_GATES"] = slice_name
    if probe_parent is not None:
        env["CGROUP_PARENT_DEV_INTERACTIVE"] = probe_parent
    proc = subprocess.run(
        [str(SCRIPT)], capture_output=True, text=True, env=env, check=False
    )
    calls = (
        [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
        if calls_path.exists()
        else []
    )
    return proc, calls


def test_unset_gates_tier_refuses_before_docker(tmp_path: Path):
    proc, calls = run_probe(tmp_path, "OK", slice_name=None)

    assert proc.returncode != 0
    assert "CGROUP_PARENT_DEV_GATES is unset" in proc.stderr
    assert calls == []


def test_unset_trusted_probe_parent_refuses_before_docker(tmp_path: Path):
    proc, calls = run_probe(
        tmp_path, "OK", slice_name="dev-gates.slice", probe_parent=None
    )

    assert proc.returncode != 0
    assert "CGROUP_PARENT_DEV_INTERACTIVE is unset" in proc.stderr
    assert calls == []


def test_mismatched_probe_parent_refuses_before_docker(tmp_path: Path):
    proc, calls = run_probe(
        tmp_path,
        "OK",
        slice_name="dev-gates.slice",
        cockpit_parent="dev-background.slice",
    )

    assert proc.returncode != 0
    assert "differs from the cockpit's actual Docker parent" in proc.stderr
    assert len(calls) == 1


def test_verified_configured_slice_uses_a_private_capped_probe(tmp_path: Path):
    proc, calls = run_probe(
        tmp_path,
        "OK unit=dev-gates.slice LoadState=loaded ControlGroup=/dev.slice/dev-gates.slice",
        slice_name="dev-gates.slice",
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "dev-gates.slice\n"
    assert "ASSAY_CGROUP_PARENT_PROBE_CONTAINER=" in proc.stderr
    run = next(argv for argv in calls if argv[:2] == ["run", "-d"])
    run_index = calls.index(run)
    name = run[run.index("--name") + 1]
    assert calls[run_index + 1] == ["update", "--cpus=3", name]
    assert "--cgroupns=host" not in run
    assert "--cgroup-parent=dev-interactive.slice" in run
    assert "--cpus=3" in run
    assert "--network=none" in run
    assert "--read-only" in run
    assert "--cap-drop=ALL" in run
    assert "--user=0:0" in run
    assert "type=bind,source=/sys/fs/cgroup,target=/hostcg,readonly" in run
    assert "type=bind,source=/run/systemd/system,target=/run/systemd/system,readonly" in run
    assert "type=bind,source=/run/dbus/system_bus_socket,target=/tmp/host-system-bus,readonly" in run
    command = run[run.index("bash") + 2]
    assert "/bin/systemctl show" in command
    assert "--property=LoadState" in command
    assert "--property=FragmentPath" in command
    assert "--property=ControlGroup" in command
    assert "/hostcg$control_group" in command
    assert "memory.max" in command and "cpu.max" in command
    assert not any("--cgroupns=host" in argv for argv in calls)
    assert calls[-1][0] == "rm"
    assert calls[-1][1] == name


@pytest.mark.parametrize(
    ("output", "wait_status", "expected"),
    [
        ("MISSING /hostcg/dev.slice/typo.slice", "1", "not a loaded, instantiated"),
        ("UNCONFIGURED /hostcg/dev.slice/dev-gates.slice", "1", "kernel-default"),
    ],
)
def test_missing_or_unconfigured_slice_is_refused_and_own_probe_removed(
    tmp_path: Path, output: str, wait_status: str, expected: str
):
    proc, calls = run_probe(
        tmp_path,
        output,
        slice_name="dev-gates.slice",
        wait_status=wait_status,
    )

    assert proc.returncode != 0
    assert expected in proc.stderr
    assert calls[-1][:2] == ["rm", "-f"]
    assert calls[-1][2].startswith("assay-cgroup-parent-")


def test_non_slice_name_is_refused_before_docker(tmp_path: Path):
    proc, calls = run_probe(tmp_path, "OK", slice_name="dev-background")

    assert proc.returncode != 0
    assert "does not name a systemd slice" in proc.stderr
    assert calls == []


def test_nyxloom_gate_uses_verified_value_without_a_literal_slice():
    """The trove gate is a thin pointer at the run-gate SSOT lane (D-110/D-111);
    the lane invokes the driver, and the driver still derives and verifies the
    cgroup tier itself — no literal slice anywhere in the chain.

    The pointer itself is parsed structurally (RG-2): exactly one cd into this
    project, an exact-token run-gate invocation carrying --worktree {worktree},
    naming a lane that exists in the SSOT — not substrings, which a broken
    pointer such as echo run-gate.py or tester-unified-typo would satisfy.
    run-gate validate-pointers enforces the same contract estate-wide."""
    document = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"]
    gate_cfg = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    lane = gate_cfg["lanes"]["tester-unified"]
    driver = GATE_DRIVER.read_text(encoding="utf-8")

    pointer = document["tester-unified"]["argv"][-1]
    assert re.findall(r"\bcd\s+(\S+)", pointer) == ["{worktree}/assay"]
    invocation = re.search(r"run-gate\.py\s+([^&;]*)$", pointer)
    assert invocation is not None
    assert invocation.group(1).split() == ["--worktree", "{worktree}", "tester-unified"]
    assert lane["environment"] == "bare-host"
    assert lane["argv"][0] == "bash"
    assert lane["argv"][1] == "{worktree}/assay/tools/tester-unified-gate.sh"
    assert '"$worktree/assay/tools/cgroup-parent.sh"' in driver
    assert '--cgroup-parent="$cgroup_parent"' in driver
    assert "--cgroupns=host" not in driver
    assert "source=/sys/fs/cgroup,target=/hostcg,readonly" in (PROJECT_ROOT / "tools/cgroup-parent.sh").read_text(encoding="utf-8")
    assert 'run_b145_bounded_wait_acceptance_probe "$host_repo_root" "$cgroup_parent"' in driver
    assert 'run_b145_low_pids_probe "$worktree" "$host_repo_root" "$cgroup_parent" "$observer_gid"' in driver
    assert "nyxloom-gates.slice" not in driver
    assert "dev-background.slice" not in driver
    assert "ASSAY_GATE_HOST_REPO_ROOT" in driver
    assert '--mount "type=bind,src=$host_repo_root,dst=/workspaces/vbpub"' in driver
