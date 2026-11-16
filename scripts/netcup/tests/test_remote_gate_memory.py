"""Hermetic tests for the live-memory admission of runner/remote-gate.py: the rendered job's own bash functions run against
meminfo fixtures, and the ticket is exercised with real flock. No host, no ssh, no container."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from test_remote_gate import PREFLIGHT_OK, SHA, SEED, Fake, ctx_for, git_handlers, hosts, psi, rg, run_args  # noqa: F401

LS = ("ls /home/runner/checkouts", rg.Result(0, SEED + "\n"))

# 8 GiB box: MemTotal 7934 MiB, plenty or little available.
MEM_OK = "MemTotal: 8125000 kB\nMemFree: 400000 kB\nMemAvailable: 7300000 kB\n"
MEM_TIGHT = "MemTotal: 8125000 kB\nMemFree: 400000 kB\nMemAvailable: 3000000 kB\n"


def _job(need=0, floor=1024, wait=900, settle=20):
    st = {"sha": SHA, "base": None, "tags": []}
    return rg.render_job(hosts()["v1001"], "n1", st, "suite", ".", "", 60, [], "vbpub", None, need, floor, wait, settle)


def _admission_functions(need, floor):
    """The job's own `mem_admit_ok`, verbatim, plus the four values it reads."""
    job = _job(need, floor)
    start = job.index("mem_admit_ok() {")
    end = job.index("# PEP 440 ordering", start)
    return f"MEM_NEED_MB={need}\nMEM_FLOOR_MB={floor}\nMEMINFO=$MEMINFO\n" + job[start:end]


def _ask(fn_text, meminfo_text, tmp_path):
    """Run the job's function and print its three-way outcome (0 admitted, 1 wait, 2 unreadable) and its MEM_LAST line."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(meminfo_text)
    script = 'set -u\nlog() { printf "%s\\n" "$*" >&2; }\n' + fn_text + """
mem_admit_ok; rc=$?
printf 'RC=%s\\n' "$rc"
printf 'LAST=%s\\n' "$MEM_LAST"
"""
    return subprocess.run(["bash", "-c", script], env={**os.environ, "MEMINFO": str(meminfo)}, capture_output=True, text=True)


def _outcome(r):
    assert r.returncode == 0, r.stderr
    lines = dict(x.split("=", 1) for x in r.stdout.splitlines())
    return int(lines["RC"]), lines["LAST"]


def test_an_idle_host_admits_the_lane(tmp_path):
    rc, last = _outcome(_ask(_admission_functions(1500, 1024), MEM_OK, tmp_path))
    assert rc == 0
    assert "MemAvailable 7128 MiB" in last and "lane peak 1500 MiB" in last and "floor 1024 MiB" in last


def test_a_host_without_enough_memory_asks_to_wait_and_does_not_admit(tmp_path):
    rc, last = _outcome(_ask(_admission_functions(3500, 1024), MEM_TIGHT, tmp_path))
    assert rc == 1  # "wait", never "admitted" and never "unreadable"
    assert "lane peak 3500 MiB" in last


def test_the_floor_is_inclusive_and_the_comparison_is_not_off_by_one(tmp_path):
    avail_mib, need, floor = 3000, 1976, 1024
    text = f"MemTotal: 8125000 kB\nMemAvailable: {avail_mib * 1024} kB\n"
    assert _outcome(_ask(_admission_functions(need, floor), text, tmp_path))[0] == 0  # the difference EQUALS the floor
    assert _outcome(_ask(_admission_functions(need + 1, floor), text, tmp_path))[0] == 1  # one MiB below the floor


def test_a_lane_larger_than_ram_runs_alone_instead_of_waiting_forever(tmp_path):
    rc, last = _outcome(_ask(_admission_functions(99999, 1024), MEM_OK, tmp_path))
    assert rc == 0  # capped to MemTotal - 2 x floor, so one oversized lane is never refused for being oversized
    assert "lane peak 5886 MiB (declared 99999)" in last  # 7934 - 2 x 1024
    # the cap is what makes it fit: without a cap the 99999 MiB lane would never be admitted
    assert "MemTotal 7934 MiB" in last


def test_the_capped_lane_can_still_be_refused_when_the_host_is_occupied(tmp_path):
    rc, last = _outcome(_ask(_admission_functions(99999, 1024), MEM_TIGHT, tmp_path))
    assert rc == 1  # occupied host: the oversized lane waits, it is not silently admitted
    assert "lane peak 5886 MiB (declared 99999)" in last


@pytest.mark.parametrize("text, needle", [
    ("", "cannot read MemTotal/MemAvailable"),
    ("MemTotal: 8125000 kB\n", "MemAvailable 'unreadable'"),
    ("MemFree: 100 kB\nMemAvailable: 9000000 kB\n", "MemTotal 'unreadable'"),
    ("MemTotal: nope kB\nMemAvailable: 9000000 kB\n", "cannot read MemTotal/MemAvailable"),
])
def test_an_unreadable_meminfo_is_indeterminate_and_never_admits(tmp_path, text, needle):
    rc, last = _outcome(_ask(_admission_functions(1500, 1024), text, tmp_path))
    assert rc == 2  # a fact that exists and could not be read is NOT "there is plenty of memory"
    assert needle in last


def test_a_negative_floor_or_peak_cannot_sneak_through_the_cli(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    for args, needle in (
        (["--mem-need-mb", "-5"], "--mem-need-mb"),
        (["--mem-floor-mb", "0"], "--mem-floor-mb"),
        (["--mem-floor-mb", "-7"], "--mem-floor-mb"),
        (["--wait-mem", "-1"], "--wait-mem"),
    ):
        with pytest.raises(rg.GateError) as e:
            rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "m1", *args))
        assert e.value.code == 2 and needle in str(e.value)
    assert not any("run-job" in " ".join(c[0]) for c in ctx.runner.calls)  # refused before anything is started


def test_the_expected_peak_reaches_the_host_job(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "m2",
                                     "--mem-need-mb", "1500", "--mem-floor-mb", "1280", "--wait-mem", "60")) == 0
    job = (tmp_path / "out" / "m2" / "job.sh").read_text()
    assert "MEM_NEED_MB=1500\n" in job and "MEM_FLOOR_MB=1280\n" in job and "MEM_WAIT=60\n" in job
    assert "@@" not in job
    assert 'MEMINFO=${MEMINFO:-/proc/meminfo}' in job


def test_the_default_admission_checks_the_floor_only_and_says_so(tmp_path):
    ctx, _ = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    assert rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "m3")) == 0
    job = (tmp_path / "out" / "m3" / "job.sh").read_text()
    assert "MEM_NEED_MB=0\n" in job and "MEM_FLOOR_MB=1024\n" in job and "MEM_WAIT=900\n" in job
    assert "--mem-need-mb" in rg.parser().format_help()


def test_the_ticket_is_one_host_wide_file_not_one_per_run():
    """Two runs on one host must contend for the SAME inode; a per-run path would let them read the same MemAvailable."""
    job = _job()
    assert 'exec 9>>"$HOME/rg/.mem-admit.lock"' in job
    lane_call = next(line for line in job.splitlines() if line.lstrip().startswith('"$COCKPIT" sh -c') )
    assert lane_call.endswith("9>&- &")  # the lane itself never inherits the ticket fd
    assert '( sleep "$MEM_SETTLE" ) &' in job  # the ticket is held until the started lane's memory is visible
    assert job.index('( sleep "$MEM_SETTLE" ) &') < job.index("exec 9>&-")


def _ticket_block(need=0, floor=1024, wait=0, settle=0):
    job = _job(need, floor, wait, settle)
    start = job.index("# memory admission: one host-wide ticket")
    end = job.index('args=("$LANE"', start)
    return job[start:end]


def test_a_held_ticket_refuses_with_exit_75_and_starts_no_lane(tmp_path):
    home = tmp_path / "home"
    (home / "rg").mkdir(parents=True)
    marker = tmp_path / "held"
    holder = subprocess.Popen(
        ["bash", "-c", f'exec 9>>"$HOME/rg/.mem-admit.lock"; flock 9; : > {str(marker)!r}; sleep 60'],
        env={**os.environ, "HOME": str(home)},
    )
    try:
        deadline = time.monotonic() + 10  # synchronise on the holder's own marker: a bounded poll, never a fixed sleep
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "the holding process did not take the ticket"
        meminfo = tmp_path / "meminfo"
        meminfo.write_text(MEM_OK)
        block = 'set -u\nlog() { printf "%s\\n" "$*"; }\nrc=70\n' + _admission_functions(1500, 1024) + f"MEM_WAIT=0\nMEM_SETTLE=0\n" + _ticket_block(wait=0)
        r = subprocess.run(["bash", "-c", block], env={**os.environ, "HOME": str(home), "MEMINFO": str(meminfo)},
                           capture_output=True, text=True)
        assert r.returncode == 75
        assert "memory admission ticket not obtained within 0s" in r.stdout
        assert "memory admission ok" not in r.stdout  # the lane was never admitted
    finally:
        holder.terminate()
        holder.wait(timeout=30)


def test_the_ticket_is_released_with_the_job_so_the_next_lane_can_start(tmp_path):
    home = tmp_path / "home"
    (home / "rg").mkdir(parents=True)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(MEM_OK)
    block = 'set -u\nlog() { :; }\nrc=70\n' + _admission_functions(1500, 1024) + f"MEM_WAIT=0\nMEM_SETTLE=0\n" + _ticket_block(wait=0, settle=0)
    for run in (1, 2):
        r = subprocess.run(["bash", "-c", block], env={**os.environ, "HOME": str(home), "MEMINFO": str(meminfo)},
                           capture_output=True, text=True)
        assert r.returncode == 0, (run, r.stdout, r.stderr)


def test_a_memory_refusal_is_not_reported_as_a_psi_refusal(tmp_path):
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "m4"))
    job_log = ("2026-11-16T00:00:00Z PSI ok (waited 0s)\n"
               "2026-11-16T00:00:01Z memory admission: MemAvailable 900 MiB, lane peak 1500 MiB (declared 1500), "
               "floor 1024 MiB, MemTotal 7934 MiB: refusing to start after 0s\n"
               "2026-11-16T00:00:02Z done rc=75\n")
    ctx.runner.handlers.insert(0, ("jobs/rg-m4/job.log", rg.Result(0, job_log)))
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(0, "75\n")))
    ctx.runner.handlers.insert(0, ("/rc", rg.Result(0, "75\n")))
    host, meta, run_dir = rg.load_meta(ctx, "m4")
    assert rg.collect(ctx, host, meta, run_dir) == 75
    assert out[-1].startswith("lane suite verdict MEM_REFUSED; exit_code 75; reason memory admission")
    assert json.loads((run_dir / "result.json").read_text())["verdict"] == "MEM_REFUSED"


def test_a_psi_refusal_keeps_its_own_verdict(tmp_path):
    ctx, out = ctx_for(tmp_path, git_handlers() + [psi(1, 1), LS])
    rg.cmd_run(ctx, run_args(tmp_path, "v1001", "/wt", "suite", "--detach", "--name", "m5"))
    job_log = "2026-11-16T00:00:00Z PSI above 25 (memory some / io full) after 900s: refusing to start\n2026-11-16T00:00:01Z done rc=75\n"
    ctx.runner.handlers.insert(0, ("jobs/rg-m5/job.log", rg.Result(0, job_log)))
    ctx.runner.handlers.insert(0, ("/EXIT", rg.Result(0, "75\n")))
    ctx.runner.handlers.insert(0, ("/rc", rg.Result(0, "75\n")))
    host, meta, run_dir = rg.load_meta(ctx, "m5")
    assert rg.collect(ctx, host, meta, run_dir) == 75
    assert out[-1].startswith("lane suite verdict PSI_REFUSED; exit_code 75")


def test_the_admission_job_script_stays_valid_bash_and_shellcheck_clean(tmp_path):
    import shutil
    path = tmp_path / "job.sh"
    path.write_text(_job(1500, 1024, 60, 5))
    subprocess.run(["bash", "-n", str(path)], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-x", str(path)], check=True)
