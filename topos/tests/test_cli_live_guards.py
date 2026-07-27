"""Pre-collection safety guards for the live CLI mode."""

import pytest

import topos.cli as cli


@pytest.fixture
def no_live_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("live Collector/UI boundary was reached")

    monkeypatch.setattr(cli, "Collector", fail)
    monkeypatch.setattr(cli, "_run_ui", fail)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--headless", "--replay", "replay.jsonl"], "--headless is not supported with --replay"),
        (["--headless"], "--headless requires --record"),
        (["--headless", "--record", "out.jsonl", "--attach", "/run/topos.sock"], "--headless is not supported with --attach"),
        (["--record", "out.jsonl", "--replay", "replay.jsonl"], "choose either --record or --replay"),
    ],
)
def test_live_mode_rejects_incompatible_headless_options(
    argv: list[str], message: str, no_live_boundary: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(argv) == 2
    assert message in capsys.readouterr().err


def test_live_mode_rejects_duration_and_frames(
    no_live_boundary: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--record", "out.jsonl", "--duration", "10", "--frames", "2"]) == 2
    assert "--duration and --frames are mutually exclusive" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--slice", "/invalid", "invalid --slice"),
        ("--metrics", "unknown_metric", "invalid --metrics"),
    ],
)
def test_live_mode_rejects_invalid_collection_constraints(
    option: str,
    value: str,
    message: str,
    no_live_boundary: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--once", option, value]) == 2
    assert message in capsys.readouterr().err
