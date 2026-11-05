"""run-r2-campaign.sh run for real in bash against a stub lib.sh and a stub run-lane.sh: the DST-37 --jobs clamp and the campaign verdict.json."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "runner" / "run-r2-campaign.sh"
COMMIT = "a" * 40


def campaign(tmp_path, args, jobs_max=None, exits=("0",), assay=True):
    home = tmp_path / "home"
    runner = tmp_path / "runner"
    runner.mkdir()
    shutil.copy(SCRIPT, runner / "run-r2-campaign.sh")
    (runner / "lib.sh").write_text(f"identity_check() {{ :; }}\nRUNNER_HOME={home}\nGATES_SLICE=none\n")
    seq = tmp_path / "seq"
    seq.write_text("\n".join(exits) + "\n")
    # the stub lane records its argv and exits with the next code of the sequence
    (runner / "run-lane.sh").write_text(
        f'printf "%s\\n" "$*" >> {tmp_path}/argv.log\ncode=$(head -n1 {seq}); tail -n +2 {seq} > {seq}.n; mv {seq}.n {seq}\nexit "${{code:-0}}"\n')
    proj = home / "checkouts" / COMMIT / "proj"
    proj.mkdir(parents=True)
    if assay:
        mut = f"[lanes.mylane.judge.mutation]\njobs_max = {jobs_max}\n" if jobs_max is not None else "[lanes.mylane.judge]\nfail_under = 1\n"
        (proj / "assay.toml").write_text(mut)
    # output goes to files, not pipes: the script's 10 s sampler leaves a `sleep` behind that would hold a pipe open
    with open(tmp_path / "err.log", "w") as err:
        r = subprocess.run(["bash", str(runner / "run-r2-campaign.sh"), "camp1", COMMIT, "proj", "mylane", *args], stdout=subprocess.DEVNULL, stderr=err,
                           env={**os.environ, "R2_MAX_ATTEMPTS": "4"})
    r.stderr = (tmp_path / "err.log").read_text()
    return r, home / "campaigns" / "camp1", (tmp_path / "argv.log").read_text().splitlines()


@pytest.mark.parametrize("args,expect", [
    (["--jobs", "6"], "--jobs 3"),
    (["--jobs=6"], "--jobs=3"),
    (["--jobs", "2"], "--jobs 2"),
    (["--jobs=3", "--progress"], "--jobs=3 --progress"),
    (["--progress"], "--progress"),
])
def test_jobs_above_the_lane_jobs_max_are_clamped(tmp_path, args, expect):
    r, _, argv = campaign(tmp_path, args, jobs_max=3)
    assert r.returncode == 0, r.stderr
    assert argv == [f"{COMMIT} proj mylane {expect}"]
    assert ("clamping" in r.stderr) == (expect != " ".join(args) and "--jobs" in " ".join(args))


@pytest.mark.parametrize("kw", [{"jobs_max": None}, {"jobs_max": 3, "assay": False}])
def test_no_jobs_max_or_no_assay_toml_leaves_the_arguments_alone(tmp_path, kw):
    r, _, argv = campaign(tmp_path, ["--jobs", "9"], **kw)
    assert r.returncode == 0, r.stderr
    assert argv == [f"{COMMIT} proj mylane --jobs 9"]


def test_verdict_json_summarises_a_passing_campaign(tmp_path):
    r, d, _ = campaign(tmp_path, [], exits=("0",))
    v = json.loads((d / "verdict.json").read_text())
    assert (v["campaign"], v["lane"], v["commit"], v["project"], v["attempts"], v["final_exit"], v["verdict"]) == ("camp1", "mylane", COMMIT, "proj", 1, 0, "PASS")
    assert v["finished_utc"].endswith("Z") and (d / "final-exit").read_text().strip() == "0"


def test_verdict_json_after_budget_exits_records_attempts_and_a_non_pass(tmp_path):
    r, d, argv = campaign(tmp_path, [], exits=("4", "1"))
    assert r.returncode == 1 and len(argv) == 2
    v = json.loads((d / "verdict.json").read_text())
    assert (v["attempts"], v["final_exit"], v["verdict"]) == (2, 1, "NOT_PASS")
