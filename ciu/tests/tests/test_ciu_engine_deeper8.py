"""Public empty-state contracts for ``ciu secrets``.

The command must make an empty declaration/store state explicit without
inventing a deletion or leaking a value.  These tests drive the public CLI;
configuration and store access are replaced only at their external boundary.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _prepare_empty_secret_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make lifecycle discovery deterministic without a workspace or Vault."""
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {})
    monkeypatch.setattr(
        engine.config_model, "render_stack", lambda *args, **kwargs: {"demo": {}}
    )
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *args: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *args: [])


def test_secrets_list_reports_empty_declaration_without_a_table(tmp_path, monkeypatch, capsys):
    """S4.25: no declared secrets is a clear successful lifecycle result."""
    _prepare_empty_secret_command(monkeypatch, tmp_path)
    monkeypatch.setattr(engine.secret_materialize, "list_secrets", lambda *args: [])

    assert engine.main(["secrets", "list", "-d", str(tmp_path), "--define-root", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "No secrets declared in this stack." in output
    assert "NAME" not in output


def test_secrets_reset_reports_no_artifacts_after_affirmative_empty_reset(tmp_path, monkeypatch, capsys):
    """S4.25: an affirmative reset with no store files is a no-op, not an error."""
    _prepare_empty_secret_command(monkeypatch, tmp_path)
    monkeypatch.setattr(engine.secret_materialize, "reset_secrets", lambda *args, **kwargs: [])

    assert engine.main(["secrets", "reset", "-y", "-d", str(tmp_path), "--define-root", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "No secret store files to remove" in output
    assert "Removed:" not in output
