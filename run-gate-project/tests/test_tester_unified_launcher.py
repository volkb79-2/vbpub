"""Behavioral contract for the repository-owned tester-unified launcher."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest


REPO = Path(__file__).resolve().parents[2]
LAUNCHER = REPO / "tester-unified" / "run"
DOCS = (
    REPO / "tester-unified" / "README.md",
    REPO / "tester-unified" / "docs" / "DESIGN-GUIDE.md",
    REPO / "tester-unified" / "docs" / "CONSUMERS.md",
)


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    state = tmp_path / "docker-state"
    bin_dir.mkdir()
    state.mkdir()
    log = state / "calls"
    docker = bin_dir / "docker"
    docker.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        set -euo pipefail
        state=$FAKE_DOCKER_STATE
        printf '%s\\n' "$*" >> "$state/calls"
        command=$1
        shift
        case "$command" in
          image)
            exit 0
            ;;
          ps)
            exit 0
            ;;
          run)
            : > "$state/mounts"
            : > "$state/environment"
            while (($#)); do
              case "$1" in
                -v)
                  value=$2
                  printf '%s -> %s\\n' "${value%%:*}" "${value#*:}" >> "$state/mounts"
                  shift 2
                  ;;
                -e)
                  printf '%s\\n' "$2" >> "$state/environment"
                  shift 2
                  ;;
                --cgroup-parent)
                  printf '%s' "$2" > "$state/cgroup"
                  shift 2
                  ;;
                --network)
                  printf '%s' "$2" > "$state/network"
                  shift 2
                  ;;
                -w)
                  printf '%s' "$2" > "$state/workdir"
                  shift 2
                  ;;
                bash)
                  if [[ ${FAKE_DOCKER_EXECUTE_JOB:-0} == 1 ]]; then
                    mkdir -p "$state/home"
                    set +e
                    HOME="$state/home" "$@" > "$state/job.log" 2>&1
                    printf '%s\\n' "$?" > "$state/job.exit"
                    set -e
                    break
                  fi
                  shift
                  ;;
                *) shift ;;
              esac
            done
            printf '%064d\\n' 1
            ;;
          update)
            if [[ ${FAKE_DOCKER_UPDATE_FAIL:-0} == 1 ]]; then
              exit 9
            fi
            printf '%s\\n' "${@: -1}"
            ;;
          inspect)
            if [[ $* == *'.Config.User'* ]]; then
              printf '1003|%s|3000000000|%s\\n' "$(<"$state/cgroup")" "$(<"$state/workdir")"
            elif [[ $* == *'.Mounts'* ]]; then
              sed -n '1,$p' "$state/mounts"
            elif [[ $* == *'.Config.Env'* ]]; then
              sed -n '1,$p' "$state/environment"
            elif [[ $* == *'.HostConfig.NetworkMode'* ]]; then
              printf '%s\\n' "${FAKE_DOCKER_NETWORK:-$(<"$state/network")}"
            else
              printf '[{"Id":"fake"}]\\n'
            fi
            ;;
          wait)
            if [[ -f $state/job.exit ]]; then cat "$state/job.exit"; else printf '0\\n'; fi
            ;;
          logs)
            if [[ -f $state/job.log ]]; then cat "$state/job.log"; else printf 'fake gate ran\\nTESTER_UNIFIED_JOB_EXIT=0\\n'; fi
            ;;
          stop|rm)
            exit 0
            ;;
          *)
            printf 'unexpected fake docker command: %s\\n' "$command" >&2
            exit 91
            ;;
        esac
    """))
    docker.chmod(0o755)
    return bin_dir, log


def _run_launcher(tmp_path: Path, *args: str, pressure: float = 0.0,
                  cgroup: str | None = "dev-gates.slice",
                  update_fails: bool = False, network: str | None = None,
                  accepted_network: str | None = None, execute_job: bool = False):
    bin_dir, log = _fake_docker(tmp_path)
    pressure_file = tmp_path / "pressure"
    pressure_file.write_text(
        f"some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
        f"full avg10={pressure:.2f} avg60=0.00 avg300=0.00 total=0\n"
    )
    evidence = tmp_path / "evidence"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_DOCKER_STATE"] = str(log.parent)
    env["_TESTER_UNIFIED_PRESSURE_FILE"] = str(pressure_file)
    env["_TESTER_UNIFIED_LAUNCH_LOCK"] = str(tmp_path / "launch.lock")
    env["TESTER_UNIFIED_RUN_NAME"] = "tester-unified-contract-test"
    env["FAKE_DOCKER_UPDATE_FAIL"] = "1" if update_fails else "0"
    env["FAKE_DOCKER_EXECUTE_JOB"] = "1" if execute_job else "0"
    if accepted_network is not None:
        env["FAKE_DOCKER_NETWORK"] = accepted_network
    if cgroup is None:
        env.pop("CGROUP_PARENT_DEV_GATES", None)
    else:
        env["CGROUP_PARENT_DEV_GATES"] = cgroup
    proc = subprocess.run(
        [str(LAUNCHER), "--workdir", str(REPO / "run-gate-project"),
         "--evidence-dir", str(evidence),
         *(["--network", network] if network is not None else []), "--", *args],
        text=True, capture_output=True, env=env, check=False,
    )
    return proc, log, evidence


def test_launcher_constructs_and_verifies_the_complete_gate_boundary(tmp_path):
    proc, log, evidence = _run_launcher(tmp_path, "bash", "-c", "exit 0")
    assert proc.returncode == 0, proc.stderr
    assert "fake gate ran" in proc.stdout
    assert "tester-unified: evidence:" in proc.stdout

    calls = log.read_text()
    workspace = subprocess.run(
        ["findmnt", "--target", str(REPO), "--noheadings", "--output", "TARGET"],
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    host_workspace = subprocess.run(
        ["findmnt", "--target", str(REPO), "--noheadings", "--output", "FSROOT"],
        text=True, capture_output=True, check=True,
    ).stdout.strip()
    socket_gid = os.stat("/var/run/docker.sock").st_gid

    assert "run -d" in calls
    assert "--init" in calls
    assert "--cgroup-parent dev-gates.slice" in calls
    assert "--cpus=3" in calls
    assert f"--group-add {socket_gid}" in calls
    assert f"-v {host_workspace}:{host_workspace}" in calls
    assert f"-v {host_workspace}:{workspace}" in calls
    assert "-v /var/run/docker.sock:/var/run/docker.sock" in calls
    assert "-e PATH=/opt/tester-venv/bin:/usr/local/bin:/usr/bin:/bin" in calls
    assert "update --cpus=3 tester-unified-contract-test" in calls
    assert "wait tester-unified-contract-test" in calls
    assert "logs tester-unified-contract-test" in calls
    assert "rm tester-unified-contract-test" in calls

    container_tmp = "/var/tmp/tester-unified" if workspace == "/tmp" else "/tmp"
    mounts = (log.parent / "mounts").read_text().splitlines()
    temp_mounts = [line.removesuffix(f" -> {container_tmp}")
                   for line in mounts
                   if line.endswith(f" -> {container_tmp}")]
    assert len(temp_mounts) == 1
    worktree_relative = REPO.relative_to(Path(workspace))
    expected_temp_parent = (
        Path(host_workspace) / worktree_relative
        / ".assay" / "tester-unified-tmp"
    )
    assert temp_mounts[0].startswith(f"{expected_temp_parent}/run.")
    environment = (log.parent / "environment").read_text().splitlines()
    assert f"TMPDIR={container_tmp}" in environment
    assert f"TMP={container_tmp}" in environment
    assert f"TEMP={container_tmp}" in environment
    run_evidence = evidence / "tester-unified-contract-test"
    assert (run_evidence / "container.inspect.json").read_text().startswith("[")
    assert (run_evidence / "docker-wait.exit").read_text() == "0\n"
    assert "TESTER_UNIFIED_JOB_EXIT=0" in (
        run_evidence / "container.log").read_text()


@pytest.mark.parametrize(
    ("cgroup", "pressure", "message"),
    [
        (None, 0.0, "CGROUP_PARENT_DEV_GATES is required"),
        ("not-a-slice", 0.0, "not a valid slice name"),
        ("dev-gates.slice", 5.01, "exceeds the launch ceiling 5.0"),
    ],
)
def test_launcher_refuses_missing_or_unsafe_launch_facts(
        tmp_path, cgroup, pressure, message):
    proc, log, _ = _run_launcher(
        tmp_path, "true", pressure=pressure, cgroup=cgroup,
    )
    assert proc.returncode == 2
    assert message in proc.stderr
    assert not log.exists() or "run -d" not in log.read_text()


def test_post_launch_verification_failure_stops_and_removes_only_its_container(
        tmp_path):
    proc, log, _ = _run_launcher(tmp_path, "true", update_fails=True)
    assert proc.returncode == 125
    assert "docker rejected the post-launch 3-CPU cap" in proc.stderr
    calls = log.read_text()
    assert "stop -t 10 tester-unified-contract-test" in calls
    assert "wait tester-unified-contract-test" in calls
    assert "rm tester-unified-contract-test" in calls


def test_launcher_preserves_offline_gate_policy_and_records_live_acceptance(tmp_path):
    proc, log, evidence = _run_launcher(tmp_path, "true", network="none")
    assert proc.returncode == 0, proc.stderr
    assert "--network none" in log.read_text()
    assert "network_mode=none\n" in (evidence / "tester-unified-contract-test/launch.txt").read_text()


def test_launcher_refuses_network_argument_or_failed_acceptance(tmp_path):
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    proc, log, _ = _run_launcher(invalid, "true", network="host")
    assert proc.returncode == 2 and "accepts only none" in proc.stderr
    assert not log.exists()
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    proc, log, _ = _run_launcher(rejected, "true", network="none", accepted_network="bridge")
    assert proc.returncode == 125 and "did not accept network mode none" in proc.stderr
    assert "stop -t 10 tester-unified-contract-test" in log.read_text()
    assert "rm tester-unified-contract-test" in log.read_text()


@pytest.mark.parametrize("job_exit", [0, 7])
def test_real_job_wrapper_preserves_exit_when_stdout_has_no_final_newline(tmp_path, job_exit):
    proc, log, evidence = _run_launcher(
        tmp_path, "bash", "-c", f"printf no-newline; exit {job_exit}", execute_job=True)
    assert proc.returncode == job_exit, proc.stderr
    run = evidence / "tester-unified-contract-test"
    assert (run / "container.log").read_bytes() == (
        f"no-newline\nTESTER_UNIFIED_JOB_EXIT={job_exit}\n".encode())
    assert (run / "docker-wait.exit").read_text() == f"{job_exit}\n"
    assert "rm tester-unified-contract-test" in log.read_text()


def _heading_ids(text: str) -> set[str]:
    result = set()
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        heading = line.lstrip("#").strip().lower()
        slug = re.sub(r"[^a-z0-9 _-]", "", heading).replace(" ", "-")
        result.add(re.sub(r"-+", "-", slug))
    return result


def test_launcher_docs_are_linked_and_document_the_complete_cli():
    for document in DOCS:
        text = document.read_text()
        for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
            path_text, _, anchor = target.partition("#")
            target_path = (document.parent / path_text).resolve() if path_text else document
            assert target_path.is_file(), f"broken link in {document}: {target}"
            if anchor:
                assert anchor in _heading_ids(target_path.read_text()), (
                    f"broken anchor in {document}: {target}"
                )
    combined = "\n".join(path.read_text() for path in DOCS)
    for token in ("--workdir", "--evidence-dir", "--network none", "--help", " -- "):
        assert token in combined
    assert "tester-unified/run --workdir run-gate-project -- ./run-gate.py selftest" \
        in (DOCS[0].read_text() + DOCS[2].read_text())
