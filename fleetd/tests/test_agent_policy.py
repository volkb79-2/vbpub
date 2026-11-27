import os
import socket
from pathlib import Path

import pytest

from fleetd.admission import AdmissionError, ExitCode
from fleetd.agent_policy import load_agent_policy
from fleetd.contracts import ContractError


def _policy_text(repo_root: Path, identities: str = "") -> str:
    default_identities = '''[[identities]]
transport = "peer"
principal = "controller-devcontainer"
group = "controller"
uid = 1000
gid = 100

[[identities]]
transport = "forced-command"
principal = "worker"
group = "worker"
'''
    return f'''schema_version = 1
idle_connection_s = 20
query_timeout_s = 5
requested_by_clients = ["fleetd"]
extra_argv = [["--verbose"]]
dev_argv = [["status"]]

[repo_roots]
vbpub = "{repo_root}"

[env_values]
CI = ["1", "true"]

{identities or default_identities}'''


def _load(tmp_path, identities=""):
    path = tmp_path / "agent.toml"
    root = tmp_path / "checkouts"
    path.write_text(_policy_text(root, identities), encoding="utf-8")
    return load_agent_policy(path)


def test_agent_policy_loads_closed_identity_and_allowlist_config(tmp_path):
    policy = _load(tmp_path)

    assert policy.idle_connection_s == 20
    assert policy.query_timeout_s == 5
    assert policy.repo_roots["vbpub"] == tmp_path / "checkouts"
    assert policy.requested_by_clients == frozenset({"fleetd"})
    assert policy.extra_argv == frozenset({("--verbose",)})
    assert policy.dev_argv == frozenset({("status",)})
    assert policy.env_values["CI"] == frozenset({"1", "true"})
    assert policy.resolve_peer(1000, 100) == policy.identities[0]
    assert policy.resolve_forced_group("worker") == policy.identities[1]


def test_unknown_transport_identity_is_exit_three(tmp_path):
    policy = _load(tmp_path)

    with pytest.raises(AdmissionError) as peer_error:
        policy.resolve_peer(1001, 100)
    assert peer_error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    with pytest.raises(AdmissionError) as group_error:
        policy.resolve_forced_group("missing")
    assert group_error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT


def test_socket_identity_comes_from_peer_credentials(tmp_path):
    policy_text = _policy_text(tmp_path / "checkouts").replace(
        "uid = 1000", f"uid = {os.getuid()}",
    ).replace("gid = 100", f"gid = {os.getgid()}")
    path = tmp_path / "agent.toml"
    path.write_text(policy_text, encoding="utf-8")
    policy = load_agent_policy(path)
    client, server = socket.socketpair()
    try:
        identity = policy.resolve_socket_peer(server)
    finally:
        client.close()
        server.close()
    assert identity.principal == "controller-devcontainer"
    assert identity.group == "controller"
    assert identity.transport == "peer"


def test_socket_credential_failure_is_exit_three(tmp_path):
    policy = _load(tmp_path)
    with pytest.raises(AdmissionError) as error:
        policy.resolve_socket_peer(object())
    assert error.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT


def test_agent_policy_rejects_overlapping_group_prefixes(tmp_path):
    identities = '''[[identities]]
transport = "forced-command"
principal = "ops"
group = "ops"

[[identities]]
transport = "forced-command"
principal = "ops-ci"
group = "ops-ci"
'''
    path = tmp_path / "agent.toml"
    path.write_text(_policy_text(tmp_path / "checkouts", identities), encoding="utf-8")

    with pytest.raises(ContractError, match="overlapping authenticated group prefixes"):
        load_agent_policy(path)


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("idle_connection_s = 20", "idle_connection_s = 0", "positive number"),
        ("idle_connection_s = 20", "idle_connection_s = true", "positive number"),
        ("query_timeout_s = 5", "query_timeout_s = 0", "positive number"),
        ("group = \"controller\"", "group = \"../controller\"", "safe identifier"),
        ("uid = 1000", "uid = true", "non-negative integer"),
        ("CI = [\"1\", \"true\"]", "CI = [\"1\", \"1\"]", "duplicate value"),
        ("transport = \"peer\"", "transport = \"other\"", "expected 'peer' or 'forced-command'"),
        ("schema_version = 1", "schema_version = 2", "expected integer 1"),
    ],
)
def test_agent_policy_rejects_invalid_or_duplicate_values(tmp_path, old, new, message):
    path = tmp_path / "agent.toml"
    path.write_text(_policy_text(tmp_path / "checkouts").replace(old, new), encoding="utf-8")

    with pytest.raises(ContractError, match=message):
        load_agent_policy(path)


def test_agent_policy_rejects_unknown_keys_and_unreadable_files(tmp_path):
    path = tmp_path / "agent.toml"
    path.write_text(_policy_text(tmp_path / "checkouts").replace(
        "schema_version = 1", "schema_version = 1\nunknown = true",
    ), encoding="utf-8")
    with pytest.raises(ContractError, match="unknown key.*unknown"):
        load_agent_policy(path)

    with pytest.raises(ContractError, match="cannot read required config"):
        load_agent_policy(tmp_path / "missing.toml")
