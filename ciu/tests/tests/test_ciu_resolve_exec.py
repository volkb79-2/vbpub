"""CIU-118 service identity queries and exact, already-running exec."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ciu import cli, composefile, deploy, worktree
from ciu.worktree import WorktreeError


@pytest.fixture
def resolvable_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "test-runner"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text(
        '[runner]\nstack_name = "test-runner"\n'
        '[runner.api]\nname = "test-runner"\n'
        '[runner.ui]\nname = "test-runner-ui"\n',
        encoding="utf-8",
    )
    (stack / "ciu.compose.yml.j2").write_text(
        "services:\n"
        "  {{ runner.api.name }}:\n"
        "    image: example/runner:1\n"
        "    container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-runner\n"
        "  {{ runner.ui.name }}:\n"
        "    image: example/runner-ui:1\n"
        "    container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-runner-ui\n",
        encoding="utf-8",
    )
    global_config = {
        "deploy": {
            "project_name": "demo",
            "environment_tag": "test",
            "network_name": "demo-test-net",
            "env": {"shared": {"CONTAINER_UID": "1000", "DOCKER_GID": "1000"}},
            "phases": {
                "phase_1": {
                    "services": [
                        {"path": "tools/test-runner", "name": "test-runner", "enabled": True}
                    ]
                }
            },
        },
        "topology": {
            "services": {
                "test-runner": {"internal_host": "demo-test-runner", "internal_port": 8080}
            }
        },
    }
    monkeypatch.setattr(
        deploy,
        "load_global_config",
        lambda _root, write_rendered=True: global_config,
    )
    return repo, stack, global_config


def test_resolve_reads_exact_service_keys_without_docker_or_rendered_files(
    resolvable_repo, monkeypatch
):
    repo, stack, _global = resolvable_repo

    def forbidden_docker(*_args, **_kwargs):
        pytest.fail("ciu resolve without --live must not contact Docker")

    monkeypatch.setattr(deploy.procutil, "docker", forbidden_docker)
    document = deploy.resolve_identities(repo, service="test-runner")

    identity = document["resolved"]["identities"]["tools/test-runner"]["test-runner"]
    assert document["schema_version"] == 1
    assert identity["container_name"] == "demo-test-runner"
    assert identity["compose_project"] == "demo-test-test-runner"
    assert identity["network"] == "demo-test-net"
    assert identity["image"] == "example/runner:1"
    assert identity["internal_host"] == "demo-test-runner"
    assert identity["port"] == 8080
    assert not (repo / "ciu.global.toml").exists()
    assert not (stack / "ciu.toml").exists()
    assert not (stack / "ciu.compose.yml").exists()


def test_service_filter_is_exact_not_substring(resolvable_repo):
    repo, _stack, _global = resolvable_repo
    document = deploy.resolve_identities(repo, service="test-runner-ui")
    services = document["resolved"]["identities"]["tools/test-runner"]
    assert set(services) == {"test-runner-ui"}


def test_stack_selector_accepts_absolute_inside_and_unique_basename(resolvable_repo):
    repo, stack, _global = resolvable_repo
    assert deploy.resolve_identities(repo, stack=str(stack))["resolved"]["identities"].keys() == {
        "tools/test-runner"
    }
    assert deploy.resolve_identities(repo, stack="test-runner")["resolved"]["identities"].keys() == {
        "tools/test-runner"
    }


def test_stack_selector_refuses_outside_ambiguous_and_unselected_paths(tmp_path):
    selection = [
        {"path": "one/test-runner", "service": "test-runner"},
        {"path": "two/test-runner", "service": "test-runner"},
    ]
    with pytest.raises(ValueError, match="outside repo root"):
        deploy._resolve_identity_stack_paths(tmp_path, selection, str(tmp_path.parent / "elsewhere"))
    with pytest.raises(ValueError, match="matches multiple"):
        deploy._resolve_identity_stack_paths(tmp_path, selection, "test-runner")
    with pytest.raises(ValueError, match="not in the current deploy selection"):
        deploy._resolve_identity_stack_paths(tmp_path, selection, "other/api")
    with pytest.raises(ValueError, match="not in the current deploy selection"):
        deploy._resolve_identity_stack_paths(tmp_path, selection, "missing")


def test_explicit_profile_selects_a_profile_only_stack(resolvable_repo):
    repo, _stack, global_config = resolvable_repo
    global_config["deploy"]["phases"] = {}
    global_config["deploy"]["profiles"] = {
        "test": {"stacks": ["tools/test-runner"]},
    }

    with pytest.raises(ValueError, match="not in the current deploy selection"):
        deploy.resolve_identities(repo, stack="tools/test-runner")

    document = deploy.resolve_identities(
        repo, stack="tools/test-runner", profiles=["test"]
    )
    identity = document["resolved"]["identities"]["tools/test-runner"]["test-runner"]
    assert identity["container_name"] == "demo-test-runner"


def test_resolve_refuses_missing_network_and_missing_service(resolvable_repo):
    repo, _stack, global_config = resolvable_repo
    global_config["deploy"].pop("network_name")
    with pytest.raises(ValueError, match="no declared network identity"):
        deploy.resolve_identities(repo)
    global_config["deploy"]["network_name"] = "demo-test-net"
    with pytest.raises(ValueError, match="service 'missing' was not found"):
        deploy.resolve_identities(repo, stack="test-runner", service="missing")


def test_resolve_uses_generated_facts_network_when_global_has_none(resolvable_repo):
    from ciu import workspace_env

    repo, _stack, global_config = resolvable_repo
    global_config["deploy"].pop("network_name")
    facts = {
        "repo_name": "demo", "instance_id": "ab12cd", "network": "facts-net",
        "physical_repo_root": str(repo), "repo_root": str(repo), "public_fqdn": "demo.test",
    }
    workspace_env.write_generated_facts(repo, facts)
    identity = deploy.resolve_identities(repo)["resolved"]["identities"]["tools/test-runner"]["test-runner"]
    assert identity["network"] == "facts-net"


def test_resolve_refuses_missing_compose_template_and_invalid_shapes(resolvable_repo, monkeypatch):
    repo, stack, _global = resolvable_repo
    template = stack / "ciu.compose.yml.j2"
    template.unlink()
    with pytest.raises(ValueError, match="has no ciu.compose.yml.j2"):
        deploy.resolve_identities(repo)
    template.write_text("services: {}\n", encoding="utf-8")

    monkeypatch.setattr(deploy.engine, "compose_project_name", lambda *_a: (_ for _ in ()).throw(ValueError("legacy")))
    monkeypatch.setattr(deploy.engine, "identity_compose_project_name", lambda *_a, **_kw: "legacy-project")
    monkeypatch.setattr(composefile, "render_compose", lambda *_a, **_kw: "services: []\n")
    with pytest.raises(ValueError, match="has no services table"):
        deploy.resolve_identities(repo)

    monkeypatch.setattr(composefile, "render_compose", lambda *_a, **_kw: "services: { bad: value }\n")
    with pytest.raises(ValueError, match="must be a table"):
        deploy.resolve_identities(repo)


def test_resolve_bad_yaml_and_missing_py_yaml_fail_closed(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    monkeypatch.setattr(composefile, "render_compose", lambda *_a, **_kw: "not: [valid")
    with pytest.raises(ValueError, match="rendered compose.*is invalid"):
        deploy.resolve_identities(repo)

    original_import = __import__

    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("hidden yaml")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_yaml)
    with pytest.raises(RuntimeError, match="PyYAML is required"):
        deploy.resolve_identities(repo)


def test_resolve_shipped_stack_uses_committed_compose_and_refuses_missing_file(resolvable_repo, monkeypatch):
    repo, stack, _global = resolvable_repo
    monkeypatch.setattr(deploy.phases_pkg, "service_shipped", lambda _service: True)
    shipped = stack / "docker-compose.yml"
    shipped.write_text("services:\n  fixed:\n    image: fixed:1\n", encoding="utf-8")
    document = deploy.resolve_identities(repo)
    assert set(document["resolved"]["identities"]["tools/test-runner"]) == {"fixed"}
    shipped.unlink()
    with pytest.raises(ValueError, match="has no docker-compose.yml"):
        deploy.resolve_identities(repo)


def test_resolve_keeps_working_dir_without_topology_table(resolvable_repo, monkeypatch):
    repo, _stack, global_config = resolvable_repo
    global_config["topology"]["services"] = []
    monkeypatch.setattr(
        composefile, "render_compose",
        lambda *_a, **_kw: "services:\n  only:\n    image: test:1\n    working_dir: /app\n",
    )
    identities = deploy.resolve_identities(repo)["resolved"]["identities"]
    row = identities["tools/test-runner"]["only"]
    assert row["working_dir"] == "/app"
    assert row["internal_host"] is None and row["port"] is None


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (OSError("daemon gone"), "could not query Docker"),
        (SimpleNamespace(returncode=1, stdout="", stderr="denied"), "Docker query failed"),
    ],
)
def test_live_resolution_query_errors_are_runtime_errors(resolvable_repo, monkeypatch, response, message):
    repo, _stack, _global = resolvable_repo
    def docker(*_args, **_kwargs):
        if isinstance(response, BaseException):
            raise response
        return response
    monkeypatch.setattr(deploy.procutil, "docker", docker)
    with pytest.raises(RuntimeError, match=message):
        deploy.resolve_identities(repo, service="test-runner", live=True)


def test_live_resolution_absent_multiple_and_inspect_errors(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    monkeypatch.setattr(deploy.procutil, "docker", lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    absent = deploy.resolve_identities(repo, service="test-runner", live=True)["resolved"]["identities"]["tools/test-runner"]["test-runner"]["live"]
    assert absent == {"state": "absent", "health": None, "containers": []}

    def multiple(argv, **_kwargs):
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="one\ntwo\n", stderr="")
        return SimpleNamespace(returncode=0, stdout='{"Status":"running"}', stderr="")
    monkeypatch.setattr(deploy.procutil, "docker", multiple)
    live = deploy.resolve_identities(repo, service="test-runner", live=True)["resolved"]["identities"]["tools/test-runner"]["test-runner"]["live"]
    assert live["state"] == "multiple" and live["health"] is None

    def bad_inspect(argv, **_kwargs):
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="one\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="gone")
    monkeypatch.setattr(deploy.procutil, "docker", bad_inspect)
    with pytest.raises(RuntimeError, match="Docker inspect failed"):
        deploy.resolve_identities(repo, service="test-runner", live=True)

    monkeypatch.setattr(deploy.procutil, "docker", lambda argv, **_kw: SimpleNamespace(returncode=0, stdout="one\n" if argv[0] == "ps" else "not-json", stderr=""))
    with pytest.raises(RuntimeError, match="invalid state"):
        deploy.resolve_identities(repo, service="test-runner", live=True)


def test_exec_selector_and_selection_refusals(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    with pytest.raises(ValueError, match="requires a command"):
        deploy.exec_service(repo, "tools/test-runner", [])
    for selector in (":api", "tools/test-runner:"):
        with pytest.raises(ValueError, match="service selector"):
            deploy.exec_service(repo, selector, ["true"])
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: {"resolved": {"identities": {}}})
    with pytest.raises(ValueError, match="has no rendered services"):
        deploy.exec_service(repo, "missing", ["true"])
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: {"resolved": {"identities": {"tools/test-runner": {"a": {}, "b": {}}}}})
    with pytest.raises(ValueError, match="has 2 services"):
        deploy.exec_service(repo, "tools/test-runner", ["true"])
    with pytest.raises(ValueError, match="was not found"):
        deploy.exec_service(repo, "tools/test-runner:missing", ["true"])


def test_exec_declared_mount_target_and_duplicate_target_guards(resolvable_repo, monkeypatch):
    repo, _stack, global_config = resolvable_repo
    identity_doc = {"resolved": {"identities": {"tools/test-runner": {"test-runner": {
        "compose_project": "project", "network": "net", "working_dir": "/default",
    }}}}}
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: identity_doc)
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: global_config)
    target = SimpleNamespace(alias="runner", stack="tools/test-runner", service="test-runner", requires_worktree_mount=True, workdir="/workspace")
    monkeypatch.setattr(worktree, "resolve_exec_targets_config", lambda _cfg: {"runner": target})
    monkeypatch.setattr(worktree, "_resolve_target_container", lambda *_a: "cid")
    monkeypatch.setattr(worktree, "_verify_checkout_mount", lambda *_a: None)
    from ciu import workspace_env
    monkeypatch.setattr(workspace_env, "read_generated_facts", lambda *_a, **_kw: {})
    with pytest.raises(ValueError, match="no physical_repo_root"):
        deploy.exec_service(repo, "tools/test-runner:test-runner", ["true"])
    monkeypatch.setattr(workspace_env, "read_generated_facts", lambda *_a, **_kw: {"physical_repo_root": "/physical"})
    calls = []
    monkeypatch.setattr(deploy.procutil, "docker", lambda argv, **kw: calls.append((argv, kw)) or SimpleNamespace(returncode=5))
    assert deploy.exec_service(repo, "tools/test-runner:test-runner", ["true"]) == 5
    assert calls[0][0] == ["exec", "-w", "/workspace", "cid", "true"]

    second = SimpleNamespace(**{**target.__dict__, "alias": "other"})
    monkeypatch.setattr(worktree, "resolve_exec_targets_config", lambda _cfg: {"runner": target, "other": second})
    with pytest.raises(ValueError, match="multiple declared exec targets"):
        deploy.exec_service(repo, "tools/test-runner:test-runner", ["true"])


def test_live_resolution_uses_exact_labels_and_reports_state(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="cid-1\n", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout='{"Status":"running","Health":{"Status":"healthy"}}',
            stderr="",
        )

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    document = deploy.resolve_identities(repo, service="test-runner", live=True)
    identity = document["resolved"]["identities"]["tools/test-runner"]["test-runner"]
    assert identity["live"]["state"] == "running"
    assert identity["live"]["health"] == "healthy"
    assert calls[0] == [
        "ps", "-a", "--filter", "label=com.docker.compose.project=demo-test-test-runner",
        "--filter", "label=com.docker.compose.service=test-runner", "--format", "{{.ID}}",
    ]


def test_exec_runs_verbatim_in_the_single_exact_service(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="cid-api\n", stderr="")
        return SimpleNamespace(returncode=7, stdout="", stderr="")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    result = deploy.exec_service(repo, "tools/test-runner:test-runner", ["python", "--help"])
    assert result == 7
    assert calls[0][0] == [
        "ps", "--filter", "label=com.docker.compose.project=demo-test-test-runner",
        "--filter", "label=com.docker.compose.service=test-runner",
        "--filter", "network=demo-test-net", "--format", "{{.ID}}",
    ]
    assert calls[1][0] == ["exec", "cid-api", "python", "--help"]


def test_exec_requests_tty_only_when_stdin_and_stdout_are_terminals(
    resolvable_repo, monkeypatch
):
    repo, _stack, _global = resolvable_repo
    monkeypatch.setattr(deploy.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(deploy.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="cid-api\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    assert deploy.exec_service(
        repo, "tools/test-runner:test-runner", ["bash"]
    ) == 0
    assert calls[1] == ["exec", "-it", "cid-api", "bash"]


def test_exec_selects_the_only_service_when_no_service_suffix_is_given(resolvable_repo, monkeypatch):
    repo, _stack, global_config = resolvable_repo
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: {
        "resolved": {"identities": {"tools/test-runner": {
            "only": {"compose_project": "project", "network": "net"},
        }}}
    })
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: global_config)
    monkeypatch.setattr(worktree, "resolve_exec_targets_config", lambda _cfg: {})
    monkeypatch.setattr(worktree, "_resolve_target_container", lambda *_a: "cid")
    calls = []
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda argv, **_kw: calls.append(argv) or SimpleNamespace(returncode=0),
    )
    assert deploy.exec_service(repo, "tools/test-runner", ["true"]) == 0
    assert calls == [["exec", "cid", "true"]]


def test_exec_refuses_two_exact_matches_before_running_command(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="cid-a\ncid-b\n", stderr="")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    with pytest.raises(WorktreeError, match="expected exactly one running container"):
        deploy.exec_service(repo, "tools/test-runner:test-runner", ["true"])
    assert len(calls) == 1
    assert calls[0][0] == "ps"


def test_exec_cli_passes_child_help_through_separator():
    from ciu.cli import _wants_verb_help

    assert not _wants_verb_help("exec", ["tools/test-runner", "--", "python", "--help"])
    assert _wants_verb_help("exec", ["--help"])


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["--sta", "tools/test-runner", "--json"],
    ],
)
def test_resolve_parser_disables_private_help_and_option_abbreviation(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli._resolve_identities_cli(argv)
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_resolve_cli_returns_zero_and_keeps_sorted_json(monkeypatch, tmp_path, capsys):
    document = {
        "z": {"value": 1},
        "a": {"value": 2},
    }
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: document)

    assert cli._resolve_identities_cli(["--json"]) == 0
    assert capsys.readouterr().out == json.dumps(document, indent=2, sort_keys=True) + "\n"


def test_exec_parser_leaves_help_to_top_level_dispatcher(capsys):
    with pytest.raises(SystemExit) as exc:
        cli._exec_service_cli(["--help", "--", "true"])
    assert exc.value.code == 2
    assert "error:" in capsys.readouterr().err
