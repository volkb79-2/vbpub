"""Hermetic tests for runner/provision-gates-limits.sh: fake systemd (argv log), a temp systemd root and cgroupfs, and
meminfo fixtures. No host, no real systemd, nothing outside tmp_path (except the two explicit real-path refusals).

The fixtures are the operator's own measured hosts: v1001 reports MemTotal 7935 MiB and 4 x 16 GiB swap, and the policy
it was given by hand is MemoryMax=6656M / MemorySwapMax=57344M. A test that hard-codes 6656M would not exercise the
derivation, so every expectation below is computed from the fixture through the documented rule.
"""
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

# v1001 as measured (reports/sonnet-v1001-slicelimits-REPORT.md): 7935 MiB RAM, 4 x 16 GiB swap at prio 10.
HOST_V1001 = "MemTotal:        8125440 kB\nMemFree:          300000 kB\nMemAvailable:    7300000 kB\nSwapTotal:       67108864 kB\n"
# The same 8 GiB host as a different kernel revision reports it (7934 MiB): one MiB must not move the ceiling.
HOST_8GIB_DRIFT = "MemTotal:        8124416 kB\nMemFree:          600000 kB\nMemAvailable:    7300000 kB\nSwapTotal:       67108864 kB\n"
# 4 GiB RAM / 32 GiB swap, and 16 GiB RAM / 32 GiB swap: three sizes no single literal can satisfy.
HOST_4GIB = "MemTotal:        4050000 kB\nMemFree:          300000 kB\nMemAvailable:    3600000 kB\nSwapTotal:       33554432 kB\n"
HOST_16GIB = "MemTotal:       16773000 kB\nMemFree:         1000000 kB\nMemAvailable:  15000000 kB\nSwapTotal:       33554432 kB\n"

BASE_ENV = {
    "SYSTEMCTL_LOG": "systemctl.log",
    "FAKE_APPLY_LIMITS": "1",
}

STEP_MIB = 1024
FLOOR_MIB = 1024
SLACK_MIB = 512
SWAP_RESERVE_MIB = 8192


def derived(meminfo_text: str, *, step: int = STEP_MIB, floor: int = FLOOR_MIB, slack: int = SLACK_MIB,
            swap_reserve: int = SWAP_RESERVE_MIB) -> tuple[int, int, int, int]:
    """The documented rule, applied to a meminfo fixture: (raw MemTotal MiB, raw SwapTotal MiB, MemoryMax, MemorySwapMax).

    NOMINAL sizes: each total is rounded UP to the install plan's step (a host whose kernel reserves a little reports
    just under the size that was bought), then the control-plane reserve (floor + slack) or the host's swap share
    (swap reserve) is subtracted."""
    values = dict(re.findall(r"^(MemTotal|SwapTotal):\s+(\d+) kB$", meminfo_text, re.M))
    raw_mem = int(values["MemTotal"]) // 1024
    raw_swap = int(values["SwapTotal"]) // 1024
    nominal_mem = -(-raw_mem // step) * step
    nominal_swap = -(-raw_swap // step) * step
    return raw_mem, raw_swap, nominal_mem - (floor + slack), nominal_swap - swap_reserve


def _fixture(tmp_path: Path, meminfo: str = HOST_V1001, *, units: tuple = ()):
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
    log.write_text("")  # an empty argv log, so "systemctl was never called" reads as an empty file, not a missing one
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir()
    cgroupfs = tmp_path / "cgroupfs"
    slice_cg = cgroupfs / "dev.slice" / SLICE
    slice_cg.mkdir(parents=True)
    for name, value in (("memory.high", "max"), ("memory.max", "6291456000"), ("memory.swap.max", "max")):
        (slice_cg / name).write_text(value + "\n")
    (slice_cg / "cgroup.procs").write_text("")  # a started slice with no tasks: idle, not undeterminable
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
        "      ActiveState) printf '%s\\n' \"${SLICE_ACTIVE_STATE:-active}\" ;;\n"
        "      FragmentPath) printf '%s\\n' \"${SLICE_FRAGMENT:-/etc/systemd/system/dev-gates.slice}\" ;;\n"
        "      ControlGroup) printf '%s\\n' \"${SLICE_CONTROL_GROUP-/dev.slice/dev-gates.slice}\" ;;\n"
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


def _fake_limits(env, meminfo_text, **kwargs):
    """The limits a restart must apply for the fixture to converge, from the same rule the script uses."""
    _, _, maximum, swap = derived(meminfo_text, **kwargs)
    env["FAKE_MAX_BYTES"] = str(maximum * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(swap * 1024 * 1024)
    return maximum, swap


def test_the_measured_v1001_host_gets_the_policy_the_operator_chose(tmp_path):
    """The whole point of the derivation: v1001's own numbers, through the rule, reproduce 6656M and 57344M."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    maximum, swap = _fake_limits(env, HOST_V1001)
    assert (maximum, swap) == (6656, 57344)  # computed, not typed: 7935 -> nominal 8192, minus 1536; 64 GiB minus 8
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    slice_dropin = _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")
    assert "MemoryHigh=infinity" in slice_dropin
    assert "MemoryMax=6656M" in slice_dropin
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
    assert "MemoryMax=6656M" in r.stdout and "MemorySwapMax=57344M" in r.stdout


@pytest.mark.parametrize("meminfo, raw_mem", [(HOST_V1001, 7935), (HOST_8GIB_DRIFT, 7934)])
def test_one_step_absorbs_a_kernel_revision_instead_of_moving_the_ceiling(tmp_path, meminfo, raw_mem):
    """Raw-kB arithmetic rendered 6655M for the 7935 MiB host (the review's finding); the nominal step removes that."""
    script, env, _, systemd_dir, _, path, procfs = _fixture(tmp_path, meminfo)
    maximum, swap = _fake_limits(env, meminfo)
    assert derived(meminfo)[0] == raw_mem
    assert (maximum, swap) == (6656, 57344)
    r = _run(script, env, _args(path, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert f"MemoryMax={maximum}M" in _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")
    assert f"MemoryMax={maximum}M" in r.stdout


def test_the_step_and_the_reserve_are_what_decide_the_ceiling(tmp_path):
    """Hand-mutants of the rule: a different step or a different reserve must move the value, so no test can pass on a
    literal. The step is one GiB (the size grid a host is bought in); a step of 1 MiB is raw-kB arithmetic, the number
    the review found off by one, and must render a DIFFERENT ceiling."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    args = _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs)
    for step, expected in ((1024, 6656), (2048, 6656), (256, 6400), (1, 6399)):
        env["GATES_NOMINAL_STEP_MIB"] = str(step)
        _fake_limits(env, HOST_V1001, step=step)
        r = _run(script, env, args)
        assert r.returncode == 0, (step, r.stderr)
        assert f"MemoryMax={derived(HOST_V1001, step=step)[2]}M" in _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")
        assert derived(HOST_V1001, step=step)[2] == expected, step
    # a larger reserve moves the ceiling by exactly the added MiB, so the reserve is exercised and not restated
    env["GATES_NOMINAL_STEP_MIB"] = str(STEP_MIB)
    env["GATES_MEMORY_SLACK_MIB"] = "1024"
    _fake_limits(env, HOST_V1001, slack=1024)
    assert derived(HOST_V1001, slack=1024)[2] == 6656 - 512
    r = _run(script, env, args)
    assert r.returncode == 0, r.stderr
    assert f"MemoryMax={derived(HOST_V1001, slack=1024)[2]}M" in _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")


def test_three_host_sizes_produce_three_ceilings_and_never_a_literal(tmp_path):
    cases = ("h4", HOST_4GIB, "h8", HOST_V1001, "h16", HOST_16GIB)
    ceilings = {}
    for key, text in zip(cases[::2], cases[1::2]):
        script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path / key, text)
        maximum, swap = _fake_limits(env, text)
        r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / key / "cgroupfs", procfs))
        assert r.returncode == 0, (key, r.stderr)
        ceilings[key] = _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")
    assert {key: derived(text)[2] for key, text in (("h4", HOST_4GIB), ("h8", HOST_V1001), ("h16", HOST_16GIB))} == {
        "h4": 2560, "h8": 6656, "h16": 14848}
    assert len(set(ceilings.values())) == 3
    assert "MemoryMax=2560M" in ceilings["h4"] and "MemoryMax=14848M" in ceilings["h16"]


def test_the_override_pins_the_measured_value_of_one_host(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_MAX_BYTES"] = str(6656 * 1024 * 1024)
    env["FAKE_SWAP_BYTES"] = str(57344 * 1024 * 1024)
    env["GATES_MEMORY_MAX_MIB"] = "6656"
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "MemoryMax=6656M" in _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")


@pytest.mark.parametrize("value", ["0", "-1", "7935", "8034", "9000", "eight"])
def test_an_override_that_does_not_protect_a_floor_is_refused(tmp_path, value):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["GATES_MEMORY_MAX_MIB"] = value
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode != 0 and "MemoryMax" in r.stderr, value
    assert not (systemd_dir / f"{SLICE}.d").exists()


@pytest.mark.parametrize("value", ["0", "-1", "65536", "70000", "sixty"])
def test_a_swap_pin_that_leaves_the_host_no_swap_is_refused(tmp_path, value):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["GATES_MEMORY_SWAP_MAX_MIB"] = value
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode != 0 and "MemorySwapMax" in r.stderr, value


def test_a_second_run_with_the_same_facts_changes_nothing(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    _fake_limits(env, HOST_V1001)
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
    _fake_limits(env, HOST_V1001)
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
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 4, r.stdout + r.stderr
    assert "restart dev-gates.slice" not in (tmp_path / "systemctl.log").read_text()
    assert "oom_kill" in r.stderr and "Re-run when the tier is idle" in r.stderr


def test_an_unreadable_process_list_is_not_an_idle_slice(tmp_path):
    """Fail closed: "cannot tell whether it is idle" must never be treated as "it is idle", because the only action that
    needs the answer is the restart that resets the counters."""
    script, env, _, systemd_dir, slice_cg, meminfo, procfs = _fixture(tmp_path)
    (slice_cg / "cgroup.procs").unlink()
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "cannot determine whether dev-gates.slice is idle" in r.stderr
    assert "restart dev-gates.slice" not in (tmp_path / "systemctl.log").read_text()


def test_nested_tasks_count_as_tasks(tmp_path):
    script, env, _, systemd_dir, slice_cg, meminfo, procfs = _fixture(tmp_path)
    scope = slice_cg / "docker-abc123.scope"
    scope.mkdir()
    (scope / "cgroup.procs").write_text("777\n")
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 4, r.stdout + r.stderr
    assert "restart dev-gates.slice" not in (tmp_path / "systemctl.log").read_text()


def test_a_live_slice_that_still_disagrees_after_the_restart_is_an_error(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    _fake_limits(env, HOST_V1001)
    env["FAKE_APPLY_LIMITS"] = "0"
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 7, r.stdout + r.stderr
    assert "still disagrees" in r.stderr and "memory.max=6291456000" in r.stderr


def test_an_absent_slice_leaves_the_live_host_alone(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["SLICE_LOAD_STATE"] = "not-found"
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "not installed on this host" in r.stdout
    log = (tmp_path / "systemctl.log").read_text()
    assert "restart" not in log
    assert (systemd_dir / "docker-.scope.d/20-oom-group.conf").exists()  # the drop-ins are still staged


@pytest.mark.parametrize(("control_group", "active_state", "rc", "needle"), [
    ("", "inactive", 0, "loaded but inactive"),
    ("", "active", 3, "cannot resolve the cgroup"),
    ("", "activating", 3, "cannot resolve the cgroup"),
])
def test_a_loaded_slice_without_a_live_cgroup_is_classified_by_its_state(tmp_path, control_group, active_state, rc, needle):
    """A freshly installed host has the slice loaded but NOT active: no live limit to disagree with, no counter to reset,
    so provisioning must not abort there (the review's blocker 3)."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    env["SLICE_CONTROL_GROUP"] = control_group
    env["SLICE_ACTIVE_STATE"] = active_state
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == rc, r.stdout + r.stderr
    assert needle in r.stdout + r.stderr
    assert "restart" not in (tmp_path / "systemctl.log").read_text()
    assert (systemd_dir / "docker-.scope.d/20-oom-group.conf").exists()  # the drop-ins are staged either way


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
    if why != "swap reserve":
        assert "BY DESIGN" not in r.stderr  # a ceiling/floor refusal is not the swapless-host refusal


def test_a_swapless_host_refuses_by_design_and_says_so(tmp_path):
    """Not a fallback: without swap this policy would kill the oversized lane it exists to page, so it refuses."""
    meminfo = "MemTotal: 8125440 kB\nSwapTotal: 0 kB\n"
    script, env, _, systemd_dir, _, path, procfs = _fixture(tmp_path, meminfo)
    r = _run(script, env, _args(path, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 3
    assert "refuses BY DESIGN" in r.stderr
    assert not (systemd_dir / f"{SLICE}.d").exists()


def test_a_host_whose_ram_only_just_covers_the_floor_plus_headroom_is_refused(tmp_path):
    """`nominal MemTotal == floor + slack + 1024 MiB` leaves exactly the floor plus the slack and nothing else: not enough
    to be worth a policy, and the boundary itself is refused (the comparison is strict, so an equality never passes)."""
    mem_mib = FLOOR_MIB + SLACK_MIB + 1024  # 2560
    text = f"MemTotal: {mem_mib * 1024} kB\nSwapTotal: 67108864 kB\n"
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path, text)
    env["GATES_NOMINAL_STEP_MIB"] = str(mem_mib)  # the nominal size IS the boundary here, for either rounding rule
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "leaves nothing above the control-plane floor" in r.stderr


def test_a_foreign_dropin_on_this_slice_is_refused_before_anything_is_written(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    foreign = systemd_dir / f"{SLICE}.d" / "10-remote-gate-limits.conf"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("[Slice]\nCPUQuota=300%\nMemoryHigh=5G\nMemoryMax=6G\nTasksMax=8192\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5, r.stdout + r.stderr
    assert str(foreign) in r.stderr and "delete that file deliberately" in r.stderr
    assert foreign.read_text().count("MemoryHigh=5G") == 1  # untouched
    # refused BEFORE the render, so no half-written policy of ours is left for an operator to notice and undo
    assert [p.name for p in systemd_dir.rglob("*.conf")] == [foreign.name]


def test_a_foreign_cpu_only_dropin_is_tolerated(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    foreign = systemd_dir / f"{SLICE}.d" / "10-remote-gate-limits.conf"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("[Slice]\nCPUQuota=300%\nTasksMax=8192\n")
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert foreign.read_text() == "[Slice]\nCPUQuota=300%\nTasksMax=8192\n"


@pytest.mark.parametrize(("relative", "content", "needle"), [
    ("docker-.scope.d/50-default-limits.conf", "[Scope]\nMemoryMax=12G\nOOMPolicy=continue\n", "OOMPolicy=continue"),
    ("ssh.service.d/50-mdt-dedicated-dev.conf", "[Service]\nOOMScoreAdjust=-500\n", "OOMScoreAdjust=-500"),
])
def test_a_competing_scope_or_service_key_is_refused_but_an_agreeing_duplicate_is_kept(tmp_path, relative, content, needle):
    """A later fragment wins, so a DIFFERENT value silently overrides this policy; an equal one is the same policy twice."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    other = systemd_dir / relative
    other.parent.mkdir(parents=True)
    other.write_text(content)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5, r.stdout + r.stderr
    assert needle in r.stderr and str(other) in r.stderr
    assert other.read_text() == content  # untouched
    assert list(systemd_dir.rglob("20-*.conf")) == []


@pytest.mark.parametrize(("relative", "content", "needle"), [
    ("docker-.scope.d/50-default-limits.conf", "[Scope]\nMemoryMax=12G\nOOMPolicy=kill\n", "also sets OOMPolicy=kill"),
    ("ssh.service.d/50-mdt-dedicated-dev.conf", "[Service]\nOOMScoreAdjust=-900\n", "also protects ssh.service"),
])
def test_an_agreeing_dropin_of_another_owner_is_kept_and_reported(tmp_path, relative, content, needle):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    other = systemd_dir / relative
    other.parent.mkdir(parents=True)
    other.write_text(content)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert needle in r.stderr  # reported, not silently dropped
    assert other.read_text() == content


def _hand_slice_dropin(systemd_dir: Path, maximum: int, swap: int) -> Path:
    """What an operator wrote by hand on v1001 (the 2026-11-16 measurement): no marker, the same policy."""
    path = systemd_dir / f"{SLICE}.d" / "20-memory-policy.conf"
    path.parent.mkdir(parents=True)
    path.write_text("# applied by hand 2026-11-16\n\n[Slice]\nMemoryHigh=infinity\n"
                    f"MemoryMax={maximum}M\nMemorySwapMax={swap}M\n")
    return path


def test_a_recognised_hand_dropin_is_refused_without_adopt_and_adopted_with_it(tmp_path):
    """Blocker 2: re-running on a hand-configured host must be one explicit adoption, kept as a retired copy, and after
    it the second run must change nothing and restart nothing."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    maximum, swap = _fake_limits(env, HOST_V1001)
    hand = _hand_slice_dropin(systemd_dir, maximum, swap)
    args = _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs)
    refused = _run(script, env, args)
    assert refused.returncode == 5, refused.stdout + refused.stderr
    assert "--adopt" in refused.stderr  # the message names the escape hatch
    assert hand.read_text().startswith("# applied by hand 2026-11-16")
    assert "restart" not in (tmp_path / "systemctl.log").read_text()
    adopted = _run(script, env, [*args, "--adopt"])
    assert adopted.returncode == 0, adopted.stdout + adopted.stderr
    assert "retired" in adopted.stderr
    assert hand.read_text().splitlines()[0].startswith("# RENDERED by scripts/netcup/runner/provision-gates-limits.sh")
    retired = list((systemd_dir / f"{SLICE}.d").glob("20-memory-policy.conf.retired-*"))
    assert len(retired) == 1 and "MemoryMax=" in retired[0].read_text()  # the hand content is kept, never deleted
    assert not retired[0].name.endswith(".conf")  # systemd does not load it
    second = _run(script, env, args)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "unchanged " in second.stdout
    calls = (tmp_path / "systemctl.log").read_text()
    assert calls.count("restart dev-gates.slice") == 1  # only the adoption run applied limits; the re-run changed nothing


@pytest.mark.parametrize("content", [
    "[Slice]\nCPUQuota=300%\nMemoryMax=6656M\n",          # a key of a policy this file does not own
    "[Slice]\nMemoryHigh=5G\nMemoryMax=6G\nTasksMax=8192\n",  # tolerated keys plus one it does not own
    "[Slice]\nMemoryLow=1G\nMemoryMax=6656M\n",            # a kernel key it never renders
    "[Unit]\nDescription=hand made\n[Slice]\nMemoryMax=6656M\n",  # a second section
    "[Slice]\nMemoryMaxnot=6656M\n",                        # a typo
    "[Slice]\n",                                            # no directive at all
],
)
def test_adopt_refuses_a_hand_file_it_does_not_recognise(tmp_path, content):
    """`--adopt` must not become "overwrite any file": an unknown directive or section means the meaning was never
    established, and a value that merely looks familiar is not evidence of anything."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    hand = systemd_dir / f"{SLICE}.d" / "20-memory-policy.conf"
    hand.parent.mkdir(parents=True)
    hand.write_text(content)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs, "--adopt"))
    assert r.returncode == 5, r.stdout + r.stderr
    assert hand.read_text() == content
    assert list((systemd_dir / f"{SLICE}.d").glob("*.retired-*")) == []


def test_adopt_accepts_the_measured_hosts_own_spelling(tmp_path):
    """The measured host writes `MemorySwapMax=56G`, the same amount as the rendered `57344M` spelled differently. --adopt
    compares the KEYS, not the values, so that spelling is adopted rather than mistaken for a foreign policy."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    maximum, _ = _fake_limits(env, HOST_V1001)
    hand = systemd_dir / f"{SLICE}.d" / "20-memory-policy.conf"
    hand.parent.mkdir(parents=True)
    hand.write_text(f"[Slice]\nMemoryHigh=infinity\nMemoryMax={maximum}M\nMemorySwapMax=56G\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs, "--adopt"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "MemorySwapMax=57344M" in hand.read_text()  # now the tool's own rendering
    retired = list((systemd_dir / f"{SLICE}.d").glob("*.retired-*"))
    assert len(retired) == 1 and "MemorySwapMax=56G" in retired[0].read_text()  # the original spelling is kept


def test_dry_run_writes_nothing_restarts_nothing_and_prints_the_hidden_steps(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs, "--dry-run"))
    assert r.returncode == 0, r.stderr
    assert list(systemd_dir.rglob("*.conf")) == []
    assert "restart" not in (tmp_path / "systemctl.log").read_text()
    assert "daemon-reload" not in (tmp_path / "systemctl.log").read_text()
    assert "would write" in r.stdout and "would restart" in r.stdout
    assert "would run systemctl daemon-reload" in r.stdout  # a dry run states the reload it would perform


def test_a_dry_run_adoption_retires_nothing(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    maximum, swap = _fake_limits(env, HOST_V1001)
    hand = _hand_slice_dropin(systemd_dir, maximum, swap)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs, "--adopt", "--dry-run"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would adopt the recognised hand drop-in" in r.stdout
    assert list((systemd_dir / f"{SLICE}.d").glob("*.retired-*")) == []
    assert hand.read_text().startswith("# applied by hand 2026-11-16")


def test_an_owned_file_whose_content_is_not_rendered_is_not_overwritten(tmp_path):
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    owned = systemd_dir / f"{SLICE}.d" / "20-memory-policy.conf"
    owned.parent.mkdir(parents=True)
    owned.write_text("[Slice]\nMemoryMax=1G\n")  # no marker, so it is not ours: refused, never silently rewritten
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5 and "not the policy this tool renders" in r.stderr
    assert owned.read_text() == "[Slice]\nMemoryMax=1G\n"


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
    _fake_limits(env, HOST_V1001)
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
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 8, r.stdout + r.stderr
    assert (systemd_dir / "ssh.service.d/30-oom-protect.conf").exists()


def test_a_unit_that_is_not_installed_yet_is_reported_and_still_gets_its_dropin(tmp_path):
    """Not silent: a unit absent from this host (cgprofiled arrives later in the bring-up) is named, and its drop-in is
    staged anyway so no reinstall loses it."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "systemctl.log").read_text()
    assert "cat cgprofiled.service" in log  # examined
    assert (systemd_dir / "cgprofiled.service.d/30-oom-protect.conf").exists()  # staged anyway
    assert "cgprofiled.service is not installed on this host" in r.stdout  # and said, not skipped silently


def test_write_to_the_real_system_directory_requires_root(tmp_path):
    script, env, _, _, _, meminfo, procfs = _fixture(tmp_path)
    env["FAKE_UID"] = "1003"
    r = _run(script, env, ["--meminfo", meminfo, "--systemd-dir", "/etc/systemd/system", "--cgroupfs", tmp_path / "cgroupfs"])
    assert r.returncode == 6, r.stdout + r.stderr
    assert "must run as root" in r.stderr
    assert "GATES_MEMORY_POLICY=" not in r.stdout


def test_the_script_carries_no_hard_coded_host_ceiling():
    """The CODE derives every number; a comment may cite a measurement. No directive or assignment may carry a ceiling, and
    no MemoryHigh may ever be finite — not even in a comment example."""
    text = SCRIPT.read_text()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for literal in ("6656M", "6G", "7168", "57344M"):
        assert literal not in code, literal
    assert "6656" not in code and "57344" not in code
    assert not re.search(r"MemoryHigh=\d", text)


def _provision_runner_block():
    """The part of provision-runner.sh that calls this script, verbatim, so its exit-code contract is tested, not restated."""
    text = (RUNNER / "provision-runner.sh").read_text()
    start = text.index("ec_rc=0\nadopt_args=()")
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


def test_provisioning_passes_adopt_only_when_the_operator_asked_for_it(tmp_path):
    """The one-time migration must be an operator's explicit act, not something provisioning decides for them."""
    helper = tmp_path / "provision-gates-limits.sh"
    helper.write_text('#!/usr/bin/env bash\nprintf "ARGS=%s\\n" "$*"\nexit 0\n')
    helper.chmod(0o755)
    block = _provision_runner_block().replace("$here/provision-gates-limits.sh", f"{helper}")
    for value, expected in (("", "ARGS=\n"), ("0", "ARGS=\n"), ("1", "ARGS=--adopt\n")):
        env = {**os.environ, "GATES_MEMORY_ADOPT": value} if value else {k: v for k, v in os.environ.items() if k != "GATES_MEMORY_ADOPT"}
        run = subprocess.run(["bash", "-c", f'set -Eeuo pipefail\n{block}'], env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert expected in run.stdout, (value, run.stdout)


def test_provisioning_calls_the_policy_step_before_it_reports_success():
    text = (RUNNER / "provision-runner.sh").read_text()
    assert text.index('"$here/provision-gates-limits.sh"') < text.index("PROVISIONING_OK_UTC")
