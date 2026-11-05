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


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--duration", "0", "--duration must be greater than 0"),
        ("--frames", "0", "--frames must be at least 1"),
    ],
)
def test_headless_record_rejects_non_positive_duration_and_frames(
    option: str, value: str, message: str, no_live_boundary: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--headless", "--record", "out.jsonl", option, value]) == 2
    assert message in capsys.readouterr().err


def test_restartable_source_reuses_first_stream_then_builds_fresh_ones() -> None:
    made: list[object] = []

    def make(stop_event):
        stream = [len(made)]
        made.append(stop_event)
        return stream

    source = cli._RestartableSource(make, retry_delay=cli._attach_retry_delay)
    assert list(source) == [0]  # the eagerly created stream
    assert list(source) == [1]  # a restart builds a new one
    assert list(source) == [2]
    assert all(event is source.stop_event for event in made)


def test_attach_retry_delay_backs_off_and_is_capped() -> None:
    delays = [cli._attach_retry_delay(n) for n in range(8)]
    assert delays[:4] == [1.0, 2.0, 4.0, 8.0]
    assert delays[5:] == [30.0, 30.0, 30.0]  # 2**5 = 32 is capped at 30
