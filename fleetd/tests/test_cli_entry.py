"""The entry-point boundary: one actionable line when cli-extended is missing.

``fleet``'s console script is ``fleetd.entry:main``, which imports only the standard
library before it touches the registered CLI. On a managed host that is the difference
between one install instruction and a ``ModuleNotFoundError`` traceback, so the mapping
is pinned here rather than left to the import system: *missing declared dependency* is
exit 3 with the documented message, and *any other* missing module is still a real error
that must surface (a vendor fallback or a swallowed import would hide a broken install).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fleetd import entry
from fleetd.cli import main

PROJECT = Path(__file__).resolve().parents[1]


class _CLI:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def main(self, argv: object) -> int:
        self.calls.append(argv)
        return 0


def test_a_missing_cli_extended_is_one_actionable_line_and_exit_three(capsys, monkeypatch):
    def importer(name: str):
        raise ModuleNotFoundError("No module named 'cli_extended'", name="cli_extended")

    assert entry.main(["--version"], importer=importer) == entry.EXIT_MISSING_DEPENDENCY
    # The STATUS is part of the contract: 3, never the boundary's generic 1.
    assert entry.EXIT_MISSING_DEPENDENCY == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"fleet: ERROR {entry.MISSING_DEPENDENCY}\n"
    # The message names the supported install path, never a path hack.
    assert "fleetd wheel" in entry.MISSING_DEPENDENCY
    assert "cli-extended is not installed" in entry.MISSING_DEPENDENCY
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert "PYTHONPATH" not in entry.MISSING_DEPENDENCY


def test_a_different_missing_module_is_not_reported_as_the_cli_dependency():
    """Two conditions, two outcomes: only the declared dependency gets the install hint."""

    def importer(name: str):
        raise ModuleNotFoundError("No module named 'fleetd.other'", name="fleetd.other")

    with pytest.raises(ModuleNotFoundError, match="fleetd.other"):
        entry.main([], importer=importer)


def test_a_module_without_a_name_attribute_still_raises():
    """An unnamed ``ModuleNotFoundError`` cannot be attributed, so it is not swallowed."""

    def importer(name: str):
        raise ModuleNotFoundError("no module")

    with pytest.raises(ModuleNotFoundError, match="no module"):
        entry.main([], importer=importer)


def test_the_cli_is_invoked_with_the_given_argv_and_its_status_forwarded():
    cli = _CLI()

    def importer(name: str):
        assert name == "fleetd.cli"
        return cli

    assert entry.main(["--version"], importer=importer) == 0
    assert cli.calls == [["--version"]]


def test_no_argv_is_forwarded_as_none_so_the_cli_reads_sys_argv():
    cli = _CLI()

    def importer(name: str):
        return cli

    assert entry.main(importer=importer) == 0
    assert cli.calls == [None]


def test_the_real_import_path_reports_a_version_line(capsys):
    """Without an importer override the entry point runs the registered CLI."""

    assert entry.main(["--version"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("fleet ")
    assert captured.err == ""


def test_entry_module_imports_only_the_standard_library():
    """The guard exists only if nothing heavy is imported before it."""

    import subprocess

    code = (
        "import sys, fleetd.entry;"
        "print(sorted(m for m in sys.modules if m.startswith('cli_extended')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=True,
        env={"PYTHONPATH": str(PROJECT / "src"), "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    assert result.stdout.strip() == "[]"


def test_the_module_entry_point_gets_the_same_guard_as_the_console_script(monkeypatch, capsys):
    """`python -m fleetd` on a host without cli-extended must not end in a traceback either."""

    import runpy

    monkeypatch.setitem(sys.modules, "cli_extended", None)
    monkeypatch.delitem(sys.modules, "fleetd.cli", raising=False)
    monkeypatch.setattr(sys, "argv", ["fleetd", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("fleetd.__main__", run_name="__main__")
    assert exit_info.value.code == entry.EXIT_MISSING_DEPENDENCY
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cli-extended is not installed" in captured.err


def test_the_console_script_body_is_the_entry_point_not_the_cli_module():
    """A console script pointing at ``fleetd.cli:main`` would defeat the guard."""

    pyproject = (PROJECT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'fleet = "fleetd.entry:main"' in pyproject
    assert main is not entry.main


def test_a_source_checkout_without_installed_metadata_refuses_instead_of_inventing_a_version():
    """No literal fallback: with no distribution metadata the CLI exits 3 and names the fix."""

    import subprocess

    code = "import sys; from fleetd.cli import main; print('STATUS', main(['--version']))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=False,
        env={"PYTHONPATH": str(PROJECT / "src"), "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "STATUS 3" in result.stdout, result.stdout
    assert "cannot determine the fleetd version" in result.stderr, result.stderr
    # Nothing was printed as a version: an invented literal would appear on stdout.
    assert result.stdout.strip() == "STATUS 3"


def test_the_package_version_comes_from_the_resolved_identity():
    """``fleetd.__version__`` has one source, ``CliIdentity.resolve``, never a literal.

    Two checks, because both are the failure: the value must come from the resolver, and
    the module must not carry a stored ``__version__`` that could go stale between a tag
    and the next commit.
    """

    import fleetd
    from fleetd import cli as cli_module

    assert "__version__" not in vars(fleetd), "a stored literal would outlive a tag"
    assert fleetd.__version__ == cli_module.identity().version
    with pytest.raises(AttributeError, match="has no attribute"):
        fleetd.some_unknown_attribute
    assert "some_unknown_attribute" not in vars(fleetd)


def test_the_package_version_follows_the_identity_resolver(monkeypatch):
    """A hard-coded literal would survive the equality check while the resolver moved.

    ``__version__`` must be *derived*: when the resolver reports another version (a
    checkout whose metadata says something else), the package attribute moves with it.
    """

    from cli_extended import CliIdentity

    import fleetd
    from fleetd import cli as cli_module

    moved = CliIdentity("FLEET", "9.9.9", cli_module.LONG_NAME, cli_module.COMMAND)
    monkeypatch.setattr(cli_module, "identity", lambda: moved)
    assert fleetd.__version__ == "9.9.9"
