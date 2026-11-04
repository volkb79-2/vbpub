import dataclasses
import json
import subprocess
import sys

import pytest

from flakehunt import hunt
from flakehunt.hunt import HuntError, Options, Result

IDLE = lambda kind: 1.0  # noqa: E731


class FakeRunner:
    """Records every argv; ``lane_codes`` is consumed one per lane run."""

    def __init__(self, lane_codes=(), rm_fail=False, stress_fail=False):
        self.calls, self.spawned = [], []
        self.lane_codes = list(lane_codes)
        self.rm_fail, self.stress_fail = rm_fail, stress_fail

    def run(self, argv, timeout=None):
        self.calls.append(list(argv))
        if argv[1] == "rm":
            return Result(1, "", "boom") if self.rm_fail else Result(0)
        if "-d" in argv:
            return Result(1, "", "no image") if self.stress_fail else Result(0, "cid")
        if argv[1] == "run":
            code = self.lane_codes.pop(0) if self.lane_codes else 0
            return Result(code, "out\n", "FAILED test_x\n" if code else "")
        code = self.lane_codes.pop(0) if self.lane_codes else 0  # inner mode: pytest itself
        return Result(code, "inner out", "")

    def spawn(self, argv):
        self.spawned.append(argv)
        return FakeProc()


class FakeProc:
    killed = waited = False

    def kill(self):
        self.killed = True

    def wait(self):
        self.waited = True


def opts(**kw):
    base = dict(image="img:1", run_id="t1", pytest_args=("tests/x.py::t",), pass_m=3, max_runs=5,
                io_device="/dev/vda", memory="1g")
    base.update(kw)
    return Options(**base)


def names(runner):
    return [c[c.index("--name") + 1] for c in runner.calls if "--name" in c]


def test_stable_after_m_passes_and_cleans_up_by_exact_name():
    runner = FakeRunner()
    report = hunt.hunt(opts(), runner, psi=IDLE)
    assert (report["verdict"], report["exit_code"], report["passes"]) == ("stable", 0, 3)
    removed = [c[3] for c in runner.calls if c[1] == "rm"]
    assert removed == ["flakehunt-t1-1-lane", "flakehunt-t1-2-lane", "flakehunt-t1-3-lane"]
    assert all(c[:3] == ["docker", "rm", "-f"] for c in runner.calls if c[1] == "rm")


def test_failure_stops_at_n_and_records_first_trace():
    report = hunt.hunt(opts(recipe="none", fail_n=2), FakeRunner([0, 1, 1]), psi=IDLE)
    assert (report["verdict"], report["exit_code"], report["failures"]) == ("failed", 1, 2)
    assert report["first_failure"]["run"] == 2 and "FAILED test_x" in report["first_failure"]["trace"]
    assert "first failure (run 2)" in hunt.render(report)


def test_inconclusive_when_max_runs_reached():
    report = hunt.hunt(opts(recipe="none", pass_m=9, max_runs=2, fail_n=5), FakeRunner([0, 1]), psi=IDLE)
    assert (report["verdict"], report["exit_code"]) == ("inconclusive", 3)


def test_pytest_usage_error_is_infrastructure_not_a_verdict():
    report = hunt.hunt(opts(recipe="none"), FakeRunner([2]), psi=IDLE)
    assert (report["verdict"], report["exit_code"]) == ("error", 2)
    assert "error trace" in hunt.render(report)


def test_lane_argv_per_recipe():
    lane = lambda **kw: hunt.lane_argv(opts(**kw), 1)  # noqa: E731
    assert "--cpus" in lane(recipe="slow-cpu") and "0.25" in lane(recipe="slow-cpu")
    contended = lane(recipe="cpu-contended")
    assert "--cpu-shares" in contended and "--cpuset-cpus" in contended
    disk = lane(recipe="slow-disk")
    assert "--device-write-bps" in disk and "/dev/vda:6mb" in disk and "/dev/vda:300" in disk
    mem = lane(recipe="mem-pressure", memory_swap="4g", mounts=("/a:/work:ro",), env=("K=V",))
    assert mem[mem.index("--memory") + 1] == "1g" and "--memory-swap" in mem and "-v" in mem and "-e" in mem
    combo = lane(recipe="combo")
    assert "--cpus" in combo and "--device-write-bps" in combo
    for argv in (disk, mem, combo, contended):
        assert "--privileged" not in argv and "--init" in argv
        assert argv[argv.index("--cgroup-parent") + 1] == "dev-gates.slice"
        assert not any(a in ("--pid=host", "--net=host", "--cgroupns=host") for a in argv)
    assert lane(recipe="none")[-3:] == ["-p", "no:cacheprovider", "tests/x.py::t"][-3:]


def test_stressors_are_exact_named_detached_and_lane_runs_last():
    runner = FakeRunner()
    hunt.hunt(opts(recipe="combo", pass_m=1), runner, psi=IDLE)
    started = names(runner)
    assert started == ["flakehunt-t1-1-cpu", "flakehunt-t1-1-mem", "flakehunt-t1-1-lane"]
    assert [c for c in runner.calls if "-d" in c] and sum("stress-ng" in c for c in runner.calls) == 2
    removed = [c[3] for c in runner.calls if c[1] == "rm"]
    assert set(removed) == set(started)


def test_cleanup_failure_and_stressor_failure_are_errors_but_still_clean_up():
    with pytest.raises(HuntError, match="could not remove"):
        hunt.hunt(opts(recipe="none"), FakeRunner(rm_fail=True), psi=IDLE)
    runner = FakeRunner(stress_fail=True)
    with pytest.raises(HuntError, match="stressor failed to start"):
        hunt.hunt(opts(recipe="cpu-contended"), runner, psi=IDLE)
    assert any(c[1] == "rm" for c in runner.calls)


def test_cleanup_tolerates_already_removed_containers():
    class Gone(FakeRunner):
        def run(self, argv, timeout=None):
            if argv[1] == "rm":
                return Result(1, "", "Error: No such container: x")
            return super().run(argv, timeout)

    assert hunt.hunt(opts(recipe="none", pass_m=1), Gone(), psi=IDLE)["verdict"] == "stable"


def test_psi_gate_refuses_a_busy_host():
    with pytest.raises(HuntError, match="pressure above 25"):
        hunt.hunt(opts(), FakeRunner(), psi=lambda kind: 40.0 if kind == "cpu" else 0.0)


@pytest.mark.parametrize(
    "kw,msg",
    [
        (dict(recipe="bogus"), "unknown recipe"),
        (dict(mode="x"), "unknown mode"),
        (dict(run_id="Bad Id"), "run id"),
        (dict(pytest_args=()), "no pytest selection"),
        (dict(run_timeout=60), "failsafe floor"),
        (dict(fail_n=0), "at least 1"),
        (dict(mode="inner", recipe="slow-cpu"), "outer container"),
        (dict(image=""), "needs --image"),
        (dict(recipe="slow-disk", io_device=""), "needs --io-device"),
        (dict(recipe="mem-pressure", memory=""), "needs --memory"),
    ],
)
def test_validation_refusals(kw, msg):
    with pytest.raises(HuntError, match=msg):
        hunt.validate(opts(**kw))


def test_inner_mode_runs_pytest_with_stdlib_spinners_and_kills_them():
    runner = FakeRunner([0, 1])
    report = hunt.hunt(opts(mode="inner", recipe="cpu-contended", image="", fail_n=1, stress_workers=2), runner, psi=IDLE)
    assert report["verdict"] == "failed"
    assert len(runner.spawned) == 4 and runner.spawned[0][0] == sys.executable
    assert not any(c[0] == "docker" for c in runner.calls)
    plain = FakeRunner()
    assert hunt.hunt(opts(mode="inner", recipe="none", image="", pass_m=1), plain, psi=IDLE)["verdict"] == "stable"
    assert plain.spawned == []


def test_dry_run_plan_container_and_inner_start_nothing():
    plan = hunt.dry_run_plan(opts(recipe="combo"))
    assert any(line.startswith("stressor:") for line in plan) and any(line.startswith("lane:") for line in plan)
    assert plan[-1].startswith("cleanup: docker rm -f flakehunt-t1-1-cpu")
    inner = hunt.dry_run_plan(opts(mode="inner", recipe="cpu-contended", image=""))
    assert "stdlib spinners: 4 at 90%" in inner[1]
    assert "stdlib spinners: 0" in hunt.dry_run_plan(opts(mode="inner", recipe="none", image=""))[1]


def test_main_dry_run_report_and_errors(tmp_path, capsys):
    argv = ["--image", "i", "--run-id", "m1", "--recipe", "none", "--dry-run", "--", "t.py"]
    assert hunt.main(argv) == 0
    assert "lane: docker run" in capsys.readouterr().out
    report_path = tmp_path / "r.json"
    code = hunt.main(["--image", "i", "--run-id", "m1", "--recipe", "none", "--pass-m", "1", "--report", str(report_path), "--", "t.py"],
                     runner=FakeRunner(), psi=IDLE)
    assert code == 0 and json.loads(report_path.read_text())["verdict"] == "stable"
    assert "stable after 1 runs" in capsys.readouterr().out
    assert hunt.main(["--image", "i", "--run-id", "m1"], runner=FakeRunner(), psi=IDLE) == 2
    assert "no pytest selection" in capsys.readouterr().err
    assert hunt.main(["--run-id", "m1", "--", "t.py"], runner=FakeRunner(), psi=IDLE) == 2


def test_main_default_run_id_is_valid(capsys):
    opts_, _ = hunt.parse_args(["--image", "i", "--", "t.py"])
    hunt.validate(opts_)
    assert opts_.cgroup_parent == "dev-gates.slice"


def test_psi_reader_parses_proc_format(tmp_path, monkeypatch):
    good = tmp_path / "cpu"
    good.write_text("some avg10=3.50 avg60=1.00 avg300=0.50 total=1\nfull avg10=0.00 avg60=0 avg300=0 total=0\n")
    real_open = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: real_open(good if "/proc/pressure" in str(path) else path, *a, **k))
    assert hunt.read_psi("cpu") == 3.5
    good.write_text("full avg10=0.00\n")
    with pytest.raises(HuntError, match="no 'some' line"):
        hunt.read_psi("cpu")


def test_real_runner_runs_times_out_and_spawns():
    runner = hunt.Runner()
    ok = runner.run([sys.executable, "-c", "print('hi')"], timeout=60)
    assert (ok.returncode, ok.stdout.strip()) == (0, "hi")
    slow = runner.run([sys.executable, "-c", "import time; print('x', flush=True); time.sleep(30)"], timeout=1)
    assert slow.returncode == 124 and "timed out" in slow.stderr
    proc = runner.spawn([sys.executable, "-c", "import time; time.sleep(30)"])
    hunt.stop_inner([proc])
    assert proc.returncode is not None
    assert hunt._text(None) == "" and hunt._text(b"a") == "a" and hunt._text("a") == "a"


def test_inner_spinner_script_is_valid_python():
    captured = []

    class Spy(FakeRunner):
        def spawn(self, argv):
            captured.append(argv[2])
            return FakeProc()

    hunt.run_inner_stress(opts(mode="inner", recipe="cpu-contended", image="", stress_workers=1), Spy())
    compile(captured[0], "spinner", "exec")


def test_main_without_report_file(capsys):
    code = hunt.main(["--image", "i", "--run-id", "m2", "--recipe", "none", "--pass-m", "1", "--", "t.py"], runner=FakeRunner(), psi=IDLE)
    assert code == 0 and "stable" in capsys.readouterr().out
