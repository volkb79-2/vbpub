"""run-r2-campaign.sh run for real in bash against a stub lib.sh and a stub run-lane.sh: the DST-37 --jobs clamp and the campaign verdict.json (the lane's own Assay verdict)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "runner" / "run-r2-campaign.sh"
COMMIT = "a" * 40


def campaign(tmp_path, args, jobs_max=None, exits=("0",), assay=True, write="default", rg_toml=None, jobs_key="jobs_max"):
    home = tmp_path / "home"
    runner = tmp_path / "runner"
    runner.mkdir()
    shutil.copy(SCRIPT, runner / "run-r2-campaign.sh")
    (runner / "lib.sh").write_text(f"identity_check() {{ :; }}\nRUNNER_HOME={home}\nGATES_SLICE=none\n")
    if write == "default":  # a lane that behaves like run-gate: leaves its own verdict in the checkout's .assay/
        write = f"mkdir -p {home}/checkouts/{COMMIT}/proj/.assay; printf '{{\"outcome\": \"PASS\"}}' > {home}/checkouts/{COMMIT}/proj/.assay/verdict-mylane.json"
    seq = tmp_path / "seq"
    seq.write_text("\n".join(exits) + "\n")
    # the stub lane records its argv and exits with the next code of the sequence
    (runner / "run-lane.sh").write_text(
        f'printf "%s\\n" "$*" >> {tmp_path}/argv.log\n{write or ":"}\ncode=$(head -n1 {seq}); tail -n +2 {seq} > {seq}.n; mv {seq}.n {seq}\nexit "${{code:-0}}"\n')
    proj = home / "checkouts" / COMMIT / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    if assay:
        mut = f"[lanes.mylane.judge.mutation]\n{jobs_key} = {jobs_max}\n" if jobs_max is not None else "[lanes.mylane.judge]\nfail_under = 1\n"
        (proj / "assay.toml").write_text(mut)
    if rg_toml is not None:
        (proj / "run-gate.toml").write_text(rg_toml)
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


VERDICT = b'{"outcome": "PASS", "z": 1,  "sig": "x"}\n\x00 raw bytes'


def campaign_with_verdict(tmp_path, exits, verdict=VERDICT, name="verdict-mylane.json", rg_toml=""):
    """Run the campaign with a stub lane that writes the lane's own verdict file into the checkout's .assay/ like run-gate does."""
    write = False
    if verdict is not None:
        src = tmp_path / "verdict.src"
        src.write_bytes(verdict)
        out = tmp_path / "home" / "checkouts" / COMMIT / "proj" / ".assay"
        write = f"mkdir -p {out}; cp {src} {out}/{name}"  # during the supervised run, so fresher than the start marker
    return campaign(tmp_path, [], exits=exits, write=write, rg_toml=rg_toml)


def test_verdict_json_is_the_lanes_own_assay_verdict_byte_for_byte(tmp_path):
    r, d, _ = campaign_with_verdict(tmp_path, ("0",))
    assert r.returncode == 0, r.stderr
    assert (d / "verdict.json").read_bytes() == VERDICT
    s = json.loads((d / "campaign-summary.json").read_text())
    assert (s["campaign"], s["lane"], s["commit"], s["project"], s["attempts"], s["final_exit"], s["result"]) == ("camp1", "mylane", COMMIT, "proj", 1, 0, "PASS")
    assert s["finished_utc"].endswith("Z") and (d / "final-exit").read_text().strip() == "0"


def test_verdict_json_follows_a_declared_assay_lane_name(tmp_path):
    r, d, _ = campaign_with_verdict(tmp_path, ("0",), name="verdict-other.json", rg_toml='[lanes.mylane]\nkind = "assay"\nassay_lane = "other"\n')
    assert r.returncode == 0, r.stderr
    assert (d / "verdict.json").read_bytes() == VERDICT


def test_a_failing_campaign_still_publishes_the_real_fail_verdict(tmp_path):
    r, d, argv = campaign_with_verdict(tmp_path, ("4", "1"), verdict=b'{"outcome": "FAIL"}\n')
    assert r.returncode == 1 and len(argv) == 2
    assert (d / "verdict.json").read_bytes() == b'{"outcome": "FAIL"}\n'
    s = json.loads((d / "campaign-summary.json").read_text())
    assert (s["attempts"], s["final_exit"], s["result"]) == (2, 1, "NOT_PASS")


def test_exit_zero_without_a_verdict_fails_loudly_and_writes_none(tmp_path):
    r, d, _ = campaign_with_verdict(tmp_path, ("0",), verdict=None)
    assert r.returncode == 70 and "wrote no fresh Assay verdict" in r.stderr
    assert not (d / "verdict.json").exists() and "verdict_missing=" in (d / "metadata.txt").read_text()
    assert (d / "final-exit").read_text().strip() == "0"


def test_a_stale_verdict_from_before_this_supervision_is_not_published(tmp_path):
    ck = tmp_path / "home" / "checkouts" / COMMIT / "proj" / ".assay"
    ck.mkdir(parents=True)
    (ck / "verdict-mylane.json").write_bytes(b"stale")
    r, d, _ = campaign(tmp_path, [], exits=("0",), write=False)
    assert r.returncode == 70 and not (d / "verdict.json").exists()


def test_a_failed_campaign_without_a_verdict_keeps_its_exit_code(tmp_path):
    r, d, _ = campaign_with_verdict(tmp_path, ("3",), verdict=None)
    assert r.returncode == 3 and not (d / "verdict.json").exists()
    assert "verdict_missing=" in (d / "metadata.txt").read_text()


def test_the_jobs_alias_is_honoured(tmp_path):
    r, _, argv = campaign(tmp_path, ["--jobs", "6"], jobs_max=2, jobs_key="jobs")
    assert r.returncode == 0, r.stderr
    assert argv == [f"{COMMIT} proj mylane --jobs 2"]
