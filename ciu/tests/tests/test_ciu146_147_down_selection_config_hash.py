"""CIU-146 (restricted ``down``) and CIU-147 (config-content recreate stamp).

Only the Docker process boundary is faked; ``action_stop``, the compose
project resolution and ``generate_overlay`` run for real.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from ciu import deploy
from ciu.composefile import CONFIG_HASH_LABEL, ConfigFileMount, generate_overlay

CONFIG = {"deploy": {"project_name": "proj", "environment_tag": "dev"}}

# compose project -> containers (all running or exited)
WORLD = {
    "proj-dev-core": ["proj-dev-vault", "proj-dev-vault-init"],
    "proj-dev-apps": ["proj-dev-api", "proj-dev-web"],
    "proj-dev-db": ["proj-dev-pg"],
}


class FakeDocker:
    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kw):
        self.calls.append(list(cmd))
        if cmd[0] == "stop":
            self.stopped.extend(cmd[1:])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "ps":
            label = next((c for c in cmd if c.startswith("label=")), None)
            if label:
                project = label.split("=", 2)[2]
                names = WORLD.get(project, [])
            else:
                names = [n for ns in WORLD.values() for n in ns]
            return subprocess.CompletedProcess(cmd, 0, "\n".join(names) + "\n", "")
        raise AssertionError(cmd)


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(deploy.procutil, "docker", fake)
    return fake


def _repo(tmp_path: Path) -> tuple[Path, list[dict]]:
    for name in ("core", "apps", "db"):
        (tmp_path / "stacks" / name).mkdir(parents=True)
    selection = [
        {"path": f"stacks/{n}", "name": n, "phase_key": "phase_1"}
        for n in ("apps",)
    ]
    return tmp_path, selection


def test_restricted_stop_stops_only_selected_stack(docker, tmp_path):
    """Oracle 1: selecting `apps` leaves core and db containers alone."""
    root, selection = _repo(tmp_path)
    assert deploy.action_stop(CONFIG, repo_root=root, selection=selection) == 0
    assert sorted(docker.stopped) == ["proj-dev-api", "proj-dev-web"]


def test_restricted_stop_two_stacks(docker, tmp_path):
    root, _ = _repo(tmp_path)
    selection = [
        {"path": "stacks/core", "name": "core", "phase_key": "phase_1"},
        {"path": "stacks/db", "name": "db", "phase_key": "phase_2"},
    ]
    assert deploy.action_stop(CONFIG, repo_root=root, selection=selection) == 0
    assert sorted(docker.stopped) == [
        "proj-dev-pg", "proj-dev-vault", "proj-dev-vault-init",
    ]


def test_bare_stop_is_still_project_wide(docker):
    """Oracle 3: regression guard for the unrestricted verb."""
    assert deploy.action_stop(CONFIG) == 0
    assert sorted(docker.stopped) == sorted(n for ns in WORLD.values() for n in ns)


def test_restricted_stop_with_empty_selection_refuses_instead_of_widening(docker, tmp_path):
    root, _ = _repo(tmp_path)
    assert deploy.action_stop(CONFIG, repo_root=root, selection=[]) == 1
    assert docker.stopped == []
    assert not any(c[0] == "stop" for c in docker.calls)


def test_restricted_stop_without_root_refuses(docker):
    assert deploy.action_stop(CONFIG, selection=[{"path": "x"}]) == 1
    assert docker.stopped == []


def test_restricted_stop_daemon_failure_refuses(monkeypatch, tmp_path):
    root, selection = _repo(tmp_path)
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 1, "", "daemon down"),
    )
    assert deploy.action_stop(CONFIG, repo_root=root, selection=selection) == 1


def _mount(stack: Path, content: str) -> ConfigFileMount:
    rendered = stack / ".ciu" / "rendered" / "svc" / "etc" / "svc" / "config.toml"
    rendered.parent.mkdir(parents=True, exist_ok=True)
    rendered.write_text(content, encoding="utf-8")
    return ConfigFileMount(
        service="svc", name="main", rendered_path=rendered,
        target="/etc/svc/config.toml",
    )


def _label(stack: Path) -> str:
    doc = yaml.safe_load((stack / ".ciu" / "ciu.compose.overlay.yml").read_text())
    return doc["services"]["svc"]["labels"][CONFIG_HASH_LABEL]


def test_overlay_label_changes_with_content_and_is_stable_otherwise(tmp_path, monkeypatch):
    """Oracles 1+2: changed content changes the label; same content does not."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path))
    stack = tmp_path / "stack"
    stack.mkdir()

    generate_overlay(stack, {}, [_mount(stack, "a = 1\n")])
    first = _label(stack)
    generate_overlay(stack, {}, [_mount(stack, "a = 1\n")])
    assert _label(stack) == first
    generate_overlay(stack, {}, [_mount(stack, "a = 2\n")])
    assert _label(stack) != first


def _run_down(monkeypatch, tmp_path, *, profile_arg, phases_arg=None):
    import argparse

    from ciu.deploy_pkg.profiles import Profile

    root, selection = _repo(tmp_path)
    profile = Profile(name="apps", config=CONFIG)
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_k: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _c: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _d: root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _r: {})
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _c, _n: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _p, _ph: selection)
    monkeypatch.setattr(deploy, "build_action_sequence", lambda _raw: ["stop"])
    args = argparse.Namespace(
        define_root=None, update_cert_permission=False, profile=profile_arg,
        phases=phases_arg, dry_run=False, no_preflight=False, ignore_errors=False,
    )
    return deploy._run(args, ["--stop"])


def test_down_with_profile_flag_is_restricted(docker, monkeypatch, tmp_path):
    assert _run_down(monkeypatch, tmp_path, profile_arg=["apps"]) == 0
    assert sorted(docker.stopped) == ["proj-dev-api", "proj-dev-web"]


def test_down_with_phases_flag_is_restricted(docker, monkeypatch, tmp_path):
    assert _run_down(monkeypatch, tmp_path, profile_arg=None, phases_arg="1") == 0
    assert sorted(docker.stopped) == ["proj-dev-api", "proj-dev-web"]


def test_bare_down_is_project_wide(docker, monkeypatch, tmp_path):
    assert _run_down(monkeypatch, tmp_path, profile_arg=None) == 0
    assert len(docker.stopped) == 5
