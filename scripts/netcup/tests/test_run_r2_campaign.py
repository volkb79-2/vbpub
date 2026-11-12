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
    fake_nproc = bindir / "nproc"
    fake_nproc.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "${FAKE_NPROC:-4}"\n')
    fake_nproc.chmod(0o755)
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


@pytest.mark.parametrize(
    "label_capable,record_lost",
    [(True, False), (False, False), (True, True)],
    ids=["labels", "legacy-25.0.2-full-checkout-name", "labels-only-record-lost"],
)
def test_stop_terminates_supervisor_lane_sampler_and_exact_container(
        tmp_path, label_capable, record_lost):
    """`record_lost`: the exact-name record is gone, so only the Run-Gate
    label lookup can find the container (review: label path pinned)."""
    home, runner, bindir, project = prepare(tmp_path)
    ready = tmp_path / "lane.ready"
    termed = tmp_path / "lane.termed"
    docker_log = tmp_path / "docker.log"
    lookup_count = tmp_path / "fallback-lookup-count"
    container_record = home / "campaigns" / "camp-stop-s1" / "attempt-01.container-name"
    active_record = home / "locks" / "camp-stop.active"
    lane_container = f"run-gate-{COMMIT}-mylane-123-1730000000"
    lookalike_container = f"run-gate-{COMMIT}-mylane-lookalike"
    baseline_container = f"run-gate-{COMMIT}-mylane-999-1730000001"
    fake_docker = bindir / "docker"
    fake_docker.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {shlex.quote(str(docker_log))}
if [[ $1 == ps ]]; then
  if [[ " $* " == *" label=run-gate.job=camp-stop-s1 "* &&
        " $* " == *" label=run-gate.campaign=camp-stop-s1 "* ]]; then
    if [[ {str(label_capable).lower()} == true && -f {shlex.quote(str(ready))} ]]; then
      printf '%s\\n' {shlex.quote(lane_container)}
    fi
  elif [[ " $* " == *" --format {{{{.Names}}}} "* ]]; then
    if [[ {str(label_capable).lower()} == false && -f {shlex.quote(str(ready))} ]]; then
      count=0
      [[ ! -f {shlex.quote(str(lookup_count))} ]] || read -r count < {shlex.quote(str(lookup_count))}
      count=$((count + 1))
      printf '%s\\n' "$count" > {shlex.quote(str(lookup_count))}
      if (( count == 3 )) && [[ ! -f {shlex.quote(str(container_record))} ]]; then
        lane_pid=$(sed -n 's/^lane=//p' {shlex.quote(str(active_record))})
        [[ ! $lane_pid =~ ^[1-9][0-9]*$ ]] || kill -TERM "$lane_pid" 2>/dev/null || true
      fi
    fi
    [[ ! -f {shlex.quote(str(ready))} ]] || printf '%s\\n' {shlex.quote(lane_container)}
    [[ ! -f {shlex.quote(str(ready))} ]] || printf '%s\\n' {shlex.quote(lookalike_container)}
    printf '%s\\n' {shlex.quote(baseline_container)}
    printf '%s\\n' 'unrelated-running-container'
  else
    printf '%s\\n' 'unrelated-running-container'
  fi
fi
if [[ $1 == rm ]]; then printf 'rm %s\\n' "$*" >> {shlex.quote(str(docker_log))}; fi
"""
    )
    fake_docker.chmod(0o755)
    lane_lines = [
        "#!/usr/bin/env bash",
        f"touch {shlex.quote(str(ready))}",
        "child=",
        f"trap '[[ -z $child ]] || kill \"$child\" 2>/dev/null || true; [[ -z $child ]] || wait \"$child\" 2>/dev/null || true; touch {shlex.quote(str(termed))}; exit 143' TERM INT",
        'while :; do sleep 3600 & child=$!; wait "$child" || true; done',
    ]
    (runner / "run-lane.sh").write_text("\n".join(lane_lines) + "\n")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_GIT_DIRTY": "0",
        "R2_MAX_ATTEMPTS": "1",
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
            _wait_for(container_record, supervisor, "the exact lane container name")
            assert container_record.read_text().strip() == lane_container
            if record_lost:
                container_record.unlink()
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
    assert any(f"rm -f {lane_container}" in call for call in calls)
    assert not any("rm -f unrelated-running-container" in call for call in calls)
    assert not any(f"rm -f {lookalike_container}" in call for call in calls)
    assert not any(f"rm -f {baseline_container}" in call for call in calls)
    lock = home / "locks" / "camp-stop.lock"
    with lock.open("a") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lock_file, fcntl.LOCK_UN)
    sampler_pid = int(active_fields["sampler"])
    proc_stat = Path(f"/proc/{sampler_pid}/stat")
    assert not proc_stat.exists() or proc_stat.read_text().split(")", 1)[1].split()[0] == "Z"


# --- NC-42: several shard instances per host, merge, cross-host split -------


NEW_RG_HELP = ("  --shard K/N       assay R2 only\n"
               "  --merge-shards N  assay R2 only: STRICT whole-lane merge\n"
               "  --carry-over DIR --carry-over-reason TEXT")
OLD_RG_HELP = "usage: run-gate <lane> [--worktree PATH] [--jobs N|auto]"

FLEET_LANE = r'''
args="$*"
if [[ $args == *" --help" ]]; then
  printf '%s\n' "$args" >> @ARGV@.help
  [[ ${FAKE_RG_HELP_EXIT:-0} == 0 ]] || exit "$FAKE_RG_HELP_EXIT"
  printf '%s\n' "$FAKE_RG_HELP"
  exit 0
fi
printf '%s\n' "$args" >> @ARGV@
mkdir -p @ASSAY@
key=mylane
if [[ $args =~ --shard\ ([0-9]+)/([0-9]+) ]]; then
  key="mylane.mylane.shard${BASH_REMATCH[1]}of${BASH_REMATCH[2]}"
fi
printf '{"event":"candidate"}\n' >> @ASSAY@/progress-$key.jsonl
printf '{"outcome": "PASS", "key": "%s"}' "$key" > @ASSAY@/verdict-$key.json
exit "${FLEET_EXIT:-0}"
'''


def fleet(tmp_path, args, *, meminfo_kb=64 * 1024 * 1024, extra_env=None, name="camp-s1",
          lane_body=FLEET_LANE, rg_toml=None, assay_toml=None, meminfo_text=None):
    home, runner, bindir, project = prepare(tmp_path)
    argv_path = tmp_path / "argv.log"
    (runner / "run-lane.sh").write_text(
        lane_body.replace("@ARGV@", shlex.quote(str(argv_path)))
        .replace("@ASSAY@", shlex.quote(str(project / ".assay")))
    )
    if rg_toml is not None:
        (project / "run-gate.toml").write_text(rg_toml)
    if assay_toml is not None:
        (project / "assay.toml").write_text(assay_toml)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(meminfo_text or (
        f"MemTotal: {meminfo_kb} kB\nMemAvailable: {meminfo_kb} kB\n"
        "SwapTotal: 0 kB\nSwapFree: 0 kB\n"))
    fake_ksm = tmp_path / "ksm-default"
    fake_ksm.mkdir()
    (fake_ksm / "run").write_text("0\n")
    (fake_ksm / "pages_sharing").write_text("0\n")
    (fake_ksm / "pages_shared").write_text("0\n")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_GIT_DIRTY": "0",
        "R2_MAX_ATTEMPTS": "3",
        "R2_INSTANCE_STAGGER_S": "0",
        "R2_MEMINFO": str(meminfo),
        "R2_KSM_SYSFS": str(fake_ksm),
        "R2_INSTANCE_MEM_MIB": "1024",
        "FAKE_RG_HELP": NEW_RG_HELP,
    }
    for key in ("R2_HOST_RESERVE_MIB", "R2_OVERCOMMIT", "R2_WORKER_MEM_MIB",
                "R2_EXPECTED_PEAK_CREDIT_MIB", "FAKE_NPROC"):
        env.pop(key, None)
    env.update(extra_env or {})
    env = {key: value for key, value in env.items() if value is not None}
    err_path = tmp_path / "fleet.err"
    with err_path.open("w") as err:
        result = subprocess.run(
            ["bash", str(runner / "run-r2-campaign.sh"), name, COMMIT, "proj", "mylane", *args],
            stdout=subprocess.DEVNULL, stderr=err, env=env,
        )
    result.stderr = err_path.read_text()
    argv = argv_path.read_text().splitlines() if argv_path.exists() else []
    return result, home, argv, runner, env


def test_instances_run_every_shard_then_one_merge(tmp_path):
    result, home, argv, *_ = fleet(tmp_path, ["--instances", "2", "--jobs", "2"])
    assert result.returncode == 0, result.stderr
    assert sorted(argv) == sorted([
        f"{COMMIT} proj mylane --jobs 2 --shard 0/2",
        f"{COMMIT} proj mylane --jobs 2 --shard 1/2",
        f"{COMMIT} proj mylane --jobs 2 --merge-shards 2",
    ])
    assert argv[-1].endswith("--merge-shards 2"), "the merge runs after every shard"
    fleet_dir = home / "campaigns" / "camp-s1"
    summary = json.loads((fleet_dir / "campaign-summary.json").read_text())
    assert summary["mode"] == "fleet" and summary["final_exit"] == 0
    assert [i["campaign"] for i in summary["instances"]] == ["camp.i0of2-s1", "camp.i1of2-s1"]
    assert all(i["final_exit"] == 0 for i in summary["instances"])
    assert summary["merge"]["campaign"] == "camp.merge-s1"
    assert json.loads((fleet_dir / "verdict.json").read_text())["key"] == "mylane"
    assert (fleet_dir / "final-exit").read_text() == "0\n"
    for child in ("camp.i0of2-s1", "camp.i1of2-s1"):
        child_dir = home / "campaigns" / child
        assert (child_dir / "final-exit").read_text() == "0\n"
        shard = child.split(".")[1].split("-")[0].replace("i", "shard")
        assert (json.loads((child_dir / "verdict.json").read_text())["key"]
                == f"mylane.mylane.{shard}"), "keyed by assay lane AND run-gate lane"
    assert not (home / "locks" / "camp.active").exists()


def test_a_host_running_part_of_the_shards_reports_merge_pending(tmp_path):
    result, home, argv, *_ = fleet(
        tmp_path, ["--instances", "1", "--shard-total", "2", "--shard-offset", "1"])
    assert result.returncode == 75, result.stderr
    assert argv == [f"{COMMIT} proj mylane --shard 1/2"]
    summary = json.loads((home / "campaigns" / "camp-s1" / "campaign-summary.json").read_text())
    assert (summary["reason"], summary["merge"]) == ("merge-pending", None)


def test_merge_only_runs_just_the_merge(tmp_path):
    result, home, argv, *_ = fleet(tmp_path, ["--merge-only", "4"])
    assert result.returncode == 0, result.stderr
    assert argv == [f"{COMMIT} proj mylane --merge-shards 4"]


def test_a_failing_merge_is_the_fleet_result(tmp_path):
    result, home, _argv, *_ = fleet(tmp_path, ["--instances", "2"],
                                     extra_env={"FLEET_EXIT": "1"})
    assert result.returncode == 1
    summary = json.loads((home / "campaigns" / "camp-s1" / "campaign-summary.json").read_text())
    assert summary["result"] == "NOT_PASS" and summary["merge"]["final_exit"] == 1


def test_the_memory_guard_refuses_before_any_campaign_state(tmp_path):
    result, home, argv, *_ = fleet(
        tmp_path, ["--instances", "3"], meminfo_kb=4 * 1024 * 1024,
        extra_env={"R2_INSTANCE_MEM_MIB": "2048"})
    assert result.returncode == 75
    assert "memory guard" in result.stderr
    assert "expected 3 instance(s) x 2048 MiB = 6144 MiB" in result.stderr
    assert "budget=3072 MiB" in result.stderr
    assert argv == []
    assert not (home / "campaigns" / "camp-s1").exists()


def test_the_memory_guard_reads_the_lanes_declared_memory(tmp_path):
    result, *_ = fleet(
        tmp_path, ["--instances", "2"], meminfo_kb=6 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\njobs = "auto"\n'
                 '[lanes.mylane.resources]\nmemory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None})
    assert result.returncode == 75
    assert "expected 2 instance(s) x 3072 MiB = 6144 MiB" in result.stderr
    assert "workers_per_instance=3/3" in result.stderr


def test_explicit_jobs_are_clamped_before_fleet_memory_estimation(tmp_path):
    result, _home, argv, *_ = fleet(
        tmp_path, ["--instances", "2", "--jobs", "6"], meminfo_kb=32 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\n[lanes.mylane.resources]\n'
                 'memory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None, "R2_OVERCOMMIT": "2.0"})
    assert result.returncode == 0, result.stderr
    assert "clamping --jobs 6 -> 3" in result.stderr
    assert "workers_per_instance=3/3" in result.stderr
    assert all("--jobs 3" in line for line in argv if "--shard " in line)


def test_three_one_worker_shards_scale_the_declared_lane_ceiling(tmp_path):
    result, _home, argv, *_ = fleet(
        tmp_path, ["--instances", "3", "--jobs", "1"], meminfo_kb=5 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\njobs = "auto"\n'
                 '[lanes.mylane.resources]\nmemory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None, "R2_OVERCOMMIT": "2.0"})
    assert result.returncode == 0, result.stderr
    assert "expected 3 instance(s) x 1024 MiB = 3072 MiB" in result.stderr
    assert "workers_per_instance=1/3, scaled_lane_ceiling=1024 MiB" in result.stderr
    assert len([line for line in argv if "--shard " in line]) == 3
    assert all("--jobs 1" in line for line in argv if "--shard " in line)


def test_worker_memory_override_is_applied_per_worker(tmp_path):
    result, *_ = fleet(
        tmp_path, ["--instances", "3", "--jobs", "1"], meminfo_kb=4 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\n[lanes.mylane.resources]\n'
                 'memory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None, "R2_WORKER_MEM_MIB": "430",
                   "R2_OVERCOMMIT": "3.0"})
    assert result.returncode == 0, result.stderr
    assert "expected 3 instance(s) x 430 MiB = 1290 MiB" in result.stderr


def test_scaled_lane_ceiling_rounds_up_and_has_a_128_mib_floor(tmp_path):
    result, *_ = fleet(
        tmp_path, ["--instances", "3", "--jobs", "1"], meminfo_kb=1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\n[lanes.mylane.resources]\n'
                 'memory = "129k"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None, "R2_HOST_RESERVE_MIB": "0",
                   "R2_OVERCOMMIT": "1.0"})
    assert result.returncode == 0, result.stderr
    assert "lane_cap=1 MiB" in result.stderr
    assert "scaled_lane_ceiling=128 MiB" in result.stderr
    assert "expected 3 instance(s) x 128 MiB = 384 MiB" in result.stderr


@pytest.mark.parametrize(("instances", "expected_jobs", "expected_total"), [
    (1, 3, 3072),
    (2, 2, 4096),
    (3, 1, 3072),
    (4, 1, 4096),
])
def test_auto_lane_defaults_per_instance_jobs_by_instance_count(
        tmp_path, instances, expected_jobs, expected_total):
    result, _home, argv, *_ = fleet(
        tmp_path, ["--instances", str(instances)], meminfo_kb=32 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\njobs = "auto"\n'
                 '[lanes.mylane.resources]\nmemory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None})
    assert result.returncode == 0, result.stderr
    assert f"expected {instances} instance(s) x {1024 * expected_jobs} MiB = {expected_total} MiB" in result.stderr
    assert f"workers_per_instance={expected_jobs}/3" in result.stderr
    assert f"--jobs {expected_jobs}" in result.stderr
    assert all(f"--jobs {expected_jobs}" in line for line in argv)


def test_memory_guard_uses_the_last_jobs_value_like_run_gate(tmp_path):
    result, home, argv, *_ = fleet(
        tmp_path, ["--instances", "3", "--jobs", "1", "--jobs", "3"],
        meminfo_kb=8 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\njobs = "auto"\n'
                 '[lanes.mylane.resources]\nmemory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None})
    assert result.returncode == 75
    assert "workers_per_instance=3/3" in result.stderr
    assert "expected 3 instance(s) x 3072 MiB = 9216 MiB" in result.stderr
    assert argv == [] and not (home / "campaigns" / "camp-s1").exists()


def live_host_fleet(tmp_path, args, *, ksm_run="1", credit=None):
    ksm = tmp_path / "live-ksm"
    ksm.mkdir()
    (ksm / "run").write_text(f"{ksm_run}\n")
    (ksm / "pages_sharing").write_text("123456\n")
    (ksm / "pages_shared").write_text("654321\n")
    meminfo = (
        "MemTotal: 8192000 kB\nMemAvailable: 6608896 kB\n"
        "SwapTotal: 4194304 kB\nSwapFree: 4096000 kB\n")
    return fleet(
        tmp_path, args,
        rg_toml=('[lanes.mylane]\nkind = "assay"\njobs = "auto"\n'
                 '[lanes.mylane.resources]\nmemory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        meminfo_text=meminfo,
        extra_env={"R2_INSTANCE_MEM_MIB": None, "R2_WORKER_MEM_MIB": None,
                   "R2_KSM_SYSFS": str(ksm),
                   "R2_EXPECTED_PEAK_CREDIT_MIB": credit})


def test_live_host_three_instances_default_to_one_worker_and_are_admitted(tmp_path):
    result, _home, argv, *_ = live_host_fleet(tmp_path, ["--instances", "3"])
    assert result.returncode == 0, result.stderr
    assert "expected 3 instance(s) x 1024 MiB = 3072 MiB" in result.stderr
    assert "workers_per_instance=1/3" in result.stderr
    assert "--jobs 1" in result.stderr
    assert all("--jobs 1" in line for line in argv)


@pytest.mark.parametrize("ksm_run", ["0", "1"])
def test_swap_and_ksm_do_not_credit_expected_peaks(tmp_path, ksm_run):
    result, home, argv, *_ = live_host_fleet(
        tmp_path, ["--instances", "3", "--jobs", "3"], ksm_run=ksm_run)
    assert result.returncode == 75
    assert "expected 3 instance(s) x 3072 MiB = 9216 MiB" in result.stderr
    assert "expected_budget=5430 MiB" in result.stderr
    assert "expected_peak_credit=0 MiB" in result.stderr
    assert "declared_cap_total=9216 MiB" in result.stderr
    assert "cap_budget=12908 MiB" in result.stderr
    assert "KSM do not credit expected peaks" in result.stderr
    assert "advice: retry with --instances 3 --jobs 1" in result.stderr
    assert "3 x 1024 MiB = 3072 MiB <= 5430 MiB" in result.stderr
    assert argv == []
    assert not (home / "campaigns" / "camp-s1").exists()


def test_explicit_expected_peak_credit_is_printed_and_only_changes_expected_budget(tmp_path):
    result, *_ = fleet(
        tmp_path, ["--instances", "1"], meminfo_kb=4 * 1024 * 1024,
        rg_toml='[lanes.mylane]\nkind = "assay"\n',
        extra_env={"R2_INSTANCE_MEM_MIB": "4096",
                   "R2_EXPECTED_PEAK_CREDIT_MIB": "1024",
                   "R2_HOST_RESERVE_MIB": "1024"})
    assert result.returncode == 0, result.stderr
    assert "expected_peak_credit=1024 MiB" in result.stderr
    assert "expected_budget=4096 MiB" in result.stderr
    assert "declared_cap_total=not-applicable" in result.stderr


def test_declared_caps_use_overcommit_separately_from_expected_peaks(tmp_path):
    result, home, argv, *_ = fleet(
        tmp_path, ["--instances", "3", "--jobs", "1"], meminfo_kb=4 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\n[lanes.mylane.resources]\n'
                 'memory = "3g"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None, "R2_OVERCOMMIT": "1.0"})
    assert result.returncode == 75
    assert "expected_budget=3072 MiB" in result.stderr
    assert "expected 3 instance(s) x 1024 MiB = 3072 MiB" in result.stderr
    assert "declared_cap_total=9216 MiB" in result.stderr
    assert "cap_budget=4096 MiB" in result.stderr
    assert "declared caps exceed cap budget" in result.stderr
    assert argv == [] and not (home / "campaigns" / "camp-s1").exists()


def test_scaled_lane_ceiling_rounding_is_shown_as_ceil_arithmetic(tmp_path):
    result, *_ = fleet(
        tmp_path, ["--instances", "3", "--jobs", "1"], meminfo_kb=2 * 1024 * 1024,
        rg_toml=('[lanes.mylane]\nkind = "assay"\n[lanes.mylane.resources]\n'
                 'memory = "1000m"\n'),
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n',
        extra_env={"R2_INSTANCE_MEM_MIB": None, "R2_HOST_RESERVE_MIB": "0",
                   "R2_OVERCOMMIT": "3.0"})
    assert result.returncode == 0, result.stderr
    assert "lane_cap=1000 MiB" in result.stderr
    assert "scaled_lane_ceiling=334 MiB" in result.stderr
    assert "worker_peak=334 MiB" in result.stderr


def test_missing_ksm_sysfs_reports_unavailable_and_continues(tmp_path):
    result, *_ = fleet(
        tmp_path, ["--instances", "1"],
        extra_env={"R2_KSM_SYSFS": str(tmp_path / "missing-ksm")})
    assert result.returncode == 0, result.stderr
    assert ("KSM: run=unavailable pages_sharing=unavailable pages_shared=unavailable "
            "lane_eligibility=unverified") in result.stderr


def test_cpu_underuse_hint_names_instance_count_without_changing_jobs(tmp_path):
    result, _home, argv, *_ = fleet(
        tmp_path, ["--jobs", "1"],
        rg_toml='[lanes.mylane]\nkind = "assay"\njobs = 1\n',
        assay_toml='[lanes.mylane.judge.mutation]\njobs_max = 3\n')
    assert result.returncode == 0, result.stderr
    assert "CPU hint: 1 worker(s) per native R2 instance on 4 CPUs" in result.stderr
    assert "--instances 4" in result.stderr
    assert argv == [f"{COMMIT} proj mylane --jobs 1"]


def test_ksm_preflight_is_saved_for_status_and_reports_eligibility(tmp_path):
    ksm = tmp_path / "ksm"
    ksm.mkdir()
    (ksm / "run").write_text("1\n")
    (ksm / "pages_sharing").write_text("23\n")
    (ksm / "pages_shared").write_text("7\n")
    result, _home, _argv, runner, env = fleet(
        tmp_path, [], extra_env={"R2_KSM_SYSFS": str(ksm)})
    assert result.returncode == 0, result.stderr
    expected = ("KSM: run=1 pages_sharing=23 pages_shared=7 "
                "lane_eligibility=unverified (workloads must opt in with "
                "MADV_MERGEABLE or PR_SET_MEMORY_MERGE)")
    assert f"INFO: {expected}" in result.stderr
    status = subprocess.run(
        ["bash", str(runner / "run-r2-campaign.sh"), "status", "camp"],
        capture_output=True, text=True, env=env)
    assert status.returncode == 0, status.stderr
    assert f"  {expected}" in status.stdout


def test_carry_over_main_points_at_the_checkouts_own_store(tmp_path):
    result, _home, argv, *_ = fleet(tmp_path, ["--carry-over-main", "assay 12.2 upgrade"])
    assert result.returncode == 0, result.stderr
    assert argv == [
        f"{COMMIT} proj mylane --carry-over /workspaces/{COMMIT}/.run-gate/assay-state/proj "
        "--carry-over-reason assay 12.2 upgrade"]


@pytest.mark.parametrize("args", [
    ["--instances", "2", "--shard", "0/2"],
    ["--merge-only", "2", "--instances", "2"],
    ["--shard-total", "2"],
    ["--instances", "2", "--shard-total", "2", "--shard-offset", "1"],
    ["--instances", "17"],
    ["--instances", "x"],
    ["--instances="],
    ["--merge-only", "65"],
    # review: a zero or empty value is refused, never read as "absent"
    ["--instances", "0"],
    ["--instances=0"],
    ["--merge-only", "0"],
    ["--instances", "2", "--shard-total", "0"],
    ["--instances", "2", "--shard-total", "1"],
    ["--instances", "01"],
    ["--instances", "2", "--instances", "3"],
    ["--shard-offset", "0"],
    ["--carry-over-main", ""],
    ["--carry-over-main=   "],
    ["--carry-over-main", "two\nlines"],
])
def test_invalid_instance_options_are_refused(tmp_path, args):
    result, home, argv, *_ = fleet(tmp_path, args)
    assert result.returncode == 2, result.stderr
    assert argv == []
    assert not (home / "campaigns" / "camp-s1").exists()
    option = args[-2] if not args[-1].startswith("--") else args[-1]
    assert option.split("=")[0] in result.stderr, "the refusal names the option"


def test_an_instance_counts_only_its_own_progress_for_the_stall_guard(tmp_path):
    """Another instance's candidates must not hide a stalled one."""
    body = FLEET_LANE.replace(
        "printf '{\"event\":\"candidate\"}\\n' >> @ASSAY@/progress-$key.jsonl",
        "printf '{\"event\":\"candidate\"}\\n' >> @ASSAY@/progress-other.jsonl",
    )
    result, home, argv, *_ = fleet(
        tmp_path, ["--shard", "0/2"], extra_env={
            "FLEET_EXIT": "4", "R2_INSTANCE_ARTIFACT": "mylane.shard0of2"},
        lane_body=body)
    assert result.returncode == 4
    lines = (home / "campaigns" / "camp-s1" / "attempts.log").read_text().splitlines()
    assert "reason=stalled" in lines[-1]
    assert len(argv) == 2


def test_status_lists_the_fleet_and_each_instance(tmp_path):
    result, home, _argv, runner, env = fleet(tmp_path, ["--instances", "2"])
    assert result.returncode == 0, result.stderr
    status = subprocess.run(
        ["bash", str(runner / "run-r2-campaign.sh"), "status", "camp"],
        capture_output=True, text=True, env=env)
    assert status.returncode == 0, status.stderr
    lines = status.stdout.splitlines()
    assert lines[0].startswith("camp-s1: final_exit=0")
    assert any(line.startswith("camp.i1of2-s1: final_exit=0") and
               "completed_candidates=1" in line for line in lines)
    assert any(line.startswith("camp.merge-s1: final_exit=0") for line in lines)
    assert any("expected_peak_credit=0 MiB" in line for line in lines)
    missing = subprocess.run(
        ["bash", str(runner / "run-r2-campaign.sh"), "status", "nothing"],
        capture_output=True, text=True, env=env)
    assert missing.returncode == 1


SLEEPING_LANE = (
    'if [[ $* == *" --help" ]]; then printf "%s\\n" "$FAKE_RG_HELP"; exit 0; fi\n'
    "exec sleep 60\n")


def test_stop_of_a_fleet_stops_every_instance(tmp_path):
    home, runner, bindir, project = prepare(tmp_path)
    (runner / "run-lane.sh").write_text(SLEEPING_LANE)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 67108864 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "R2_INSTANCE_STAGGER_S": "0", "R2_MEMINFO": str(meminfo),
           "R2_INSTANCE_MEM_MIB": "1024", "FAKE_RG_HELP": NEW_RG_HELP}
    err_path = tmp_path / "fleet-stop.err"
    with err_path.open("w") as err:
        fleet_proc = subprocess.Popen(
            ["bash", str(runner / "run-r2-campaign.sh"), "camp-s1", COMMIT, "proj",
             "mylane", "--instances", "2"],
            stdout=subprocess.DEVNULL, stderr=err, env=env)
        try:
            locks = home / "locks"
            deadline = time.monotonic() + 20
            wanted = [locks / "camp.active", locks / "camp.i0of2.active",
                      locks / "camp.i1of2.active"]
            while time.monotonic() < deadline and not all(
                    path.exists() and "lane=" in path.read_text()
                    and path.read_text().split("lane=")[1].split("\n")[0]
                    for path in wanted[1:]):
                time.sleep(0.1)
            assert all(path.exists() for path in wanted), err_path.read_text()
            stop = subprocess.run(
                ["bash", str(runner / "run-r2-campaign.sh"), "stop", "camp"],
                capture_output=True, text=True, env=env, timeout=60)
            assert stop.returncode == 0, stop.stderr
            assert fleet_proc.wait(timeout=30) == 143
        finally:
            if fleet_proc.poll() is None:
                fleet_proc.kill()
    assert (home / "campaigns" / "camp-s1" / "final-exit").read_text() == "143\n"
    for child in ("camp.i0of2-s1", "camp.i1of2-s1"):
        assert (home / "campaigns" / child / "final-exit").read_text() == "143\n"
    assert not any((home / "locks").glob("*.active"))


def test_stop_reaches_the_instances_of_a_dead_fleet_supervisor(tmp_path):
    """The fleet process died (OOM, SIGKILL): stop still finds its children."""
    home, runner, bindir, project = prepare(tmp_path)
    (runner / "run-lane.sh").write_text(SLEEPING_LANE)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable: 67108864 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "R2_INSTANCE_STAGGER_S": "0", "R2_MEMINFO": str(meminfo),
           "R2_INSTANCE_MEM_MIB": "1024", "FAKE_RG_HELP": NEW_RG_HELP}
    err_path = tmp_path / "fleet-dead.err"
    locks = home / "locks"
    children = [locks / "camp.i0of2.active", locks / "camp.i1of2.active"]
    with err_path.open("w") as err:
        fleet_proc = subprocess.Popen(
            ["bash", str(runner / "run-r2-campaign.sh"), "camp-s1", COMMIT, "proj",
             "mylane", "--instances", "2"],
            stdout=subprocess.DEVNULL, stderr=err, env=env)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not all(
                    path.exists() and "lane=" in path.read_text()
                    and path.read_text().split("lane=")[1].split("\n")[0]
                    for path in children):
                time.sleep(0.1)
            assert all(path.exists() for path in children), err_path.read_text()
            fleet_proc.kill()
            fleet_proc.wait(timeout=10)
            stop = subprocess.run(
                ["bash", str(runner / "run-r2-campaign.sh"), "stop", "camp"],
                capture_output=True, text=True, env=env, timeout=60)
            # The fake Docker identifies no container, so stop reaches each
            # orphaned child and then fails loudly with the exact-name advice.
            assert stop.returncode == 1, stop.stderr
            assert "(campaign camp.i0of2-s1)" in stop.stderr
            assert "(campaign camp.i1of2-s1)" in stop.stderr
        finally:
            if fleet_proc.poll() is None:
                fleet_proc.kill()
    for child in ("camp.i0of2-s1", "camp.i1of2-s1"):
        assert (home / "campaigns" / child / "final-exit").read_text() == "143\n"
    assert not any(locks.glob("*.active"))


# --- review: uncapped memory, functional floor --------------------------------


def test_an_uncapped_lane_is_refused_without_a_per_instance_estimate(tmp_path):
    """Run-Gate applies no memory cap without resources.memory: no default."""
    result, home, argv, *_ = fleet(tmp_path, ["--instances", "2"],
                                   extra_env={"R2_INSTANCE_MEM_MIB": None,
                                              "R2_WORKER_MEM_MIB": "430"})
    assert result.returncode == 2
    assert "declares no resources.memory" in result.stderr
    assert "R2_INSTANCE_MEM_MIB" in result.stderr
    assert argv == [] and not (home / "campaigns" / "camp-s1").exists()


@pytest.mark.parametrize("env", [
    {"R2_INSTANCE_MEM_MIB": "99999999999999999999"},
    {"R2_INSTANCE_MEM_MIB": "0"},
    {"R2_HOST_RESERVE_MIB": "99999999999999999999"},
    {"R2_OVERCOMMIT": "0.5"},
    {"R2_OVERCOMMIT": "4.01"},
    {"R2_EXPECTED_PEAK_CREDIT_MIB": "-1"},
    {"R2_EXPECTED_PEAK_CREDIT_MIB": ""},
    {"R2_EXPECTED_PEAK_CREDIT_MIB": "10000000"},
])
def test_memory_guard_values_are_bounded(tmp_path, env):
    result, _home, argv, *_ = fleet(tmp_path, ["--instances", "2"], extra_env=env)
    assert result.returncode == 2
    assert ("below 10^7" in result.stderr or "R2_OVERCOMMIT" in result.stderr)
    assert argv == []


@pytest.mark.parametrize("args", [
    ["--instances", "2"], ["--merge-only", "2"],
    ["--carry-over-main", "assay 12.2 upgrade"],
])
def test_an_old_host_run_gate_is_refused_by_the_functional_floor(tmp_path, args):
    result, home, argv, *_ = fleet(tmp_path, args, extra_env={"FAKE_RG_HELP": OLD_RG_HELP})
    assert result.returncode == 2
    assert "functional floor" in result.stderr and "RG-265" in result.stderr
    assert "Assay >= 12.2.0" in result.stderr
    assert argv == [] and not (home / "campaigns" / "camp-s1").exists()


def test_an_unreadable_run_gate_help_is_infrastructure(tmp_path):
    result, home, argv, *_ = fleet(tmp_path, ["--instances", "2"],
                                   extra_env={"FAKE_RG_HELP_EXIT": "125"})
    assert result.returncode == 75
    assert "cannot read the host Run-Gate's --help" in result.stderr
    assert argv == []


def test_the_legacy_single_campaign_never_probes_and_runs_on_an_old_run_gate(tmp_path):
    result, _home, argv, *_ = fleet(tmp_path, ["--jobs", "2"],
                                    extra_env={"FAKE_RG_HELP": OLD_RG_HELP,
                                               "R2_INSTANCE_MEM_MIB": None})
    assert result.returncode == 0, result.stderr
    assert argv == [f"{COMMIT} proj mylane --jobs 2"]
    assert not (tmp_path / "argv.log.help").exists()


def test_a_fleet_probes_the_run_gate_once(tmp_path):
    result, _home, _argv, *_ = fleet(tmp_path, ["--instances", "2"])
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "argv.log.help").read_text().splitlines() == [f"{COMMIT} proj --help"]


@pytest.mark.parametrize("args,option", [
    (["--instances", "0"], "--instances"),
    (["--merge-only", "0"], "--merge-only"),
    (["--instances", "2", "--shard-total", "0"], "--shard-total"),
    (["--shard-total", "0"], "--shard-total"),
])
def test_a_zero_value_is_refused_by_its_own_option(tmp_path, args, option):
    result, home, argv, *_ = fleet(tmp_path, args)
    assert result.returncode == 2
    assert f"{option} must be a decimal integer" in result.stderr
    assert argv == []


def test_a_run_gate_without_shard_is_below_the_floor(tmp_path):
    """Review round 2: strict merge + carry-over but no --shard K/N."""
    no_shard = "\n".join(line for line in NEW_RG_HELP.splitlines()
                         if "--shard K/N" not in line)
    assert "STRICT" in no_shard and "--carry-over DIR" in no_shard
    result, home, argv, *_ = fleet(tmp_path, ["--instances", "2"],
                                   extra_env={"FAKE_RG_HELP": no_shard})
    assert result.returncode == 2 and "functional floor" in result.stderr
    assert argv == [] and not (home / "campaigns" / "camp-s1").exists()


def test_a_run_gate_with_a_permissive_merge_is_below_the_floor(tmp_path):
    """A pre-review Run-Gate listed --merge-shards without the STRICT merge."""
    permissive = NEW_RG_HELP.replace("STRICT whole-lane merge", "merge shard stores")
    result, home, argv, *_ = fleet(tmp_path, ["--instances", "2"],
                                   extra_env={"FAKE_RG_HELP": permissive})
    assert result.returncode == 2 and "functional floor" in result.stderr
    assert argv == []
