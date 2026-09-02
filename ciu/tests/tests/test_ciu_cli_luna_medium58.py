"""Focused regression coverage for thin-health activation errors."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import ciu.deploy as REAL_DEPLOY  # captured BEFORE any fixture stubs sys.modules
from ciu import cli


def test_thin_health_maps_activation_value_error_to_error_exit(monkeypatch, tmp_path, capsys):
    """An activation validation error is reported without exposing a traceback."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    host = {"ssh_host": "web", "ssh_key": "/key", "known_host": "pinned"}

    import ciu.activate as activate
    import ciu.hosts as hosts

    monkeypatch.setattr(
        hosts,
        "get_host",
        lambda root, name, admin=False: host,
    )
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda root: {},
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
    )
    monkeypatch.setattr(
        activate,
        "run_activation",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid activation")),
    )
    monkeypatch.setattr(sys, "argv", ["ciu", "health", "--host", "web", "--thin"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "[ERROR] invalid activation" in capsys.readouterr().err
