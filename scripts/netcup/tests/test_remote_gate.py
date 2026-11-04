"""Hermetic tests for runner/remote-gate.py: a fake Runner stands in for ssh, git and the identity check."""
from __future__ import annotations

import importlib.util
import json
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


def ctx_for(tmp_path, handlers):
    out = []
    bundle_sum = rg.hashlib.sha256(b"bundle").hexdigest() + "  in.bundle\n"
    handlers = [("test -e /home/runner/rg/", rg.Result(1))] + list(handlers) + [("sha256sum", rg.Result(0, bundle_sum))]
    return rg.Ctx(runner=Fake(handlers), hosts=hosts(), out_dir=tmp_path / "out", sleep=lambda s: None, say=out.append), out


def git_handlers(dirty="", base_ok=True, count="3"):
    return [
        ("--show-toplevel", rg.Result(0, "/wt\n")),
        ("rev-parse HEAD", rg.Result(0, SHA + "\n")),
        ("status --porcelain", rg.Result(0, dirty)),
        ("--show-prefix", rg.Result(0, "scripts/netcup/\n")),
        ("--verify", rg.Result(0 if base_ok else 1, "c" * 40 + "\n")),
        ("cat-file -e", rg.Result(0)),
        ("rev-list --count", rg.Result(0, count + "\n")),
        ("bundle create", lambda argv: (Path(argv[5]).write_bytes(b"bundle"), rg.Result(0))[1]),
    ]


def psi(cpu, mem):
    return ("/proc/pressure/cpu /proc/pressure/memory", rg.Result(0, f"{cpu}\n{mem}\n"))


def run_args(tmp_path, *extra):
    return rg.parser().parse_args(["--out-dir", str(tmp_path / "out"), "run", *extra])


def test_auto_picks_the_lower_psi_host(tmp_path):
    calls = {}

    def per_host(argv):
        calls[argv[1]] = 1
        return rg.Result(0, "9.0\n1.0\n" if argv[1] == "r1002" else "3.0\n2.0\n")

    ctx, _ = ctx_for(tmp_path, [("/proc/pressure/cpu", per_host)])
    assert rg.choose_host(ctx, "auto").name == "v1001"


def test_auto_refuses_when_both_hosts_are_above_25(tmp_path):
    ctx, _ = ctx_for(tmp_path, [psi(30.0, 1.0)])
    with pytest.raises(rg.GateError) as e:
        rg.choose_host(ctx, "auto")
    assert e.value.code == 75 and "above PSI 25" in str(e.value)
    assert not any("run-job" in " ".join(c[0]) for c in ctx.runner.calls)


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
