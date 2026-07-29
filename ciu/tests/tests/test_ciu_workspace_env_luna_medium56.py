"""Bootstrap forwarding contract for certificate permission setup."""
from __future__ import annotations

from pathlib import Path

import pytest

from ciu import workspace_env


def test_bootstrap_forwards_resolved_root_and_fqdn_before_required_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permission setup receives bootstrap context before key validation."""
    (tmp_path / workspace_env.ENV_FILE_NAME).write_text("", encoding="utf-8")
    monkeypatch.setenv("PUBLIC_FQDN", "current.example.test")
    permission_calls: list[tuple[Path, str | None]] = []

    def capture_permission(repo_root: Path, public_fqdn: str | None = None) -> None:
        permission_calls.append((repo_root, public_fqdn))

    monkeypatch.setattr(workspace_env, "update_cert_permissions", capture_permission)

    with pytest.raises(
        workspace_env.WorkspaceEnvError,
        match=r"Missing required workspace environment variables: BOOTSTRAP_REQUIRED",
    ):
        workspace_env.bootstrap_workspace_env(
            start_dir=tmp_path,
            define_root=tmp_path,
            defaults_filename="ciu.global.defaults.toml.j2",
            generate_env=False,
            update_cert_permission=True,
            required_keys=("BOOTSTRAP_REQUIRED",),
        )

    assert permission_calls == [(tmp_path.resolve(), "current.example.test")]
