"""flakehunt: repeat a pytest selection under a named stress recipe until it fails N times or passes M times.

Standard library only. Two modes:

* ``container`` (default): every run is a throwaway docker container with an exact name, ``--init``,
  ``--cgroup-parent`` (default ``dev-gates.slice``), never privileged, never sharing a host namespace; the stress
  recipe is applied with cgroup limits (cpu.max, io throttle, memory) and exact-named sibling stressor containers.
  Everything it started is removed by exact name in a ``finally``.
* ``inner``: for use inside a lane container, where there is no docker. pytest runs as a child process and the
  CPU recipes use stdlib spinner processes; cgroup recipes (slow-cpu, slow-disk, mem-pressure) are applied by the
  OUTER container, so ``inner`` refuses them rather than pretend.

Exit status: 0 stable (M passes, no failure), 1 failure reached the N threshold, 2 infrastructure error
(pytest usage error, docker failure, PSI gate), 3 inconclusive (max runs reached without M passes).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

EXIT_STABLE, EXIT_FAILED, EXIT_ERROR, EXIT_INCONCLUSIVE = 0, 1, 2, 3
PSI_MAX_DEFAULT = 25.0
RUN_TIMEOUT_FLOOR_S = 120
TRACE_TAIL_LINES = 60
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,40}$")


class HuntError(Exception):
    """Infrastructure problem: the hunt cannot give a verdict."""


@dataclasses.dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner:
    """Executes argv lists. Tests substitute a fake with the same ``run`` signature."""

    def run(self, argv: Sequence[str], timeout: float | None = None) -> Result:
        try:
            done = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            return Result(124, _text(exc.stdout), _text(exc.stderr) + f"\nflakehunt: timed out after {timeout} s")
        return Result(done.returncode, done.stdout, done.stderr)

    def spawn(self, argv: Sequence[str]) -> subprocess.Popen:
        return subprocess.Popen(list(argv), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


@dataclasses.dataclass(frozen=True)
class Options:
    mode: str = "container"
    recipe: str = "slow-cpu"
    image: str = ""
    mounts: tuple[str, ...] = ()
    workdir: str = "/work"
    cgroup_parent: str = "dev-gates.slice"
    docker: str = "docker"
    cpus: float = 0.25
    cpu_percent: int = 90
    stress_workers: int = 4
    io_device: str = ""
    io_write_bps: str = "6mb"
    io_read_iops: int = 300
    memory: str = ""
    memory_swap: str = ""
    stress_image: str = ""
    env: tuple[str, ...] = ()
    run_id: str = ""
    psi_max: float = PSI_MAX_DEFAULT
    run_timeout: int = 3600
    fail_n: int = 1
    pass_m: int = 20
    max_runs: int = 40
    pytest_args: tuple[str, ...] = ()
    pytest_python: str = "python"


RECIPES = ("none", "slow-cpu", "cpu-contended", "slow-disk", "mem-pressure", "combo")
_INNER_RECIPES = ("none", "cpu-contended")


def validate(opts: Options) -> None:
    if opts.recipe not in RECIPES:
        raise HuntError(f"unknown recipe {opts.recipe!r}; choose from {', '.join(RECIPES)}")
    if opts.mode not in ("container", "inner"):
        raise HuntError(f"unknown mode {opts.mode!r}")
    if not NAME_RE.match(opts.run_id):
        raise HuntError(f"run id {opts.run_id!r} must match {NAME_RE.pattern} (it is part of exact container names)")
    if not opts.pytest_args:
        raise HuntError("no pytest selection given: pass it after `--`")
    if opts.run_timeout < RUN_TIMEOUT_FLOOR_S:
        raise HuntError(f"--run-timeout {opts.run_timeout} is below the {RUN_TIMEOUT_FLOOR_S} s failsafe floor")
    if opts.fail_n < 1 or opts.pass_m < 1 or opts.max_runs < 1:
        raise HuntError("--fail-n, --pass-m and --max-runs must be at least 1")
    if opts.mode == "inner":
        if opts.recipe not in _INNER_RECIPES:
            raise HuntError(
                f"recipe {opts.recipe!r} needs cgroup limits that only the outer container can apply; "
                f"inner mode supports: {', '.join(_INNER_RECIPES)}"
            )
        return
    if not opts.image:
        raise HuntError("container mode needs --image")
    if opts.recipe in ("slow-disk", "combo") and not opts.io_device:
        raise HuntError(f"recipe {opts.recipe} needs --io-device (block device of the docker root); it is not guessed")
    if opts.recipe in ("mem-pressure", "combo") and not opts.memory:
        raise HuntError(f"recipe {opts.recipe} needs --memory (observed peak x 0.8); it is not guessed")


def uses_cpu_stress(opts: Options) -> bool:
    return opts.recipe in ("cpu-contended", "combo")


def uses_mem_stress(opts: Options) -> bool:
    return opts.recipe in ("mem-pressure", "combo")


def lane_name(opts: Options, index: int) -> str:
    return f"flakehunt-{opts.run_id}-{index}-lane"


def stress_names(opts: Options, index: int) -> list[str]:
    names = []
    if uses_cpu_stress(opts):
        names.append(f"flakehunt-{opts.run_id}-{index}-cpu")
    if uses_mem_stress(opts):
        names.append(f"flakehunt-{opts.run_id}-{index}-mem")
    return names


def _base_docker(opts: Options, name: str) -> list[str]:
    return [opts.docker, "run", "--init", "--name", name, "--cgroup-parent", opts.cgroup_parent, "--pids-limit", "4096"]


def lane_argv(opts: Options, index: int) -> list[str]:
    argv = _base_docker(opts, lane_name(opts, index))
    if opts.recipe in ("slow-cpu", "combo"):
        argv += ["--cpus", f"{opts.cpus:g}"]
    if opts.recipe == "cpu-contended":
        argv += ["--cpu-shares", "10", "--cpuset-cpus", "0"]
    if opts.recipe == "combo":
        argv += ["--cpu-shares", "10"]
    if opts.recipe in ("slow-disk", "combo"):
        argv += ["--device-write-bps", f"{opts.io_device}:{opts.io_write_bps}", "--device-read-iops", f"{opts.io_device}:{opts.io_read_iops}"]
    if opts.memory:
        argv += ["--memory", opts.memory]
        if opts.memory_swap:
            argv += ["--memory-swap", opts.memory_swap]
    for mount in opts.mounts:
        argv += ["-v", mount]
    for item in opts.env:
        argv += ["-e", item]
    argv += ["-w", opts.workdir, opts.image, opts.pytest_python, "-m", "pytest", "-p", "no:cacheprovider", *opts.pytest_args]
    return argv


def stress_argvs(opts: Options, index: int) -> list[list[str]]:
    image = opts.stress_image or opts.image
    argvs = []
    if uses_cpu_stress(opts):
        argvs.append(
            [*_base_docker(opts, f"flakehunt-{opts.run_id}-{index}-cpu"), "-d", "--cpu-shares", "1000", "--cpuset-cpus", "0", image,
             "stress-ng", "--cpu", str(opts.stress_workers), "--cpu-load", str(opts.cpu_percent), "--timeout", f"{opts.run_timeout}s"]
        )
    if uses_mem_stress(opts):
        argvs.append(
            [*_base_docker(opts, f"flakehunt-{opts.run_id}-{index}-mem"), "-d", "--memory", opts.memory, image,
             "stress-ng", "--vm", "1", "--vm-bytes", "90%", "--timeout", f"{opts.run_timeout}s"]
        )
    return argvs


def read_psi(kind: str) -> float:
    with open(f"/proc/pressure/{kind}", encoding="ascii") as handle:
        for line in handle:
            if line.startswith("some "):
                return float(re.search(r"avg10=([0-9.]+)", line).group(1))
    raise HuntError(f"no 'some' line in /proc/pressure/{kind}")


def psi_gate(opts: Options, reader: Callable[[str], float] = read_psi) -> dict[str, float]:
    values = {kind: reader(kind) for kind in ("cpu", "memory")}
    busy = {k: v for k, v in values.items() if v > opts.psi_max}
    if busy:
        raise HuntError(f"host pressure above {opts.psi_max:g} (some avg10): {busy}; start only on an idle host")
    return values


def classify(result: Result) -> str:
    """pass / fail / error. pytest: 0 ok, 1 test failures; anything else is not a verdict about the tests."""
    if result.returncode == 0:
        return "pass"
    if result.returncode == 1:
        return "fail"
    return "error"


def trace_tail(result: Result) -> str:
    lines = (result.stdout + "\n" + result.stderr).strip().splitlines()
    return "\n".join(lines[-TRACE_TAIL_LINES:])


def run_inner_stress(opts: Options, runner: Runner) -> list[subprocess.Popen]:
    if not uses_cpu_stress(opts):
        return []
    busy = opts.cpu_percent / 100.0
    code = (
        "import os,time\n"
        "os.nice(5)\n"
        f"b={busy}\n"
        "while True:\n"
        " t=time.monotonic()\n"
        " while time.monotonic()-t<0.1*b: pass\n"
        " time.sleep(0.1*(1-b))\n"
    )
    return [runner.spawn([sys.executable, "-c", code]) for _ in range(opts.stress_workers)]


def stop_inner(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        proc.kill()
    for proc in procs:
        proc.wait()


def cleanup(opts: Options, runner: Runner, names: list[str]) -> list[str]:
    """Remove exactly the containers this hunt named. Returns names whose removal failed."""
    failed = []
    for name in names:
        done = runner.run([opts.docker, "rm", "-f", name], timeout=120)
        if done.returncode != 0 and "No such container" not in done.stderr:
            failed.append(name)
    return failed


def one_run(opts: Options, runner: Runner, index: int) -> tuple[str, Result]:
    if opts.mode == "inner":
        procs = run_inner_stress(opts, runner)
        try:
            argv = [opts.pytest_python, "-m", "pytest", "-p", "no:cacheprovider", *opts.pytest_args]
            result = runner.run(argv, timeout=opts.run_timeout)
        finally:
            stop_inner(procs)
        return classify(result), result
    names = [*stress_names(opts, index), lane_name(opts, index)]
    try:
        for argv in stress_argvs(opts, index):
            started = runner.run(argv, timeout=120)
            if started.returncode != 0:
                raise HuntError(f"stressor failed to start: {started.stderr.strip()}")
        result = runner.run(lane_argv(opts, index), timeout=opts.run_timeout + 60)
    finally:
        left = cleanup(opts, runner, names)
    if left:
        raise HuntError(f"could not remove containers by exact name: {', '.join(left)}")
    return classify(result), result


def hunt(opts: Options, runner: Runner, *, psi: Callable[[str], float] = read_psi) -> dict:
    validate(opts)
    report: dict = {"recipe": opts.recipe, "mode": opts.mode, "run_id": opts.run_id, "selection": list(opts.pytest_args),
                    "psi_at_start": psi_gate(opts, psi), "runs": [], "passes": 0, "failures": 0, "first_failure": None}
    for index in range(1, opts.max_runs + 1):
        started = time.monotonic()
        outcome, result = one_run(opts, runner, index)
        entry = {"run": index, "outcome": outcome, "returncode": result.returncode, "seconds": round(time.monotonic() - started, 2)}
        report["runs"].append(entry)
        if outcome == "error":
            report.update(verdict="error", exit_code=EXIT_ERROR, error_trace=trace_tail(result))
            return report
        if outcome == "pass":
            report["passes"] += 1
        else:
            report["failures"] += 1
            if report["first_failure"] is None:
                report["first_failure"] = {"run": index, "trace": trace_tail(result)}
        if report["failures"] >= opts.fail_n:
            report.update(verdict="failed", exit_code=EXIT_FAILED)
            return report
        if report["passes"] >= opts.pass_m:
            report.update(verdict="stable", exit_code=EXIT_STABLE)
            return report
    report.update(verdict="inconclusive", exit_code=EXIT_INCONCLUSIVE)
    return report


def dry_run_plan(opts: Options) -> list[str]:
    validate(opts)
    lines = [f"recipe={opts.recipe} mode={opts.mode} fail_n={opts.fail_n} pass_m={opts.pass_m} max_runs={opts.max_runs}"]
    if opts.mode == "inner":
        lines.append(f"stdlib spinners: {opts.stress_workers if uses_cpu_stress(opts) else 0} at {opts.cpu_percent}%")
        lines.append(" ".join([opts.pytest_python, "-m", "pytest", "-p", "no:cacheprovider", *opts.pytest_args]))
        return lines
    for argv in stress_argvs(opts, 1):
        lines.append("stressor: " + " ".join(argv))
    lines.append("lane: " + " ".join(lane_argv(opts, 1)))
    lines.append("cleanup: " + "; ".join(f"{opts.docker} rm -f {n}" for n in [*stress_names(opts, 1), lane_name(opts, 1)]))
    return lines


def render(report: dict) -> str:
    head = (f"flakehunt {report['recipe']}: {report['verdict']} after {len(report['runs'])} runs "
            f"({report['passes']} pass, {report['failures']} fail)")
    tail = report.get("first_failure") or {}
    if tail:
        head += f"\nfirst failure (run {tail['run']}):\n{tail['trace']}"
    if report["verdict"] == "error":
        head += f"\nerror trace:\n{report['error_trace']}"
    return head


def parse_args(argv: Sequence[str]) -> tuple[Options, argparse.Namespace]:
    if "--" in argv:
        split = list(argv).index("--")
        own, selection = list(argv[:split]), tuple(argv[split + 1:])
    else:
        own, selection = list(argv), ()
    parser = argparse.ArgumentParser(prog="flakehunt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recipe", choices=RECIPES, default="slow-cpu")
    parser.add_argument("--mode", choices=("container", "inner"), default="container")
    parser.add_argument("--image", default="")
    parser.add_argument("--stress-image", default="", help="image with stress-ng (default: --image)")
    parser.add_argument("-v", "--mount", action="append", default=[], help="docker -v SRC:DST[:ro]; repeatable")
    parser.add_argument("-e", "--env", action="append", default=[], help="docker -e KEY=VALUE; repeatable")
    parser.add_argument("--workdir", default="/work")
    parser.add_argument("--cgroup-parent", default="dev-gates.slice")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--cpus", type=float, default=0.25, help="slow-cpu/combo lane cpu.max in cores")
    parser.add_argument("--cpu-percent", type=int, default=90, help="stressor load per worker in percent")
    parser.add_argument("--stress-workers", type=int, default=4)
    parser.add_argument("--io-device", default="", help="block device for slow-disk, e.g. /dev/vda")
    parser.add_argument("--io-write-bps", default="6mb")
    parser.add_argument("--io-read-iops", type=int, default=300)
    parser.add_argument("--memory", default="", help="lane memory limit for mem-pressure (observed peak x 0.8)")
    parser.add_argument("--memory-swap", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--psi-max", type=float, default=PSI_MAX_DEFAULT)
    parser.add_argument("--run-timeout", type=int, default=3600)
    parser.add_argument("--fail-n", type=int, default=1)
    parser.add_argument("--pass-m", type=int, default=20)
    parser.add_argument("--max-runs", type=int, default=40)
    parser.add_argument("--python", default="python", help="python inside the lane image")
    parser.add_argument("--report", default="", help="write the JSON report here")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and exit; starts nothing")
    ns = parser.parse_args(own)
    opts = Options(mode=ns.mode, recipe=ns.recipe, image=ns.image, mounts=tuple(ns.mount), workdir=ns.workdir,
                   cgroup_parent=ns.cgroup_parent, docker=ns.docker, cpus=ns.cpus, cpu_percent=ns.cpu_percent,
                   stress_workers=ns.stress_workers, io_device=ns.io_device, io_write_bps=ns.io_write_bps,
                   io_read_iops=ns.io_read_iops, memory=ns.memory, memory_swap=ns.memory_swap, stress_image=ns.stress_image,
                   env=tuple(ns.env), run_id=ns.run_id or f"{os.getpid()}-{int(time.time())}", psi_max=ns.psi_max,
                   run_timeout=ns.run_timeout, fail_n=ns.fail_n, pass_m=ns.pass_m, max_runs=ns.max_runs,
                   pytest_args=selection, pytest_python=ns.python)
    return opts, ns


def main(argv: Sequence[str] | None = None, runner: Runner | None = None, psi: Callable[[str], float] = read_psi) -> int:
    opts, ns = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if ns.dry_run:
            print("\n".join(dry_run_plan(opts)))
            return EXIT_STABLE
        report = hunt(opts, runner or Runner(), psi=psi)
    except HuntError as exc:
        print(f"flakehunt: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if ns.report:
        with open(ns.report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    print(render(report))
    return report["exit_code"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
