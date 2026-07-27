"""Boundary tests for the report CLI's validation, rendering, and exits."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.cli import _main_report


REPORT_PATH = Path("/synthetic/recording.jsonl")


def _computation() -> SimpleNamespace:
    return SimpleNamespace(profiles=["PROFILE SENTINEL"], window_selection="WINDOW SENTINEL")


def test_report_returns_argparse_usage_exit_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _main_report(["--not-a-report-option", str(REPORT_PATH)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: --not-a-report-option" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        (
            "--stability-cov",
            "not-a-number",
            "invalid --stability-cov — must be a finite non-negative number",
        ),
        (
            "--min-frames",
            "not-an-integer",
            "invalid --min-frames — must be a positive integer",
        ),
    ],
)
def test_report_rejects_invalid_numeric_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    option: str,
    value: str,
    message: str,
) -> None:
    def unexpected_compute(*args: object, **kwargs: object) -> object:
        raise AssertionError("numeric validation must precede report loading")

    monkeypatch.setattr("topos.report.compute_report_with_selection", unexpected_compute)

    assert _main_report([option, value, str(REPORT_PATH)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == message + "\n"


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError(), f"file not found: {REPORT_PATH}"),
        (RuntimeError("cannot read compressed recording without zstandard"),
         "cannot read compressed recording without zstandard"),
        (RuntimeError("corrupt or truncated compressed recording"),
         "corrupt or truncated compressed recording"),
        (ValueError(f"recording contains no frames: {REPORT_PATH}"),
         f"recording contains no frames: {REPORT_PATH}"),
        (OSError(5, "synthetic disk failure"),
         f"error reading {REPORT_PATH}: synthetic disk failure"),
        (LookupError("synthetic reader failure"),
         f"unexpected error reading {REPORT_PATH}"),
    ],
)
def test_report_bounds_every_typed_reader_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
    message: str,
) -> None:
    def failed_compute(*args: object, **kwargs: object) -> object:
        raise failure

    monkeypatch.setattr("topos.report.compute_report_with_selection", failed_compute)

    assert _main_report([str(REPORT_PATH)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == message + "\n"
    assert "Traceback" not in captured.err


def test_report_rejects_malformed_assertion_spec_at_cli_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("topos.report.compute_report_with_selection", lambda *a, **k: _computation())

    assert _main_report(["--assert", "broken-spec", str(REPORT_PATH)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "invalid --assert spec 'broken-spec' — expected "
        "GROUP:METRIC:STAT<=VALUE or GROUP:METRIC:STAT>=VALUE\n"
    )


@pytest.mark.parametrize(
    ("json_output", "expected", "expected_code"),
    [
        (True, "JSON SENTINEL", 0),
        (False, "TEXT SENTINEL", 0),
    ],
)
def test_report_uses_the_selected_public_renderer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_output: bool,
    expected: str,
    expected_code: int,
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr("topos.report.compute_report_with_selection", lambda *a, **k: _computation())
    monkeypatch.setattr(
        "topos.report.format_report",
        lambda profiles, **kwargs: calls.append(("json", (profiles, kwargs))) or "JSON SENTINEL",
    )
    monkeypatch.setattr(
        "topos.report.report_to_jsonable",
        lambda profiles, **kwargs: calls.append(("jsonable", (profiles, kwargs))) or "JSONABLE SENTINEL",
    )
    monkeypatch.setattr(
        "topos.render.render_report",
        lambda value: calls.append(("text", value)) or "TEXT SENTINEL",
    )

    argv = [str(REPORT_PATH)] + (["--json"] if json_output else [])
    assert _main_report(argv) == expected_code
    captured = capsys.readouterr()
    assert captured.out == expected + "\n"
    assert captured.err == ""
    if json_output:
        assert calls == [("json", (["PROFILE SENTINEL"], {"assertions": None, "window_selection": "WINDOW SENTINEL"}))]
    else:
        assert calls == [
            ("jsonable", (["PROFILE SENTINEL"], {"assertions": None, "window_selection": "WINDOW SENTINEL"})),
            ("text", "JSONABLE SENTINEL"),
        ]


@pytest.mark.parametrize(
    ("passed", "expected_code"),
    [(True, 0), (False, 1)],
)
def test_report_maps_assertion_pass_and_breach_to_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    passed: bool,
    expected_code: int,
) -> None:
    result = SimpleNamespace(passed=passed)
    calls: list[object] = []
    monkeypatch.setattr("topos.report.compute_report_with_selection", lambda *a, **k: _computation())
    monkeypatch.setattr("topos.report.parse_assert_spec", lambda spec: "ASSERTION SENTINEL")
    monkeypatch.setattr(
        "topos.report.evaluate_assertions",
        lambda profiles, assertions: calls.append((profiles, assertions)) or [result],
    )
    monkeypatch.setattr(
        "topos.report.format_report",
        lambda profiles, **kwargs: calls.append((profiles, kwargs)) or "ASSERTION OUTPUT",
    )

    assert _main_report(["--json", "--assert", "group:metric:p95<=1", str(REPORT_PATH)]) == expected_code
    captured = capsys.readouterr()
    assert captured.out == "ASSERTION OUTPUT\n"
    assert captured.err == ""
    assert calls == [
        (["PROFILE SENTINEL"], ["ASSERTION SENTINEL"]),
        (
            ["PROFILE SENTINEL"],
            {"assertions": [result], "window_selection": "WINDOW SENTINEL"},
        ),
    ]
