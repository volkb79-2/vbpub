"""Hermetic tests for the repo-aware part of runner/remote-gate.py: repository name, per-repo prerequisite profiles, the
preflight, the generated job script run in real bash against a fake `docker`. No network, no host, no real container."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from test_remote_gate import PREFLIGHT_OK, SHA, SEED, Fake, ctx_for, git_handlers, hosts, psi, rg, run_args  # noqa: F401

LS = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\nvbpub\ndstdns\n"))


# ---- repository name ----

def test_repo_name_is_the_main_checkout_directory_of_a_linked_worktree(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers(repo="dstdns"))
    assert rg.repo_name(ctx, "/ws/dstdns/.worktrees/x", None) == "dstdns"


def test_repo_name_is_never_guessed_from_the_worktree_directory(tmp_path):
    ctx, _ = ctx_for(tmp_path, [("--git-common-dir", rg.Result(0, "/srv/x/bare.git\n"))])
    with pytest.raises(rg.GateError, match="pass --repo NAME") as e:
        rg.repo_name(ctx, "/home/me/myrepo", None)
    assert e.value.code == 2  # a wrong guess would select the empty profile and skip every prerequisite check
    assert rg.repo_name(ctx, "/home/me/myrepo", "other") == "other"


def test_odd_main_checkout_directory_name_is_refused_without_repo(tmp_path):
    ctx, _ = ctx_for(tmp_path, [("--git-common-dir", rg.Result(0, "/ws/9-main/.git\n"))])
    with pytest.raises(rg.GateError, match="--repo") as e:
        rg.repo_name(ctx, "/ws/9-main/.worktrees/x", None)
    assert e.value.code == 2
    ctx, _ = ctx_for(tmp_path, git_handlers(repo="dstdns") + [psi(1, 1), LS])
    with pytest.raises(rg.GateError):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--name", "nr1", "--repo", "9bad"))
    assert not (tmp_path / "out" / "nr1").exists()


@pytest.mark.parametrize("bad", ["", "9lives", "-x", "a/b", "x" * 60, "a" * 40, "foo.git", "has space"])
def test_invalid_repo_names_are_refused(bad):
    with pytest.raises(rg.GateError):
        rg.check_repo_name(bad)


def test_unusable_directory_name_asks_for_repo_flag(tmp_path):
    ctx, _ = ctx_for(tmp_path, [("--git-common-dir", rg.Result(0, ""))])
    with pytest.raises(rg.GateError, match="--repo"):
        rg.repo_name(ctx, "/home/me/9bad", None)


# ---- profiles ----

def test_unknown_repo_gets_the_empty_profile_and_dstdns_gets_its_prerequisites():
    h = hosts()["r1002"]
    p = h.profile("anything")
    assert (p.run_gate_min, p.required_images, p.uid_check, p.admission_check, p.run_gate_bins) == ("", [], None, False, [rg.DEFAULT_RG_BIN])
    d = h.profile("dstdns")
    assert d.run_gate_min == "25.0.1" and d.required_images == ["dstdns/test-runner:local"]
    assert d.uid_check == {"image": "dstdns/test-runner:local", "uid": 1003} and d.admission_check is True
    assert d.run_gate_bins[0] == rg.DEFAULT_RG_BIN and d.run_gate_bins[-1].endswith("/gate-tools2502/bin/run-gate")


def test_host_table_overrides_a_builtin_profile_key_by_key():
    h = hosts()["r1002"]
    h.repos = {"dstdns": {"tester_image": "tester-unified:r25-abc", "run_gate_bins": ["/opt/rg/bin/run-gate"], "env": {"FOO": "a b"},
                          "admission_check": False}}
    d = h.profile("dstdns")
    assert d.tester_image == "tester-unified:r25-abc" and d.run_gate_bins == ["/opt/rg/bin/run-gate"] and d.env == {"FOO": "a b"}
    assert d.run_gate_min == "25.0.1" and d.admission_check is False  # untouched keys keep the built-in value


@pytest.mark.parametrize("row", [
    {"nonsense": 1},
    {"run_gate_min": "latest"},
    {"run_gate_bins": []},
    {"run_gate_bins": ["relative/run-gate"]},
    {"run_gate_bins": "/x"},
    {"required_images": "img"},
    {"required_images": ["bad image"]},
    {"uid_check": {"image": "x"}},
    {"uid_check": {"image": "x", "uid": "1003"}},
    {"uid_check": {"image": "x", "uid": True}},
    {"uid_check": {"image": "x", "uid": -1}},
    {"tester_image": "bad image"},
    {"env": {"lower": "x"}},
    {"env": {"OK": 1}},
    {"env": ["A"]},
    {"env": {"RUN_GATE_ALLOW_MAIN_CHECKOUT": "0"}},
    {"env": {"RUN_GATE_ALLOW_ANYTHING": "1"}},
    {"env": {"ASSAY_GATE_HOSTS_REPO_ROOT": "/"}},
    {"env": {"ASSAY_GATE_HOST_REPO_ROOT": "/"}},
    {"env": {"RUN_GATE_PROFILE": "on"}},
    {"env": {"PATH": "/tmp"}},
])
def test_invalid_profiles_are_refused(row):
    with pytest.raises(rg.GateError):
        rg.build_profile("x", row)


def test_hosts_file_repos_are_validated_at_load_time(tmp_path):
    base = {"ssh": ["ssh"], "api_check": ["x"], "guard": "true"}
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"hosts": {"r1002": {**base, "repos": {"dstdns": {"run_gate_min": "25.0.2"}}}}}))
    assert rg.load_hosts(str(f))["r1002"].profile("dstdns").run_gate_min == "25.0.2"
    f.write_text(json.dumps({"hosts": {"r1002": {**base, "repos": {"dstdns": {"typo_key": 1}}}}}))
    with pytest.raises(rg.GateError):
        rg.load_hosts(str(f))
    f.write_text(json.dumps({"hosts": {"r1002": {**base, "repos": {"../evil": {}}}}}))
    with pytest.raises(rg.GateError):
        rg.load_hosts(str(f))


def test_allowlist_still_holds_for_every_repo(tmp_path):
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"hosts": {"nano1": {"ssh": ["ssh"], "api_check": ["x"], "guard": "true", "repos": {"dstdns": {}}}}}))
    with pytest.raises(rg.GateError, match="only r1002, v1001"):
        rg.load_hosts(str(f))
    assert rg.ALLOWED_HOSTS == ("r1002", "v1001")


# ---- cmd_run for another repository ----

def test_dstdns_worktree_is_checked_out_under_its_own_repo_directory(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers(repo="dstdns") + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "run-gate-docs", "--detach", "--name", "d1", "--project-dir", ".")) == 0
    job = (tmp_path / "out" / "d1" / "job.sh").read_text()
    assert "REPO=dstdns\n" in job and "@@" not in job
    assert "RG_MIN=25.0.1\n" in job and "REQ_IMAGES=(dstdns/test-runner:local)\n" in job and "ADMISSION_CHECK=1\n" in job
    assert "UID_IMAGE=dstdns/test-runner:local\n" in job and "UID_NUM=1003\n" in job
    meta = json.loads((tmp_path / "out" / "d1" / "meta.json").read_text())
    assert meta["repo"] == "dstdns" and meta["run_gate"] == "/home/vscode/.local/bin/run-gate" and meta["project_dir"] == "."
    flat = [" ".join(c[0]) for c in ctx.runner.calls]
    pre = next(i for i, c in enumerate(flat) if "check_prereqs || true" in c)
    first_write = next(i for i, c in enumerate(flat) if "mkdir -p -m 0700" in c)
    assert pre < first_write  # prerequisites are checked before anything is created on the host


def test_repo_flag_overrides_the_derived_name(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers(repo="dstdns") + [psi(1, 1), LS])
    rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "d2", "--repo", "someother"))
    assert "REPO=someother\n" in (tmp_path / "out" / "d2" / "job.sh").read_text()


def test_missing_prerequisites_stop_before_any_upload_with_a_clear_error(tmp_path):
    msg = "PREREQ docker image dstdns/test-runner:local is not present on this host\nPREREQ no run-gate >= 25.0.1 in the cockpit (tried: /x=24.3.0)\n"
    handlers = git_handlers(repo="dstdns") + [psi(1, 1), LS, ("check_prereqs || true", rg.Result(0, msg))]
    ctx, _ = ctx_for(tmp_path, handlers)
    with pytest.raises(rg.GateError) as e:
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "run-gate-docs", "--detach", "--name", "d3"))
    assert e.value.code == 4
    text = str(e.value)
    assert "r1002 does not meet the prerequisites of repo dstdns" in text and "dstdns/test-runner:local is not present" in text
    assert "no run-gate >= 25.0.1" in text and "Per-repo prerequisites" in text
    flat = " ".join(" ".join(c[0]) for c in ctx.runner.calls)
    assert "mkdir -p" not in flat and "in.bundle" not in flat and "run-job" not in flat
    assert not (tmp_path / "out" / "d3").exists()


def test_preflight_transport_failure_and_silent_host_are_errors(tmp_path):
    h = hosts()["r1002"]
    prof = h.profile("dstdns")
    ctx, _ = ctx_for(tmp_path, [("check_prereqs || true", rg.Result(255, "", "ssh: refused"))])
    with pytest.raises(rg.GateError) as e:
        rg.preflight(ctx, h, "dstdns", prof)
    assert e.value.code == 3 and "could not run" in str(e.value)
    ctx, _ = ctx_for(tmp_path, [("check_prereqs || true", rg.Result(0, ""))])
    with pytest.raises(rg.GateError, match="no answer from the host check") as e:
        rg.preflight(ctx, h, "dstdns", prof)
    assert e.value.code == 4


def test_preflight_returns_the_selected_run_gate(tmp_path):
    h = hosts()["r1002"]
    ctx, _ = ctx_for(tmp_path, [("check_prereqs || true", rg.Result(0, "noise\nOK /home/vscode/.venvs/gate-tools2502/bin/run-gate 25.0.2\n"))])
    assert rg.preflight(ctx, h, "dstdns", h.profile("dstdns")) == "/home/vscode/.venvs/gate-tools2502/bin/run-gate"
    sent = ctx.runner.calls[-1][0][-1]
    assert sent.startswith("bash -c ") and "guard-r1002" in sent


def test_collect_maps_the_prereq_exit_code_to_a_verdict(tmp_path):
    ctx, out = ctx_for(tmp_path, git_handlers(repo="dstdns") + [psi(1, 1), LS])
    rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "run-gate-docs", "--detach", "--name", "d4"))
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(0, "76\n")))
    ctx.runner.handlers.insert(0, ("/rc", rg.Result(0, "76\n")))
    host, meta, run_dir = rg.load_meta(ctx, "d4")
    assert rg.collect(ctx, host, meta, run_dir) == 76
    assert out[-1] == "lane run-gate-docs verdict PREREQ_MISSING; exit_code 76; reason no lane output (job exit 76); see job.log"


def test_collect_maps_the_psi_refusal_to_a_verdict(tmp_path):
    ctx, out = _started(tmp_path, "d6")
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(0, "75\n")))
    ctx.runner.handlers.insert(0, ("/rc", rg.Result(0, "75\n")))
    host, meta, run_dir = rg.load_meta(ctx, "d6")
    assert rg.collect(ctx, host, meta, run_dir) == 75
    assert out[-1] == "lane docs verdict PSI_REFUSED; exit_code 75; reason no lane output (job exit 75); see job.log"


def test_substitution_is_single_pass():
    assert rg.substitute("a @@X@@ b @@Y@@ @@Z@@", {"X": "@@Y@@", "Y": "y"}) == "a @@Y@@ b y @@Z@@"
    job = rg.render_job(hosts()["v1001"], "n", {"sha": SHA, "base": None}, "suite", ".", "", 0, ["@@REPO@@"], "dstdns")
    assert "EXTRA=(@@REPO@@)" in job and "REPO=dstdns\n" in job


def test_release_tags_of_a_dstdns_style_repository_ride_along(tmp_path):
    tag = "d" * 40
    handlers = [("for-each-ref", rg.Result(0, f"{tag} dstdns-v0.1.0\n"))] + git_handlers(repo="dstdns") + [psi(1, 1), LS]
    ctx, _ = ctx_for(tmp_path, handlers)
    rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "d5"))
    assert f"{tag} dstdns-v0.1.0\nTAGS_EOF" in (tmp_path / "out" / "d5" / "job.sh").read_text()


# ---- the preflight and the job script, run in real bash against a fake docker ----

FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, subprocess, sys
cfg = json.load(open(os.environ["FAKE_DOCKER_CFG"]))
argv = sys.argv[1:]
with open(cfg["log"], "a") as fh:
    fh.write(json.dumps(argv) + "\n")
cmd = argv[0]
if cmd == "exec":
    i, env = 1, dict(os.environ)
    while argv[i].startswith("-"):
        if argv[i] == "-e":
            k, _, v = argv[i + 1].partition("=")
            env[k] = v
        i += 2
    rest = argv[i + 1:]
    if rest == ["true"]:
        sys.exit(1 if cfg.get("cockpit_down") else 0)
    if rest[-1] == "--version":
        v = cfg["versions"].get(rest[0])
        if v is None:
            sys.exit(1)
        print(cfg["version_line"].replace("@V@", v) if "version_line" in cfg else "run-gate " + v)
        sys.exit(0)
    if rest[1:3] == ["admission", "show"]:
        print(cfg.get("admission", ""))
        sys.exit(cfg.get("admission_rc", 0))
    if rest[:2] == ["sh", "-c"]:
        sys.exit(subprocess.run(["sh", "-c", rest[2], *rest[3:]], env=env).returncode)
    sys.exit(1)  # cat / rm inside the cockpit: nothing to read, fall back to the host side
if cmd == "image" and argv[1] == "inspect":
    if argv[2] not in cfg["images"]:
        sys.exit(1)
    print("image sha256:fake created now")
    sys.exit(0)
if cmd == "run":
    sys.exit(0 if cfg.get("uid_ok", True) else 2)
if cmd == "ps":
    sys.exit(0)
sys.exit(0)
'''

FAKE_RUN_GATE = r'''#!/usr/bin/env bash
{ printf 'argv:'; printf ' <%s>' "$@"; printf '\nPATH0=%s\nFOO=%s\nREPOROOT=%s\nIMG=%s\nPWD=%s\n' "${PATH%%:*}" "${FOO:-}" "${ASSAY_GATE_HOST_REPO_ROOT:-}" "${RUN_GATE_TESTER_UNIFIED_IMAGE:-}" "$PWD"; } >> "$FAKE_RG_LOG"
echo "run-gate: lane '$1' verdict ${FAKE_VERDICT:-PASS}; exit_code ${FAKE_RC:-0}; log /tmp/run-gate/lanes/none.log"
exit "${FAKE_RC:-0}"
'''


def _exe(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def sandbox(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", FAKE_DOCKER)
    rgdir = tmp_path / "cockpit" / "bin"
    rgdir.mkdir(parents=True)
    rgbin = _exe(rgdir / "run-gate", FAKE_RUN_GATE)
    cfg = {"log": str(tmp_path / "docker.log"), "versions": {str(rgbin): "25.0.2"}, "images": ["dstdns/test-runner:local", "tester-unified:local"],
           "admission": "run-gate admission: enabled=true (config: x)\nrun-gate admission: ciu-admission-2 generation=2 max_concurrent=4 unreadable_policy=refuse"}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg))
    home = tmp_path / "home"
    (home / "rg" / "j1").mkdir(parents=True)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home), "FAKE_DOCKER_CFG": str(tmp_path / "cfg.json"),
           "FAKE_RG_LOG": str(tmp_path / "rg.log"), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def conf(**kw):
        c = json.loads((tmp_path / "cfg.json").read_text())
        c.update(kw)
        (tmp_path / "cfg.json").write_text(json.dumps(c))

    return type("SB", (), {"tmp": tmp_path, "home": home, "env": env, "rgbin": rgbin, "conf": staticmethod(conf), "cfg": cfg})


def _source_bundle(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    run = lambda *a: subprocess.run(a, cwd=src, env=env, check=True, capture_output=True, text=True).stdout.strip()  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    (src / "f.txt").write_text("x")
    run("git", "add", "f.txt")
    run("git", "commit", "-q", "-m", "one")
    sha = run("git", "rev-parse", "HEAD")
    run("git", "bundle", "create", str(tmp_path / "in.bundle"), "HEAD")
    return sha, tmp_path / "in.bundle"


def _job(sb, repo, extra=("--x",), profile=None, lane="docs", proj="."):
    sha, bundle = _source_bundle(sb.tmp)
    (sb.home / "rg" / "j1" / "in.bundle").write_bytes(bundle.read_bytes())
    host = hosts()["r1002"]
    host.repos = {repo: {"run_gate_bins": [str(sb.rgbin)], **(profile or {})}}
    text = rg.render_job(host, "j1", {"sha": sha, "base": None, "tags": []}, lane, proj, "", 0, list(extra), repo)
    # the sandbox has its own pressure files and pid file location: a verdict about the job must not depend on this machine's load
    (sb.tmp / "mem").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    (sb.tmp / "io").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
    text = text.replace("/proc/pressure/memory /proc/pressure/io", f"{sb.tmp}/mem {sb.tmp}/io").replace("/tmp/rg-j1.pid", str(sb.tmp / "rg-j1.pid"))
    path = sb.tmp / "job.sh"
    path.write_text(text)
    r = subprocess.run(["bash", str(path)], env=sb.env, capture_output=True, text=True, timeout=60)
    return r, sha, host


def _docker_calls(sb):
    return [json.loads(x) for x in (sb.tmp / "docker.log").read_text().splitlines()]


def _preflight(sandbox, host, repo="dstdns", psi_value="0.00", io_full="0.00"):
    """Run the real preflight script in bash with the sandbox's fake docker; the pressure files are the sandbox's own."""
    (sandbox.tmp / "psi").write_text(f"some avg10={psi_value} avg60=0.00 avg300=0.00 total=0\n")  # memory: some
    (sandbox.tmp / "psi-io").write_text(f"some avg10=99.00 avg60=0.00 avg300=0.00 total=0\nfull avg10={io_full} avg60=0.00 avg300=0.00 total=0\n")
    script = rg.substitute(rg.PREFLIGHT, rg.profile_subs(host, host.profile(repo)))
    script = script.replace("/proc/pressure/memory /proc/pressure/io", f"{sandbox.tmp}/psi {sandbox.tmp}/psi-io")
    return subprocess.run(["bash", "-c", script], env=sandbox.env, capture_output=True, text=True)


def test_preflight_script_in_real_bash_selects_the_first_run_gate_that_meets_the_floor(sandbox):
    old = sandbox.tmp / "old" / "run-gate"
    host = hosts()["r1002"]
    host.repos = {"dstdns": {"run_gate_bins": [str(old), str(sandbox.rgbin)]}}
    sandbox.conf(versions={str(old): "24.3.0", str(sandbox.rgbin): "25.0.2"})
    r = _preflight(sandbox, host)
    assert r.stdout.strip() == f"OK {sandbox.rgbin} 25.0.2", r.stderr


def test_preflight_script_reports_every_unmet_prerequisite(sandbox):
    old = sandbox.tmp / "old" / "run-gate"
    host = hosts()["r1002"]
    host.repos = {"dstdns": {"run_gate_bins": [str(old), str(sandbox.rgbin), "/nonexistent/run-gate"]}}
    sandbox.conf(versions={str(old): "24.3.0", str(sandbox.rgbin): "25.0.0"}, images=[], cockpit_down=True)
    r = _preflight(sandbox, host)
    lines = r.stdout.strip().splitlines()
    assert all(x.startswith("PREREQ ") for x in lines) and len(lines) == 3, r.stdout
    assert "cockpit container mdt-cockpit-runner is not running" in lines[0]
    assert "docker image dstdns/test-runner:local is not present" in lines[1]
    assert f"no run-gate >= 25.0.1 in the cockpit (tried: {old}=24.3.0 {sandbox.rgbin}=25.0.0 /nonexistent/run-gate=unusable)" in lines[2]


def test_preflight_checks_the_lane_user_before_any_upload(sandbox):
    host = hosts()["r1002"]
    host.repos = {"dstdns": {"run_gate_bins": [str(sandbox.rgbin)]}}
    sandbox.conf(uid_ok=False)
    r = _preflight(sandbox, host)
    assert r.stdout.strip() == "PREREQ image dstdns/test-runner:local has no passwd entry for uid 1003 (run-gate >= 25 refuses the lane user; rebuild the image with the uid entry)"
    assert any(c[0] == "run" and any(a.startswith("rg-preflight-uidchk-") for a in c) for c in _docker_calls(sandbox))


def test_preflight_defers_the_lane_user_check_on_a_busy_host(sandbox):
    host = hosts()["r1002"]
    host.repos = {"dstdns": {"run_gate_bins": [str(sandbox.rgbin)]}}
    sandbox.conf(uid_ok=False)
    r = _preflight(sandbox, host, psi_value="60.00")
    assert "NOTE uid check deferred to the job" in r.stdout and f"OK {sandbox.rgbin} 25.0.2" in r.stdout
    assert not any(c[0] == "run" for c in _docker_calls(sandbox))  # no container started on a busy host


def test_preflight_io_full_defers_the_uid_check_but_io_some_does_not(sandbox):
    host = hosts()["r1002"]
    host.repos = {"dstdns": {"run_gate_bins": [str(sandbox.rgbin)]}}
    sandbox.conf(uid_ok=False)
    assert "NOTE uid check deferred" in _preflight(sandbox, host, io_full="60.00").stdout
    assert "NOTE uid check deferred" not in _preflight(sandbox, host, io_full="1.00").stdout  # io some=99 is ignored


def test_repo_without_uid_check_starts_no_container_in_preflight(sandbox):
    host = hosts()["r1002"]
    host.repos = {"other": {"run_gate_bins": [str(sandbox.rgbin)]}}
    r = _preflight(sandbox, host, repo="other")
    assert r.stdout.strip() == f"OK {sandbox.rgbin} 25.0.2"
    assert not any(c[0] == "run" for c in _docker_calls(sandbox))


@pytest.mark.parametrize("have,want,ok", [
    ("25.0.2", "25.0.1", True), ("25.0.1", "25.0.1", True), ("24.3.0", "25.0.1", False), ("25.10.0", "25.9.9", True), ("9.0.0", "25.0.1", False),
    ("25.0.1rc1", "25.0.1", False), ("25.0.1.dev3", "25.0.1", False), ("25.0.1a2", "25.0.1", False), ("25.0.1b1", "25.0.1", False),
    ("25.0.1.post1", "25.0.1", True), ("25.0.2rc1", "25.0.1", True), ("25.0.1rc2", "25.0.1rc1", True), ("25.0.1.dev1", "25.0.1rc1", False),
    ("25.1.dev0", "25.0.1", True), ("25.0.1+local.3", "25.0.1", True), ("25", "25.0.0", True), ("garbage", "25.0.1", False),
])
def test_version_floor_is_a_pep440_comparison(have, want, ok):
    r = subprocess.run(["bash", "-c", rg.PREREQ_FUNCS + '\nver_ge "$0" "$1"', have, want], capture_output=True)
    assert (r.returncode == 0) is ok, (have, want)


@pytest.mark.parametrize("line,bin_ok", [
    ("run-gate (python 3.12) 25.0.2", True), ("run-gate 25.0.2", True), ("25.0.2", True),
    ("run-gate (python 3.12) 25.0.1rc1", False), ("run-gate 25.0.1.dev4", False), ("run-gate 25.0.1.post2", True), ("no version here", False),
])
def test_version_is_read_from_the_last_version_word_and_prereleases_miss_the_floor(sandbox, line, bin_ok):
    host = hosts()["r1002"]
    host.repos = {"dstdns": {"run_gate_bins": [str(sandbox.rgbin)]}}
    sandbox.conf(version_line=line.replace("25.0.2", "@V@") if "25.0.2" in line else line, versions={str(sandbox.rgbin): "25.0.2"})
    r = _preflight(sandbox, host)
    assert r.stdout.startswith("OK ") is bin_ok, r.stdout


def test_job_runs_a_dstdns_lane_from_the_repo_layout_with_the_selected_run_gate(sandbox):
    prof = {"env": {"FOO": "bar baz"}, "tester_image": "tester-unified:r25-x"}
    r, sha, host = _job(sandbox, "dstdns", profile=prof)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PSI ok" in r.stdout and "admission: max_concurrent=4" in r.stdout and "lane exited rc=0" in r.stdout
    assert f"{sandbox.home}/checkouts/dstdns/.worktrees/rg-j1-{sha[:12]}" in r.stdout
    calls = _docker_calls(sandbox)
    lane = next(c for c in calls if c[0] == "exec" and "sh" in c and "-c" in c)
    assert lane[lane.index("-w") + 1] == f"/workspaces/dstdns/.worktrees/rg-j1-{sha[:12]}/."
    assert "ASSAY_GATE_HOST_REPO_ROOT=" + str(sandbox.home / "checkouts" / "dstdns") in lane
    assert "RUN_GATE_TESTER_UNIFIED_IMAGE=tester-unified:r25-x" in lane and "FOO=bar baz" in lane
    rglog = (sandbox.tmp / "rg.log").read_text()
    assert "argv: <docs> <--x>" in rglog and f"PATH0={sandbox.rgbin.parent}" in rglog and "FOO=bar baz" in rglog and "IMG=tester-unified:r25-x" in rglog
    assert f"REPOROOT={sandbox.home}/checkouts/dstdns\n" in rglog
    assert not (sandbox.home / "checkouts" / "dstdns" / ".worktrees" / f"rg-j1-{sha[:12]}").exists()  # throwaway checkout removed on exit
    assert (sandbox.home / "rg" / "j1" / "rc").read_text().strip() == "0"
    assert any(c[:2] == ["run", "--rm"] and "getent" in c and c[-2:] == ["passwd", "1003"] and "rg-j1-uidchk" in c for c in calls)


def test_job_propagates_the_lane_exit_code(sandbox):
    sandbox.env["FAKE_RC"] = "1"
    sandbox.env["FAKE_VERDICT"] = "FAIL"
    r, _, _ = _job(sandbox, "dstdns")
    assert r.returncode == 1 and (sandbox.home / "rg" / "j1" / "rc").read_text().strip() == "1"
    assert "verdict FAIL" in (sandbox.home / "rg" / "j1" / "lane.log").read_text()


def _refused(sandbox, repo="dstdns", **kw):
    r, sha, _ = _job(sandbox, repo)
    assert r.returncode == 76, r.stdout + r.stderr
    assert not (sandbox.tmp / "rg.log").exists()  # the lane never started
    assert (sandbox.home / "rg" / "j1" / "rc").read_text().strip() == "76"
    assert "bring-up steps" in r.stdout
    return r


def test_job_refuses_when_the_required_image_is_missing(sandbox):
    sandbox.conf(images=[])
    r = _refused(sandbox)
    assert "PREREQ MISSING: docker image dstdns/test-runner:local is not present on this host" in r.stdout
    assert not (sandbox.home / "checkouts").exists()


def test_job_refuses_when_no_run_gate_meets_the_floor(sandbox):
    sandbox.conf(versions={str(sandbox.rgbin): "24.3.0"})
    r = _refused(sandbox)
    assert f"no run-gate >= 25.0.1 in the cockpit (tried: {sandbox.rgbin}=24.3.0)" in r.stdout


def test_job_refuses_when_the_lane_user_has_no_passwd_entry(sandbox):
    sandbox.conf(uid_ok=False)
    r = _refused(sandbox)
    assert "has no passwd entry for uid 1003" in r.stdout


def test_job_refuses_when_admission_is_enabled_but_no_cap_is_published(sandbox):
    sandbox.conf(admission="run-gate admission: enabled=true (config: x)\nrun-gate admission: no admission object published")
    r = _refused(sandbox)
    assert "publishes no gates cap" in r.stdout and "run-gate admission set --max-concurrent" in r.stdout
    assert not list((sandbox.home / "checkouts" / "dstdns" / ".worktrees").glob("rg-*"))  # the checkout is cleaned on the way out


def test_admission_show_errors_fail_the_check(sandbox):
    sandbox.conf(admission="run-gate: no such subcommand", admission_rc=2)
    r = _refused(sandbox)
    assert "admission show failed (rc 2)" in r.stdout and "no such subcommand" in r.stdout


def test_admission_output_without_an_enabled_statement_fails_the_check(sandbox):
    sandbox.conf(admission="admission is on, cap 4")  # a changed format must not pass silently
    r = _refused(sandbox)
    assert "no 'enabled=true|false' statement" in r.stdout


def test_admission_that_the_repo_does_not_enable_is_not_checked_further(sandbox):
    sandbox.conf(admission="run-gate admission: enabled=false")
    r, _, _ = _job(sandbox, "dstdns")
    assert r.returncode == 0 and "lane exited rc=0" in r.stdout and "max_concurrent" not in r.stdout


def test_a_repo_without_a_profile_needs_only_the_cockpit(sandbox):
    sandbox.conf(images=[], uid_ok=False, admission="")
    r, sha, _ = _job(sandbox, "otherrepo")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "admission:" not in r.stdout
    assert not any(c[0] == "run" for c in _docker_calls(sandbox))
    assert f"{sandbox.home}/checkouts/otherrepo/.worktrees/rg-j1-{sha[:12]}" in r.stdout


def test_rendered_dstdns_job_is_valid_bash_and_shellcheck_clean(tmp_path):
    host = hosts()["r1002"]
    host.repos = {"dstdns": {"env": {"A": "b c"}}}
    text = rg.render_job(host, "n1", {"sha": SHA, "base": "c" * 40}, "docs", ".", SEED, 900, ["--jobs", "3"], "dstdns")
    path = tmp_path / "job.sh"
    path.write_text(text)
    subprocess.run(["bash", "-n", str(path)], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-x", str(path)], check=True)
    pre = tmp_path / "pre.sh"
    pre.write_text(rg.substitute(rg.PREFLIGHT, rg.profile_subs(host, host.profile("dstdns"))))
    subprocess.run(["bash", "-n", str(pre)], check=True)


# ---- the rest of the command surface (refusals and plumbing the repo-aware flow shares) ----

def test_real_runner_executes_processes_and_reports_timeouts(tmp_path):
    r = rg.Runner()
    inp = tmp_path / "in.txt"
    inp.write_text("hello")
    res = r.run(["cat"], stdin_path=str(inp))
    assert (res.rc, res.out) == (0, "hello")
    assert r.run(["sleep", "5"], timeout=0.2).rc == 124
    dest = tmp_path / "out.txt"
    assert r.run_to_file(["echo", "x"], str(dest)).rc == 0 and dest.read_text() == "x\n"
    assert r.run_to_file(["sleep", "5"], str(dest), timeout=0.2).rc == 124


@pytest.mark.parametrize("args,needle", [
    (("r1002", "/wt", "bad lane!"), "invalid lane"),
    (("r1002", "/wt", "suite", "--name", "../x"), "invalid --name"),
    (("r1002", "/wt", "suite", "--project-dir", "/abs"), "project-dir"),
    (("r1002", "/wt", "suite", "--project-dir", "a/../../b"), "project-dir"),
])
def test_argument_refusals(tmp_path, args, needle):
    ctx, _ = ctx_for(tmp_path, git_handlers())
    with pytest.raises(rg.GateError, match=needle):
        rg.cmd_run(ctx, run_args(tmp_path, *args))


@pytest.mark.parametrize("handler,needle", [
    (("--show-toplevel", rg.Result(128)), "not a git worktree"),
    (("rev-parse HEAD", rg.Result(0, "nothex\n")), "cannot resolve HEAD"),
    (("--verify", rg.Result(1)), "does not resolve"),
])
def test_local_git_refusals(tmp_path, handler, needle):
    ctx, _ = ctx_for(tmp_path, [handler] + git_handlers())
    with pytest.raises(rg.GateError, match=needle):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--base", "nope"))


def test_psi_read_failure_and_bundle_failure(tmp_path):
    ctx, _ = ctx_for(tmp_path, [("/^full/ {sub", rg.Result(1, "", "ssh down"))])
    with pytest.raises(rg.GateError, match="cannot read PSI"):
        rg.choose_host(ctx, "auto")
    ctx, _ = ctx_for(tmp_path, [("bundle create", rg.Result(1, "", "boom"))] + git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match="git bundle failed"):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--name", "e1"))


def test_auto_skips_hosts_missing_from_the_table(tmp_path):
    ctx, _ = ctx_for(tmp_path, [psi(1, 1)])
    del ctx.hosts["r1002"]
    assert rg.choose_host(ctx, "auto").name == "v1001"


def test_seed_that_lacks_the_commit_is_skipped_and_an_unready_host_is_refused(tmp_path):
    ctx, _ = ctx_for(tmp_path, [("cat-file -e", rg.Result(1))] + git_handlers() + [psi(1, 1), LS])
    rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "e2"))
    assert json.loads((tmp_path / "out" / "e2" / "meta.json").read_text())["seed_checkout"] == ""
    ctx, _ = ctx_for(tmp_path, [("mkdir -p -m 0700", rg.Result(1, "", "no space"))] + git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match="cannot prepare"):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--name", "e3"))
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    ctx.out_dir.joinpath("e4").mkdir(parents=True)
    with pytest.raises(rg.GateError, match="already exists; pick"):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--name", "e4"))
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    ctx.runner.handlers.insert(0, ("test -e /home/runner/rg/e5/job.sh", rg.Result(0)))
    with pytest.raises(rg.GateError, match="already exists on the host"):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--name", "e5"))


@pytest.mark.parametrize("needle,msg", [("cat > /home/runner/rg/e6/in.bundle", "bundle upload failed"),
                                         ("cat > /home/runner/rg/e6/job.sh", "job upload failed"),
                                         ("run-job", "could not start the job")])
def test_upload_and_start_failures(tmp_path, needle, msg):
    ctx, _ = ctx_for(tmp_path, [(needle, rg.Result(1, "", "nope"))] + git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match=msg):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--name", "e6"))


def test_bundle_checksum_mismatch_is_refused(tmp_path):
    ctx, _ = ctx_for(tmp_path, [("sha256sum", rg.Result(0, "0" * 64 + "  in.bundle\n"))] + git_handlers() + [psi(1, 1), LS])
    with pytest.raises(rg.GateError, match="checksum mismatch"):
        rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--name", "e7"))


def _started(tmp_path, name, repo="dstdns", extra=()):
    ctx, out = ctx_for(tmp_path, list(extra) + git_handlers(repo=repo) + [psi(1, 1), LS])
    rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "docs", "--detach", "--name", name))
    return ctx, out


def test_status_logs_and_cancel_for_a_repo_run(tmp_path):
    ctx, out = _started(tmp_path, "s1")
    ctx.runner.handlers.insert(0, ("tail -n 4", rg.Result(0, "line a\nline b\n")))
    assert rg.cmd_status(ctx, rg.parser().parse_args(["status", "s1"])) == 0
    assert "lane=docs" in out[-2] and ": running" in out[-2] and out[-1] == "line a\nline b"
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(0, "0\n")))
    rg.cmd_status(ctx, rg.parser().parse_args(["status", "s1"]))
    assert "finished exit=0" in out[-2]
    ctx.runner.handlers.insert(0, ("tail -c +1", rg.Result(0, "hello\n")))
    assert rg.cmd_logs(ctx, rg.parser().parse_args(["logs", "s1"])) == 0 and out[-1] == "hello"
    assert rg.cmd_logs(ctx, rg.parser().parse_args(["logs", "s1", "-f", "--lane-log"])) == 0
    ctx.runner.handlers.insert(0, ("tail -c +", rg.Result(255, "", "down")))
    with pytest.raises(rg.GateError, match="could not read the log"):
        rg.cmd_logs(ctx, rg.parser().parse_args(["logs", "s1"]))
    ctx.runner.handlers.insert(0, ("kill -TERM", rg.Result(1)))
    assert rg.cmd_cancel(ctx, rg.parser().parse_args(["cancel", "s1"])) == 1


def test_logs_follow_polls_until_the_job_finishes(tmp_path):
    ctx, out = _started(tmp_path, "s2")
    seen = {"n": 0}

    def exit_marker(argv):
        seen["n"] += 1
        return rg.Result(0, "" if seen["n"] < 2 else "0\n")

    ctx.runner.handlers.insert(0, ("/EXIT", exit_marker))
    ctx.runner.handlers.insert(0, ("tail -c +", rg.Result(0, "more\n")))
    sleeps = []
    ctx.sleep = sleeps.append
    assert rg.cmd_logs(ctx, rg.parser().parse_args(["logs", "s2", "-f"])) == 0
    assert len(sleeps) == 1 and "more" in out


def test_collect_extracts_artifacts_and_maps_cancel(tmp_path):
    import io
    import tarfile
    ctx, out = _started(tmp_path, "s3")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("./.assay/verdict-x.json")
        data = b"{}"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    real_run_to_file = ctx.runner.run_to_file

    def run_to_file(argv, dest, timeout=600):
        res = real_run_to_file(argv, dest, timeout)
        if dest.endswith("artifacts.tgz"):
            Path(dest).write_bytes(buf.getvalue())
        return res

    ctx.runner.run_to_file = run_to_file
    ctx.runner.run = lambda argv, stdin_path=None, timeout=300, _o=ctx.runner.run: (
        subprocess.run(argv, capture_output=True, text=True) and rg.Result(0)) if argv[0] == "tar" else _o(argv, stdin_path, timeout)
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(0, "143\n")))
    ctx.runner.handlers.insert(0, ("/rc", rg.Result(0, "143\n")))
    host, meta, run_dir = rg.load_meta(ctx, "s3")
    assert rg.collect(ctx, host, meta, run_dir) == 143
    assert (run_dir / "artifacts" / ".assay" / "verdict-x.json").read_text() == "{}"
    assert out[-1] == "lane docs verdict CANCELLED; exit_code 143"


def test_main_reports_gate_errors_and_missing_files(tmp_path, capsys):
    ctx, _ = ctx_for(tmp_path, [])
    assert rg.main(["--out-dir", str(tmp_path / "o"), "status", "missing"], ctx) == 2
    assert "no such run" in capsys.readouterr().err
    assert rg.main(["--hosts-file", str(tmp_path / "nope.json"), "status", "x"]) == 2
    assert "nope.json" in capsys.readouterr().err
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"hosts": {"r1002": {"ssh": ["ssh"], "api_check": ["x"], "guard": "true"}}}))
    assert rg.main(["--hosts-file", str(f), "--out-dir", str(tmp_path / "o2"), "status", "x"]) == 2


def test_script_entrypoint_prints_usage_for_no_arguments():
    r = subprocess.run([sys.executable, "-I", str(Path(rg.__file__)), "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--repo NAME" in r.stdout and "worktree-of-any-git-repo" in r.stdout
