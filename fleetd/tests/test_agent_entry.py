import pytest

from fleetd.agent_entry import DEFAULT_AGENT_STATE_DIR, _parser, agent_semver
from fleetd.contracts import ContractError


def test_agent_entry_converts_supported_distribution_versions_to_semver():
    assert agent_semver("1.2.3") == "1.2.3"
    assert agent_semver("0.1.1.dev810+g9b3a9dd46") == "0.1.1-dev.810+g9b3a9dd46"
    assert agent_semver("1.2.3rc4") == "1.2.3-rc.4"

    with pytest.raises(ContractError, match="cannot be represented as SemVer"):
        agent_semver("1.2.3.unknown")


def test_agent_entry_selects_path_or_inherited_listener_and_declares_state_dir():
    path_args = _parser().parse_args(["serve", "--socket-path", "/run/fleetd-agent/agent.sock"])
    fd_args = _parser().parse_args(["serve", "--listen-fd", "3"])

    assert path_args.socket_path.as_posix() == "/run/fleetd-agent/agent.sock"
    assert fd_args.listen_fd == 3
    assert path_args.state_dir == DEFAULT_AGENT_STATE_DIR
