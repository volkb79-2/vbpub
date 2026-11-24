"""CIU-231 — an empty deploy selection inside a stack dir is a refusal, not rc 0.

Reported: ``ciu up`` with no ``--dir``, run from inside ``voice-screen-share/
jitsi-server`` of a multi-stack root, printed ``No phases/stacks selected to
deploy`` and exited 0. `ciu up --dir .` in the same directory deployed. An rc of
0 hides that miss from automation, so the refusal (rc 2) names the remedy.

The empty-selection rc-0 no-op is KEPT for the cases it was written for: the
root's own profile selecting nothing, and an invocation outside this root.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _deploy(monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> int:
    monkeypatch.setattr(
        deploy, "profile_env",
        lambda _profile: pytest.fail("an empty selection must not build a stack env"),
    )
    return deploy.action_deploy(
        repo_root,
        Profile(),
        [],
        dry_run=False,
        ignore_errors=False,
        health_after_phase=False,
        update_cert_permission=False,
    )


def test_empty_selection_inside_a_stack_dir_refuses_with_the_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """(a) cwd in one stack dir of a multi-stack root: rc != 0 AND the remedy."""
    root = tmp_path / "root"
    stack = root / "voice-screen-share" / "jitsi-server"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text("[jitsi]\n", encoding="utf-8")
    (root / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    # A multi-stack root: another stack exists beside the invocation directory.
    (root / "voice-screen-share" / "fluxer-server").mkdir()

    monkeypatch.chdir(stack)
    assert _deploy(monkeypatch, root) == 2

    out = capsys.readouterr().out
    assert "--dir ." in out
    assert str(stack) in out
    assert "No phases/stacks selected to deploy" not in out


def test_empty_selection_at_the_root_keeps_the_rc_zero_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The original contract: a profile that selects nothing is not an error."""
    monkeypatch.chdir(tmp_path)
    assert _deploy(monkeypatch, tmp_path) == 0
    assert "No phases/stacks selected to deploy" in capsys.readouterr().out


def test_empty_selection_from_a_non_stack_directory_keeps_the_rc_zero_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """(b) A directory that is not a stack has no stack to name."""
    root = tmp_path / "root"
    plain = root / "docs"
    plain.mkdir(parents=True)
    monkeypatch.chdir(plain)
    assert _deploy(monkeypatch, root) == 0
    assert "No phases/stacks selected to deploy" in capsys.readouterr().out


def test_empty_selection_outside_the_root_keeps_the_rc_zero_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """An invocation outside this root is not the reported case."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "elsewhere"
    (outside / "stack").mkdir(parents=True)
    (outside / "stack" / "ciu.defaults.toml.j2").write_text("[x]\n", encoding="utf-8")

    monkeypatch.chdir(outside / "stack")
    assert _deploy(monkeypatch, root) == 0
    assert "No phases/stacks selected to deploy" in capsys.readouterr().out


def test_a_shipped_stack_dir_also_gets_the_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """A shipped stack has no defaults template; the compose file marks it."""
    root = tmp_path / "root"
    stack = root / "apps" / "shipped"
    stack.mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    monkeypatch.chdir(stack)
    assert _deploy(monkeypatch, root) == 2
    assert "--dir ." in capsys.readouterr().out
