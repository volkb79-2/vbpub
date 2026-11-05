"""CIU-141 selection, plan projection, and typed extension contracts."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
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


def test_dry_run_cli_prints_only_allowlisted_plan_json(tmp_path, monkeypatch, capsys):
    prepared = _prepared(tmp_path)
    def prepare(*args, **kwargs):
        print("[STEP 1/17] test preparation")
        return prepared, "tools/runner", "worker"

    monkeypatch.setattr(oneshot, "_prepare_run_stack", prepare)
    monkeypatch.setattr(
        oneshot, "build_run_plan",
        lambda *args, **kwargs: {"schema": "ciu-run-plan/1", "name": "rg-one"},
    )

    result = oneshot.run_cli(
        tmp_path, "tools/runner:worker", ["python", "-c", "print('ok')"],
        dry_run=True, json_output=True, env_names=["OPTIONAL_DSN"],
    )

    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {
        "schema": "ciu-run-plan/1", "name": "rg-one",
    }
    assert "STEP" not in captured.out
    assert "STEP" in captured.err  # preparation output is isolated from plan stdout


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
    assert validated["services"]["worker"]["volumes"][0]["bind"] == {
        "create_host_path": False,
    }


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("user", "0:0", "user"),
        ("cgroup_parent", "evil.slice", "cgroup_parent"),
        ("cap_add", ["SYS_ADMIN"], "cap_add"),
        ("security_opt", ["apparmor=unconfined"], "security_opt"),
        ("ports", ["8080:8080"], "ports"),
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
        ({"volumes": [{
            "type": "bind", "source": "/tmp/other", "target": "/workspace",
        }]}, "target '/workspace'"),
    ],
)
def test_extension_refuses_silent_compose_collisions(extension_service, reason):
    base_service = _service()
    if "init" in extension_service:
        base_service["init"] = False
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
