"""Hermetic tests for runner/remote-gate.py: a fake Runner stands in for ssh, git and the identity check."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "runner" / "remote-gate.py"
spec = importlib.util.spec_from_file_location("remote_gate", SCRIPT)
rg = importlib.util.module_from_spec(spec)
sys.dont_write_bytecode = True  # a __pycache__ in runner/ would trip the directory-wide runner checks
sys.modules["remote_gate"] = rg
spec.loader.exec_module(rg)

SHA = "a" * 40
SEED = "b" * 40


class Fake(rg.Runner):
    """Scripted runner: handlers are (substring, Result|callable); calls are recorded in order."""

    def __init__(self, handlers=None):
        self.calls = []
        self.handlers = handlers or []
        self.files = {}

    def _answer(self, argv):
        flat = " ".join(argv)
        for needle, res in self.handlers:
            if needle in flat:
                return res(argv) if callable(res) else res
        return rg.Result(0, "")

    def run(self, argv, stdin_path=None, timeout=300):
        self.calls.append((list(argv), stdin_path))
        return self._answer(argv)

    def run_to_file(self, argv, dest, timeout=600):
        self.calls.append((list(argv), dest))
        res = self._answer(argv)
        Path(dest).write_text(res.out)
        return rg.Result(res.rc, "", res.err)


def hosts():
    row = lambda n: {"ssh": ["ssh", n], "api_check": ["apicheck", n], "guard": f"guard-{n}"}  # noqa: E731
    return {n: rg.Host(name=n, ssh=row(n)["ssh"], api_check=row(n)["api_check"], guard=row(n)["guard"]) for n in ("r1002", "v1001")}


PREFLIGHT_OK = ("check_prereqs || true", rg.Result(0, "OK /home/vscode/.local/bin/run-gate 25.0.2\n"))


def ctx_for(tmp_path, handlers):
    out = []
    bundle_sum = rg.hashlib.sha256(b"bundle").hexdigest() + "  in.bundle\n"
    handlers = [("test -e /home/runner/rg/", rg.Result(1))] + list(handlers) + [("sha256sum", rg.Result(0, bundle_sum)), PREFLIGHT_OK]
    return rg.Ctx(runner=Fake(handlers), hosts=hosts(), out_dir=tmp_path / "out", sleep=lambda s: None, say=out.append), out


def git_handlers(dirty="", base_ok=True, count="3", repo="vbpub"):
    return [
        ("--show-toplevel", rg.Result(0, "/wt\n")),
        ("--git-common-dir", rg.Result(0, f"/ws/{repo}/.git\n")),
        ("rev-parse HEAD", rg.Result(0, SHA + "\n")),
        ("status --porcelain", rg.Result(0, dirty)),
        ("--show-prefix", rg.Result(0, "scripts/netcup/\n")),
        ("--verify", rg.Result(0 if base_ok else 1, "c" * 40 + "\n")),
        ("cat-file -e", rg.Result(0)),
        ("rev-list --count", rg.Result(0, count + "\n")),
        ("bundle create", lambda argv: (Path(argv[5]).write_bytes(b"bundle"), rg.Result(0))[1]),
    ]


def psi(cpu, mem, io=0):
    return ("/^full/ {sub", rg.Result(0, f"{cpu}\n{mem}\n{io}\n"))


def run_args(tmp_path, *extra):
    return rg.parser().parse_args(["--out-dir", str(tmp_path / "out"), "run", *extra])


def test_auto_picks_the_lower_psi_host(tmp_path):
    calls = {}

    def per_host(argv):
        calls[argv[1]] = 1
        return rg.Result(0, "9.0\n1.0\n0.0\n" if argv[1] == "r1002" else "3.0\n2.0\n0.0\n")

    ctx, _ = ctx_for(tmp_path, [("/proc/pressure/cpu", per_host)])
    assert rg.choose_host(ctx, "auto").name == "v1001"


def test_a_busy_cpu_is_not_a_stop_signal(tmp_path):
    ctx, out = ctx_for(tmp_path, [psi(60.0, 1.0)])  # CPU PSI 60 with memory PSI 1: accepted
    assert rg.choose_host(ctx, "auto").name in ("r1002", "v1001")
    assert "cpu_some=60.0" in out[-1]


def test_auto_prefers_the_host_with_less_cpu_stall_among_acceptable_ones(tmp_path):
    def per_host(argv):
        return rg.Result(0, "80.0\n20.0\n0\n" if argv[1] == "v1001" else "40.0\n24.0\n0\n")

    ctx, _ = ctx_for(tmp_path, [("/^full/ {sub", per_host)])
    assert rg.choose_host(ctx, "auto").name == "r1002"


@pytest.mark.parametrize("cpu,mem,io", [(1.0, 30.0, 0.0), (1.0, 1.0, 30.0)])
def test_auto_refuses_when_both_hosts_are_above_25_on_memory_or_io(tmp_path, cpu, mem, io):
    ctx, _ = ctx_for(tmp_path, [psi(cpu, mem, io)])
    with pytest.raises(rg.GateError) as e:
        rg.choose_host(ctx, "auto")
    assert e.value.code == 75 and "memory PSI some avg10 or io PSI full avg10" in str(e.value)
    assert not any("run-job" in " ".join(c[0]) for c in ctx.runner.calls)


def test_auto_takes_the_only_host_below_the_memory_limit_whatever_its_cpu(tmp_path):
    def per_host(argv):
        return rg.Result(0, "5.0\n40.0\n0\n" if argv[1] == "r1002" else "90.0\n2.0\n0\n")

    ctx, _ = ctx_for(tmp_path, [("/^full/ {sub", per_host)])
    assert rg.choose_host(ctx, "auto").name == "v1001"


def test_identity_check_runs_before_any_ssh_and_failure_aborts(tmp_path):
    ctx, _ = ctx_for(tmp_path, [("apicheck", rg.Result(1, "", "IDENTITY MISMATCH"))])
    with pytest.raises(rg.GateError) as e:
        rg.choose_host(ctx, "v1001")
    assert e.value.code == 3
    assert ctx.runner.calls == [(["apicheck", "v1001"], None)]


def test_only_allowlisted_hosts_exist(tmp_path):
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"hosts": {"nano1": {"ssh": [], "api_check": [], "guard": "x"}}}))
    with pytest.raises(rg.GateError):
        rg.load_hosts(str(f))
    ctx, _ = ctx_for(tmp_path, [])
    with pytest.raises(rg.GateError):
        rg.get_host(ctx, "nano1")


def test_dirty_tracked_tree_is_refused(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers(dirty=" M file.py\n"))
    with pytest.raises(rg.GateError, match="uncommitted tracked changes"):
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite"))


def test_run_detached_ships_bundle_verifies_checksum_and_starts_job(tmp_path):
    ls = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\nnot-a-sha\n"))
    shipped = {}

    def sha(argv):
        return rg.Result(0, shipped.get("sum", "") + "  in.bundle\n")

    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ls, ("sha256sum", sha)])
    sha_expected = rg.hashlib.sha256(b"bundle").hexdigest()
    shipped["sum"] = sha_expected
    rc = rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "t1", "--base", "origin/main", "--", "--fresh"))
    assert rc == 0
    flat = [" ".join(c[0]) for c in ctx.runner.calls]
    first_ssh = next(i for i, c in enumerate(flat) if c.startswith("ssh"))
    assert flat.index("apicheck v1001") < first_ssh  # identity before any write
    assert any("cat > /home/runner/rg/t1/in.bundle" in c and ctx.runner.calls[i][1].endswith("in.bundle") for i, c in enumerate(flat))
    assert any("/home/runner/bin/run-job rg-t1 -- bash /home/runner/rg/t1/job.sh" in c for c in flat)
    assert all("guard-v1001" in c for c in flat if c.startswith("ssh"))
    job = (tmp_path / "out" / "t1" / "job.sh").read_text()
    assert f"SHA={SHA}" in job and f"SEED={SEED}" in job and "PROJ=scripts/netcup" in job and "EXTRA=(--fresh)" in job
    assert f"BASE_SHA={'c' * 40}" in job and "@@" not in job
    meta = json.loads((tmp_path / "out" / "t1" / "meta.json").read_text())
    assert meta["host"] == "v1001" and meta["sha"] == SHA and meta["bundle"] is True
    assert not (tmp_path / "out" / "t1" / "in.bundle").exists()


def test_head_already_on_the_host_needs_no_bundle(tmp_path):
    ls = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))
    ctx, _ = ctx_for(tmp_path, git_handlers(count="0") + [psi(1, 1), ls])
    assert rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "t2")) == 0
    assert not any("bundle create" in " ".join(c[0]) for c in ctx.runner.calls)
    assert not any("in.bundle" in " ".join(c[0]) for c in ctx.runner.calls)


TAG_A, TAG_B = "d" * 40, "e" * 40
TAG_OUT = f"{TAG_A} run-gate-v24.3.0\n{TAG_B} assay-v7.2.0\nnot a tag line\n"


def test_all_release_tags_ride_the_bundle_and_the_job_script(tmp_path):
    ls = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))
    ctx, _ = ctx_for(tmp_path, [("for-each-ref", rg.Result(0, TAG_OUT))] + git_handlers() + [psi(1, 1), ls])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "t3")) == 0
    gitcalls = [c[0] for c in ctx.runner.calls]
    fer = next(c for c in gitcalls if "for-each-ref" in c and "refs/tags/*-v[0-9]*" in c)
    assert fer[-1] == "refs/tags/*-v[0-9]*"
    bundle = next(c for c in gitcalls if "bundle" in c and "create" in c)
    assert "HEAD" in bundle and "refs/tags/run-gate-v24.3.0" in bundle and "refs/tags/assay-v7.2.0" in bundle
    assert f"^{SEED}" in bundle  # still thin
    job = (tmp_path / "out" / "t3" / "job.sh").read_text()
    assert f"{TAG_A} run-gate-v24.3.0\n{TAG_B} assay-v7.2.0\nTAGS_EOF" in job and "@@" not in job


def test_tags_missing_on_a_seed_that_has_head_still_trigger_a_bundle(tmp_path):
    ls = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))

    def count(argv):  # HEAD is on the host (0) but the tagged commits are not (2)
        return rg.Result(0, ("2" if TAG_A in argv else "0") + "\n")

    handlers = [("for-each-ref", rg.Result(0, TAG_OUT)), ("rev-list --count", count)] + git_handlers() + [psi(1, 1), ls]
    ctx, _ = ctx_for(tmp_path, handlers)
    assert rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "t4")) == 0
    assert any("bundle" in c[0] and "create" in c[0] for c in ctx.runner.calls)
    assert json.loads((tmp_path / "out" / "t4" / "meta.json").read_text())["bundle"] is True


def test_tags_already_on_the_seed_need_no_bundle_but_still_reach_the_job(tmp_path):
    ls = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))
    ctx, _ = ctx_for(tmp_path, [("for-each-ref", rg.Result(0, TAG_OUT))] + git_handlers(count="0") + [psi(1, 1), ls])
    assert rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "t5")) == 0
    assert not any("bundle" in c[0] and "create" in c[0] for c in ctx.runner.calls)
    assert f"{TAG_A} run-gate-v24.3.0" in (tmp_path / "out" / "t5" / "job.sh").read_text()


def test_job_tag_loop_adds_missing_tags_and_keeps_existing_ones(tmp_path):
    def sh(*a, cwd):
        return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null",
                              "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()

    ck = tmp_path / "ck"
    ck.mkdir()
    sh("git", "init", "-q", cwd=ck)
    sh("git", "commit", "-q", "--allow-empty", "-m", "one", cwd=ck)
    c1 = sh("git", "rev-parse", "HEAD", cwd=ck)
    sh("git", "commit", "-q", "--allow-empty", "-m", "two", cwd=ck)
    c2 = sh("git", "rev-parse", "HEAD", cwd=ck)
    sh("git", "tag", "-a", "-m", "m", "run-gate-v25.0.0", c1, cwd=ck)  # already present (annotated): must stay as is
    before = sh("git", "rev-parse", "run-gate-v25.0.0", cwd=ck)
    tags = f"{c1} run-gate-v25.0.0\n{c1} run-gate-v24.3.0\n{c2} assay-v7.2.0\n{'f' * 40} ghost-v1.0.0"
    job = rg.render_job(hosts()["v1001"], "t6", {"sha": SHA, "base": None, "tags": [tuple(x.split()) for x in tags.splitlines()]}, "suite", ".", "", 60, [])
    start = job.index("while read -r tsha tname")
    end = job.index("TAGS_EOF\n", job.index("<<'TAGS_EOF'")) + len("TAGS_EOF\n")
    log = f'CKDIR={ck}\nlog() {{ :; }}\n' + job[start:end] + "true\n"  # the job runs without -e; the last (ghost) tag legitimately ends the loop non-zero
    subprocess.run(["bash", "-c", log], check=True, env={**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null"})
    assert sh("git", "tag", "-l", cwd=ck).split() == sorted(["assay-v7.2.0", "run-gate-v24.3.0", "run-gate-v25.0.0"])
    assert sh("git", "rev-parse", "run-gate-v25.0.0", cwd=ck) == before
    assert sh("git", "rev-parse", "run-gate-v24.3.0^{commit}", cwd=ck) == c1


def test_follow_collects_artifacts_and_prints_the_exact_verdict_line(tmp_path):
    state = {"n": 0}

    def exit_marker(argv):
        state["n"] += 1
        return rg.Result(0, "" if state["n"] < 2 else "1\n")

    lane = "noise\nrun-gate: lane 'suite' verdict FAIL; exit_code 1; log /tmp/x.log\n"
    handlers = git_handlers() + [
        psi(1, 1),
        ("ls /home/runner/checkouts", rg.Result(0, "")),
        ("sha256sum", rg.Result(0, rg.hashlib.sha256(b"bundle").hexdigest() + "  f\n")),
        ("/EXIT", exit_marker),
        ("cat /home/runner/rg/t3/rc", rg.Result(0, "1\n")),
        ("cat /home/runner/rg/t3/lane.log", rg.Result(0, lane)),
    ]
    ctx, out = ctx_for(tmp_path, handlers)
    rc = rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--name", "t3"))
    assert rc == 1
    assert out[-1] == "lane suite verdict FAIL; exit_code 1"
    assert json.loads((tmp_path / "out" / "t3" / "result.json").read_text()) == {"lane": "suite", "verdict": "FAIL", "exit_code": 1}


def test_collect_of_a_running_job_refuses(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, ""))])
    rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "t4"))
    a = rg.parser().parse_args(["collect", "t4"])
    with pytest.raises(rg.GateError) as e:
        rg.cmd_collect(ctx, a)
    assert e.value.code == 75


def test_cancel_signals_only_the_job_pid_file(tmp_path):
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, ""))])
    rg.cmd_run(ctx, run_args(tmp_path, "r1002", "/wt", "suite", "--detach", "--name", "t5"))
    assert rg.cmd_cancel(ctx, rg.parser().parse_args(["cancel", "t5"])) == 0
    last = " ".join(ctx.runner.calls[-1][0])
    assert "kill -TERM" in last and "/home/runner/rg/t5/pid" in last and "pkill" not in last


def test_unknown_id_and_bad_names_are_rejected(tmp_path):
    ctx, _ = ctx_for(tmp_path, [])
    with pytest.raises(rg.GateError):
        rg.load_meta(ctx, "../etc")
    with pytest.raises(rg.GateError):
        rg.load_meta(ctx, "missing")


def test_verdict_parser_takes_the_last_verdict_and_defaults_to_unknown():
    assert rg.verdict_of("lane 'a' verdict PASS; x\nlane 'a' verdict INCONCLUSIVE; y", "a") == "INCONCLUSIVE"
    assert rg.verdict_of("nothing", "a") == "UNKNOWN"


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_rendered_job_script_is_shellcheck_clean(tmp_path):
    st = {"sha": SHA, "base": "c" * 40}
    text = rg.render_job(hosts()["v1001"], "n1", st, "suite", "scripts/netcup", SEED, 900, ["--fresh", "a b"])
    path = tmp_path / "job.sh"
    path.write_text(text)
    subprocess.run(["bash", "-n", str(path)], check=True)
    subprocess.run(["shellcheck", "-x", str(path)], check=True)


def test_rendered_job_script_is_valid_bash(tmp_path):
    text = rg.render_job(hosts()["v1001"], "n1", {"sha": SHA, "base": None}, "suite", ".", "", 0, [])
    path = tmp_path / "job.sh"
    path.write_text(text)
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_checkout_lives_under_vbpub_worktrees_and_cleanup_is_scoped(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, ""))])
    rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "t2"))
    job = (tmp_path / "out" / "t2" / "job.sh").read_text()
    assert "REPO=vbpub\n" in job
    assert "CKDIR=$CKROOT/$CK" in job and "CKROOT=$HOME/checkouts/$REPO/.worktrees" in job
    assert "CKIN=/workspaces/$REPO/.worktrees/$CK" in job
    assert '"$HOME/checkouts/$REPO/.worktrees"/rg-*' in job  # removal guard only matches our own rg-* dirs of this repo
    assert '-e ASSAY_GATE_HOST_REPO_ROOT="$HOME/checkouts/$REPO"' in job
    assert '-w "$CKIN/$PROJ"' in job and "/workspaces/$CK" not in job


# ---- review fixes: fail-closed guard, bounded follow, safe cancel, precise container cleanup ----

def _bash(cmd, **kw):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, **kw)


def test_failing_guard_prevents_the_script_from_running_in_real_bash(tmp_path):
    marker = tmp_path / "ran"
    for guard in ("test 1 = 2", "test 1 = 2; test 1 = 1", "test 1 = 1; test 1 = 2", "false"):
        host = rg.Host(name="r1002", ssh=[], api_check=["x"], guard=guard)
        r = _bash(rg.guarded(host, f"touch {marker}"))
        assert r.returncode != 0 and not marker.exists(), guard
    host = rg.Host(name="r1002", ssh=[], api_check=["x"], guard="test 1 = 1; test 2 = 2")
    assert _bash(rg.guarded(host, f"touch {marker}; true")).returncode == 0 and marker.exists()


def test_remote_uses_the_guarded_form_and_hosts_need_a_guard(tmp_path):
    ctx, _ = ctx_for(tmp_path, [])
    rg.remote(ctx, ctx.hosts["r1002"], "echo hi")
    sent = ctx.runner.calls[-1][0][-1]
    assert sent.startswith("bash -c ") and "guard-r1002" in sent and " && { echo hi" in sent
    f = tmp_path / "h.json"
    f.write_text(json.dumps({"hosts": {"r1002": {"ssh": ["ssh"], "api_check": ["x"], "guard": " "}}}))
    with pytest.raises(rg.GateError):
        rg.load_hosts(str(f))
    ex = json.loads((SCRIPT.parent / "remote-gate-hosts.example.json").read_text())["hosts"]["v1001"]["guard"]
    assert ";" not in ex


def test_follow_gives_up_when_ssh_or_guard_keeps_failing(tmp_path):
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, ""))])
    rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "t6"))
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(255, "", "ssh: connect refused")))
    sleeps = []
    ctx.sleep = sleeps.append
    host, meta, run_dir = rg.load_meta(ctx, "t6")
    with pytest.raises(rg.GateError) as e:
        rg.follow(ctx, host, meta, run_dir, 1)
    assert e.value.code == 3 and "giving up" in str(e.value) and len(sleeps) == rg.MAX_POLL_FAILURES - 1


def _cancel_script(tmp_path, pid_text):
    ctx, _ = ctx_for(tmp_path, [])
    rgd = tmp_path / "rg" / "t7"
    rgd.mkdir(parents=True, exist_ok=True)
    if pid_text is not None:
        (rgd / "pid").write_text(pid_text)
    d = tmp_path / "out" / "t7"
    d.mkdir(parents=True, exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"name": "t7", "host": "r1002", "lane": "suite", "sha": SHA, "remote_dir": str(rgd)}))
    rg.cmd_cancel(ctx, rg.parser().parse_args(["cancel", "t7"]))
    return ctx.runner.calls[-1][0][-1].split(" && { ", 1)[1].rsplit("\n}", 1)[0]


def test_cancel_never_signals_a_process_that_is_not_this_jobs_script(tmp_path):
    victim = subprocess.Popen(["sleep", "30"])
    try:
        script = _cancel_script(tmp_path, f"{victim.pid}\n")  # reused pid: some unrelated process
        assert _bash(script).returncode != 0
        assert victim.poll() is None
    finally:
        victim.kill()
        victim.wait()
    assert _bash(_cancel_script(tmp_path, None)).returncode != 0  # pid file removed by finish()
    assert _bash(_cancel_script(tmp_path, "abc\n")).returncode != 0


def test_cancel_signals_the_job_whose_cmdline_names_its_job_sh(tmp_path):
    jobsh = tmp_path / "rg" / "t7" / "job.sh"
    (tmp_path / "rg" / "t7").mkdir(parents=True)
    jobsh.write_text("sleep 30\n")
    p = subprocess.Popen(["bash", str(jobsh)])
    try:
        assert _bash(_cancel_script(tmp_path, f"{p.pid}\n")).returncode == 0
        assert p.wait(timeout=10) == -15
    finally:
        if p.poll() is None:
            p.kill()


def test_job_cleanup_removes_only_containers_that_reference_this_run(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, ""))])
    rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "t8"))
    job = (tmp_path / "out" / "t8" / "job.sh").read_text()
    body = job.split("finish() {", 1)[1].split("\n}\ntrap finish EXIT", 1)[0]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    removed = tmp_path / "removed"
    (bindir / "docker").write_text(f"""#!/usr/bin/env bash
case "$1" in
  ps) printf 'run-gate-mine\\nrun-gate-other\\nrun-gate-prefix\\nunrelated\\n' ;;
  inspect) case "$2" in
    run-gate-mine) echo '"Source": "/h/.worktrees/rg-t8-{SHA[:12]}"' ;;
    run-gate-other) echo '"Source": "/h/.worktrees/rg-t8-{SHA[:12]}-x"' ;;
    run-gate-prefix) echo '"Source": "/h/.worktrees/rg-t8x-{SHA[:12]}"' ;;
  esac ;;
  rm) echo "${{@: -1}}" >> {removed} ;;
esac
""")
    (bindir / "docker").chmod(0o755)
    d = tmp_path / "D"
    d.mkdir()
    script = (f'export PATH={bindir}:$PATH; D={d}; CK=rg-t8-{SHA[:12]}; CKDIR=/nonexistent/x; COCKPIT=c; PIDF=/tmp/p; rc=0\n'
              f'log() {{ :; }}\nfinish() {{{body}\n}}\n')
    assert _bash(script + "LANE_STARTED=0; finish").returncode == 0
    assert not removed.exists()  # lane never started (e.g. PSI refusal): nothing removed
    assert _bash(script + "LANE_STARTED=1; finish").returncode == 0
    assert removed.read_text().split() == ["run-gate-mine"]


def test_job_cleanup_finds_label_and_legacy_names_once(tmp_path):
    """RG-242: new `<repo>-run-gate-...` names are found by label, pre-RG-242 `run-gate-*` by name, each once."""
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, ""))])
    rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "t9"))
    job = (tmp_path / "out" / "t9" / "job.sh").read_text()
    body = job.split("finish() {", 1)[1].split("\n}\ntrap finish EXIT", 1)[0]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    removed = tmp_path / "removed"
    new_name = "vbpub-run-gate-cmru-r2-ccd96cda-261105-1425-123456789"
    (bindir / "docker").write_text(f"""#!/usr/bin/env bash
case "$1" in
  ps) case "$*" in
        *label=run-gate.lane*) printf '{new_name}\\n' ;;
        *) printf 'run-gate-legacy\\nunrelated\\n{new_name}\\n' ;;
      esac ;;
  inspect) echo '"Source": "/h/.worktrees/rg-t9-{SHA[:12]}"' ;;
  rm) echo "${{@: -1}}" >> {removed} ;;
esac
""")
    (bindir / "docker").chmod(0o755)
    d = tmp_path / "D"
    d.mkdir()
    script = (f'export PATH={bindir}:$PATH; D={d}; CK=rg-t9-{SHA[:12]}; CKDIR=/nonexistent/x; COCKPIT=c; PIDF=/tmp/p; rc=0\n'
              f'log() {{ :; }}\nfinish() {{{body}\n}}\n')
    assert _bash(script + "LANE_STARTED=1; finish").returncode == 0
    assert sorted(removed.read_text().split()) == sorted([new_name, "run-gate-legacy"])


# ---- --base must reach the host (job exit 72 'base ... missing on the host') ----

BASE = "c" * 40


def _base_ctx(tmp_path, handlers, name):
    ctx, _ = ctx_for(tmp_path, handlers + git_handlers(count="3") + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--base", "main", "--name", name)) == 0
    return ctx


def test_base_commit_rides_the_thin_bundle_under_a_temporary_ref(tmp_path):
    ctx = _base_ctx(tmp_path, [], "b1")
    calls = [c[0] for c in ctx.runner.calls]
    bundle = next(c for c in calls if "bundle" in c and "create" in c)
    assert "HEAD" in bundle and rg.BASE_REF in bundle and f"^{SEED}" in bundle
    i_set = next(i for i, c in enumerate(calls) if "update-ref" in c and BASE in c and rg.BASE_REF in c)
    i_bundle = calls.index(bundle)
    i_del = next(i for i, c in enumerate(calls) if "update-ref" in c and "-d" in c and rg.BASE_REF in c)
    assert i_set < i_bundle < i_del  # the ref exists only while the bundle is built
    job = (tmp_path / "out" / "b1" / "job.sh").read_text()
    assert f"BASE_SHA={BASE}" in job and "ls-remote" in job and "refs/remote-gate/base" in job


def test_base_not_on_the_seed_forces_a_bundle_even_when_head_is_there(tmp_path):
    def count(argv):  # HEAD is covered by the seed, the base commit is not
        return rg.Result(0, ("2" if BASE in argv else "0") + "\n")

    ctx = _base_ctx(tmp_path, [("rev-list --count", count)], "b2")
    assert any("bundle" in c[0] and "create" in c[0] for c in ctx.runner.calls)


def test_base_already_on_the_seed_needs_no_bundle(tmp_path):
    ctx = _base_ctx(tmp_path, [("rev-list --count", rg.Result(0, "0\n"))], "b3")
    assert not any("bundle" in c[0] and "create" in c[0] for c in ctx.runner.calls)
    assert not any("update-ref" in c[0] for c in ctx.runner.calls)


def test_no_base_means_no_temporary_ref(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "b4")) == 0
    assert not any("update-ref" in c[0] for c in ctx.runner.calls)
    bundle = next(c[0] for c in ctx.runner.calls if "bundle" in c[0] and "create" in c[0])
    assert rg.BASE_REF not in bundle


def test_failing_update_ref_aborts_before_the_bundle(tmp_path):
    handlers = [("update-ref", rg.Result(1, "", "boom"))] + git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))]
    ctx, _ = ctx_for(tmp_path, handlers)
    with pytest.raises(rg.GateError, match="cannot name the base commit"):
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--base", "main", "--name", "b5"))
    assert not any("bundle" in c[0] and "create" in c[0] for c in ctx.runner.calls)


def test_failed_bundle_still_removes_the_temporary_ref(tmp_path):
    handlers = [("bundle create", rg.Result(1, "", "nope"))] + git_handlers() + [psi(1, 1), ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))]
    ctx, _ = ctx_for(tmp_path, handlers)
    with pytest.raises(rg.GateError, match="git bundle failed"):
        rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--base", "main", "--name", "b6"))
    assert any("update-ref" in c[0] and "-d" in c[0] for c in ctx.runner.calls)


def test_real_git_and_bash_a_base_that_is_not_an_ancestor_of_head_reaches_the_host(tmp_path):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def sh(*a, cwd):
        return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()

    src = tmp_path / "src"
    src.mkdir()
    sh("git", "init", "-q", "-b", "main", cwd=src)
    sh("git", "commit", "-q", "--allow-empty", "-m", "seed", cwd=src)
    seed = sh("git", "rev-parse", "HEAD", cwd=src)
    host = tmp_path / "host"  # the host checkout holds only the seed commit
    sh("git", "clone", "-q", str(src), str(host), cwd=tmp_path)
    sh("git", "checkout", "-q", "-b", "feature", cwd=src)
    sh("git", "commit", "-q", "--allow-empty", "-m", "feature work", cwd=src)
    head = sh("git", "rev-parse", "HEAD", cwd=src)
    sh("git", "checkout", "-q", "main", cwd=src)
    sh("git", "commit", "-q", "--allow-empty", "-m", "main moved on", cwd=src)
    base = sh("git", "rev-parse", "HEAD", cwd=src)
    sh("git", "checkout", "-q", "feature", cwd=src)
    assert subprocess.run(["git", "merge-base", "--is-ancestor", base, head], cwd=src).returncode == 1  # main is NOT in the bundle's history
    assert subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=host).returncode != 0

    ctx = rg.Ctx(runner=rg.Runner(), hosts=hosts(), out_dir=tmp_path / "out")
    st = {"top": str(src), "sha": head, "base": base, "tags": []}
    dest = tmp_path / "rgdir" / "in.bundle"
    dest.parent.mkdir()
    assert rg.build_bundle(ctx, st, [seed], str(dest)) == seed
    assert dest.exists()
    assert sh("git", "for-each-ref", "refs/remote-gate", cwd=src) == ""  # the temporary ref is gone

    job = rg.render_job(hosts()["v1001"], "t7", {"sha": head, "base": base, "tags": []}, "suite", ".", "", 60, [])
    part = job[job.index("if [[ -f $D/in.bundle ]]"):job.index("# every release tag of the source")]
    script = f'D={dest.parent}\nCKDIR={host}\nBASE_SHA={base}\nMAIN_SHA=\nlog() {{ echo "$@" >&2; }}\n' + part
    r = subprocess.run(["bash", "-euo", "pipefail", "-c", script], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert subprocess.run(["git", "cat-file", "-e", f"{base}^{{commit}}"], cwd=host).returncode == 0
    assert subprocess.run(["git", "cat-file", "-e", f"{head}^{{commit}}"], cwd=host).returncode == 0


def test_the_closest_of_several_host_checkouts_is_the_seed(tmp_path):
    other = "d" * 40

    def count(argv):  # the second checkout is closer to HEAD and the base than SEED
        return rg.Result(0, ("1" if f"^{other}" in argv else "5") + "\n")

    ls = ("ls /home/runner/checkouts", rg.Result(0, f"{other}\n{SEED}\n"))  # nearer one first, farther one after: the farther must not replace it
    ctx, _ = ctx_for(tmp_path, [("rev-list --count", count)] + git_handlers() + [psi(1, 1), ls])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--base", "main", "--name", "b7")) == 0
    bundle = next(c[0] for c in ctx.runner.calls if "bundle" in c[0] and "create" in c[0])
    assert f"^{other}" in bundle and f"^{SEED}" not in bundle
