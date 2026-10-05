"""CIU-118 service identity queries and exact, already-running exec."""

from __future__ import annotations

import json
import subprocess
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
    def load_global_config(root, *, write_rendered=True):
        if write_rendered:
            (Path(root) / "ciu.global.toml").write_text(
                "# rendered global config\n", encoding="utf-8"
            )
        return global_config

    monkeypatch.setattr(deploy, "load_global_config", load_global_config)
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
    assert identity["hostname"] == "demo-test-runner"
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


def test_single_stack_resolve_preserves_existing_state_without_writing(
    resolvable_repo,
):
    repo, stack, _global = resolvable_repo
    rendered = stack / "ciu.toml"
    deploy.config_model.write_rendered_toml(
        rendered, {"state": {"marker": "state-v"}}
    )
    before = rendered.read_bytes()
    (stack / "ciu.compose.yml.j2").write_text(
        "services:\n"
        "  runner:\n"
        "    image: {{ state.marker }}\n",
        encoding="utf-8",
    )

    document = deploy.resolve_identities(repo)

    identity = document["resolved"]["identities"]["tools/test-runner"]["runner"]
    assert identity["image"] == "state-v"
    assert rendered.read_bytes() == before
    assert not (repo / "ciu.global.toml").exists()


def test_resolve_rejects_truthy_non_string_network(resolvable_repo):
    repo, _stack, global_config = resolvable_repo
    global_config["deploy"]["network_name"] = 17

    with pytest.raises(ValueError, match="no declared network identity"):
        deploy.resolve_identities(repo)


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
    with pytest.raises(
        ValueError,
        match="service 'missing' was not found in stack 'test-runner'",
    ):
        deploy.resolve_identities(repo, stack="test-runner", service="missing")


def test_resolve_does_not_repair_identity_that_appears_during_query(
    resolvable_repo, monkeypatch,
):
    from ciu import workspace_env

    repo, _stack, _global = resolvable_repo
    facts = {
        "repo_name": "demo",
        "instance_id": "ab12cd",
        "network": "demo-ab12cd-network",
        "physical_repo_root": str(repo),
        "repo_root": str(repo),
        "public_fqdn": "demo.test",
    }

    def old_format_record() -> None:
        path = workspace_env.generated_facts_path(repo)
        lines = workspace_env.render_generated_facts_block(facts)
        path.write_text(
            "\n".join(
                line for line in lines
                if not line.startswith("schema_version = ")
            ) + "\n",
            encoding="utf-8",
        )

    def project_name(_config, _stack_dir):
        old_format_record()
        raise ValueError("compose project is not declared")

    monkeypatch.setattr(deploy.engine, "compose_project_name", project_name)
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_args, **_kwargs: pytest.fail(
            "read-only resolve must reject before checking resources"
        ),
    )
    monkeypatch.setattr(
        workspace_env,
        "generate_ciu_env",
        lambda *_args, **_kwargs: pytest.fail(
            "read-only resolve must never rewrite generated identity"
        ),
    )

    with pytest.raises(
        workspace_env.WorkspaceEnvError,
        match="read-only operation will not repair",
    ):
        deploy.resolve_identities(repo)


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


def test_live_identity_state_captures_both_docker_responses(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[1] == "ps":
            stdout = "cid-1\n" if kwargs["capture_output"] else None
        else:
            stdout = (
                '{"Status":"running","Health":{"Status":"healthy"}}'
                if kwargs["capture_output"] else None
            )
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(deploy.procutil.subprocess, "run", run)
    assert deploy._resolve_identity_live_state("project", "service") == {
        "state": "running",
        "health": "healthy",
        "containers": [{
            "container_id": "cid-1",
            "state": {"Status": "running", "Health": {"Status": "healthy"}},
            "health": "healthy",
        }],
    }
    assert [kwargs["capture_output"] for _argv, kwargs in calls] == [True, True]


@pytest.mark.parametrize(
    ("phase", "stdout", "stderr", "chosen", "ignored"),
    [
        ("query", "query stdout", "query stderr", "query stderr", "query stdout"),
        ("query", "query stdout", "", "query stdout", None),
        ("query", "", "", "Docker query failed", None),
        ("inspect", "query stdout", "query stderr", "query stderr", "query stdout"),
        ("inspect", "query stdout", "", "query stdout", None),
    ],
)
def test_live_identity_state_wraps_docker_errors_and_prefers_stderr(
    monkeypatch, phase, stdout, stderr, chosen, ignored,
):
    def run(argv, **kwargs):
        if argv[1] == "ps":
            if phase == "query":
                return subprocess.CompletedProcess(
                    argv, 17, stdout=stdout, stderr=stderr
                )
            return subprocess.CompletedProcess(argv, 0, stdout="cid-1\n", stderr="")
        return subprocess.CompletedProcess(argv, 17, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(deploy.procutil.subprocess, "run", run)
    with pytest.raises(RuntimeError) as exc_info:
        deploy._resolve_identity_live_state("project", "service")
    message = str(exc_info.value)
    assert chosen in message
    if ignored is not None:
        assert ignored not in message


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


@pytest.mark.parametrize(
    ("other_stack", "other_service"),
    [
        ("tools/other", "test-runner"),
        ("tools/test-runner", "other-service"),
    ],
)
def test_exec_target_match_requires_exact_stack_and_service(
    resolvable_repo, monkeypatch, other_stack, other_service,
):
    repo, _stack, global_config = resolvable_repo
    identity_doc = {"resolved": {"identities": {"tools/test-runner": {
        "test-runner": {
            "compose_project": "project",
            "network": "net",
            "working_dir": "/default",
        },
    }}}}
    target = SimpleNamespace(
        alias="requested", stack="tools/test-runner", service="test-runner",
        requires_worktree_mount=False, workdir="/requested",
    )
    other = SimpleNamespace(
        alias="other", stack=other_stack, service=other_service,
        requires_worktree_mount=False, workdir="/wrong",
    )
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: identity_doc)
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: global_config)
    monkeypatch.setattr(
        worktree, "resolve_exec_targets_config",
        lambda _cfg: {"requested": target, "other": other},
    )
    monkeypatch.setattr(worktree, "_resolve_target_container", lambda *_a: "cid")
    calls = []
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda argv, **kwargs: calls.append((argv, kwargs))
        or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert deploy.exec_service(repo, "tools/test-runner:test-runner", ["true"]) == 0
    assert calls == [
        (["exec", "-w", "/requested", "cid", "true"],
         {"capture": False, "check": False}),
    ]


def test_exec_mount_proof_refuses_outdated_identity_without_repair(
    resolvable_repo, monkeypatch,
):
    from ciu import workspace_env

    repo, _stack, global_config = resolvable_repo
    identity_doc = {"resolved": {"identities": {"tools/test-runner": {"test-runner": {
        "compose_project": "project", "network": "net", "working_dir": "/default",
    }}}}}
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: identity_doc)
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: global_config)
    target = SimpleNamespace(
        alias="runner", stack="tools/test-runner", service="test-runner",
        requires_worktree_mount=True, workdir="/workspace",
    )
    monkeypatch.setattr(worktree, "resolve_exec_targets_config", lambda _cfg: {"runner": target})
    monkeypatch.setattr(worktree, "_resolve_target_container", lambda *_a: "cid")

    old_facts = {
        "repo_name": "demo",
        "instance_id": "ab12cd",
        "network": "demo-ab12cd-network",
        "physical_repo_root": str(repo),
        "repo_root": str(repo),
        "public_fqdn": "demo.test",
    }
    path = workspace_env.generated_facts_path(repo)
    old_document = workspace_env.render_generated_facts_block(old_facts)
    path.write_text(
        "\n".join(
            line for line in old_document
            if not line.startswith("schema_version = ")
        ) + "\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    docker_calls = []

    def docker(argv, **kwargs):
        docker_calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    monkeypatch.setattr(
        workspace_env,
        "generate_ciu_env",
        lambda *_a, **_kw: pytest.fail("exec must not repair generated identity facts"),
    )

    with pytest.raises(
        workspace_env.WorkspaceEnvError,
        match="read-only operation will not repair",
    ):
        deploy.exec_service(repo, "tools/test-runner:test-runner", ["true"])

    assert path.read_bytes() == before
    assert docker_calls == []


def test_live_resolution_uses_exact_labels_and_reports_state(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "ps":
            # Match subprocess.run: without capture_output=True, stdout is
            # None, so a live service must not be certified as running.
            stdout = "cid-1\n" if kwargs.get("capture") else None
            return SimpleNamespace(returncode=0, stdout=stdout, stderr=None)
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
    assert calls[0][0] == [
        "ps", "-a", "--filter", "label=com.docker.compose.project=demo-test-test-runner",
        "--filter", "label=com.docker.compose.service=test-runner", "--format", "{{.ID}}",
    ]
    assert calls[0][1]["capture"] is True


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
    assert calls[1][1] == {"capture": False, "check": False}


def test_exec_does_not_render_global_config(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    rendered_config = repo / "ciu.global.toml"
    assert not rendered_config.exists()
    monkeypatch.setattr(worktree, "resolve_exec_targets_config", lambda _cfg: {})
    monkeypatch.setattr(worktree, "_resolve_target_container", lambda *_a: "cid")
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    assert deploy.exec_service(repo, "tools/test-runner:test-runner", ["true"]) == 0
    assert not rendered_config.exists()


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


@pytest.mark.parametrize("stdin_tty,stdout_tty", [(True, False), (False, True)])
def test_exec_requests_no_tty_when_only_one_stream_is_a_terminal(
    resolvable_repo, monkeypatch, stdin_tty, stdout_tty,
):
    repo, _stack, _global = resolvable_repo
    monkeypatch.setattr(deploy.sys, "stdin", SimpleNamespace(isatty=lambda: stdin_tty))
    monkeypatch.setattr(deploy.sys, "stdout", SimpleNamespace(isatty=lambda: stdout_tty))
    calls = []

    def docker(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="cid-api\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    assert deploy.exec_service(repo, "tools/test-runner:test-runner", ["bash"]) == 0
    assert calls[1][0] == ["exec", "cid-api", "bash"]
    assert calls[1][1] == {"capture": False, "check": False}


def test_stop_stack_uses_only_the_exact_compose_project(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="container-a\ncontainer-b\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    assert deploy.stop_stack(repo, "tools/test-runner") == 0
    assert calls == [
        (
            ["ps", "--filter", "label=com.docker.compose.project=demo-test-test-runner",
             "--format", "{{.ID}}"],
            {"capture": True, "check": False},
        ),
        (
            ["stop", "container-a", "container-b"],
            {"capture": True, "check": False},
        ),
    ]


def test_stop_stack_refuses_docker_query_failure_before_stop(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=19, stdout="", stderr="daemon unavailable")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    with pytest.raises(RuntimeError, match="daemon unavailable"):
        deploy.stop_stack(repo, "tools/test-runner")
    assert calls == [[
        "ps", "--filter", "label=com.docker.compose.project=demo-test-test-runner",
        "--format", "{{.ID}}",
    ]]


def test_stop_stack_refuses_stack_outside_the_selected_root(resolvable_repo, monkeypatch):
    repo, _stack, _global = resolvable_repo
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_a, **_kw: pytest.fail("outside stack must refuse before Docker"),
    )
    with pytest.raises(ValueError, match="must select a stack below CIU root"):
        deploy.stop_stack(repo, "../outside")


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ({}, "no resolved services"),
        ({"api": {"compose_project": None}}, "one exact Compose project"),
        ({"api": {"compose_project": ""}}, "one exact Compose project"),
        (
            {"api": {"compose_project": "project-a"},
             "worker": {"compose_project": "project-b"}},
            "one exact Compose project",
        ),
    ],
)
def test_stop_stack_refuses_missing_or_ambiguous_projects(
    resolvable_repo, monkeypatch, rows, message,
):
    repo, _stack, _global = resolvable_repo
    monkeypatch.setattr(
        deploy,
        "resolve_identities",
        lambda *_a, **_kw: {"resolved": {"identities": {"tools/test-runner": rows}}},
    )
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda *_a, **_kw: pytest.fail("invalid project must refuse before Docker"),
    )
    with pytest.raises(ValueError, match=message):
        deploy.stop_stack(repo, "tools/test-runner")


@pytest.mark.parametrize("stdout", ["", "  \n\t\n", None])
def test_stop_stack_succeeds_when_no_containers_are_running(
    resolvable_repo, monkeypatch, stdout, capsys,
):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    assert deploy.stop_stack(repo, "tools/test-runner") == 0
    assert len(calls) == 1
    assert "No running containers" in capsys.readouterr().out


def test_stop_stack_reports_docker_stop_failure_without_success_message(
    resolvable_repo, monkeypatch,
):
    repo, _stack, _global = resolvable_repo
    calls = []

    def docker(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "ps":
            return SimpleNamespace(returncode=0, stdout="container-a\n", stderr="")
        return SimpleNamespace(returncode=17, stdout="", stderr="stop denied")

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    with pytest.raises(RuntimeError, match="stop denied"):
        deploy.stop_stack(repo, "tools/test-runner")
    assert [call[0][0] for call in calls] == ["ps", "stop"]


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


def test_exec_parser_rejects_abbreviated_profile_option(monkeypatch, capsys):
    monkeypatch.setattr(
        deploy,
        "exec_service",
        lambda *_args, **_kwargs: pytest.fail(
            "abbreviated --profile must be rejected before service resolution"
        ),
    )
    with pytest.raises(SystemExit) as exc:
        cli._exec_service_cli([
            "--prof", "test", "tools/test-runner", "--", "true",
        ])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err
