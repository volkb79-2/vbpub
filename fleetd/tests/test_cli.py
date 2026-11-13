import runpy
import sys
from pathlib import Path

import pytest

from fleetd.cli import main


FIXTURES = Path(__file__).parent / "fixtures"


def test_facts_validate_cli_reports_valid_record(capsys):
    status = main(["facts", "validate", str(FIXTURES / "host-facts.valid.json")])
    assert status == 0
    assert capsys.readouterr().out == "valid host-facts/1\n"


def test_guard_validate_cli_reports_valid_file(capsys):
    status = main(["guard", "validate", str(FIXTURES / "guard.valid.toml")])
    assert status == 0
    assert capsys.readouterr().out == "valid guard.toml\n"


@pytest.mark.parametrize(
    "argv",
    [
        ["facts", "validate", str(FIXTURES / "work-request.valid.json")],
        ["facts", "validate", str(FIXTURES / "missing.json")],
        ["guard", "validate", str(FIXTURES / "missing.toml")],
        ["guard", "validate", str(FIXTURES / "work-request.valid.json")],
    ],
)
def test_cli_validation_errors_are_reported_with_nonzero_status(argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith("fleet: ERROR ")


def test_version_exit_and_module_entrypoint(monkeypatch):
    with pytest.raises(SystemExit, match="0"):
        main(["--version"])
    monkeypatch.setattr(sys, "argv", ["fleet", "--version"])
    with pytest.raises(SystemExit, match="0"):
        runpy.run_module("fleetd.__main__", run_name="__main__")
