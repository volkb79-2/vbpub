"""W2-PKG4 (KI-51): cli-extended is a real wheel dependency, never vendored.

Static oracles over the packaging inputs, plus the wheel-content oracle
(``tools/installed_wheel_smoke.check_wheel_contents``) run on synthetic wheels
that each carry exactly one defect. The real wheel build and offline install is
the ``installed-wheel`` gate lane (``tools/installed_wheel_smoke.py``).
"""
from __future__ import annotations

import importlib.util
import re
import tomllib
import zipfile
from pathlib import Path

import pytest

from tests._cx_paths import CX_LIBRARY

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parent


def _load_smoke():
    spec = importlib.util.spec_from_file_location(
        "installed_wheel_smoke", PROJECT / "tools" / "installed_wheel_smoke.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()
PYPROJECT = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))


def _code_lines(path: Path) -> str:
    """Non-comment text of a config/script file (a comment may name the path)."""
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


# --- pyproject.toml ---------------------------------------------------------

def test_pyproject_declares_cli_extended_with_the_documented_floor():
    assert PYPROJECT["project"]["dependencies"] == ["cli-extended>=0.3.0"]
    assert smoke.CLI_EXTENDED_REQUIREMENT == "cli-extended>=0.3.0"
    text = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    # The floor carries its reason (estate version policy), right at the declaration.
    declaration = text.index('dependencies = ["cli-extended>=0.3.0"]')
    assert "Floor 0.3.0" in text[:declaration] and "CX-D1" in text[:declaration]
    assert "CLI-EXT-26" in text[:declaration]


def test_pyproject_vendors_no_cli_extended_source_in_any_packaging_config():
    code = _code_lines(PROJECT / "pyproject.toml")
    assert "cli-extended/src" not in code and "cli_extended" not in code
    setuptools = PYPROJECT["tool"]["setuptools"]
    assert setuptools["packages"]["find"]["where"] == ["src", "../libraries/worktree/src"]
    assert "cli_extended" not in setuptools.get("package-dir", {})
    assert setuptools["package-dir"] == {"worktree": "../libraries/worktree/src/worktree"}


def test_skills_ship_as_package_data_and_the_checkout_skill_dir_is_gone():
    patterns = PYPROJECT["tool"]["setuptools"]["package-data"]["cmru"]
    assert "skills/*/*" in patterns and "skills/*/**/*" in patterns
    skill = PROJECT / "src" / "cmru" / "skills" / "cmru-cli" / "SKILL.md"
    assert skill.is_file()
    head = skill.read_text(encoding="utf-8").split("---", 2)[1]
    assert re.search(r"^name: cmru-cli$", head, re.MULTILINE)


def test_no_checkout_skill_tree_duplicates_the_packaged_skill():
    """AC-19 / W2-PKG5: the D-2 discovery symlink is retired. `cmru skills install`
    is the install path, so no `.claude/skills` entry in the project may carry a
    packaged skill's name (a symlink, a copy, or a dangling link)."""
    packaged = {
        path.name for path in (PROJECT / "src" / "cmru" / "skills").iterdir() if path.is_dir()
    }
    assert "cmru-cli" in packaged
    tree = PROJECT / ".claude" / "skills"
    present = {entry.name for entry in tree.iterdir()} if tree.exists() else set()
    assert not (packaged & present), packaged & present
    roots = PYPROJECT["tool"]["setuptools"]["packages"]["find"]["where"]
    assert all(not (PROJECT / root / ".claude").exists() for root in roots)


# --- gate/bootstrap configuration -------------------------------------------

@pytest.mark.parametrize("relative", [
    "run-gate.toml", "assay.toml", "cmru.toml",
    "tools/run_release_gate.py", "tools/project_fixture.py",
])
def test_no_gate_or_bootstrap_config_puts_cli_extended_source_on_a_path(relative):
    code = _code_lines(PROJECT / relative)
    assert CX_LIBRARY not in code, relative
    assert "cli-extended/src" not in code and "cli_extended/src" not in code, relative
    # The split path form (`REPOSITORY_ROOT / "libraries" / "cli-extended" / "src"`),
    # which the literal greps above cannot see: "cli-extended" as a path component.
    assert not re.search(r"""["']/?cli[-_]extended/?["']""", code), relative


def test_bootstrap_names_the_library_checkout_only_as_the_source_mode_build_input():
    """W3-ZERO Z1: the bootstrap's explicit `source` mode BUILDS a wheel from the
    library checkout (--source), but never puts that tree on an import path."""
    code = _code_lines(PROJECT / "build-initial-standalone.sh")
    assert "cli-extended/src" not in code and "cli_extended/src" not in code
    assert not re.search(r"""["']/?cli[-_]extended/?["']""", code)
    lines = [line for line in code.splitlines() if CX_LIBRARY in line]
    assert len(lines) == 1 and lines[0].lstrip().startswith("cli_extended_library="), lines
    # The library source reaches PYTHONPATH exactly once: the explicit source-mode build subshell.
    on_path = [line for line in code.splitlines() if "cli_extended_library}/src" in line]
    assert len(on_path) == 1 and "export PYTHONPATH" in on_path[0], on_path
    assert code.index(on_path[0]) > code.index('"${cli_extended_mode}" == "source" ]]; then\n    cli_extended_library')


def test_every_gate_pythonpath_keeps_the_worktree_root_only():
    for relative in ("run-gate.toml", "assay.toml", "cmru.toml"):
        found = re.findall(r"PYTHONPATH\s*=\s*\"?([^\s\"']+)", _code_lines(PROJECT / relative))
        assert found, relative
        assert set(found) == {"src:../libraries/worktree/src"}, (relative, found)


# --- the wheel-content oracle, on synthetic wheels ---------------------------

def _wheel(tmp_path: Path, *, requires=("cli-extended>=0.3.0",), extra=(), skill=True) -> Path:
    path = tmp_path / "cmru-1.0.0-py3-none-any.whl"
    members = {
        "cmru/__init__.py": "",
        "worktree/__init__.py": "",
        "cmru-1.0.0.dist-info/METADATA":
            "Metadata-Version: 2.1\nName: cmru\nVersion: 1.0.0\n"
            + "".join(f"Requires-Dist: {item}\n" for item in requires),
    }
    if skill:
        members["cmru/skills/cmru-cli/SKILL.md"] = "---\nname: cmru-cli\n---\n"
    members.update({name: "" for name in extra})
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return path


def test_wheel_oracle_accepts_the_right_shape(tmp_path):
    smoke.check_wheel_contents(_wheel(tmp_path))


def test_wheel_oracle_rejects_a_vendored_copy(tmp_path):
    wheel = _wheel(tmp_path, extra=("cli_extended/__init__.py", "cli_extended/core.py"))
    with pytest.raises(RuntimeError, match="vendors cli_extended"):
        smoke.check_wheel_contents(wheel)


def test_wheel_oracle_rejects_a_missing_requires_dist(tmp_path):
    with pytest.raises(RuntimeError, match="lacks Requires-Dist"):
        smoke.check_wheel_contents(_wheel(tmp_path, requires=()))
    # a different floor is a different declaration
    with pytest.raises(RuntimeError, match="lacks Requires-Dist"):
        smoke.check_wheel_contents(_wheel(tmp_path, requires=("cli-extended>=0.1.0",)))


def test_wheel_oracle_rejects_a_wheel_without_the_packaged_skill(tmp_path):
    with pytest.raises(RuntimeError, match="skill as package data"):
        smoke.check_wheel_contents(_wheel(tmp_path, skill=False))


def test_wheel_oracle_requires_exactly_one_metadata_file(tmp_path):
    path = tmp_path / "broken-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("broken/__init__.py", "")
    with pytest.raises(RuntimeError, match="expected one METADATA"):
        smoke.check_wheel_contents(path)
