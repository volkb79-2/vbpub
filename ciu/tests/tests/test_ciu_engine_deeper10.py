"""Public ``--generate-env`` preflight failure contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def test_generate_env_dependency_failure_is_red_and_stops_before_environment_write(
    tmp_path, monkeypatch, capsys
):
    """S2.8/S10.3: an unavailable runtime blocks environment generation safely.

    ``ciu --generate-env`` still depends on CIU's runtime preflight.  When that
    preflight fails, the public CLI must expose the environment/preflight exit
    code and must not resolve a root or write ``ciu.env`` afterward.
    """
    monkeypatch.setattr(
        engine,
        "check_runtime_dependencies",
        lambda: (_ for _ in ()).throw(engine.DependencyError("docker unavailable")),
    )
    monkeypatch.setattr(
        engine,
        "resolve_env_root",
        lambda **_kwargs: pytest.fail("failed preflight must not resolve an environment root"),
    )
    monkeypatch.setattr(
        engine,
        "bootstrap_env_init",
        lambda _root: pytest.fail("failed preflight must not write ciu.env"),
    )

    assert engine.main(["--generate-env", "-d", str(tmp_path)]) == 3
    assert "[ERROR] docker unavailable" in capsys.readouterr().out
