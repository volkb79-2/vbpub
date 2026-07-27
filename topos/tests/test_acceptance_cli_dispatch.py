"""Direct-boundary tests for acceptance CLI routing and result contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import topos.acceptance as acceptance


def _result(ok: bool = True) -> SimpleNamespace:
    return SimpleNamespace(ok=ok)


@pytest.mark.parametrize(
    ("argv", "runner", "runner_args", "json_flag", "json_formatter", "text_formatter"),
    [
        (
            ["smoke", "--cgroup-root", "/cg", "--replay", "/frame", "--pretty-json"],
            "run_smoke",
            {"cgroup_root": Path("/cg"), "replay_path": Path("/frame")},
            True,
            "format_json",
            "format_text",
        ),
        (
            [
                "steady", "--cgroup-root", "/cg", "--samples", "3", "--interval-s", "1.5",
                "--max-cpu-pct", "8.5", "--max-rss-kb", "1024", "--json",
            ],
            "run_steady",
            {
                "cgroup_root": Path("/cg"), "samples": 3, "interval_s": 1.5,
                "max_cpu_pct": 8.5, "max_rss_kb": 1024,
            },
            True,
            "format_steady_json",
            "format_steady_text",
        ),
        (
            [
                "tui-smoke", "--replay", "/frame", "--config", "/cfg", "--profile", "minimal",
                "--timeout-s", "4.5",
            ],
            "run_tui_smoke",
            {
                "replay_path": Path("/frame"), "config_path": Path("/cfg"),
                "profile": "minimal", "timeout_s": 4.5,
            },
            False,
            "format_tui_smoke_json",
            "format_tui_smoke_text",
        ),
        (
            ["mcp-smoke", "--socket", "/sock", "--timeout-s", "6", "--pretty-json"],
            "run_mcp_smoke",
            {"socket_path": Path("/sock"), "timeout_s": 6.0},
            True,
            "format_mcp_smoke_json",
            "format_mcp_smoke_text",
        ),
    ],
)
def test_acceptance_routes_public_options_and_selected_formatter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    runner: str,
    runner_args: dict[str, object],
    json_flag: bool,
    json_formatter: str,
    text_formatter: str,
) -> None:
    result = _result()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(acceptance, runner, lambda **kwargs: calls.append(("runner", kwargs)) or result)
    monkeypatch.setattr(
        acceptance,
        json_formatter,
        lambda value, *, pretty=False: calls.append(("json", (value, pretty))) or "JSON SENTINEL",
    )
    monkeypatch.setattr(
        acceptance,
        text_formatter,
        lambda value: calls.append(("text", value)) or "TEXT SENTINEL",
    )

    assert acceptance.acceptance_main(argv) == 0
    captured = capsys.readouterr()
    assert captured.out == ("JSON SENTINEL\n" if json_flag else "TEXT SENTINEL\n")
    assert captured.err == ""
    if json_flag:
        assert calls == [("runner", runner_args), ("json", (result, "--pretty-json" in argv))]
    else:
        assert calls == [("runner", runner_args), ("text", result)]


@pytest.mark.parametrize(
    ("argv", "runner"),
    [
        (["steady", "--samples", "0"], "run_steady"),
        (["steady", "--interval-s", "-0.1"], "run_steady"),
        (["steady", "--max-cpu-pct", "-1"], "run_steady"),
        (["steady", "--max-rss-kb", "0"], "run_steady"),
        (["tui-smoke", "--timeout-s", "0"], "run_tui_smoke"),
        (["mcp-smoke", "--timeout-s", "-1"], "run_mcp_smoke"),
    ],
)
def test_acceptance_rejects_invalid_numeric_options_before_runner(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    runner: str,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(acceptance, runner, lambda **kwargs: calls.append(kwargs))

    assert acceptance.acceptance_main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert calls == []


@pytest.mark.parametrize("ok, expected_code", [(True, 0), (False, 1)])
def test_acceptance_maps_result_ok_to_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ok: bool,
    expected_code: int,
) -> None:
    result = _result(ok)
    monkeypatch.setattr(acceptance, "run_smoke", lambda **kwargs: result)
    monkeypatch.setattr(acceptance, "format_text", lambda value: "RESULT SENTINEL")

    assert acceptance.acceptance_main(["smoke"]) == expected_code
    captured = capsys.readouterr()
    assert captured.out == "RESULT SENTINEL\n"
    assert captured.err == ""
