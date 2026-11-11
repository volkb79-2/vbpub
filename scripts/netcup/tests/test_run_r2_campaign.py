"""Exercise the persistent R2 supervisor with fake git, lane, and Docker commands."""
from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "runner" / "run-r2-campaign.sh"
COMMIT = "a" * 40


def prepare(tmp_path: Path):
    home = tmp_path / "home"
    runner = tmp_path / "runner"
    bindir = tmp_path / "bin"
    runner.mkdir(parents=True, exist_ok=True)
    bindir.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, runner / "run-r2-campaign.sh")
    (runner / "lib.sh").write_text(
        f"identity_check() {{ :; }}\nRUNNER_HOME={shlex.quote(str(home))}\nGATES_SLICE=none\n"
    )
    fake_git = bindir / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *"config --get remote.origin.url"* ]]; then
  printf '%s\\n' 'https://example.invalid/vbpub.git'
  exit 0
fi
if [[ "$*" == *status* ]]; then
  [[ ${FAKE_GIT_DIRTY:-0} != 1 ]] || printf ' M tracked.txt\\n'
fi
exit 0
"""
    )
    fake_git.chmod(0o755)
    fake_docker = bindir / "docker"
    fake_docker.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_docker.chmod(0o755)
    project = home / "checkouts" / COMMIT / "proj"
    project.mkdir(parents=True, exist_ok=True)
    return home, runner, bindir, project


def campaign(
    tmp_path: Path,
    args,
    jobs_max=None,
    exits=("0",),
    assay=True,
    write="default",
    rg_toml=None,
    jobs_key="jobs_max",
    name="camp1-s1",
    max_attempts=4,
    dirty=False,
):
    home, runner, bindir, project = prepare(tmp_path)
    seq = tmp_path / f"seq-{name}"
    seq.write_text("\n".join(exits) + "\n")
    argv_path = tmp_path / f"argv-{name}.log"
    if write == "default":
        write = (
            f"mkdir -p {shlex.quote(str(project / '.assay'))}; "
            f"printf '{{\"outcome\": \"PASS\"}}' > "
            f"{shlex.quote(str(project / '.assay' / 'verdict-mylane.json'))}"
        )
    (runner / "run-lane.sh").write_text(
        f'printf "%s\\n" "$*" >> {shlex.quote(str(argv_path))}\n'
        f"{write or ':'}\n"
        f"code=$(head -n1 {shlex.quote(str(seq))}); "
        f"tail -n +2 {shlex.quote(str(seq))} > {shlex.quote(str(seq) + '.next')}; "
        f"mv {shlex.quote(str(seq) + '.next')} {shlex.quote(str(seq))}\n"
        'exit "${code:-0}"\n'
    )
    if assay:
        mut = (
            f"[lanes.mylane.judge.mutation]\n{jobs_key} = {jobs_max}\n"
            if jobs_max is not None
            else "[lanes.mylane.judge]\nfail_under = 1\n"
        )
        (project / "assay.toml").write_text(mut)
    if rg_toml is not None:
        (project / "run-gate.toml").write_text(rg_toml)
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_GIT_DIRTY": "1" if dirty else "0",
        "R2_MAX_ATTEMPTS": str(max_attempts),
    }
    # File output ensures the supervisor's sampler never keeps a captured pipe open.
    err_path = tmp_path / f"err-{name}.log"
    with err_path.open("w") as err:
        result = subprocess.run(
            ["bash", str(runner / "run-r2-campaign.sh"), name, COMMIT, "proj", "mylane", *args],
            stdout=subprocess.DEVNULL,
            stderr=err,
            env=env,
        )
    result.stderr = err_path.read_text()
    argv = argv_path.read_text().splitlines() if argv_path.exists() else []
    return result, home / "campaigns" / name, argv, home, runner, bindir, project


@pytest.mark.parametrize(
    "args,expect",
    [
        (["--jobs", "6"], "--jobs 3"),
        (["--jobs=6"], "--jobs=3"),
        (["--jobs", "2"], "--jobs 2"),
        (["--jobs=3", "--progress"], "--jobs=3 --progress"),
        (["--progress"], "--progress"),
    ],
)
def test_jobs_above_the_lane_jobs_max_are_clamped(tmp_path, args, expect):
    result, _, argv, *_ = campaign(tmp_path, args, jobs_max=3)
    assert result.returncode == 0, result.stderr
    assert argv == [f"{COMMIT} proj mylane {expect}"]
    assert ("clamping" in result.stderr) == (expect != " ".join(args) and "--jobs" in " ".join(args))


@pytest.mark.parametrize("kw", [{"jobs_max": None}, {"jobs_max": 3, "assay": False}])
def test_no_jobs_max_or_no_assay_toml_leaves_the_arguments_alone(tmp_path, kw):
    result, _, argv, *_ = campaign(tmp_path, ["--jobs", "9"], **kw)
    assert result.returncode == 0, result.stderr
    assert argv == [f"{COMMIT} proj mylane --jobs 9"]


VERDICT = b'{"outcome": "PASS", "z": 1,  "sig": "x"}\n\x00 raw bytes'


def campaign_with_verdict(tmp_path, exits, verdict=VERDICT, name="verdict-mylane.json", rg_toml=""):
    write = ":"
    if verdict is not None:
        src = tmp_path / "verdict.src"
        src.write_bytes(verdict)
        out = tmp_path / "home" / "checkouts" / COMMIT / "proj" / ".assay"
        write = f"mkdir -p {shlex.quote(str(out))}; cp {shlex.quote(str(src))} {shlex.quote(str(out / name))}"
    return campaign(tmp_path, [], exits=exits, write=write, rg_toml=rg_toml)


def test_verdict_json_is_the_lanes_own_assay_verdict_byte_for_byte(tmp_path):
    result, directory, *_ = campaign_with_verdict(tmp_path, ("0",))
    assert result.returncode == 0, result.stderr
    assert (directory / "verdict.json").read_bytes() == VERDICT
    summary = json.loads((directory / "campaign-summary.json").read_text())
    assert (
        summary["campaign"],
        summary["lane"],
        summary["commit"],
        summary["project"],
        summary["attempts"],
        summary["final_exit"],
        summary["result"],
        summary["reason"],
    ) == ("camp1-s1", "mylane", COMMIT, "proj", 1, 0, "PASS", "pass")
    assert summary["finished_utc"].endswith("Z")
    assert (directory / "final-exit").read_text().strip() == "0"


def test_verdict_json_follows_a_declared_assay_lane_name(tmp_path):
    result, directory, *_ = campaign_with_verdict(
        tmp_path,
        ("0",),
        name="verdict-other.json",
        rg_toml='[lanes.mylane]\nkind = "assay"\nassay_lane = "other"\n',
    )
    assert result.returncode == 0, result.stderr
    assert (directory / "verdict.json").read_bytes() == VERDICT


def test_a_failing_campaign_still_publishes_the_real_fail_verdict(tmp_path):
    result, directory, argv, *_ = campaign_with_verdict(
        tmp_path, ("4", "1"), verdict=b'{"outcome": "FAIL"}\n'
    )
    assert result.returncode == 1 and len(argv) == 2
    assert (directory / "verdict.json").read_bytes() == b'{"outcome": "FAIL"}\n'
    summary = json.loads((directory / "campaign-summary.json").read_text())
    assert (summary["attempts"], summary["final_exit"], summary["result"]) == (2, 1, "NOT_PASS")
    assert summary["reason"] == "lane-exit"


def test_exit_zero_without_a_verdict_fails_loudly_and_writes_none(tmp_path):
    result, directory, *_ = campaign_with_verdict(tmp_path, ("0",), verdict=None)
    assert result.returncode == 70 and "wrote no fresh Assay verdict" in result.stderr
    assert not (directory / "verdict.json").exists()
    assert "verdict_missing=" in (directory / "metadata.txt").read_text()
    assert (directory / "final-exit").read_text().strip() == "0"


def test_a_stale_verdict_from_before_this_supervision_is_not_published(tmp_path):
    project = tmp_path / "home" / "checkouts" / COMMIT / "proj" / ".assay"
    project.mkdir(parents=True)
    (project / "verdict-mylane.json").write_bytes(b"stale")
    result, directory, *_ = campaign(tmp_path, [], exits=("0",), write=False)
    assert result.returncode == 70 and not (directory / "verdict.json").exists()


def test_a_failed_campaign_without_a_verdict_keeps_its_exit_code(tmp_path):
    result, directory, *_ = campaign_with_verdict(tmp_path, ("3",), verdict=None)
    assert result.returncode == 3 and not (directory / "verdict.json").exists()
    assert "verdict_missing=" in (directory / "metadata.txt").read_text()


def test_the_jobs_alias_is_honoured(tmp_path):
    result, _, argv, *_ = campaign(tmp_path, ["--jobs", "6"], jobs_max=2, jobs_key="jobs")
    assert result.returncode == 0, result.stderr
    assert argv == [f"{COMMIT} proj mylane --jobs 2"]


def test_dirty_persistent_checkout_is_refused_before_campaign_creation(tmp_path):
    result, directory, argv, *_ = campaign(tmp_path, [], dirty=True)
    assert result.returncode == 2
    assert "persistent checkout is dirty; commit or remove changes before starting R2" in result.stderr
    assert not directory.exists()
    assert argv == []


def test_campaign_start_requires_a_positive_per_start_suffix(tmp_path):
    home, runner, bindir, _ = prepare(tmp_path)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(runner / "run-r2-campaign.sh"), "camp1", COMMIT, "proj", "mylane"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 2
    assert "campaign start name must end in a positive -s<N> suffix" in result.stderr
    assert not (home / "campaigns").exists()


def test_budget_give_up_writes_final_exit_and_baseline_replay_is_not_progress(tmp_path):
    _, _, _, project = prepare(tmp_path)
    progress = shlex.quote(str(project / ".assay" / "progress-r2.jsonl"))
    result, directory, argv, *_ = campaign(
        tmp_path,
        [],
        exits=("4", "4", "4", "4"),
        write=f"printf '{{\"event\":\"baseline\"}}\\n' >> {progress}",
        max_attempts=4,
    )
    assert result.returncode == 4 and len(argv) == 2
    assert (directory / "final-exit").read_text().strip() == "4"
    summary = json.loads((directory / "campaign-summary.json").read_text())
    assert (summary["result"], summary["reason"], summary["stop_reason"]) == (
        "NOT_PASS",
        "budget",
        "stalled",
    )
    lines = (directory / "attempts.log").read_text().splitlines()
    assert len([line for line in lines if "completed_candidates=" in line]) == 2
    assert "stopping: stalled=2 attempt=2 reason=stalled" in lines[-1]


def test_completed_candidate_growth_resets_stall_but_max_attempts_still_writes_exit_4(tmp_path):
    _, _, _, project = prepare(tmp_path)
    progress = shlex.quote(str(project / ".assay" / "progress-r2.jsonl"))
    result, directory, argv, *_ = campaign(
        tmp_path,
        [],
        exits=("4", "4", "4"),
        write=f"printf '{{\"event\":\"baseline\"}}\\n{{\"event\":\"candidate\"}}\\n' >> {progress}",
        max_attempts=2,
    )
    assert result.returncode == 4 and len(argv) == 2
    summary = json.loads((directory / "campaign-summary.json").read_text())
    assert (summary["final_exit"], summary["result"], summary["reason"], summary["stop_reason"]) == (
        4,
        "NOT_PASS",
        "budget",
        "max-attempts",
    )
    attempt_lines = [line for line in (directory / "attempts.log").read_text().splitlines() if "completed_candidates=" in line]
    assert "completed_candidates=0->1" in attempt_lines[0]
    assert "completed_candidates=1->2" in attempt_lines[1]


def test_each_supervision_start_has_its_own_campaign_directory(tmp_path):
    first, first_dir, _, home, *_ = campaign(tmp_path, [], name="camp1-s1")
    first_log = (first_dir / "attempt-01.log").read_bytes()
    second, second_dir, _, second_home, *_ = campaign(tmp_path, [], name="camp1-s2")
    assert first.returncode == second.returncode == 0
    assert first_dir != second_dir
    assert home == second_home
    assert (home / "locks" / "camp1.lock").exists()
    assert (first_dir / "attempt-01.log").read_bytes() == first_log
    assert (first_dir / "final-exit").read_text().strip() == "0"
    assert (second_dir / "final-exit").read_text().strip() == "0"


def test_reusing_a_start_suffix_refuses_without_overwriting_evidence(tmp_path):
    first, directory, first_argv, *_ = campaign(tmp_path, [], name="camp1-s1")
    first_log = (directory / "attempt-01.log").read_bytes()
    second, second_directory, argv, *_ = campaign(tmp_path, [], name="camp1-s1")
    assert first.returncode == 0
    assert second.returncode == 3
    assert "campaign start already exists" in second.stderr
    assert second_directory == directory
    assert (directory / "attempt-01.log").read_bytes() == first_log
    assert (directory / "final-exit").read_text().strip() == "0"
    assert argv == first_argv


def test_stop_ignores_a_reused_pid_with_a_different_start_time(tmp_path):
    home, runner, bindir, _ = prepare(tmp_path)
    fake_docker = bindir / "docker"
    fake_docker.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake_docker.chmod(0o755)
    unrelated = subprocess.Popen(["sleep", "infinity"])
    try:
        proc_stat = Path(f"/proc/{unrelated.pid}/stat").read_text()
        actual_start = proc_stat.rsplit(") ", 1)[1].split()[19]
        active = home / "locks" / "camp-stale.active"
        active.parent.mkdir(parents=True, exist_ok=True)
        active.write_text(
            "\n".join(
                [
                    "start=camp-stale-s1",
                    "supervisor=99999999",
                    "supervisor_start=0",
                    f"lane={unrelated.pid}",
                    f"lane_start={int(actual_start) + 1}",
                    "sampler=",
                    "sampler_start=",
                    "",
                ]
            )
        )
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
        result = subprocess.run(
            ["bash", str(runner / "run-r2-campaign.sh"), "stop", "camp-stale"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert "cannot identify the lane container; stop it by exact name:" in result.stderr
        assert unrelated.poll() is None
        assert not active.exists()
    finally:
        unrelated.terminate()
        unrelated.wait()


def _wait_for(path: Path, process: subprocess.Popen, detail: str):
    while not path.exists():
        if process.poll() is not None:
            raise AssertionError(f"supervisor exited before {detail}: {process.returncode}")
        time.sleep(0.01)


@pytest.mark.parametrize("label_capable", [True, False], ids=["labels", "recorded-name"])
def test_stop_terminates_supervisor_lane_sampler_and_exact_container(tmp_path, label_capable):
    home, runner, bindir, project = prepare(tmp_path)
    ready = tmp_path / "lane.ready"
    termed = tmp_path / "lane.termed"
    docker_log = tmp_path / "docker.log"
    fake_docker = bindir / "docker"
    fake_docker.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {shlex.quote(str(docker_log))}
if [[ $1 == ps ]]; then
  if [[ " $* " == *" label=run-gate.job=camp-stop-s1 "* &&
        " $* " == *" label=run-gate.campaign=camp-stop-s1 "* ]]; then
    if [[ {str(label_capable).lower()} == true && -f {shlex.quote(str(ready))} ]]; then
      printf '%s\\n' 'run-gate-vbpub-mylane-123-1730000000'
    fi
  elif [[ " $* " == *" --format {{{{.Names}}}} "* ]]; then
    [[ ! -f {shlex.quote(str(ready))} ]] || printf '%s\\n' 'run-gate-vbpub-mylane-123-1730000000'
    printf '%s\\n' 'run-gate-vbpub-mylane-lookalike'
    printf '%s\\n' 'run-gate-vbpub-mylane-999-1730000001'
    printf '%s\\n' 'unrelated-running-container'
  else
    printf '%s\\n' 'unrelated-running-container'
  fi
fi
if [[ $1 == rm ]]; then printf 'rm %s\\n' "$*" >> {shlex.quote(str(docker_log))}; fi
"""
    )
    fake_docker.chmod(0o755)
    (runner / "run-lane.sh").write_text(
        f"""#!/usr/bin/env bash
touch {shlex.quote(str(ready))}
child=
trap '[[ -z $child ]] || kill "$child" 2>/dev/null || true; [[ -z $child ]] || wait "$child" 2>/dev/null || true; touch {shlex.quote(str(termed))}; exit 143' TERM INT
while :; do sleep 3600 & child=$!; wait "$child" || true; done
"""
    )
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_GIT_DIRTY": "0",
    }
    err_path = tmp_path / "stop-supervisor.err"
    with err_path.open("w") as err:
        supervisor = subprocess.Popen(
            ["bash", str(runner / "run-r2-campaign.sh"), "camp-stop-s1", COMMIT, "proj", "mylane"],
            stdout=subprocess.DEVNULL,
            stderr=err,
            env=env,
        )
        try:
            _wait_for(ready, supervisor, "the lane start")
            active = home / "locks" / "camp-stop.active"
            _wait_for(active, supervisor, "the active record")
            while True:
                if supervisor.poll() is not None:
                    raise AssertionError(
                        f"supervisor exited before recording the lane: {supervisor.returncode}"
                    )
                active_fields = dict(
                    line.split("=", 1) for line in active.read_text().splitlines()
                )
                if active_fields["lane"]:
                    break
                time.sleep(0.01)
            container_record = home / "campaigns" / "camp-stop-s1" / "attempt-01.container-name"
            _wait_for(container_record, supervisor, "the exact lane container name")
            assert container_record.read_text().strip() == "run-gate-vbpub-mylane-123-1730000000"
            stop = subprocess.run(
                ["bash", str(runner / "run-r2-campaign.sh"), "stop", "camp-stop"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            assert stop.returncode == 0, stop.stderr
            assert "campaign lock released" in stop.stdout
            assert supervisor.wait() == 143
        finally:
            if supervisor.poll() is None:
                supervisor.kill()
                supervisor.wait()
    assert termed.exists()
    assert not active.exists()
    directory = home / "campaigns" / "camp-stop-s1"
    assert (directory / "final-exit").read_text().strip() == "143"
    summary = json.loads((directory / "campaign-summary.json").read_text())
    assert (summary["result"], summary["reason"], summary["stop_reason"]) == (
        "NOT_PASS",
        "cancelled",
        "TERM",
    )
    calls = docker_log.read_text().splitlines()
    assert any(call.startswith("ps -a ") for call in calls)
    assert any("rm -f run-gate-vbpub-mylane-123-1730000000" in call for call in calls)
    assert not any("rm -f unrelated-running-container" in call for call in calls)
    assert not any("rm -f run-gate-vbpub-mylane-lookalike" in call for call in calls)
    assert not any("rm -f run-gate-vbpub-mylane-999-1730000001" in call for call in calls)
    lock = home / "locks" / "camp-stop.lock"
    with lock.open("a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    sampler_pid = int(active_fields["sampler"])
    proc_stat = Path(f"/proc/{sampler_pid}/stat")
    assert not proc_stat.exists() or proc_stat.read_text().split(")", 1)[1].split()[0] == "Z"
