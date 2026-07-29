"""Git-ignore safeguard fallback contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def test_gitignore_check_skips_when_git_is_not_installed(monkeypatch, tmp_path: Path):
    """CIU remains usable in a minimal runtime that has no git binary."""

    monkeypatch.setattr(
        engine.procutil,
        "run_cmd",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("git")),
    )

    engine._check_gitignore(tmp_path / "stack")


def test_gitignore_check_skips_non_worktree_and_accepts_literal_ciu_fallback(monkeypatch, tmp_path: Path):
    """Non-repositories are skipped; repositories may ignore the literal .ciu directory."""

    stack = tmp_path / "stack"
    non_worktree = SimpleNamespace(returncode=128, stdout="", stderr="not a repo")
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *_args, **_kwargs: non_worktree)
    engine._check_gitignore(stack)

    calls: list[list[str]] = []
    responses = iter([
        SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
        SimpleNamespace(returncode=1, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
    ])
    monkeypatch.setattr(
        engine.procutil,
        "run_cmd",
        lambda argv, **_kwargs: calls.append(argv) or next(responses),
    )

    engine._check_gitignore(stack)

    assert calls[1][-1] == str(stack / ".ciu" / "secrets")
    assert calls[2][-1] == str(stack / ".ciu")
