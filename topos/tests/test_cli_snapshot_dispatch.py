"""CLI boundary contracts for snapshot inspection."""

from pathlib import Path

import pytest

from topos.cli import _main_snapshot


def test_snapshot_inspect_forwards_path_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = Path("/safe/fixture.tar.zst")
    received: list[Path] = []

    def fake_inspect(path: Path) -> str:
        received.append(path)
        return "status: ok\nframes: 1"

    monkeypatch.setattr("topos.cli.inspect_bundle", fake_inspect)

    assert _main_snapshot(["inspect", str(bundle)]) == 0
    assert received == [bundle]
    assert capsys.readouterr().out == "status: ok\nframes: 1\n"


@pytest.mark.parametrize(
    "error",
    [
        OSError("bundle cannot be read"),
        RuntimeError("bundle manifest is invalid"),
        ValueError("bundle path is malformed"),
    ],
)
def test_snapshot_inspect_reports_expected_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    def fail_inspect(_path: Path) -> str:
        raise error

    monkeypatch.setattr("topos.cli.inspect_bundle", fail_inspect)

    assert _main_snapshot(["inspect", "/safe/fixture.tar.zst"]) == 1
    captured = capsys.readouterr()
    assert captured.err == f"{error}\n"
    assert captured.out == ""
