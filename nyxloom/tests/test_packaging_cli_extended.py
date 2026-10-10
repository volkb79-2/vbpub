"""NYX-CLIX: cli-extended is a real wheel dependency of nyxloom, never vendored.

Program decision CX-D1 (libraries/cli-extended/docs/PROGRAM-2026-10-UNIFIED-ADOPTION.md).
Static oracles over the packaging inputs, plus a wheel-content oracle
(`check_wheel_contents`) exercised on synthetic wheels that each carry exactly
one defect. The REAL wheel is checked by running the same function over the
output of `cmru handler wheel-build --cwd nyxloom` (see the NYX-CLIX report).
"""
from __future__ import annotations

import builtins
import importlib
import re
import tomllib
import zipfile
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
REQUIREMENT = "cli-extended>=0.3.0"


def _code_lines(path: Path) -> str:
    """Non-comment text of a config file (a comment may legitimately name the path)."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def check_wheel_contents(wheel: Path) -> None:
    """The nyxloom wheel declares cli-extended as a dependency and carries NO
    copy of it. Raises ``RuntimeError`` on any violation."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise RuntimeError(f"{wheel.name}: expected one METADATA file, found {metadata_names}")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    vendored = [n for n in names if n.split("/", 1)[0] == "cli_extended"]
    if vendored:
        raise RuntimeError(f"{wheel.name} vendors cli_extended files: {vendored[:3]}")
    requires = [line.split(":", 1)[1].strip() for line in metadata.splitlines()
                if line.startswith("Requires-Dist:")]
    if REQUIREMENT not in requires:
        raise RuntimeError(f"{wheel.name} lacks Requires-Dist: {REQUIREMENT} ({requires})")


def _wheel(tmp_path: Path, *, requires=(REQUIREMENT,), extra=()) -> Path:
    path = tmp_path / "nyxloom-0-py3-none-any.whl"
    metadata = "Metadata-Version: 2.1\nName: nyxloom\nVersion: 0\n" + "".join(
        f"Requires-Dist: {r}\n" for r in requires)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("nyxloom/__init__.py", "")
        archive.writestr("nyxloom-0.dist-info/METADATA", metadata)
        for name in extra:
            archive.writestr(name, "")
    return path


# --- pyproject.toml ---------------------------------------------------------

def test_pyproject_declares_cli_extended_with_the_documented_floor():
    assert REQUIREMENT in PYPROJECT["project"]["dependencies"]
    text = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    declaration = text.index(f'"{REQUIREMENT}"')
    # The floor carries its reason (estate version policy), right at the declaration.
    assert "Floor 0.3.0" in text[:declaration] and "CX-D1" in text[:declaration]


def test_pyproject_vendors_no_cli_extended_source_in_any_packaging_config():
    code = _code_lines(PROJECT / "pyproject.toml")
    assert "cli-extended/src" not in code
    assert "cli_extended" not in code
    setuptools = PYPROJECT["tool"]["setuptools"]
    assert setuptools["packages"]["find"]["where"] == ["src"]
    assert setuptools["packages"]["find"]["include"] == ["nyxloom*"]
    assert setuptools["package-dir"] == {"": "src"}


def test_legacy_upgrade_repair_is_ordered_and_documented_across_user_docs():
    readme = (PROJECT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (PROJECT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    repair = consumers.index("## Upgrade from Nyxloom 0.10.0")
    nyxloom_install = consumers.index("pip install ./nyxloom/artifacts/nyxloom-v0.10.1", repair)
    cli_extended_reinstall = consumers.index(
        "pip install --force-reinstall ./libraries/cli-extended/artifacts/cli-extended-v0.4.0",
        repair,
    )
    assert nyxloom_install < cli_extended_reinstall
    assert "wheel ownership rationale" in readme
    assert "#the-cli-extended-wheel-ownership-boundary" in readme
    assert "#the-cli-extended-wheel-ownership-boundary" in consumers
    assert "does not track shared-file ownership" in design
    release_notes = tomllib.loads((PROJECT / "cmru.toml").read_text(encoding="utf-8"))[
        "env"
    ]["NYXLOOM_RELEASE_NOTES"]
    assert "pipx install --preinstall ./cli_extended-*.whl nyxloom-*.whl" in release_notes
    assert "pipx runpip nyxloom install --force-reinstall ./cli_extended-*.whl" in release_notes


def test_missing_cli_extended_import_explains_the_safe_repair(monkeypatch):
    from nyxloom import cli_registry

    real_import = builtins.__import__

    def missing_cli_extended(name, *args, **kwargs):
        if name == "cli_extended":
            raise ImportError("cannot import name 'ArgumentSpec'", name=name)
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", missing_cli_extended)
        with pytest.raises(
            ImportError,
            match="official cli-extended wheel after upgrading Nyxloom",
        ) as exc:
            importlib.reload(cli_registry)
        assert "--force-reinstall" in str(exc.value)
        assert isinstance(exc.value.__cause__, ImportError)

    # reload left the module partially initialized at the failed import; restore
    # its normal definitions for any tests that hold references to it.
    importlib.reload(cli_registry)


# --- gate lanes -------------------------------------------------------------

@pytest.mark.parametrize("relative", ["assay.toml", "run-gate.toml", "cmru.toml"])
def test_no_gate_config_puts_cli_extended_source_on_a_path(relative):
    """CX-D1/CX-D3: lanes import the cli_extended installed in the lane image
    (the released wheel); the library checkout is never a source root."""
    code = _code_lines(PROJECT / relative)
    assert "cli-extended/src" not in code and "cli_extended/src" not in code, relative
    assert not re.search(r"""["']/?cli[-_]extended/?["']""", code), relative


def test_every_gate_pythonpath_is_the_project_source_only():
    found = re.findall(r"PYTHONPATH\s*=\s*\"?([^\s\"',}]+)", _code_lines(PROJECT / "assay.toml"))
    assert found
    assert set(found) == {"src"}, found


# --- the wheel-content oracle itself ----------------------------------------

def test_a_clean_wheel_passes(tmp_path):
    check_wheel_contents(_wheel(tmp_path))


def test_a_wheel_that_vendors_cli_extended_fails(tmp_path):
    with pytest.raises(RuntimeError, match="vendors cli_extended"):
        check_wheel_contents(_wheel(tmp_path, extra=["cli_extended/__init__.py"]))


@pytest.mark.parametrize("requires", [(), ("cli-extended>=0.1.0",), ("cli-extended",)])
def test_a_wheel_without_the_floor_fails(tmp_path, requires):
    with pytest.raises(RuntimeError, match="lacks Requires-Dist"):
        check_wheel_contents(_wheel(tmp_path, requires=requires))


def test_a_wheel_without_exactly_one_metadata_file_fails(tmp_path):
    path = tmp_path / "bad.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("nyxloom/__init__.py", "")
    with pytest.raises(RuntimeError, match="expected one METADATA"):
        check_wheel_contents(path)
