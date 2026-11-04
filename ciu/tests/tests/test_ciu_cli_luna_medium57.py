"""Focused coverage for CIU CLI entry, help, and version behavior."""

import runpy
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli
from ciu.cli_utils import get_cli_version
from ciu.cli_utils import cli_headline


@pytest.mark.parametrize("spelling", ["version", "--version"])
def test_main_version_prints_version_and_exits_zero(monkeypatch, capsys, spelling):
    monkeypatch.setattr(sys, "argv", ["ciu", spelling])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert captured.out == f"ciu {get_cli_version()}\n"
    assert captured.err == ""


def test_unknown_verb_is_a_usage_error_with_the_headline(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ciu", "nonexistent-verb"])
    with pytest.raises(SystemExit) as exc:
        cli.main()

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert captured.out == ""
    assert "nonexistent-verb" in captured.err
    assert cli_headline() in captured.err
    assert f"CIU {get_cli_version()}" in captured.err


def test_nested_parser_diagnostics_carry_the_headline(monkeypatch, capsys):
    """The library leads with the diagnostic, then the identity headline and the usage."""
    with pytest.raises(SystemExit) as exc_info:
        monkeypatch.setattr(sys, "argv", ["ciu", "worktree", "create"])
        cli.main()
    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert captured.out == ""
    assert captured.err.splitlines()[0].startswith("[ERROR]")
    assert cli_headline() in captured.err.splitlines()

    with pytest.raises(SystemExit) as help_info:
        monkeypatch.setattr(sys, "argv", ["ciu", "worktree", "create", "--help"])
        cli.main()
    help_capture = capsys.readouterr()
    assert help_info.value.code == 0
    assert help_capture.err == ""
    assert help_capture.out.splitlines()[0] == cli_headline()


def test_module_entry_version_matches_main(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ciu", "version"])

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"'ciu\.cli' found in sys\.modules",
            category=RuntimeWarning,
        )
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("ciu.cli", run_name="__main__")

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"ciu {get_cli_version()}"
