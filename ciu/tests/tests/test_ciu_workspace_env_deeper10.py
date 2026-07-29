"""Malformed rendered-endpoint configuration contract for ``ciu env generate``."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_env_generate_warns_and_falls_back_for_scalar_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid-but-wrong-shaped rendered config cannot crash environment generation.

    ``ciu.global.toml`` may be syntactically valid while violating CIU's table
    shape.  ``ciu env generate`` must retain the malformed-config recovery
    behavior used for invalid TOML: warn, avoid external endpoint use, and emit
    the deterministic localhost fallback rather than leaking ``AttributeError``.
    """
    (tmp_path / "ciu.global.toml").write_text(
        'infrastructure = "not-a-table"\n', encoding="utf-8"
    )
    monkeypatch.delenv("PUBLIC_FQDN", raising=False)
    monkeypatch.delenv("PUBLIC_IP", raising=False)
    monkeypatch.setenv("ENV_TYPE", "native")
    monkeypatch.setattr(workspace_env, "_detect_docker_gid", lambda: "1002")
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda root: root)
    monkeypatch.setattr(workspace_env, "_detect_host_mdt_tmp", lambda: "")
    monkeypatch.setattr(
        workspace_env, "_detect_governance_read_iops", lambda: ("600", "test baseline")
    )

    def offline_urlopen(*_args: object, **_kwargs: object) -> object:
        raise workspace_env.urllib.error.URLError("offline")

    monkeypatch.setattr(workspace_env.urllib.request, "urlopen", offline_urlopen)

    env_path = workspace_env.generate_ciu_env(tmp_path)

    assert workspace_env.parse_workspace_env(env_path)["PUBLIC_FQDN"] == "localhost"
    assert f"Could not parse {tmp_path / 'ciu.global.toml'}" in capsys.readouterr().out
