"""``assay capabilities --json``: the closed, versioned consumer contract."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from assay import __version__
from assay.cli import main
from assay.verdict import VERDICT_SCHEMA_VERSION


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _capabilities() -> tuple[dict, str]:
    code, out, err = _run(["capabilities", "--json"])
    assert code == 0
    assert err == ""
    return json.loads(out), out


def test_document_is_closed_and_has_pinned_schema(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    before = list(tmp_path.iterdir())

    document, _ = _capabilities()

    assert list(tmp_path.iterdir()) == before
    assert list(document) == [
        "assay_version",
        "commands",
        "mutation_store_policies",
        "schema",
        "verdict_schema_version",
    ]
    assert document["schema"] == "assay-capabilities/1"
    assert document["assay_version"] == __version__
    assert document["verdict_schema_version"] == VERDICT_SCHEMA_VERSION
    assert list(document["commands"]) == [
        "analyze campaign",
        "lanes",
        "plan",
        "run",
        "verify",
    ]
    assert all(
        flags == sorted(flags) for flags in document["commands"].values()
    )


def test_run_flags_match_help_and_policy_flags_are_real(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    document, _ = _capabilities()

    help_out = io.StringIO()
    help_err = io.StringIO()
    with (
        redirect_stdout(help_out),
        redirect_stderr(help_err),
        pytest.raises(SystemExit) as exited,
    ):
        main(["run", "--help"])
    assert exited.value.code == 0
    assert help_err.getvalue() == ""
    assert all(
        flag in help_out.getvalue() for flag in document["commands"]["run"]
    )
    assert set(document["mutation_store_policies"]) <= set(
        document["commands"]["run"]
    )
    assert document["mutation_store_policies"] == {
        "--carry-over": "import-unverified",
        "--merge-shard-store": "strict-shard",
        "--merge-state": "same-judge",
        "--resume": "same-judge",
        "--reuse-from": "hint",
    }


def test_output_is_byte_identical_across_calls():
    first = _run(["capabilities", "--json"])
    second = _run(["capabilities", "--json"])
    default = _run(["capabilities"])

    assert first == second
    assert default == first
