"""Behavioral closure for the surviving ciu-v7.24.1 R2 candidates.

Docker command contracts use a real executable on PATH, so procutil's check
and capture options affect returned exit status and user-visible output.
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, engine, oneshot  # noqa: E402


_CONTAINER_ID = "a" * 64
_FAKE_DOCKER = r'''#!{python}
import json, pathlib, sys
argv = sys.argv[1:]
path = pathlib.Path({rules!r})
with path.open(encoding="utf-8") as stream:
    rules = json.load(stream)
for rule in rules:
    prefix = rule["prefix"]
    if argv[:len(prefix)] == prefix:
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("rc", 0))
sys.exit(91)
'''


def _docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rules: list[dict]):
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    rule_path = tmp_path / "docker-rules.json"
    rule_path.write_text(json.dumps(rules), encoding="utf-8")
    executable = bindir / "docker"
    executable.write_text(
        _FAKE_DOCKER.format(python=sys.executable, rules=str(rule_path)),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")

    def configure(updated: list[dict]) -> None:
        rule_path.write_text(json.dumps(updated), encoding="utf-8")

    return configure


def _install_session_observer_docker(tmp_path: Path, *, config: dict, network: str) -> Path:
    bindir = tmp_path / "session-docker-bin"
    bindir.mkdir()
    config_path = tmp_path / "session-compose.json"
    state_path = tmp_path / "session-container.json"
    session_path = tmp_path / "compose-session.txt"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    executable = bindir / "docker"
    script = r'''#!@PYTHON@
import json, os, pathlib, sys
argv = sys.argv[1:]
config = pathlib.Path("@CONFIG@")
state = pathlib.Path("@STATE@")
session = pathlib.Path("@SESSION@")
network = "@NETWORK@"
container_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
if argv[:1] == ["compose"] and "config" in argv:
    sys.stdout.write(config.read_text(encoding="utf-8"))
    raise SystemExit(0)
if argv[:1] == ["compose"] and "run" in argv:
    name = argv[argv.index("--name") + 1]
    labels = {}
    for index, token in enumerate(argv[:-1]):
        if token in {"-l", "--label"}:
            key, _, value = argv[index + 1].partition("=")
            labels[key] = value
    labels.update({
        "com.docker.compose.project": "demo-runner",
        "com.docker.compose.oneoff": "True",
        "com.docker.compose.project.working_dir": str(pathlib.Path.cwd()),
        "ciu.run": "1",
    })
    document = {
        "Id": container_id, "Name": "/" + name, "Image": "sha256:imageid",
        "Config": {"Image": "busybox:1", "User": None, "Labels": labels},
        "HostConfig": {"CgroupParent": None, "CgroupnsMode": "private"},
        "NetworkSettings": {"Networks": {network: {}}},
        "State": {
            "Status": "exited", "ExitCode": 0, "StartedAt": "2026-11-05T00:00:00Z",
            "FinishedAt": "2026-11-05T00:00:01Z", "OOMKilled": False,
        },
    }
    state.write_text(json.dumps([document]), encoding="utf-8")
    session.write_text(str(os.getsid(0)), encoding="ascii")
    raise SystemExit(0)
if argv[:3] == ["network", "inspect", network]:
    sys.stdout.write(json.dumps([{"Name": network, "Labels": {}}]))
    raise SystemExit(0)
if argv[:3] == ["inspect", "--type", "container"]:
    if state.exists():
        document = json.loads(state.read_text(encoding="utf-8"))[0]
        if argv[3] in {document["Id"], document["Name"].lstrip("/")}:
            sys.stdout.write(json.dumps([document]))
            raise SystemExit(0)
    raise SystemExit(1)
if argv[:1] == ["ps"]:
    if state.exists():
        sys.stdout.write(container_id + "\n")
    raise SystemExit(0)
if argv[:1] == ["rm"]:
    raise SystemExit(0)
raise SystemExit(91)
'''
    for needle, replacement in (
        ("@PYTHON@", sys.executable),
        ("@CONFIG@", str(config_path)),
        ("@STATE@", str(state_path)),
        ("@SESSION@", str(session_path)),
        ("@NETWORK@", network),
    ):
        script = script.replace(needle, replacement)
    executable.write_text(script, encoding="utf-8")
    executable.chmod(0o755)
    return bindir


def _prepared(root: Path):
    return SimpleNamespace(
        working_dir=root,
        repo_root=root,
        project="demo-runner",
        file_args=[],
        compose_env={"PATH": os.environ.get("PATH", "")},
        rendered_compose="services: {}\n",
        ownership_path=None,
        config={},
        secret_values=frozenset(),
    )


def _config(*, labels=None, volumes=None, networks=None):
    service = {
        "image": "busybox:1",
        "labels": labels or {},
        "volumes": volumes or [],
        "networks": networks or {},
    }
    document = {"services": {"worker": service}}
    if networks:
        document["networks"] = networks
    return document


def test_engine_prepared_stack_is_immutable_and_keeps_secret_values_private():
    first = engine.PreparedStack(
        working_dir=Path("/repo/stack"), repo_root=Path("/repo"), project="p",
        file_args=[], compose_env={}, rendered_compose="", ownership_path=None,
        config={}, secret_values=frozenset({"secret-one"}),
    )
    second = dataclasses.replace(first, secret_values=frozenset({"secret-two"}))

    assert "secret-one" not in repr(first)
    assert "secret-two" not in repr(second)
    assert first == second
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.project = "changed"  # type: ignore[misc]


def test_run_plan_accepts_gid_zero_and_keeps_missing_read_only_false(
    tmp_path, monkeypatch,
):
    prepared = _prepared(tmp_path)
    document = _config(volumes=[{
        "type": "bind", "source": "/data", "target": "/work",
    }])

    def compose_config(_prepared, *, extension_path=None):
        return document

    monkeypatch.setattr(oneshot, "_compose_config", compose_config)
    valid = oneshot.validate_extension_document(
        {"services": {"worker": {"group_add": [0]}}},
        service_name="worker", base_service={}, secret_values=frozenset(),
    )
    plan = oneshot.build_run_plan(prepared, stack="tools/runner", service="worker")

    assert valid["services"]["worker"]["group_add"] == ["0"]
    assert plan["mounts"] == [{
        "type": "bind", "source": "/data", "target": "/work", "ro": False,
    }]
    assert plan["extend_accepted"] is False


def test_extension_observer_requires_a_bind_to_the_exact_socket_directory():
    extension = {
        "services": {"worker": {"volumes": [{
            "type": "bind", "source": "/other/socket-dir", "target": "/observer",
            "read_only": True,
        }]}},
        "x-ciu-run": {"observer-socket-dir": "/run/observer"},
    }
    with pytest.raises(ValueError, match="one matching read-only bind"):
        oneshot.validate_extension_document(
            extension, service_name="worker", base_service={},
            secret_values=frozenset(),
        )


def test_plan_without_extension_reports_that_no_extension_was_accepted(
    tmp_path, monkeypatch,
):
    document = _config()
    monkeypatch.setattr(
        oneshot, "_compose_config",
        lambda _prepared, *, extension_path=None: document,
    )

    plan = oneshot.build_run_plan(
        _prepared(tmp_path), stack="tools/runner", service="worker",
    )

    assert plan["extend_accepted"] is False


def test_started_at_ignores_truthy_non_string_labels():
    assert oneshot._started_at({"ciu.run.started": 17}) is None


def test_foreground_compose_runs_in_its_own_process_session(tmp_path, monkeypatch):
    network_name = "demo-runner_instance-net"
    config = _config(networks={
        "instance-net": {"name": network_name, "external": True},
    })
    bindir = _install_session_observer_docker(
        tmp_path, config=config, network=network_name,
    )
    prepared = _prepared(tmp_path)
    prepared.compose_env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
    }

    def prepare(_root, _selector, *, profiles, skip_hooks):
        return prepared, "tools/runner", "worker"

    monkeypatch.setattr(oneshot, "_prepare_run_stack", prepare)
    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda _root: None)
    monkeypatch.setenv(
        "PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
    )

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["true"], name="session-observer",
        owner_pid=os.getpid(),
    )

    child_session = int((tmp_path / "compose-session.txt").read_text(encoding="ascii"))
    assert result == 0
    assert child_session != os.getsid(os.getpid())


def test_event_writer_starts_live_and_emits_a_record(tmp_path):
    path = tmp_path / "events.jsonl"
    writer = oneshot._EventWriter(path=path)
    writer.write({"event": "created"})
    writer.close()

    assert json.loads(path.read_text(encoding="utf-8")) == {"event": "created"}


def test_event_writer_leaves_an_inherited_descriptor_open_after_close():
    read_fd, write_fd = os.pipe()
    writer = oneshot._EventWriter(fd=write_fd)
    try:
        writer.close()
        os.write(write_fd, b"still-owned-by-caller")
        assert os.read(read_fd, 64) == b"still-owned-by-caller"
    finally:
        os.close(read_fd)
        try:
            os.close(write_fd)
        except OSError:
            pass


def test_event_writer_treats_zero_byte_write_as_a_bounded_failure(monkeypatch, capsys):
    calls = 0

    def zero_then_guard(fd, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0
        raise AssertionError("a zero-byte write must stop the retry loop")

    writer = oneshot._EventWriter()
    writer.fd = 99
    monkeypatch.setattr(oneshot.os, "write", zero_then_guard)

    writer.write({"event": "created"})
    writer.write({"event": "exited"})

    assert calls == 1
    assert "short write" in capsys.readouterr().err


class _BufferedText:
    def __init__(self):
        self.pending = ""
        self.visible = ""

    def write(self, value):
        self.pending += value
        return len(value)

    def flush(self):
        self.visible += self.pending
        self.pending = ""


def test_event_sink_failure_is_visible_while_the_run_continues(monkeypatch):
    stream = _BufferedText()
    monkeypatch.setattr(oneshot.sys, "stderr", stream)
    calls = 0

    def fail_write(fd, payload):
        nonlocal calls
        calls += 1
        assert fd == 99
        assert payload
        raise OSError("observer pipe closed")

    monkeypatch.setattr(oneshot.os, "write", fail_write)
    writer = oneshot._EventWriter()
    writer.fd = 99

    writer.write({"event": "launched"})
    writer.write({"event": "exited"})

    assert calls == 1
    assert "observer pipe closed" in stream.visible
    assert stream.visible.count("event sink write failed") == 1


def test_event_writer_accepts_descriptor_three_for_run_cli(tmp_path, monkeypatch):
    read_fd, write_fd = os.pipe()
    read_copy = os.dup(read_fd)
    write_copy = os.dup(write_fd)
    os.close(read_fd)
    os.close(write_fd)
    saved_three = None
    try:
        try:
            saved_three = os.dup(3)
        except OSError:
            pass
        os.dup2(write_copy, 3)

        prepared = _prepared(tmp_path)

        def prepare(_root, _selector, *, profiles, skip_hooks):
            return prepared, "tools/runner", "worker"

        def plan(prepared_stack, *, stack, service, name, extend, labels, skip_hooks):
            return {"cgroup_parent": None}

        def foreground(
            prepared_stack, *, plan, stack, service, argv, name, run_id, labels,
            env_names, extend, event_writer,
        ):
            event_writer.write({"event": "accepted", "name": name})
            return 0

        monkeypatch.setattr(oneshot, "_prepare_run_stack", prepare)
        monkeypatch.setattr(oneshot, "build_run_plan", plan)
        monkeypatch.setattr(oneshot, "_run_foreground", foreground)
        result = oneshot.run_cli(
            tmp_path, "tools/runner:worker", ["true"], events_fd=3,
            name="run-fd-three", owner_pid=os.getpid(),
        )
        os.close(3)
        if saved_three is not None:
            os.dup2(saved_three, 3)
        payload = os.read(read_copy, 4096)
    finally:
        if saved_three is None:
            try:
                os.close(3)
            except OSError:
                pass
        elif os.fstat(3).st_ino != os.fstat(saved_three).st_ino:
            os.dup2(saved_three, 3)
        if saved_three is not None:
            os.close(saved_three)
        os.close(read_copy)
        os.close(write_copy)

    assert result == 0
    assert json.loads(payload) == {"event": "accepted", "name": "run-fd-three"}


def test_owner_identity_accepts_pid_one():
    identity = oneshot._owner_identity(1)

    assert identity["pid"] == "1"
    assert int(identity["start"]) >= 0
    assert int(identity["pidns"]) > 0


def test_docker_command_results_are_checked_and_captured_at_behavior_boundaries(
    tmp_path, monkeypatch, capfd,
):
    network_name = "demo-runner_instance-net"
    config = _config(
        networks={"instance-net": {}},
    )
    config["networks"] = {"instance-net": {"name": network_name}}
    inspected_network = json.dumps([{
        "Name": network_name,
        "Labels": {
            "com.docker.compose.project": "demo-runner",
            "com.docker.compose.network": "instance-net",
        },
    }])
    container_doc = [{
        "Id": _CONTAINER_ID,
        "Name": "/run-one",
        "Config": {"Labels": {
            "com.docker.compose.project": "demo-runner",
            "com.docker.compose.oneoff": "True",
            "com.docker.compose.project.working_dir": str(tmp_path.resolve()),
            "ciu.run": "1",
            "ciu.run.id": "run-id",
            "ciu.run.stack": "other/stack",
        }},
    }]
    configure = _docker(tmp_path, monkeypatch, [])
    prepared = _prepared(tmp_path)

    configure([{"prefix": ["compose"], "rc": 8, "stderr": "bad compose\n"}])
    with pytest.raises(RuntimeError, match=r"failed \(exit 8\)"):
        oneshot._compose_config(prepared)

    configure([{
        "prefix": ["compose"], "stdout": json.dumps(config),
    }])
    assert oneshot._compose_config(prepared) == config

    configure([{"prefix": ["inspect"], "rc": 1}])
    assert oneshot._docker_inspect("absent-container") is None

    configure([{
        "prefix": ["inspect"], "stdout": json.dumps(container_doc),
    }])
    assert oneshot._docker_inspect("run-one")["Id"] == _CONTAINER_ID

    configure([{"prefix": ["ps"], "stdout": _CONTAINER_ID + "\n"}])
    assert oneshot._owned_container_ids("run-id") == [_CONTAINER_ID]

    configure([{"prefix": ["ps"], "rc": 4, "stderr": "daemon unavailable\n"}])
    with pytest.raises(RuntimeError, match="failed while locating"):
        oneshot._owned_container_ids("run-id")

    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda _root: None)
    configure([
        {"prefix": ["compose"], "stdout": json.dumps(config)},
        {"prefix": ["network", "inspect"], "stdout": inspected_network},
    ])
    oneshot._verify_run_networks(prepared, "worker")

    for labels in (
        {"com.docker.compose.project": "someone-else", "com.docker.compose.network": "instance-net"},
        {"com.docker.compose.project": "demo-runner", "com.docker.compose.network": "someone-else"},
    ):
        configure([
            {"prefix": ["compose"], "stdout": json.dumps(config)},
            {"prefix": ["network", "inspect"], "stdout": json.dumps([{
                "Name": network_name, "Labels": labels,
            }])},
        ])
        with pytest.raises(ValueError, match="is not owned by Compose project"):
            oneshot._verify_run_networks(prepared, "worker")

    configure([
        {"prefix": ["compose"], "stdout": json.dumps(config)},
        {"prefix": ["network", "inspect"], "rc": 6},
    ])
    with pytest.raises(ValueError, match="required stack network .* is not present"):
        oneshot._verify_run_networks(prepared, "worker")

    configure([{"prefix": ["rm"], "stdout": "removed\n"}])
    assert oneshot._run_docker_rm(_CONTAINER_ID) is True
    assert capfd.readouterr().out == ""

    configure([{"prefix": ["rm"], "rc": 5, "stderr": "busy\n"}])
    assert oneshot._run_docker_rm(_CONTAINER_ID) is False

    configure([
        {"prefix": ["ps"], "stdout": _CONTAINER_ID + "\n"},
        {"prefix": ["inspect"], "stdout": json.dumps(container_doc)},
    ])
    monkeypatch.setattr(
        engine, "workspace_ownership_labels",
        lambda _root: {
            "ciu.instance": "instance-one", "ciu.repo-root": str(tmp_path.resolve()),
        },
    )
    container_doc[0]["Config"]["Labels"].update({
        "ciu.instance": "instance-one",
        "ciu.repo-root": str(tmp_path.resolve()),
    })
    with pytest.raises(engine.ComposeError, match="another checkout or stack"):
        engine.guard_clean_one_shots(
            "demo-runner", repo_root=tmp_path, stack_dir=tmp_path / "tools" / "runner",
        )

    configure([
        {"prefix": ["ps"], "stdout": _CONTAINER_ID + "\n"},
        {"prefix": ["inspect"], "rc": 3, "stderr": "container changed\n"},
    ])
    with pytest.raises(engine.ComposeError, match="changed during clean ownership inspection"):
        engine.guard_clean_one_shots(
            "demo-runner", repo_root=tmp_path, stack_dir=tmp_path / "tools" / "runner",
        )

    configure([{"prefix": ["ps"], "rc": 7, "stderr": "daemon unavailable\n"}])
    with pytest.raises(engine.ComposeError, match="Docker failed while guarding"):
        engine.guard_clean_one_shots(
            "demo-runner", repo_root=tmp_path, stack_dir=tmp_path / "tools" / "runner",
        )

    assert capfd.readouterr().out == ""


def test_owned_endpoint_being_removed_does_not_block_its_network(monkeypatch):
    owner = deploy._CleanOwner(instance_id="i", repo_root="/repo", is_instance=True)
    monkeypatch.setattr(deploy, "_network_foreign_reason", lambda _name, _owner: None)
    monkeypatch.setattr(deploy, "_network_endpoints", lambda _name: [{"name": "run-one"}])
    monkeypatch.setattr(
        deploy, "_endpoint_foreign_reason",
        lambda _name, _owner: "foreign endpoint",
    )

    removable, kept = deploy._split_foreign_endpoint_networks(
        ["network-one"], owner, removing=frozenset({"run-one"}),
    )

    assert removable == ["network-one"]
    assert kept == []


def test_global_only_clean_never_sweeps_tagged_stack_networks(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(deploy, "_is_worktree_instance", lambda _root: False)
    monkeypatch.setattr(deploy, "_workspace_identity_network", lambda _root: "")
    monkeypatch.setattr(deploy, "_list_stack_project_networks", lambda _projects: [])
    monkeypatch.setattr(
        deploy, "_tagged_project_networks",
        lambda _root, _config, _projects: (["shared-app-network"], []),
    )

    result = deploy._clean_global_only_root(
        tmp_path, {"deploy": {"project_name": "shared", "environment_tag": "prod"}},
        vanilla=True, plan_only=True,
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "networks to remove (0): -" in output
    assert "shared-app-network" not in output


def test_global_only_vanilla_propagates_reset_failure(tmp_path, monkeypatch):
    target = tmp_path / deploy.VANILLA_RESET_FILES[0]
    target.mkdir()

    def no_network_targets(_repo_root, _config, _projects, *, tagged):
        return [], {}, [], ""

    monkeypatch.setattr(deploy, "_clean_network_targets", no_network_targets)
    monkeypatch.setattr(deploy, "_remove_identity_networks", lambda _targets: ([], []))

    assert deploy._clean_global_only_root(
        tmp_path, {}, vanilla=True, plan_only=False,
    ) == 1


def test_global_only_vanilla_reports_the_reason_a_network_survived(
    tmp_path, monkeypatch, capsys,
):
    network = "workspace-net"

    def network_target(_repo_root, _config, _projects, *, tagged):
        return [network], {}, [], ""

    monkeypatch.setattr(deploy, "_clean_network_targets", network_target)
    monkeypatch.setattr(
        deploy, "_remove_identity_networks",
        lambda _targets: ([], [(network, "still attached by a foreign container")]),
    )
    monkeypatch.setattr(deploy, "_network_exists", lambda _name: True)

    result = deploy._clean_global_only_root(
        tmp_path, {}, vanilla=True, plan_only=False,
    )

    assert result == 1
    assert "network 'workspace-net' remains — still attached by a foreign container" in (
        capsys.readouterr().out
    )


def test_root_cleanability_does_not_follow_a_symlinked_stack(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / deploy.STACK_CONFIG_DEFAULTS).write_text("", encoding="utf-8")
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    assert deploy._root_has_cleanable_stack(root) is False


def test_engine_clean_guard_keeps_primary_instance_and_stack_identity(
    tmp_path, monkeypatch,
):
    configure = _docker(tmp_path, monkeypatch, [])
    labels = {
        "com.docker.compose.project": "demo-runner",
        "com.docker.compose.oneoff": "True",
        "com.docker.compose.project.working_dir": str(tmp_path.resolve()),
        "ciu.run": "1",
        "ciu.run.stack": "other/stack",
    }
    document = [{"Id": _CONTAINER_ID, "Name": "/run-one", "Config": {"Labels": labels}}]
    configure([
        {"prefix": ["ps"], "stdout": _CONTAINER_ID + "\n"},
        {"prefix": ["inspect"], "stdout": json.dumps(document)},
    ])
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)

    with pytest.raises(engine.ComposeError, match="another checkout or stack"):
        engine.guard_clean_one_shots(
            "demo-runner", repo_root=tmp_path, stack_dir=tmp_path / "tools" / "runner",
        )


def test_unavailable_git_metadata_does_not_mark_a_plain_directory_as_a_worktree(tmp_path):
    assert not deploy._is_linked_git_worktree(tmp_path)
