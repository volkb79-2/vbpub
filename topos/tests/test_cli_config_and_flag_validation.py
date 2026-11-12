from __future__ import annotations

from pathlib import Path

import pytest

from topos import cli


def test_main_reports_config_error_and_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[general]\ninterval = 0\n")
    assert cli.main(["--config", str(path), "--once", "--json"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"topos: config {path}: ")
    assert "general.interval" in err
    assert "Traceback" not in err


def test_main_reports_toml_syntax_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[general\n")
    assert cli.main(["--config", str(path), "--once", "--json"]) == 2
    assert "topos: config" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["--duration", "5", "--once", "--json"],
        ["--frames", "3"],
        ["--record", "x.jsonl", "--frames", "3"],
        ["--speed", "2"],
        ["--step"],
        ["--interval", "0.01"],
        ["--interval", "nan"],
    ],
)
def test_ignored_flags_are_rejected(argv: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = tmp_path / "empty.toml"
    cfg.write_text("")
    assert cli.main(["--config", str(cfg), *argv]) == 2
    assert capsys.readouterr().err


def test_interval_flag_reaches_attach_poll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "empty.toml"
    cfg.write_text("")
    seen: dict[str, float] = {}

    def fake_source(socket: Path, *, poll_interval_s: float):
        seen["poll"] = poll_interval_s
        return iter(())

    monkeypatch.setattr(cli, "_attach_frame_source", fake_source)
    monkeypatch.setattr(cli, "_run_ui", lambda *a, **k: (seen.setdefault("ui_interval", k["config"].interval), 0)[1])
    assert cli.main(["--config", str(cfg), "--attach", str(tmp_path / "s.sock"), "--interval", "1.5"]) == 0
    assert seen == {"poll": 1.5, "ui_interval": 1.5}
