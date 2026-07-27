"""Direct behavioral tests for inspect-files CLI dispatch."""

import json

import pytest

from topos.cli import _main_inspect_files
from topos.inspect_files.catalog import InspectFilesKind
from topos.inspect_files.plan import DisabledInspector, InspectFilesPlan
from topos.inspect_files.reader import (
    InspectFilesReadError,
    InspectFilesReadResult,
    ReadDenied,
)


def _plan() -> InspectFilesPlan:
    return InspectFilesPlan(
        kind=InspectFilesKind.CGROUP_FILES,
        target="resolved-target",
        kind_label="cgroup files",
        description="inert plan",
        path_previews=(),
        command_previews=(),
    )


def _read() -> InspectFilesReadResult:
    return InspectFilesReadResult(
        kind=InspectFilesKind.CGROUP_FILES,
        target="resolved-target",
        kind_label="cgroup files",
        description="inert read",
        path="/inert/file",
        content="payload",
    )


def test_plan_forwards_resolved_target_and_flags_and_renders_text_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[object, ...]] = []
    plan = _plan()
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda target, container, command: calls.append(("resolve", target, container, command)) or "resolved-target")
    monkeypatch.setattr("topos.inspect_files.plan.build_gated_inspect_plan", lambda *args, **kwargs: calls.append(("plan", *args, kwargs)) or plan)
    monkeypatch.setattr(InspectFilesPlan, "to_text", lambda self: calls.append(("text",)) or "PLAN TEXT")

    assert _main_inspect_files(["plan", "--kind", "cgroup-files", "--container", "prefix", "--inspect-files", "--admin"]) == 0
    assert calls == [
        ("resolve", None, "prefix", "inspect-files"),
        ("plan", "cgroup-files", "resolved-target", {"inspect_files": True, "admin": True}),
        ("text",),
    ]
    assert capsys.readouterr().out == "PLAN TEXT\n"


def test_plan_json_renders_exactly_once(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    plan = _plan()
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved-target")
    monkeypatch.setattr("topos.inspect_files.plan.build_gated_inspect_plan", lambda *args, **kwargs: plan)
    monkeypatch.setattr(InspectFilesPlan, "to_jsonable", lambda self: {"mode": "plan", "target": self.target})
    monkeypatch.setattr(InspectFilesPlan, "to_text", lambda self: pytest.fail("text renderer called"))

    assert _main_inspect_files(["plan", "--kind", "cgroup-files", "--target", "target", "--inspect-files", "--admin", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "plan", "target": "resolved-target"}


@pytest.mark.parametrize("result, status, needle", [
    (DisabledInspector(kind=InspectFilesKind.CGROUP_FILES, target="resolved-target"), 2, "not enabled"),
    (object(), 2, "unexpected inspection plan result"),
])
def test_plan_failures_short_circuit_renderer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    result: object,
    status: int,
    needle: str,
) -> None:
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved-target")
    monkeypatch.setattr("topos.inspect_files.plan.build_gated_inspect_plan", lambda *args, **kwargs: result)
    monkeypatch.setattr(InspectFilesPlan, "to_text", lambda self: pytest.fail("success renderer called"))

    assert _main_inspect_files(["plan", "--kind", "cgroup-files", "--target", "target"]) == status
    captured = capsys.readouterr()
    assert captured.out == ""
    assert needle in captured.err


def test_plan_target_and_value_errors_are_actionable_and_do_not_reach_boundary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: (_ for _ in ()).throw(ValueError("target required")))
    monkeypatch.setattr("topos.inspect_files.plan.build_gated_inspect_plan", lambda *_args, **_kwargs: calls.append("plan") or pytest.fail("boundary called"))
    assert _main_inspect_files(["plan", "--kind", "cgroup-files"]) == 2
    assert "target required" in capsys.readouterr().err
    assert calls == []

    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved-target")
    monkeypatch.setattr("topos.inspect_files.plan.build_gated_inspect_plan", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad plan")))
    assert _main_inspect_files(["plan", "--kind", "cgroup-files", "--target", "target"]) == 2
    assert capsys.readouterr().err == "bad plan\n"


def test_read_forwards_bounds_and_flags_and_renders_text_once(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[object, ...]] = []
    result = _read()
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved-target")
    monkeypatch.setattr("topos.inspect_files.reader.build_inspect_read", lambda *args, **kwargs: calls.append((*args, kwargs)) or result)
    monkeypatch.setattr(InspectFilesReadResult, "to_text", lambda self: calls.append(("text",)) or "READ TEXT")

    assert _main_inspect_files(["read", "--kind", "cgroup-files", "--target", "target", "--inspect-files", "--admin", "--max-bytes", "123", "--max-lines", "7"]) == 0
    assert calls == [("cgroup-files", "resolved-target", {"inspect_files": True, "admin": True, "max_bytes": 123, "max_lines": 7}), ("text",)]
    assert capsys.readouterr().out == "READ TEXT\n"


def test_read_json_and_declared_failures_never_use_success_renderer(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    result = _read()
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved-target")
    monkeypatch.setattr("topos.inspect_files.reader.build_inspect_read", lambda *args, **kwargs: result)
    monkeypatch.setattr(InspectFilesReadResult, "to_jsonable", lambda self: {"mode": "content", "content": self.content})
    monkeypatch.setattr(InspectFilesReadResult, "to_text", lambda self: pytest.fail("text renderer called"))
    assert _main_inspect_files(["read", "--kind", "cgroup-files", "--target", "target", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "content", "content": "payload"}

    for failure, status, needle in [
        (ReadDenied(None, "resolved-target"), 2, "not enabled"),
        (InspectFilesReadError(None, "resolved-target", "read failed"), 1, "read failed"),
        (object(), 2, "unexpected inspection read result"),
    ]:
        monkeypatch.setattr("topos.inspect_files.reader.build_inspect_read", lambda *args, failure=failure, **kwargs: failure)
        assert _main_inspect_files(["read", "--kind", "cgroup-files", "--target", "target"]) == status
        captured = capsys.readouterr()
        assert captured.out == ""
        assert needle in captured.err


def test_unknown_command_and_read_value_error_are_nonzero_without_boundary(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("topos.cli._resolve_mutual_exclusive_target", lambda *_: "resolved-target")
    monkeypatch.setattr("topos.inspect_files.reader.build_inspect_read", lambda *_args, **_kwargs: pytest.fail("read boundary called"))
    with pytest.raises(SystemExit) as exc_info:
        _main_inspect_files(["unknown", "--kind", "cgroup-files", "--target", "target"])
    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err

    monkeypatch.setattr("topos.inspect_files.reader.build_inspect_read", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad read")))
    assert _main_inspect_files(["read", "--kind", "cgroup-files", "--target", "target"]) == 2
    assert capsys.readouterr().err == "bad read\n"


def test_defensive_unknown_command_branch_reachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The final 'unknown inspect-files command' branch (cli.py:1519) is
    unreachable through normal argparse flow (required subparsers reject
    unknown commands at parse time with SystemExit).  Prove it is still
    correct by monkeypatching the parser to return a structurally valid
    but dispatch-unhandled command."""
    from argparse import Namespace

    monkeypatch.setattr(
        "topos.cli._resolve_mutual_exclusive_target",
        lambda *_: "resolved-target",
    )
    monkeypatch.setattr(
        "topos.cli.parse_inspect_files_args",
        lambda _argv: Namespace(
            command="delete",
            kind="cgroup-files",
            target="target",
            container=None,
            inspect_files=False,
            admin=False,
            json=False,
            max_bytes=65536,
            max_lines=5000,
        ),
    )

    assert (
        _main_inspect_files(
            ["delete", "--kind", "cgroup-files", "--target", "target"]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown inspect-files command" in captured.err
