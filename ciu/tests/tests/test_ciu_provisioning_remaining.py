"""Failure-safe provisioning reference and live-probe contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import provisioning  # noqa: E402


@pytest.mark.parametrize(
    "ref",
    [
        "vault:secret/app/password\n",
        "pg:role/app\n",
        "minio:user/worker\n",
        "consul:token/app\n",
        "stack:db:healthy\n",
    ],
)
def test_parse_ref_rejects_trailing_newline(ref):
    """A rendered ref with a line terminator is malformed, never provisionable."""
    with pytest.raises(ValueError, match="does not match any valid pattern"):
        provisioning.parse_ref(ref)


def test_lint_graph_reports_external_stack_requirement_without_cycle():
    """An unknown stack dependency is an unsatisfied provider, not a DFS cycle."""
    errors = provisioning.lint_graph(
        {"apps/api": {"requires": ["stack:outside:healthy"], "provides": []}}
    )
    assert errors == [
        "[ERROR] Stack 'apps/api' requires 'stack:outside:healthy' but nobody provides it"
    ]


def test_vault_probe_returns_red_when_token_is_unavailable(monkeypatch, tmp_path):
    """No token must block a Vault requirement before any client/network use."""
    from ciu.secrets import providers

    monkeypatch.setattr(providers, "vault_addr_from_config", lambda config: "http://vault")
    monkeypatch.setattr(providers, "resolve_vault_token", lambda config, root: None)

    result = provisioning.probe_ref("vault:secret/app/password", {}, tmp_path)

    assert result.satisfied is False
    assert result.reason == "No Vault token available"


def test_vault_probe_returns_red_for_address_configuration_error(monkeypatch, tmp_path):
    """Invalid Vault topology must be a failed requirement, not an uncaught error."""
    from ciu.secrets import providers

    monkeypatch.setattr(
        providers,
        "vault_addr_from_config",
        lambda config: (_ for _ in ()).throw(providers.VaultError("missing vault topology")),
    )

    result = provisioning.probe_ref("vault:secret/app/password", {}, tmp_path)

    assert result.satisfied is False
    assert result.reason == "missing vault topology"


def test_vault_probe_returns_red_when_read_raises(tmp_path):
    """A Vault transport/read error must not be mistaken for an existing secret."""
    class BrokenVault:
        def read(self, path):
            raise RuntimeError("connection reset")

    result = provisioning.probe_ref(
        "vault:secret/app/password", {}, tmp_path, vault_client=BrokenVault()
    )

    assert result.satisfied is False
    assert result.reason == "Vault read error: connection reset"


@pytest.mark.parametrize(
    "ref, expected",
    [
        ("pg:db/app", "docker not available"),
        ("minio:user/worker", "docker not available"),
    ],
)
def test_service_probe_returns_red_when_docker_is_unavailable(monkeypatch, tmp_path, ref, expected):
    """A missing Docker boundary cannot turn a required resource green."""
    from ciu import procutil

    def missing_docker(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(procutil, "docker", missing_docker)

    result = provisioning.probe_ref(
        ref, {}, tmp_path,
        stacks={"provider": {"provides": ["pg:db/app", "minio:user/worker"]}},
    )

    assert result.satisfied is False
    assert expected in result.reason


def test_consul_probe_uses_safe_default_for_malformed_path_template(tmp_path):
    """Malformed token templates fall back to the documented default path."""
    seen_paths = []

    class Vault:
        def read(self, path):
            seen_paths.append(path)
            return None

    result = provisioning.probe_ref(
        "consul:token/api",
        {"registry": {"consul": {"token_vault_path": "tokens/{unknown}"}}},
        tmp_path,
        vault_client=Vault(),
    )

    assert result.satisfied is False
    assert seen_paths == ["consul/acl/tokens/api"]
    assert "not found" in result.reason


@pytest.mark.parametrize(
    "state, expected",
    [
        (None, "Container 'db' not found"),
        ("", "No state for container 'db'"),
        ("not json", "Could not parse container state for 'db'"),
        ('{"Health": {"Status": "unhealthy"}}', "health status: unhealthy"),
        ('{"Health": {}, "Running": false, "ExitCode": 2}', "not running (exit code 2)"),
    ],
)
def test_stack_probe_direct_inspect_failure_states_are_unsatisfied(
    monkeypatch, tmp_path, state, expected
):
    """Absent, malformed, unhealthy, and failed containers never satisfy a stack ref."""
    from ciu import procutil

    if state is None:
        result = SimpleNamespace(returncode=1, stdout="")
    else:
        result = SimpleNamespace(returncode=0, stdout=state)
    monkeypatch.setattr(procutil, "docker", lambda *args, **kwargs: result)

    outcome = provisioning.probe_ref("stack:db:healthy", {}, tmp_path)

    assert outcome.satisfied is False
    assert expected in outcome.reason
