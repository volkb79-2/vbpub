"""Direct behavioral contracts for the compare CLI dispatch boundary."""

import json
from pathlib import Path

import pytest

from topos.cli import _main_compare
from topos.compare import CompareError


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    current = tmp_path / "current.json"
    baseline = tmp_path / "baseline.json"
    current.write_text(json.dumps({"role": "current", "value": 9}))
    baseline.write_text(json.dumps({"role": "baseline", "value": 6}))
    return current, baseline


def test_compare_forwards_loaded_order_metrics_rules_and_renders_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    current_path, baseline_path = _write_pair(tmp_path)
    calls: dict[str, object] = {}
    rules = [object(), object()]
    deltas = [object()]
    assertions = [object()]

    def compare(current, baseline, *, metrics):
        calls["compare"] = (current, baseline, metrics)
        return deltas

    def parse(spec: str):
        calls.setdefault("parse", []).append(spec)
        return rules[len(calls["parse"]) - 1]

    def evaluate(received_deltas, received_rules):
        calls["evaluate"] = (received_deltas, received_rules)
        return assertions

    monkeypatch.setattr("topos.compare.compare_summaries", compare)
    monkeypatch.setattr("topos.compare.parse_compare_rule", parse)
    monkeypatch.setattr("topos.compare.evaluate_compare_rules", evaluate)
    def format_compare(*args, **kwargs):
        calls["format"] = (args, kwargs)
        return "FORMATTED"
    monkeypatch.setattr("topos.compare.format_compare", format_compare)
    monkeypatch.setattr("topos.compare.compare_exit_code", lambda received: (calls.setdefault("exit", received), 7)[1])

    assert _main_compare([
        str(current_path), str(baseline_path), "--json", "--pretty",
        "--metric", "ram", "--metric", "cpu", "--assert", "a:ram:delta<=1",
        "--assert", "b:cpu:pct>=2",
    ]) == 7
    assert calls["compare"] == ({"role": "current", "value": 9}, {"role": "baseline", "value": 6}, ("ram", "cpu"))
    assert calls["parse"] == ["a:ram:delta<=1", "b:cpu:pct>=2"]
    assert calls["evaluate"] == (deltas, rules)
    assert calls["format"] == ((deltas,), {"assertions": assertions, "pretty": True})
    assert calls["exit"] is assertions
    assert capsys.readouterr().out == "FORMATTED\n"


def test_compare_without_rules_is_informational_and_formats_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    current_path, baseline_path = _write_pair(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr("topos.compare.compare_summaries", lambda *_args, **_kwargs: ["delta"])
    monkeypatch.setattr("topos.compare.evaluate_compare_rules", lambda *_args: calls.append("evaluate"))
    monkeypatch.setattr("topos.compare.compare_exit_code", lambda *_args: calls.append("exit") or 9)
    monkeypatch.setattr("topos.compare.format_compare", lambda *args, **kwargs: calls.append(f"format:{args[1:]!r}:{kwargs!r}") or '{"ok":true}')

    assert _main_compare([str(current_path), str(baseline_path), "--json"]) == 0
    assert calls == ["format:():{'assertions': None, 'pretty': False}"]
    assert capsys.readouterr().out == '{"ok":true}\n'


@pytest.mark.parametrize("argv,needle", [
    (["only-one-file", "--json"], "required"),
    (["current", "baseline"], "required: --json"),
])
def test_parser_misuse_exits_2_before_any_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str], needle: str
) -> None:
    monkeypatch.setattr("topos.compare.compare_summaries", lambda *_args, **_kwargs: pytest.fail("comparison called"))
    assert _main_compare(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert needle in captured.err


@pytest.mark.parametrize("failure,needle", [
    ("missing", "file not found"),
    ("unreadable", "error reading"),
    ("current-json", "current ("),
    ("baseline-json", "baseline ("),
    ("comparison", "comparison failed"),
])
def test_compare_declared_errors_exit_2_without_later_boundaries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str, needle: str
) -> None:
    current_path, baseline_path = _write_pair(tmp_path)
    if failure == "missing":
        current_path.unlink()
    elif failure == "unreadable":
        original_read_text = Path.read_text
        def unreadable(self: Path) -> str:
            if self == current_path:
                raise PermissionError(13, "permission denied")
            return original_read_text(self)
        monkeypatch.setattr(Path, "read_text", unreadable)
    elif failure == "current-json":
        current_path.write_text("not json")
    elif failure == "baseline-json":
        baseline_path.write_text("not json")
    else:
        monkeypatch.setattr("topos.compare.compare_summaries", lambda *_args, **_kwargs: (_ for _ in ()).throw(CompareError("comparison failed")))

    later: list[str] = []
    monkeypatch.setattr("topos.compare.format_compare", lambda *_args, **_kwargs: later.append("format"))
    monkeypatch.setattr("topos.compare.evaluate_compare_rules", lambda *_args, **_kwargs: later.append("evaluate"))
    assert _main_compare([str(current_path), str(baseline_path), "--json", "--assert", "bad"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert needle in captured.err
    assert later == []


def test_invalid_assertion_exits_2_without_evaluation_or_rendering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    current_path, baseline_path = _write_pair(tmp_path)
    monkeypatch.setattr("topos.compare.compare_summaries", lambda *_args, **_kwargs: ["delta"])
    monkeypatch.setattr("topos.compare.parse_compare_rule", lambda _spec: (_ for _ in ()).throw(CompareError("invalid rule")))
    monkeypatch.setattr("topos.compare.evaluate_compare_rules", lambda *_args: pytest.fail("evaluation called"))
    monkeypatch.setattr("topos.compare.format_compare", lambda *_args, **_kwargs: pytest.fail("formatter called"))
    assert _main_compare([str(current_path), str(baseline_path), "--json", "--assert", "bad"]) == 2
    assert capsys.readouterr().err == "invalid rule\n"
