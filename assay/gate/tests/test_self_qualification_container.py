"""The B105 outer runner proves its Docker argv and ownership lifecycle."""

from __future__ import annotations

import json
import fcntl
import os
import signal
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT

REPO_ROOT = PROJECT_ROOT.parent
SCRIPT = PROJECT_ROOT / "tools" / "self-qualification-container.sh"
CGROUP_HELPER = PROJECT_ROOT / "tools" / "cgroup-parent.sh"
CONTAINER_ID = "a" * 64


FAKE_GIT = r'''#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if "status" in args and "--porcelain" in args:
    counter = Path(os.environ["GIT_STATUS_COUNTER"])
    number = int(counter.read_text(encoding="ascii")) + 1 if counter.exists() else 1
    counter.write_text(str(number), encoding="ascii")
    if number == int(os.environ["GIT_STATUS_FAIL_AT"]):
        raise SystemExit(19)
os.execv(os.environ["REAL_GIT"], [os.environ["REAL_GIT"], *args])
'''


FAKE_TIMEOUT = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["TIMEOUT_TRACE"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\n")
if os.environ.get("FAST_DOCKER_TIMEOUT") == "1" and "docker" in args:
    index = args.index("docker")
    args = ["--signal=TERM", "--kill-after=1s", "0.25s", *args[index:]]
os.execv(os.environ["REAL_TIMEOUT"], [os.environ["REAL_TIMEOUT"], *args])
'''


FAKE_DOCKER = r'''#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
trace = Path(os.environ["DOCKER_TRACE"])
state_path = Path(os.environ["DOCKER_STATE"])
mode = os.environ.get("DOCKER_MODE", "success")

def record():
    with trace.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(args) + "\n")

def read_state():
    return json.loads(state_path.read_text(encoding="utf-8"))

def read_unit_probe_state():
    return json.loads(Path(os.environ["DOCKER_UNIT_PROBE_STATE"]).read_text(encoding="utf-8"))

def is_unit_probe_target(target):
    try:
        probe = read_unit_probe_state()
    except (OSError, json.JSONDecodeError):
        return False
    return target in (probe["id"], probe["name"])

def one_after(flag):
    return args[args.index(flag) + 1]

def value_with_prefix(prefix):
    return next(item[len(prefix):] for item in args if item.startswith(prefix))

record()
if args[:2] == ["image", "inspect"]:
    raise SystemExit(0)
if args and args[0] == "exec":
    print("|".join((os.readlink("/proc/self/ns/mnt"), os.readlink("/proc/self/ns/pid"))))
    raise SystemExit(0)
if args[:2] == ["ps", "--no-trunc"]:
    if mode == "busy":
        print("run-gate-other-session")
    raise SystemExit(0)
if args and args[0] == "run" and "--detach" in args and any(
    "source=/run/dbus/system_bus_socket" in item for item in args
):
    name = value_with_prefix("--name=")
    owner = value_with_prefix("--label=io.assay.cgroup-parent.owner=")
    probe = {"id": "c" * 64, "name": name, "owner": owner, "units": args[-2:]}
    Path(os.environ["DOCKER_UNIT_PROBE_STATE"]).write_text(json.dumps(probe), encoding="utf-8")
    print(probe["id"])
    raise SystemExit(0)
if args[:2] == ["run", "--rm"] and any("source=/run/dbus/system_bus_socket" in item for item in args):
    for unit in args[-2:]:
        print(f"ASSAY_UNIT_BEGIN={unit}")
        print(f"Id={unit}")
        print("LoadState=loaded")
        print(f"FragmentPath=/etc/systemd/system/{unit}")
        print(f"ASSAY_UNIT_END={unit}")
    raise SystemExit(0)
if args[:2] == ["run", "--rm"]:
    print("ASSAY_CGROUP_MEMORY_MAX=8589934592")
    print("ASSAY_CGROUP_MEMORY_CURRENT=1073741824")
    print("ASSAY_CGROUP_PARENT_PROBE=OK")
    raise SystemExit(0)
if args and args[0] == "run" and "-d" in args:
    mounts = []
    env = []
    for index, item in enumerate(args[:-1]):
        if item == "--mount":
            mounts.append(args[index + 1])
        if item == "-e":
            env.append(args[index + 1])
    image_index = args.index("tester-unified:local")
    state = {
        "id": "''' + CONTAINER_ID + r'''",
        "name": one_after("--name"),
        "token": one_after("--label").split("=", 1)[1],
        "cgroup_parent": next(item.split("=", 1)[1] for item in args if item.startswith("--cgroup-parent=")),
        "cgroupns": next(item.split("=", 1)[1] for item in args if item.startswith("--cgroupns=")),
        "cpus": next(item.split("=", 1)[1] for item in args if item.startswith("--cpus=")),
        "memory": next(item.split("=", 1)[1] for item in args if item.startswith("--memory=")),
        "memory_swap": next(item.split("=", 1)[1] for item in args if item.startswith("--memory-swap=")),
        "network": next(item.split("=", 1)[1] for item in args if item.startswith("--network=")),
        "mounts": mounts,
        "env": env,
        "inner": args[image_index + 1 :],
    }
    cidfile = Path(one_after("--cidfile"))
    cidfile.write_text(state["id"], encoding="utf-8")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    if mode == "create-then-nonzero":
        raise SystemExit(17)
    if mode == "blocked-launch":
        while True:
            time.sleep(0.05)
    print(state["id"])
    raise SystemExit(0)
if args and args[0] == "inspect":
    fmt = one_after("--format")
    if fmt == "{{.Id}}|{{.Config.Hostname}}|{{.HostConfig.CgroupParent}}|{{.State.Running}}":
        hostname = Path("/proc/sys/kernel/hostname").read_text(encoding="utf-8").strip()
        print("|".join(("b" * 64, hostname, os.environ["CGROUP_PARENT_DEV_INTERACTIVE"], "true")))
        raise SystemExit(0)
    if is_unit_probe_target(args[1]):
        state = read_unit_probe_state()
        print("|".join((state["id"], "/" + state["name"], state["owner"])))
        raise SystemExit(0)
    state = read_state()
    if mode == "hang-inspect":
        time.sleep(5)
    if fmt == "{{.Id}}":
        print(state["id"])
    elif ".State.Running" in fmt:
        print("true" if mode in {"blocked-wait", "blocked-launch", "create-then-nonzero", "stop-fails", "force-remove-fails"} and not state.get("stopped") else "false")
    elif ".Config.User" in fmt:
        cgroupns = "private" if mode == "bad-config" else state["cgroupns"]
        print("|".join((state["id"], "/" + state["name"], state["token"], "1003", state["cgroup_parent"], cgroupns, "3000000000", "2147483648", "8589934592", state["network"])))
    elif ".Mounts" in fmt:
        for mount in state["mounts"]:
            fields = dict(part.split("=", 1) for part in mount.split(","))
            print(fields["src"] + "\t" + fields["dst"])
    elif ".Config.Env" in fmt:
        print("\n".join(state["env"]))
    elif ".Name" in fmt and "Labels" in fmt:
        token = state["token"] + "-other" if mode == "bad-owner" else state["token"]
        print("|".join((state["id"], "/" + state["name"], token)))
    raise SystemExit(0)
if args and args[0] == "wait":
    if is_unit_probe_target(args[1]):
        print("0")
        raise SystemExit(0)
    if mode == "blocked-wait":
        while not read_state().get("stopped"):
            time.sleep(0.05)
    print(os.environ.get("DOCKER_WAIT_STATUS", "0"))
    raise SystemExit(0)
if args and args[0] == "logs":
    if is_unit_probe_target(args[1]):
        for unit in read_unit_probe_state()["units"]:
            print(f"ASSAY_UNIT_BEGIN={unit}")
            print(f"Id={unit}")
            print("LoadState=loaded")
            print(f"FragmentPath=/etc/systemd/system/{unit}")
            print(f"ASSAY_UNIT_END={unit}")
        raise SystemExit(0)
    if mode == "hang-logs" and "--follow" not in args:
        time.sleep(5)
    state = read_state()
    lane = state["inner"][-1]
    if lane == "self-qualification":
        print("ASSAY_SELF_QUALIFICATION_VERIFIED=1")
    elif lane == "self-qualification-preflight":
        print("ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1")
    elif lane == "b110-pilot":
        if mode == "pilot-init-refused":
            print("B110_PILOT_INIT_REFUSED=1")
        elif mode == "pilot-timeout":
            print("B110_PILOT_EXIT=124")
            print("B110_PILOT_TIMEOUT_FAILSAFE=1")
        elif mode == "pilot-incomplete":
            print("B110_PILOT_EXIT=4")
        elif mode == "pilot-wrong-marker":
            print("B110_PILOT_EXIT=6")
            print("B110_PILOT_COMPLETED=1")
            print("B110_SCREEN_VERIFIED=1")
        else:
            print("B110_PILOT_EXIT=6")
            print("B110_PILOT_COMPLETED=1")
    elif lane == "b110-screen":
        if mode == "screen-r0-fail":
            print("B110_SCREEN_EXIT=1")
            print("B110_SCREEN_VERDICT=.assay/verdict-b110-screen.json")
            print('{"R0":"FAIL","nonempty":true}')
        elif mode == "screen-timeout":
            print("B110_SCREEN_EXIT=124")
            print("B110_SCREEN_TIMEOUT_FAILSAFE=1")
        elif mode == "screen-malformed-exit":
            print("B110_SCREEN_EXIT=unknown")
            print("B110_SCREEN_VERIFIED=1")
        elif mode == "screen-wrong-marker":
            print("B110_SCREEN_EXIT=1")
            print("B110_PILOT_COMPLETED=1")
        else:
            # A complete survivor screen is valid even though assay run returns 1.
            print("B110_SCREEN_EXIT=1")
            print("B110_SCREEN_VERIFIED=1")
    raise SystemExit(0)
if args and args[0] == "stop":
    if mode in {"stop-fails", "force-remove-fails"}:
        raise SystemExit(17)
    state = read_state()
    state["stopped"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")
    raise SystemExit(0)
if args and args[0] == "rm":
    if is_unit_probe_target(args[-1]):
        raise SystemExit(0)
    if "-f" in args and mode == "force-remove-fails":
        raise SystemExit(18)
    raise SystemExit(0)
raise SystemExit(90)
'''


@pytest.fixture
def committed_worktree():
    worktrees_dir = REPO_ROOT / ".worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    worktree = Path(tempfile.mkdtemp(prefix="assay-b105-launcher-", dir=worktrees_dir))
    project = worktree / "assay"
    (project / "tools").mkdir(parents=True)
    (project / ".gitignore").write_text(".assay/\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname='assay-fixture'\nversion='1.0'\n", encoding="utf-8")
    shutil.copy2(SCRIPT, project / "tools" / SCRIPT.name)
    shutil.copy2(CGROUP_HELPER, project / "tools" / CGROUP_HELPER.name)

    def git(*args: str) -> None:
        subprocess.run(
            [
                "git",
                "-c", "maintenance.auto=false",
                "-c", "maintenance.autoDetach=false",
                "-c", "gc.autoDetach=false",
                "-c", "user.name=Assay gate fixture",
                "-c", "user.email=assay-gate-fixture@example.invalid",
                *args,
            ],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        )

    try:
        git("init", "--quiet")
        git("add", "assay")
        git("commit", "--quiet", "-m", "seed B105 launcher fixture")
        yield worktree
    finally:
        shutil.rmtree(worktree, ignore_errors=True)


def run_launcher(
    tmp_path: Path,
    worktree: Path,
    *,
    lane: str,
    mode: str = "success",
    wait_status: str = "0",
    background: str | None = None,
    host_workspace_root: str | None = None,
    shared_host_value: str | None = None,
    status_fail_at: int | None = None,
    trace_timeouts: bool = False,
    fast_docker_timeout: bool = False,
    hide_docker: bool = False,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    if not hide_docker:
        docker = fake_bin / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
    if status_fail_at is not None:
        git = fake_bin / "git"
        git.write_text(FAKE_GIT, encoding="utf-8")
        git.chmod(0o755)
    if trace_timeouts or fast_docker_timeout:
        timeout = fake_bin / "timeout"
        timeout.write_text(FAKE_TIMEOUT, encoding="utf-8")
        timeout.chmod(0o755)
    trace = tmp_path / "docker.jsonl"
    state = tmp_path / "docker-state.json"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
        "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
        "HOSTNAME": "deliberately-not-the-parent-container-name",
        "DOCKER_TRACE": str(trace),
        "DOCKER_STATE": str(state),
        "DOCKER_UNIT_PROBE_STATE": str(tmp_path / "docker-unit-probe-state.json"),
        "DOCKER_MODE": mode,
        "DOCKER_WAIT_STATUS": wait_status,
        "TMPDIR": str(tmp_path),
        "REAL_GIT": shutil.which("git"),
        "REAL_TIMEOUT": shutil.which("timeout"),
        "GIT_STATUS_COUNTER": str(tmp_path / "git-status-count"),
        "GIT_STATUS_FAIL_AT": str(status_fail_at or 0),
        "TIMEOUT_TRACE": str(tmp_path / "timeout.jsonl"),
        "FAST_DOCKER_TIMEOUT": "1" if fast_docker_timeout else "0",
    }
    env.pop("ASSAY_GATE_HOST_WORKSPACE_ROOT", None)
    env.pop("ASSAY_GATE_ALLOW_SHARED_HOST", None)
    if host_workspace_root is not None:
        env["ASSAY_GATE_HOST_WORKSPACE_ROOT"] = host_workspace_root
    if shared_host_value is not None:
        env["ASSAY_GATE_ALLOW_SHARED_HOST"] = shared_host_value
    env.pop("CGROUP_PARENT_DEV_BACKGROUND", None)
    if background is not None:
        env["CGROUP_PARENT_DEV_BACKGROUND"] = background
    if hide_docker:
        minimal_bin = tmp_path / "minimal-bin"
        minimal_bin.mkdir(exist_ok=True)
        for name in ("bash", "git", "findmnt", "realpath", "flock", "chmod", "rm"):
            target = shutil.which(name)
            assert target is not None
            (minimal_bin / name).symlink_to(target)
        env["PATH"] = str(minimal_bin)
    started = time.monotonic()
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(worktree), lane],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()] if trace.exists() else []
    return proc, calls, time.monotonic() - started


def launch_env(tmp_path: Path, *, mode: str):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    trace = tmp_path / "docker.jsonl"
    state = tmp_path / "docker-state.json"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CGROUP_PARENT_DEV_GATES": "dev-gates.slice",
        "CGROUP_PARENT_DEV_INTERACTIVE": "dev-interactive.slice",
        "HOSTNAME": "deliberately-not-the-parent-container-name",
        "DOCKER_TRACE": str(trace),
        "DOCKER_STATE": str(state),
        "DOCKER_UNIT_PROBE_STATE": str(tmp_path / "docker-unit-probe-state.json"),
        "DOCKER_MODE": mode,
        "TMPDIR": str(tmp_path),
    }
    env.pop("ASSAY_GATE_HOST_WORKSPACE_ROOT", None)
    env.pop("ASSAY_GATE_ALLOW_SHARED_HOST", None)
    env.pop("CGROUP_PARENT_DEV_BACKGROUND", None)
    return env, trace


def launch_call(calls: list[list[str]]) -> list[str]:
    return next(call for call in calls if call and call[0] == "run" and "-d" in call)


def host_workspace_root(worktree: Path) -> str:
    target = subprocess.run(
        ["findmnt", "--target", str(worktree), "--noheadings", "--output", "TARGET"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    fsroot = subprocess.run(
        ["findmnt", "--target", str(worktree), "--noheadings", "--output", "FSROOT"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert str(worktree).startswith(target.rstrip("/") + "/")
    return fsroot


@pytest.mark.parametrize(
    ("lane", "wait_budget", "inner_budget", "marker", "background"),
    [
        (
            "self-qualification-preflight",
            "65m",
            None,
            "ASSAY_SELF_QUALIFICATION_PREFLIGHT_VERIFIED=1",
            None,
        ),
        (
            "self-qualification",
            "7h40m",
            "27000s",
            "ASSAY_SELF_QUALIFICATION_VERIFIED=1",
            "dev-background.slice",
        ),
        (
            "b110-pilot",
            "2h15m",
            None,
            "B110_PILOT_COMPLETED=1",
            None,
        ),
        (
            "b110-screen",
            "7h15m",
            None,
            "B110_SCREEN_VERIFIED=1",
            None,
        ),
    ],
)
def test_outer_runner_launches_bounded_cgroup_visible_container_and_reads_job_status(
    tmp_path: Path,
    committed_worktree: Path,
    lane: str,
    wait_budget: str,
    inner_budget: str | None,
    marker: str,
    background: str | None,
):
    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane=lane,
        background=background,
    )

    assert proc.returncode == 0, proc.stderr
    assert marker in proc.stdout
    if lane.startswith("b110-"):
        assert "ASSAY_B110_GATE_CONTAINER_EXIT=0" in proc.stdout
        assert f"ASSAY_B110_GATE_COMPLETE={lane}" in proc.stdout
    else:
        assert "ASSAY_B105_GATE_CONTAINER_EXIT=0" in proc.stdout
        assert f"ASSAY_B105_GATE_COMPLETE={lane}" in proc.stdout
    launch = launch_call(calls)
    assert "--init" in launch
    assert "--cgroupns=host" in launch
    assert "--cgroup-parent=dev-gates.slice" in launch
    assert "--cpus=3" in launch
    assert "--memory=2g" in launch
    assert "--memory-swap=8g" in launch
    assert "--network=none" in launch
    cgroup_probe = next(call for call in calls if "--cgroupns=host" in call)
    assert "--cgroup-parent=dev-gates.slice" in cgroup_probe
    assert "CGROUP_PARENT_DEV_GATES=dev-gates.slice" in launch
    if background is None:
        assert "CGROUP_PARENT_DEV_BACKGROUND" not in " ".join(launch)
    else:
        assert f"CGROUP_PARENT_DEV_BACKGROUND={background}" in launch
    host_workspace = host_workspace_root(committed_worktree)
    mounts = [launch[index + 1] for index, item in enumerate(launch[:-1]) if item == "--mount"]
    expected_mounts = [f"type=bind,src={host_workspace},dst={host_workspace}"]
    if host_workspace != "/workspaces/vbpub":
        expected_mounts.append(f"type=bind,src={host_workspace},dst=/workspaces/vbpub")
    assert mounts == expected_mounts
    assert all("docker.sock" not in mount for mount in mounts)
    if inner_budget is not None:
        assert "timeout" in launch and inner_budget in launch
    else:
        assert "timeout" not in launch
    assert f"ASSAY_B105_GATE_WAIT_TIMEOUT={wait_budget}" in proc.stdout
    assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls)
    state = json.loads((tmp_path / "docker-state.json").read_text(encoding="utf-8"))
    env = state["env"]
    source_commit = subprocess.run(
        ["git", "-C", str(committed_worktree), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    source_tree = subprocess.run(
        ["git", "-C", str(committed_worktree), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert f"ASSAY_B105_GATE_EXPECTED_COMMIT={source_commit}" in env
    assert f"ASSAY_B105_GATE_EXPECTED_TREE={source_tree}" in env


@pytest.mark.parametrize(
    ("lane", "mode", "expected_error"),
    [
        ("b110-pilot", "pilot-init-refused", "campaign initialization was refused"),
        ("b110-pilot", "pilot-timeout", "campaign failsafe"),
        ("b110-pilot", "pilot-incomplete", "complete exit status (6)"),
        ("b110-screen", "screen-r0-fail", "verified complete verdict marker"),
        ("b110-screen", "screen-timeout", "failsafe timeout"),
        ("b110-screen", "screen-malformed-exit", "run exit status"),
        ("b110-screen", "screen-wrong-marker", "pilot-mode marker"),
        ("b110-pilot", "pilot-wrong-marker", "screen-mode marker"),
    ],
)
def test_b110_child_evidence_is_required_before_outer_completion(
    tmp_path: Path, committed_worktree: Path, lane: str, mode: str, expected_error: str
):
    proc, _calls, _elapsed = run_launcher(tmp_path, committed_worktree, lane=lane, mode=mode)

    assert proc.returncode != 0
    assert expected_error in proc.stderr
    assert "ASSAY_B110_GATE_COMPLETE" not in proc.stdout


@pytest.mark.parametrize(("lane", "wait_seconds"), [
    ("b110-pilot", 2 * 60 * 60 + 15 * 60),
    ("b110-screen", 7 * 60 * 60 + 15 * 60),
])
def test_b110_container_wait_and_log_follow_include_the_exact_bounds(
    tmp_path: Path, committed_worktree: Path, lane: str, wait_seconds: int
):
    proc, _calls, _elapsed = run_launcher(
        tmp_path, committed_worktree, lane=lane, trace_timeouts=True
    )

    assert proc.returncode == 0, proc.stderr
    timeouts = [
        json.loads(line)
        for line in (tmp_path / "timeout.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    wait = next(
        args for args in timeouts
        if "docker" in args and args[args.index("docker") + 1 : args.index("docker") + 3]
        == ["wait", CONTAINER_ID]
    )
    follower = next(
        args for args in timeouts
        if "docker" in args and args[args.index("docker") + 1 : args.index("docker") + 4]
        == ["logs", "--follow", CONTAINER_ID]
    )
    assert wait[wait.index("docker") - 1] == f"{wait_seconds}s"
    assert follower[follower.index("docker") - 1] == f"{wait_seconds + 90}s"


def test_runner_refuses_to_launch_beside_an_active_registered_gate(tmp_path: Path, committed_worktree: Path):
    proc, calls, _elapsed = run_launcher(
        tmp_path, committed_worktree, lane="self-qualification-preflight", mode="busy"
    )

    assert proc.returncode == 3
    assert "host busy" in proc.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)


def test_b110_screen_removes_stale_verdict_before_host_admission(
    tmp_path: Path, committed_worktree: Path
):
    state = committed_worktree / "assay" / ".assay"
    state.mkdir(parents=True)
    stale_outputs = (
        "verdict-b110-screen.json",
        "b110-screen-plan.json",
        "b110-screen-run.log",
    )
    for name in stale_outputs:
        (state / name).write_text("stale attempt\n", encoding="utf-8")

    proc, calls, _elapsed = run_launcher(
        tmp_path, committed_worktree, lane="b110-screen", mode="busy"
    )

    assert proc.returncode == 3
    assert "host busy" in proc.stderr
    assert all(not (state / name).exists() for name in stale_outputs)
    assert any(call[:2] == ["ps", "--no-trunc"] for call in calls)
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)


def test_b110_screen_removes_stale_verdict_before_shared_host_refusal(
    tmp_path: Path, committed_worktree: Path
):
    state = committed_worktree / "assay" / ".assay"
    state.mkdir(parents=True)
    stale_outputs = (
        "verdict-b110-screen.json",
        "b110-screen-plan.json",
        "b110-screen-run.log",
    )
    for name in stale_outputs:
        (state / name).write_text("stale attempt\n", encoding="utf-8")

    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-screen",
        shared_host_value="1",
    )

    assert proc.returncode == 3
    assert "ASSAY_GATE_ALLOW_SHARED_HOST=1 is unsupported" in proc.stderr
    assert all(not (state / name).exists() for name in stale_outputs)
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)


def test_b110_screen_clears_stale_outputs_before_host_bind_refusal(
    tmp_path: Path, committed_worktree: Path
):
    state = committed_worktree / "assay" / ".assay"
    state.mkdir(parents=True)
    stale_outputs = (
        "verdict-b110-screen.json",
        "b110-screen-plan.json",
        "b110-screen-run.log",
    )
    for name in stale_outputs:
        (state / name).write_text("stale attempt\n", encoding="utf-8")

    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-screen",
        host_workspace_root="/other/vbpub",
    )

    assert proc.returncode != 0
    assert "differs from the findmnt workspace bind source" in proc.stderr
    assert all(not (state / name).exists() for name in stale_outputs)
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)


def test_b110_pilot_clears_stale_attempt_outputs_before_host_admission(
    tmp_path: Path, committed_worktree: Path
):
    state = committed_worktree / "assay" / ".assay"
    state.mkdir(parents=True)
    stale_outputs = (
        "b110-pilot-plan.json",
        "b110-pilot-candidates.txt",
        "b110-pilot-selection.json",
        "b110-pilot-summary.json",
        "b110-pilot-run.log",
    )
    for name in stale_outputs:
        (state / name).write_text("stale attempt\n", encoding="utf-8")
    progress = state / "progress-b110-pilot.jsonl"
    progress.write_text('{"event":"resume evidence"}\n', encoding="utf-8")
    deadline = state / "campaign-deadline-b110-pilot-test.json"
    deadline.write_text('{"expires_at_utc":"2030-01-01T00:00:00Z"}\n', encoding="utf-8")
    mutation_state = state / "b110-pilot-state" / "PILOT-STATE"
    mutation_state.parent.mkdir()
    mutation_state.write_text("state identity\n", encoding="utf-8")

    proc, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
        mode="busy",
    )

    assert proc.returncode == 3
    assert "host busy" in proc.stderr
    assert all(not (state / name).exists() for name in stale_outputs)
    assert progress.read_text(encoding="utf-8") == '{"event":"resume evidence"}\n'
    assert deadline.exists()
    assert mutation_state.read_text(encoding="utf-8") == "state identity\n"


@pytest.mark.parametrize(
    ("lane", "artifact_names"),
    [
        (
            "b110-pilot",
            (
                "b110-pilot-plan.json",
                "b110-pilot-candidates.txt",
                "b110-pilot-selection.json",
                "b110-pilot-summary.json",
                "b110-pilot-run.log",
            ),
        ),
        (
            "b110-screen",
            (
                "verdict-b110-screen.json",
                "b110-screen-plan.json",
                "b110-screen-run.log",
            ),
        ),
    ],
)
def test_b110_attempt_clears_old_artifacts_before_missing_docker_refusal(
    tmp_path: Path,
    committed_worktree: Path,
    lane: str,
    artifact_names: tuple[str, ...],
):
    state = committed_worktree / "assay" / ".assay"
    state.mkdir(parents=True)
    for name in artifact_names:
        (state / name).write_text("previous attempt\n", encoding="utf-8")

    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane=lane,
        hide_docker=True,
    )

    assert proc.returncode == 1
    assert "Docker is required" in proc.stderr
    assert all(not (state / name).exists() for name in artifact_names)
    assert calls == []


def test_b105_refuses_the_shared_host_opt_in(tmp_path: Path, committed_worktree: Path):
    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="self-qualification-preflight",
        shared_host_value="1",
    )

    assert proc.returncode == 3
    assert "ASSAY_GATE_ALLOW_SHARED_HOST=1 is unsupported" in proc.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)


def test_b105_lock_serializes_callers_across_tmp_namespaces(
    tmp_path: Path, committed_worktree: Path
):
    common_dir = subprocess.run(
        ["git", "-C", str(committed_worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    lock_path = Path(common_dir) / "assay-b105-self-qualification.lock"
    lock_path.touch(mode=0o600)
    with lock_path.open("r+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc, calls, _elapsed = run_launcher(
            tmp_path,
            committed_worktree,
            lane="self-qualification-preflight",
        )

    assert proc.returncode == 3
    assert "another B105 host check holds the shared Git-directory lock" in proc.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)


def test_b110_screen_lock_refusal_preserves_the_existing_verdict(
    tmp_path: Path, committed_worktree: Path
):
    common_dir = subprocess.run(
        ["git", "-C", str(committed_worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    lock_path = Path(common_dir) / "assay-b105-self-qualification.lock"
    lock_path.touch(mode=0o600)
    state = committed_worktree / "assay" / ".assay"
    state.mkdir(parents=True)
    active_outputs = (
        "verdict-b110-screen.json",
        "b110-screen-plan.json",
        "b110-screen-run.log",
    )
    for name in active_outputs:
        (state / name).write_text("active attempt\n", encoding="utf-8")

    with lock_path.open("r+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc, calls, _elapsed = run_launcher(
            tmp_path,
            committed_worktree,
            lane="b110-screen",
        )

    assert proc.returncode == 3
    assert "another B105 host check holds the shared Git-directory lock" in proc.stderr
    assert all((state / name).read_text(encoding="utf-8") == "active attempt\n" for name in active_outputs)
    assert not any(call and call[0] in {"ps", "run"} for call in calls)


def test_runner_fails_on_nonzero_container_exit_and_removes_its_verified_container(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="self-qualification-preflight",
        wait_status="137",
    )

    assert proc.returncode != 0
    assert "container exited with status 137" in proc.stderr
    assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls)


def test_failed_stop_falls_back_to_force_removal_of_verified_container(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="self-qualification-preflight",
        mode="stop-fails",
        wait_status="137",
    )

    assert proc.returncode != 0
    assert "graceful stop failed" in proc.stderr
    assert any(call and call[0] == "stop" and CONTAINER_ID in call for call in calls)
    assert any(call[:3] == ["rm", "-f", CONTAINER_ID] for call in calls)
    assert "preserving ownership evidence" not in proc.stderr


def test_failed_force_removal_marks_cleanup_incomplete_and_preserves_owner_evidence(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="self-qualification-preflight",
        mode="force-remove-fails",
        wait_status="137",
    )

    assert proc.returncode != 0
    assert "graceful stop failed" in proc.stderr
    assert any(call[:3] == ["rm", "-f", CONTAINER_ID] for call in calls)
    assert "preserving ownership evidence at" in proc.stderr
    scratch = next(tmp_path.glob("assay-b105-container.*"))
    assert "ownership_token=" in (scratch / "ownership.txt").read_text(encoding="utf-8")


def test_runner_refuses_name_based_cleanup_when_the_owner_token_disagrees(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="self-qualification-preflight",
        mode="bad-owner",
    )

    assert proc.returncode != 0
    assert "ownership token does not match" in proc.stderr
    assert "refusing removal" in proc.stderr
    assert not any(call[:2] == ["rm", CONTAINER_ID] for call in calls)


def test_runner_removes_a_verified_owned_container_when_launch_policy_mismatches(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="self-qualification-preflight",
        mode="bad-config",
    )

    assert proc.returncode != 0
    assert "launch verification mismatch" in proc.stderr
    assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls)


def test_runner_cancellation_stops_and_removes_its_owned_container(
    tmp_path: Path, committed_worktree: Path
):
    env, trace = launch_env(tmp_path, mode="blocked-wait")
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), str(committed_worktree), "self-qualification-preflight"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(200):
            if trace.exists() and any(
                json.loads(line)[:1] == ["wait"]
                and CONTAINER_ID in json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ):
                break
            if proc.poll() is not None:
                pytest.fail("launcher exited before reaching docker wait")
            time.sleep(0.05)
        else:
            pytest.fail("launcher did not reach docker wait")

        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert proc.returncode == 143, (stdout, stderr)
    assert any(call and call[0] == "stop" and CONTAINER_ID in call for call in calls)
    assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls)


def test_host_workspace_override_must_match_findmnt_workspace_bind(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path, committed_worktree, lane="self-qualification-preflight",
        host_workspace_root="/other/vbpub",
    )

    assert proc.returncode != 0
    assert "differs from the findmnt workspace bind source" in proc.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)


def test_created_container_is_reconciled_after_nonzero_docker_run(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path, committed_worktree, lane="self-qualification-preflight",
        mode="create-then-nonzero",
    )

    assert proc.returncode == 17
    assert "docker run failed (exit 17)" in proc.stderr
    assert any(call[0] == "inspect" and "{{.Id}}" in call for call in calls)
    assert any(call[0] == "stop" and CONTAINER_ID in call for call in calls)
    assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls)
    assert "ASSAY_B105_GATE_COMPLETE" not in proc.stdout


def test_cancellation_during_launch_reconciles_cidfile_and_owned_name(
    tmp_path: Path, committed_worktree: Path
):
    env, trace = launch_env(tmp_path, mode="blocked-launch")
    state_path = Path(env["DOCKER_STATE"])
    proc = subprocess.Popen(
        ["bash", str(SCRIPT), str(committed_worktree), "self-qualification-preflight"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        for _ in range(200):
            if state_path.exists():
                break
            if proc.poll() is not None:
                pytest.fail("launcher exited before the ambiguous creation window")
            time.sleep(0.05)
        else:
            pytest.fail("docker run did not create its container")
        proc.send_signal(signal.SIGTERM)
        stdout, stderr = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)

    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert proc.returncode == 143, (stdout, stderr)
    assert any(call[0] == "inspect" and "{{.Id}}" in call for call in calls)
    assert any(call[0] == "stop" and CONTAINER_ID in call for call in calls)
    assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls)


@pytest.mark.parametrize("failure_at", [1, 2])
def test_git_status_failure_never_certifies_a_clean_worktree(
    tmp_path: Path, committed_worktree: Path, failure_at: int
):
    proc, calls, _elapsed = run_launcher(
        tmp_path, committed_worktree, lane="self-qualification-preflight",
        status_fail_at=failure_at,
    )

    assert proc.returncode != 0
    assert "cannot read selected worktree status" in proc.stderr
    assert "ASSAY_B105_GATE_COMPLETE" not in proc.stdout
    if failure_at == 1:
        assert not any(call[0] == "run" and "-d" in call for call in calls)
    else:
        assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls)


def test_all_wrapper_docker_calls_have_client_deadlines(
    tmp_path: Path, committed_worktree: Path
):
    proc, calls, _elapsed = run_launcher(
        tmp_path, committed_worktree, lane="self-qualification-preflight",
        trace_timeouts=True,
    )
    assert proc.returncode == 0, proc.stderr
    timeout_calls = [json.loads(line) for line in
                     (tmp_path / "timeout.jsonl").read_text(encoding="utf-8").splitlines()]
    bounded_docker = [call[call.index("docker") + 1:] for call in timeout_calls
                      if "docker" in call]
    for call in calls:
        if call[:2] == ["run", "--rm"]:  # cgroup helper owns its own probe
            continue
        assert call in bounded_docker, call
    launch_timeout = next(call for call in timeout_calls
                          if "docker" in call and call[call.index("docker") + 1:][:2] == ["run", "-d"])
    assert "60s" in launch_timeout[:launch_timeout.index("docker")]


@pytest.mark.parametrize(
    ("mode", "message", "removed"),
    [
        ("hang-inspect", "could not verify B105 container ownership", False),
        ("hang-logs", "could not collect the completed B105 container log", True),
    ],
)
def test_hung_docker_client_is_bounded_and_cleanup_is_conservative(
    tmp_path: Path, committed_worktree: Path, mode: str, message: str, removed: bool
):
    proc, calls, elapsed = run_launcher(
        tmp_path, committed_worktree, lane="self-qualification-preflight",
        mode=mode, fast_docker_timeout=True,
    )
    assert proc.returncode != 0
    assert message in proc.stderr
    assert elapsed < 5
    assert any(call[:2] == ["rm", CONTAINER_ID] for call in calls) is removed
    if not removed:
        assert "preserving ownership evidence at" in proc.stderr
        scratch = next(tmp_path.glob("assay-b105-container.*"))
        assert "ownership_token=" in (scratch / "ownership.txt").read_text(encoding="utf-8")
