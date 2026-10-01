"""The ``assay analyze`` parser: headline, exit codes and the report failure shape."""

from __future__ import annotations

import io
import json

import pytest

from assay.cli import cli_headline
from assay_analysis import cli as analysis_cli


def test_nested_help_and_missing_argument_start_with_the_headline(capsys):
    parser = analysis_cli.build_parser()
    with pytest.raises(SystemExit) as help_exit:
        parser.parse_args(["collect", "--help"])
    help_capture = capsys.readouterr()
    assert help_exit.value.code == 0
    assert help_capture.err == ""
    assert help_capture.out.splitlines()[0] == cli_headline()

    with pytest.raises(SystemExit) as error_exit:
        parser.parse_args(["collect"])
    error_capture = capsys.readouterr()
    assert error_exit.value.code == 2
    assert error_capture.out == ""
    assert error_capture.err.splitlines()[0] == cli_headline()
    assert "assay analyze collect: error" in error_capture.err


def test_report_of_a_missing_verdict_exits_two_with_an_evidence_error():
    out, err = io.StringIO(), io.StringIO()
    code = analysis_cli.main(
        ["report", "--expected-commit", "a" * 40, "--verdict", "l", "/nonexistent"],
        stdout=out, stderr=err)
    assert code == 2
    document = json.loads(out.getvalue())
    assert [lane["status"] for lane in document["lanes"]] == ["evidence_error"]
    assert document["exit_code"] == 2 and err.getvalue() == ""
