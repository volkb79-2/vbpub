"""Focused Luna-medium36 contract for atomic secret-store cleanup."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.directives import parse_value  # noqa: E402
from ciu.secrets.materialize import materialize, project_store  # noqa: E402


def test_replace_failure_cleans_up_gen_local_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "repo"
    stack_dir = repo_root / "infra" / "thing"
    stack_dir.mkdir(parents=True)
    spec = parse_value("token", "GEN_LOCAL:shared/token", "mystack.secrets")
    target = project_store(repo_root) / "shared" / "token"
    original_error = RuntimeError("atomic replace failed")
    replace_seen: list[Path] = []

    import ciu.secrets.materialize as materialize_module

    monkeypatch.setattr(
        materialize_module._stdlib_secrets,
        "token_urlsafe",
        lambda _length: "deterministic-secret",
    )

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        replace_seen.append(source_path)
        assert source_path.exists()
        raise original_error

    monkeypatch.setattr(materialize_module.os, "replace", fail_replace)

    with pytest.raises(RuntimeError) as raised:
        materialize(
            [spec],
            stack_dir=stack_dir,
            repo_root=repo_root,
            vault=None,
            assume_yes=True,
            env={},
            chown_fn=lambda *_args: None,
        )

    assert raised.value is original_error
    assert replace_seen
    assert not target.exists()
    assert not list(project_store(repo_root).rglob(".tmp-secret-*"))
