"""The B105 outer runner proves its Docker argv and ownership lifecycle."""

from __future__ import annotations

import json
import argparse
import fcntl
import hashlib
import importlib.util
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from gate.tests.support import PROJECT_ROOT
from gate.tests.test_analysis_r2_pilot import _run_checker_cli_with_valid_evidence
from gate.tests.test_self_lane import _run_analysis_r2_pilot_inner_harness

REPO_ROOT = PROJECT_ROOT.parent
SCRIPT = PROJECT_ROOT / "tools" / "self-qualification-container.sh"
CGROUP_HELPER = PROJECT_ROOT / "tools" / "cgroup-parent.sh"
PILOT_HOST_CHECK = PROJECT_ROOT / "tools" / "b110_pilot_host_check.py"
ANALYSIS_PILOT_HOST_CHECK = PROJECT_ROOT / "tools" / "analysis_r2_pilot_host_check.py"
CONTAINER_ID = "a" * 64
ANALYSIS_PILOT_ATTESTATION = "f" * 64


FAKE_GIT = r'''#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

args = sys.argv[1:]
if "status" in args and "--porcelain" in args:
    counter = Path(os.environ["GIT_STATUS_COUNTER"])
    number = int(counter.read_text(encoding="ascii")) + 1 if counter.exists() else 1
    counter.write_text(str(number), encoding="ascii")
    if number == int(os.environ.get("GIT_STATUS_DELAY_AT", "0")):
        time.sleep(float(os.environ.get("GIT_STATUS_DELAY_SECONDS", "0")))
    if number == int(os.environ["GIT_STATUS_FAIL_AT"]):
        raise SystemExit(19)
    if number == int(os.environ.get("GIT_STATUS_DIRTY_AT", "0")):
        result = subprocess.run(
            [os.environ["REAL_GIT"], *args], capture_output=True, text=True
        )
        sys.stdout.write(result.stdout)
        sys.stdout.write(" M assay/tools/analysis_r2_pilot_check.py\n")
        sys.stderr.write(result.stderr)
        raise SystemExit(result.returncode)
    if os.environ.get("TAMPER_HOST_CHECKER_AFTER_FINAL_STATUS") == "1" and number == 2:
        result = subprocess.run(
            [os.environ["REAL_GIT"], *args], capture_output=True, text=True
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        worktree = Path(args[args.index("-C") + 1])
        checker = worktree / "assay" / "tools" / "b110_pilot_host_check.py"
        checker.chmod(0o600)
        checker.write_text("raise SystemExit(42)\n", encoding="utf-8")
        Path(os.environ["HOST_CHECKER_TAMPER_COUNT"]).write_text("1", encoding="ascii")
        raise SystemExit(result.returncode)
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
import hashlib
import json
import os
import sys
import subprocess
import time
from datetime import datetime, timedelta, timezone
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
    if mode == "analysis-pilot-inner-log":
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
            project = Path(os.environ["DOCKER_WORKTREE"]) / "assay"
            artifacts = project / ".assay"
            state_dir = artifacts / "b110-pilot-state"
            state_dir.mkdir(parents=True, exist_ok=True)
            identity = "a" * 64
            commit = subprocess.check_output(
                [os.environ["REAL_GIT"], "-C", os.environ["DOCKER_WORKTREE"], "rev-parse", "HEAD"],
                text=True,
            ).strip()
            tree = subprocess.check_output(
                [os.environ["REAL_GIT"], "-C", os.environ["DOCKER_WORKTREE"], "rev-parse", "HEAD^{tree}"],
                text=True,
            ).strip()
            campaign = "b110-pilot-" + commit[:12]
            now = datetime.now(timezone.utc).replace(microsecond=0)
            if mode == "pilot-campaign-expired":
                created = now - timedelta(hours=3)
                expires = created + timedelta(hours=2)
            else:
                created = now
                expires = now + timedelta(hours=2)
            deadline_name = f"campaign-deadline-b110-pilot-{commit[:12]}.json"
            deadline = {
                "schema": "assay-campaign-deadline/1",
                "campaign": campaign,
                "commit": commit,
                "git_tree": tree,
                "lanes": ["self-qualification"],
                "assay_version": "8.0.0",
                "wheel_sha256": "f" * 64,
                "plan_sha256": {"self-qualification": "e" * 64},
                "created_at_utc": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at_utc": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            deadline_raw = (json.dumps(deadline, sort_keys=True) + "\n").encode()
            attempt_started = time.time_ns()
            if mode == "pilot-attempt-expired":
                attempt_started -= 91 * 60 * 1_000_000_000
            elif mode == "pilot-attempt-near-expired":
                attempt_started -= 90 * 60 * 1_000_000_000 - 5 * 1_000_000_000
            attempt_window = {
                "schema": "assay-b110-pilot-attempt-window/1",
                "campaign": campaign,
                "commit": commit,
                "started_at_epoch_ns": attempt_started,
                "expires_at_epoch_ns": attempt_started + 90 * 60 * 1_000_000_000,
            }
            attempt_raw = (
                json.dumps(attempt_window, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            files = {
                ".assay/b110-pilot-plan.json": b"plan\n",
                ".assay/b110-pilot-candidates.txt": (
                    "# b110 pilot selection seed=b110-pilot-2026 size=64 plan_candidates=1\n"
                    + identity + "\n"
                ).encode(),
                ".assay/b110-pilot-selection.json": b"selection\n",
                ".assay/b110-pilot-summary.json": b"summary\n",
                ".assay/b110-pilot-run.log": b"run log\n",
                ".assay/r2-manifest-b110-pilot.txt": b"test_contract::test_example\n",
                ".assay/b110-pilot-attempt-window.json": attempt_raw,
                ".assay/progress-b110-pilot.jsonl": b"progress\n",
                f".assay/{deadline_name}": deadline_raw,
                ".assay/b110-pilot-state/PILOT-STATE": b"sentinel\n",
                f".assay/b110-pilot-state/{identity}.json": b"state\n",
            }
            for relative, content in files.items():
                target = project / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            manifest_entries = dict(files)
            if mode == "pilot-manifest-omits":
                manifest_entries.pop(".assay/b110-pilot-run.log")
            manifest = artifacts / "b110-pilot-artifacts.sha256"
            manifest_bytes = "".join(
                f"{hashlib.sha256(content).hexdigest()}  {relative}\n"
                for relative, content in sorted(manifest_entries.items())
            ).encode()
            manifest.write_bytes(manifest_bytes)
            if mode == "pilot-artifact-tampered":
                (artifacts / "b110-pilot-summary.json").write_text("changed after attestation\n")
            print("B110_PILOT_EXIT=6")
            print("B110_PILOT_VERIFIED=1")
            print("B110_PILOT_ATTESTATION_SHA256=" + hashlib.sha256(manifest_bytes).hexdigest())
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
    elif lane == "analysis-r2-pilot":
        if mode == "analysis-pilot-incomplete":
            print("ANALYSIS_R2_PILOT_EXIT=4")
        elif mode == "analysis-pilot-inner-log":
            sys.stdout.write(
                Path(os.environ["ANALYSIS_PILOT_INNER_LOG"]).read_text(encoding="utf-8")
            )
        elif mode == "analysis-pilot-duplicate-marker":
            print("ANALYSIS_R2_PILOT_EXIT=6")
            print("ANALYSIS_R2_PILOT_CHECKER_VERIFIED=" + "f" * 64)
            print("ANALYSIS_R2_PILOT_CHECKER_VERIFIED=" + "f" * 64)
        elif mode == "analysis-pilot-premature-final-marker":
            print("ANALYSIS_R2_PILOT_EXIT=6")
            print("ANALYSIS_R2_PILOT_CHECKER_VERIFIED=" + "f" * 64)
            print("ANALYSIS_R2_PILOT_VERIFIED=1")
        else:
            print("ANALYSIS_R2_PILOT_EXIT=6")
            if mode == "analysis-pilot-b110-marker":
                print("B110_SCREEN_EXIT=1")
            if mode != "analysis-pilot-unverified":
                print("ANALYSIS_R2_PILOT_CHECKER_VERIFIED=" + "f" * 64)
    sys.stdout.flush()
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
    (project / ".gitignore").write_text(".assay/\n.run-gate/\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname='assay-fixture'\nversion='1.0'\n", encoding="utf-8")
    shutil.copy2(PROJECT_ROOT / "run-gate.toml", project / "run-gate.toml")
    (project / "run-gate.py").symlink_to((PROJECT_ROOT / "run-gate.py").resolve())
    shutil.copy2(SCRIPT, project / "tools" / SCRIPT.name)
    shutil.copy2(CGROUP_HELPER, project / "tools" / CGROUP_HELPER.name)
    shutil.copy2(PILOT_HOST_CHECK, project / "tools" / PILOT_HOST_CHECK.name)
    shutil.copy2(ANALYSIS_PILOT_HOST_CHECK, project / "tools" / ANALYSIS_PILOT_HOST_CHECK.name)

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
        shutil.rmtree(gate_evidence_root(worktree), ignore_errors=True)
        shutil.rmtree(worktree, ignore_errors=True)


def gate_evidence_root(worktree: Path) -> Path:
    return worktree.parent / f"{worktree.name}.run-gate-evidence"


def record_run_gate_success(worktree: Path, *, include_host_verified_marker: bool = True) -> dict:
    project = worktree / "assay"
    state = project / ".assay"
    receipt = json.loads(
        (state / "b110-pilot-evidence.receipt.json").read_text(encoding="utf-8")
    )
    evidence_root = gate_evidence_root(worktree)
    lane_dir = evidence_root / "lanes" / "b110-pilot"
    lane_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(evidence_root, 0o755)
    os.chmod(evidence_root / "lanes", 0o755)
    os.chmod(lane_dir, 0o700)
    run_id = "b" * 32
    log_path = lane_dir / f"{run_id}.log"
    transcript = (
        f"B105_SOURCE_COMMIT={receipt['commit']}\n"
        f"B105_SOURCE_TREE={receipt['tree']}\n"
        f"B110_PILOT_SNAPSHOT_SHA256={receipt['snapshot_sha256']}\n"
    )
    if include_host_verified_marker:
        transcript += f"B110_PILOT_HOST_VERIFIED_SHA256={receipt['snapshot_sha256']}\n"
    log_path.write_text(transcript, encoding="ascii")
    os.chmod(log_path, 0o600)
    record = {
        "run_id": run_id,
        "lane": "b110-pilot",
        "commit": receipt["commit"],
        "outcome": "pass",
        "exit_code": 0,
        "started_at": "2026-10-09T00:00:00Z",
        "duration_seconds": 1.0,
        "worktree": str(worktree),
        "history_eligible": True,
        "log_path": str(log_path),
    }
    history_dir = project / ".run-gate"
    history_dir.mkdir(mode=0o700, exist_ok=True)
    (history_dir / "history.json").write_text(
        json.dumps({"schema": 2, "lanes": {"b110-pilot": {
            "latest": record,
            "history": [record],
        }}}),
        encoding="utf-8",
    )
    return receipt


def record_run_gate_failure_replacing_success(worktree: Path, receipt: dict) -> None:
    """Model run-gate retaining only a newer same-commit completion."""
    project = worktree / "assay"
    evidence_root = gate_evidence_root(worktree)
    lane_dir = evidence_root / "lanes" / "b110-pilot"
    run_id = "c" * 32
    log_path = lane_dir / f"{run_id}.log"
    log_path.write_text("retry refused before a new pilot snapshot\n", encoding="ascii")
    os.chmod(log_path, 0o600)
    failed_record = {
        "run_id": run_id,
        "lane": "b110-pilot",
        "commit": receipt["commit"],
        "outcome": "fail",
        "exit_code": 1,
        "started_at": "2026-10-09T00:01:00Z",
        "duration_seconds": 1.0,
        "worktree": str(worktree),
        "history_eligible": True,
        "log_path": str(log_path),
    }
    history_dir = project / ".run-gate"
    history_dir.mkdir(mode=0o700, exist_ok=True)
    (history_dir / "history.json").write_text(
        json.dumps({"schema": 2, "lanes": {"b110-pilot": {
            "latest": failed_record,
            "history": [],
        }}}),
        encoding="utf-8",
    )


def record_run_gate_error_preserving_success(worktree: Path, receipt: dict) -> None:
    """Model an exit-mapped ERROR: latest changes, eligible pass history stays."""
    project = worktree / "assay"
    history_path = project / ".run-gate" / "history.json"
    store = json.loads(history_path.read_text(encoding="utf-8"))
    lane = store["lanes"]["b110-pilot"]
    run_id = "e" * 32
    log_path = gate_evidence_root(worktree) / "lanes" / "b110-pilot" / f"{run_id}.log"
    log_path.write_text("host prerequisite refused before pilot launch\n", encoding="ascii")
    os.chmod(log_path, 0o600)
    lane["latest"] = {
        "run_id": run_id,
        "lane": "b110-pilot",
        "commit": receipt["commit"],
        "outcome": "error",
        "exit_code": 3,
        "started_at": "2026-10-09T00:01:00Z",
        "duration_seconds": 1.0,
        "worktree": str(worktree),
        "history_eligible": False,
        "log_path": str(log_path),
    }
    history_path.write_text(json.dumps(store), encoding="utf-8")


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
    status_dirty_at: int | None = None,
    trace_timeouts: bool = False,
    fast_docker_timeout: bool = False,
    hide_docker: bool = False,
    host_verify_fails: bool = False,
    host_receipt_write_fails: bool = False,
    host_archive_receipt_move_fails: bool = False,
    host_archive_receipt_read_fails: bool = False,
    fail_chmod: bool = False,
    tamper_host_checker_after_final_status: bool = False,
    status_delay_at: int | None = None,
    status_delay_seconds: float = 0,
    inner_pilot_log: Path | None = None,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    if not hide_docker:
        docker = fake_bin / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
    if fail_chmod:
        fake_chmod = fake_bin / "chmod"
        fake_chmod.write_text("#!/bin/sh\nexit 19\n", encoding="ascii")
        fake_chmod.chmod(0o755)
    if status_fail_at is not None or status_dirty_at is not None or tamper_host_checker_after_final_status or status_delay_at is not None:
        git = fake_bin / "git"
        git.write_text(FAKE_GIT, encoding="utf-8")
        git.chmod(0o755)
    if (
        host_verify_fails
        or host_receipt_write_fails
        or host_archive_receipt_move_fails
        or host_archive_receipt_read_fails
        or lane == "analysis-r2-pilot"
    ):
        injected_parts = []
        if lane == "analysis-r2-pilot":
            injected_parts.append("""
if '--expected-sha256' in sys.argv:
    expected = sys.argv[sys.argv.index('--expected-sha256') + 1]
    if os.environ.get('DOCKER_MODE') == 'analysis-host-check-dirties-evidence':
        counter_path = __import__('pathlib').Path(os.environ['ANALYSIS_HOST_CHECK_CALL_COUNT'])
        count = int(counter_path.read_text(encoding='ascii')) if counter_path.exists() else 0
        counter_path.write_text(str(count + 1), encoding='ascii')
        if count == 0:
            summary = __import__('pathlib').Path(
                os.environ['DOCKER_WORKTREE'], 'assay', '.assay', 'analysis-r2-pilot-summary.json'
            )
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text('changed during outer source check\\n', encoding='ascii')
        else:
            print('injected final B131 evidence refusal', file=sys.stderr)
            raise SystemExit(42)
        print('ANALYSIS_R2_PILOT_HOST_EVIDENCE_VERIFIED=' + expected)
        raise SystemExit(0)
    if os.environ.get('DOCKER_MODE') == 'analysis-host-check-fails':
        print('injected B131 host evidence refusal', file=sys.stderr)
        raise SystemExit(42)
    if os.environ.get('DOCKER_MODE') == 'analysis-host-check-dirties':
        __import__('pathlib').Path(os.environ['DOCKER_WORKTREE'], 'assay', 'README.md').write_text(
            'changed during final B131 host verification\\n', encoding='utf-8'
        )
    print('ANALYSIS_R2_PILOT_HOST_EVIDENCE_VERIFIED=' + expected)
    raise SystemExit(0)
""")
        if host_verify_fails:
            injected_parts.append(
                "if '--verify-published-snapshot' in sys.argv:\n"
                "    print('injected final snapshot refusal', file=sys.stderr)\n"
                "    raise SystemExit(42)\n"
            )
        if host_receipt_write_fails:
            injected_parts.append("""
if '--container-exit' in sys.argv:
    source = sys.stdin.read()
    namespace = {'__name__': 'b110_pilot_host_check_injected', '__file__': '<stdin>'}
    exec(compile(source, '<stdin>', 'exec'), namespace)
    original = namespace['_write_snapshot_receipt_named']
    def injected_writer(assay_fd, name, receipt_raw):
        if name == 'b110-pilot-evidence.receipt.json':
            raise OSError('injected receipt publication failure')
        return original(assay_fd, name, receipt_raw)
    namespace['_write_snapshot_receipt_named'] = injected_writer
    arguments = sys.argv[2:] if sys.argv[1:2] == ['-'] else sys.argv[1:]
    raise SystemExit(namespace['main'](arguments))
""")
        if host_archive_receipt_move_fails:
            injected_parts.append("""
if '--archive-prior-snapshot' in sys.argv:
    source = sys.stdin.read()
    namespace = {'__name__': 'b110_pilot_host_check_archive_injected', '__file__': '<stdin>'}
    exec(compile(source, '<stdin>', 'exec'), namespace)
    original = namespace['os'].rename
    def injected_rename(source_name, destination_name, *args, **kwargs):
        if source_name == 'b110-pilot-evidence.receipt.json':
            raise OSError('injected archive receipt move failure')
        return original(source_name, destination_name, *args, **kwargs)
    namespace['os'].rename = injected_rename
    arguments = sys.argv[2:] if sys.argv[1:2] == ['-'] else sys.argv[1:]
    raise SystemExit(namespace['main'](arguments))
""")
        if host_archive_receipt_read_fails:
            injected_parts.append("""
if '--archive-prior-snapshot' in sys.argv:
    source = sys.stdin.read()
    namespace = {'__name__': 'b110_pilot_host_check_read_injected', '__file__': '<stdin>'}
    exec(compile(source, '<stdin>', 'exec'), namespace)
    original = namespace['_read_fd']
    def injected_read(descriptor, *, limit, label):
        if label == 'published pilot snapshot receipt':
            raise OSError(__import__('errno').EIO, 'injected transient receipt read failure')
        return original(descriptor, limit=limit, label=label)
    namespace['_read_fd'] = injected_read
    arguments = sys.argv[2:] if sys.argv[1:2] == ['-'] else sys.argv[1:]
    raise SystemExit(namespace['main'](arguments))
""")
        injected = "".join(injected_parts)
        python = fake_bin / "python3"
        python.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            f"{injected}"
            f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        python.chmod(0o755)
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
        "DOCKER_WORKTREE": str(worktree),
        "RUN_GATE_EVIDENCE_DIR": str(gate_evidence_root(worktree)),
        "TMPDIR": str(tmp_path),
        "REAL_GIT": shutil.which("git"),
        "REAL_TIMEOUT": shutil.which("timeout"),
        "GIT_STATUS_COUNTER": str(tmp_path / "git-status-count"),
        "GIT_STATUS_FAIL_AT": str(status_fail_at or 0),
        "GIT_STATUS_DIRTY_AT": str(status_dirty_at or 0),
        "GIT_STATUS_DELAY_AT": str(status_delay_at or 0),
        "GIT_STATUS_DELAY_SECONDS": str(status_delay_seconds),
        "TAMPER_HOST_CHECKER_AFTER_FINAL_STATUS": (
            "1" if tamper_host_checker_after_final_status else "0"
        ),
        "HOST_CHECKER_TAMPER_COUNT": str(tmp_path / "host-checker-tamper-count"),
        "ANALYSIS_HOST_CHECK_CALL_COUNT": str(tmp_path / "analysis-host-check-call-count"),
        "TIMEOUT_TRACE": str(tmp_path / "timeout.jsonl"),
        "FAST_DOCKER_TIMEOUT": "1" if fast_docker_timeout else "0",
    }
    if inner_pilot_log is not None:
        env["ANALYSIS_PILOT_INNER_LOG"] = str(inner_pilot_log)
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
        for name in (
            "bash", "git", "findmnt", "realpath", "flock", "chmod", "rm",
            "mkdir", "mv", "python3", "stat",
        ):
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
        ("b110-pilot", "pilot-manifest-omits", "exact artifact set"),
        ("b110-pilot", "pilot-artifact-tampered", "retained pilot artifact differs"),
        ("b110-pilot", "pilot-attempt-expired", "90-minute pilot attempt window is not active"),
        ("b110-pilot", "pilot-campaign-expired", "two-hour campaign deadline is not active"),
        ("analysis-r2-pilot", "analysis-pilot-incomplete", "complete exit status (6)"),
        ("analysis-r2-pilot", "analysis-pilot-b110-marker", "B110-mode marker"),
        ("analysis-r2-pilot", "analysis-pilot-unverified", "evidence-attestation digest"),
        ("analysis-r2-pilot", "analysis-pilot-duplicate-marker", "exactly one evidence-attestation digest"),
        ("analysis-r2-pilot", "analysis-pilot-premature-final-marker", "outer completion marker"),
        ("analysis-r2-pilot", "analysis-host-check-fails", "host rejected B131 pilot evidence"),
        ("analysis-r2-pilot", "analysis-host-check-dirties-evidence", "host rejected B131 pilot evidence during the final publication check"),
        ("analysis-r2-pilot", "analysis-host-check-dirties", "selected worktree became dirty during final B131 host verification"),
    ],
)
def test_b110_child_evidence_is_required_before_outer_completion(
    tmp_path: Path, committed_worktree: Path, lane: str, mode: str, expected_error: str
):
    proc, _calls, _elapsed = run_launcher(tmp_path, committed_worktree, lane=lane, mode=mode)

    assert proc.returncode != 0
    assert expected_error in proc.stderr
    assert "ASSAY_B110_GATE_COMPLETE" not in proc.stdout


def test_analysis_r2_pilot_emits_lane_specific_launch_markers(
    tmp_path: Path, committed_worktree: Path
):
    proc, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="analysis-r2-pilot",
    )

    assert proc.returncode == 0, proc.stderr
    assert "ASSAY_ANALYSIS_R2_PILOT_GATE_WAIT_TIMEOUT=2h15m" in proc.stdout
    assert "ASSAY_ANALYSIS_R2_PILOT_GATE_CONTAINER=run-gate-assay-analysis-r2-pilot-" in proc.stdout
    commit = subprocess.run(
        ["git", "-C", str(committed_worktree), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert (
        "ASSAY_ANALYSIS_R2_PILOT_DEADLINE_ARTIFACT="
        f"{committed_worktree}/assay/.assay/campaign-deadline-analysis-r2-pilot-{commit[:12]}.json"
    ) in proc.stdout
    assert "ASSAY_ANALYSIS_R2_PILOT_GATE_COMPLETE=analysis-r2-pilot" in proc.stdout
    assert "ASSAY_B105_GATE_WAIT_TIMEOUT=" not in proc.stdout


def test_analysis_pilot_checker_inner_shell_and_outer_launcher_accept_one_marker(
    tmp_path: Path, committed_worktree: Path
):
    checker_dir = tmp_path / "checker"
    checker_dir.mkdir()
    checker_status, checker_output, _paths = (
        _run_checker_cli_with_valid_evidence(checker_dir)
    )
    assert checker_status == 0
    attestation_sha256 = checker_output.strip().split("=", 1)[1]
    assert checker_output == f"ANALYSIS_R2_PILOT_VERIFIED={attestation_sha256}\n"

    inner_dir = tmp_path / "inner"
    inner_dir.mkdir()
    checker_output_path = inner_dir / "checker-output.txt"
    checker_output_path.write_text(checker_output, encoding="ascii")
    inner_proc = _run_analysis_r2_pilot_inner_harness(
        inner_dir, checker_output=checker_output_path
    )
    assert inner_proc.returncode == 0, inner_proc.stderr
    assert "STATUS=0" in inner_proc.stdout
    checker_marker = f"ANALYSIS_R2_PILOT_CHECKER_VERIFIED={attestation_sha256}"
    host_marker = f"ANALYSIS_R2_PILOT_HOST_EVIDENCE_VERIFIED={attestation_sha256}"
    assert inner_proc.stdout.count(checker_marker) == 1
    assert host_marker not in inner_proc.stdout
    assert "ANALYSIS_R2_PILOT_VERIFIED=1" not in inner_proc.stdout

    inner_log = tmp_path / "container-inner.log"
    inner_log.write_text(inner_proc.stdout, encoding="utf-8")
    outer_tmp = tmp_path / "outer"
    outer_tmp.mkdir()
    outer_proc, _calls, _elapsed = run_launcher(
        outer_tmp,
        committed_worktree,
        lane="analysis-r2-pilot",
        mode="analysis-pilot-inner-log",
        inner_pilot_log=inner_log,
    )
    assert outer_proc.returncode == 0, outer_proc.stderr
    assert outer_proc.stdout.count(checker_marker) == 1
    assert outer_proc.stdout.count(host_marker) == 1
    assert outer_proc.stdout.count("ANALYSIS_R2_PILOT_VERIFIED=1") == 1
    container_exit_position = outer_proc.stdout.index(
        "ASSAY_ANALYSIS_R2_PILOT_GATE_CONTAINER_EXIT=0"
    )
    host_marker_position = outer_proc.stdout.index(host_marker)
    outer_marker_position = outer_proc.stdout.index("ANALYSIS_R2_PILOT_VERIFIED=1")
    complete_position = outer_proc.stdout.index("ASSAY_ANALYSIS_R2_PILOT_GATE_COMPLETE=analysis-r2-pilot")
    # The checker marker originates in the container and may reach the live
    # log follower on either side of the docker-wait result. The host
    # attestation must be emitted only after the container has exited, and the
    # outer success marker must follow that host-side check.
    assert container_exit_position < host_marker_position < outer_marker_position < complete_position
    assert "ASSAY_ANALYSIS_R2_PILOT_GATE_COMPLETE=analysis-r2-pilot" in outer_proc.stdout


def test_analysis_r2_pilot_outer_marker_waits_for_final_source_check(
    tmp_path: Path, committed_worktree: Path
):
    proc, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="analysis-r2-pilot",
        status_dirty_at=2,
    )

    assert proc.returncode != 0
    assert f"ANALYSIS_R2_PILOT_CHECKER_VERIFIED={ANALYSIS_PILOT_ATTESTATION}" in proc.stdout
    assert "ANALYSIS_R2_PILOT_VERIFIED=1" not in proc.stdout
    assert "ASSAY_ANALYSIS_R2_PILOT_GATE_COMPLETE=analysis-r2-pilot" not in proc.stdout
    assert "selected worktree became dirty during B105 qualification" in proc.stderr


def test_pilot_host_publishes_snapshot_and_returns_its_digest(
    tmp_path: Path, committed_worktree: Path
):
    proc, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )

    assert proc.returncode == 0, proc.stderr
    assert "ASSAY_B110_GATE_COMPLETE=b110-pilot" in proc.stdout
    assert re.search(r"^B110_PILOT_SNAPSHOT_SHA256=[0-9a-f]{64}$", proc.stdout, re.M)
    assert re.search(r"^B110_PILOT_HOST_VERIFIED_SHA256=[0-9a-f]{64}$", proc.stdout, re.M)
    snapshot = committed_worktree / "assay" / ".assay" / "b110-pilot-evidence"
    assert (snapshot / "snapshot.sha256").is_file()
    assert (snapshot.parent / "b110-pilot-evidence.receipt.json").is_file()


def test_pilot_final_host_check_runs_after_markers_and_controls_gate_status(
    tmp_path: Path, committed_worktree: Path
):
    proc, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
        host_verify_fails=True,
    )

    assert proc.returncode != 0
    assert "final host verification rejected" in proc.stderr
    assert "B110_PILOT_COMPLETED=1" in proc.stdout
    assert "B110_PILOT_HOST_VERIFIED_SHA256=" not in proc.stdout
    # Marker text is provisional; the nonzero process result makes this gate
    # fail even though the marker was already written before the final check.
    assert "ASSAY_B110_GATE_COMPLETE=b110-pilot" in proc.stdout

    script = SCRIPT.read_text(encoding="utf-8")
    pilot_complete = script.index("printf 'B110_PILOT_COMPLETED=1\\n'")
    final_check = script.index("--verify-published-snapshot", pilot_complete)
    gate_complete = script.index("printf 'ASSAY_B110_GATE_COMPLETE=%s\\n'", pilot_complete)
    final_status = script.index('final_status="$(assay_git -C "$worktree" status --porcelain --untracked-files=all)"', pilot_complete)
    assert pilot_complete < gate_complete < final_status < final_check
    assert "B110_PILOT_HOST_VERIFIED_SHA256=%s" not in script
    host_checker = PILOT_HOST_CHECK.read_text(encoding="utf-8")
    verify_call = host_checker.index("verified_snapshot_sha256 = verify_published_snapshot(args)")
    host_verified = host_checker.index("print(f\"B110_PILOT_HOST_VERIFIED_SHA256={verified_snapshot_sha256}\")")
    assert verify_call < host_verified


def test_pilot_final_host_check_rejects_expiry_during_post_publication_git_check(
    tmp_path: Path, committed_worktree: Path
):
    proc, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
        mode="pilot-attempt-near-expired",
        status_delay_at=5,
        status_delay_seconds=7,
    )

    assert proc.returncode != 0
    assert "final host verification rejected" in proc.stderr
    assert "B110_PILOT_HOST_VERIFIED_SHA256=" not in proc.stdout
    assert (tmp_path / "git-status-count").read_text(encoding="ascii") == "5"
    assert "B110_PILOT_SNAPSHOT_SHA256=" in proc.stdout
    attempt_window = json.loads(
        (
            committed_worktree
            / "assay"
            / ".assay"
            / "b110-pilot-evidence"
            / "b110-pilot-attempt-window.json"
        ).read_text(encoding="utf-8")
    )
    assert attempt_window["expires_at_epoch_ns"] <= time.time_ns()


def test_host_verifier_detects_checkout_tamper_during_snapshot_publication(
    tmp_path: Path, committed_worktree: Path
):
    proc, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
        tamper_host_checker_after_final_status=True,
    )

    assert proc.returncode != 0
    assert "selected worktree became dirty during final B110 host verification" in proc.stderr
    assert "ASSAY_B110_GATE_COMPLETE=b110-pilot" not in proc.stdout
    assert "B110_PILOT_HOST_VERIFIED_SHA256=" not in proc.stdout
    assert (tmp_path / "host-checker-tamper-count").read_text(encoding="ascii") == "1"


def test_pilot_retry_archives_prior_snapshot_before_publishing_new_one(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )

    assert first.returncode == 0, first.stderr
    record_run_gate_success(committed_worktree)
    prior_snapshot = project / ".assay" / "b110-pilot-evidence"
    prior_index = (prior_snapshot / "snapshot.sha256").read_bytes()
    receipt_path = project / ".assay" / "b110-pilot-evidence.receipt.json"
    prior_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected_digest = prior_receipt["snapshot_sha256"]

    retry_root = tmp_path / "retry"
    retry_root.mkdir()
    proc, _calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert proc.returncode == 0, proc.stderr
    archive_root = project / ".assay" / "b110-pilot-evidence-archive"
    archives = list(archive_root.glob(f"*-{expected_digest}"))
    assert len(archives) == 1
    archived_snapshot = archives[0] / "b110-pilot-evidence"
    assert (archived_snapshot / "snapshot.sha256").read_bytes() == prior_index
    archived_receipt = archives[0] / "b110-pilot-evidence.receipt.json"
    assert json.loads(archived_receipt.read_text(encoding="utf-8")) == prior_receipt
    archived_attestation = archives[0] / "b110-pilot-evidence.attestation.json"
    attestation = json.loads(archived_attestation.read_text(encoding="utf-8"))
    assert attestation["schema"] == "assay-b110-pilot-run-gate-attestation/2"
    assert attestation["snapshot_sha256"] == expected_digest
    assert attestation["archive_transcript_path"] == "b110-pilot-run-gate.log"
    archived_transcript = archives[0] / attestation["archive_transcript_path"]
    assert hashlib.sha256(archived_transcript.read_bytes()).hexdigest() == attestation["transcript_sha256"]
    assert (project / ".assay" / "b110-pilot-evidence" / "snapshot.sha256").is_file()


def test_pilot_retry_archives_prior_success_before_early_status_failure(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    state = project / ".assay"
    expected_digest = receipt["snapshot_sha256"]

    failed_retry_root = tmp_path / "retry-with-early-status-failure"
    failed_retry_root.mkdir()
    failed_retry, calls, _elapsed = run_launcher(
        failed_retry_root,
        committed_worktree,
        lane="b110-pilot",
        status_fail_at=1,
    )

    assert failed_retry.returncode != 0
    assert "cannot read selected worktree status before B105 qualification" in failed_retry.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    archive_root = state / "b110-pilot-evidence-archive"
    archived = list(archive_root.glob(f"*-{expected_digest}"))
    assert len(archived) == 1
    archived_attestation = json.loads(
        (archived[0] / "b110-pilot-evidence.attestation.json").read_text(encoding="utf-8")
    )
    assert archived_attestation["snapshot_sha256"] == expected_digest

    # A completed same-commit failure may replace run-gate's pass entry only
    # after the prior snapshot has already been safely archived.
    record_run_gate_failure_replacing_success(committed_worktree, receipt)
    recover_root = tmp_path / "retry-after-early-status-failure"
    recover_root.mkdir()
    recovered, recovery_calls, _elapsed = run_launcher(
        recover_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert recovered.returncode == 0, recovered.stderr
    assert any(call and call[0] == "run" and "-d" in call for call in recovery_calls)
    assert list(archive_root.glob(f"*-{expected_digest}")) == archived


def test_prearchive_host_refusal_is_ineligible_and_keeps_pass_for_next_retry(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    state = project / ".assay"
    original_receipt = (state / "b110-pilot-evidence.receipt.json").read_bytes()

    failed_retry_root = tmp_path / "retry-with-lock-mode-prerequisite-failure"
    failed_retry_root.mkdir()
    failed_retry, calls, _elapsed = run_launcher(
        failed_retry_root,
        committed_worktree,
        lane="b110-pilot",
        fail_chmod=True,
    )

    assert failed_retry.returncode == 3
    assert "cannot restrict the B105 host lock permissions" in failed_retry.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    assert (state / "b110-pilot-evidence.receipt.json").read_bytes() == original_receipt
    assert not (state / "b110-pilot-evidence.attestation.json").exists()

    # run-gate maps exit 3 to ERROR: latest advances, but the eligible prior
    # pass remains in history for the next attempt to bind and archive.
    record_run_gate_error_preserving_success(committed_worktree, receipt)
    retry_root = tmp_path / "retry-after-ineligible-host-refusal"
    retry_root.mkdir()
    retry, retry_calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 0, retry.stderr
    assert any(call and call[0] == "run" and "-d" in call for call in retry_calls)
    archive_root = state / "b110-pilot-evidence-archive"
    assert len(list(archive_root.glob(f"*-{receipt['snapshot_sha256']}"))) == 1


def test_pilot_retry_archives_with_unresolved_ineligible_latest_history(
    tmp_path: Path, committed_worktree: Path
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    history_path = project / ".run-gate" / "history.json"
    store = json.loads(history_path.read_text(encoding="utf-8"))
    prior_pass = store["lanes"]["b110-pilot"]["history"][0]
    store["lanes"]["b110-pilot"]["latest"] = {
        "run_id": "d" * 32,
        "lane": "b110-pilot",
        "commit": None,
        "outcome": "not_run",
        "exit_code": None,
        "started_at": "2026-10-09T00:01:00Z",
        "duration_seconds": None,
        "worktree": str(committed_worktree),
        "history_eligible": False,
        "excluded_reason": "HEAD did not resolve to a commit",
    }
    store["lanes"]["b110-pilot"]["history"] = [prior_pass]
    history_path.write_text(json.dumps(store), encoding="utf-8")

    retry_root = tmp_path / "retry-with-unresolved-latest-commit"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 0, retry.stderr
    assert any(call and call[0] == "run" and "-d" in call for call in calls)
    archive_root = project / ".assay" / "b110-pilot-evidence-archive"
    assert len(list(archive_root.glob(f"*-{receipt['snapshot_sha256']}"))) == 1


def test_orphan_run_gate_attestation_is_quarantined_before_new_pilot(
    tmp_path: Path, committed_worktree: Path
):
    state = committed_worktree / "assay" / ".assay"
    state.mkdir()
    orphan = state / "b110-pilot-evidence.attestation.json"
    orphan.write_text("malformed orphan\n", encoding="ascii")
    orphan.chmod(0o400)

    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )

    assert proc.returncode == 0, proc.stderr
    assert not orphan.exists()
    quarantined = list(
        (state / "b110-pilot-evidence-incomplete").glob(
            "*/b110-pilot-evidence.attestation.json"
        )
    )
    assert len(quarantined) == 1
    assert any(call and call[0] == "run" and "-d" in call for call in calls)


@pytest.mark.parametrize("unsafe_incomplete", ["symlink", "public-directory"])
def test_pilot_retry_quarantines_to_fallback_when_incomplete_path_is_unsafe(
    tmp_path: Path, committed_worktree: Path, unsafe_incomplete: str
):
    state = committed_worktree / "assay" / ".assay"
    state.mkdir()
    orphan = state / "b110-pilot-evidence.attestation.json"
    orphan.write_text("malformed orphan\n", encoding="ascii")
    orphan.chmod(0o400)
    incomplete = state / "b110-pilot-evidence-incomplete"
    if unsafe_incomplete == "symlink":
        external = tmp_path / "external-incomplete"
        external.mkdir(mode=0o700)
        incomplete.symlink_to(external, target_is_directory=True)
    else:
        incomplete.mkdir(mode=0o755)
        incomplete.chmod(0o755)

    proc, calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )

    assert proc.returncode == 0, proc.stderr
    assert any(call and call[0] == "run" and "-d" in call for call in calls)
    assert not orphan.exists()
    fallback_root = state / "b110-pilot-evidence-unverified"
    quarantined = list(fallback_root.glob("*/b110-pilot-evidence.attestation.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="ascii") == "malformed orphan\n"
    if unsafe_incomplete == "symlink":
        assert incomplete.is_symlink()
        assert not list((tmp_path / "external-incomplete").iterdir())
    else:
        assert stat.S_IMODE(incomplete.stat().st_mode) == 0o755


def test_pilot_archive_pair_is_not_published_when_second_move_fails(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(tmp_path, committed_worktree, lane="b110-pilot")
    assert first.returncode == 0, first.stderr
    record_run_gate_success(committed_worktree)
    state = project / ".assay"

    fail_root = tmp_path / "retry-with-archive-failure"
    fail_root.mkdir()
    failed, calls, _elapsed = run_launcher(
        fail_root,
        committed_worktree,
        lane="b110-pilot",
        host_archive_receipt_move_fails=True,
    )

    assert failed.returncode != 0
    assert "injected archive receipt move failure" in failed.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    archive_root = state / "b110-pilot-evidence-archive"
    assert archive_root.is_dir()
    assert not list(archive_root.iterdir())
    incomplete_root = state / "b110-pilot-evidence-incomplete"
    stages = list(incomplete_root.glob("*/b110-pilot-evidence"))
    assert len(stages) == 1
    assert (stages[0] / "snapshot.sha256").is_file()
    assert not (stages[0].parent / "b110-pilot-evidence.receipt.json").exists()
    assert (state / "b110-pilot-evidence.receipt.json").is_file()

    recover_root = tmp_path / "retry-after-archive-failure"
    recover_root.mkdir()
    recovered, _recovery_calls, _elapsed = run_launcher(
        recover_root,
        committed_worktree,
        lane="b110-pilot",
    )
    assert recovered.returncode == 0, recovered.stderr
    assert not list(archive_root.iterdir())
    assert len(list(incomplete_root.glob("*/b110-pilot-evidence"))) == 1


def test_pilot_retry_refuses_public_archive_and_survives_history_replacement(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(tmp_path, committed_worktree, lane="b110-pilot")
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    state = project / ".assay"
    archive_root = state / "b110-pilot-evidence-archive"
    archive_root.mkdir(mode=0o700)
    archive_root.chmod(0o777)
    original_receipt = (state / "b110-pilot-evidence.receipt.json").read_bytes()
    original_index = (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes()

    retry_root = tmp_path / "retry-with-public-archive-dir"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode != 0
    assert "archive directory is not private and writable" in retry.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    assert (state / "b110-pilot-evidence.receipt.json").read_bytes() == original_receipt
    assert (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes() == original_index
    attestation_path = state / "b110-pilot-evidence.attestation.json"
    attestation_raw = attestation_path.read_bytes()
    attestation = json.loads(attestation_raw)
    assert attestation["run_id"] == "b" * 32
    assert attestation["snapshot_sha256"] == json.loads(original_receipt)["snapshot_sha256"]
    assert stat.S_IMODE(archive_root.stat().st_mode) == 0o777

    # A completed same-commit failure replaces run-gate's previous pass slot.
    # The durable attestation still proves that the retained snapshot passed.
    record_run_gate_failure_replacing_success(committed_worktree, receipt)
    archive_root.chmod(0o700)
    recover_root = tmp_path / "retry-after-history-replacement"
    recover_root.mkdir()
    recovered, recovery_calls, _elapsed = run_launcher(
        recover_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert recovered.returncode == 0, recovered.stderr
    assert any(call and call[0] == "run" and "-d" in call for call in recovery_calls)
    archived = list(
        archive_root.glob(f"*-{json.loads(original_receipt)['snapshot_sha256']}")
    )
    assert len(archived) == 1
    assert (
        archived[0] / "b110-pilot-evidence.attestation.json"
    ).read_bytes() == attestation_raw
    assert not list((state / "b110-pilot-evidence-incomplete").glob("*/b110-pilot-evidence"))


def test_pilot_retry_preserves_snapshot_when_durable_transcript_binding_changes(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(tmp_path, committed_worktree, lane="b110-pilot")
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    state = project / ".assay"
    archive_root = state / "b110-pilot-evidence-archive"
    archive_root.mkdir(mode=0o700)
    archive_root.chmod(0o777)

    failed_root = tmp_path / "retry-with-public-archive-dir"
    failed_root.mkdir()
    failed, _calls, _elapsed = run_launcher(
        failed_root,
        committed_worktree,
        lane="b110-pilot",
    )
    assert failed.returncode != 0
    attestation_path = state / "b110-pilot-evidence.attestation.json"
    attestation_path.chmod(0o600)
    changed_attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    changed_attestation["transcript_sha256"] = "0" * 64
    attestation_path.write_text(json.dumps(changed_attestation) + "\n", encoding="utf-8")
    attestation_path.chmod(0o400)
    record_run_gate_failure_replacing_success(committed_worktree, receipt)
    archive_root.chmod(0o700)

    retry_root = tmp_path / "retry-with-changed-attestation"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 3
    assert "durable successful run-gate transcript changed" in retry.stderr
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    assert (state / "b110-pilot-evidence.receipt.json").is_file()
    assert (state / "b110-pilot-evidence" / "snapshot.sha256").is_file()
    assert attestation_path.read_text(encoding="utf-8") == json.dumps(changed_attestation) + "\n"
    assert not (state / "b110-pilot-evidence-incomplete").exists()


def test_pilot_retry_preserves_snapshot_when_transcript_lacks_post_check_marker(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    record_run_gate_success(committed_worktree, include_host_verified_marker=False)
    state = project / ".assay"
    original_receipt = (state / "b110-pilot-evidence.receipt.json").read_bytes()
    original_index = (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes()

    retry_root = tmp_path / "retry-with-forged-history"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 3
    assert "final host-verification marker" in retry.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=prior B110 pilot evidence is unavailable" in retry.stdout
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    assert (state / "b110-pilot-evidence.receipt.json").read_bytes() == original_receipt
    assert (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes() == original_index
    assert not (state / "b110-pilot-evidence-incomplete").exists()
    assert not (state / "b110-pilot-evidence-archive").exists()


def test_pilot_retry_preserves_snapshot_when_run_gate_store_is_corrupt(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    record_run_gate_success(committed_worktree)
    state = project / ".assay"
    original_receipt = (state / "b110-pilot-evidence.receipt.json").read_bytes()
    original_index = (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes()
    (project / ".run-gate" / "history.json").write_text("{", encoding="ascii")

    retry_root = tmp_path / "retry-with-corrupt-history"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 3
    assert "run-gate history store is unavailable" in retry.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=prior B110 pilot evidence is unavailable" in retry.stdout
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    assert (state / "b110-pilot-evidence.receipt.json").read_bytes() == original_receipt
    assert (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes() == original_index
    assert not (state / "b110-pilot-evidence-incomplete").exists()
    assert not (state / "b110-pilot-evidence-archive").exists()


@pytest.mark.parametrize(
    ("latest_outcome", "latest_exit_code"),
    [("fail", 1), ("error", None)],
)
def test_failed_first_publication_is_quarantined_and_retry_can_proceed(
    tmp_path: Path,
    committed_worktree: Path,
    latest_outcome: str,
    latest_exit_code: int | None,
):
    project = committed_worktree / "assay"
    state = project / ".assay"
    failed_first_root = tmp_path / "failed-first-publication"
    failed_first_root.mkdir()
    failed_first, _calls, _elapsed = run_launcher(
        failed_first_root,
        committed_worktree,
        lane="b110-pilot",
        host_verify_fails=True,
    )

    assert failed_first.returncode != 0
    assert "final host verification rejected" in failed_first.stderr
    assert (state / "b110-pilot-evidence").is_dir()
    assert (state / "b110-pilot-evidence.receipt.json").is_file()

    receipt = json.loads((state / "b110-pilot-evidence.receipt.json").read_text(encoding="utf-8"))
    evidence_root = gate_evidence_root(committed_worktree)
    lane_dir = evidence_root / "lanes" / "b110-pilot"
    lane_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(evidence_root, 0o755)
    os.chmod(evidence_root / "lanes", 0o755)
    os.chmod(lane_dir, 0o700)
    history_dir = project / ".run-gate"
    history_dir.mkdir(mode=0o700, exist_ok=True)
    failed_record = {
        "run_id": "c" * 32,
        "lane": "b110-pilot",
        "commit": receipt["commit"],
        "outcome": latest_outcome,
        "exit_code": latest_exit_code,
        "started_at": "2026-10-09T00:00:00Z",
        "duration_seconds": 1.0,
        "worktree": str(committed_worktree),
        "history_eligible": False,
        "log_path": str(gate_evidence_root(committed_worktree) / "lanes" / "b110-pilot" / ("c" * 32 + ".log")),
    }
    (history_dir / "history.json").write_text(
        json.dumps({"schema": 2, "lanes": {"b110-pilot": {"latest": failed_record, "history": []}}}),
        encoding="utf-8",
    )
    # With no eligible success in valid history, an absent transcript root is
    # irrelevant: the failed first publication is quarantined and retried.
    shutil.rmtree(evidence_root)

    retry_root = tmp_path / "retry-after-failed-first-publication"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 0, retry.stderr
    assert any(call and call[0] == "run" and "-d" in call for call in calls)
    incomplete_root = state / "b110-pilot-evidence-incomplete"
    quarantined = list(incomplete_root.glob("*/b110-pilot-evidence"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "snapshot.sha256").is_file()
    assert (quarantined[0].parent / "b110-pilot-evidence.receipt.json").is_file()


@pytest.mark.parametrize(
    "malformed_history",
    [
        "null-entry",
        "pass-string-exit-code",
        "pass-nonzero-exit-code",
        "latest-error-string-exit-code",
        "array-outcome",
        "null-lane-slot",
    ],
)
def test_pilot_retry_preserves_snapshot_when_history_is_malformed(
    tmp_path: Path, committed_worktree: Path, malformed_history: str
):
    project = committed_worktree / "assay"
    state = project / ".assay"
    failed_first_root = tmp_path / "malformed-history-first-publication"
    failed_first_root.mkdir()
    failed_first, _calls, _elapsed = run_launcher(
        failed_first_root,
        committed_worktree,
        lane="b110-pilot",
        host_verify_fails=True,
    )
    assert failed_first.returncode != 0
    assert (state / "b110-pilot-evidence").is_dir()
    assert (state / "b110-pilot-evidence.receipt.json").is_file()
    receipt = json.loads((state / "b110-pilot-evidence.receipt.json").read_text(encoding="utf-8"))

    history_dir = project / ".run-gate"
    history_dir.mkdir(mode=0o700, exist_ok=True)
    failed_record = {
        "run_id": "d" * 32,
        "lane": "b110-pilot",
        "commit": receipt["commit"],
        "outcome": "fail",
        "exit_code": 1,
        "started_at": "2026-10-09T00:00:00Z",
        "duration_seconds": 1.0,
        "worktree": str(committed_worktree),
        "history_eligible": False,
        "log_path": str(
            gate_evidence_root(committed_worktree)
            / "lanes"
            / "b110-pilot"
            / ("d" * 32 + ".log")
        ),
    }
    latest_record = failed_record
    if malformed_history == "null-lane-slot":
        lanes = {"b110-pilot": None}
    elif malformed_history == "latest-error-string-exit-code":
        latest_record = {
            **failed_record,
            "run_id": "a" * 32,
            "outcome": "error",
            "exit_code": "3",
            "duration_seconds": None,
        }
        lanes = {"b110-pilot": {"latest": latest_record, "history": []}}
    else:
        history_entries = [None]
        if malformed_history == "pass-string-exit-code":
            history_entries = [{
                **failed_record,
                "run_id": "e" * 32,
                "outcome": "pass",
                "exit_code": "0",
                "history_eligible": True,
            }]
        elif malformed_history == "pass-nonzero-exit-code":
            history_entries = [{
                **failed_record,
                "run_id": "e" * 32,
                "outcome": "pass",
                "exit_code": 1,
                "history_eligible": True,
            }]
        elif malformed_history == "array-outcome":
            history_entries = [{
                **failed_record,
                "run_id": "f" * 32,
                "outcome": [],
                "history_eligible": True,
            }]
        lanes = {"b110-pilot": {"latest": failed_record, "history": history_entries}}
    (history_dir / "history.json").write_text(
        json.dumps({"schema": 2, "lanes": lanes}),
        encoding="utf-8",
    )
    original_receipt = (state / "b110-pilot-evidence.receipt.json").read_bytes()
    original_index = (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes()

    retry_root = tmp_path / "retry-with-malformed-nested-history"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 3
    assert "run-gate B110 pilot" in retry.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=prior B110 pilot evidence is unavailable" in retry.stdout
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    assert (state / "b110-pilot-evidence.receipt.json").read_bytes() == original_receipt
    assert (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes() == original_index
    assert not (state / "b110-pilot-evidence-incomplete").exists()
    assert not (state / "b110-pilot-evidence-archive").exists()


def test_pilot_attestation_uses_the_preflighted_history_snapshot(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    history_path = project / ".run-gate" / "history.json"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_history_snapshot_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_preflight = module._preflight_history_store

    def replace_store_after_preflight(path: Path):
        snapshot = original_preflight(path)
        history_path.write_text("{", encoding="ascii")
        return snapshot

    monkeypatch.setattr(module, "_preflight_history_store", replace_store_after_preflight)
    attestation, transcript_raw = module._has_successful_run_gate_attestation(
        project,
        receipt,
        committed_worktree,
    )
    assert attestation["transcript_sha256"] == hashlib.sha256(transcript_raw).hexdigest()


def test_persisted_attestation_rechecks_transcript_path_after_validation(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_transcript_path_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    attestation, _transcript_raw = module._has_successful_run_gate_attestation(
        project,
        receipt,
        committed_worktree,
    )
    log_path = Path(attestation["log_path"])
    original_marker = module._transcript_marker
    replaced = False

    def replace_after_final_marker(raw: bytes, name: bytes, pattern: re.Pattern[str]):
        nonlocal replaced
        value = original_marker(raw, name, pattern)
        if name == b"B110_PILOT_HOST_VERIFIED_SHA256" and not replaced:
            replacement = log_path.with_name(f".{log_path.name}.replacement")
            replacement.write_bytes(log_path.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, log_path)
            replaced = True
        return value

    monkeypatch.setattr(module, "_transcript_marker", replace_after_final_marker)
    with pytest.raises(ValueError, match="path changed during host verification"):
        module._verify_persisted_run_gate_attestation(
            receipt,
            attestation,
            committed_worktree,
        )
    assert replaced


def test_persisted_attestation_rejects_in_place_transcript_write_during_validation(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_transcript_in_place_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    attestation, _transcript_raw = module._has_successful_run_gate_attestation(
        project,
        receipt,
        committed_worktree,
    )
    log_path = Path(attestation["log_path"])
    original_marker = module._transcript_marker
    replaced = False

    def overwrite_after_final_marker(raw: bytes, name: bytes, pattern: re.Pattern[str]):
        nonlocal replaced
        value = original_marker(raw, name, pattern)
        if name == b"B110_PILOT_HOST_VERIFIED_SHA256" and not replaced:
            log_path.write_text("in-place replacement after transcript read\n", encoding="ascii")
            log_path.chmod(0o600)
            replaced = True
        return value

    monkeypatch.setattr(module, "_transcript_marker", overwrite_after_final_marker)
    with pytest.raises(module.HistoryUnavailableError, match="changed during host verification"):
        module._verify_persisted_run_gate_attestation(
            receipt,
            attestation,
            committed_worktree,
        )
    assert replaced


def test_success_history_attestation_rejects_in_place_transcript_write_during_validation(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_history_in_place_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    log_path = gate_evidence_root(committed_worktree) / "lanes" / "b110-pilot" / ("b" * 32 + ".log")
    original_marker = module._transcript_marker
    replaced = False

    def overwrite_after_final_marker(raw: bytes, name: bytes, pattern: re.Pattern[str]):
        nonlocal replaced
        value = original_marker(raw, name, pattern)
        if name == b"B110_PILOT_HOST_VERIFIED_SHA256" and not replaced:
            log_path.write_text("in-place replacement after transcript read\n", encoding="ascii")
            log_path.chmod(0o600)
            replaced = True
        return value

    monkeypatch.setattr(module, "_transcript_marker", overwrite_after_final_marker)
    with pytest.raises(module.HistoryUnavailableError, match="changed during host verification"):
        module._has_successful_run_gate_attestation(
            project,
            receipt,
            committed_worktree,
        )
    assert replaced


def test_archive_rechecks_transcript_path_before_atomic_publish(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_path_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    attestation, transcript_raw = module._has_successful_run_gate_attestation(
        project,
        receipt,
        committed_worktree,
    )
    assay_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        module._write_snapshot_receipt_named(
            assay_fd,
            module._SNAPSHOT_ATTESTATION,
            module._snapshot_attestation_bytes(attestation),
        )
    finally:
        os.close(assay_fd)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    log_path = Path(attestation["log_path"])
    replaced = False
    original_rename = module._rename_noreplace

    def replace_source_at_archive_commit(source, destination, *args, **kwargs):
        nonlocal replaced
        if (
            isinstance(source, str)
            and source.startswith("archive-stage-")
            and isinstance(destination, str)
            and not replaced
        ):
            replacement = log_path.with_name(f".{log_path.name}.replacement")
            replacement.write_bytes(b"replacement at archive rename boundary\n")
            replacement.chmod(0o600)
            os.replace(replacement, log_path)
            replaced = True
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", replace_source_at_archive_commit)
    assert module.archive_prior_snapshot(args)[0] == "archived"

    assert replaced
    archive_root = state / "b110-pilot-evidence-archive"
    assert archive_root.is_dir()
    archives = list(archive_root.iterdir())
    assert len(archives) == 1
    archived_attestation = json.loads(
        (archives[0] / "b110-pilot-evidence.attestation.json").read_text(encoding="utf-8")
    )
    archived_transcript = archives[0] / "b110-pilot-run-gate.log"
    assert archived_transcript.read_bytes() == transcript_raw
    assert hashlib.sha256(archived_transcript.read_bytes()).hexdigest() == archived_attestation["transcript_sha256"]
    assert archived_attestation["archive_transcript_path"] == archived_transcript.name


def test_archive_withdraws_replaced_staging_directory_at_atomic_publish(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_stage_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    original_rename = module.os.rename
    original_publish = module._rename_noreplace
    replaced = False
    collision_injected = False
    held_stage_name: str | None = None
    published_archive_name: str | None = None
    occupied_quarantine_name: str | None = None
    occupied_quarantine_identity: tuple[int, int] | None = None

    def replace_stage_at_archive_commit(source, destination, *args, **kwargs):
        nonlocal held_stage_name, published_archive_name, occupied_quarantine_name
        nonlocal occupied_quarantine_identity
        nonlocal replaced, collision_injected
        if (
            isinstance(source, str)
            and source.startswith("archive-stage-")
            and isinstance(destination, str)
            and not replaced
        ):
            stage_parent_fd = kwargs["src_dir_fd"]
            published_archive_name = destination
            held_stage_name = f".held-{source}"
            original_rename(
                source,
                held_stage_name,
                src_dir_fd=stage_parent_fd,
                dst_dir_fd=stage_parent_fd,
            )
            os.mkdir(source, 0o700, dir_fd=stage_parent_fd)
            replaced = True
            return original_publish(source, destination, *args, **kwargs)
        if (
            source == published_archive_name
            and isinstance(destination, str)
            and destination.startswith("unverified-archive-entry-")
            and not collision_injected
        ):
            occupied_quarantine_name = destination
            incomplete_fd = kwargs["dst_dir_fd"]
            os.mkdir(destination, 0o700, dir_fd=incomplete_fd)
            occupied_info = os.stat(destination, dir_fd=incomplete_fd, follow_symlinks=False)
            occupied_quarantine_identity = (occupied_info.st_dev, occupied_info.st_ino)
            collision_injected = True
        return original_publish(source, destination, *args, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", replace_stage_at_archive_commit)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert replaced and collision_injected and held_stage_name is not None
    assert occupied_quarantine_name is not None and occupied_quarantine_identity is not None
    archive_root = state / "b110-pilot-evidence-archive"
    assert archive_root.is_dir()
    assert list(archive_root.iterdir()) == []
    incomplete_root = state / "b110-pilot-evidence-incomplete"
    quarantined_entries = list(incomplete_root.glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 2
    occupied_entry = incomplete_root / occupied_quarantine_name
    occupied_info = occupied_entry.stat()
    assert (occupied_info.st_dev, occupied_info.st_ino) == occupied_quarantine_identity
    assert list(occupied_entry.iterdir()) == []
    withdrawn_entry = next(entry for entry in quarantined_entries if entry != occupied_entry)
    assert withdrawn_entry.is_dir()
    held_stage = incomplete_root / held_stage_name
    assert (held_stage / "b110-pilot-evidence").is_dir()
    assert (held_stage / "b110-pilot-evidence.receipt.json").is_file()
    assert (held_stage / "b110-pilot-evidence.attestation.json").is_file()
    assert (held_stage / "b110-pilot-run-gate.log").is_file()


@pytest.mark.parametrize("source_exists", [True, False])
def test_archive_withdraw_reopens_missing_destination_and_checks_source_path(
    tmp_path: Path,
    source_exists: bool,
):
    assay_dir = tmp_path / ".assay"
    archive_root = assay_dir / "b110-pilot-evidence-archive"
    incomplete_root = assay_dir / "b110-pilot-evidence-incomplete"
    archive_root.mkdir(parents=True, mode=0o700)
    incomplete_root.mkdir(mode=0o700)
    archive_name = "candidate"
    if source_exists:
        archived_entry = archive_root / archive_name
        archived_entry.mkdir()
        (archived_entry / "payload").write_bytes(b"unverified\n")

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_withdraw_destination_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    archive_fd = os.open(archive_root, os.O_RDONLY | os.O_DIRECTORY)
    incomplete_fd = os.open(incomplete_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        if source_exists:
            # Keep the old descriptor alive while removing its path, forcing
            # renameat2 to report ENOENT for the destination parent.
            os.rmdir("b110-pilot-evidence-incomplete", dir_fd=assay_fd)
        result = module._withdraw_unverified_archive_entry(
            archive_fd,
            incomplete_fd,
            archive_name,
            assay_fd,
        )
        if source_exists:
            assert result is not None
            assert not (archive_root / archive_name).exists()
            assert (incomplete_root / result / "payload").read_bytes() == b"unverified\n"
        else:
            assert result is None
            assert not (archive_root / archive_name).exists()
            assert list(incomplete_root.iterdir()) == []
    finally:
        os.close(incomplete_fd)
        os.close(archive_fd)
        os.close(assay_fd)


def test_archive_withdraw_refuses_to_report_a_renamed_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assay_dir = tmp_path / ".assay"
    archive_root = assay_dir / "b110-pilot-evidence-archive"
    incomplete_root = assay_dir / "b110-pilot-evidence-incomplete"
    archive_root.mkdir(parents=True, mode=0o700)
    incomplete_root.mkdir(mode=0o700)
    archived_entry = archive_root / "candidate"
    archived_entry.mkdir()
    (archived_entry / "payload").write_bytes(b"unverified\n")

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_withdraw_renamed_destination_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    archive_fd = os.open(archive_root, os.O_RDONLY | os.O_DIRECTORY)
    incomplete_fd = os.open(incomplete_root, os.O_RDONLY | os.O_DIRECTORY)
    original_rename = module._rename_noreplace
    renamed = False

    def rename_root_during_withdrawal(source, destination, **kwargs):
        nonlocal renamed
        if (
            source == "candidate"
            and isinstance(destination, str)
            and destination.startswith("unverified-archive-entry-")
            and not renamed
        ):
            os.rename(
                module._SNAPSHOT_INCOMPLETE,
                "held-incomplete",
                src_dir_fd=assay_fd,
                dst_dir_fd=assay_fd,
            )
            renamed = True
        return original_rename(source, destination, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", rename_root_during_withdrawal)
    try:
        with pytest.raises(ValueError, match="directory path could not be checked|directory path changed"):
            module._withdraw_unverified_archive_entry(
                archive_fd,
                incomplete_fd,
                "candidate",
                assay_fd,
            )
        assert renamed
        assert not (archive_root / "candidate").exists()
        quarantined = list((assay_dir / "held-incomplete").glob("unverified-archive-entry-*"))
        assert len(quarantined) == 1
        assert (quarantined[0] / "payload").read_bytes() == b"unverified\n"
        assert not incomplete_root.exists()
    finally:
        os.close(incomplete_fd)
        os.close(archive_fd)
        os.close(assay_fd)


def test_archive_withdraw_falls_back_when_incomplete_directory_is_replaced_unsafe(
    tmp_path: Path,
):
    assay_dir = tmp_path / ".assay"
    archive_root = assay_dir / "b110-pilot-evidence-archive"
    incomplete_root = assay_dir / "b110-pilot-evidence-incomplete"
    archive_root.mkdir(parents=True, mode=0o700)
    incomplete_root.mkdir(mode=0o700)
    archived_entry = archive_root / "candidate"
    archived_entry.mkdir()
    (archived_entry / "payload").write_bytes(b"unverified\n")

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_withdraw_fallback_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    archive_fd = os.open(archive_root, os.O_RDONLY | os.O_DIRECTORY)
    incomplete_fd = os.open(incomplete_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.rmdir("b110-pilot-evidence-incomplete", dir_fd=assay_fd)
        os.mkdir("b110-pilot-evidence-incomplete", 0o755, dir_fd=assay_fd)
        os.chmod(
            "b110-pilot-evidence-incomplete",
            0o755,
            dir_fd=assay_fd,
            follow_symlinks=False,
        )
        result = module._withdraw_unverified_archive_entry(
            archive_fd,
            incomplete_fd,
            "candidate",
            assay_fd,
        )
        assert result is not None
        fallback_name, quarantined_name = result.split("/", 1)
        assert fallback_name == module._SNAPSHOT_UNVERIFIED_FALLBACK
        fallback_root = assay_dir / fallback_name
        assert stat.S_IMODE(fallback_root.stat().st_mode) == 0o700
        assert (fallback_root / quarantined_name / "payload").read_bytes() == b"unverified\n"
        assert not (archive_root / "candidate").exists()
        assert stat.S_IMODE(incomplete_root.stat().st_mode) == 0o755
        assert list(incomplete_root.iterdir()) == []
    finally:
        os.close(incomplete_fd)
        os.close(archive_fd)
        os.close(assay_fd)


def test_prior_quarantine_refuses_to_report_a_renamed_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assay_dir = tmp_path / ".assay"
    assay_dir.mkdir(mode=0o700)
    prior_snapshot = assay_dir / "b110-pilot-evidence"
    prior_snapshot.mkdir(mode=0o700)
    (prior_snapshot / "sentinel").write_text("prior evidence\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_quarantine_renamed_destination_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    original_rename = module._rename_noreplace
    renamed = False

    def rename_root_during_quarantine(source, destination, **kwargs):
        nonlocal renamed
        if source == "b110-pilot-evidence" and not renamed:
            os.rename(
                module._SNAPSHOT_INCOMPLETE,
                "held-incomplete",
                src_dir_fd=assay_fd,
                dst_dir_fd=assay_fd,
            )
            renamed = True
        return original_rename(source, destination, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", rename_root_during_quarantine)
    try:
        with pytest.raises(ValueError, match="directory path could not be checked|directory path changed"):
            _quarantine_for_test(
                module,
                assay_fd,
                assay_dir,
                reason="test-renamed-destination",
            )
        assert renamed
        quarantined = list((assay_dir / "held-incomplete").glob("*"))
        assert len(quarantined) == 1
        assert (quarantined[0] / "b110-pilot-evidence" / "sentinel").read_text(
            encoding="utf-8"
        ) == "prior evidence\n"
        assert not (assay_dir / "b110-pilot-evidence-incomplete").exists()
    finally:
        os.close(assay_fd)


def _quarantine_for_test(module, assay_fd: int, assay_path: Path, *, reason: str) -> str:
    project_fd = os.open(assay_path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return module._quarantine_prior_evidence(
            assay_fd,
            project_fd=project_fd,
            reason=reason,
        )
    finally:
        os.close(project_fd)


def test_prior_quarantine_refuses_to_report_a_renamed_assay_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path
    assay_dir = project / ".assay"
    assay_dir.mkdir(mode=0o700)
    prior_snapshot = assay_dir / "b110-pilot-evidence"
    prior_snapshot.mkdir(mode=0o700)
    (prior_snapshot / "sentinel").write_text("prior evidence\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_quarantine_assay_ancestor_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    assay_fd = os.open(".assay", os.O_RDONLY | os.O_DIRECTORY, dir_fd=project_fd)
    original_rename = module._rename_noreplace
    renamed = False

    def rename_assay_during_quarantine(source, destination, **kwargs):
        nonlocal renamed
        if source == "b110-pilot-evidence" and not renamed:
            os.rename(".assay", ".assay-held", src_dir_fd=project_fd, dst_dir_fd=project_fd)
            os.mkdir(".assay", 0o700, dir_fd=project_fd)
            renamed = True
        return original_rename(source, destination, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", rename_assay_during_quarantine)
    try:
        with pytest.raises(ValueError, match="pilot directory path could not be checked|pilot directory path changed"):
            module._quarantine_prior_evidence(
                assay_fd,
                project_fd=project_fd,
                reason="test-renamed-assay-ancestor",
            )
        assert renamed
        held = project / ".assay-held"
        quarantined = list((held / "b110-pilot-evidence-incomplete").glob("*"))
        assert len(quarantined) == 1
        assert (quarantined[0] / "b110-pilot-evidence" / "sentinel").read_text(
            encoding="utf-8"
        ) == "prior evidence\n"
        assert list((project / ".assay").iterdir()) == []
    finally:
        os.close(assay_fd)
        os.close(project_fd)


def test_prior_quarantine_refuses_to_report_a_public_quarantine_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = tmp_path
    assay_dir = project / ".assay"
    assay_dir.mkdir(mode=0o700)
    prior_snapshot = assay_dir / "b110-pilot-evidence"
    prior_snapshot.mkdir(mode=0o700)
    (prior_snapshot / "sentinel").write_text("prior evidence\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_quarantine_mode_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    assay_fd = os.open(".assay", os.O_RDONLY | os.O_DIRECTORY, dir_fd=project_fd)
    original_rename = module._rename_noreplace
    changed = False

    def make_quarantine_root_public_after_move(source, destination, **kwargs):
        nonlocal changed
        result = original_rename(source, destination, **kwargs)
        if source == "b110-pilot-evidence" and not changed:
            incomplete_fd = os.open(
                "b110-pilot-evidence-incomplete",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=assay_fd,
            )
            try:
                os.fchmod(incomplete_fd, 0o755)
            finally:
                os.close(incomplete_fd)
            changed = True
        return result

    monkeypatch.setattr(module, "_rename_noreplace", make_quarantine_root_public_after_move)
    try:
        with pytest.raises(ValueError, match="directory permissions changed during host verification"):
            module._quarantine_prior_evidence(
                assay_fd,
                project_fd=project_fd,
                reason="test-public-quarantine-root",
            )
        assert changed
        quarantined = list((assay_dir / "b110-pilot-evidence-incomplete").glob("*"))
        assert len(quarantined) == 1
        assert (quarantined[0] / "b110-pilot-evidence" / "sentinel").read_text(
            encoding="utf-8"
        ) == "prior evidence\n"
    finally:
        os.close(assay_fd)
        os.close(project_fd)


def test_archive_withdraws_replaced_transcript_at_atomic_publish(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_transcript_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    original_rename = module._rename_noreplace
    replaced = False

    def replace_transcript_at_archive_commit(source, destination, *rename_args, **kwargs):
        nonlocal replaced
        if (
            isinstance(source, str)
            and source.startswith("archive-stage-")
            and isinstance(destination, str)
            and not replaced
        ):
            stage_fd = os.open(
                source,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=kwargs["src_dir_fd"],
            )
            try:
                replacement_name = ".b110-pilot-run-gate.log.replacement"
                replacement_fd = os.open(
                    replacement_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=stage_fd,
                )
                os.write(replacement_fd, b"replacement transcript at archive commit\n")
                os.fchmod(replacement_fd, 0o400)
                os.close(replacement_fd)
                os.replace(
                    replacement_name,
                    "b110-pilot-run-gate.log",
                    src_dir_fd=stage_fd,
                    dst_dir_fd=stage_fd,
                )
            finally:
                os.close(stage_fd)
            replaced = True
        return original_rename(source, destination, *rename_args, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", replace_transcript_at_archive_commit)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert replaced
    archive_root = state / "b110-pilot-evidence-archive"
    assert archive_root.is_dir()
    assert list(archive_root.iterdir()) == []
    incomplete_root = state / "b110-pilot-evidence-incomplete"
    quarantined_entries = list(incomplete_root.glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1
    quarantined = quarantined_entries[0]
    assert (quarantined / "b110-pilot-evidence").is_dir()
    assert (quarantined / "b110-pilot-evidence.receipt.json").is_file()
    archived_attestation = json.loads(
        (quarantined / "b110-pilot-evidence.attestation.json").read_text(encoding="utf-8")
    )
    quarantined_transcript = quarantined / "b110-pilot-run-gate.log"
    assert hashlib.sha256(quarantined_transcript.read_bytes()).hexdigest() != archived_attestation[
        "transcript_sha256"
    ]


def test_archive_refuses_to_report_a_renamed_assay_ancestor(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_assay_ancestor_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    project_fd = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    original_rename = module._rename_noreplace
    renamed = False

    def rename_assay_during_archive_publish(source, destination, *rename_args, **kwargs):
        nonlocal renamed
        if (
            isinstance(source, str)
            and source.startswith("archive-stage-")
            and isinstance(destination, str)
            and not renamed
        ):
            os.rename(".assay", ".assay-held", src_dir_fd=project_fd, dst_dir_fd=project_fd)
            os.mkdir(".assay", 0o700, dir_fd=project_fd)
            renamed = True
        return original_rename(source, destination, *rename_args, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", rename_assay_during_archive_publish)
    try:
        with pytest.raises(ValueError, match=r"pilot \.assay path changed during archive withdrawal"):
            module.archive_prior_snapshot(args)
        assert renamed
        held = project / ".assay-held"
        archive_root = held / "b110-pilot-evidence-archive"
        assert archive_root.is_dir()
        assert list(archive_root.iterdir()) == []
        quarantined_entries = list((held / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*"))
        assert len(quarantined_entries) == 1
        assert (quarantined_entries[0] / "b110-pilot-evidence").is_dir()
        assert (quarantined_entries[0] / "b110-pilot-evidence.receipt.json").is_file()
        assert (project / ".assay").is_dir()
        assert list((project / ".assay").iterdir()) == []
    finally:
        os.close(project_fd)


def test_archive_refuses_a_substituted_name_after_final_snapshot_verification(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_final_name_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    archive_root = state / "b110-pilot-evidence-archive"
    original_verify = module._verify_snapshot_directory
    post_publish_checks = 0
    replaced = False
    held_name: str | None = None
    published_name: str | None = None

    def replace_archive_name_after_final_snapshot_check(*verify_args, **kwargs):
        nonlocal post_publish_checks, replaced, held_name, published_name
        result = original_verify(*verify_args, **kwargs)
        if archive_root.is_dir() and not replaced:
            entries = list(archive_root.iterdir())
            if len(entries) == 1:
                post_publish_checks += 1
                if post_publish_checks == 2:
                    published_name = entries[0].name
                    held_name = f".held-{published_name}"
                    os.rename(entries[0], archive_root / held_name)
                    os.mkdir(entries[0], 0o700)
                    replaced = True
        return result

    monkeypatch.setattr(module, "_verify_snapshot_directory", replace_archive_name_after_final_snapshot_check)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert replaced and post_publish_checks == 2
    assert published_name is not None and held_name is not None
    assert not (archive_root / published_name).exists()
    assert (archive_root / held_name / "b110-pilot-evidence").is_dir()
    quarantined_entries = list((state / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1
    assert list(quarantined_entries[0].iterdir()) == []


def test_archive_refuses_a_public_archive_root_at_return_boundary(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_mode_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    archive_root = state / "b110-pilot-evidence-archive"
    original_verify = module._verify_snapshot_directory
    post_publish_checks = 0
    changed = False

    def make_archive_root_public_after_final_snapshot_check(*verify_args, **kwargs):
        nonlocal post_publish_checks, changed
        result = original_verify(*verify_args, **kwargs)
        if archive_root.is_dir() and list(archive_root.iterdir()) and not changed:
            post_publish_checks += 1
            if post_publish_checks == 2:
                archive_root.chmod(0o755)
                changed = True
        return result

    monkeypatch.setattr(module, "_verify_snapshot_directory", make_archive_root_public_after_final_snapshot_check)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert changed and post_publish_checks == 2
    assert archive_root.stat().st_mode & 0o777 == 0o755
    assert list(archive_root.iterdir()) == []
    quarantined_entries = list((state / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1


def test_archive_rechecks_snapshot_child_modes_after_final_archive_entry_check(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_child_mode_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    archive_root = state / "b110-pilot-evidence-archive"
    original_path_check = module._check_same_directory_path
    archive_identity = None
    archive_entry_checks = 0
    changed = False

    def make_published_snapshot_child_writable(parent_fd, name, descriptor, original, **kwargs):
        nonlocal archive_identity, archive_entry_checks, changed
        result = original_path_check(parent_fd, name, descriptor, original, **kwargs)
        parent_info = os.fstat(parent_fd)
        if archive_identity is None and archive_root.is_dir():
            visible_archive = archive_root.stat()
            archive_identity = (visible_archive.st_dev, visible_archive.st_ino)
        entries = list(archive_root.iterdir()) if archive_root.is_dir() else []
        if (
            archive_identity is not None
            and (parent_info.st_dev, parent_info.st_ino) == archive_identity
            and len(entries) == 1
            and name == entries[0].name
        ):
            archive_entry_checks += 1
            if archive_entry_checks == 2 and not changed:
                summary = entries[0] / "b110-pilot-evidence" / "b110-pilot-summary.json"
                summary.chmod(0o600)
                changed = True
        return result

    monkeypatch.setattr(module, "_check_same_directory_path", make_published_snapshot_child_writable)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert changed and archive_entry_checks == 2
    assert archive_root.is_dir() and list(archive_root.iterdir()) == []
    quarantined_entries = list((state / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1
    archived_summary = quarantined_entries[0] / "b110-pilot-evidence" / "b110-pilot-summary.json"
    assert archived_summary.stat().st_mode & 0o200


@pytest.mark.parametrize("target", ["archive-root", "archive-entry", "snapshot"])
def test_archive_rechecks_visible_names_after_final_permission_sweep(
    tmp_path: Path,
    committed_worktree: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        f"b110_pilot_host_check_permission_sweep_{target.replace('-', '_')}_race_test",
        PILOT_HOST_CHECK,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    original_verify = module._verify_snapshot_permissions
    changed = False
    archive_root: Path | None = None
    archive_name: str | None = None
    held_path: Path | None = None

    def replace_visible_name_during_permission_sweep(snapshot_fd: int, verify_args):
        nonlocal changed, archive_root, archive_name, held_path
        original_verify(snapshot_fd, verify_args)
        snapshot_path = Path(os.readlink(f"/proc/self/fd/{snapshot_fd}"))
        entry_path = snapshot_path.parent
        archive_root = entry_path.parent
        archive_name = entry_path.name
        if target == "archive-root":
            held_path = archive_root.with_name(".held-b110-pilot-evidence-archive")
            archive_root.rename(held_path)
            archive_root.mkdir(mode=0o700)
        elif target == "archive-entry":
            held_path = entry_path.with_name(f".held-{archive_name}")
            entry_path.rename(held_path)
            entry_path.mkdir(mode=0o700)
        else:
            held_path = snapshot_path.with_name(".held-original-snapshot")
            snapshot_path.rename(held_path)
            snapshot_path.mkdir(mode=0o700)
        changed = True

    monkeypatch.setattr(module, "_verify_snapshot_permissions", replace_visible_name_during_permission_sweep)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert changed and archive_root is not None and archive_name is not None and held_path is not None
    assert archive_root.is_dir()
    assert not (archive_root / archive_name).exists()
    quarantined_entries = list((state / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1


def test_archive_rehashes_snapshot_contents_after_final_boundary_checks(
    tmp_path: Path, committed_worktree: Path, monkeypatch: pytest.MonkeyPatch
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_child_content_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    original_verify = module._verify_snapshot_permissions
    tampered = b"changed after the prior digest pass\n"
    changed = False

    def replace_snapshot_child_before_permission_sweep(snapshot_fd: int, verify_args):
        nonlocal changed
        snapshot_path = Path(os.readlink(f"/proc/self/fd/{snapshot_fd}"))
        summary_path = snapshot_path / "b110-pilot-summary.json"
        summary_path.chmod(0o600)
        summary_path.write_bytes(tampered)
        summary_path.chmod(0o400)
        changed = True
        return original_verify(snapshot_fd, verify_args)

    monkeypatch.setattr(module, "_verify_snapshot_permissions", replace_snapshot_child_before_permission_sweep)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert changed
    archive_root = state / "b110-pilot-evidence-archive"
    assert archive_root.is_dir() and list(archive_root.iterdir()) == []
    quarantined_entries = list((state / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1
    archived_summary = quarantined_entries[0] / "b110-pilot-evidence" / "b110-pilot-summary.json"
    assert archived_summary.read_bytes() == tampered


@pytest.mark.parametrize("target", ["receipt", "attestation", "transcript"])
def test_archive_rechecks_all_bundle_sidecars_after_snapshot_sweep(
    tmp_path: Path,
    committed_worktree: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        f"b110_pilot_host_check_archive_{target}_completion_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    target_names = {
        "receipt": "b110-pilot-evidence.receipt.json",
        "attestation": "b110-pilot-evidence.attestation.json",
        "transcript": "b110-pilot-run-gate.log",
    }
    tampered = f"{target} changed during the final snapshot sweep\n".encode()
    original_verify = module._verify_snapshot_permissions
    changed = False

    def tamper_sidecar_after_snapshot_sweep(snapshot_fd: int, verify_args):
        nonlocal changed
        result = original_verify(snapshot_fd, verify_args)
        snapshot_path = Path(os.readlink(f"/proc/self/fd/{snapshot_fd}"))
        sidecar_path = snapshot_path.parent / target_names[target]
        sidecar_path.chmod(0o600)
        sidecar_path.write_bytes(tampered)
        sidecar_path.chmod(0o400)
        changed = True
        return result

    monkeypatch.setattr(module, "_verify_snapshot_permissions", tamper_sidecar_after_snapshot_sweep)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert changed
    archive_root = state / "b110-pilot-evidence-archive"
    assert archive_root.is_dir() and list(archive_root.iterdir()) == []
    quarantined_entries = list((state / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1
    assert (quarantined_entries[0] / target_names[target]).read_bytes() == tampered


@pytest.mark.parametrize("target", ["transcript", "receipt", "attestation", "snapshot"])
def test_archive_rehashes_bundle_after_atomic_publish(
    tmp_path: Path,
    committed_worktree: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_archive_in_place_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    target_names = {
        "transcript": "b110-pilot-run-gate.log",
        "receipt": "b110-pilot-evidence.receipt.json",
        "attestation": "b110-pilot-evidence.attestation.json",
        "snapshot": "b110-pilot-summary.json",
    }
    tampered = b"same-inode write at archive publication boundary\n"
    original_rename = module._rename_noreplace
    replaced = False

    def write_in_place_at_archive_commit(source, destination, *rename_args, **kwargs):
        nonlocal replaced
        if (
            isinstance(source, str)
            and source.startswith("archive-stage-")
            and isinstance(destination, str)
            and not replaced
        ):
            stage_fd = os.open(
                source,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=kwargs["src_dir_fd"],
            )
            parent_fd = stage_fd
            try:
                target_name = target_names[target]
                if target == "snapshot":
                    parent_fd = os.open(
                        "b110-pilot-evidence",
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=stage_fd,
                    )
                mode_fd = os.open(
                    target_name,
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                os.fchmod(mode_fd, 0o600)
                os.close(mode_fd)
                write_fd = os.open(
                    target_name,
                    os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                os.write(write_fd, tampered)
                os.fsync(write_fd)
                os.fchmod(write_fd, 0o400)
                os.close(write_fd)
            finally:
                if parent_fd != stage_fd:
                    os.close(parent_fd)
                os.close(stage_fd)
            replaced = True
        return original_rename(source, destination, *rename_args, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", write_in_place_at_archive_commit)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert replaced
    archive_root = state / "b110-pilot-evidence-archive"
    assert archive_root.is_dir()
    assert list(archive_root.iterdir()) == []
    incomplete_root = state / "b110-pilot-evidence-incomplete"
    quarantined_entries = list(incomplete_root.glob("unverified-archive-entry-*"))
    assert len(quarantined_entries) == 1
    quarantined = quarantined_entries[0]
    archived_target = (
        quarantined / "b110-pilot-evidence" / target_names[target]
        if target == "snapshot"
        else quarantined / target_names[target]
    )
    assert archived_target.read_bytes() == tampered


def test_archive_revalidates_snapshot_path_at_return_boundary(
    tmp_path: Path,
    committed_worktree: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    receipt = record_run_gate_success(committed_worktree)
    project = committed_worktree / "assay"
    state = project / ".assay"
    monkeypatch.setenv("RUN_GATE_EVIDENCE_DIR", str(gate_evidence_root(committed_worktree)))
    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_snapshot_return_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    project_info = project.stat()
    args = argparse.Namespace(
        project_root=project,
        expected_project_device=project_info.st_dev,
        expected_project_inode=project_info.st_ino,
        expected_commit=receipt["commit"],
        expected_tree=receipt["tree"],
    )
    archive_root = state / "b110-pilot-evidence-archive"
    original_check = module._check_same_directory_path
    post_publish_snapshot_checks = 0
    replaced = False

    def replace_snapshot_before_final_path_check(parent_fd, name, descriptor, original, **kwargs):
        nonlocal post_publish_snapshot_checks, replaced
        if (
            name == module._SNAPSHOT_NAME
            and archive_root.is_dir()
            and list(archive_root.iterdir())
        ):
            post_publish_snapshot_checks += 1
            if post_publish_snapshot_checks == 2 and not replaced:
                os.rename(
                    name,
                    ".held-original-snapshot",
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                replaced = True
        return original_check(parent_fd, name, descriptor, original, **kwargs)

    monkeypatch.setattr(module, "_check_same_directory_path", replace_snapshot_before_final_path_check)
    with pytest.raises(ValueError, match="archive failed final verification at publication boundary"):
        module.archive_prior_snapshot(args)

    assert replaced and post_publish_snapshot_checks == 2
    assert archive_root.is_dir()
    assert list(archive_root.iterdir()) == []
    quarantined_entries = list(
        (state / "b110-pilot-evidence-incomplete").glob("unverified-archive-entry-*")
    )
    assert len(quarantined_entries) == 1
    quarantined = quarantined_entries[0]
    assert (quarantined / ".held-original-snapshot" / "snapshot.sha256").is_file()
    assert list((quarantined / module._SNAPSHOT_NAME).iterdir()) == []


def test_pilot_retry_preserves_snapshot_on_transient_receipt_read_error(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    record_run_gate_success(committed_worktree)
    state = project / ".assay"
    original_receipt = (state / "b110-pilot-evidence.receipt.json").read_bytes()
    original_index = (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes()

    retry_root = tmp_path / "retry-with-transient-receipt-read-error"
    retry_root.mkdir()
    retry, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
        host_archive_receipt_read_fails=True,
    )

    assert retry.returncode == 3
    assert "injected transient receipt read failure" in retry.stderr
    assert "ASSAY_GATE_INCONCLUSIVE=prior B110 pilot evidence is unavailable" in retry.stdout
    assert not any(call and call[0] == "run" and "-d" in call for call in calls)
    assert (state / "b110-pilot-evidence.receipt.json").read_bytes() == original_receipt
    assert (state / "b110-pilot-evidence" / "snapshot.sha256").read_bytes() == original_index
    assert not (state / "b110-pilot-evidence-incomplete").exists()
    assert not (state / "b110-pilot-evidence-archive").exists()


@pytest.mark.parametrize(("target", "mode"), [("snapshot", 0o555), ("file", 0o444)])
def test_pilot_retry_quarantines_public_read_snapshot_modes(
    tmp_path: Path, committed_worktree: Path, target: str, mode: int
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    record_run_gate_success(committed_worktree)
    state = project / ".assay"
    original_snapshot = state / "b110-pilot-evidence"
    if target == "snapshot":
        original_snapshot.chmod(mode)
    else:
        (original_snapshot / "b110-pilot-summary.json").chmod(mode)

    retry_root = tmp_path / f"retry-with-public-{target}-mode"
    retry_root.mkdir()
    retry, _calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert retry.returncode == 0, retry.stderr
    quarantined_snapshots = list(
        (state / "b110-pilot-evidence-incomplete").glob("*/b110-pilot-evidence")
    )
    assert len(quarantined_snapshots) == 1
    quarantined = quarantined_snapshots[0]
    if target == "snapshot":
        assert stat.S_IMODE(quarantined.stat().st_mode) == mode
    else:
        assert stat.S_IMODE((quarantined / "b110-pilot-summary.json").stat().st_mode) == mode
    assert (state / "b110-pilot-evidence" / "snapshot.sha256").is_file()
    assert not (state / "b110-pilot-evidence-archive").exists()


def test_quarantine_refuses_a_replaced_non_directory_evidence_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assay_dir = tmp_path / ".assay"
    assay_dir.mkdir(mode=0o700)
    receipt_name = "b110-pilot-evidence.receipt.json"
    receipt_path = assay_dir / receipt_name
    receipt_path.write_text("original receipt\n", encoding="ascii")
    receipt_path.chmod(0o400)

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_quarantine_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    original_rename = module._rename_noreplace
    replaced = False

    def replace_then_rename(source, destination, *args, **kwargs):
        nonlocal replaced
        if (
            source == receipt_name
            and kwargs.get("src_dir_fd") == assay_fd
            and kwargs.get("dst_dir_fd") != assay_fd
            and not replaced
        ):
            replaced = True
            replacement_name = ".replacement-receipt"
            replacement_fd = os.open(
                replacement_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
                dir_fd=assay_fd,
            )
            os.write(replacement_fd, b"replacement receipt\n")
            os.close(replacement_fd)
            os.rename(
                replacement_name,
                receipt_name,
                src_dir_fd=assay_fd,
                dst_dir_fd=assay_fd,
            )
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", replace_then_rename)
    try:
        with pytest.raises(ValueError, match="changed during quarantine"):
            _quarantine_for_test(module, assay_fd, assay_dir, reason="test-race")
    finally:
        os.close(assay_fd)

    quarantined_receipts = list(
        (assay_dir / "b110-pilot-evidence-incomplete").glob(
            "*/b110-pilot-evidence.receipt.json"
        )
    )
    assert replaced
    assert len(quarantined_receipts) == 1
    assert quarantined_receipts[0].read_bytes() == b"replacement receipt\n"


def test_quarantine_does_not_replace_an_empty_destination_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    assay_dir = tmp_path / ".assay"
    assay_dir.mkdir(mode=0o700)
    snapshot = assay_dir / "b110-pilot-evidence"
    snapshot.mkdir(mode=0o700)
    snapshot.chmod(0o500)

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_quarantine_empty_collision_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    original_rename = module._rename_noreplace
    collision: dict[str, object] = {}

    def create_empty_destination(source, destination, *args, **kwargs):
        if source == module._SNAPSHOT_NAME and not collision:
            quarantine_fd = kwargs["dst_dir_fd"]
            os.mkdir(destination, 0o700, dir_fd=quarantine_fd)
            info = os.stat(destination, dir_fd=quarantine_fd, follow_symlinks=False)
            collision.update(
                destination=destination,
                dev=info.st_dev,
                ino=info.st_ino,
            )
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(module, "_rename_noreplace", create_empty_destination)
    try:
        with pytest.raises(FileExistsError):
            _quarantine_for_test(
                module,
                assay_fd,
                assay_dir,
                reason="empty-destination-collision",
            )
    finally:
        os.close(assay_fd)

    assert collision
    assert snapshot.is_dir()
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o500
    quarantine_name = next((assay_dir / "b110-pilot-evidence-incomplete").iterdir())
    occupied = quarantine_name / str(collision["destination"])
    occupied_info = occupied.stat()
    assert (occupied_info.st_dev, occupied_info.st_ino) == (
        collision["dev"],
        collision["ino"],
    )
    assert list(occupied.iterdir()) == []


def test_quarantine_does_not_change_mode_on_replaced_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assay_dir = tmp_path / ".assay"
    assay_dir.mkdir(mode=0o700)
    snapshot = assay_dir / "b110-pilot-evidence"
    snapshot.mkdir(mode=0o700)
    snapshot.chmod(0o500)

    spec = importlib.util.spec_from_file_location(
        "b110_pilot_host_check_directory_race_test", PILOT_HOST_CHECK
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assay_fd = os.open(assay_dir, os.O_RDONLY | os.O_DIRECTORY)
    original_open = os.open
    replaced = False

    def replace_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if (
            path == module._SNAPSHOT_NAME
            and dir_fd == assay_fd
            and flags & os.O_DIRECTORY
            and not replaced
        ):
            replaced = True
            os.rename(
                path,
                ".original-b110-pilot-evidence",
                src_dir_fd=assay_fd,
                dst_dir_fd=assay_fd,
            )
            os.mkdir(path, 0o700, dir_fd=assay_fd)
            os.chmod(path, 0o755, dir_fd=assay_fd, follow_symlinks=False)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "open", replace_before_open)
    try:
        with pytest.raises(ValueError, match="changed before quarantine"):
            _quarantine_for_test(module, assay_fd, assay_dir, reason="test-replacement")
    finally:
        os.close(assay_fd)

    assert replaced
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o755
    assert stat.S_IMODE((assay_dir / ".original-b110-pilot-evidence").stat().st_mode) == 0o500


def test_pilot_retry_quarantines_a_coherently_rewritten_snapshot(
    tmp_path: Path, committed_worktree: Path
):
    project = committed_worktree / "assay"
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
    )
    assert first.returncode == 0, first.stderr
    original_receipt = record_run_gate_success(committed_worktree)

    state = project / ".assay"
    snapshot = state / "b110-pilot-evidence"
    (snapshot / "b110-pilot-summary.json").chmod(0o600)
    (snapshot / "b110-pilot-artifacts.sha256").chmod(0o600)
    (snapshot / "snapshot.sha256").chmod(0o600)
    snapshot.chmod(0o700)
    changed_summary = b"coherently rewritten after completion\n"
    (snapshot / "b110-pilot-summary.json").write_bytes(changed_summary)
    inner_manifest_path = snapshot / "b110-pilot-artifacts.sha256"
    inner_entries = {}
    for line in inner_manifest_path.read_text(encoding="ascii").splitlines():
        digest, relative = line.split("  ", 1)
        inner_entries[relative] = digest
    inner_entries[".assay/b110-pilot-summary.json"] = hashlib.sha256(changed_summary).hexdigest()
    inner_manifest = "".join(
        f"{digest}  {relative}\n" for relative, digest in sorted(inner_entries.items())
    ).encode("ascii")
    inner_manifest_path.write_bytes(inner_manifest)
    index_entries = {}
    index_path = snapshot / "snapshot.sha256"
    for line in index_path.read_text(encoding="ascii").splitlines():
        digest, relative = line.split("  ", 1)
        index_entries[relative] = digest
    index_entries["b110-pilot-summary.json"] = inner_entries[".assay/b110-pilot-summary.json"]
    index_entries["b110-pilot-artifacts.sha256"] = hashlib.sha256(inner_manifest).hexdigest()
    rewritten_index = "".join(
        f"{digest}  {relative}\n" for relative, digest in sorted(index_entries.items())
    ).encode("ascii")
    index_path.write_bytes(rewritten_index)
    new_digest = hashlib.sha256(rewritten_index).hexdigest()
    receipt = state / "b110-pilot-evidence.receipt.json"
    changed_receipt = json.loads(receipt.read_text(encoding="utf-8"))
    changed_receipt["manifest_sha256"] = hashlib.sha256(inner_manifest).hexdigest()
    changed_receipt["snapshot_sha256"] = new_digest
    receipt.chmod(0o600)
    receipt.write_text(json.dumps(changed_receipt, sort_keys=True, separators=(",", ":")) + "\n")
    for path in (
        snapshot / "b110-pilot-summary.json",
        inner_manifest_path,
        index_path,
    ):
        path.chmod(0o400)
    snapshot.chmod(0o500)
    receipt.chmod(0o400)
    archive_root = state / "b110-pilot-evidence-archive"

    retry_root = tmp_path / "retry"
    retry_root.mkdir()
    proc, calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )

    assert proc.returncode == 0, proc.stderr
    assert not archive_root.exists()
    incomplete_root = state / "b110-pilot-evidence-incomplete"
    quarantined = list(incomplete_root.glob("*/b110-pilot-evidence"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "snapshot.sha256").read_bytes() == rewritten_index
    quarantined_receipt = json.loads(
        (quarantined[0].parent / "b110-pilot-evidence.receipt.json").read_text(encoding="utf-8")
    )
    assert quarantined_receipt["snapshot_sha256"] == new_digest
    assert original_receipt["snapshot_sha256"] != new_digest
    assert any(call and call[0] == "run" and "-d" in call for call in calls)


def test_pilot_retry_quarantines_and_recovers_incomplete_publication(
    tmp_path: Path, committed_worktree: Path
):
    first, _calls, _elapsed = run_launcher(
        tmp_path,
        committed_worktree,
        lane="b110-pilot",
        host_receipt_write_fails=True,
    )

    assert first.returncode != 0
    assert "injected receipt publication failure" in first.stderr
    state = committed_worktree / "assay" / ".assay"
    pending = state / "b110-pilot-evidence.pending.json"
    snapshot = state / "b110-pilot-evidence"
    receipt = state / "b110-pilot-evidence.receipt.json"
    assert pending.is_file()
    assert snapshot.is_dir()
    assert not receipt.exists()

    retry_root = tmp_path / "retry-incomplete"
    retry_root.mkdir()
    retry, _calls, _elapsed = run_launcher(
        retry_root,
        committed_worktree,
        lane="b110-pilot",
    )
    assert retry.returncode == 0, retry.stderr
    assert not pending.exists()
    assert (state / "b110-pilot-evidence.receipt.json").is_file()
    incomplete = list((state / "b110-pilot-evidence-incomplete").glob("*/b110-pilot-evidence"))
    assert len(incomplete) == 1
    assert (incomplete[0].parent / "b110-pilot-evidence.pending.json").is_file()
    assert not (state / "b110-pilot-evidence-archive").exists()


@pytest.mark.parametrize("failure_stage", ["rename", "identity-check"])
def test_quarantine_restores_snapshot_mode_when_move_or_identity_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
):
    assay = tmp_path / ".assay"
    assay.mkdir()
    snapshot = assay / "b110-pilot-evidence"
    snapshot.mkdir(mode=0o700)
    (assay / "b110-pilot-evidence.receipt.json").write_text("untrusted\n", encoding="ascii")
    snapshot.chmod(0o500)

    spec = importlib.util.spec_from_file_location("b110_pilot_quarantine_mode_test", PILOT_HOST_CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if failure_stage == "rename":
        original_rename = module._rename_noreplace

        def fail_snapshot_move(source_name, destination_name, *args, **kwargs):
            if source_name == module._SNAPSHOT_NAME:
                raise OSError("injected quarantine rename failure")
            return original_rename(source_name, destination_name, *args, **kwargs)

        monkeypatch.setattr(module, "_rename_noreplace", fail_snapshot_move)
    else:
        original_check = module._check_same_directory_path

        def fail_quarantine_identity_check(parent_fd, name, descriptor, info, **kwargs):
            if name == module._SNAPSHOT_NAME:
                raise OSError("injected quarantine identity-check failure")
            return original_check(parent_fd, name, descriptor, info, **kwargs)

        monkeypatch.setattr(
            module, "_check_same_directory_path", fail_quarantine_identity_check
        )

    assay_fd = os.open(assay, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError, match="injected quarantine"):
            _quarantine_for_test(module, assay_fd, assay, reason="test-failure")
    finally:
        os.close(assay_fd)

    if failure_stage == "rename":
        retained_snapshot = snapshot
    else:
        retained = list((assay / "b110-pilot-evidence-incomplete").glob("*/b110-pilot-evidence"))
        assert len(retained) == 1
        retained_snapshot = retained[0]
    assert stat.S_IMODE(retained_snapshot.stat().st_mode) == 0o500
    retained_snapshot.chmod(0o700)


def test_pilot_host_rejects_replaced_artifact_directory(tmp_path: Path):
    assay = tmp_path / ".assay"
    assay.mkdir()
    project_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    assay_fd = os.open(".assay", os.O_RDONLY | os.O_DIRECTORY, dir_fd=project_fd)
    original = os.fstat(assay_fd)
    assay.rename(tmp_path / ".assay-original")
    assay.mkdir()
    try:
        spec = importlib.util.spec_from_file_location("b110_pilot_host_check_test", PILOT_HOST_CHECK)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with pytest.raises(ValueError, match="pilot directory path changed"):
            module._check_same_directory_path(project_fd, ".assay", assay_fd, original)
    finally:
        os.close(assay_fd)
        os.close(project_fd)


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
        "b110-pilot-attempt.log",
        "b110-pilot-attempt-window.json",
        "r2-manifest-b110-pilot.txt",
        "b110-pilot-artifacts.sha256",
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
                "r2-manifest-b110-pilot.txt",
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
