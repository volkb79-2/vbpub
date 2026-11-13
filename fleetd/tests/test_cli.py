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


def test_non_utf8_guard_file_and_oversized_integers_are_input_errors_not_tracebacks(tmp_path, capsys):
    binary = tmp_path / "guard.toml"
    binary.write_bytes(b"\xff\xfe")
    assert main(["guard", "validate", str(binary)]) == 2
    assert capsys.readouterr().err.startswith("fleet: ERROR ")

    # A candidate memory figure too large for float arithmetic is refused with exit 2.
    candidates = tmp_path / "candidates.json"
    candidates.write_text(
        '{"schema":"candidate-table/1","candidates":[{"id":"x","file":"a.py","measured_memory_bytes":1'
        + "0" * 400 + '}]}',
        encoding="utf-8",
    )
    demand = tmp_path / "run-gate.toml"
    demand.write_text(
        "[lanes.r2.demand]\nunit='candidate'\ninstance_memory='250m'\nunit_memory='120m'\n"
        "unit_seconds_ref=95\nefficient_upper_bound=2\n",
        encoding="utf-8",
    )
    status = main([
        "plan", "r2", "--candidates", str(candidates), "--hosts", str(FIXTURES / "fleet-host-set.valid.json"),
        "--demand", str(demand),
    ])
    assert status == 2
    assert capsys.readouterr().err.startswith("fleet: ERROR ")


def test_version_exit_and_module_entrypoint(monkeypatch):
    with pytest.raises(SystemExit, match="0"):
        main(["--version"])
    monkeypatch.setattr(sys, "argv", ["fleet", "--version"])
    with pytest.raises(SystemExit, match="0"):
        runpy.run_module("fleetd.__main__", run_name="__main__")
