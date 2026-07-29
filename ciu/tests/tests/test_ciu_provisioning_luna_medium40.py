"""Luna-medium40 provisioning probes fail safely at live boundaries."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import provisioning  # noqa: E402


def test_probe_ref_vault_without_token_is_unsatisfied(monkeypatch, tmp_path):
    from ciu.secrets import providers

    monkeypatch.setattr(providers, "vault_addr_from_config", lambda config: "http://vault")
    monkeypatch.setattr(providers, "resolve_vault_token", lambda config, root: None)

    result = provisioning.probe_ref("vault:secret/app/password", {}, tmp_path)

    assert result.satisfied is False
    assert result.reason == "No Vault token available"


@pytest.mark.parametrize(
    "ref",
    ["pg:db/app", "minio:user/worker", "stack:db:healthy"],
)
def test_probe_ref_unavailable_docker_is_unsatisfied(monkeypatch, tmp_path, ref):
    from ciu import procutil

    def unavailable_docker(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(procutil, "docker", unavailable_docker)

    result = provisioning.probe_ref(ref, {}, tmp_path)

    assert result.satisfied is False
    assert "docker not available" in result.reason


@pytest.mark.parametrize(
    "ref, default_name",
    [
        ("pg:db/app", "postgres"),
        ("minio:user/worker", "minio"),
        ("stack:db:healthy", "db"),
    ],
)
def test_probe_ref_uses_service_default_when_container_name_cannot_be_built(
    monkeypatch, tmp_path, ref, default_name
):
    from ciu import deploy

    seen = []

    def unavailable_container_name(config, service):
        raise ValueError("cannot derive container name")

    def injected_docker_exec(container, command):
        seen.append((container, command))
        return 1, ""

    monkeypatch.setattr(deploy, "container_name", unavailable_container_name)

    result = provisioning.probe_ref(
        ref, {"project": {"name": "broken"}}, tmp_path,
        docker_exec_fn=injected_docker_exec,
    )

    assert result.satisfied is False
    assert seen and seen[0][0] == default_name
