"""CIU-141 selection, plan projection, and typed extension contracts."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from ciu import deploy, engine, oneshot


def _service(**updates):
    service = {
        "image": "python:3.11-slim",
        "user": "1001:1001",
        "working_dir": "/workspace",
        "cgroup_parent": "dev-gates.slice",
        "networks": {"instance-net": {}},
        "volumes": [
            {
                "type": "bind", "source": "/repo", "target": "/workspace",
                "read_only": True,
            },
        ],
        "mem_limit": "134217728",
        "memswap_limit": "268435456",
        "pids_limit": 64,
        "cpus": 0.25,
        "labels": {"app": "runner"},
        "environment": {"TOKEN": "planted-secret-value"},
    }
    service.update(updates)
    return service


def _prepared(tmp_path):
    return SimpleNamespace(
        working_dir=tmp_path / "tools" / "runner",
        repo_root=tmp_path,
        project="demo-runner",
        file_args=["-f", "ciu.compose.yml"],
        compose_env={"COMPOSE_PROFILES": "test"},
        rendered_compose="services:\n  worker:\n    image: python:3.11-slim\n",
        ownership_path=None,
        config={"worker": {"image": "python:3.11-slim"}},
        secret_values=frozenset({"planted-secret-value"}),
    )


def test_selector_resolution_is_shared_and_exact():
    assert deploy.split_service_selector("tools/runner:worker") == (
        "tools/runner", "worker",
    )
    assert deploy.split_service_selector("tools/runner") == ("tools/runner", None)
    with pytest.raises(ValueError, match="selector"):
        deploy.split_service_selector(":worker")
    identities = {
        "tools/runner": {
            "worker": {"image": "python:3.11-slim"},
            "worker-sidecar": {"image": "busybox"},
        },
    }

    stack_path, service, identity = deploy.select_service(identities, "worker")

    assert stack_path == "tools/runner"
    assert service == "worker"
    assert identity["image"] == "python:3.11-slim"
    with pytest.raises(ValueError, match="name one as"):
        deploy.select_service(identities, None)
    with pytest.raises(ValueError, match="service 'work' was not found"):
        deploy.select_service(identities, "work")


@pytest.mark.parametrize("selector", ["", None, 7, "tools/runner:", ":"])
def test_selector_grammar_refuses_empty_and_malformed_selectors(selector):
    with pytest.raises(ValueError, match=r"\[S18\] service selector must be"):
        deploy.split_service_selector(selector)


def test_service_selection_refuses_no_stack_and_more_than_one_stack():
    with pytest.raises(ValueError, match="selected stack has no rendered services"):
        deploy.select_service({}, "worker")
    two_stacks = {"tools/a": {"worker": {}}, "tools/b": {"worker": {}}}
    with pytest.raises(ValueError, match="resolved more than one stack"):
        deploy.select_service(two_stacks, "worker")


def test_run_name_and_label_validation_preserves_label_values():
    assert oneshot.validate_container_name("run-1_test") == "run-1_test"
    assert oneshot.parse_run_label("run-gate.note=value with spaces=kept") == (
        "run-gate.note", "value with spaces=kept",
    )
    assert oneshot.parse_run_label("empty=") == ("empty", "")
    for name in ("", "-bad", "contains/slash", "x" * 129):
        with pytest.raises(ValueError, match="container name"):
            oneshot.validate_container_name(name)
    for label in ("=empty-key", "ciu.run.id=caller", "com.docker.compose.project=evil"):
        with pytest.raises(ValueError, match="label"):
            oneshot.parse_run_label(label)


def test_plan_projects_compose_config_without_environment_values(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    captured = []

    def fake_run(argv, **kwargs):
        captured.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "services": {"worker": _service()},
        }), "")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", fake_run)
    plan = oneshot.build_run_plan(
        prepared, stack="tools/runner", service="worker", name="rg-one",
    )

    assert set(plan) == {
        "schema", "project", "stack", "service", "image", "user", "working_dir",
        "cgroup_parent", "networks", "mounts", "limits", "labels", "name",
        "extend_accepted", "hooks",
    }
    assert plan["schema"] == "ciu-run-plan/1"
    assert plan["image"] == "python:3.11-slim"
    assert plan["user"] == "1001:1001"
    assert plan["working_dir"] == "/workspace"
    assert plan["cgroup_parent"] == "dev-gates.slice"
    assert plan["networks"] == ["instance-net"]
    assert plan["mounts"] == [{
        "type": "bind", "source": "/repo", "target": "/workspace", "ro": True,
    }]
    assert plan["limits"] == {
        "mem_limit": "134217728", "memswap_limit": "268435456",
        "pids_limit": 64, "cpus": 0.25,
    }
    assert plan["hooks"] == ["pre_secrets", "pre_compose"]
    assert "planted-secret-value" not in json.dumps(plan)
    argv, kwargs = captured[0]
    assert argv[:6] == [
        "docker", "compose", "--progress", "quiet", "-p", "demo-runner",
    ]
    assert argv[-3:] == ["config", "--format", "json"]
    assert kwargs["env"] == prepared.compose_env
    assert kwargs["cwd"] == str(prepared.working_dir)


_NETWORK = "demo_instance-net"


def _dry_run_docker(monkeypatch, tmp_path, *, existing_name=None, network_present=True):
    """Install a read-only docker fake for the dry-run checks and return its call log."""
    prepared = _prepared(tmp_path)
    calls = []

    def prepare(*args, **kwargs):
        print("[STEP 1/17] test preparation")
        return prepared, "tools/runner", "worker"

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if argv[:3] == ["docker", "compose", "--progress"] and "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "services": {"worker": _service()},
                "networks": {"instance-net": {"name": _NETWORK, "external": True}},
            }), "")
        if argv[:3] == ["docker", "network", "inspect"]:
            if not network_present:
                return subprocess.CompletedProcess(argv, 1, "", "not found")
            return subprocess.CompletedProcess(
                argv, 0, json.dumps([{"Name": argv[-1], "Labels": {}}]), "",
            )
        if argv[:4] == ["docker", "inspect", "--type", "container"]:
            if existing_name is not None and argv[-1] == existing_name:
                return subprocess.CompletedProcess(argv, 0, json.dumps([{
                    "Id": "c" * 64, "Name": f"/{existing_name}",
                    "Config": {"Labels": {}},
                }]), "")
            return subprocess.CompletedProcess(argv, 1, "", "")
        raise AssertionError(f"dry-run must stay read-only: {argv!r}")

    monkeypatch.setattr(oneshot, "_prepare_run_stack", prepare)
    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda *_a: None)
    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)
    return calls


def test_dry_run_cli_reports_what_a_real_run_would_do(tmp_path, monkeypatch, capsys):
    calls = _dry_run_docker(monkeypatch, tmp_path)

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "print('ok')"],
        dry_run=True, json_output=True, env_names=["OPTIONAL_DSN"],
        name="rg-one", labels=["run-gate.job=lane 1"], owner_pid=os.getpid(),
    )

    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert result == 0
    assert document["schema"] == "ciu-run-plan/1"
    assert document["name"] == "rg-one"
    assert document["name_generated"] is False
    assert document["name_available"] is True
    assert document["networks_verified"] is True
    assert document["mode"] == "foreground"
    assert document["argv_len"] == 3
    assert document["owner_pid"] == os.getpid()
    labels = document["runtime_labels"]
    assert labels["ciu.run"] == "1"
    assert labels["ciu.run.stack"] == "tools/runner"
    assert labels["ciu.run.service"] == "worker"
    assert labels["ciu.run.mode"] == "foreground"
    assert labels["ciu.run.id"] == labels["ciu.run.started"] == "<assigned at launch>"
    assert "run-gate.job" not in labels  # user labels live in "labels"
    assert document["labels"]["run-gate.job"] == "lane 1"
    assert document["env"] == {
        "forwarded_names": ["OPTIONAL_DSN"],
        "injected": {
            "CIU_RUN_CONTAINER_NAME": "rg-one",
            "CIU_RUN_ID": "<assigned at launch>",
            "CIU_RUN_CGROUP_PARENT": "dev-gates.slice",
        },
    }
    assert "planted-secret-value" not in captured.out
    assert "STEP" not in captured.out
    assert "STEP" in captured.err  # preparation output is isolated from plan stdout
    # The same read-only checks a real run performs first; nothing is created.
    assert any(argv[:3] == ["docker", "network", "inspect"] for argv in calls)
    assert any(argv[:4] == ["docker", "inspect", "--type", "container"] for argv in calls)


def test_dry_run_cli_generates_and_reports_the_effective_name(tmp_path, monkeypatch, capsys):
    _dry_run_docker(monkeypatch, tmp_path)

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["true"], dry_run=True, json_output=True,
        detach=True, owner_pid=os.getpid(),
    )

    document = json.loads(capsys.readouterr().out)
    assert result == 0
    assert document["name_generated"] is True
    assert document["name"].startswith("demo-runner-worker-run-")
    assert document["env"]["injected"]["CIU_RUN_CONTAINER_NAME"] == document["name"]
    assert document["mode"] == "detached"


def test_dry_run_cli_refuses_where_the_real_run_would(tmp_path, monkeypatch):
    _dry_run_docker(monkeypatch, tmp_path, existing_name="taken")
    with pytest.raises(RuntimeError, match="container name 'taken' is already in use"):
        oneshot.run_cli(
            tmp_path, "tools/runner:worker", ["true"], dry_run=True, json_output=True,
            name="taken", owner_pid=os.getpid(),
        )
    _dry_run_docker(monkeypatch, tmp_path, network_present=False)
    with pytest.raises(ValueError, match="required stack network .* is not present"):
        oneshot.run_cli(
            tmp_path, "tools/runner:worker", ["true"], dry_run=True, json_output=True,
            name="free", owner_pid=os.getpid(),
        )


def test_dry_run_document_never_contains_a_secret(tmp_path):
    prepared = _prepared(tmp_path)
    with pytest.raises(ValueError, match="run plan contains a CIU secret"):
        oneshot._dry_run_document(
            {"cgroup_parent": None}, prepared=prepared, name="planted-secret-value",
            name_given=True, runtime_labels={}, env_names=[], mode="foreground",
            argv=["true"], owner_pid=1,
        )


def test_prepare_run_stack_uses_shared_service_selection_and_profile_context(
    tmp_path, monkeypatch,
):
    profile = SimpleNamespace(compose_profiles=["runtime"])
    prepared = _prepared(tmp_path)
    captured = {}
    monkeypatch.setattr(
        deploy, "resolve_identities",
        lambda *args, **kwargs: {
            "resolved": {"identities": {"tools/runner": {"worker": {"image": "x"}}}},
        },
    )
    monkeypatch.setattr(deploy, "load_global_config", lambda *args, **kwargs: {})
    monkeypatch.setattr(deploy, "resolve_profiles", lambda *args, **kwargs: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda selected_profile: [selected_profile])
    monkeypatch.setattr(
        deploy.profiles_pkg, "render_ciu_context",
        lambda selected_profile, selection: {"selected_profiles": ["test"]},
    )

    def prepare_stack(stack_dir, **kwargs):
        captured["stack_dir"] = stack_dir
        captured.update(kwargs)
        return prepared

    monkeypatch.setattr(engine, "prepare_stack", prepare_stack)

    actual = oneshot._prepare_run_stack(
        tmp_path, "tools/runner:worker", profiles=["test"], skip_hooks=True,
    )

    assert actual == (prepared, "tools/runner", "worker")
    assert captured["stack_dir"] == tmp_path / "tools/runner"
    assert captured["define_root"] == tmp_path
    assert captured["compose_profiles"] == ["runtime"]
    assert captured["ciu_context"] == {"selected_profiles": ["test"]}
    assert captured["skip_hooks"] is True


def test_extension_accepts_typed_run_gate_additions_and_init():
    extension = {
        "services": {
            "worker": {
                "mem_limit": "256m",
                "group_add": ["1234", 5678],
                "volumes": [{
                    "type": "bind", "source": "/run/observer",
                    "target": "/run/cgprofile", "read_only": True,
                }],
                "environment": {"CGPROFILE_SOCKET_DIR": "/run/cgprofile"},
                "labels": {"run-gate.job": "job-1"},
                "init": True,
            },
        },
        "x-ciu-run": {"observer-socket-dir": "/run/observer"},
    }

    base_service = _service()
    base_service.pop("mem_limit")
    validated = oneshot.validate_extension_document(
        extension, service_name="worker", base_service=base_service,
        secret_values={"planted-secret-value"},
    )

    assert validated["services"]["worker"]["init"] is True
    assert validated["services"]["worker"]["group_add"] == ["1234", "5678"]
    assert "cap_add" not in validated["services"]["worker"]
    assert "security_opt" not in validated["services"]["worker"]
    assert validated["services"]["worker"]["volumes"][0]["bind"] == {
        "create_host_path": False,
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("user", "0:0", "user"),
        ("cgroup_parent", "evil.slice", "cgroup_parent"),
        ("ports", ["8080:8080"], "ports"),
        # F3: privilege-widening keys are refused outright (plan 2.4), whether
        # or not the rendered service declares them.
        ("cap_add", ["SYS_ADMIN"], "cap_add"),
        ("security_opt", ["seccomp=unconfined", "apparmor=unconfined"], "security_opt"),
    ],
)
def test_extension_refuses_unlisted_fields_by_name(field, value, reason):
    with pytest.raises(ValueError, match=reason):
        oneshot.validate_extension_document(
            {"services": {"worker": {field: value}}}, service_name="worker",
            base_service=_service(), secret_values=set(),
        )


@pytest.mark.parametrize(
    ("extension_service", "reason"),
    [
        ({"mem_limit": "256m"}, "mem_limit"),
        ({"init": True}, "init"),
        ({"group_add": ["1234"]}, "group_add"),
        ({"volumes": [{
            "type": "bind", "source": "/tmp/other", "target": "/workspace",
        }]}, "target '/workspace'"),
    ],
)
def test_extension_refuses_silent_compose_collisions(extension_service, reason):
    base_service = _service()
    if "init" in extension_service:
        base_service["init"] = False
    if "group_add" in extension_service:
        base_service["group_add"] = ["2345"]
    with pytest.raises(ValueError, match=reason):
        oneshot.validate_extension_document(
            {"services": {"worker": extension_service}}, service_name="worker",
            base_service=base_service, secret_values=set(),
        )


def test_extension_rejects_secret_literals_reserved_labels_and_bad_observer_mount():
    with pytest.raises(ValueError, match="secret value"):
        oneshot.validate_extension_document(
            {"services": {"worker": {"environment": {"TOKEN": "planted-secret-value"}}}},
            service_name="worker", base_service=_service(),
            secret_values={"planted-secret-value"},
        )
    with pytest.raises(ValueError, match="reserved"):
        oneshot.validate_extension_document(
            {"services": {"worker": {"labels": {"ciu.run.id": "caller"}}}},
            service_name="worker", base_service=_service(), secret_values=set(),
        )


def test_run_plan_refuses_missing_rendered_service_and_sanitizes_compose_errors(
    tmp_path, monkeypatch,
):
    prepared = _prepared(tmp_path)
    compose_config = oneshot._compose_config
    monkeypatch.setattr(
        oneshot, "_compose_config",
        lambda _prepared: {"services": {"other": {"image": "busybox"}}},
    )
    with pytest.raises(ValueError, match="service 'worker' is not present"):
        oneshot.build_run_plan(
            prepared, stack="tools/runner", service="worker",
        )

    def failed_compose(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "secret output")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", failed_compose)
    with pytest.raises(RuntimeError, match="exit 1") as failure:
        compose_config(prepared)
    assert "secret output" not in str(failure.value)


def test_compose_config_refuses_when_docker_is_unavailable(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)

    def no_docker(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", no_docker)
    with pytest.raises(RuntimeError, match="Docker is unavailable"):
        oneshot._compose_config(prepared)


def test_run_plan_rejects_secret_in_projected_user_labels(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)
    monkeypatch.setattr(
        oneshot, "_compose_config",
        lambda _prepared: {"services": {"worker": _service()}},
    )
    with pytest.raises(ValueError, match="run plan contains a CIU secret"):
        oneshot.build_run_plan(
            prepared, stack="tools/runner", service="worker",
            labels=["run-gate.note=planted-secret-value"],
        )
    with pytest.raises(ValueError, match="read-only bind"):
        oneshot.validate_extension_document(
            {
                "services": {"worker": {"volumes": [{
                    "type": "bind", "source": "/run/other", "target": "/run/observer",
                    "read_only": False,
                }]}},
                "x-ciu-run": {"observer-socket-dir": "/run/observer"},
            },
            service_name="worker", base_service=_service(), secret_values=set(),
        )
