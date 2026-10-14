"""Oracles for the CIU release gate's installed Run-Gate wheel check."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


CIU_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = CIU_ROOT / "scripts" / "check_run_gate_wheel.py"
SPEC = importlib.util.spec_from_file_location("check_run_gate_wheel", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
check_run_gate_wheel = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_run_gate_wheel)


def test_matching_installed_module_is_accepted(tmp_path):
    venv = tmp_path / "venv"
    installed = venv / "lib" / "run_gate.py"
    source = tmp_path / "checkout" / "run_gate.py"
    installed.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    installed.write_bytes(b"same module\n")
    source.write_bytes(b"same module\n")

    check_run_gate_wheel.verify_module_file(
        "run_gate", installed, source, venv
    )


def test_byte_mismatch_is_rejected_with_module_name(tmp_path):
    venv = tmp_path / "venv"
    installed = venv / "lib" / "run_gate.py"
    source = tmp_path / "checkout" / "run_gate.py"
    installed.parent.mkdir(parents=True)
    source.parent.mkdir(parents=True)
    installed.write_bytes(b"old wheel\n")
    source.write_bytes(b"selected checkout\n")

    with pytest.raises(SystemExit, match="run_gate") as exc:
        check_run_gate_wheel.verify_module_file(
            "run_gate", installed, source, venv
        )
    assert "tester-unified/README.md" in str(exc.value)
    assert "exact Run-Gate wheel" in str(exc.value)


def test_module_outside_installed_venv_is_rejected(tmp_path):
    venv = tmp_path / "venv"
    installed = tmp_path / "checkout" / "run_gate.py"
    source = installed
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"same module\n")

    with pytest.raises(SystemExit, match="outside tester-unified's installed venv"):
        check_run_gate_wheel.verify_module_file(
            "run_gate", installed, source, venv
        )


def test_missing_checkout_module_is_rejected(tmp_path):
    venv = tmp_path / "venv"
    installed = venv / "lib" / "run_gate.py"
    source = tmp_path / "checkout" / "run_gate.py"
    installed.parent.mkdir(parents=True)
    installed.write_bytes(b"same module\n")

    with pytest.raises(SystemExit, match="checkout source is missing"):
        check_run_gate_wheel.verify_module_file(
            "run_gate", installed, source, venv
        )


def test_main_checks_both_installed_wheel_modules(monkeypatch, tmp_path, capsys):
    checkout_root = tmp_path / "checkout"
    script_path = checkout_root / "ciu" / "scripts" / "check_run_gate_wheel.py"
    source_root = checkout_root / "run-gate-project"
    venv_root = tmp_path / "venv"
    script_path.parent.mkdir(parents=True)
    source_root.mkdir()
    (source_root / "pyproject.toml").write_text(
        '[project.scripts]\nrun-gate = "run_gate:main"\n', encoding="utf-8"
    )
    modules = {}
    for module_name in ("run_gate", "run_gate_admission"):
        source_path = source_root / f"{module_name}.py"
        installed_path = venv_root / "lib" / f"{module_name}.py"
        installed_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(module_name.encode("ascii"))
        installed_path.write_bytes(module_name.encode("ascii"))
        modules[module_name] = SimpleNamespace(__file__=str(installed_path))

    monkeypatch.setattr(check_run_gate_wheel, "__file__", str(script_path))
    monkeypatch.setattr(check_run_gate_wheel.sys, "prefix", str(venv_root))
    monkeypatch.setattr(
        check_run_gate_wheel.importlib,
        "import_module",
        lambda module_name: modules[module_name],
    )
    monkeypatch.setattr(
        check_run_gate_wheel.importlib.metadata,
        "distribution",
        lambda _name: SimpleNamespace(entry_points=[
            SimpleNamespace(name="run-gate", value="run_gate:main", group="console_scripts")
        ]),
    )

    assert check_run_gate_wheel.main() == 0
    assert "modules and console entry points match the selected checkout" in capsys.readouterr().out


def test_main_rejects_only_stale_admission_module(monkeypatch, tmp_path):
    checkout_root = tmp_path / "checkout"
    script_path = checkout_root / "ciu" / "scripts" / "check_run_gate_wheel.py"
    source_root = checkout_root / "run-gate-project"
    venv_root = tmp_path / "venv"
    script_path.parent.mkdir(parents=True)
    source_root.mkdir()
    (source_root / "pyproject.toml").write_text(
        '[project.scripts]\nrun-gate = "run_gate:main"\n', encoding="utf-8"
    )
    modules = {}
    for module_name in ("run_gate", "run_gate_admission"):
        source_path = source_root / f"{module_name}.py"
        installed_path = venv_root / "lib" / f"{module_name}.py"
        installed_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(module_name.encode("ascii"))
        installed_path.write_bytes(
            b"old admission wheel" if module_name == "run_gate_admission"
            else module_name.encode("ascii")
        )
        modules[module_name] = SimpleNamespace(__file__=str(installed_path))

    monkeypatch.setattr(check_run_gate_wheel, "__file__", str(script_path))
    monkeypatch.setattr(check_run_gate_wheel.sys, "prefix", str(venv_root))
    monkeypatch.setattr(
        check_run_gate_wheel.importlib,
        "import_module",
        lambda module_name: modules[module_name],
    )

    with pytest.raises(SystemExit, match="run_gate_admission"):
        check_run_gate_wheel.main()


def test_stale_console_entry_point_is_rejected():
    installed = SimpleNamespace(entry_points=[
        SimpleNamespace(name="run-gate", value="old_module:main", group="console_scripts")
    ])
    with pytest.raises(SystemExit, match="console entry points do not match"):
        check_run_gate_wheel.verify_console_scripts(
            installed, {"run-gate": "run_gate:main"}
        )
