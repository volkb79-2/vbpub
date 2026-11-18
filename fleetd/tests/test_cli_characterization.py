"""Contract tests for the ``fleet`` command line, written before the cli-extended migration.

These pin what a consumer of ``fleet`` may rely on: the subcommand and action
names, the flag spellings, the exit codes, the stdout/stderr split, and the
JSON documents a successful command writes. They are deliberately blind to how
the parser is built and to the exact wording of an error line, so the same
tests pass for the hand-written argparse CLI and for the registered
cli-extended one (see test_cli_adoption.py for the adopted surface itself).

The campaign supervisor and the ``campaign-contract`` lane depend on these,
not on help text.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

import fleetd
from fleetd.cli import main
from fleetd.transport import FakeTransport
from test_campaign import _plan

FIXTURES = Path(__file__).parent / "fixtures"
VERSION = fleetd.__version__


def run(argv):
    """Run the CLI the way a caller does: its exit status, stdout and stderr.

    ``main`` may raise ``SystemExit`` (the hand-written argparse parser does for
    ``--version`` and every parse error) or return the status (the registered
    cli-extended boundary does). A caller sees the same process status either
    way, so this helper reads both as one status.
    """

    try:
        return main(argv)
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)


def _demand(tmp_path: Path) -> Path:
    path = tmp_path / "demand.toml"
    path.write_text(
        "schema_version = 1\n\n[lanes.r2.demand]\nunit='candidate'\ninstance_memory='250m'\n"
        "unit_memory='120m'\nunit_seconds_ref=95\nefficient_upper_bound=2\n",
        encoding="utf-8",
    )
    return path


def _plan_inputs(tmp_path: Path) -> list[str]:
    return [
        "plan", "r2",
        "--candidates", str(FIXTURES / "candidate-table.valid.json"),
        "--hosts", str(FIXTURES / "fleet-host-set.valid.json"),
        "--demand", str(_demand(tmp_path)),
    ]


def test_version_is_one_line_on_stdout(capsys):
    status = run(["--version"])
    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == f"fleet {VERSION}\n"
    assert captured.err == ""


def test_module_entrypoint_prints_the_same_version_line(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fleet", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("fleetd.__main__", run_name="__main__")
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"fleet {VERSION}\n"


@pytest.mark.parametrize(
    "argv, marker",
    [
        # Every documented option spelling still reaches its handler: the failure
        # below is the handler's own domain error, never a parse error.
        (["facts", "collect", "--root", "@{root}/absent"], "required config"),
        (["facts", "validate", "@{root}/absent.json"], "absent.json"),
        (["guard", "validate", "@{root}/absent.toml"], "absent.toml"),
        (
            [
                "plan", "r2", "--candidates", "@{root}/absent.json",
                "--hosts", "@{root}/absent.json", "--demand", "@{root}/absent.toml",
            ],
            "absent.json",
        ),
        (["run", "@{root}/absent.json", "--transport", "hx", "--campaign", "c"], "absent.json"),
        (["status", "c", "--transport", "hx", "--state-dir", "@{root}/state"], "campaign state does not exist"),
        (["tick", "c", "--transport", "hx", "--state-dir", "@{root}/state"], "campaign state does not exist"),
        (["collect", "c", "--transport", "hx", "--state-dir", "@{root}/state"], "campaign state does not exist"),
    ],
)
def test_domain_errors_prove_every_flag_spelling_was_accepted(argv, marker, capsys, tmp_path):
    resolved = [token.replace("@{root}", str(tmp_path)) for token in argv]
    assert run(resolved) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert marker in captured.err


def test_unknown_verb_and_unknown_flag_are_input_errors(capsys):
    for argv in (["bogus"], ["plan", "r2", "--nonsuch"], ["facts", "bogus"], ["status"]):
        assert run(argv) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()


def test_facts_collect_writes_host_facts_json_and_nothing_else(capsys):
    assert run(["facts", "collect", "--root", str(FIXTURES / "facts-root")]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    document = json.loads(captured.out)
    assert document["schema"] == "host-facts/1"


def test_facts_collect_without_a_readable_config_is_exit_two_on_stderr_only(capsys, tmp_path):
    assert run(["facts", "collect", "--root", str(tmp_path / "missing")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "required config" in captured.err


def test_facts_validate_reports_the_document_kind_on_stdout(capsys):
    assert run(["facts", "validate", str(FIXTURES / "host-facts.valid.json")]) == 0
    captured = capsys.readouterr()
    assert captured.out == "valid host-facts/1\n"
    assert captured.err == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["facts", "validate", str(FIXTURES / "missing.json")],
        ["facts", "validate", str(FIXTURES / "work-request.valid.json")],
    ],
)
def test_facts_validate_errors_keep_exit_two_and_an_empty_stdout(argv, capsys):
    assert run(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()


def test_guard_validate_reports_the_document_kind_on_stdout(capsys):
    assert run(["guard", "validate", str(FIXTURES / "guard.valid.toml")]) == 0
    captured = capsys.readouterr()
    assert captured.out == "valid guard.toml\n"
    assert captured.err == ""


def test_guard_validate_errors_keep_exit_two_and_an_empty_stdout(capsys, tmp_path):
    assert run(["guard", "validate", str(tmp_path / "missing.toml")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip()


def test_plan_writes_fleet_plan_json(capsys, tmp_path):
    assert run(_plan_inputs(tmp_path)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    plan = json.loads(captured.out)
    assert plan["schema"] == "fleet-plan/1"
    assert plan["lane"] == "r2"


def test_plan_commands_selector_prints_a_shell_script(capsys, tmp_path):
    assert run(_plan_inputs(tmp_path) + ["--commands"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.startswith("set -euo pipefail\n")
    assert "--only-candidates" in captured.out


def test_plan_batch_multiplier_keeps_its_spelling_and_input_error(capsys, tmp_path):
    assert run(_plan_inputs(tmp_path) + ["--batch-multiplier", "0"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "batch_multiplier" in captured.err


def test_campaign_commands_keep_their_json_schemas_and_exit_codes(monkeypatch, capsys, tmp_path):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(2)), encoding="utf-8")
    state_dir = tmp_path / "state"
    monkeypatch.setattr("fleetd.cli.HxTransport", lambda *_args, **_kwargs: FakeTransport())

    assert run(["run", str(plan_path), "--transport", "hx", "--campaign", "c", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "fleet-campaign-status/1"
    assert run(["status", "c", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "fleet-campaign-status/1"
    assert run(["tick", "c", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "fleet-campaign-status/1"
    assert run(["collect", "c", "--state-dir", str(state_dir)]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == "fleet-campaign-collection/1"


def test_campaign_commands_report_a_bad_plan_with_exit_two(capsys, tmp_path):
    plan = tmp_path / "invalid.json"
    plan.write_text("[]", encoding="utf-8")
    assert run(["run", str(plan), "--campaign", "bad", "--state-dir", str(tmp_path / "state")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "expected a JSON object" in captured.err


def test_a_missing_campaign_state_is_an_input_error(capsys, tmp_path):
    assert run(["status", "absent", "--state-dir", str(tmp_path / "state")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "campaign state does not exist" in captured.err


def test_help_lists_every_document_command_and_the_library_controls(capsys):
    """The help screen is the one place a caller learns the spelling; it must be complete."""

    assert run(["help"]) == 0
    text = capsys.readouterr().out
    for verb in ("facts", "guard", "plan", "run", "status", "tick", "collect", "skills"):
        assert verb in text, verb
    assert "fleet" in text
    assert "--version" in text
    assert "--json" in text or "JSON" in text


def test_an_unknown_verb_names_the_verbs_and_exits_two(capsys):
    assert run(["nonesuch"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid choice" in captured.err
    for verb in ("facts", "plan", "run", "skills"):
        assert verb in captured.err, verb


def test_help_for_one_verb_shows_its_own_options(capsys):
    assert run(["help", "plan"]) == 0
    text = capsys.readouterr().out
    for flag in ("--candidates", "--hosts", "--demand", "--commands", "--batch-multiplier"):
        assert flag in text, flag
    assert "--campaign" not in text
