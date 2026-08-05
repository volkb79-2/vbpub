"""Public local CLI dispatch contracts not exercised by parser or remote suites.

Each handler boundary is replaced at its owning module.  These tests verify
that user-facing CIU verbs preserve arguments and exit status without Docker,
SSH, a workspace, or a clock.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def test_up_dir_removes_public_dir_flag_and_forwards_engine_flags(monkeypatch):
    """`ciu up --dir` selects one stack without losing engine options."""
    import ciu.engine as engine

    calls: list[list[str]] = []
    monkeypatch.setattr(engine, "main", lambda argv: calls.append(argv) or 17)

    assert _run(monkeypatch, ["up", "--dir", "services/api", "--render-toml", "--reset"]) == 17
    assert calls == [["-d", "services/api", "--render-toml", "--reset"]]


def test_profile_up_and_down_preserve_profile_selection(monkeypatch):
    """Local profile verbs enter the legacy handler with their documented mode."""
    import ciu.deploy as deploy

    calls: list[list[str]] = []
    monkeypatch.setattr(deploy, "main", lambda argv: calls.append(argv) or 8)

    assert _run(monkeypatch, ["up", "--profile", "staging", "--dry-run"]) == 8
    assert _run(monkeypatch, ["down", "--profile", "staging"]) == 8
    assert calls == [
        ["--profile", "staging", "--dry-run"],
        ["--stop", "--profile", "staging"],
    ]


def test_dev_requires_stack_before_resolving_or_running(monkeypatch, capsys):
    """A malformed `ciu dev` command must not inspect a repository or launch Docker."""
    import ciu.dev as dev

    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_: pytest.fail("must not resolve root"))
    monkeypatch.setattr(dev, "run_dev", lambda *_args, **_kwargs: pytest.fail("must not run dev"))

    assert _run(monkeypatch, ["dev"]) == 2
    assert "missing <stack>" in capsys.readouterr().err


def test_dev_forwards_all_public_options_and_handler_exit(monkeypatch, tmp_path):
    """The dev verb forwards stack, profile, root and prebuild policy unchanged."""
    import ciu.dev as dev

    resolved = tmp_path / "repo"
    calls: list[tuple[str, Path, str | None, bool]] = []
    monkeypatch.setattr(dev, "resolve_repo_root", lambda root, cwd: resolved)
    monkeypatch.setattr(
        dev,
        "run_dev",
        lambda stack, *, repo_root, profile_name, no_prebuild:
        calls.append((stack, repo_root, profile_name, no_prebuild)) or 19,
    )

    assert _run(
        monkeypatch,
        ["dev", "web", "--profile", "laptop", "--no-prebuild", "--define-root", "/source"],
    ) == 19
    assert calls == [("web", resolved, "laptop", True)]


def test_secrets_forwards_subcommand_without_reinterpreting_arguments(monkeypatch):
    """Secret-store actions remain engine-owned; CIU only adds the namespace."""
    import ciu.engine as engine

    calls: list[list[str]] = []
    monkeypatch.setattr(engine, "main", lambda argv: calls.append(argv) or 5)

    assert _run(monkeypatch, ["secrets", "reset", "--name", "db", "-y"]) == 5
    assert calls == [["secrets", "reset", "--name", "db", "-y"]]


def test_top_level_and_unknown_verb_have_distinct_user_facing_outcomes(monkeypatch, capsys):
    """No verb is help; an arbitrary verb is an actionable command error."""
    assert _run(monkeypatch, []) == 0
    out = capsys.readouterr().out
    assert "Container Infrastructure Utility" in out
    for public_verb in (
        "env", "iops-baseline", "render", "profiles", "up", "down", "clean", "health",
        "diagnose", "check", "graph", "bake", "dev", "secrets", "ssh",
    ):
        assert public_verb in out

    assert _run(monkeypatch, ["not-a-verb"]) == 2
    assert "unknown verb 'not-a-verb'" in capsys.readouterr().err
