"""
CIU test-repo validation.
"""
from __future__ import annotations

import os
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import deploy  # noqa: E402
from ciu import engine  # noqa: E402
from ciu.render_utils import load_global_config, render_global_config_if_missing, render_stack_configs  # noqa: E402
from ciu.workspace_env import bootstrap_workspace_env, detect_standalone_root  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"


def _set_env_defaults() -> None:
    os.environ.setdefault("DOCKER_GID", "999")
    os.environ.setdefault("CONTAINER_UID", "1000")
    os.environ.setdefault("CONTAINER_GID", "999")
    os.environ.setdefault("USER_UID", "1000")
    os.environ.setdefault("USER_GID", "1000")
    os.environ.setdefault("USER_NAME", "tester")
    os.environ.setdefault("DOCKER_UID", "1000")
    os.environ.setdefault("PUBLIC_FQDN", "example.test")
    os.environ.setdefault("PUBLIC_IP", "127.0.0.1")
    os.environ["REPO_ROOT"] = str(TEST_REPO)
    os.environ["PHYSICAL_REPO_ROOT"] = str(TEST_REPO)
    os.environ["DOCKER_NETWORK_INTERNAL"] = "ciu-test-network"
    # v2 engine-path tests run --dry-run / --render-toml (no docker):
    os.environ["SKIP_DEPENDENCY_CHECK"] = "1"
    os.environ["CIU_SKIP_DOOD_PREFLIGHT"] = "1"


def test_test_repo_exists() -> None:
    assert (TEST_REPO / "ciu-global.defaults.toml.j2").exists()
    assert (TEST_REPO / "applications" / "app-simple" / "ciu.defaults.toml.j2").exists()
    assert (TEST_REPO / "applications" / "app-vault" / "pre_compose_hook.py").exists()
    assert (TEST_REPO / "infra" / "vault-core" / "post_compose_hook.py").exists()
    assert (TEST_REPO / "infra" / "consul-core" / "post_compose_hook.py").exists()


def test_bootstrap_workspace_env_generates_env_file(monkeypatch) -> None:
    _set_env_defaults()

    monkeypatch.chdir(TEST_REPO)
    env_root = bootstrap_workspace_env(
        start_dir=TEST_REPO,
        define_root=None,
        defaults_filename="ciu-global.defaults.toml.j2",
        generate_env=True,
        update_cert_permission=False,
        required_keys=[
            "REPO_ROOT",
            "PHYSICAL_REPO_ROOT",
            "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID",
            "DOCKER_GID",
            "PUBLIC_FQDN",
            "PUBLIC_TLS_CRT_PEM",
            "PUBLIC_TLS_KEY_PEM",
        ],
    )

    assert (env_root / ".env.ciu").exists()


def test_render_global_and_stack_configs(monkeypatch) -> None:
    _set_env_defaults()
    monkeypatch.chdir(TEST_REPO)

    bootstrap_workspace_env(
        start_dir=TEST_REPO,
        define_root=None,
        defaults_filename="ciu-global.defaults.toml.j2",
        generate_env=True,
        update_cert_permission=False,
        required_keys=[
            "REPO_ROOT",
            "PHYSICAL_REPO_ROOT",
            "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID",
            "DOCKER_GID",
            "PUBLIC_FQDN",
            "PUBLIC_TLS_CRT_PEM",
            "PUBLIC_TLS_KEY_PEM",
        ],
    )

    render_global_config_if_missing(TEST_REPO)
    global_config = load_global_config(TEST_REPO)

    stack_paths = {
        TEST_REPO / "applications" / "app-simple",
        TEST_REPO / "applications" / "app-vault",
        TEST_REPO / "infra" / "vault-core",
        TEST_REPO / "infra" / "consul-core",
    }
    render_stack_configs(stack_paths, global_config, preserve_state=True)

    for stack_path in stack_paths:
        assert (stack_path / "ciu.toml").exists()


def test_ciu_main_execution_runs_hooks(monkeypatch) -> None:
    _set_env_defaults()

    # No docker in this path: stub the network attach step.
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

    stack = TEST_REPO / "applications" / "app-vault"
    monkeypatch.chdir(stack)
    result = engine.main_execution(
        working_dir=stack,
        dry_run=True,
        print_context=False,
        generate_env=True,
    )

    assert result.get("status") == "success"
    rendered = stack / "ciu.toml"
    assert rendered.exists()

    # The v2 pre_compose hook applied VAULT_BOOTSTRAP_TOKEN to the config
    # (apply_to_config), so the rendered compose carries it (steps 11 -> 13).
    compose = (stack / "docker-compose.yml").read_text()
    assert "VAULT_BOOTSTRAP_TOKEN=demo-token" in compose


def test_detects_standalone_root() -> None:
    standalone_root = TEST_REPO / "standalone" / "project"
    detected = detect_standalone_root(standalone_root / "app")
    assert detected == standalone_root


def test_deploy_render_all_configs_respects_phases(monkeypatch) -> None:
    _set_env_defaults()
    monkeypatch.chdir(TEST_REPO)

    bootstrap_workspace_env(
        start_dir=TEST_REPO,
        define_root=None,
        defaults_filename="ciu-global.defaults.toml.j2",
        generate_env=True,
        update_cert_permission=False,
        required_keys=[
            "REPO_ROOT",
            "PHYSICAL_REPO_ROOT",
            "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID",
            "DOCKER_GID",
            "PUBLIC_FQDN",
            "PUBLIC_TLS_CRT_PEM",
            "PUBLIC_TLS_KEY_PEM",
        ],
    )

    app_simple_rendered = TEST_REPO / "applications" / "app-simple" / "ciu.toml"
    if app_simple_rendered.exists():
        app_simple_rendered.unlink()

    global_config = render_global_config_if_missing(TEST_REPO)
    phases = deploy.load_deployment_phases(global_config)
    deploy.render_all_configs(TEST_REPO, phases, selected_phases=[1])

    assert (TEST_REPO / "infra" / "vault-core" / "ciu.toml").exists()
    assert (TEST_REPO / "infra" / "consul-core" / "ciu.toml").exists()
    assert not app_simple_rendered.exists()
