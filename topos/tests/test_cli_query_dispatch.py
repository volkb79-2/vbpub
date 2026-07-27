"""Direct behavioral contracts for the query CLI dispatch boundary."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.cli import _main_query
from topos.query import Caps, MetricRef, Query, Selector, SortSpec
from topos.query.errors import QueryError


def test_parser_misuse_and_missing_metric_are_pre_io(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("topos.query.source.RecordingFrameSource", lambda *_: calls.append("source"))
    monkeypatch.setattr("topos.query.run_query", lambda *_: calls.append("run"))

    assert _main_query(["/fixture/recording", "--shape", "invalid"]) == 2
    assert _main_query(["/fixture/recording"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid choice" in captured.err
    assert "topos query requires at least one --metric" in captured.err
    assert calls == []


def test_query_forwards_complete_typed_boundary_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sources: list[Path] = []
    queries: list[Query] = []
    result = SimpleNamespace(to_jsonable=lambda: {"rows": []})

    class Source:
        def __init__(self, path: Path) -> None:
            sources.append(path)

    def run(source: Source, query: Query) -> object:
        queries.append(query)
        assert source is not None
        return result

    monkeypatch.setattr("topos.query.source.RecordingFrameSource", Source)
    monkeypatch.setattr("topos.query.run_query", run)
    monkeypatch.setattr("topos.query.format_result", lambda value, *, pretty: f"JSON({value is result},{pretty})")

    assert _main_query(
        [
            "/fixture/recording.jsonl",
            "--shape", "current",
            "--metric", "cpu:rate",
            "--metric", "ram",
            "--window", "last:30s",
            "--entities", "host.*",
            "--entities", "*.scope",
            "--select", "host.scope",
            "--slice", "services.slice",
            "--projection", "hierarchy",
            "--visibility", "available",
            "--sort", "cpu:p95:asc",
            "--max-rows", "7", "--max-points", "8", "--max-bytes", "9",
            "--on-exceed", "truncate", "--json", "--pretty",
        ]
    ) == 0
    assert sources == [Path("/fixture/recording.jsonl")]
    assert queries == [
        Query(
            shape="current",
            metrics=(MetricRef("cpu", "rate"), MetricRef("ram")),
            window_spec="last:30s",
            selector=Selector(keys=("host.scope",), globs=("host.*", "*.scope"), slice="services.slice"),
            projection="hierarchy",
            visibility="available",
            sort=SortSpec("cpu", "p95", "asc"),
            caps=Caps(7, 8, 9, "truncate"),
        )
    ]
    assert capsys.readouterr().out == "JSON(True,True)\n"


def test_query_default_table_uses_jsonable_render_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = SimpleNamespace(to_jsonable=lambda: {"meta": {"ok": True}, "rows": []})
    calls: list[object] = []
    monkeypatch.setattr("topos.query.source.RecordingFrameSource", lambda _path: object())
    monkeypatch.setattr("topos.query.run_query", lambda *_: result)
    monkeypatch.setattr("topos.render.render_query", lambda value: calls.append(value) or "TABLE")

    assert _main_query(["/fixture/recording", "--metric", "cpu"]) == 0
    assert calls == [{"meta": {"ok": True}, "rows": []}]
    assert capsys.readouterr().out == "TABLE\n"


@pytest.mark.parametrize(
    "error",
    [QueryError("bad query"), FileNotFoundError(), RuntimeError("runtime"), ValueError("value"), OSError(5, "read failed"), Exception("boom")],
)
def test_query_maps_boundary_failures_without_success_renderer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    monkeypatch.setattr("topos.query.source.RecordingFrameSource", lambda _path: object())
    monkeypatch.setattr("topos.query.run_query", lambda *_: (_ for _ in ()).throw(error))
    rendered: list[object] = []
    monkeypatch.setattr("topos.query.format_result", lambda *_args, **_kwargs: rendered.append("json"))
    monkeypatch.setattr("topos.render.render_query", lambda *_args: rendered.append("table"))

    assert _main_query(["/fixture/missing.jsonl", "--metric", "cpu", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert rendered == []
    if isinstance(error, FileNotFoundError):
        assert captured.err == "file not found: /fixture/missing.jsonl\n"
    elif isinstance(error, OSError):
        assert captured.err == "error reading /fixture/missing.jsonl: read failed\n"
    elif isinstance(error, Exception) and type(error) is Exception:
        assert captured.err == "unexpected error querying /fixture/missing.jsonl\n"
    else:
        assert captured.err == f"{error}\n"
