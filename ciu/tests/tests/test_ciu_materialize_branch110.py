"""Focused branch coverage for secret materialization, listing, and reset."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.secrets.directives import parse_value  # noqa: E402
from ciu.secrets.materialize import list_secrets, materialize, reset_secrets  # noqa: E402


def _spec(name: str, directive: str):
    return parse_value(name, directive, "mystack.secrets")


def _materialize(specs, *, stack_dir: Path, repo_root: Path, **kwargs):
    kwargs.setdefault("vault", None)
    kwargs.setdefault("assume_yes", True)
    kwargs.setdefault("chown_fn", lambda *args, **kw: None)
    return materialize(specs, stack_dir=stack_dir, repo_root=repo_root, **kwargs)


@pytest.fixture()
def dirs(tmp_path: Path):
    repo_root = tmp_path / "repo"
    stack_dir = repo_root / "infra" / "thing"
    stack_dir.mkdir(parents=True)
    return stack_dir, repo_root


def test_empty_interactive_ask_external_input_is_not_persisted(dirs, monkeypatch):
    stack_dir, repo_root = dirs
    spec = _spec("external", "ASK_EXTERNAL:MISSING_KEY")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    with pytest.raises(ValueError, match=r"\[S4\.13\].*has no value"):
        _materialize(
            [spec],
            stack_dir=stack_dir,
            repo_root=repo_root,
            assume_yes=False,
            prompt_fn=lambda prompt: "",
        )

    assert not (stack_dir / ".ciu" / "secrets" / "external").exists()


def test_absolute_ask_file_is_referenced_unchanged(dirs, tmp_path: Path):
    stack_dir, repo_root = dirs
    source = tmp_path / "outside-stack" / "secret.pem"
    source.parent.mkdir()
    source.write_bytes(b"secret material")
    spec = _spec("certificate", f"ASK_FILE:{source}")

    result = _materialize([spec], stack_dir=stack_dir, repo_root=repo_root)

    assert result["certificate"].file == source


def test_list_reports_absolute_ask_file_store_path(dirs, tmp_path: Path):
    stack_dir, repo_root = dirs
    source = tmp_path / "outside-stack" / "secret.pem"
    source.parent.mkdir()
    source.write_bytes(b"secret material")
    spec = _spec("certificate", f"ASK_FILE:{source}")

    row = list_secrets([spec], stack_dir, repo_root)[0]

    assert row["store"] == str(source)
    assert row["exists"] is True


def test_reset_missing_selected_store_continues_and_returns_actual_deletions(dirs):
    stack_dir, repo_root = dirs
    missing = _spec("missing", "GEN_EPHEMERAL")
    present = _spec("present", "GEN_EPHEMERAL")
    result = _materialize([present], stack_dir=stack_dir, repo_root=repo_root)

    deleted = reset_secrets(
        stack_dir,
        repo_root,
        [missing, present],
        names=["missing", "present"],
    )

    assert deleted == [result["present"].file]
    assert not result["present"].file.exists()
