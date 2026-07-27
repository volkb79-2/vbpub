"""Regression tests for report CLI numeric boundary validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.cli import _main_report


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--stability-cov", "nan", "invalid --stability-cov — must be a finite non-negative number"),
        ("--stability-cov", "inf", "invalid --stability-cov — must be a finite non-negative number"),
        ("--stability-cov", "-0.1", "invalid --stability-cov — must be a finite non-negative number"),
        ("--min-frames", "0", "invalid --min-frames — must be a positive integer"),
        ("--min-frames", "-1", "invalid --min-frames — must be a positive integer"),
    ],
)
def test_report_rejects_nonfinite_negative_and_nonpositive_numeric_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    option: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        "topos.report.compute_report_with_selection",
        lambda *_args, **_kwargs: pytest.fail("numeric validation must precede report loading"),
    )

    assert _main_report([option, value, str(Path("/synthetic/recording.jsonl"))]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == message + "\n"
