"""Public missing-workspace-root failure contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def test_cli_rejects_bootstrap_without_repo_root_before_render_or_deployment(tmp_path, monkeypatch, capsys):
    """S2/S10.3: no resolved workspace root is an environment failure, not a deploy.

    A bootstrap provider that returns without ``REPO_ROOT`` leaves CIU unable to
    determine which repository owns the invocation.  The CLI must exit with
    the environment code and never render configuration, create a network, or
    enter stack execution against an arbitrary working directory.
    """
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "enforce_standalone_root", lambda _path: None)
    monkeypatch.setattr(
        engine.config_model,
        "render_global_chain",
        lambda *_args: pytest.fail("missing workspace root must not render configuration"),
    )
    monkeypatch.setattr(
        engine,
        "ensure_workspace_network",
        lambda **_kwargs: pytest.fail("missing workspace root must not touch the workspace network"),
    )
    monkeypatch.setattr(
        engine,
        "auto_generate_values",
        lambda _config: pytest.fail("missing workspace root must not begin stack execution"),
    )

    assert engine.main(["--dry-run", "-d", str(tmp_path)]) == 3
    assert "REPO_ROOT not set" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
