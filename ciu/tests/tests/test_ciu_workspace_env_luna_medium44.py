"""Hermetic workspace TLS validation and generated-bootstrap contracts."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import workspace_env  # noqa: E402


def test_tls_file_disappearing_before_stat_is_typed_readability_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert_path = tmp_path / "cert.pem"
    cert_path.write_text("certificate", encoding="utf-8")
    original_stat = workspace_env.Path.stat

    def disappearing_stat(path: Path, *args: object, **kwargs: object):
        if path == cert_path:
            raise FileNotFoundError(2, "No such file or directory", str(path))
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(workspace_env.Path, "stat", disappearing_stat)

    with pytest.raises(workspace_env.WorkspaceEnvError, match=r"TLS file not readable: .*cert\.pem"):
        workspace_env.validate_required_certs(
            {
                "PUBLIC_TLS_CRT_PEM": str(cert_path),
                "DOCKER_GID": "1000",
            }
        )


def test_malformed_docker_gid_does_not_grant_group_only_tls_readability(
    tmp_path: Path,
) -> None:
    cert_path = tmp_path / "group-only.pem"
    cert_path.write_text("certificate", encoding="utf-8")
    cert_path.chmod(0o040)

    with pytest.raises(workspace_env.WorkspaceEnvError, match=r"DOCKER_GID \(None\)"):
        workspace_env.validate_required_certs(
            {
                "PUBLIC_TLS_CRT_PEM": str(cert_path),
                "DOCKER_GID": "not-a-gid",
            }
        )


def test_generated_bootstrap_warns_on_network_workspace_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_instance_facts,
) -> None:
    env_path = tmp_path / "ciu.env"

    def generate(root: Path, **_kw) -> Path:
        # Both records, as the real generate writes them (CIU-75): the network
        # the bootstrap acts on comes from the overlay table now.
        assert root == tmp_path
        env_path.write_text("DOCKER_NETWORK_INTERNAL=generated-net\n", encoding="utf-8")
        write_instance_facts(root, network="generated-net")
        return env_path

    monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
    monkeypatch.setattr(workspace_env, "generate_ciu_env", generate)
    monkeypatch.setattr(
        workspace_env,
        "ensure_workspace_network",
        lambda _name: (_ for _ in ()).throw(
            workspace_env.WorkspaceEnvError("daemon unavailable")
        ),
    )
    tls_calls: list[bool] = []
    monkeypatch.setattr(workspace_env, "_check_tls_access", lambda: tls_calls.append(True))

    assert workspace_env.bootstrap_workspace_env(
        start_dir=tmp_path,
        define_root=None,
        defaults_filename="ciu.global.defaults.toml.j2",
        generate_env=True,
        update_cert_permission=False,
        required_keys=(),
    ) == tmp_path.resolve()
    assert "Network setup skipped: daemon unavailable" in capsys.readouterr().out
    assert tls_calls == [True]
