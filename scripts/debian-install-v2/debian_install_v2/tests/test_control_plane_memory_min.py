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
    CONTROL_PLANE_SYSTEM_SLICE_CHILD_UNITS,
    CONTROL_PLANE_SYSTEM_SLICE_HEADROOM_MIB,
    CONTROL_PLANE_SYSTEM_SLICE_MEMORY_MIN_CEILING_MIB,
    CONTROL_PLANE_SYSTEM_SLICE_UNIT,
    control_plane_system_slice_memory_min_bytes,
    format_memory_mib_token,
    render_control_plane_memory_min_dropin,
    render_control_plane_memory_min_slice_dropin,
)


PROJECT = Path(__file__).resolve().parents[2]
VALUE_TABLE = PROJECT / "docs" / "CONTROL-PLANE-MEMORY.md"


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
