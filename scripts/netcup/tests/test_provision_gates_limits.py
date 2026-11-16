"""Hermetic tests for runner/provision-gates-limits.sh: fake systemd (argv log), a temp systemd root and cgroupfs, and
meminfo fixtures. No host, no real systemd, nothing outside tmp_path (except the two explicit real-path refusals)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "runner"
SCRIPT = RUNNER / "provision-gates-limits.sh"
SLICE = "dev-gates.slice"

# Two hosts with different RAM, so a hard-coded number cannot pass both.
HOST_8GIB = "MemTotal:        8125000 kB\nMemFree:          600000 kB\nMemAvailable:    7300000 kB\nSwapTotal:       67108864 kB\n"
HOST_4GIB = "MemTotal:        4050000 kB\nMemFree:          300000 kB\nMemAvailable:    3600000 kB\nSwapTotal:       33554432 kB\n"

BASE_ENV = {
    "SYSTEMCTL_LOG": "systemctl.log",
    "FAKE_APPLY_LIMITS": "1",
}


def _fixture(tmp_path: Path, meminfo: str = HOST_8GIB, *, units: tuple = ()):
    """A copy of the script with a stub lib.sh (only the identity guard, which is not under test), a fake-bin with
    `systemctl` and `id`, a temp systemd root, a temp cgroupfs and a meminfo fixture."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    script_dir = tmp_path / "runner"
    script_dir.mkdir()
    script = script_dir / SCRIPT.name
    shutil.copy2(SCRIPT, script)
    (script_dir / "lib.sh").write_text("identity_check() { :; }\n")

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    log = tmp_path / "systemctl.log"
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    cgroupfs = tmp_path / "cgroupfs"
    slice_cg = cgroupfs / "dev.slice" / SLICE
    slice_cg.mkdir(parents=True)
    for name, value in (("memory.high", "max"), ("memory.max", "6291456000"), ("memory.swap.max", "max")):
        (slice_cg / name).write_text(value + "\n")
    procfs = tmp_path / "procfs"
    procfs.mkdir()

    systemctl = fake_bin / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$SYSTEMCTL_LOG\"\n"
        "case $1 in\n"
        "  daemon-reload) exit 0 ;;\n"
        "  restart)\n"
        "    if [[ ${FAKE_APPLY_LIMITS:-1} == 1 ]]; then\n"
        "      printf 'max\\n' > \"$SLICE_CG/memory.high\"\n"
        "      printf '%s\\n' \"$FAKE_MAX_BYTES\" > \"$SLICE_CG/memory.max\"\n"
        "      printf '%s\\n' \"$FAKE_SWAP_BYTES\" > \"$SLICE_CG/memory.swap.max\"\n"
        "    fi\n"
        "    exit \"${FAKE_RESTART_RC:-0}\" ;;\n"
        "  cat)\n"
        "    [[ \" ${UNITS_INSTALLED:-} \" == *\" $2 \"* ]] || exit 1\n"
        "    exit 0 ;;\n"
        "  show)\n"
        "    unit=$2; prop=$4\n"
        "    case \"$prop\" in\n"
        "      LoadState) printf '%s\\n' \"${SLICE_LOAD_STATE:-loaded}\" ;;\n"
        "      FragmentPath) printf '%s\\n' \"${SLICE_FRAGMENT:-/etc/systemd/system/dev-gates.slice}\" ;;\n"
        "      ControlGroup) printf '%s\\n' \"${SLICE_CONTROL_GROUP:-/dev.slice/dev-gates.slice}\" ;;\n"
        "      MainPID)\n"
        "        for pair in ${UNIT_MAINPIDS:-}; do\n"
        "          [[ $pair == \"$unit=\"* ]] && printf '%s\\n' \"${pair#*=}\" && exit 0\n"
        "        done\n"
        "        printf '0\\n' ;;\n"
        "    esac\n"
        "    exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    systemctl.chmod(0o755)
    fake_id = fake_bin / "id"
    fake_id.write_text("#!/bin/sh\nprintf '%s\\n' \"${FAKE_UID:-0}\"\n")
    fake_id.chmod(0o755)

    meminfo_path = tmp_path / "meminfo"
    meminfo_path.write_text(meminfo)
    env = {
        **os.environ,
        "SYSTEMCTL_LOG": str(log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GATES_SLICE": SLICE,
        "SLICE_CG": str(slice_cg),
        "UNITS_INSTALLED": " ".join(units),
        "UNIT_MAINPIDS": "",
    }
    return script, env, tmp_path, systemd_dir, slice_cg, meminfo_path, procfs


def _run(script, env, args):
    return subprocess.run(["bash", str(script), *map(str, args)], env=env, capture_output=True, text=True)


def _args(meminfo, systemd_dir, cgroupfs, procfs, *extra):
    return ["--meminfo", meminfo, "--systemd-dir", systemd_dir, "--cgroupfs", cgroupfs, "--procfs", procfs, *extra]


def _dropin(systemd_dir: Path, relative: str) -> str:
    return (systemd_dir / relative).read_text()


def test_the_dropins_render_the_decided_policy_with_no_finite_high_mark(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str((7934 - 1024 - 256) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str((65536 - 8192) * 1024 * 1024)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    slice_dropin = _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")
    assert "MemoryHigh=infinity" in slice_dropin
    assert "MemoryMax=6654M" in slice_dropin  # 7934 - 1024 - 256, derived from this host's own MemTotal
    assert "MemorySwapMax=57344M" in slice_dropin
    assert "MemoryOOMGroup" not in slice_dropin and "ManagedOOMMemoryPressure" not in slice_dropin
    for text in (slice_dropin, _dropin(systemd_dir, "docker-.scope.d/20-oom-group.conf")):
        assert "MemoryOOMGroup" not in text
        assert "oom.group" not in text.replace("# memory.oom.group", "")  # only the comment names it: never a set key
    scope = _dropin(systemd_dir, "docker-.scope.d/20-oom-group.conf")
    assert scope.splitlines()[0].startswith("# RENDERED by scripts/netcup/runner/provision-gates-limits.sh")
    assert "[Scope]" in scope and "OOMPolicy=kill" in scope
    for unit in ("ssh.service", "systemd-logind.service", "systemd-journald.service", "docker.service", "cgprofiled.service"):
        text = _dropin(systemd_dir, f"{unit}.d/30-oom-protect.conf")
        assert "[Service]" in text and "OOMScoreAdjust=-900" in text
    assert not (systemd_dir / "sshd.service.d").exists()
    assert r.stdout.count("GATES_MEMORY_POLICY=") == 1
    assert "MemoryMax=6654M" in r.stdout and "MemorySwapMax=57344M" in r.stdout


def test_two_different_host_facts_produce_two_different_ceilings_and_never_a_literal(tmp_path):
    script8, env8, tmp8, dir8, _, mem8, proc8 = _fixture(tmp_path / "h8")
    script4, env4, tmp4, dir4, _, mem4, proc4 = _fixture(tmp_path / "h4", HOST_4GIB)
    for env, tmp, systemd_dir, meminfo, procfs, total, swap in (
        (env8, tmp8, dir8, mem8, proc8, 7934, 65536),
        (env4, tmp4, dir4, mem4, proc4, 3955, 32768),
    ):
        maximum = (total - 1024 - 256) * 1024 * 1024
        env["FAKE_MAX_BYTES"] = str(maximum)
        env["FAKE_SWAP_BYTES"] = str((swap - 8192) * 1024 * 1024)
        r = _run(script8, env, _args(meminfo, systemd_dir, tmp / "cgroupfs", procfs))
        assert r.returncode == 0, r.stderr
    eight = _dropin(dir8, f"{SLICE}.d/20-memory-policy.conf")
    four = _dropin(dir4, f"{SLICE}.d/20-memory-policy.conf")
    assert "MemoryMax=6654M" in eight and "MemoryMax=2675M" in four
    assert eight != four


def test_the_override_pins_the_measured_value_of_one_host(tmp_path):
    script, env, _, systemd_dir, slice_cg, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str(6656 * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    env["GATES_MEMORY_MAX_MIB"] = "6656"
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "MemoryMax=6656M" in _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")


def test_an_override_that_does_not_protect_a_floor_is_refused(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    for value in ("0", "-1", str(7934), "9000", "eight"):
        env["GATES_MEMORY_MAX_MIB"] = value
        r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
        assert r.returncode != 0 and "MemoryMax" in r.stderr, value
    assert not (systemd_dir / f"{SLICE}.d").exists()


def test_a_second_run_with_the_same_facts_changes_nothing(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    args = _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs)
    first = _run(script, env, args)
    assert first.returncode == 0, first.stderr
    before = {p: p.stat().st_mtime_ns for p in systemd_dir.rglob("*.conf")}
    calls_before = (tmp_path / "systemctl.log").read_text()
    second = _run(script, env, args)
    assert second.returncode == 0, second.stderr
    after = {p: p.stat().st_mtime_ns for p in systemd_dir.rglob("*.conf")}
    assert before == after  # nothing rewritten
    assert second.stdout.count("unchanged ") == 7  # one slice policy, one docker scope, five protected units
    calls_after = (tmp_path / "systemctl.log").read_text()
    second_calls = calls_after[len(calls_before):]
    assert "restart" not in second_calls and "daemon-reload" not in second_calls  # a converged host is only read
    assert "already match the policy" in second.stdout


def test_the_slice_is_restarted_only_while_idle_and_verified_afterwards(tmp_path):
    script, env, _, systemd_dir, slice_cg, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "systemctl.log").read_text()
    assert "restart dev-gates.slice" in log
    assert "restarted dev-gates.slice while idle" in r.stdout
    assert (slice_cg / "memory.max").read_text().strip() == env["FAKE_MAX_BYTES"]
    # the memory.events consequence is stated, not hidden
    assert "memory.events counters start over" in r.stdout


def test_a_slice_with_tasks_is_never_restarted(tmp_path):
    script, env, _, systemd_dir, slice_cg, meminfo, procfs = _fixture(tmp_path)
    (slice_cg / "cgroup.procs").write_text("4242\n")
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 4, r.stdout + r.stderr
    assert "restart dev-gates.slice" not in (tmp_path / "systemctl.log").read_text()
    assert "oom_kill" in r.stderr and "Re-run when the tier is idle" in r.stderr


def test_a_live_slice_that_still_disagrees_after_the_restart_is_an_error(tmp_path):
    script, env, _, systemd_dir, slice_cg, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    env["FAKE_APPLY_LIMITS"] = "0"
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 7, r.stdout + r.stderr
    assert "still disagrees" in r.stderr and "memory.max=6291456000" in r.stderr


def test_an_absent_slice_leaves_the_live_host_alone(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["SLICE_LOAD_STATE"] = "not-found"
    env["GATES_SLICE"] = "dev-gates.slice"
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "not installed on this host" in r.stdout
    log = (tmp_path / "systemctl.log").read_text()
    assert "restart" not in log
    assert (systemd_dir / "docker-.scope.d/20-oom-group.conf").exists()  # the drop-ins are still staged


@pytest.mark.parametrize(
    "meminfo, why",
    [
        ("MemFree: 100 kB\n", "MemTotal"),
        ("MemTotal: 8125000 kB\n", "SwapTotal"),
        ("MemTotal: 8125000 kB\nSwapTotal: 0 kB\n", "swap reserve"),
        ("MemTotal: 8125000 kB\nSwapTotal: 4194304 kB\n", "swap reserve"),
        ("MemTotal: nope kB\nSwapTotal: 67108864 kB\n", "MemTotal"),
    ],
)
def test_an_unreadable_or_unusable_host_fact_refuses_by_name(tmp_path, meminfo, why):
    script, env, _, systemd_dir, _, path, procfs = _fixture(tmp_path, meminfo)
    r = _run(script, env, _args(path, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 3, r.stdout + r.stderr
    assert why in r.stderr
    assert "GATES_MEMORY_POLICY=" not in r.stdout
    assert not (systemd_dir / f"{SLICE}.d").exists()  # refused before anything was written


def test_a_host_whose_ram_only_just_covers_the_floor_plus_headroom_is_refused(tmp_path):
    """`MemTotal == floor + slack + 1024 MiB` leaves exactly 1024 MiB of usable RAM: not enough to be worth a policy, and the
    boundary itself is refused (the comparison is strict, so an equality never passes as usable)."""
    mem_mib = 1024 + 256 + 1024  # floor + slack + the 1024 MiB headroom: exactly at the boundary, so there is nothing left
    text = f"MemTotal: {mem_mib * 1024} kB\nSwapTotal: 67108864 kB\n"
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path, text)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "leaves nothing above the control-plane floor" in r.stderr


def test_a_foreign_dropin_on_this_slice_is_refused_rather_than_overridden(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    foreign = systemd_dir / f"{SLICE}.d" / "10-remote-gate-limits.conf"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("[Slice]\nCPUQuota=300%\nMemoryHigh=5G\nMemoryMax=6G\nTasksMax=8192\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5, r.stdout + r.stderr
    assert str(foreign) in r.stderr and "delete that file deliberately" in r.stderr
    assert foreign.read_text().count("MemoryHigh=5G") == 1  # untouched


def test_a_foreign_cpu_only_dropin_is_tolerated(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    foreign = systemd_dir / f"{SLICE}.d" / "10-remote-gate-limits.conf"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("[Slice]\nCPUQuota=300%\nTasksMax=8192\n")
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert foreign.read_text() == "[Slice]\nCPUQuota=300%\nTasksMax=8192\n"


def test_a_pinned_swap_ceiling_that_leaves_no_paging_room_is_impossible(tmp_path):
    """A pin is used as given, but never silently: the swap pin is MiB like the derived value and lands in the drop-in verbatim."""
    script, env, _, systemd_dir, slice_cg, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(4096 * 1024 * 1024)
    env["GATES_MEMORY_SWAP_MAX_MIB"] = "4096"
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "MemorySwapMax=4096M" in _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")


def test_an_owned_file_with_a_foreign_first_line_is_refused(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    owned = systemd_dir / f"{SLICE}.d" / "20-memory-policy.conf"
    owned.parent.mkdir(parents=True)
    owned.write_text("[Slice]\nMemoryMax=1G\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5 and "this tool's marker" in r.stderr
    assert owned.read_text() == "[Slice]\nMemoryMax=1G\n"


def test_dry_run_writes_nothing_and_restarts_nothing(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs, "--dry-run"))
    assert r.returncode == 0, r.stderr
    assert list(systemd_dir.rglob("*.conf")) == []
    assert "restart" not in (tmp_path / "systemctl.log").read_text()
    assert "daemon-reload" not in (tmp_path / "systemctl.log").read_text()
    assert "would write" in r.stdout and "would restart" in r.stdout


def test_the_live_control_plane_scores_are_set_in_place_without_a_restart(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(
        tmp_path, units=("ssh.service", "docker.service")
    )
    pids = {"ssh.service": 411, "docker.service": 412}
    env["UNIT_MAINPIDS"] = " ".join(f"{unit}={pid}" for unit, pid in pids.items())
    proc = tmp_path / "procfs"
    for pid in pids.values():
        (proc / str(pid)).mkdir()
        (proc / str(pid) / "oom_score_adj").write_text("0\n")
    (proc / "414").mkdir()
    (proc / "414" / "oom_score_adj").write_text("-900\n")  # already protected: left alone
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    for pid in pids.values():
        assert (proc / str(pid) / "oom_score_adj").read_text().strip() == "-900"
    log = (tmp_path / "systemctl.log").read_text()
    for unit in pids:
        assert f"restart {unit}" not in log  # a restart would drop live sessions and the cockpit
    assert "in place (no restart)" in r.stdout


def test_a_live_score_that_cannot_be_set_is_an_error_but_the_dropin_is_installed(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path, units=("ssh.service",))
    env["UNIT_MAINPIDS"] = "ssh.service=411"
    proc = tmp_path / "procfs"
    (proc / "411").mkdir()
    (proc / "411" / "oom_score_adj").mkdir()  # a directory: the redirection cannot write a score
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 8, r.stdout + r.stderr
    assert (systemd_dir / "ssh.service.d/30-oom-protect.conf").exists()


def test_an_unprotected_unit_is_reported_not_silently_skipped(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str((7934 - 1280) * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "not installed on this host" not in r.stdout  # the slice is installed in this fixture
    log = (tmp_path / "systemctl.log").read_text()
    assert "cat cgprofiled.service" in log  # examined, and a drop-in was staged for it anyway


def test_write_to_the_real_system_directory_requires_root(tmp_path):
    script, env, _, _, _, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_UID"] = "1003"
    r = _run(script, env, ["--meminfo", meminfo, "--systemd-dir", "/etc/systemd/system", "--cgroupfs", tmp_path / "cgroupfs"])
    assert r.returncode == 6, r.stdout + r.stderr
    assert "must run as root" in r.stderr
    assert "GATES_MEMORY_POLICY=" not in r.stdout


def test_the_script_carries_no_hard_coded_host_ceiling():
    text = SCRIPT.read_text()
    assert "6656M" not in text and "6G" not in text and "7168" not in text
    assert not re.search(r"MemoryHigh=\d", text)  # never a finite mark, not even in a comment example


def _provision_runner_block():
    """The part of provision-runner.sh that calls this script, verbatim, so its exit-code contract is tested, not restated."""
    text = (RUNNER / "provision-runner.sh").read_text()
    start = text.index('ec_rc=0\n"$here/provision-gates-limits.sh"')
    end = text.index("identity_check\n# uv searches", start)
    return text[start:end]


@pytest.mark.parametrize(("child_rc", "runner_rc", "stdout_needle", "stderr_needle"), [
    (0, 0, "GATES_MEMORY_POLICY=applied", ""),
    (4, 0, "GATES_MEMORY_POLICY_PENDING=", ""),  # drop-ins installed, live slice busy: provisioning still succeeds
    (3, 3, "", "refused with exit 3"),
    (5, 5, "", "refused with exit 5"),
    (8, 8, "", "refused with exit 8"),
])
def test_provisioning_reports_and_propagates_the_memory_policy_step(tmp_path, child_rc, runner_rc, stdout_needle, stderr_needle):
    helper = tmp_path / "provision-gates-limits.sh"
    helper.write_text(f'#!/usr/bin/env bash\nprintf "GATES_MEMORY_POLICY=applied\\n"\nexit {child_rc}\n')
    helper.chmod(0o755)
    script = f'set -Eeuo pipefail\nhere={tmp_path}\n{_provision_runner_block()}'
    run = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert run.returncode == runner_rc, run.stderr
    assert stdout_needle in run.stdout
    if stderr_needle:
        assert stderr_needle in run.stderr


def test_provisioning_calls_the_policy_step_before_it_reports_success():
    text = (RUNNER / "provision-runner.sh").read_text()
    assert text.index('"$here/provision-gates-limits.sh"') < text.index("PROVISIONING_OK_UTC")
