"""Focused Luna-medium35 contracts for secret materialization."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.directives import SecretSpec, parse_value  # noqa: E402
from ciu.secrets.materialize import materialize  # noqa: E402


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = tmp_path / "repo"
    stack_dir = repo_root / "infra" / "thing"
    stack_dir.mkdir(parents=True)
    return stack_dir, repo_root


def test_invalid_optional_ownership_env_degrades_to_none(tmp_path: Path) -> None:
    stack_dir, repo_root = _dirs(tmp_path)
    spec = parse_value("token", "GEN_LOCAL:shared/token", "mystack.secrets")
    seen: list[tuple[Path, int | None, int | None]] = []

    result = materialize(
        [spec],
        stack_dir=stack_dir,
        repo_root=repo_root,
        vault=None,
        assume_yes=True,
        env={"CONTAINER_UID": "not-a-uid", "DOCKER_GID": "not-a-gid"},
        chown_fn=lambda path, uid, gid: seen.append((path, uid, gid)),
    )

    assert result["token"].file is not None
    assert seen and seen[0][1:] == (None, None)


def test_malformed_mode_degrades_to_default_0440(tmp_path: Path) -> None:
    stack_dir, repo_root = _dirs(tmp_path)
    spec = parse_value(
        "token",
        {"directive": "GEN_LOCAL:shared/token", "mode": "not-octal"},
        "mystack.secrets",
    )

    result = materialize(
        [spec],
        stack_dir=stack_dir,
        repo_root=repo_root,
        vault=None,
        assume_yes=True,
        env={},
        chown_fn=lambda *_args: None,
    )

    assert result["token"].file is not None
    assert result["token"].file.stat().st_mode & 0o777 == 0o440


def test_unsupported_kind_fails_before_persisting_secret(tmp_path: Path) -> None:
    stack_dir, repo_root = _dirs(tmp_path)
    spec = SecretSpec(
        name="token",
        kind="NOT_A_KIND",
        locator=None,
        table_path="mystack.secrets",
    )

    with pytest.raises(ValueError, match=r"\[S4\.2\] unsupported secret kind"):
        materialize(
            [spec],
            stack_dir=stack_dir,
            repo_root=repo_root,
            vault=None,
            assume_yes=True,
            env={},
            chown_fn=lambda *_args: None,
        )

    assert not list(repo_root.rglob("token"))
