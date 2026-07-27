"""Safety and result contracts for the squeeze CLI boundary."""

import json
from pathlib import Path

import pytest

from topos.actions.squeeze import SqueezeConfig, SqueezeResult
from topos.cli import _main_squeeze


def _config(tmp_path: Path, *, target: str = "/test.scope") -> SqueezeConfig:
    return SqueezeConfig(
        target=target,
        step=256 * 1024 * 1024,
        delay=2.0,
        floor=1024 * 1024 * 1024,
        start=None,
        relax_to="max",
        psi_some_limit=10.0,
        psi_full_limit=5.0,
        rf_limit=200,
        force=False,
        log_path=tmp_path / "squeeze.jsonl",
        audit_path=tmp_path / "audit.jsonl",
        admin=True,
        confirm="SQUEEZE",
    )


def _result(tmp_path: Path, *, stop_reason: str, error: str = "") -> SqueezeResult:
    return SqueezeResult(
        stop_reason=stop_reason,
        stop_high=1024 * 1024 * 1024,
        squeeze_point=768 * 1024 * 1024,
        config=_config(tmp_path),
        steps=(),
        restored_to="max",
        error=error,
    )


def test_invalid_size_is_rejected_before_gated_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[SqueezeConfig] = []
    monkeypatch.setattr("topos.actions.squeeze.run_squeeze_gated", calls.append)

    assert _main_squeeze(["--target", "/test.scope", "--step", "nonsense"]) == 2
    assert "cannot parse size" in capsys.readouterr().err
    assert calls == []


def test_subsecond_delay_is_rejected_before_gated_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[SqueezeConfig] = []
    monkeypatch.setattr("topos.actions.squeeze.run_squeeze_gated", calls.append)

    assert _main_squeeze(["--target", "/test.scope", "--delay", "0.5"]) == 2
    assert "--delay should be at least 1 second" in capsys.readouterr().err
    assert calls == []


def test_gated_runner_receives_parsed_safety_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[SqueezeConfig] = []
    monkeypatch.setattr(
        "topos.actions.squeeze.run_squeeze_gated",
        lambda config: calls.append(config) or _result(tmp_path, stop_reason="floor"),
    )

    assert _main_squeeze(
        [
            "--target",
            "/test.scope",
            "--admin",
            "--confirm",
            "SQUEEZE",
            "--step",
            "128M",
            "--delay",
            "2.5",
            "--floor",
            "512M",
            "--start",
            "1G",
            "--force",
            "--audit-path",
            str(tmp_path / "custom-audit.jsonl"),
        ]
    ) == 0
    assert len(calls) == 1
    config = calls[0]
    assert config.target == "/test.scope"
    assert config.step == 128 * 1024 * 1024
    assert config.delay == 2.5
    assert config.floor == 512 * 1024 * 1024
    assert config.start == 1024 * 1024 * 1024
    assert config.force is True
    assert config.audit_path == tmp_path / "custom-audit.jsonl"


def test_error_result_is_stderr_and_exit_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "topos.actions.squeeze.run_squeeze_gated",
        lambda _config: _result(tmp_path, stop_reason="error", error="permission denied"),
    )

    assert _main_squeeze(["--target", "/test.scope"]) == 2
    assert "SQUEEZE ERROR: permission denied" in capsys.readouterr().err


def test_interrupted_result_returns_distinct_status_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "topos.actions.squeeze.run_squeeze_gated",
        lambda _config: _result(tmp_path, stop_reason="interrupted"),
    )

    assert _main_squeeze(["--target", "/test.scope", "--json"]) == 130
    assert json.loads(capsys.readouterr().out)["stop_reason"] == "interrupted"


@pytest.mark.parametrize("json_mode", [False, True])
def test_success_result_uses_selected_text_or_json_rendering(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    json_mode: bool,
) -> None:
    monkeypatch.setattr(
        "topos.actions.squeeze.run_squeeze_gated",
        lambda _config: _result(tmp_path, stop_reason="floor"),
    )

    argv = ["--target", "/test.scope"]
    if json_mode:
        argv.append("--json")
    assert _main_squeeze(argv) == 0
    output = capsys.readouterr().out
    if json_mode:
        assert json.loads(output)["stop_reason"] == "floor"
    else:
        assert "=== Squeeze Result ===" in output
        assert "Stop reason: floor" in output
