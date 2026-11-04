"""Tests for the report-tier subprocess entry (``legacy.py report``), the
reporting-deps guard and ``main``'s error mapping. pandas/plotly are replaced by
fake modules so the tests run on a bare interpreter as well as in the gate."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from cgprofile import access, targets as targets_mod
from cgprofile.cli import legacy as cg


def _fake_report_modules(monkeypatch, tmp_path: Path, calls: list):
    analysis = object()

    class _Run:
        def __init__(self, root, name, create):
            calls.append(("RunDir", root, name, create))

    analyze = types.SimpleNamespace(build=lambda run: analysis)
    html = types.SimpleNamespace(
        render=lambda a, path, plotlyjs: (calls.append(("html", path, plotlyjs)), path)[1])
    md = types.SimpleNamespace(
        render=lambda a, run_path: (calls.append(("md", run_path)), run_path + "/report.md")[1])
    store = types.SimpleNamespace(RunDir=_Run)
    import cgprofile
    monkeypatch.setattr(cgprofile, "analyze", analyze, raising=False)
    monkeypatch.setattr(cgprofile, "report_html", html, raising=False)
    monkeypatch.setattr(cgprofile, "report_md", md, raising=False)
    monkeypatch.setattr(cgprofile, "store", store, raising=False)
    for name, mod in (("analyze", analyze), ("report_html", html), ("report_md", md), ("store", store)):
        monkeypatch.setitem(sys.modules, f"cgprofile.{name}", mod)
    monkeypatch.setitem(sys.modules, "pandas", types.ModuleType("pandas"))
    monkeypatch.setitem(sys.modules, "plotly", types.ModuleType("plotly"))


def test_err_prints_and_exits_with_the_code(capsys):
    with pytest.raises(SystemExit) as exc:
        cg._err("boom", 7)
    assert exc.value.code == 7
    assert capsys.readouterr().err == "cgprofile: boom\n"


def test_note_goes_to_stderr(capsys):
    cg._note("hello")
    assert capsys.readouterr().err == "[cgprofile] hello\n"


def test_missing_reporting_deps_exit_with_the_fix(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "pandas", None)  # import raises ImportError
    with pytest.raises(SystemExit) as exc:
        cg._require_reporting_deps()
    assert exc.value.code == 2
    assert "pandas and plotly" in capsys.readouterr().err


def test_present_reporting_deps_pass(monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", types.ModuleType("pandas"))
    monkeypatch.setitem(sys.modules, "plotly", types.ModuleType("plotly"))
    cg._require_reporting_deps()


def test_report_renders_html_and_markdown_by_default(monkeypatch, tmp_path, capsys):
    calls: list = []
    _fake_report_modules(monkeypatch, tmp_path, calls)
    run_dir = str(tmp_path / "run-1")
    assert cg.main(["report", "--run-dir", run_dir]) == 0
    assert ("RunDir", str(tmp_path), "run-1", False) in calls
    assert ("html", run_dir + "/report.html", "inline") in calls
    assert ("md", run_dir) in calls
    assert capsys.readouterr().out.splitlines() == [run_dir + "/report.html", run_dir + "/report.md"]


def test_report_html_only_skips_markdown(monkeypatch, tmp_path, capsys):
    calls: list = []
    _fake_report_modules(monkeypatch, tmp_path, calls)
    assert cg.main(["report", "--run-dir", str(tmp_path / "r"), "--html-only"]) == 0
    assert not any(c[0] == "md" for c in calls)
    assert capsys.readouterr().out.strip().endswith("report.html")


def test_report_md_only_skips_html(monkeypatch, tmp_path, capsys):
    calls: list = []
    _fake_report_modules(monkeypatch, tmp_path, calls)
    assert cg.main(["report", "--run-dir", str(tmp_path / "r"), "--md-only",
                    "--plotlyjs", "directory"]) == 0
    assert not any(c[0] == "html" for c in calls)
    assert capsys.readouterr().out.strip().endswith("report.md")


def test_report_requires_run_dir():
    with pytest.raises(SystemExit):
        cg.build_parser().parse_args(["report"])


def test_collect_subparser_defaults():
    args = cg.build_parser().parse_args(["_collect", "--run-dir", "/x"])
    assert args.func is cg.cmd_collect
    assert (args.target, args.observe, args.max_depth, args.caps) == ([], [], 4, None)
    assert (args.hot_interval, args.idle_interval, args.discovery_interval) == (0.25, 2.0, 2.0)


def test_removed_one_x_verbs_are_not_subcommands():
    for verb in ("run", "attach", "mark", "targets", "doctor", "ctl"):
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args([verb])


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cg.build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("cgprofile ")


@pytest.mark.parametrize("exc", [access.AccessError("no view"), targets_mod.TargetError("bad spec")])
def test_main_maps_access_and_target_errors_to_exit_two(monkeypatch, capsys, exc):
    def boom(args):
        raise exc

    monkeypatch.setattr(cg, "cmd_report", boom)
    # the parser captured cmd_report at build time, so build it fresh after the patch
    with pytest.raises(SystemExit) as info:
        cg.main(["report", "--run-dir", "/x"])
    assert info.value.code == 2
    assert str(exc) in capsys.readouterr().err


def test_main_returns_130_on_keyboard_interrupt(monkeypatch):
    def boom(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cg, "cmd_report", boom)
    assert cg.main(["report", "--run-dir", "/x"]) == 130
