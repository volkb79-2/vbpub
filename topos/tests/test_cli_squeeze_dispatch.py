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


def test_signal_termination_during_run_maps_to_128_plus_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import signal

    from topos.actions.squeeze import _Terminated

    def terminated(_config):
        raise _Terminated(signal.SIGTERM)

    monkeypatch.setattr("topos.actions.squeeze.run_squeeze_gated", terminated)
    assert _main_squeeze(["--target", "/test.scope"]) == 128 + signal.SIGTERM


def test_result_carrying_exit_signal_exits_128_plus_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import dataclasses

    result = dataclasses.replace(_result(tmp_path, stop_reason="floor"), exit_signal=15)
    monkeypatch.setattr("topos.actions.squeeze.run_squeeze_gated", lambda _c: result)
    assert _main_squeeze(["--target", "/test.scope"]) == 128 + 15


def test_recover_requires_root_and_does_nothing_otherwise(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        "topos.actions.squeeze.recover_squeeze",
        lambda target: pytest.fail("recovery must not run without root"),
    )
    assert _main_squeeze(["--recover"]) == 2
    assert "requires root" in capsys.readouterr().err


def test_recover_reports_nothing_success_and_failure_exit_codes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from topos.actions.squeeze import RecoverOutcome

    monkeypatch.setattr("os.geteuid", lambda: 0)
    outcomes: list = []
    monkeypatch.setattr("topos.actions.squeeze.recover_squeeze", lambda target: outcomes)

    assert _main_squeeze(["--recover"]) == 0
    assert "nothing to recover" in capsys.readouterr().out

    outcomes[:] = [RecoverOutcome(tmp_path / "a.json", "/cg/a", "1", True)]
    assert _main_squeeze(["--recover"]) == 0
    assert "recovered /cg/a: memory.high restored to 1" in capsys.readouterr().out

    outcomes.append(RecoverOutcome(tmp_path / "b.json", "/cg/b", "2", False, "EINVAL"))
    assert _main_squeeze(["--recover"]) == 3
    captured = capsys.readouterr()
    assert "RECOVERY FAILED for /cg/b" in captured.err and "EINVAL" in captured.err
