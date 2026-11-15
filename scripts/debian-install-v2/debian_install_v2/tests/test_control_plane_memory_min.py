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
    render_control_plane_memory_min_dropin,
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


def test_control_plane_memory_min_dropins_match_the_documented_table_and_rerender_idempotently(tmp_path):
    installer = make_installer(tmp_path)
    expected_bytes = {unit: value[1] for unit, value in CONTROL_PLANE_MEMORY_MIN.items()}
    assert documented_table_bytes() == expected_bytes

    installer._configure_control_plane_memory_min()
    first_render = dict(installer.actions.dry_run_writes)
    installer._configure_control_plane_memory_min()
    second_render = dict(installer.actions.dry_run_writes)

    assert second_render == first_render
    expected_paths = {
        f"/etc/systemd/system/{unit}.d/{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
        for unit in CONTROL_PLANE_MEMORY_MIN
    }
    assert set(second_render) == expected_paths
    for unit, (memory_min, _expected_bytes) in CONTROL_PLANE_MEMORY_MIN.items():
        path = f"/etc/systemd/system/{unit}.d/{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
        content = second_render[path]
        assert content == render_control_plane_memory_min_dropin(memory_min)
        assert content.splitlines().count(f"MemoryMin={memory_min}") == 1
        assert not re.search(r"(?m)^Memory(?:Max|High|Low)=", content)


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
    assert len(writes) == len(CONTROL_PLANE_MEMORY_MIN)
    assert reloads and reloads[-1] > writes[-1]


def test_control_plane_memory_min_sum_is_in_the_measured_profile_band():
    total_bytes = sum(value[1] for value in CONTROL_PLANE_MEMORY_MIN.values())
    assert 450_000_000 <= total_bytes <= 1_500_000_000
    assert total_bytes == 544 * 1024 * 1024


def test_control_plane_memory_min_readback_accepts_exact_systemd_values(tmp_path):
    installer = make_installer(tmp_path, dry_run=False)
    expected = {
        unit: str(value[1]) for unit, value in CONTROL_PLANE_MEMORY_MIN.items()
    }
    observed_units = []

    def systemctl_show(argv, description=""):
        assert argv[:4] == ["/usr/bin/systemctl", "show", "-p", "MemoryMin"]
        unit = argv[4]
        observed_units.append(unit)
        return f"MemoryMin={expected[unit]}\n"

    installer._run = systemctl_show
    installer._verify_control_plane_memory_min()
    assert observed_units == list(CONTROL_PLANE_MEMORY_MIN)


@pytest.mark.parametrize("reported", ["", "0", str(128 * 1024 * 1024 + 1)])
def test_control_plane_memory_min_readback_rejects_missing_zero_or_wrong_values(
    tmp_path, reported
):
    installer = make_installer(tmp_path, dry_run=False)

    def systemctl_show(argv, description=""):
        unit = argv[4]
        value = reported if unit == "docker.service" else str(
            CONTROL_PLANE_MEMORY_MIN[unit][1]
        )
        return f"MemoryMin={value}\n" if value else ""

    installer._run = systemctl_show
    with pytest.raises(InstallerError, match=r"docker\.service MemoryMin readback"):
        installer._verify_control_plane_memory_min()


def test_disabled_docker_omits_units_from_dropins_and_readback(tmp_path):
    installer = make_installer(tmp_path, run_docker_install=False)
    installer._configure_control_plane_memory_min()

    expected_units = ("ssh.service", "systemd-journald.service")
    assert set(installer.actions.dry_run_writes) == {
        f"/etc/systemd/system/{unit}.d/{CONTROL_PLANE_MEMORY_MIN_DROPIN}"
        for unit in expected_units
    }

    installer = make_installer(tmp_path / "readback", dry_run=False, run_docker_install=False)
    observed_units = []

    def systemctl_show(argv, description=""):
        unit = argv[4]
        observed_units.append(unit)
        return f"MemoryMin={CONTROL_PLANE_MEMORY_MIN[unit][1]}\n"

    installer._run = systemctl_show
    installer._verify_control_plane_memory_min()
    assert observed_units == list(expected_units)


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

    assert "control-plane service memory protection" in readme
    assert "docs/design-guide.md#control-plane-memory-protection" in readme
    assert "450–500 mb" in design and "unmeasured" in design
    assert "control-plane-memory.md" in design
    assert "cgprofiled.service" in design and "mdt-psi-guard.service" in design
    assert "systemctl show -p memorymin \"$unit\"" in consumers
    assert "operator-approved in-place drop-in application" in consumers
