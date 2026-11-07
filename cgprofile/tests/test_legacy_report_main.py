"""Tests for the collector child's entry (``legacy.py _collect``): its parser,
the small output helpers and ``main``'s error mapping. The 1.x ``serve`` and
``report`` subcommands are gone (cgprofiled and ``cgprofilectl report`` replace them)."""

from __future__ import annotations

import pytest

from cgprofile import access, targets as targets_mod
from cgprofile.cli import legacy as cg


def test_err_prints_and_exits_with_the_code(capsys):
    with pytest.raises(SystemExit) as exc:
        cg._err("boom", 7)
    assert exc.value.code == 7
    assert capsys.readouterr().err == "cgprofile: boom\n"


def test_note_goes_to_stderr(capsys):
    cg._note("hello")
    assert capsys.readouterr().err == "[cgprofile] hello\n"


def test_collect_subparser_defaults():
    args = cg.build_parser().parse_args(["_collect", "--run-dir", "/x"])
    assert args.func is cg.cmd_collect
    assert (args.target, args.observe, args.max_depth, args.caps) == ([], [], 4, None)
    assert (args.hot_interval, args.idle_interval, args.discovery_interval) == (0.25, 2.0, 2.0)


def test_removed_verbs_are_not_subcommands():
    for verb in ("run", "attach", "mark", "targets", "doctor", "ctl", "serve", "report"):
        with pytest.raises(SystemExit):
            cg.build_parser().parse_args([verb])


def test_the_daemon_and_report_entry_points_are_gone():
    for name in ("cmd_serve", "cmd_report", "_require_reporting_deps",
                 "DEFAULT_CTL_SOCKET", "DEFAULT_CGPROFILE_SESSIONS"):
        assert not hasattr(cg, name), name


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cg.build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("cgprofile ")


@pytest.mark.parametrize("exc", [access.AccessError("no view"), targets_mod.TargetError("bad spec")])
def test_main_maps_access_and_target_errors_to_exit_two(monkeypatch, capsys, exc):
    def boom(args):
        raise exc

    monkeypatch.setattr(cg, "cmd_collect", boom)
    with pytest.raises(SystemExit) as info:
        cg.main(["_collect", "--run-dir", "/x"])
    assert info.value.code == 2
    assert str(exc) in capsys.readouterr().err


def test_main_returns_130_on_keyboard_interrupt(monkeypatch):
    def boom(args):
        raise KeyboardInterrupt

    monkeypatch.setattr(cg, "cmd_collect", boom)
    assert cg.main(["_collect", "--run-dir", "/x"]) == 130
