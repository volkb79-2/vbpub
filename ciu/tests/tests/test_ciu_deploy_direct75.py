"""Cleanup resilience contracts at Docker and stack-directory boundaries."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ciu import deploy
from ciu.deploy_pkg.profiles import Profile


def _profile() -> Profile:
    return Profile(config={"deploy": {"project_name": "project", "environment_tag": "prod"}})


def test_clean_warns_on_failed_container_remove_skips_missing_stack_and_degrades_postcheck(
    monkeypatch, tmp_path: Path, capsys
):
    """Best-effort clean continues safely through transient Docker loss and absent stacks."""

    discovery_calls = 0

    def matching(_config, *, all_states=False):
        nonlocal discovery_calls
        assert all_states is True
        discovery_calls += 1
        if discovery_calls == 1:
            return ["project-prod-api"]
        raise ValueError("docker not available: docker")

    docker_calls: list[list[str]] = []
    monkeypatch.setattr(deploy, "_matching_containers", matching)
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda cmd, **_kwargs: (docker_calls.append(cmd) or SimpleNamespace(returncode=1, stderr="busy")),
    )
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args: {})
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda _config: [])
    monkeypatch.setattr(
        deploy.engine,
        "reset_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("missing stack must not reset")),
    )

    assert deploy.action_clean(
        tmp_path, _profile(), [{"path": "apps/missing", "service": {}}], ignore_errors=False
    ) == 0

    assert docker_calls == [["rm", "-f", "project-prod-api"]]
    output = capsys.readouterr().out
    assert "Removing 1 container(s): project-prod-api" in output
    assert "docker rm failed: busy" in output
    assert "post-clean container check skipped (docker unavailable)" in output
    assert "clean complete" in output
