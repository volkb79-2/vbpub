import os
import socket
import threading
from types import SimpleNamespace

import pytest

from fleetd import agent_entry
from fleetd.admission import AdmissionError, ExitCode
from fleetd.agent_entry import DEFAULT_AGENT_STATE_DIR, _parser, agent_semver
from fleetd.agent_protocol import read_frame, write_frame
from fleetd.contracts import ContractError
from test_agent_verbs import THREAD_WATCHDOG_S, _service


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


def test_shipped_cli_probe_omits_lease_and_lease_refuses_without_c3b_providers(tmp_path, monkeypatch):
    service, _identity, _checkout = _service(
        tmp_path, peer_credentials=(os.geteuid(), os.getegid()),
    )
    policy = service.policy
    socket_path = tmp_path / "agent.sock"
    server_ready = threading.Event()
    cli_finished = threading.Event()
    servers = []
    original_on_path = agent_entry.AgentServer.on_path

    def capture_server(path, configured_service, configured_policy):
        server = original_on_path(path, configured_service, configured_policy)
        servers.append(server)
        server_ready.set()
        return server

    monkeypatch.setattr(agent_entry, "_service", lambda _policy_path, _state_dir: (service, policy))
    monkeypatch.setattr(agent_entry.AgentServer, "on_path", staticmethod(capture_server))
    results = []

    def run_cli():
        try:
            results.append(agent_entry.main([
                "serve", "--socket-path", str(socket_path),
                "--policy", str(tmp_path / "agent.toml"),
                "--state-dir", str(tmp_path / "cli-state"),
            ]))
        finally:
            cli_finished.set()

    # Daemon plus close-in-finally: a failed assertion must not leave a serving thread that hangs pytest at exit.
    cli = threading.Thread(target=run_cli, daemon=True)
    cli.start()
    try:
        assert server_ready.wait(THREAD_WATCHDOG_S), "CLI server did not start before the failure watchdog"

        probe_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe_client.connect(str(socket_path))
        write_frame(probe_client, {"verb": "probe"})
        probe_client.shutdown(socket.SHUT_WR)
        probe = read_frame(probe_client, THREAD_WATCHDOG_S)
        probe_client.close()
        assert "lease" not in probe["capabilities"]

        lease_client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        lease_client.connect(str(socket_path))
        write_frame(lease_client, {
            "verb": "lease", "lease_id": "controller-cli-lease", "container_name": "rg-test",
            "footprint": "tiny", "planned_cores": 1, "priority": "gate",
        })
        refusal = read_frame(lease_client, THREAD_WATCHDOG_S)
        lease_client.close()
        assert "exit_code" not in refusal and "reason" not in refusal
        assert refusal["error"]["exit_code"] == 2
        assert "object inspector" in refusal["error"]["reason"]
    finally:
        for server in servers:
            server.close()

    assert cli_finished.wait(THREAD_WATCHDOG_S), "CLI did not stop before the failure watchdog"
    cli.join(timeout=THREAD_WATCHDOG_S)
    assert not cli.is_alive(), "CLI worker remained alive after close"
    assert results == [0]


def test_agent_semver_converts_all_supported_prerelease_and_postrelease_forms():
    assert agent_semver("1.2.3a4") == "1.2.3-alpha.4"
    assert agent_semver("1.2.3b5") == "1.2.3-beta.5"
    assert agent_semver("1.2.3.post6") == "1.2.3-post.6"
    assert agent_semver("1.2.3+local.7") == "1.2.3+local.7"


def test_agent_semver_handles_an_empty_optional_pep440_suffix_from_parser(monkeypatch):
    fields = {
        "major": "2", "minor": "4", "patch": "6",
        "dev": None, "alpha": None, "beta": None, "rc": None, "post": None, "local": None,
    }

    class Match:
        def __getitem__(self, name):
            return fields[name]

    class Parser:
        def __init__(self, result):
            self.result = result

        def fullmatch(self, _value):
            return self.result

    monkeypatch.setattr(agent_entry, "_VERSION_RE", Parser(None))
    monkeypatch.setattr(agent_entry, "_PEP440_RE", Parser(Match()))
    assert agent_semver("injected-version-shape") == "2.4.6"


def test_installed_agent_version_requires_distribution_metadata(monkeypatch):
    monkeypatch.setattr(
        agent_entry.importlib.metadata, "version",
        lambda _name: (_ for _ in ()).throw(agent_entry.importlib.metadata.PackageNotFoundError("fleetd")),
    )
    with pytest.raises(ContractError, match="requires the packaged fleetd distribution metadata"):
        agent_entry.installed_agent_version()


def test_service_loads_policy_and_converts_the_packaged_version(tmp_path, monkeypatch):
    policy = object()
    service = object()
    calls = {}

    def load_policy(path):
        calls["policy_path"] = path
        return policy

    monkeypatch.setattr(agent_entry, "load_agent_policy", load_policy)
    monkeypatch.setattr(agent_entry.importlib.metadata, "version", lambda _name: "1.2.3.dev4+local")

    def build_service(state_dir, loaded_policy, facts_provider, *, agent_version):
        calls["service_args"] = state_dir, loaded_policy, facts_provider, agent_version
        return service

    monkeypatch.setattr(agent_entry, "AgentVerbService", build_service)
    built, loaded = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")
    assert built is service
    assert loaded is policy
    assert calls["service_args"] == (
        tmp_path / "state", policy, agent_entry.collect_host_facts, "1.2.3-dev.4+local",
    )


def test_service_wires_executor_and_reads_guard_presence_from_host_facts(tmp_path, monkeypatch):
    policy = object()

    class Executor:
        def __init__(self, state_dir, loaded_policy, *, policy_path, guard_presence):
            self.state_dir = state_dir
            self.policy = loaded_policy
            self.policy_path = policy_path
            self.guard_presence = guard_presence

    class Service:
        def __init__(self, state_dir, loaded_policy, facts_provider, *, agent_version, executor):
            self.state_dir = state_dir
            self.policy = loaded_policy
            self.facts_provider = facts_provider
            self.agent_version = agent_version
            self.executor = executor

    monkeypatch.setattr(agent_entry, "load_agent_policy", lambda _path: policy)
    monkeypatch.setattr(agent_entry, "HostJobExecutor", Executor)
    monkeypatch.setattr(agent_entry, "AgentVerbService", Service)
    monkeypatch.setattr(agent_entry, "collect_host_facts", lambda: {
        "guard": {"guard_present": {"v": False}},
    })
    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")

    service, loaded = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")

    assert loaded is policy
    assert isinstance(service.executor, Executor)
    assert service.executor.policy_path == tmp_path / "agent.toml"
    assert service.executor.guard_presence() is False


def test_service_guard_presence_stays_unknown_when_host_facts_fail(tmp_path, monkeypatch):
    policy = object()

    class Executor:
        def __init__(self, *_args, guard_presence, **_kwargs):
            self.guard_presence = guard_presence

    class Service:
        def __init__(self, *_args, executor, **_kwargs):
            self.executor = executor

    monkeypatch.setattr(agent_entry, "load_agent_policy", lambda _path: policy)
    monkeypatch.setattr(agent_entry, "HostJobExecutor", Executor)
    monkeypatch.setattr(agent_entry, "AgentVerbService", Service)
    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")
    monkeypatch.setattr(agent_entry, "collect_host_facts", lambda: (_ for _ in ()).throw(OSError("facts unavailable")))

    service, _loaded = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")

    assert service.executor.guard_presence() is None


def test_main_serves_path_and_inherited_listeners_and_closes_both(tmp_path, monkeypatch):
    service = object()
    policy = object()
    monkeypatch.setattr(agent_entry, "_service", lambda *_args: (service, policy))
    instances = []

    class Server:
        def __init__(self):
            self.closed = False

        def serve_forever(self):
            return None

        def close(self):
            self.closed = True

    def new_server(*_args):
        result = Server()
        instances.append(result)
        return result

    monkeypatch.setattr(agent_entry.AgentServer, "on_path", staticmethod(new_server))
    monkeypatch.setattr(agent_entry.AgentServer, "from_fd", staticmethod(new_server))
    assert agent_entry.main(["serve", "--socket-path", str(tmp_path / "agent.sock")]) == ExitCode.PASS
    assert agent_entry.main(["serve", "--listen-fd", "3"]) == ExitCode.PASS
    assert len(instances) == 2
    assert all(server.closed for server in instances)


def test_main_forced_command_returns_transport_result_and_maps_startup_errors(tmp_path, monkeypatch, capsys):
    service = object()
    policy = object()
    monkeypatch.setattr(agent_entry, "_service", lambda *_args: (service, policy))
    monkeypatch.setattr(agent_entry, "serve_forced_command", lambda *_args: 23)
    assert agent_entry.main(["forced-command", "--group", "worker"]) == 23

    monkeypatch.setattr(
        agent_entry, "_service",
        lambda *_args: (_ for _ in ()).throw(AdmissionError(ExitCode.QUEUED_WITHDRAWN, "facts unavailable")),
    )
    assert agent_entry.main(["forced-command", "--group", "worker"]) == ExitCode.QUEUED_WITHDRAWN
    assert "facts unavailable" in capsys.readouterr().err

    monkeypatch.setattr(
        agent_entry, "_service",
        lambda *_args: (_ for _ in ()).throw(ContractError("bad configuration")),
    )
    assert agent_entry.main(["forced-command", "--group", "worker"]) == ExitCode.REFUSAL
    assert "bad configuration" in capsys.readouterr().err

    monkeypatch.setattr(
        agent_entry, "_service",
        lambda *_args: (_ for _ in ()).throw(OSError("state unavailable")),
    )
    assert agent_entry.main(["forced-command", "--group", "worker"]) == ExitCode.RUNNER_FAILURE
    assert "state unavailable" in capsys.readouterr().err


def test_main_closes_server_when_serve_fails(tmp_path, monkeypatch, capsys):
    server = SimpleNamespace(closed=False)
    server.serve_forever = lambda: (_ for _ in ()).throw(OSError("listen failed"))
    server.close = lambda: setattr(server, "closed", True)
    monkeypatch.setattr(agent_entry, "_service", lambda *_args: (object(), object()))
    monkeypatch.setattr(agent_entry.AgentServer, "on_path", staticmethod(lambda *_args: server))
    assert agent_entry.main(["serve", "--socket-path", str(tmp_path / "agent.sock")]) == ExitCode.RUNNER_FAILURE
    assert server.closed is True
    assert "listen failed" in capsys.readouterr().err
