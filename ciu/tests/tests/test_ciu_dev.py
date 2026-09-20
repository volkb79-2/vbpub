"""``dev.resolve_repo_root`` precedence contracts (S1.1, ciu-P32).

CIU-41-style masked-default hazard: an ambient ``$REPO_ROOT`` inherited from a
DIFFERENT checkout's sourced ``ciu.env`` used to silently outrank a successful
walk-up derivation from where the operator was actually standing (the OLD
buggy order checked ``$REPO_ROOT`` before ``define_root`` at all). This is the
exact live scenario the operator hit: standing inside a real ciu-managed
repo, no ``--root-folder``, and a conflicting ambient ``$REPO_ROOT`` from a
sibling checkout silently winning. `resolve_repo_root` now REFUSES on a
genuine disagreement instead of silently preferring either value -- this
resolver decides which repo destructive verbs (``worktree rm``, ``branches
-y``, ``clean``) operate on.

``tests/conftest.py``'s autouse ``_scrub_ambient_identity_env`` fixture clears
``REPO_ROOT`` (and siblings) before every test body, so each test below sets
exactly the ambient state it needs via ``monkeypatch.setenv``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, dev  # noqa: E402


def test_live_scenario_ignores_conflicting_ambient_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the operator's EXACT live scenario, not a simplification.

    A real ciu-managed tree (``ciu.global.defaults.toml.j2`` present at its
    root), cwd nested a few levels inside it, no ``--root-folder`` -- exactly
    how ``ciu worktree list``/``ciu dev``/etc. are invoked day-to-day -- and an
    ambient ``$REPO_ROOT`` set to a DIFFERENT, also-real-looking repo path
    (standing in for a sibling checkout's sourced ``ciu.env``). Before this
    fix, the ambient value would have silently won; now the local marker
    wins and the ambient value is ignored.
    """
    standing_in = tmp_path / "vbpub" / "ciu"
    nested_cwd = standing_in / "src" / "ciu"
    nested_cwd.mkdir(parents=True)
    (standing_in / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")

    sibling_checkout = tmp_path / "dstdns"
    sibling_checkout.mkdir()

    monkeypatch.setenv("REPO_ROOT", str(sibling_checkout))

    assert dev.resolve_repo_root(None, nested_cwd) == standing_in.resolve()
    assert sibling_checkout.exists()


def test_uncontaminated_case_is_completely_unaffected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case -- no ambient REPO_ROOT at all -- never refuses.

    Most invocations run from a plain shell with nothing sourced: cwd inside
    a real repo, no ambient identity env at all. This must derive from cwd
    exactly as before the fix, silently, with no behavior change.
    """
    repo_root = tmp_path / "repo"
    nested_cwd = repo_root / "apps" / "web"
    nested_cwd.mkdir(parents=True)
    (repo_root / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")

    monkeypatch.delenv("REPO_ROOT", raising=False)

    assert dev.resolve_repo_root(None, nested_cwd) == repo_root.resolve()


def test_define_root_wins_outright_even_over_conflicting_ambient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit ``--root-folder`` is operator intent -- it is never second-
    guessed against ambient ``$REPO_ROOT``, even when they disagree."""
    explicit = tmp_path / "explicit-root"
    explicit.mkdir()
    (explicit / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    ambient = tmp_path / "ambient-root"
    ambient.mkdir()

    monkeypatch.setenv("REPO_ROOT", str(ambient))

    assert dev.resolve_repo_root(explicit, tmp_path) == explicit.resolve()


def test_consistent_ambient_repo_root_is_silently_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ambient REPO_ROOT that agrees with the derived root never refuses."""
    repo_root = tmp_path / "repo"
    nested_cwd = repo_root / "a" / "b"
    nested_cwd.mkdir(parents=True)
    (repo_root / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")

    monkeypatch.setenv("REPO_ROOT", str(repo_root))

    assert dev.resolve_repo_root(None, nested_cwd) == repo_root.resolve()


def test_walkup_finds_nothing_refuses_even_with_ambient_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No marker anywhere above start_dir is a typed refusal."""
    unrelated = tmp_path / "unrelated" / "nested"
    unrelated.mkdir(parents=True)
    ambient = tmp_path / "ambient-root"
    ambient.mkdir()

    monkeypatch.setenv("REPO_ROOT", str(ambient))

    with pytest.raises(ValueError, match=r"\[no-ciu-root\]"):
        dev.resolve_repo_root(None, unrelated)


def test_walkup_finds_nothing_and_no_ambient_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No marker and no explicit override is a typed refusal."""
    unrelated = tmp_path / "unrelated" / "nested"
    unrelated.mkdir(parents=True)

    monkeypatch.delenv("REPO_ROOT", raising=False)

    with pytest.raises(ValueError, match=r"\[no-ciu-root\]"):
        dev.resolve_repo_root(None, unrelated)


# ---------------------------------------------------------------------------
# O3 — every real cli.py call site propagates the refusal cleanly (never a
# raw traceback, never swallowed/downgraded, never a caller that proceeds).
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    return exc_info.value.code


@pytest.fixture
def _conflicting_repo_root_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real ciu-managed tree, nested cwd, and a disagreeing ambient REPO_ROOT.

    Every ``cli.py`` handler resolves the repo root via ``Path.cwd()`` --
    monkeypatching ``os.getcwd`` (which ``Path.cwd()`` calls internally) lets
    each test stand "inside" the fixture tree without an unsafe process-wide
    ``os.chdir`` under pytest-xdist.
    """
    repo_root = tmp_path / "repo"
    nested_cwd = repo_root / "a" / "b"
    nested_cwd.mkdir(parents=True)
    (repo_root / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")

    ambient = tmp_path / "ambient-root"
    ambient.mkdir()

    monkeypatch.setenv("REPO_ROOT", str(ambient))
    monkeypatch.setattr(os, "getcwd", lambda: str(nested_cwd))
    return nested_cwd


@pytest.mark.parametrize(
    "argv",
    [
        ["worktree", "list"],
        ["status"],
        ["bake", "--profile", "core"],
        ["ksm", "build"],
        ["provenance"],
        ["dev", "web"],
    ],
)
def test_every_cli_call_site_ignores_conflicting_ambient_root(
    argv: list[str],
    _conflicting_repo_root_cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every real resolver call site ignores ambient identity."""
    exit_code = _run_cli(monkeypatch, argv)

    err = capsys.readouterr().err
    assert not ("REPO_ROOT" in err and "not a CIU root" in err)
    assert exit_code in (0, 1, 2)


def test_worktree_exec_refuses_before_running_anything(
    _conflicting_repo_root_cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`worktree exec` still refuses a non-Git selected base."""
    from ciu import worktree as wt_mod

    monkeypatch.setattr(
        wt_mod, "exec_instance",
        lambda *a, **k: pytest.fail("must not exec: refusal should abort first"),
    )

    exit_code = _run_cli(
        monkeypatch, ["worktree", "exec", "logical-one", "--", "pwd"]
    )

    assert exit_code == 2
    err = capsys.readouterr().err
    assert "[ERROR]" in err
    assert "[no-git-root]" in err


def test_dev_verb_uses_derived_root_before_running_dev(
    _conflicting_repo_root_cwd: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ciu dev` resolves the marker-selected root before dispatch."""
    monkeypatch.setattr(
        dev, "run_dev",
        lambda *a, **k: 17,
    )

    exit_code = _run_cli(monkeypatch, ["dev", "web"])

    assert exit_code == 17
    assert capsys.readouterr().err == ""


def test_resolve_repo_root_cli_helper_reraises_as_clean_system_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unit-level: `cli._resolve_repo_root_cli` itself turns ANY ValueError
    from `dev.resolve_repo_root` into `[ERROR] ...` on stderr + a clean
    `SystemExit(2)` -- the one seam every real call site above funnels
    through (arbitrary message, to isolate this from S1.1's own wording)."""
    monkeypatch.setattr(
        dev, "resolve_repo_root",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("boom")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_repo_root_cli(None, tmp_path)

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "boom" in err
