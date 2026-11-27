import math
import os
import socket
import struct
from pathlib import Path

import pytest

from fleetd import agent_policy
from fleetd.admission import AdmissionError, ExitCode
from fleetd.agent_policy import load_agent_policy
from fleetd.contracts import ContractError
from test_agent_protocol import _policy


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
lease_reconcile_interval_s = 30
lease_bind_timeout_s = 10
max_connections = 4
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
    assert policy.lease_reconcile_interval_s == 30
    assert policy.lease_bind_timeout_s == 10
    assert policy.max_connections == 4
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
        ("lease_reconcile_interval_s = 30", "lease_reconcile_interval_s = 0", "positive number"),
        ("lease_reconcile_interval_s = 30", "lease_reconcile_interval_s = true", "positive number"),
        ("lease_bind_timeout_s = 10", "lease_bind_timeout_s = 0", "positive number"),
        ("lease_bind_timeout_s = 10", "lease_bind_timeout_s = true", "positive number"),
        ("max_connections = 4", "max_connections = 0", "positive integer"),
        ("max_connections = 4", "max_connections = true", "positive integer"),
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

    path.write_text(_policy_text(tmp_path / "checkouts").replace(
        "lease_reconcile_interval_s = 30\n", "",
    ), encoding="utf-8")
    with pytest.raises(ContractError, match="missing key.*lease_reconcile_interval_s"):
        load_agent_policy(path)

    path.write_text(_policy_text(tmp_path / "checkouts").replace(
        "lease_bind_timeout_s = 10\n", "",
    ), encoding="utf-8")
    with pytest.raises(ContractError, match="missing key.*lease_bind_timeout_s"):
        load_agent_policy(path)

    path.write_text(_policy_text(tmp_path / "checkouts").replace("max_connections = 4\n", ""), encoding="utf-8")
    with pytest.raises(ContractError, match="missing key.*max_connections"):
        load_agent_policy(path)


def test_policy_object_and_scalar_validators_reject_invalid_shapes():
    with pytest.raises(ContractError, match="expected table"):
        agent_policy._object([], "table", {"required"})
    with pytest.raises(ContractError, match="unknown key"):
        agent_policy._object({"required": 1, "extra": 2}, "table", {"required"})
    with pytest.raises(ContractError, match="missing key"):
        agent_policy._object({}, "table", {"required"})

    for value in (None, "", "  ", "bad\0name"):
        with pytest.raises(ContractError, match="non-empty string"):
            agent_policy._string(value, "value")
    with pytest.raises(ContractError, match="safe identifier"):
        agent_policy._safe_name("unsafe/name", "group")

    for value in (True, "1", 0, -1, math.inf, math.nan, 10**400):
        with pytest.raises(ContractError, match="finite positive number"):
            agent_policy._positive_number(value, "number")
    for value in (True, 0, -1):
        with pytest.raises(ContractError, match="positive integer"):
            agent_policy._positive_integer(value, "integer")
    for value in (True, -1):
        with pytest.raises(ContractError, match="non-negative integer"):
            agent_policy._nonnegative_int(value, "integer")

    for value in ("relative/path", "/", "/root/../outside"):
        with pytest.raises(ContractError, match="absolute path without"):
            agent_policy._absolute_path(value, "root")


def test_policy_string_and_argv_allowlists_reject_duplicates_and_bad_values():
    with pytest.raises(ContractError, match="array of strings"):
        agent_policy._string_set("not-an-array", "clients")
    with pytest.raises(ContractError, match="duplicate value"):
        agent_policy._string_set(["fleetd", "fleetd"], "clients")
    with pytest.raises(ContractError, match="non-empty string"):
        agent_policy._string_set([""], "clients")

    with pytest.raises(ContractError, match="array of argv arrays"):
        agent_policy._argv_set({}, "argv")
    with pytest.raises(ContractError, match="non-empty argv array"):
        agent_policy._argv_set([[]], "argv")
    with pytest.raises(ContractError, match="non-empty string"):
        agent_policy._argv_set([["ok", ""]], "argv")
    with pytest.raises(ContractError, match="duplicate argv entry"):
        agent_policy._argv_set([["status"], ["status"]], "argv")
    assert agent_policy._argv_set([["status"]], "argv") == frozenset({("status",)})


def test_policy_identity_tables_reject_bad_rows_duplicates_and_prefixes():
    with pytest.raises(ContractError, match="expected array of tables"):
        agent_policy._identities({})
    with pytest.raises(ContractError, match="expected table"):
        agent_policy._identities(["peer"])
    with pytest.raises(ContractError, match="expected 'peer' or 'forced-command'"):
        agent_policy._identities([{"transport": "unknown"}])

    peer = {
        "transport": "peer", "principal": "controller", "group": "controller",
        "uid": 1, "gid": 2,
    }
    duplicate_peer = {**peer, "principal": "other", "group": "other"}
    with pytest.raises(ContractError, match="duplicate peer UID/GID"):
        agent_policy._identities([peer, duplicate_peer])

    forced = {"transport": "forced-command", "principal": "worker", "group": "controller"}
    with pytest.raises(ContractError, match="duplicate authenticated group"):
        agent_policy._identities([peer, forced])

    prefixed = {"transport": "forced-command", "principal": "worker", "group": "controller-dev"}
    with pytest.raises(ContractError, match="overlapping authenticated group prefixes"):
        agent_policy._identities([peer, prefixed])


def test_policy_identity_resolution_authenticates_only_transport_credentials(tmp_path):
    policy, _checkout = _policy(tmp_path)
    peer = policy.resolve_peer(1000, 100)
    assert peer.principal == "controller-devcontainer"
    assert policy.resolve_forced_group("worker").principal == "worker"

    class PeerSocket:
        def getsockopt(self, _level, _option, size):
            assert size == struct.calcsize("3i")
            return struct.pack("3i", 55, 1000, 100)

    assert policy.resolve_socket_peer(PeerSocket()) == peer
    with pytest.raises(AdmissionError, match="unknown local socket peer") as unknown:
        policy.resolve_peer(9999, 100)
    assert unknown.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT
    with pytest.raises(AdmissionError, match="unknown forced-command group") as forced:
        policy.resolve_forced_group("missing")
    assert forced.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT


def test_socket_peer_refuses_truncated_or_unreadable_kernel_credentials(tmp_path):
    policy, _checkout = _policy(tmp_path)

    class PeerSocket:
        def getsockopt(self, *_args):
            return b"short"

    with pytest.raises(AdmissionError, match="truncated credential") as truncated:
        policy.resolve_socket_peer(PeerSocket())
    assert truncated.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT

    class BrokenPeerSocket:
        def getsockopt(self, *_args):
            raise OSError("peer credentials unavailable")

    with pytest.raises(AdmissionError, match="cannot authenticate local socket peer") as unreadable:
        policy.resolve_socket_peer(BrokenPeerSocket())
    assert unreadable.value.exit_code == ExitCode.IDENTITY_OR_TRANSPORT


def test_policy_file_read_utf8_and_toml_errors_are_reported(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    path = tmp_path / "agent.toml"
    assert agent_policy.load_agent_policy(path) == policy
    real_read_text = Path.read_text

    def unreadable(target, *args, **kwargs):
        if target == path:
            raise OSError("read denied")
        return real_read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(ContractError, match="cannot read required config"):
        agent_policy.load_agent_policy(path)

    monkeypatch.setattr(
        Path, "read_text",
        lambda target, *args, **kwargs: (_ for _ in ()).throw(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"))
        if target == path else real_read_text(target, *args, **kwargs),
    )
    with pytest.raises(ContractError, match="config is not UTF-8 text"):
        agent_policy.load_agent_policy(path)

    monkeypatch.setattr(Path, "read_text", real_read_text)
    path.write_text("schema_version = [", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid TOML"):
        agent_policy.load_agent_policy(path)


@pytest.mark.parametrize(
    ("replace", "message"),
    [
        (lambda text: text.replace("schema_version = 1", "schema_version = true"), "expected integer 1"),
        (lambda text: text.replace('vbpub = ', '"bad/repo" = '), "invalid repo name"),
        (lambda text: text.replace('vbpub = "/', 'vbpub = "relative'), "absolute path without"),
        (
            lambda text: text.replace("[repo_roots]", "repo_roots = []").replace("vbpub = ", "# vbpub = "),
            "repo_roots: expected table",
        ),
        (
            lambda text: text.replace("[repo_roots]", "env_values = []\n\n[repo_roots]").replace(
                "[env_values]", "# [env_values]",
            ).replace("CI = ", "# CI = "),
            "env_values: expected table",
        ),
        (lambda text: text.replace("CI = [\"1\"]", "bad-name = [\"1\"]"), "invalid environment name"),
        (lambda text: text.replace("requested_by_clients = [\"fleetd\"]", "requested_by_clients = 1"), "array of strings"),
    ],
)
def test_policy_document_validation_rejects_closed_schema_and_path_errors(tmp_path, replace, message):
    _policy(tmp_path)
    path = tmp_path / "agent.toml"
    original = path.read_text(encoding="utf-8")
    path.write_text(replace(original), encoding="utf-8")
    with pytest.raises(ContractError, match=message):
        agent_policy.load_agent_policy(path)
