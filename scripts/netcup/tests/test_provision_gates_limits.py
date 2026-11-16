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


def _fixture(tmp_path: Path, meminfo: str = HOST_V1001, *, units: tuple = (), fragments=None):
    """A copy of the script with a stub lib.sh (only the identity guard, which is not under test), a fake-bin with
    `systemctl` and `id`, a temp systemd root, a temp cgroupfs and a meminfo fixture.

    `units` are the units installed on the fake host; each gets its own fragment file under `units/` (no OOMScoreAdjust
    unless `fragments` says otherwise), so `systemctl show <u> -p FragmentPath` and the own-value probe read a real file
    the way they read /usr/lib/systemd/system on a host."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    script_dir = tmp_path / "runner"
    script_dir.mkdir()
    script = script_dir / SCRIPT.name
    shutil.copy2(SCRIPT, script)
    (script_dir / "lib.sh").write_text("identity_check() { :; }\n")

    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    for unit in units:
        content = (fragments or {}).get(unit, "# a packaged unit file\n[Service]\nExecStart=/bin/true\n")
        (unit_dir / unit).write_text(content)

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
        "    if [[ $unit == \"$GATES_SLICE\" ]]; then\n"
        "      case \"$prop\" in\n"
        "        LoadState) printf '%s\\n' \"${SLICE_LOAD_STATE:-loaded}\" ;;\n"
        "        ActiveState) printf '%s\\n' \"${SLICE_ACTIVE_STATE:-active}\" ;;\n"
        "        FragmentPath) printf '%s\\n' \"${SLICE_FRAGMENT:-/etc/systemd/system/dev-gates.slice}\" ;;\n"
        "        ControlGroup) printf '%s\\n' \"${SLICE_CONTROL_GROUP-/dev.slice/dev-gates.slice}\" ;;\n"
        "      esac\n"
        "      exit 0\n"
        "    fi\n"
        "    case \"$prop\" in\n"
        "      FragmentPath)\n"
        "        [[ \" ${UNITS_INSTALLED:-} \" == *\" $unit \"* ]] || exit 1\n"
        "        printf '%s\\n' \"$UNIT_FRAGMENT_DIR/$unit\" ;;\n"
        "      OOMScoreAdjust)\n"
        "        for pair in ${UNIT_OOM:-}; do\n"
        "          [[ $pair == \"$unit=\"* ]] && printf '%s\\n' \"${pair#*=}\" && exit 0\n"
        "        done\n"
        "        # The EFFECTIVE value systemd would apply: a vendor drop-in this tool does not own (UNIT_OOM), else the\n"
        "        # unit fragment's own key, else the kernel default 0. systemd always answers this, so 0 is a fact.\n"
        "        own=$UNIT_FRAGMENT_DIR/$unit\n"
        "        if [[ -r $own ]]; then\n"
        "          v=$(grep -m1 ^OOMScoreAdjust= \"$own\")\n"
        "          v=${v#OOMScoreAdjust=}\n"
        "          [[ -n $v ]] && { printf '%s\\n' \"$v\"; exit 0; }\n"
        "        fi\n"
        "        printf '0\\n' ;;\n"
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
        "UNIT_FRAGMENT_DIR": str(unit_dir),
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


# The class of defect review round 2 found: a token that should have started a new line was fused onto the END of a
# comment (a missing newline), so systemd reads it as prose. A section header fused onto a comment swallows every key
# below it; a directive fused onto a comment never becomes a directive. Detected for any text — a rendered drop-in or
# the script's own heredocs — by one function, so the drop-in checks and the source check cannot drift apart.
_FUSED_SECTION = re.compile(r"(?<=[.:;,)\]])\[[A-Za-z][A-Za-z0-9]*\]$")
_FUSED_KEY = re.compile(r"(?<=[.:;,)\]])([A-Z][A-Za-z0-9_]*)=")


def _fused_comment_violations(name: str, text: str) -> list[str]:
    """Every comment line of `text` that swallows a section header or a directive. Two forms are reported: the fused one
    (`...rendered here.[Slice]`, `...follows.MemorySwapMax=...`) and a comment whose entire body is the token (which is a
    header nobody can see systemd read). A token preceded by a SPACE is prose, not a lost newline: `#   - OOMPolicy=kill on
    every scope`, `# memory.oom.group=1 in prose` and `# IOWeight=101 is the non-default control` are text, and are not
    reported — a directive-shaped comment only counts when its value is a SINGLE token (no spaces), which is what a lost
    newline leaves. The fused form is recognised by the punctuation a sentence ends with (`.`, `:`, `;`, `,`, `)`, `]`)
    sitting IMMEDIATELY before the token with no space — exactly the shape a lost newline leaves behind."""
    violations = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line.startswith(("#", ";")):
            continue
        body = line[1:].strip()
        fused_section = _FUSED_SECTION.search(body)
        if fused_section:
            violations.append(f"{name}:{lineno}: section header {fused_section.group(0)} is fused onto a comment "
                              f"(a missing newline: no key below it can belong to a section): {line[:100]}")
        elif re.match(r"^\[[A-Za-z][A-Za-z0-9]*\]$", body):
            violations.append(f"{name}:{lineno}: section header {body} is a comment, not a section: {line[:100]}")
        fused_key = _FUSED_KEY.search(body)
        if fused_key:
            violations.append(f"{name}:{lineno}: directive {fused_key.group(1)}= is fused onto a comment "
                              f"(a missing newline): {line[:100]}")
        elif re.match(r"^[A-Z][A-Za-z0-9_]*=[^\s]+$", body):
            violations.append(f"{name}:{lineno}: directive {body.split('=')[0]}= is a comment, not a directive: {line[:100]}")
    return violations


def parse_ini(name: str, text: str) -> dict[str, list[tuple[str, str]]]:
    """Read a file the way systemd reads it: `#`/`;` comment lines and blank lines are dropped, a `[Section]` line opens a
    section, and every later `Key=Value` belongs to the CURRENT section. Returns {section: [(key, value), ...]}, with
    the comments discarded. Deliberately an INI reader and not a substring search: `MemoryMax=6656M` appearing anywhere
    (inside a comment, or before the first section header) is not a directive systemd would apply, so a test that only
    greps for the text certifies nothing.

    A non-comment line before any section header, a section header on a commented line, a key without `=`, and a key
    fused onto a comment (a missing newline, so the token can never become a directive) are all collected as violations
    instead of being silently attributed to a section. "Fused" means the token follows a non-space character on a comment
    line (`...rendered here.[Slice]`, `...follows.MemorySwapMax=...`); a directive merely MENTIONED inside a sentence
    (`# memory.oom.group=1 for every scope`, lowercase prose key) is text, not a missing newline, and is ignored."""
    sections: dict[str, list[tuple[str, str]]] = {}
    violations: list[str] = _fused_comment_violations(name, text)
    current = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith(";"):
            continue
        if re.match(r"^\[[A-Za-z][A-Za-z0-9]*\]$", line):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if "=" not in line:
            violations.append(f"{name}:{lineno}: non-comment line without '=': {line[:60]}")
            continue
        if current is None:
            violations.append(f"{name}:{lineno}: directive {line.split('=')[0]}= appears before any [section] header")
            continue
        key, value = line.split("=", 1)
        sections[current].append((key.strip(), value.strip()))
    return {"_sections": sections, "_violations": violations}


def assert_dropin_sections(name: str, text: str, expected: str, keys: list[str]) -> dict[str, str]:
    """The rendered drop-in is a file systemd can actually apply: its first non-comment line is the section header `expected`,
    it declares no other section, every `keys` entry sits under that header (never inside a comment, never before it),
    and it sets nothing outside the allowed key set. Returns {key: value}."""
    parsed = parse_ini(name, text)
    assert parsed["_violations"] == [], parsed["_violations"]
    sections = parsed["_sections"]
    first_non_comment = next(line.strip() for line in text.splitlines() if line.strip())
    assert first_non_comment.startswith("#"), f"{name}: the marker comment must be the first line, got {first_non_comment!r}"
    headers = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith(("#", ";"))]
    assert headers and headers[0] == f"[{expected}]", f"{name}: first non-comment line is {headers[0] if headers else None!r}, not '[{expected}]'"
    assert list(sections) == [expected], f"{name}: sections {list(sections)} != ['{expected}']"
    got = dict(sections[expected])
    for key in keys:
        assert key in got, f"{name}: '{key}' is not a directive under [{expected}] (keys under it: {sorted(got)})"
    assert set(got) == set(keys), f"{name}: sets {sorted(got)}, expected exactly {sorted(keys)}"
    return got


def test_the_measured_v1001_host_gets_the_policy_the_operator_chose(tmp_path):
    """The whole point of the derivation: v1001's own numbers, through the rule, reproduce 6656M and 57344M."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    maximum, swap = _fake_limits(env, HOST_V1001)
    assert (maximum, swap) == (6656, 57344)  # computed, not typed: 7935 -> nominal 8192, minus 1536; 64 GiB minus 8
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    slice_dropin = _dropin(systemd_dir, f"{SLICE}.d/20-memory-policy.conf")
    # read as systemd reads it: the three keys must sit under [Slice], not merely appear somewhere in the file
    slice_keys = assert_dropin_sections(f"{SLICE}.d/20-memory-policy.conf", slice_dropin, "Slice",
                                        ["MemoryHigh", "MemoryMax", "MemorySwapMax"])
    assert slice_keys == {"MemoryHigh": "infinity", "MemoryMax": "6656M", "MemorySwapMax": "57344M"}
    assert "MemoryOOMGroup" not in slice_keys and "ManagedOOMMemoryPressure" not in slice_keys
    for text in (slice_dropin, _dropin(systemd_dir, "docker-.scope.d/20-oom-group.conf")):
        assert "MemoryOOMGroup" not in text
        assert "oom.group" not in text.replace("# memory.oom.group", "")  # only the comment names it: never a set key
    scope = _dropin(systemd_dir, "docker-.scope.d/20-oom-group.conf")
    assert scope.splitlines()[0].startswith("# RENDERED by scripts/netcup/runner/provision-gates-limits.sh")
    assert assert_dropin_sections("docker-.scope.d/20-oom-group.conf", scope, "Scope", ["OOMPolicy"]) == {"OOMPolicy": "kill"}
    for unit in ("ssh.service", "systemd-logind.service", "systemd-journald.service", "docker.service", "cgprofiled.service"):
        text = _dropin(systemd_dir, f"{unit}.d/30-oom-protect.conf")
        assert assert_dropin_sections(f"{unit}.d/30-oom-protect.conf", text, "Service", ["OOMScoreAdjust"]) == {
            "OOMScoreAdjust": "-900"}
    assert not (systemd_dir / "sshd.service.d").exists()
    assert r.stdout.count("GATES_MEMORY_POLICY=") == 1
    assert "MemoryMax=6656M" in r.stdout and "MemorySwapMax=57344M" in r.stdout


def test_every_rendered_dropin_parses_the_way_systemd_reads_it(tmp_path):
    """The whole rendered output of one run, parsed as INI (never grepped): a key that is not under its own section header —
    for example a comment whose newline was lost so the header became part of the comment — is a drop-in systemd would
    ignore, and the review's blocker was exactly that defect."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    rendered = sorted(systemd_dir.rglob("*.conf"))
    assert len(rendered) == 7  # one slice policy, one docker scope, five protected units
    expected = {
        f"{SLICE}.d/20-memory-policy.conf": ("Slice", ["MemoryHigh", "MemoryMax", "MemorySwapMax"]),
        "docker-.scope.d/20-oom-group.conf": ("Scope", ["OOMPolicy"]),
    }
    for unit in ("ssh.service", "systemd-logind.service", "systemd-journald.service", "docker.service", "cgprofiled.service"):
        expected[f"{unit}.d/30-oom-protect.conf"] = ("Service", ["OOMScoreAdjust"])
    for path in rendered:
        relative = str(path.relative_to(systemd_dir))
        assert relative in expected, f"an unclassified drop-in was rendered: {relative}"
        section, keys = expected[relative]
        parsed = parse_ini(relative, path.read_text())
        assert parsed["_violations"] == [], parsed["_violations"]
        assert list(parsed["_sections"]) == [section]
        assert set(dict(parsed["_sections"][section])) == set(keys)
    # nothing is rendered outside this tool's own paths: exactly these seven directories, no eighth
    assert {str(p.parent.relative_to(systemd_dir)) for p in rendered} == {
        f"{SLICE}.d", "docker-.scope.d", *[f"{u}.d" for u in ("ssh.service", "systemd-logind.service", "systemd-journald.service", "docker.service", "cgprofiled.service")]}


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
    assert "wrote " not in second.stdout and "would write" not in second.stdout  # every file already IS the rendering
    assert "removed " not in second.stderr and "retired " not in second.stderr  # nothing withdrawn on a converged host
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


def test_every_competing_file_is_named_before_the_refusal(tmp_path):
    """The measured annoyance (v1001, 2026-11-16): a plain --dry-run on a hand-configured host stopped at the first foreign
    file, and an operator fixed that one only to meet the next on the following run. The refusal now lists all of them."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    foreign = systemd_dir / f"{SLICE}.d"
    foreign.mkdir(parents=True)
    names = ("10-remote-gate-limits.conf", "15-hand-memory.conf", "16-stale-policy.conf")
    for index, name in enumerate(names):
        (foreign / name).write_text(f"[Slice]\nMemoryMax={7 + index}G\n")
    for dry_run in (("--dry-run",), ()):
        r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs, *dry_run))
        assert r.returncode == 5, r.stdout + r.stderr
        for name in names:
            assert f"{foreign / name} also sets the gates-slice memory policy" in r.stderr, (dry_run, name, r.stderr)
        assert f"{len(names)} competing file(s) named above" in r.stderr
        assert "--adopt --dry-run to see every recognised hand drop-in at once" in r.stderr
        assert sorted(p.name for p in systemd_dir.rglob("*.conf")) == sorted(names)  # nothing of ours, nothing rewritten


def test_every_competing_file_is_named_across_the_slice_the_scope_and_the_unit_paths(tmp_path):
    """The round-0 listing covered the slice's `.d/` only: a foreign `OOMPolicy` under `docker-.scope.d/` or a foreign
    `OOMScoreAdjust` under a unit's `.d/` exited at the first one found, so an operator fixed that file and met the next
    on the following run. Every competing file is now recorded and named before the single refusal, and the paths the
    tool owns are named in the same run."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path, units=("ssh.service",))
    slice_foreign = systemd_dir / f"{SLICE}.d" / "10-hand-memory.conf"
    slice_foreign.parent.mkdir(parents=True)
    slice_foreign.write_text("[Slice]\nMemoryMax=6G\n")
    scope_foreign = systemd_dir / "docker-.scope.d" / "50-hand-scope.conf"
    scope_foreign.parent.mkdir(parents=True)
    scope_foreign.write_text("[Scope]\nOOMPolicy=continue\n")
    unit_foreign = systemd_dir / "ssh.service.d" / "50-hand-score.conf"
    unit_foreign.parent.mkdir(parents=True)
    unit_foreign.write_text("[Service]\nOOMScoreAdjust=-500\n")
    own_foreign = systemd_dir / "docker.service.d" / "30-oom-protect.conf"
    own_foreign.parent.mkdir(parents=True)
    own_foreign.write_text("[Service]\nOOMScoreAdjust=-900\n")  # at this tool's OWN path for a unit it protects
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5, r.stdout + r.stderr
    for path in (slice_foreign, scope_foreign, unit_foreign, own_foreign):
        assert str(path) in r.stderr, (path, r.stderr)
    assert "competing file(s) named above" in r.stderr  # one count, emitted after every pre-flight
    assert "file(s) at this tool's own paths cannot be adopted" in r.stderr
    for path in (slice_foreign, scope_foreign, unit_foreign, own_foreign):
        assert path.read_text() == path.read_text()  # untouched (each is exactly what was written above)
    assert sorted(p.name for p in systemd_dir.rglob("20-*.conf")) == []


def test_the_fragment_override_note_is_printed_only_when_a_value_really_differs(tmp_path):
    """The measured noise (v1001, 2026-11-16): the two-line `has a fragment ... deliberately overrides` note printed on
    EVERY run, including a converged host where the fragment's memory keys are absent or spell the same amount."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path)
    maximum, swap = _fake_limits(env, HOST_V1001)
    fragment = tmp_path / "dev-gates.slice"
    env["SLICE_FRAGMENT"] = str(fragment)
    needle = "this drop-in deliberately overrides it"

    # a fragment with CPU keys only (the no-host-setup marker unit): nothing to say
    fragment.write_text("[Unit]\nDescription=tier\n[Slice]\nCPUWeight=100\nIOWeight=101\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert needle not in r.stdout + r.stderr

    # the same amounts in another spelling: bytes equal, so no note at all. This sub-case is the one a "note whenever the
    # fragment sets the key" implementation fails, and it is why same_memory_amount is a BYTE comparison, not a string one
    fragment.write_text(f"[Slice]\nMemoryHigh=infinity\nMemoryMax={maximum * 1024}K\nMemorySwapMax={swap}M\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert needle not in r.stdout + r.stderr, "a fragment that agrees in bytes was reported as disagreeing"

    # `max` and `infinity` are different tokens to this tool (it cannot claim they mean the same to systemd), so that one
    # key is reported — and the report names THAT key, never the three keys it used to list unconditionally
    fragment.write_text(f"[Slice]\nMemoryHigh=max\nMemoryMax={maximum * 1024}K\nMemorySwapMax={swap}M\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "that sets MemoryHigh=max" in r.stdout + r.stderr
    assert "that sets MemoryMax" not in r.stdout + r.stderr

    # a fragment that really disagrees is named, once, with the value the policy uses
    fragment.write_text("[Slice]\nMemoryMax=6G\n")
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    out = r.stdout + r.stderr
    assert "that sets MemoryMax=6G; this drop-in deliberately overrides it" in out
    assert f"policy value MemoryMax={maximum}M" in out
    assert "sets MemoryHigh" not in out  # the keys the fragment does not set are not mentioned

    # and a converged second run repeats nothing: the note describes the fragment, not a change this run makes
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stderr
    assert "this drop-in deliberately overrides it" in r.stdout + r.stderr  # same fragment, still disagrees


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
    # a MORE protective value is not a contradiction, it is already stronger than the policy: kept and reported as such
    ("docker.service.d/50-containerd-like.conf", "[Service]\nOOMScoreAdjust=-999\n", "more protected"),
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


def _helper_block() -> str:
    """The script's helper definitions, extracted between its own markers, so a helper test calls the SHIPPED text."""
    text = SCRIPT.read_text()
    start = text.index("# ==== helper definitions")
    end = text.index("# ==== end of helper definitions")
    return text[start:end]


def test_the_helper_region_holds_every_definition_and_runs_no_code():
    """Review round 1's blocker, structurally: bash resolves a function name at CALL time, so a helper that sits below
    its first call is `command not found` at run time - and a `|| true` around it turns that into an empty value. This
    asserts the shape that makes such a defect impossible: every definition lives in the marked helper region, and that
    region contains nothing but definitions (a stray statement there would run before the pre-flights)."""
    text = SCRIPT.read_text()
    block = _helper_block()
    assert re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", block, re.M), "no definitions: the marker layout changed"
    assert not re.search(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{", text.replace(block, ""), re.M), \
        "a function is defined outside the helper region, where it can sit below its first call"
    depth = 0
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if depth == 0:
            assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{", line), \
                f"the helper region runs code at definition level: {stripped[:60]!r}"
        depth += _brace_delta(stripped)
        assert depth >= 0, f"unbalanced braces at: {stripped[:60]!r}"
    assert depth == 0, "the helper region's braces do not balance"


def _brace_delta(line: str) -> int:
    """The brace balance of a shell line, ignoring quotes and `${...}` (both appear inside conditions and messages)."""
    body = line.split(" #")[0] if " #" in line and not line.lstrip().startswith("#") else line
    body = re.sub(r'\$\{[^}]*\}', "", body)
    body = re.sub(r'"[^"]*"', '""', body)
    body = re.sub(r"'[^']*'", "''", body)
    return body.count("{") - body.count("}")


def test_a_function_is_never_used_before_its_definition_anywhere_in_the_file():
    """The ordered check: for every function, its first USE (in a body or in the flow) must not precede its definition."""
    text = SCRIPT.read_text().splitlines()
    definitions = {}
    for index, line in enumerate(text, 1):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{", line)
        if match:
            definitions[match.group(1)] = index
    offenders = []
    for name, definition in definitions.items():
        use = re.compile(r"(?:^|[\s;|&(])" + re.escape(name) + r"(?:[\s;|)&]|$)")
        for index, line in enumerate(text, 1):
            stripped = line.strip()
            if index >= definition and stripped.startswith("#"):
                continue
            if stripped.startswith("#") or re.match(r"^" + re.escape(name) + r"\(\)\s*\{", line):
                continue
            body = re.sub(r'"[^"]*"', '""', stripped)
            if use.search(body):
                if index < definition:
                    offenders.append((name, index, definition, stripped[:80]))
                break
    assert offenders == [], offenders


@pytest.mark.parametrize(("token", "usable"), [
    ("-1000", True), ("1000", True), ("-900", True), ("0", True),
    ("-1001", False), ("1001", False),
    ("010", True), ("0900", True), ("0100", True),  # base 10, never octal: `0900` has no octal spelling at all
    ("0000000900", False), ("-0000000900", False),  # an over-long digit string can wrap into the valid range
    ("10000", False), ("", False), ("+1000", False), (" 900", False), ("-", False), ("abc", False),
])
def test_the_score_parser_is_one_place_with_the_kernels_range(tmp_path, token, usable):
    """`oom_score_adj` is the kernel's -1000..1000. One helper turns the token into a number: forced base 10 (a leading
    zero would reach bash arithmetic as OCTAL and `0900` aborts the comparison), at most four digits (a longer string
    can wrap a 64-bit integer back into the valid range), and the range checked against the real bounds."""
    harness = ("set -Eeuo pipefail\n" + _helper_block() + """
if usable_oom_score "$1"; then printf 'usable\n'; else printf 'unusable\n'; fi
""")
    run = subprocess.run(["bash", "-c", harness, "helper", token], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == ("usable" if usable else "unusable"), (token, run.stdout, run.stderr)


@pytest.mark.parametrize(("current", "target", "protected"), [
    ("-1000", "-900", True), ("-999", "-900", True), ("-900", "-900", True),  # equal is at least as protective
    ("-899", "-900", False), ("0", "-900", False), ("1000", "-900", False),
    ("-1000", "-1000", True), ("1000", "1000", True), ("-1001", "-900", False), ("garbage", "-900", False),
])
def test_more_protective_compares_numbers_and_never_mistakes_equality_for_a_change(tmp_path, current, target, protected):
    """The ranking, not the magnitude: lower wins, equal is kept, and an unusable token is never "protected"."""
    harness = ("set -Eeuo pipefail\n" + _helper_block() + """
if more_protective "$1" "$2"; then printf 'protected\n'; else printf 'weaker\n'; fi
""")
    run = subprocess.run(["bash", "-c", harness, "helper", current, target], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == ("protected" if protected else "weaker"), (current, target, run.stdout, run.stderr)


def test_a_vendor_drop_in_this_tool_cannot_see_is_not_replaced_by_a_weaker_rendering(tmp_path):
    """A packaged drop-in under /usr/lib/systemd/system/<unit>.d/ is invisible from the directory this tool writes to, and
    a drop-in this tool renders there would REPLACE it whenever its own file sorts later. The unit's own fragment says
    -500 (weaker than the policy), but the EFFECTIVE score systemd would apply is -1000: nothing is rendered, and the
    message names the directory to inspect. The same host WITHOUT the vendor drop-in gets the policy drop-in, so the
    test cannot pass by refusing every unit."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("docker.service",), fragments={"docker.service": "[Service]\nOOMScoreAdjust=-500\n"})
    env["UNIT_OOM"] = "docker.service=-1000"  # the effective value: a vendor drop-in the tool never sees
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (systemd_dir / "docker.service.d/30-oom-protect.conf").exists(), "the vendor -1000 would have been replaced"
    assert "effective OOMScoreAdjust=-1000" in r.stderr
    assert "/usr/lib/systemd/system/docker.service.d" in r.stderr

    env["UNIT_OOM"] = ""  # no vendor drop-in: the policy legitimately strengthens the unit's own -500
    again = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert again.returncode == 0, again.stdout + again.stderr
    assert (systemd_dir / "docker.service.d/30-oom-protect.conf").exists()


def test_an_unreadable_effective_score_is_an_error_never_a_rendering(tmp_path):
    """The effective read gates a write: a token outside the kernel's range is not "unprotected", it is a value nobody
    could rank, so the run refuses (exit 3) and writes nothing."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("docker.service",), fragments={"docker.service": "[Service]\nOOMScoreAdjust=-500\n"})
    env["UNIT_OOM"] = "docker.service=garbage"
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "effective OOMScoreAdjust of docker.service is not a usable score" in r.stderr
    assert not (systemd_dir / "docker.service.d/30-oom-protect.conf").exists()


PROTECTED = ("ssh.service", "systemd-logind.service", "systemd-journald.service", "docker.service", "cgprofiled.service")


def _protected_host(tmp_path, *, fragments=None, units=PROTECTED):
    """A host where every protected unit is installed with its own unit file (OOMScoreAdjust unset unless `fragments`)."""
    script, env, _, systemd_dir, _, meminfo, procfs = _fixture(tmp_path, units=units, fragments=fragments)
    env["UNIT_MAINPIDS"] = ""
    return script, env, systemd_dir, meminfo, procfs


def test_a_live_score_that_is_already_more_protective_is_never_raised(tmp_path):
    """The measured defect (v1001, 2026-11-16): sshd keeps its OWN listener at -1000, and a re-run lowered it to the unit's
    -900, so the host agent had to restore -1000 by hand and a later run would lower it again. oom_score_adj is a ranking,
    so -1000 is MORE protective than -900: an equal-or-lower current value is left alone, whatever unit it belongs to."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(tmp_path, units=("ssh.service",))
    env["UNIT_MAINPIDS"] = "ssh.service=411"
    proc = tmp_path / "procfs"
    (proc / "411").mkdir()
    (proc / "411" / "oom_score_adj").write_text("-1000\n")
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (proc / "411" / "oom_score_adj").read_text() == "-1000\n", "sshd's own listener protection was lowered"
    assert "already at least as protected" in r.stdout and "-1000" in r.stdout  # reported, not silently skipped
    assert "-> -900" not in r.stdout


@pytest.mark.parametrize(("current", "write"), [
    ("0", "-900"),        # unprotected -> protected
    ("-500", "-900"),     # -500 is LESS protective than -900, so it IS lowered to the policy value
    ("-899", "-900"),     # one step less protective still converges
    ("-900", None),       # exactly the target: no write
    ("-999", None),       # containerd-like: stronger, left alone
    ("-1000", None),      # sshd's own listener: strongest, left alone
])
def test_the_live_score_is_written_only_when_the_current_value_is_less_protective(tmp_path, current, write):
    """One comparison decides every case: write iff current > target. A flipped comparison (-1000 -> -900 and 0 untoched)
    is exactly the defect this test was written for."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(tmp_path, units=("docker.service",))
    env["UNIT_MAINPIDS"] = "docker.service=412"
    proc = tmp_path / "procfs"
    (proc / "412").mkdir()
    (proc / "412" / "oom_score_adj").write_text(current + "\n")
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    if write is None:
        assert (proc / "412" / "oom_score_adj").read_text() == current + "\n"
    else:
        assert (proc / "412" / "oom_score_adj").read_text() == write + "\n"
        assert f"{current} -> {write}" in r.stdout
    assert "restart docker.service" not in (tmp_path / "systemctl.log").read_text()


@pytest.mark.parametrize("content", ["\n", "garbage\n", "+900\n", "-2000\n", "1001\n"])
def test_an_unreadable_live_score_is_an_error_never_a_write(tmp_path, content):
    """An unreadable value is not "unprotected 0": the read gates a WRITE, and writing on an assumed 0 is how a value
    nobody read gets replaced. Whatever the file holds, the tool must refuse and leave it exactly as it was."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(tmp_path, units=("docker.service",))
    env["UNIT_MAINPIDS"] = "docker.service=412"
    proc = tmp_path / "procfs"
    (proc / "412").mkdir()
    (proc / "412" / "oom_score_adj").write_text(content)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 8, r.stdout + r.stderr
    assert "refusing to overwrite a score" in r.stderr
    assert (proc / "412" / "oom_score_adj").read_text() == content
    assert (systemd_dir / "docker.service.d/30-oom-protect.conf").exists()  # the drop-in is still installed


def test_a_unit_whose_own_file_is_already_more_protective_gets_no_drop_in(tmp_path):
    """The persistent half of the same rule: a drop-in WINS over the unit's own file, so rendering -900 on ssh.service
    (whose packaged file sets -1000) would REPLACE a stronger value with a weaker one at the next start. Nothing is
    rendered, and the reason is printed."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("ssh.service", "docker.service"),
        fragments={"ssh.service": "[Service]\nOOMScoreAdjust=-1000\n",
                   "docker.service": "[Service]\nOOMScoreAdjust=-999\n"})
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (systemd_dir / "ssh.service.d/30-oom-protect.conf").exists(), "a drop-in would have lowered sshd to -900"
    assert not (systemd_dir / "docker.service.d/30-oom-protect.conf").exists()
    assert "ssh.service's own unit file sets OOMScoreAdjust=-1000, at least as protective" in r.stderr
    assert "no drop-in is rendered" in r.stderr


def test_a_drop_in_this_tool_rendered_earlier_is_removed_when_the_unit_protects_itself(tmp_path):
    """The state a host reaches after the old script ran once: ssh.service.d/30-oom-protect.conf exists (with our marker)
    and the unit's own file says -1000. Leaving the file would keep -900 winning at the next start, so the tool removes
    its own file (and says so, and says it in a --dry-run without writing) while leaving another owner's file alone."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("ssh.service",), fragments={"ssh.service": "[Service]\nOOMScoreAdjust=-1000\n"})
    ours = systemd_dir / "ssh.service.d/30-oom-protect.conf"
    ours.parent.mkdir(parents=True)
    ours.write_text("# RENDERED by scripts/netcup/runner/provision-gates-limits.sh - do not edit\n[Service]\nOOMScoreAdjust=-900\n")
    _fake_limits(env, HOST_V1001)
    args = _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs)
    dry = _run(script, env, [*args, "--dry-run"])
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "would remove" in dry.stdout + dry.stderr and ours.exists()
    r = _run(script, env, args)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not ours.exists(), "the tool's own weaker drop-in still wins over the unit's -1000"
    assert "removed" in r.stderr and "protects itself at -1000" in r.stderr
    assert "daemon-reload" in (tmp_path / "systemctl.log").read_text()  # removing a drop-in needs a reload


def test_another_owners_drop_in_that_would_lower_a_self_protected_unit_is_refused(tmp_path):
    """Not ours to delete: a foreign drop-in at our own path that would replace the unit's stronger own value is a
    competing policy, so the run refuses and writes nothing."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("ssh.service",), fragments={"ssh.service": "[Service]\nOOMScoreAdjust=-1000\n"})
    foreign = systemd_dir / "ssh.service.d/30-oom-protect.conf"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("[Service]\nOOMScoreAdjust=-900\n")  # no marker: someone else wrote it
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5, r.stdout + r.stderr
    assert str(foreign) in r.stderr and "less protective" in r.stderr
    assert foreign.read_text() == "[Service]\nOOMScoreAdjust=-900\n"
    assert sorted(p.name for p in systemd_dir.rglob("*.conf")) == [foreign.name]  # nothing of ours was written


def _own_path(tmp_path, unit: str) -> Path:
    """The drop-in path this tool owns for a unit (the fixture's systemd root, not the real one)."""
    return tmp_path / "systemd" / f"{unit}.d" / "30-oom-protect.conf"


@pytest.mark.parametrize(("foreign_value", "kept"), [
    ("-1000", True),   # equal to sshd's own listener: the same protection twice, kept
    ("-999", False),   # weaker than the unit's own -1000: it would replace it at the next start
    ("-900", False),   # equal to the POLICY value but weaker than the unit's own -1000: refused for the same reason
])
def test_a_foreign_drop_in_at_this_tools_own_path_is_read_and_judged_by_its_value(tmp_path, foreign_value, kept):
    """Review round 1's blocker: `unit_section_value` was called before its definition, so bash answered `command not
    found`, `|| true` swallowed it, and the file at this tool's own path was read as "sets nothing". Every equal-or-more
    protective file was then REFUSED with exit 5 - the tool reported that a file it cannot read was a contradiction.
    The value is now read, and only a file that would LOWER the unit's own protection is refused."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("ssh.service",), fragments={"ssh.service": "[Service]\nOOMScoreAdjust=-1000\n"})
    foreign = _own_path(tmp_path, "ssh.service")
    foreign.parent.mkdir(parents=True)
    content = f"[Service]\nOOMScoreAdjust={foreign_value}\n"
    foreign.write_text(content)  # no marker: another owner wrote it
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert foreign.read_text() == content  # never rewritten, whatever the decision
    if kept:
        assert r.returncode == 0, r.stdout + r.stderr
        assert "kept, never lowered" in r.stderr
        assert f"OOMScoreAdjust={foreign_value}" in r.stderr
        assert "unreadable" not in r.stderr, "the value was not read, so the tool refused a file it should keep"
        assert [p.name for p in foreign.parent.iterdir()] == [foreign.name]  # nothing weaker was rendered beside it
    else:
        assert r.returncode == 5, r.stdout + r.stderr
        assert f"sets OOMScoreAdjust={foreign_value}, which is less protective than ssh.service's own -1000" in r.stderr
        assert [p.name for p in foreign.parent.iterdir()] == [foreign.name]  # nothing weaker was rendered beside it


def test_another_owners_equal_drop_in_over_a_weaker_unit_file_is_kept_not_refused(tmp_path):
    """The other half of the same read: the unit's own file says -500 (weaker than the policy), so a rendered -900
    STRENGTHENS it - but another owner already put -900 there. That is the policy value itself, so it is kept and
    reported instead of being refused as "weaker than the policy"."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("docker.service",), fragments={"docker.service": "[Service]\nOOMScoreAdjust=-500\n"})
    foreign = _own_path(tmp_path, "docker.service")
    foreign.parent.mkdir(parents=True)
    content = "[Service]\nOOMScoreAdjust=-900\n"
    foreign.write_text(content)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert foreign.read_text() == content
    assert "at least as protective as the policy value" in r.stderr


@pytest.mark.parametrize("foreign_value", ["-500", "0", "+1000".replace("+", "")])
def test_another_owners_weaker_drop_in_is_refused_and_names_the_value(tmp_path, foreign_value):
    """A file that would REPLACE a stronger unit value with a weaker one is a competing policy: refused, by name, with
    the value it actually sets (never "unreadable")."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("ssh.service",), fragments={"ssh.service": "[Service]\nOOMScoreAdjust=-1000\n"})
    foreign = _own_path(tmp_path, "ssh.service")
    foreign.parent.mkdir(parents=True)
    content = f"[Service]\nOOMScoreAdjust={foreign_value}\n"
    foreign.write_text(content)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 5, r.stdout + r.stderr
    assert f"sets OOMScoreAdjust={foreign_value}" in r.stderr
    assert "less protective" in r.stderr
    assert foreign.read_text() == content


def test_adopting_a_hand_drop_in_over_a_stronger_unit_file_retires_it_and_renders_nothing(tmp_path):
    """The v1001 migration shape: a hand `30-oom-protect.conf` at -900 over a unit whose own file sets -1000. The
    never-reduce rule outranks the policy value, so --adopt retires the hand file (kept, never deleted) and NO drop-in
    replaces it; without --adopt the same host is refused, naming the flag."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("ssh.service",), fragments={"ssh.service": "[Service]\nOOMScoreAdjust=-1000\n"})
    ours = _own_path(tmp_path, "ssh.service")
    ours.parent.mkdir(parents=True)
    hand = "[Service]\nOOMScoreAdjust=-900\n"
    ours.write_text(hand)
    _fake_limits(env, HOST_V1001)
    args = _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs)
    refused = _run(script, env, args)
    assert refused.returncode == 5, refused.stdout + refused.stderr
    assert ours.read_text() == hand
    assert list(ours.parent.glob("*.retired-*")) == []
    adopted = _run(script, env, [*args, "--adopt"])
    assert adopted.returncode == 0, adopted.stdout + adopted.stderr
    assert not ours.exists(), "a rendered drop-in would have replaced sshd's -1000 with -900"
    retired = list(ours.parent.glob("30-oom-protect.conf.retired-*"))
    assert len(retired) == 1 and retired[0].read_text() == hand  # kept, never deleted
    assert "NOT replacing it" in adopted.stderr
    second = _run(script, env, [*args, "--adopt"])
    assert second.returncode == 0, second.stdout + second.stderr
    assert not ours.exists() and len(list(ours.parent.glob("*.retired-*"))) == 1


def test_a_drop_in_this_tool_rendered_is_retired_never_deleted_and_only_after_the_pre_flight(tmp_path):
    """The tool's own earlier drop-in is withdrawn when the unit protects itself, but the withdrawal is a RETIREMENT
    (the content is copied to a name systemd does not load) and it happens AFTER every pre-flight: a run that refuses
    on another file must leave that drop-in in place. The second pass is the state the real hosts reach - a rendered
    -900 drop-in, then a unit fragment that grew to -1000."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("ssh.service",), fragments={"ssh.service": "[Service]\nOOMScoreAdjust=-500\n"})
    _fake_limits(env, HOST_V1001)
    args = _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs)
    first = _run(script, env, args)
    assert first.returncode == 0, first.stdout + first.stderr
    ours = _own_path(tmp_path, "ssh.service")
    assert "OOMScoreAdjust=-900" in ours.read_text()
    rendered = ours.read_text()

    # the unit now protects itself, AND a competing slice file appears: the run refuses and must not touch its own file
    env["UNIT_FRAGMENT_DIR"] = str(tmp_path / "units2")
    units2 = tmp_path / "units2"
    units2.mkdir()
    (units2 / "ssh.service").write_text("[Service]\nOOMScoreAdjust=-1000\n")
    (systemd_dir / f"{SLICE}.d").mkdir(parents=True, exist_ok=True)
    (systemd_dir / f"{SLICE}.d" / "10-hand.conf").write_text("[Slice]\nMemoryMax=6G\n")
    refused = _run(script, env, args)
    assert refused.returncode == 5, refused.stdout + refused.stderr
    assert ours.exists() and ours.read_text() == rendered, "the pre-flight refused but its own file was already mutated"
    assert list(ours.parent.glob("*.retired-*")) == []

    (systemd_dir / f"{SLICE}.d" / "10-hand.conf").unlink()
    second = _run(script, env, args)
    assert second.returncode == 0, second.stdout + second.stderr
    assert not ours.exists()
    retired = list(ours.parent.glob("30-oom-protect.conf.retired-*"))
    assert len(retired) == 1 and retired[0].read_text() == rendered
    assert "protects itself at -1000" in second.stderr


@pytest.mark.parametrize(("own", "rendered"), [
    ("\n", True),        # the unit sets nothing: the policy is an addition, not a downgrade
    ("[Service]\nOOMScoreAdjust=0\n", True),     # 0 is explicitly unprotected
    ("[Service]\nOOMScoreAdjust=-500\n", True),  # less protective than -900: the policy strengthens it
])
def test_a_unit_with_no_or_weaker_protection_gets_the_policy_drop_in(tmp_path, own, rendered):
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("docker.service",), fragments={"docker.service": own})
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    path = systemd_dir / "docker.service.d/30-oom-protect.conf"
    assert path.exists() is rendered
    if rendered:
        assert "OOMScoreAdjust=-900" in path.read_text()


def test_an_unreadable_own_unit_file_is_an_error_not_a_default(tmp_path):
    """A unit that is installed but whose fragment cannot be read is not "unprotected": it is a fact this tool could not
    obtain for the one decision that can lower a unit's protection."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("docker.service",))
    (tmp_path / "units" / "docker.service").unlink()  # installed, but the fragment is gone (unreadable)
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 3, r.stdout + r.stderr
    assert "cannot read the OOMScoreAdjust of docker.service" in r.stderr
    assert not (systemd_dir / "docker.service.d/30-oom-protect.conf").exists()


def test_the_units_own_fragment_is_read_not_the_effective_property(tmp_path):
    """The settled value is the fragment's own, not `systemctl show -p OOMScoreAdjust` (which already includes a drop-in
    this tool rendered on an earlier run). Otherwise a second run would mistake its own output for the unit's protection,
    and a hand-set -900 in the unit file would block a policy that has every right to be rendered."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(
        tmp_path, units=("docker.service",), fragments={"docker.service": "[Service]\nOOMScoreAdjust=-500\n"})
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    log = (tmp_path / "systemctl.log").read_text()
    assert "show docker.service -p FragmentPath" in log  # the unit's OWN file was asked for
    assert (systemd_dir / "docker.service.d/30-oom-protect.conf").read_text().endswith("OOMScoreAdjust=-900\n")


def test_an_equal_live_score_is_left_alone_even_when_a_write_would_have_failed(tmp_path):
    """The `<=` boundary of the never-reduce rule, proved by CONSEQUENCE and not by a message alone: the score file is
    read-only (`0444`), so the write path fails where the comparison says there is nothing to write. A current value
    equal to the target is already at least as protected, so the run succeeds and prints no write line at all
    (`set`, `would set`, or a `-900 -> -900` arrow) - and an ATTEMPTED write also fails a non-root run with exit 8.
    A `<` comparison takes the write path and prints the arrow whatever the file mode allows, so the mutant dies for
    every uid."""
    script, env, systemd_dir, meminfo, procfs = _protected_host(tmp_path, units=("docker.service",))
    env["UNIT_MAINPIDS"] = "docker.service=412"
    proc = tmp_path / "procfs"
    (proc / "412").mkdir()
    score = proc / "412" / "oom_score_adj"
    score.write_text("-900\n")
    score.chmod(0o444)  # readable, not writable: the write the mutant would perform cannot succeed
    _fake_limits(env, HOST_V1001)
    r = _run(script, env, _args(meminfo, systemd_dir, tmp_path / "cgroupfs", procfs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-> -900" not in r.stdout  # no "-900 -> -900" arrow: equal is not a change
    assert "would set" not in r.stdout and "set docker.service" not in r.stdout
    assert "cannot set the live OOM score" not in r.stderr  # the write path was never entered
    if os.geteuid() != 0:  # as root the mode does not stop the write; the message assertions above still bite
        assert score.read_text() == "-900\n"


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


def test_parse_ini_documentation_of_the_comment_rule():
    """Text, not a missing newline: a directive merely MENTIONED in prose is fine, so the rule does not fire on every
    comment that happens to name a key."""
    prose = "# RENDERED by a tool.\n# memory.oom.group=1 for every transient scope (lowercase: prose, not a directive)\n[Slice]\nMemoryMax=6656M\n"
    parsed = parse_ini("prose", prose)
    assert parsed["_violations"] == [], parsed["_violations"]
    assert dict(parsed["_sections"]["Slice"]) == {"MemoryMax": "6656M"}


def test_the_script_never_joins_a_comment_to_a_section_header_or_a_directive():
    """Review round 2's blocker in its general form: a rendered line ending in a comment, with the newline to the next token
    lost, swallows that token. It happened to `[Slice]` (so three memory keys landed outside any section). Checked here
    over the WHOLE script — every line in it, heredoc included — with the SAME detector the drop-in tests use, so the same
    defect cannot reappear in the scope or service drop-in, in the usage text, or in a future template."""
    assert _fused_comment_violations(SCRIPT.name, SCRIPT.read_text()) == []


def test_no_runner_script_fuses_a_comment_onto_a_section_header_or_a_directive():
    """The same detector over every tracked runner script, because that block is copied between scripts and templates: any
    generated INI text in this directory is covered, not just this tool's own."""
    offenders = {}
    for path in sorted(RUNNER.iterdir()):
        if path.suffix != ".sh" and path.name not in {"run-job", "sync-from-devcontainer"}:
            continue
        violations = _fused_comment_violations(path.name, path.read_text())
        if violations:
            offenders[path.name] = violations
    assert offenders == {}, offenders


# The oracle the tests above rely on must itself be able to go red: these feed it the exact defects round 1 shipped and
# refuse a file that systemd would silently ignore. (They also pin the mutants below: re-fusing a comment with a header,
# or moving a key to another section, is what these inputs represent.)
def test_parse_ini_catches_a_section_header_fused_inside_a_comment():
    mixed = "# RENDERED by a tool.\n# CPUQuota is host policy.[Slice]\nMemoryHigh=infinity\nMemoryMax=6656M\n"
    parsed = parse_ini("fused", mixed)
    assert parsed["_violations"], "a [Slice] header inside a comment was accepted, so every key below it is invisible"
    assert parsed["_sections"] == {}  # and systemd would apply none of the keys


def test_parse_ini_catches_a_directive_fused_inside_a_comment():
    mixed = "# RENDERED by a tool.\n# the swap cap follows.MemorySwapMax=57344M\n[Slice]\nMemoryHigh=infinity\n"
    parsed = parse_ini("fused-key", mixed)
    assert any("MemorySwapMax" in v for v in parsed["_violations"]), parsed["_violations"]
    assert dict(parsed["_sections"]["Slice"]) == {"MemoryHigh": "infinity"}  # the fused key never arrives
    # and a comment that IS nothing but the token is refused too: nobody can see systemd read it
    parsed = parse_ini("bare-key", "# RENDERED by a tool.\n[Slice]\nMemoryHigh=infinity\n#MemoryMax=6656M\n")
    assert any("MemoryMax" in v and "is a comment" in v for v in parsed["_violations"]), parsed["_violations"]


def test_parse_ini_catches_a_key_before_any_section_header():
    early = "# RENDERED by a tool.\nMemoryMax=6656M\n[Slice]\nMemoryHigh=infinity\n"
    parsed = parse_ini("early", early)
    assert any("before any [section] header" in v for v in parsed["_violations"]), parsed["_violations"]


def test_assert_dropin_sections_catches_a_key_in_the_wrong_section():
    wrong = "# RENDERED by a tool.\n[Service]\nMemoryMax=6656M\nMemoryHigh=infinity\nMemorySwapMax=57344M\n"
    with pytest.raises(AssertionError) as e:
        assert_dropin_sections("wrong-section", wrong, "Slice", ["MemoryHigh", "MemoryMax", "MemorySwapMax"])
    assert "[Slice]" in str(e.value)


def test_assert_dropin_sections_catches_a_header_that_is_not_first():
    late = "# RENDERED by a tool.\n# a comment\nMemoryHigh=infinity\n[Slice]\nMemoryMax=6656M\n"
    with pytest.raises(AssertionError):
        assert_dropin_sections("late", late, "Slice", ["MemoryHigh", "MemoryMax"])


def test_assert_dropin_sections_catches_an_extra_key_the_policy_does_not_own():
    extra = "# RENDERED by a tool.\n[Slice]\nMemoryHigh=infinity\nMemoryMax=6656M\nMemorySwapMax=57344M\nMemoryLow=1G\n"
    with pytest.raises(AssertionError) as e:
        assert_dropin_sections("extra", extra, "Slice", ["MemoryHigh", "MemoryMax", "MemorySwapMax"])
    assert "MemoryLow" in str(e.value)


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
