"""Cleanup invariant and CLI parse/error mapping contracts."""

from __future__ import annotations

from pathlib import Path

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def test_clean_fails_when_postcondition_finds_project_container(monkeypatch, tmp_path: Path, capsys):
    """A clean cannot claim success while a project container remains pinned."""

    calls = 0

    def matching(_config, *, all_states=False):
        nonlocal calls
        assert all_states is True
        calls += 1
        return [] if calls == 1 else ["project-prod-vault-init"]

    monkeypatch.setattr(deploy, "_matching_containers", matching)
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args: {})
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda _config=None, **_kw: [])

    assert deploy.action_clean(tmp_path, Profile(config={"deploy": {}}), [], ignore_errors=True) == 1
    output = capsys.readouterr().out
    assert "post-clean invariant violated (S6.4)" in output
    assert "project-prod-vault-init" in output
    assert "clean completed with errors" in output


def test_phase_filter_ignores_empty_comma_components():
    """Human-friendly separators do not create invalid empty phase names."""

    assert deploy._parse_phase_filter(" 1, ,02 ,, ") == {"phase_1", "phase_2"}


def test_main_maps_argparse_error_and_successful_system_exit(monkeypatch):
    """The single exit point preserves argparse's success/failure distinction."""

    monkeypatch.setattr(
        deploy,
        "parse_args",
        lambda _argv: (_ for _ in ()).throw(SystemExit(2)),
    )
    assert deploy.main(["--bad"]) == 2

    monkeypatch.setattr(
        deploy,
        "parse_args",
        lambda _argv: (_ for _ in ()).throw(SystemExit(0)),
    )
    assert deploy.main(["--help"]) == 0


def test_main_reraises_non_argparse_system_exit(monkeypatch):
    """A later SystemExit is not silently reclassified by the broad exception mapper."""

    monkeypatch.setattr(deploy, "parse_args", lambda _argv: object())
    monkeypatch.setattr(deploy, "_run", lambda *_args: (_ for _ in ()).throw(SystemExit(7)))

    try:
        deploy.main([])
    except SystemExit as exc:
        assert exc.code == 7
    else:
        raise AssertionError("main swallowed a non-argparse SystemExit")
