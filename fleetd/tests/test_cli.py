import runpy
import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from fleetd.cli import main
from fleetd import cli
from fleetd.contracts import ContractError
from fleetd.transport import FakeTransport
from test_campaign import _plan


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
        '{"schema":"candidate-table/1","candidates":[{"id":"' + "a" * 64 + '","file":"a.py","measured_memory_bytes":1'
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


def test_plan_execution_context_requires_a_clean_run_gate_project(monkeypatch, tmp_path):
    project = tmp_path / "fleetd"
    project.mkdir()
    (project / "run-gate.toml").write_text("schema_version = 1\n", encoding="utf-8")
    values = {
        "--show-toplevel": str(tmp_path),
        "HEAD": "a" * 40,
        "--show-prefix": "fleetd/",
        "--porcelain=v1": "",
    }

    def git(argv, **_kwargs):
        key = "--porcelain=v1" if argv[1] == "status" else argv[-1]
        return subprocess.CompletedProcess(argv, 0, values[key], "")

    monkeypatch.setattr(cli.subprocess, "run", git)
    assert cli._execution_context() == {
        "schema": "fleet-plan-execution/1", "commit": "a" * 40, "project": "fleetd"
    }
    values["--porcelain=v1"] = " M tracked.py"
    assert cli._execution_context() is None
    values["--porcelain=v1"] = ""
    project.joinpath("run-gate.toml").unlink()
    assert cli._execution_context() is None

    def git_error(*_args, **_kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr(cli.subprocess, "run", git_error)
    assert cli._execution_context() is None


def test_plan_cli_pins_clean_execution_context_and_rejects_unknown_transport(monkeypatch, tmp_path, capsys):
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps({
        "schema": "candidate-table/1",
        "candidates": [{"id": "a" * 64, "file": "src/a.py", "measured_memory_bytes": 100_000_000}],
    }), encoding="utf-8")
    demand = tmp_path / "demand.toml"
    demand.write_text(
        "[lanes.r2.demand]\nunit='candidate'\ninstance_memory='250m'\nunit_memory='120m'\n"
        "unit_seconds_ref=95\nefficient_upper_bound=2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_execution_context", lambda: {
        "schema": "fleet-plan-execution/1", "commit": "a" * 40, "project": "."
    })
    args = [
        "plan", "r2", "--candidates", str(candidates), "--hosts",
        str(FIXTURES / "fleet-host-set.valid.json"), "--demand", str(demand),
    ]
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["execution"]["commit"] == "a" * 40
    assert main([*args, "--commands"]) == 0
    assert "--only-candidates" in capsys.readouterr().out
    with pytest.raises(ContractError, match="only the identity-checked hx"):
        cli._manager(Namespace(transport="ssh"))

    monkeypatch.setattr(cli, "_execution_context", lambda: None)
    assert main(args) == 0
    assert "execution" not in json.loads(capsys.readouterr().out)


def test_fleet_campaign_cli_run_status_tick_collect_with_fake_transport(monkeypatch, tmp_path, capsys):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(2)), encoding="utf-8")
    state_dir = tmp_path / "state"
    transport = FakeTransport()
    monkeypatch.setattr(cli, "HxTransport", lambda: transport)

    assert main(["run", str(plan_path), "--transport", "hx", "--campaign", "cli-campaign", "--state-dir", str(state_dir)]) == 0
    run_output = json.loads(capsys.readouterr().out)
    assert run_output["schema"] == "fleet-campaign-status/1"
    assert main(["status", "cli-campaign", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["campaign"] == "cli-campaign"
    assert main(["tick", "cli-campaign", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["campaign"] == "cli-campaign"
    assert main(["collect", "cli-campaign", "--state-dir", str(state_dir)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema"] == "fleet-campaign-collection/1"
    assert Path(result["result"]).is_file()
    assert Path(result["report"]).is_file()


def test_campaign_cli_reports_invalid_plan_with_exit_two(tmp_path, capsys):
    plan = tmp_path / "invalid.json"
    plan.write_text("[]", encoding="utf-8")
    assert main(["run", str(plan), "--campaign", "bad", "--state-dir", str(tmp_path / "state")]) == 2
    assert "expected a JSON object" in capsys.readouterr().err
