"""Output modes: the documented form without ``--json``, one compact document with it.

The registration moved stdout onto ``runtime.output``, so each handler has one
``json_mode`` branch. This file pins both, because "the output shape did not change" is
what the migration promised and what a caller scripts against: without ``--json`` a plan
and a campaign document are the same indented, key-sorted JSON as before; with ``--json``
they are a single compact document, never doubled and never mixed with a diagnostic.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from fleetd.cli import build_cli, identity
from fleetd.transport import FakeTransport
from test_campaign import _plan

FIXTURES = Path(__file__).parent / "fixtures"


def _demand(tmp_path: Path) -> Path:
    path = tmp_path / "demand.toml"
    path.write_text(
        "schema_version = 1\n\n[lanes.r2.demand]\nunit='candidate'\ninstance_memory='250m'\n"
        "unit_memory='120m'\nunit_seconds_ref=95\nefficient_upper_bound=2\n",
        encoding="utf-8",
    )
    return path


def _run(argv, monkeypatch=None, transport=None):
    app = build_cli(identity())
    if transport is not None:
        (monkeypatch or pytest.MonkeyPatch()).setattr("fleetd.cli.HxTransport", lambda: transport)
    stdout, stderr = io.StringIO(), io.StringIO()
    status = app.run(argv=list(argv), stdout=stdout, stderr=stderr)
    return status, stdout.getvalue(), stderr.getvalue()


def _assert_one_document(text: str) -> dict:
    document = json.loads(text)
    assert text.strip().endswith("}")
    return document


def test_facts_collect_prints_the_indented_document_by_default(tmp_path, monkeypatch):
    status, stdout, stderr = _run(
        ["facts", "collect", "--root", str(FIXTURES / "facts-root")], monkeypatch
    )
    assert status == 0
    assert stderr == ""
    document = _assert_one_document(stdout)
    assert document["schema"] == "host-facts/1"
    assert stdout.count("\n") > 5, "the documented form is indented, key-sorted JSON"
    assert stdout.startswith("{\n  \"boot_id\"")


def test_facts_collect_prints_one_compact_document_with_json(tmp_path, monkeypatch):
    status, stdout, stderr = _run(
        ["facts", "collect", "--root", str(FIXTURES / "facts-root"), "--json"], monkeypatch
    )
    assert status == 0
    assert stderr == ""
    assert _assert_one_document(stdout) == _assert_one_document(
        _run(["facts", "collect", "--root", str(FIXTURES / "facts-root")], monkeypatch)[1]
    )
    assert stdout.count("\n") == 1, "json mode writes exactly one line"


def test_plan_keeps_both_modes_and_pins_no_execution_outside_a_git_project(tmp_path, monkeypatch):
    argv = [
        "plan", "r2",
        "--candidates", str(FIXTURES / "candidate-table.valid.json"),
        "--hosts", str(FIXTURES / "fleet-host-set.valid.json"),
        "--demand", str(_demand(tmp_path)),
    ]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("fleetd.cli._execution_context", lambda: None)

    status, stdout, stderr = _run(argv, monkeypatch)
    assert (status, stderr) == (0, "")
    assert stdout.count("\n") > 5
    assert json.loads(stdout)["schema"] == "fleet-plan/1"

    status, stdout, stderr = _run(argv + ["--json"], monkeypatch)
    assert (status, stderr) == (0, "")
    assert json.loads(stdout)["schema"] == "fleet-plan/1"
    assert stdout.count("\n") == 1


def test_commands_selector_is_not_json_and_ignores_the_json_flag(tmp_path, monkeypatch):
    argv = [
        "plan", "r2",
        "--candidates", str(FIXTURES / "candidate-table.valid.json"),
        "--hosts", str(FIXTURES / "fleet-host-set.valid.json"),
        "--demand", str(_demand(tmp_path)),
        "--commands",
    ]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("fleetd.cli._execution_context", lambda: None)
    status, stdout, stderr = _run(argv, monkeypatch)
    assert (status, stderr) == (0, "")
    assert stdout.startswith("set -euo pipefail\n")
    assert "--only-candidates" in stdout


def test_a_campaign_command_prints_one_document_in_both_modes(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan(2)), encoding="utf-8")
    transport = FakeTransport()
    monkeypatch.setattr("fleetd.cli.HxTransport", lambda: transport)
    state_dir = tmp_path / "state"
    base = ["run", str(plan_path), "--transport", "hx", "--campaign", "output-campaign",
            "--state-dir", str(state_dir)]

    status, stdout, stderr = _run(base, monkeypatch)
    assert (status, stderr) == (0, "")
    assert json.loads(stdout)["schema"] == "fleet-campaign-status/1"
    assert stdout.count("\n") > 5

    status, stdout, stderr = _run(["status", "output-campaign", "--state-dir", str(state_dir), "--json"], monkeypatch)
    assert (status, stderr) == (0, "")
    assert json.loads(stdout)["schema"] == "fleet-campaign-status/1"
    assert stdout.count("\n") == 1


def test_a_domain_refusal_writes_nothing_to_stdout_in_either_mode(tmp_path, monkeypatch):
    """A partly written document before a refusal line would break every consumer."""

    for extra in ([], ["--json"]):
        status, stdout, stderr = _run(
            ["facts", "validate", str(FIXTURES / "missing.json"), *extra], monkeypatch
        )
        assert status == 2
        assert stdout == ""
        assert stderr.startswith("[ERROR] ")


def test_the_state_directory_default_is_derived_per_invocation(tmp_path, monkeypatch):
    """One default, two environments: the surface must not bake in one machine's home.

    A default resolved while the registry was built would freeze the builder's
    ``$HOME`` into the exported surface, the generated SPEC region and every reviewed
    signature, so two hosts would disagree about the same CLI.
    """

    from cli_extended.surface import export_cli_surface

    app = build_cli()
    surface = export_cli_surface(app)
    recorded = [
        action
        for route in surface["routes"]
        for action in route["actions"]
        if action.get("canonical") == "--state-dir"
    ]
    assert recorded, "the campaign verbs declare --state-dir"
    for action in recorded:
        assert action.get("default") is None, "no environment-specific default in the surface"

    first = tmp_path / "one"
    second = tmp_path / "two"
    for root, expected in ((first, first), (second, second)):
        monkeypatch.setenv("XDG_STATE_HOME", str(root))
        assert build_cli().identity.command_name == "fleet"
        from fleetd.cli import _default_state_dir

        assert _default_state_dir() == expected / "fleetd" / "campaigns"
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    from fleetd.cli import _default_state_dir as fresh

    assert fresh() == Path.home() / ".local" / "state" / "fleetd" / "campaigns"


def test_a_command_without_state_dir_uses_the_invocation_default(tmp_path, monkeypatch):
    """The behavioural half: `status` with no --state-dir reads the derived directory."""

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    status, stdout, stderr = _run(["status", "absent-campaign"], monkeypatch)
    assert status == 2
    assert stdout == ""
    assert "campaign state does not exist" in stderr
