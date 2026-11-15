"""Fixed service MemoryMin drop-ins and their post-install readback."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from debian_install_v2.actions import HostActions
from debian_install_v2.config import Config
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2.state import StateStore
from debian_install_v2.templates import (
    CONTROL_PLANE_MEMORY_MIN,
    CONTROL_PLANE_MEMORY_MIN_DROPIN,
    CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV,
    CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_VALUE,
    CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT,
    CONTROL_PLANE_SYSTEM_SLICE_CHILD_UNITS,
    CONTROL_PLANE_SYSTEM_SLICE_HEADROOM_MIB,
    CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB,
    CONTROL_PLANE_SYSTEM_SLICE_UNIT,
    control_plane_ram_bound_bytes,
    control_plane_ram_charged_bytes,
    control_plane_system_slice_memory_min_bytes,
    format_memory_mib_token,
    render_control_plane_memory_min_dropin,
    render_control_plane_memory_min_slice_dropin,
)


PROJECT = Path(__file__).resolve().parents[2]
VALUE_TABLE = PROJECT / "docs" / "CONTROL-PLANE-MEMORY.md"
# The real reader, so a test can restore it after the module-wide MemTotal pin.
_REAL_MEMORY_TOTAL_BYTES = Installer._memory_total_bytes


def make_installer(tmp_path, *, dry_run=True, **overrides) -> Installer:
    config = Config(
        state_dir=str(tmp_path / "state"),
        log_dir=str(tmp_path / "logs"),
        telegram_bot_token="",
        telegram_chat_id="",
        credential_mode="systemd",
        **overrides,
    )
    installer = Installer(config, HostActions(dry_run=dry_run), inspect_host=False)
    installer.state.dry_run = False
    StateStore(config.state_dir).save_new(StateStore.new(config))
    return installer


@pytest.fixture(autouse=True)
def _pin_mem_total(monkeypatch):
    """Pin MemTotal for every test in this module.

    The RAM bound reads the real /proc/meminfo on a live (non-dry-run) path, and
    a test result must not depend on the size of whatever host runs it (an 8
    GiB gate container and a 16 GiB cockpit would behave differently). Tests
    that exercise the bound install their own per-instance value, which shadows
    this class-level default.
    """
    monkeypatch.setattr(
        Installer, "_memory_total_bytes", lambda self: 8 * 1024 ** 3
    )


def documented_table_bytes() -> dict[str, int]:
    text = VALUE_TABLE.read_text(encoding="utf-8")
    rows = re.findall(r"(?m)^\| `([^`]+)` \| ([0-9]+) MiB \|", text)
    return {unit: int(mib) * 1024 * 1024 for unit, mib in rows}


def documented_system_slice_bytes() -> int:
    text = VALUE_TABLE.read_text(encoding="utf-8")
    match = re.search(
        r"(?m)^\| \*\*`system\.slice`\*\* \| \*\*([0-9]+) MiB \(([0-9,]+) bytes\)\*\*",
        text,
    )
    assert match, "documented system.slice floor row is missing"
    mib, digits = match.group(1), match.group(2).replace(",", "")
    assert int(mib) * 1024 * 1024 == int(digits)
    return int(digits)


def test_control_plane_memory_min_dropins_match_the_documented_table_and_rerender_idempotently(tmp_path):
    installer = make_installer(tmp_path)
    expected_bytes = {unit: value[1] for unit, value in CONTROL_PLANE_MEMORY_MIN.items()}
    assert documented_table_bytes() == expected_bytes

    installer._configure_control_plane_memory_min()
    first_render = dict(installer.actions.dry_run_writes)
    installer._configure_control_plane_memory_min()
    second_render = dict(installer.actions.dry_run_writes)

    assert second_render == first_render
    slice_path = (
        f"/etc/systemd/system/{CONTROL_PLANE_SYSTEM_SLICE_UNIT}.d/"
        f"{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
    )
    expected_paths = {
        f"/etc/systemd/system/{unit}.d/{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
        for unit in CONTROL_PLANE_MEMORY_MIN
    } | {slice_path}
    assert set(second_render) == expected_paths
    for unit, (memory_min, _expected_bytes) in CONTROL_PLANE_MEMORY_MIN.items():
        path = f"/etc/systemd/system/{unit}.d/{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
        content = second_render[path]
        assert content == render_control_plane_memory_min_dropin(memory_min)
        assert content.splitlines().count(f"MemoryMin={memory_min}") == 1
        assert not re.search(r"(?m)^Memory(?:Max|High|Low)=", content)
        assert not re.search(r"(?m)^Memory(?:SwapMax)=", content)

    slice_token = format_memory_mib_token(
        control_plane_system_slice_memory_min_bytes(CONTROL_PLANE_MEMORY_MIN)
    )
    assert second_render[slice_path] == render_control_plane_memory_min_slice_dropin(
        slice_token
    )
    assert "[Slice]" in second_render[slice_path]
    assert "[Service]" in second_render[path]
    assert not re.search(r"(?m)^Memory(?:Max|High|Low)=", second_render[slice_path])

    # A failure per slice: reading `systemctl show` for `system.slice` must
    # happen, and the slice section header must be the slice one.
    assert render_control_plane_memory_min_slice_dropin("373M").splitlines()[-2:] == [
        "[Slice]",
        "MemoryMin=373M",
    ]


def test_control_plane_memory_min_tokens_equal_their_byte_counts():
    # The rendered token (what systemd reads) and the byte count (what the
    # readback compares) are stored separately; a drift between them would
    # only surface on a live host as a failed install.
    for unit, (token, expected_bytes) in CONTROL_PLANE_MEMORY_MIN.items():
        match = re.fullmatch(r"([1-9][0-9]*)M", token)
        assert match, f"{unit}: unexpected MemoryMin token {token!r}"
        assert int(match.group(1)) * 1024 * 1024 == expected_bytes, unit


def test_control_plane_memory_min_drop_ins_are_reloaded_after_the_last_write(tmp_path):
    installer = make_installer(tmp_path)
    installer._configure_control_plane_memory_min()

    argvs = [action.argv for action in installer.actions.planned]
    writes = [
        index for index, argv in enumerate(argvs)
        if argv[0] == "/usr/bin/tee" and CONTROL_PLANE_MEMORY_MIN_DROPIN in argv[1]
    ]
    reloads = [
        index for index, argv in enumerate(argvs)
        if argv == ("/usr/bin/systemctl", "daemon-reload")
    ]
    assert len(writes) == len(CONTROL_PLANE_MEMORY_MIN) + 1
    assert reloads and reloads[-1] > writes[-1]


def test_memory_mib_token_refuses_a_non_whole_mib_value():
    # The derived byte count is rendered as a whole-MiB systemd token; a table
    # or headroom change that made it fractional would otherwise render a
    # token systemctl cannot report back as the expected byte count.
    assert format_memory_mib_token(373 * 1024 * 1024) == "373M"
    for bad in (0, 512, 1024 * 1024 + 1, -1024 * 1024):
        with pytest.raises(ValueError):
            format_memory_mib_token(bad)


def test_configure_records_the_derived_slice_floor_in_its_step_detail(tmp_path):
    # The step detail is the operator's evidence that the ancestor floor was
    # written from the table, not just requested.
    installer = make_installer(tmp_path)
    installer._configure_control_plane_memory_min()
    steps = installer.state.load()["steps"]
    detail = steps["control_plane_memory_min"]["detail"]
    assert detail.count(CONTROL_PLANE_SYSTEM_SLICE_UNIT) == 1
    assert f"{CONTROL_PLANE_SYSTEM_SLICE_UNIT}=" in detail
    assert "(derived)" in detail
    for unit, (memory_min, _bytes) in CONTROL_PLANE_MEMORY_MIN.items():
        assert f"{unit}={memory_min}" in detail, unit
    # The RAM bound is part of the recorded evidence, not only of the refusal.
    assert str(control_plane_ram_charged_bytes(CONTROL_PLANE_MEMORY_MIN)) in detail
    assert f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT}% of MemTotal" in detail


def test_system_slice_floor_follows_a_child_floor_change():
    # Kills a hand-written slice number that happens to equal today's sum: the
    # derived value must move by exactly a child floor's delta.
    base = control_plane_system_slice_memory_min_bytes(CONTROL_PLANE_MEMORY_MIN)
    grown = dict(CONTROL_PLANE_MEMORY_MIN)
    delta_mib = 48
    token = format_memory_mib_token(
        CONTROL_PLANE_MEMORY_MIN["docker.service"][1] + delta_mib * 1024 * 1024
    )
    grown["docker.service"] = (
        token,
        CONTROL_PLANE_MEMORY_MIN["docker.service"][1] + delta_mib * 1024 * 1024,
    )
    assert (
        control_plane_system_slice_memory_min_bytes(grown) - base
        == delta_mib * 1024 * 1024
    )
    # cgprofiled is not under system.slice, so its floor must not move it.
    moved_non_child = dict(CONTROL_PLANE_MEMORY_MIN)
    moved_non_child["cgprofiled.service"] = (
        format_memory_mib_token(
            CONTROL_PLANE_MEMORY_MIN["cgprofiled.service"][1] + 32 * 1024 * 1024
        ),
        CONTROL_PLANE_MEMORY_MIN["cgprofiled.service"][1] + 32 * 1024 * 1024,
    )
    assert control_plane_system_slice_memory_min_bytes(moved_non_child) == base


def test_system_slice_floor_is_derived_from_the_table_and_stays_under_the_ceiling():
    # `system.slice` needs a floor of its own (unit floors are inert without it
    # under memory_recursiveprot), and that floor is the sum of the floors of
    # the units living under system.slice plus headroom -- DERIVED, never a
    # second hand-written number.
    entries = dict(CONTROL_PLANE_MEMORY_MIN)
    child_bytes = sum(
        value[1] for unit, value in entries.items()
        if unit in CONTROL_PLANE_SYSTEM_SLICE_CHILD_UNITS
    )
    expected = child_bytes + CONTROL_PLANE_SYSTEM_SLICE_HEADROOM_MIB * 1024 * 1024
    assert control_plane_system_slice_memory_min_bytes(entries) == expected
    assert expected <= CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB * 1024 * 1024
    # cgprofiled runs under cgprofile.slice (MemoryMin=128M), so its floor
    # must not inflate the system.slice ancestor value.
    assert "cgprofiled.service" not in CONTROL_PLANE_SYSTEM_SLICE_CHILD_UNITS
    assert documented_system_slice_bytes() == expected


def test_system_slice_floor_follows_the_docker_drop_when_docker_is_disabled():
    entries = dict(CONTROL_PLANE_MEMORY_MIN)
    with_docker = control_plane_system_slice_memory_min_bytes(entries)
    entries.pop("docker.service")
    entries.pop("containerd.service")
    without_docker = control_plane_system_slice_memory_min_bytes(entries)
    assert with_docker - without_docker == (
        CONTROL_PLANE_MEMORY_MIN["docker.service"][1]
        + CONTROL_PLANE_MEMORY_MIN["containerd.service"][1]
    )


def test_system_slice_floor_above_the_measured_ceiling_refuses(tmp_path, monkeypatch):
    installer = make_installer(tmp_path)
    oversized = dict(CONTROL_PLANE_MEMORY_MIN)
    oversized["docker.service"] = (
        format_memory_mib_token(
            (CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB + 32) * 1024 * 1024
        ),
        (CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB + 32) * 1024 * 1024,
    )
    monkeypatch.setattr(
        "debian_install_v2.installer.CONTROL_PLANE_MEMORY_MIN", oversized
    )
    with pytest.raises(InstallerError, match=r"above the measured ceiling"):
        installer._control_plane_system_slice_memory_min_bytes(
            installer._control_plane_memory_min_entries()
        )
    # Refused during the configure step too, and only because of the derived
    # value: nothing is written, so no partial set of unit drop-ins is left.
    with pytest.raises(InstallerError, match=r"above the measured ceiling"):
        installer._configure_control_plane_memory_min()
    assert installer.actions.dry_run_writes == {}


def test_control_plane_memory_min_sum_matches_the_documented_total_and_stays_under_the_ceiling():
    # The table is now measured, but neither a specific total nor a lower bound
    # is pinned here: the documented Total row must equal the table and the
    # table must stay inside the documented overall ceiling (the same
    # tolerant style the placeholder round was reviewed with).
    values = [value[1] for value in CONTROL_PLANE_MEMORY_MIN.values()]
    assert all(value > 0 for value in values)
    total_bytes = sum(values)
    assert total_bytes <= 1_500_000_000
    text = VALUE_TABLE.read_text(encoding="utf-8")
    match = re.search(r"(?m)^\| \*\*Total\*\* \| \*\*([0-9]+) MiB \(([0-9,]+) bytes\)\*\*", text)
    assert match, "documented Total row is missing"
    assert int(match.group(1)) * 1024 * 1024 == total_bytes
    assert int(match.group(2).replace(",", "")) == total_bytes


def expected_slice_bytes() -> int:
    return control_plane_system_slice_memory_min_bytes(CONTROL_PLANE_MEMORY_MIN)


def system_slice_readback(value: str) -> str:
    return f"MemoryMin={value}\n" if value else ""


def test_the_documented_ceiling_matches_the_code_constant():
    # The 460 MiB ceiling is a documented limit the installer enforces; a code
    # change without a doc change would leave the documents claiming a bound
    # the code no longer applies.
    ceiling = f"{CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB} mib"
    for path in (VALUE_TABLE, PROJECT / "docs" / "DESIGN-GUIDE.md"):
        assert ceiling in path.read_text(encoding="utf-8").lower(), path
    assert CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB == 460


def test_control_plane_memory_min_readback_accepts_exact_systemd_values(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    expected = {
        unit: str(value[1]) for unit, value in CONTROL_PLANE_MEMORY_MIN.items()
    }
    slice_value = str(expected_slice_bytes())
    observed_units = []

    def systemctl_show(argv, description=""):
        assert argv[:3] == ["/usr/bin/systemctl", "show", "-p"]
        prop, unit = argv[3:5]
        if unit == CONTROL_PLANE_SYSTEM_SLICE_UNIT:
            assert prop == "MemoryMin"
            observed_units.append(unit)
            return system_slice_readback(slice_value)
        if prop == "LoadState":
            return "LoadState=loaded\n"
        assert prop == "MemoryMin"
        observed_units.append(unit)
        return f"MemoryMin={expected[unit]}\n"

    installer._run = systemctl_show
    installer._verify_control_plane_memory_min()
    assert observed_units == [*CONTROL_PLANE_MEMORY_MIN, CONTROL_PLANE_SYSTEM_SLICE_UNIT]


@pytest.mark.parametrize("reported", ["", "0", str(128 * 1024 * 1024 + 1)])
def test_control_plane_memory_min_readback_rejects_missing_zero_or_wrong_values(
    tmp_path, reported
):
    installer = make_installer(tmp_path, dry_run=False)
    slice_value = str(expected_slice_bytes())

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        if unit == CONTROL_PLANE_SYSTEM_SLICE_UNIT:
            return system_slice_readback(slice_value)
        if prop == "LoadState":
            return "LoadState=loaded\n"
        assert prop == "MemoryMin"
        value = reported if unit == "docker.service" else str(
            CONTROL_PLANE_MEMORY_MIN[unit][1]
        )
        return f"MemoryMin={value}\n" if value else ""

    installer._run = systemctl_show
    with pytest.raises(InstallerError, match=r"docker\.service MemoryMin readback"):
        installer._verify_control_plane_memory_min()


@pytest.mark.parametrize("reported", ["", "0", "999999999"])
def test_readback_rejects_a_wrong_or_missing_system_slice_floor(tmp_path, reported):
    # The per-unit floors are inert while system.slice.memory.min is 0
    # (measured 2026-11-15 on v1001), so the ancestor floor is verified like a
    # unit value rather than left to the documented procedure.
    installer = make_installer(tmp_path, dry_run=False)

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        if unit == CONTROL_PLANE_SYSTEM_SLICE_UNIT:
            assert prop == "MemoryMin"
            return system_slice_readback(reported)
        if prop == "LoadState":
            return "LoadState=loaded\n"
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN[unit][1]}\n"

    installer._run = systemctl_show
    with pytest.raises(
        InstallerError, match=r"system\.slice MemoryMin readback is .* expected"
    ):
        installer._verify_control_plane_memory_min()


def test_disabled_docker_omits_units_from_dropins_and_readback(tmp_path):
    installer = make_installer(tmp_path, run_docker_install=False)
    installer._configure_control_plane_memory_min()

    entries = installer._control_plane_memory_min_entries()
    expected_units = (
        "ssh.service", "systemd-journald.service",
        "cgprofiled.service", "mdt-psi-guard.service",
    )
    slice_path = (
        f"/etc/systemd/system/{CONTROL_PLANE_SYSTEM_SLICE_UNIT}.d/"
        f"{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
    )
    assert set(installer.actions.dry_run_writes) == {
        f"/etc/systemd/system/{unit}.d/{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
        for unit in expected_units
    } | {slice_path}
    # The written slice drop-in is the value derived for THIS profile, not a
    # literal that happens to match the default profile.
    assert installer.actions.dry_run_writes[slice_path] == (
        render_control_plane_memory_min_slice_dropin(
            format_memory_mib_token(control_plane_system_slice_memory_min_bytes(entries))
        )
    )

    installer = make_installer(tmp_path / "readback", dry_run=False, run_docker_install=False)
    entries = installer._control_plane_memory_min_entries()
    slice_value = str(control_plane_system_slice_memory_min_bytes(entries))
    observed_units = []

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        if unit == CONTROL_PLANE_SYSTEM_SLICE_UNIT:
            observed_units.append(unit)
            return system_slice_readback(slice_value)
        if prop == "LoadState":
            return "LoadState=loaded\n"
        assert prop == "MemoryMin"
        observed_units.append(unit)
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN[unit][1]}\n"

    installer._run = systemctl_show
    installer._verify_control_plane_memory_min()
    assert observed_units == [*expected_units, CONTROL_PLANE_SYSTEM_SLICE_UNIT]
    # The derived ancestor floor drops the Docker pair with them.
    assert control_plane_system_slice_memory_min_bytes(entries) < expected_slice_bytes()


def test_optional_control_plane_units_may_be_absent(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    optional_states = {
        "cgprofiled.service": "not-found",
        "mdt-psi-guard.service": "masked",
    }
    slice_value = str(expected_slice_bytes())
    memory_readbacks = []

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        if unit == CONTROL_PLANE_SYSTEM_SLICE_UNIT:
            memory_readbacks.append(unit)
            return system_slice_readback(slice_value)
        if prop == "LoadState":
            state = optional_states.get(unit, "loaded")
            return f"LoadState={state}\n"
        assert prop == "MemoryMin"
        memory_readbacks.append(unit)
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN[unit][1]}\n"

    installer._run = systemctl_show
    installer._verify_control_plane_memory_min()
    assert memory_readbacks == [
        "ssh.service", "docker.service", "containerd.service", "systemd-journald.service",
        CONTROL_PLANE_SYSTEM_SLICE_UNIT,
    ]


@pytest.mark.parametrize("load_output", ["", "LoadState=activating\n"])
def test_profile_verification_rejects_a_unit_without_loaded_state(tmp_path, load_output):
    installer = make_installer(tmp_path, dry_run=False)
    queries = []

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        queries.append((prop, unit))
        if prop == "LoadState":
            return load_output if unit == "ssh.service" else "LoadState=loaded\n"
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN[unit][1]}\n"

    installer._run = systemctl_show
    with pytest.raises(InstallerError, match=r"ssh\.service LoadState readback"):
        installer._verify_control_plane_memory_min()
    assert queries == [("LoadState", "ssh.service")]


@pytest.mark.parametrize("optional_unit", ["cgprofiled.service", "mdt-psi-guard.service"])
@pytest.mark.parametrize("load_output", ["", "LoadState=activating\n", "LoadState=error\n"])
def test_optional_units_are_skipped_only_for_not_found_or_masked(
    tmp_path, optional_unit, load_output
):
    # Narrow skip: an optional unit with a missing or non-loaded state that is
    # not not-found/masked must still fail, not be silently skipped.
    installer = make_installer(tmp_path, dry_run=False)

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        if prop == "LoadState":
            return load_output if unit == optional_unit else "LoadState=loaded\n"
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN.get(unit, ('', 0))[1]}\n"

    installer._run = systemctl_show
    with pytest.raises(
        InstallerError, match=rf"{re.escape(optional_unit)} LoadState readback"
    ):
        installer._verify_control_plane_memory_min()


def test_system_slice_floor_exactly_at_the_ceiling_is_accepted(tmp_path, monkeypatch):
    # Boundary of the documented "above 460 MiB refuses" rule: a derived value
    # of exactly the ceiling is allowed, one MiB more is refused.
    installer = make_installer(tmp_path)
    ceiling = CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB * 1024 * 1024
    others = sum(
        value[1] for unit, value in CONTROL_PLANE_MEMORY_MIN.items()
        if unit in CONTROL_PLANE_SYSTEM_SLICE_CHILD_UNITS and unit != "docker.service"
    )
    headroom = CONTROL_PLANE_SYSTEM_SLICE_HEADROOM_MIB * 1024 * 1024

    def with_docker(nbytes):
        table = dict(CONTROL_PLANE_MEMORY_MIN)
        table["docker.service"] = (format_memory_mib_token(nbytes), nbytes)
        return table

    monkeypatch.setattr(
        "debian_install_v2.installer.CONTROL_PLANE_MEMORY_MIN",
        with_docker(ceiling - others - headroom),
    )
    entries = installer._control_plane_memory_min_entries()
    assert installer._control_plane_system_slice_memory_min_bytes(entries) == ceiling

    monkeypatch.setattr(
        "debian_install_v2.installer.CONTROL_PLANE_MEMORY_MIN",
        with_docker(ceiling - others - headroom + 1024 * 1024),
    )
    entries = installer._control_plane_memory_min_entries()
    with pytest.raises(InstallerError, match=r"above the measured ceiling"):
        installer._control_plane_system_slice_memory_min_bytes(entries)


@pytest.mark.parametrize("missing_state", ["not-found", "masked"])
def test_profile_verification_rejects_a_missing_mandatory_unit(tmp_path, missing_state):
    installer = make_installer(tmp_path, dry_run=False)
    queries = []

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        queries.append((prop, unit))
        if prop == "LoadState":
            state = missing_state if unit == "ssh.service" else "loaded"
            return f"LoadState={state}\n"
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN[unit][1]}\n"

    installer._run = systemctl_show
    with pytest.raises(
        InstallerError,
        match=rf"ssh\.service is mandatory.*LoadState={re.escape(missing_state)}",
    ):
        installer._verify_control_plane_memory_min()
    assert queries == [("LoadState", "ssh.service")]


@pytest.mark.parametrize(
    ("unit", "reported"),
    [
        ("cgprofiled.service", "0"),
        (
            "mdt-psi-guard.service",
            str(CONTROL_PLANE_MEMORY_MIN["mdt-psi-guard.service"][1] + 1),
        ),
    ],
)
def test_loaded_optional_units_still_require_their_exact_memory_min(
    tmp_path, unit, reported
):
    installer = make_installer(tmp_path, dry_run=False)

    def systemctl_show(argv, description=""):
        prop, current_unit = argv[3:5]
        if prop == "LoadState":
            return "LoadState=loaded\n"
        expected = CONTROL_PLANE_MEMORY_MIN[current_unit][1]
        value = reported if current_unit == unit else str(expected)
        return f"MemoryMin={value}\n"

    installer._run = systemctl_show
    with pytest.raises(InstallerError, match=rf"{re.escape(unit)} MemoryMin readback"):
        installer._verify_control_plane_memory_min()


def test_memory_min_is_installed_and_checked_by_both_install_phases_and_verify():
    source = (PROJECT / "debian_install_v2" / "installer.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    installer_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Installer"
    )
    methods = {
        node.name: node for node in installer_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def called_methods(method):
        return {
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

    assert "_configure_control_plane_memory_min" in called_methods(methods["_stage1"])
    assert "_configure_control_plane_memory_min" in called_methods(methods["_stage2"])
    assert "_verify_control_plane_memory_min" in called_methods(methods["_stage2"])
    assert "_verify_control_plane_memory_min" in called_methods(methods["verify"])


def test_three_user_documents_explain_what_why_and_how():
    readme = (PROJECT / "README.md").read_text(encoding="utf-8").lower()
    design = (PROJECT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8").lower()
    consumers = (PROJECT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8").lower()
    table = VALUE_TABLE.read_text(encoding="utf-8").lower()

    assert "control-plane service memory protection" in readme
    assert "docs/design-guide.md#control-plane-memory-protection" in readme
    assert "system.slice" in readme
    # The floors are measured now; the measurement's provenance, its per-host
    # confidence, and its limits are stated in the design document.
    assert "control-plane-hotset-report.md" in design
    assert "measured" in design and "1.25" in design
    assert "v1001" in design and "r1002" in design
    assert "inferred" in design
    assert "base, not a bound" in design
    assert "re-measure when the docker or containerd version changes" in design
    assert "460" in design and "system.slice" in design
    assert "fd-007c" in design
    assert "control-plane-memory.md" in design
    assert "cgprofiled.service" in design and "mdt-psi-guard.service" in design
    assert "mandatory" in design and "optional" in design
    assert "loadstate=not-found" in consumers
    assert "systemctl show -p loadstate -p memorymin \"$unit\"" in consumers
    assert "system.slice" in consumers and "derived" in consumers
    assert "operator-approved in-place drop-in application" in consumers
    # The value table says what is measured, what is inferred, and what the
    # derived ancestor floor is.
    assert "what is measured and what is inferred" in table
    assert "system.slice" in table and "derived" in table
    assert "div2-bl-10" in table and "div2-bl-11" in table and "fd-007c" in table
    # The installed-RAM bound is documented where an adopter looks for it,
    # including the fraction and the test-guest override name.
    assert f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT}%" in table
    assert CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV.lower() in table
    assert "div2-bl-12" in table
    assert f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT}% of `memtotal`" in design
    assert CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV.lower() in design
    assert CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV.lower() in consumers
    assert f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT}%" in consumers


def charged_mib() -> int:
    return control_plane_ram_charged_bytes(CONTROL_PLANE_MEMORY_MIN) // (1024 * 1024)


def test_ram_charged_bytes_count_the_slice_once_and_its_outside_floors_on_top():
    # The derived `system.slice` floor already contains its children's floors,
    # so charging the whole six-unit table again would double-count 341 MiB of
    # protection configured once. Only floors OUTSIDE that slice are additive.
    slice_bytes = control_plane_system_slice_memory_min_bytes(CONTROL_PLANE_MEMORY_MIN)
    outside_bytes = sum(
        value[1] for unit, value in CONTROL_PLANE_MEMORY_MIN.items()
        if unit not in CONTROL_PLANE_SYSTEM_SLICE_CHILD_UNITS
    )
    assert control_plane_ram_charged_bytes(CONTROL_PLANE_MEMORY_MIN) == (
        slice_bytes + outside_bytes
    )
    # A child floor change moves the charge exactly once (through the slice).
    grown = dict(CONTROL_PLANE_MEMORY_MIN)
    grown["docker.service"] = (
        format_memory_mib_token(CONTROL_PLANE_MEMORY_MIN["docker.service"][1] + 8 * 1024 * 1024),
        CONTROL_PLANE_MEMORY_MIN["docker.service"][1] + 8 * 1024 * 1024,
    )
    assert (
        control_plane_ram_charged_bytes(grown)
        - control_plane_ram_charged_bytes(CONTROL_PLANE_MEMORY_MIN)
        == 8 * 1024 * 1024
    )
    # Only the floors outside system.slice (cgprofiled) are added on top.
    assert outside_bytes == CONTROL_PLANE_MEMORY_MIN["cgprofiled.service"][1]
    assert charged_mib() == slice_bytes // (1024 * 1024) + 4


def test_ram_bound_is_the_documented_fraction_and_accepts_the_8_gib_hosts():
    # r1002 and v1001 have 8 GiB. MemTotal there is below the nominal 8 GiB
    # (firmware/kernel reservations), so the accepted size is pinned from
    # BELOW that nominal value, not from it: the merged table must install on
    # both real hosts, and this test would fail if a table change or a tighter
    # fraction pushed the bound under what those hosts really report.
    charged = control_plane_ram_charged_bytes(CONTROL_PLANE_MEMORY_MIN)
    assert control_plane_ram_bound_bytes(8 * 1024 ** 3) == 8 * 1024 ** 3 * 10 // 100
    assert charged <= control_plane_ram_bound_bytes(8 * 1024 ** 3)
    # Real hosts report a bit less than the nominal 8 GiB; the bound still holds
    # with generous margin (about 4.6% used against a 10% bound).
    for mem_total_gib in (7990, 7800, 7500):
        assert charged <= control_plane_ram_bound_bytes(mem_total_gib * 1024 * 1024)
    # The smallest accepted host is the fraction crossing point, so a 2 GiB
    # test guest is refused and an 8 GiB production host is accepted.
    minimum = charged * 100 // CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT
    assert charged <= control_plane_ram_bound_bytes(minimum)
    assert charged > control_plane_ram_bound_bytes(minimum - 1)
    assert charged > control_plane_ram_bound_bytes(2 * 1024 ** 3)


def test_dry_run_still_accepts_the_table_on_the_planning_ram_assumption(tmp_path, monkeypatch):
    # `--dry-run` has no host to measure and keeps the same 8 GiB planning
    # assumption swap sizing uses, so a dry run on ANY host plans the table
    # instead of refusing on the cockpit's own memory size.
    installer = make_installer(tmp_path)
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: 8 * 1024 ** 3)
    installer._configure_control_plane_memory_min()
    assert installer.actions.dry_run_writes


def test_configure_refuses_a_host_too_small_for_the_measured_floors(tmp_path, monkeypatch):
    # THE GUARD: a 2 GiB host must refuse rather than ship floors that claim
    # ~19% of its RAM, and it must refuse before writing any drop-in.
    installer = make_installer(tmp_path)
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: 2 * 1024 ** 3)
    charged = control_plane_ram_charged_bytes(installer._control_plane_memory_min_entries())
    with pytest.raises(InstallerError) as caught:
        installer._configure_control_plane_memory_min()
    message = str(caught.value)
    # The message must name all four facts the operator needs to act on.
    assert str(charged) in message
    assert str(2 * 1024 ** 3) in message
    assert str(control_plane_ram_bound_bytes(2 * 1024 ** 3)) in message
    assert CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV in message
    # Assignment form, so an operator can copy it: a message naming the variable
    # without the value it must be set to is not an escape hatch.
    assert (
        f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV}="
        f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_VALUE}" in message
    )
    # The "you need a host at least this large" figure is the exact crossing
    # point (the smallest MemTotal whose bound covers the floors), so the number
    # printed is actionable and matches what the boundary test proves.
    minimum = charged * 100 // CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT
    assert charged <= control_plane_ram_bound_bytes(minimum)
    assert charged > control_plane_ram_bound_bytes(minimum - 1)
    assert f"at least {minimum} bytes" in message
    assert f"{format_memory_mib_token(charged)}" in message
    assert f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT}%" in message
    assert installer.actions.dry_run_writes == {}


def test_the_ram_bound_boundary_is_inclusive(tmp_path, monkeypatch):
    # At exactly the bound the table installs; one byte less refuses. This is
    # the difference between a bound and a suggestion.
    installer = make_installer(tmp_path)
    charged = control_plane_ram_charged_bytes(installer._control_plane_memory_min_entries())
    exact_total = charged * 100 // CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT
    assert control_plane_ram_bound_bytes(exact_total) >= charged

    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: exact_total)
    installer._configure_control_plane_memory_min()
    assert installer.actions.dry_run_writes

    installer = make_installer(tmp_path / "tight")
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: exact_total - 1)
    with pytest.raises(InstallerError, match=r"above .*MemTotal"):
        installer._configure_control_plane_memory_min()


def test_the_ram_bound_uses_the_floors_this_profile_installs(tmp_path, monkeypatch):
    # With Docker disabled the floors drop by 302 MiB and the charged value (and
    # therefore the smallest accepted host) must drop with them.
    installer = make_installer(tmp_path, run_docker_install=False)
    entries = installer._control_plane_memory_min_entries()
    without_docker = control_plane_ram_charged_bytes(entries)
    assert without_docker < control_plane_ram_charged_bytes(CONTROL_PLANE_MEMORY_MIN)
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: 2 * 1024 ** 3)
    installer._configure_control_plane_memory_min()
    assert installer.actions.dry_run_writes


def test_the_documented_override_is_the_only_way_past_the_bound(tmp_path, monkeypatch):
    # A test guest (1-2 GiB VM) cannot honour measured floors. Silently scaling
    # them would make them meaningless, so the only way past the bound is the
    # documented, explicit override -- and nothing else.
    monkeypatch.delenv(CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV, raising=False)
    installer = make_installer(tmp_path)
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: 2 * 1024 ** 3)
    with pytest.raises(InstallerError, match=r"MemTotal"):
        installer._configure_control_plane_memory_min()

    # An unrelated value is not an override (no fuzzy truthiness).
    for value in ("no", "1", "true", ""):
        monkeypatch.setenv(CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV, value)
        with pytest.raises(InstallerError, match=r"MemTotal"):
            installer._configure_control_plane_memory_min()

    monkeypatch.setenv(
        CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV,
        CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_VALUE,
    )
    installer._configure_control_plane_memory_min()
    step = installer.state.load()["steps"]["control_plane_memory_min"]
    assert step["status"] == "planned"
    assert str(control_plane_ram_charged_bytes(installer._control_plane_memory_min_entries())) in (
        step["detail"]
    )


@pytest.mark.parametrize(
    "value",
    ["YES", "Yes", " yes ", " yes\n"],
)
def test_the_override_value_is_compared_case_and_whitespace_insensitively(
    tmp_path, monkeypatch, value
):
    # The documented value is `yes`, but an operator writing YES in an env file
    # (or pasting it with trailing whitespace) means the same thing; the parsing
    # must not silently ignore a stated intent, and it must not widen the
    # accepted vocabulary either (see the per-value refusal test above).
    installer = make_installer(tmp_path)
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: 2 * 1024 ** 3)
    monkeypatch.setenv(CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV, value)
    installer._configure_control_plane_memory_min()
    assert installer.actions.dry_run_writes


def test_verify_re_checks_the_ram_bound_without_failing_on_an_8_gib_host(tmp_path, monkeypatch):
    # `verify` compares the CONFIGURED floors with MemTotal; it cannot see that
    # a floor is a sane share of RAM by reading systemd back alone.
    installer = make_installer(tmp_path, dry_run=False)
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: 8 * 1024 ** 3)
    entries = installer._control_plane_memory_min_entries()
    slice_value = str(control_plane_system_slice_memory_min_bytes(entries))

    def systemctl_show(argv, description=""):
        prop, unit = argv[3:5]
        if unit == CONTROL_PLANE_SYSTEM_SLICE_UNIT:
            return f"MemoryMin={slice_value}\n"
        if prop == "LoadState":
            return "LoadState=loaded\n"
        return f"MemoryMin={entries[unit][1]}\n"

    installer._run = systemctl_show
    installer._verify_control_plane_memory_min()
    detail = installer.state.load()["steps"]["control_plane_memory_min"]["detail"]
    assert f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_PERCENT}% of MemTotal RAM bound" in detail


def test_verify_refuses_when_the_host_ram_is_below_the_bound(tmp_path, monkeypatch):
    # The same guard in verify: a host that shrank (or was restored from a
    # larger machine's state) must not read back as a healthy install.
    installer = make_installer(tmp_path, dry_run=False)
    monkeypatch.setattr(installer, "_memory_total_bytes", lambda: 1 * 1024 ** 3)
    queries = []

    def systemctl_show(argv, description=""):
        queries.append(argv)
        prop, unit = argv[3:5]
        if unit == CONTROL_PLANE_SYSTEM_SLICE_UNIT:
            return f"MemoryMin={control_plane_system_slice_memory_min_bytes(CONTROL_PLANE_MEMORY_MIN)}\n"
        if prop == "LoadState":
            return "LoadState=loaded\n"
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN[unit][1]}\n"

    installer._run = systemctl_show
    with pytest.raises(InstallerError, match=r"MemTotal"):
        installer._verify_control_plane_memory_min()
    # The refusal comes before any readback query, so it is the bound that fails.
    assert queries == []


def test_mem_total_reader_never_reads_a_host_file_on_a_dry_run(tmp_path, monkeypatch):
    # A dry run has no host to measure and must plan against the documented
    # 8 GiB assumption, not the cockpit's own memory (which is a different
    # machine's fact and would make planning depend on where it ran).
    monkeypatch.setattr(Installer, "_memory_total_bytes", _REAL_MEMORY_TOTAL_BYTES)
    installer = make_installer(tmp_path)
    touched = []

    class _FakePath:
        def __init__(self, *args, **_kwargs):
            touched.append(args)

        @staticmethod
        def read_text(**_kwargs):
            raise AssertionError("a dry run must not read /proc/meminfo")

    monkeypatch.setattr("debian_install_v2.installer.Path", _FakePath)
    assert installer._memory_total_bytes() == 8 * 1024 ** 3
    assert touched == []


def test_mem_total_reader_names_both_of_its_consumers_and_refuses_a_bad_meminfo(tmp_path, monkeypatch):
    # This is the reader's own contract, so it runs against the real method
    # rather than the module-wide MemTotal pin.
    monkeypatch.setattr(Installer, "_memory_total_bytes", _REAL_MEMORY_TOTAL_BYTES)
    installer = make_installer(tmp_path, dry_run=False)
    class _FakePath:
        def __init__(self, *_args, **_kwargs):
            pass

        @staticmethod
        def read_text(**_kwargs):
            return "MemFree: 1 kB\n"

    monkeypatch.setattr("debian_install_v2.installer.Path", _FakePath)
    with pytest.raises(InstallerError, match=r"MemTotal.*automatic swap sizing.*RAM bound"):
        installer._memory_total_bytes()


def test_mem_total_reader_converts_kib_to_bytes_and_fails_closed_when_unreadable(
    tmp_path, monkeypatch
):
    # The reader's unit contract: /proc/meminfo reports kB (really KiB), and the
    # bound compares BYTES. A missing factor would make every host look 1024x
    # smaller, so it is pinned with a realistic '8 GB VM' value (7.5 GiB).
    monkeypatch.setattr(Installer, "_memory_total_bytes", _REAL_MEMORY_TOTAL_BYTES)
    installer = make_installer(tmp_path, dry_run=False)
    seven_and_a_half_gib_kib = 7 * 1024 * 1024 + 512 * 1024
    state = {"text": f"MemTotal:       {seven_and_a_half_gib_kib} kB\nMemFree: 1 kB\n"}

    class _FakePath:
        def __init__(self, *_args, **_kwargs):
            pass

        @staticmethod
        def read_text(**_kwargs):
            if isinstance(state["text"], Exception):
                raise state["text"]
            return state["text"]

    monkeypatch.setattr("debian_install_v2.installer.Path", _FakePath)
    assert installer._memory_total_bytes() == seven_and_a_half_gib_kib * 1024
    assert installer._memory_total_bytes() == 7.5 * 1024 ** 3
    # A real 7.5 GiB and 7.0 GiB host are accepted at the full table; the
    # reader's value feeds the bound directly.
    charged = control_plane_ram_charged_bytes(CONTROL_PLANE_MEMORY_MIN)
    assert charged <= control_plane_ram_bound_bytes(installer._memory_total_bytes())
    assert charged <= control_plane_ram_bound_bytes(7 * 1024 ** 3)

    # An unreadable file is an InstallerError that says why, not a traceback and
    # not a guessed host size.
    state["text"] = PermissionError("denied")
    with pytest.raises(InstallerError, match=r"cannot determine MemTotal.*denied"):
        installer._memory_total_bytes()


def test_stage2_unit_forwards_the_override_only_when_it_is_set(tmp_path):
    # Stage two is a systemd unit: it inherits nothing from the stage-one shell,
    # so the documented test-guest override has to travel in bootstrap.env.
    import os as _os

    installer = make_installer(tmp_path)
    installer._install_stage2()
    env = installer.actions.dry_run_writes["/etc/vbpub/bootstrap.env"]
    assert CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV not in env

    installer = make_installer(tmp_path / "with-override")
    previous = _os.environ.get(CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV)
    _os.environ[CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV] = (
        CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_VALUE
    )
    try:
        installer._install_stage2()
    finally:
        if previous is None:
            del _os.environ[CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV]
        else:
            _os.environ[CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV] = previous
    env = installer.actions.dry_run_writes["/etc/vbpub/bootstrap.env"]
    assert (
        f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_ENV}="
        f"{CONTROL_PLANE_MEMORY_MIN_RAM_BOUND_OVERRIDE_VALUE}" in env
    )
