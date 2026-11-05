"""Hermetic tests for the lane-declaration seams of runner/remote-gate.py: a local `main` ref on the host checkout, local refusal of
`--base` for a lane whose assay config declares its base, local refusal of exec-mode lanes without a container, the final-line reason
and the run-gate floor. Real git and bash where the behaviour is a git/bash one; no network, no host."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from test_remote_gate import SEED, SHA, ctx_for, git_handlers, hosts, psi, rg, run_args  # noqa: F401

LS = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def sh(*a, cwd):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True, env=GIT_ENV).stdout.strip()


# ---- (1) a local main ref on the host checkout ----

def test_local_state_records_the_local_main_sha(tmp_path):
    main = "e" * 40
    ctx, _ = ctx_for(tmp_path, git_handlers() + [("for-each-ref", rg.Result(0, main + "\n"))])
    assert rg.local_state(ctx, "/wt", None)["main"] == main
    ctx, _ = ctx_for(tmp_path, git_handlers() + [("for-each-ref", rg.Result(0, ""))])  # no local main branch: nothing to ship
    assert rg.local_state(ctx, "/wt", None)["main"] is None


def test_main_rides_the_bundle_and_forces_one_when_the_seed_lacks_it(tmp_path):
    main = "e" * 40

    def count(argv):  # HEAD is covered by the seed, main is not
        return rg.Result(0, ("2" if main in argv else "0") + "\n")

    ctx, _ = ctx_for(tmp_path, [("for-each-ref", rg.Result(0, main + "\n")), ("rev-list --count", count)] + git_handlers() + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "m1")) == 0
    bundle = next(c[0] for c in ctx.runner.calls if "bundle" in c[0] and "create" in c[0])
    assert "refs/heads/main" in bundle and f"^{SEED}" in bundle
    assert f"MAIN_SHA={main}\n" in (tmp_path / "out" / "m1" / "job.sh").read_text()


def test_no_local_main_means_an_empty_main_sha_and_no_main_ref(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "m2")) == 0
    bundle = next(c[0] for c in ctx.runner.calls if "bundle" in c[0] and "create" in c[0])
    assert "refs/heads/main" not in bundle
    assert "MAIN_SHA=''\n" in (tmp_path / "out" / "m2" / "job.sh").read_text()


def test_real_git_and_bash_the_host_checkout_gets_a_main_branch_without_checking_it_out(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=src)
    sh("git", "commit", "-q", "--allow-empty", "-m", "seed", cwd=src)
    seed = sh("git", "rev-parse", "HEAD", cwd=src)
    host = tmp_path / "host"
    sh("git", "clone", "-q", "--no-checkout", str(src), str(host), cwd=tmp_path)
    sh("git", "checkout", "-q", "-b", "feature", cwd=src)
    sh("git", "commit", "-q", "--allow-empty", "-m", "feature work", cwd=src)
    head = sh("git", "rev-parse", "HEAD", cwd=src)
    sh("git", "checkout", "-q", "main", cwd=src)
    sh("git", "commit", "-q", "--allow-empty", "-m", "main moved on", cwd=src)
    main = sh("git", "rev-parse", "HEAD", cwd=src)
    sh("git", "checkout", "-q", "feature", cwd=src)  # the worktree is on the feature branch; main is never checked out
    ctx = rg.Ctx(runner=rg.Runner(), hosts=hosts(), out_dir=tmp_path / "out")
    st = {"top": str(src), "sha": head, "base": None, "main": main, "tags": []}
    dest = tmp_path / "rgdir" / "in.bundle"
    dest.parent.mkdir()
    assert rg.build_bundle(ctx, st, [seed], str(dest)) == seed
    job = rg.render_job(hosts()["v1001"], "t8", st, "suite", ".", "", 60, [])
    fetch = job[job.index("if [[ -f $D/in.bundle ]]"):job.index("# every release tag of the source")]
    setref = job[job.index("if [[ -n $MAIN_SHA ]]; then\n  # a local"):job.index("if (( ADMISSION_CHECK ))")]
    script = (f'D={dest.parent}\nCKDIR={host}\nBASE_SHA=\nMAIN_SHA={main}\nlog() {{ echo "$@" >&2; }}\n' + fetch
              + f"git -C {host} checkout -q --detach {head} 2>/dev/null || git -C {host} update-ref --no-deref HEAD {head}\n" + setref)
    r = subprocess.run(["bash", "-euo", "pipefail", "-c", script], capture_output=True, text=True, env=GIT_ENV)
    assert r.returncode == 0, r.stderr
    assert sh("git", "rev-parse", "main", cwd=host) == main
    assert "local main ->" in r.stderr


def test_the_main_step_of_the_job_reports_a_missing_commit_instead_of_failing(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    sh("git", "init", "-q", "-b", "x", cwd=repo)
    job = rg.render_job(hosts()["v1001"], "t9", {"sha": SHA, "base": None, "main": "f" * 40, "tags": []}, "suite", ".", "", 60, [])
    setref = job[job.index("if [[ -n $MAIN_SHA ]]; then\n  # a local"):job.index("if (( ADMISSION_CHECK ))")]
    r = subprocess.run(["bash", "-euo", "pipefail", "-c", f'CKDIR={repo}\nMAIN_SHA={"f" * 40}\nlog() {{ echo "$@" >&2; }}\n' + setref],
                       capture_output=True, text=True, env=GIT_ENV)
    assert r.returncode == 0 and "is missing on the host" in r.stderr


# ---- (2) a declared assay base refuses --base locally ----

def tree(tmp_path, run_gate, assay=None, root=None, proj="p"):
    top = tmp_path / "wt"
    (top / proj).mkdir(parents=True)
    (top / proj / "run-gate.toml").write_text(run_gate)
    if assay is not None:
        (top / proj / "assay.toml").write_text(assay)
    if root is not None:
        (top / "run-gate.root.toml").write_text(root)
    return {"top": str(top)}


RG_ASSAY = '[lanes.assay]\nkind = "assay"\nassay_lane = "cmru"\n'


@pytest.mark.parametrize("judge", ['base = "cmru-v6.1.0"\n', 'base = "x"\nbase_source = "declared"\n', 'base_source = "declared"\n'])
def test_base_with_a_declared_assay_base_is_refused_before_any_host_is_contacted(tmp_path, judge):
    st = tree(tmp_path, RG_ASSAY, f"[lanes.cmru.judge]\n{judge}")
    ctx, _ = ctx_for(tmp_path, [])
    with pytest.raises(rg.GateError, match="declares its comparison base") as e:
        rg.lane_checks(ctx, st, "p", "assay", True, False)
    assert e.value.code == 2 and "drop --base" in str(e.value)
    assert ctx.runner.calls == []


@pytest.mark.parametrize("judge,given", [('base_source = "request"\n', True), ('base = "x"\nbase_source = "request"\n', True), ('base = "x"\n', False),
                                         ("fail_under = 1\n", True)])
def test_base_is_allowed_for_a_request_lane_or_when_not_given(tmp_path, judge, given):
    st = tree(tmp_path, RG_ASSAY, f"[lanes.cmru.judge]\n{judge}")
    ctx, out = ctx_for(tmp_path, [])
    rg.lane_checks(ctx, st, "p", "assay", given, False)
    assert out == []


def test_assay_lane_name_defaults_to_the_run_gate_lane_name(tmp_path):
    st = tree(tmp_path, '[lanes.cmru]\nkind = "assay"\n', '[lanes.cmru.judge]\nbase = "x"\n')
    with pytest.raises(rg.GateError, match="declares its comparison base"):
        rg.lane_checks(ctx_for(tmp_path, [])[0], st, "p", "cmru", True, False)


def test_cmd_run_refuses_a_declared_base_lane_before_contacting_a_host(tmp_path):
    top = tree(tmp_path, RG_ASSAY, '[lanes.cmru.judge]\nbase = "x"\n')["top"]
    handlers = [("--show-toplevel", rg.Result(0, top + "\n")), ("--show-prefix", rg.Result(0, "p/\n"))] + git_handlers()
    ctx, _ = ctx_for(tmp_path, handlers)
    with pytest.raises(rg.GateError, match="declares its comparison base"):
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "assay", "--base", "main", "--name", "d1"))
    assert not any(c[0][:1] in (["ssh"], ["apicheck"]) for c in ctx.runner.calls)
    assert not (tmp_path / "out" / "d1").exists()


def test_unknown_lane_is_a_warning_not_a_refusal(tmp_path):
    st = tree(tmp_path, RG_ASSAY)
    ctx, out = ctx_for(tmp_path, [])
    rg.lane_checks(ctx, st, "p", "nope", False, False)
    assert len(out) == 1 and "'nope' is not declared" in out[0]


def test_missing_or_broken_config_gives_no_opinion(tmp_path):
    ctx, out = ctx_for(tmp_path, [])
    rg.lane_checks(ctx, {"top": str(tmp_path / "absent")}, "p", "assay", True, False)
    st = tree(tmp_path, "this is [not toml")
    rg.lane_checks(ctx, st, "p", "assay", True, False)
    assert out == []


# ---- exec-mode lanes ----

def test_exec_mode_lane_without_container_name_is_refused(tmp_path):
    st = tree(tmp_path, '[environments.test-runner]\nmode = "exec"\n[lanes.l1]\nkind = "command"\nenvironment = "test-runner"\n')
    ctx, _ = ctx_for(tmp_path, [])
    with pytest.raises(rg.GateError, match="exec-mode environment 'test-runner'") as e:
        rg.lane_checks(ctx, st, "p", "l1", False, False)
    assert e.value.code == 2 and "--allow-exec-env" in str(e.value)
    rg.lane_checks(ctx, st, "p", "l1", False, True)  # explicit opt-in


def test_exec_mode_with_container_name_or_ephemeral_modes_pass(tmp_path):
    cfg = ('[environments.fixed]\nmode = "exec"\ncontainer_name = "c"\n[environments.eph]\nmode = "ephemeral"\n'
           '[lanes.a]\nenvironment = "fixed"\n[lanes.b]\nenvironment = "eph"\n[lanes.c]\nkind = "command"\n')
    st = tree(tmp_path, cfg)
    ctx, _ = ctx_for(tmp_path, [])
    for lane in "abc":
        rg.lane_checks(ctx, st, "p", lane, False, False)


def test_exec_environment_from_the_central_root_config_counts(tmp_path):
    st = tree(tmp_path, '[lanes.l1]\nenvironment = "shared"\n', root='[environments.shared]\nmode = "exec"\n')
    with pytest.raises(rg.GateError, match="exec-mode environment 'shared'"):
        rg.lane_checks(ctx_for(tmp_path, [])[0], st, "p", "l1", False, False)


# ---- (3) the reason on the final line ----

CMRU = ("run-gate: WARN: main-checkout opt-in\n"
        "run-gate: --base 'df31' was given but assay lane 'cmru' does not delegate its comparison base: it declares base_source 'declared', "
        "so assay would refuse --request-base. Set judge.base_source = \"request\" in assay.toml, or drop --base\n"
        "run-gate: lane 'assay' verdict ERROR; reason --base 'df31' was given but assay lane 'cmru' does not delegate its comparison base: "
        "it declares base_source 'declared', so assay would refuse --request-base. Set judge.base_source = \"request\" in assay.toml, or drop --base\n")
EXEC = ("run-gate: lane 'inf_appconfig_r1' verdict ERROR; reason exec-mode environment 'test-runner' needs a declared container_name or this "
        "worktree's rendered /w/ciu.global.toml with [deploy]; start this worktree's own test-runner\n")
SELFQ = ("run-gate: 24.3.0 | lane self-qualification-provisional\n"
         "self-qualification-container: the selected worktree is not inside the declared /workspaces/vbpub bind\n"
         "run-gate: artifact: /w/.assay/verdict.json\n"
         "run-gate: lane 'self-qualification-provisional' verdict ERROR; exit_code 3; reason command-exit-map; log /tmp/run-gate/lanes/x.log\n")


def test_reason_of_the_three_observed_silent_errors():
    r = rg.reason_of(CMRU, "", "ERROR", 2)
    assert "does not delegate its comparison base" in r and "rerun without --base" in r and "\n" not in r
    r = rg.reason_of(EXEC, "", "ERROR", 2)
    assert r.startswith("exec-mode environment 'test-runner' needs a declared container_name") and "does not start" in r
    r = rg.reason_of(SELFQ, "", "ERROR", 2)
    assert r.startswith("command-exit-map | self-qualification-container: the selected worktree is not inside") and "NC-14" in r


def test_reason_of_has_nothing_to_say_for_pass_fail_cancel_and_success():
    assert [rg.reason_of(CMRU, "", v, 2) for v in ("PASS", "FAIL", "CANCELLED")] == ["", "", ""]
    assert rg.reason_of(CMRU, "", "ERROR", 0) == ""


def test_reason_of_falls_back_to_the_job_log_then_to_a_pointer():
    job = ("2026-11-05T00:00:00Z PSI ok\n2026-11-05T00:00:01Z PREREQ MISSING: no run-gate >= 25.0.1 in the cockpit (tried: a=24.3.0)\n"
           "2026-11-05T00:00:02Z done rc=76\n")
    assert rg.reason_of("", job, "PREREQ_MISSING", 76) == "PREREQ MISSING: no run-gate >= 25.0.1 in the cockpit (tried: a=24.3.0)"
    assert rg.reason_of("", "", "UNKNOWN", 70) == "no lane output (job exit 70); see job.log"
    assert len(rg.reason_of("run-gate: lane 'x' verdict ERROR; reason " + "y" * 900 + "\n", "", "ERROR", 2)) <= 400


def test_collect_prints_and_records_the_reason(tmp_path):
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "assay", "--detach", "--name", "r1"))
    ctx.runner.handlers.insert(0, ("rg/r1/lane.log", rg.Result(0, CMRU)))
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(0, "2\n")))
    ctx.runner.handlers.insert(0, ("/rc", rg.Result(0, "2\n")))
    host, meta, run_dir = rg.load_meta(ctx, "r1")
    assert rg.collect(ctx, host, meta, run_dir) == 2
    assert out[-1].startswith("lane assay verdict ERROR; exit_code 2; reason --base ")
    assert "reason" in json.loads((run_dir / "result.json").read_text())


# ---- (3) run-gate floor ----

def test_vbpub_profile_has_a_run_gate_floor_and_the_venv_fallback():
    prof = hosts()["r1002"].profile("vbpub")
    assert prof.run_gate_min == "25.0.1"
    assert prof.run_gate_bins == [rg.DEFAULT_RG_BIN, "/home/vscode/.venvs/gate-tools2502/bin/run-gate"]


def test_min_run_gate_flag_overrides_the_floor_and_is_validated(tmp_path):
    seen = []

    def spy(argv):
        seen.append(argv[-1])
        return rg.Result(0, "OK /home/vscode/.venvs/gate-tools2502/bin/run-gate 25.0.2\n")

    ctx, _ = ctx_for(tmp_path, [("check_prereqs || true", spy)] + git_handlers() + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "f1", "--min-run-gate", "25.1.0")) == 0
    assert "RG_MIN=25.1.0" in seen[0]
    with pytest.raises(rg.GateError, match="not a dotted version"):
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "f2", "--min-run-gate", "x;rm"))


def test_unmet_floor_fails_before_upload_with_the_versions_seen(tmp_path):
    msg = "PREREQ no run-gate >= 25.0.1 in the cockpit (tried: /home/vscode/.local/bin/run-gate=24.3.0)\n"
    ctx, _ = ctx_for(tmp_path, [("check_prereqs || true", rg.Result(0, msg))] + git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match="24.3.0") as e:
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "f3"))
    assert e.value.code == 4 and "setup-cockpit-run-gate.sh" in str(e.value)
    assert not any("run-job" in " ".join(c[0]) for c in ctx.runner.calls)
