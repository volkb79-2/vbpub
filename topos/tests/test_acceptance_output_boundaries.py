"""Behavioral output-boundary tests for the acceptance CLI.

The command runners are deliberately replaced with complete result objects: the
tests exercise public parsing, formatter selection, stdout, and exit status
without collecting host state, launching the TUI, or opening an MCP session.
"""

from __future__ import annotations

import json

import pytest

import topos.acceptance as acceptance


def _measurements() -> dict[str, float]:
    return {"wall_s": 1.25, "user_s": 0.25, "sys_s": 0.05, "rss_kb": 4096.0}


def test_format_steady_text_reports_collection_and_threshold_failures() -> None:
    """A failed steady result keeps both failure classes visible to operators."""
    result = acceptance.SteadyResult(
        ok=False,
        version="1.2.3",
        python="3.13.0 test",
        platform="test-platform",
        samples_requested=3,
        samples_completed=1,
        measurements={
            **_measurements(),
            "avg_sample_wall_s": 1.25,
            "cpu_pct": 20.0,
        },
        entity_counts={"min": 2, "max": 4, "last": 3},
        collection_errors=["frame 2: collector disconnected"],
        threshold_errors=["CPU percent 20.00 exceeds 10.00"],
    )

    text = acceptance.format_steady_text(result)

    assert "Collection failures:" in text
    assert "[FAIL] frame 2: collector disconnected" in text
    assert "Threshold failures:" in text
    assert "[FAIL] CPU percent 20.00 exceeds 10.00" in text
    assert "SOME CHECKS FAILED  (exit code 1)" in text


@pytest.mark.parametrize(
    ("argv", "runner", "result", "expected_code", "assert_output"),
    [
        (
            ["steady", "--samples", "1", "--interval-s", "0"],
            "run_steady",
            acceptance.SteadyResult(
                ok=True,
                version="1.2.3",
                python="3.13.0 test",
                platform="test-platform",
                samples_requested=1,
                samples_completed=1,
                measurements={
                    **_measurements(),
                    "avg_sample_wall_s": 1.25,
                    "cpu_pct": 20.0,
                },
                entity_counts={"min": 2, "max": 2, "last": 2},
                collection_errors=[],
                threshold_errors=[],
            ),
            0,
            lambda output: "topos acceptance steady  v1.2.3" in output
            and "ALL CHECKS PASSED  (exit code 0)" in output,
        ),
        (
            ["tui-smoke", "--json"],
            "run_tui_smoke",
            acceptance.TuiSmokeResult(
                ok=True,
                exit_code=0,
                version="1.2.3",
                python="3.13.0 test",
                platform="test-platform",
                smoke_line="ui smoke ok frames=2 view=tree profile=ci",
                stdout_snippet="ui smoke ok frames=2 view=tree profile=ci\n",
                stderr_snippet="",
                frames=2,
                view="tree",
                profile="ci",
                measurements=_measurements(),
            ),
            0,
            lambda output: json.loads(output)["frames"] == 2,
        ),
        (
            ["mcp-smoke"],
            "run_mcp_smoke",
            acceptance.McpSmokeResult(
                ok=False,
                version="1.2.3",
                python="3.13.0 test",
                platform="test-platform",
                extra_installed=True,
                checks=[acceptance.Check("hello", False, "daemon unavailable")],
                max_response_bytes=None,
                measurements=_measurements(),
            ),
            1,
            lambda output: "[FAIL] hello: daemon unavailable" in output
            and "SOME CHECKS FAILED  (exit code 1)" in output,
        ),
    ],
)
def test_acceptance_main_selects_real_output_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    runner: str,
    result: object,
    expected_code: int,
    assert_output: object,
) -> None:
    """Each command exposes its selected text/JSON result and semantic status."""
    monkeypatch.setattr(acceptance, runner, lambda **_kwargs: result)

    assert acceptance.acceptance_main(argv) == expected_code

    captured = capsys.readouterr()
    assert captured.err == ""
    assert callable(assert_output)
    assert assert_output(captured.out)
