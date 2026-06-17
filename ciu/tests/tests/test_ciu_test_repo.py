"""CIU v2 reference-demo (test-repo/) validation.

The demo workspace under ``test-repo/`` is both the integration fixture and the
documentation-by-example for v2. These tests pin its layout to the spec.

Coverage split (intentional — see the per-test comments):

* **Render-TOML level** (vault / redis-core / db-core): rendering ``ciu.toml``
  needs no Vault, but the FULL engine pipeline does — ``--dry-run`` still runs
  step 10 (materialize), and the data stacks declare ``GEN_TO_VAULT`` secrets,
  so they cannot dry-run without a live Vault. Their start-ordering / vault
  preflight is already covered at the deploy level (test_ciu_deploy_actions.py),
  so here we only assert their TOML renders.
* **Full engine dry-run** (app-config): its four directives (GEN_LOCAL /
  ASK_EXTERNAL / GEN_EPHEMERAL / ASK_FILE) avoid Vault by design, so its entire
  pipeline — incl. the pre_compose hook, configfile render, leak scan and
  overlay — runs under ``--dry-run`` with no docker and no Vault.

``standalone/`` is exercised by ``test_detects_standalone_root`` and is left
untouched by P11.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import config_model  # noqa: E402
from ciu import deploy  # noqa: E402
from ciu import engine  # noqa: E402
from ciu.deploy_pkg import profiles as profiles_pkg  # noqa: E402
from ciu.workspace_env import bootstrap_workspace_env, detect_standalone_root  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"

# The four demo stacks and their relative paths (S3 layout).
VAULT_STACK = TEST_REPO / "infra" / "vault"
REDIS_STACK = TEST_REPO / "infra" / "redis-core"
DB_STACK = TEST_REPO / "infra" / "db-core"
APP_STACK = TEST_REPO / "applications" / "app-config"

# Vault-backed stacks: render-toml only (see module docstring).
RENDER_ONLY_STACKS = (VAULT_STACK, REDIS_STACK, DB_STACK)


def _set_env_defaults() -> None:
    uid = str(os.getuid())
    gid = str(os.getgid())
    os.environ["DOCKER_GID"] = gid
    os.environ["CONTAINER_UID"] = uid
    os.environ["CONTAINER_GID"] = gid
    os.environ["USER_UID"] = uid
    os.environ["USER_GID"] = gid
    os.environ.setdefault("USER_NAME", "tester")
    os.environ["DOCKER_UID"] = uid
    os.environ.setdefault("PUBLIC_FQDN", "example.test")
    os.environ.setdefault("PUBLIC_IP", "127.0.0.1")
    os.environ["REPO_ROOT"] = str(TEST_REPO)
    os.environ["PHYSICAL_REPO_ROOT"] = str(TEST_REPO)
    os.environ["DOCKER_NETWORK_INTERNAL"] = "ciu-test-network"
    # app-config's ASK_EXTERNAL `license` secret (S4.13) — supplied so the full
    # dry-run engine test resolves it without prompting.
    os.environ.setdefault("CIU_SECRET_LICENSE", "demo")
    # v2 engine-path tests run --dry-run / --render-toml (no docker):
    os.environ["SKIP_DEPENDENCY_CHECK"] = "1"
    os.environ["CIU_SKIP_DOOD_PREFLIGHT"] = "1"


def _clean_stack_artifacts(stack_dir: Path) -> None:
    """Remove a stack's machine-generated artifacts so the test is deterministic.

    Running CIU's hostdir step without chown privilege (as in CI / a plain
    devcontainer) leaves ``vol-*`` dirs owned by the process UID; a re-run would
    then trip the S6.3 incompatible-ownership check on its own leftovers. These
    paths are all gitignored.
    """
    for vol in stack_dir.glob("vol-*"):
        shutil.rmtree(vol, ignore_errors=True)
    shutil.rmtree(stack_dir / ".ciu", ignore_errors=True)
    for name in ("ciu.toml", "ciu.toml.j2", "ciu.compose.yml"):
        (stack_dir / name).unlink(missing_ok=True)


def _bootstrap(monkeypatch) -> None:
    monkeypatch.chdir(TEST_REPO)
    bootstrap_workspace_env(
        start_dir=TEST_REPO,
        define_root=None,
        defaults_filename="ciu.global.defaults.toml.j2",
        generate_env=True,
        update_cert_permission=False,
        required_keys=[
            "REPO_ROOT",
            "PHYSICAL_REPO_ROOT",
            "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID",
            "DOCKER_GID",
            "PUBLIC_FQDN",
        ],
    )


def test_test_repo_exists() -> None:
    # Global config + every demo stack's committed source files (S3.1).
    assert (TEST_REPO / "ciu.global.defaults.toml.j2").exists()
    assert (TEST_REPO / "README.md").exists()
    assert (VAULT_STACK / "ciu.defaults.toml.j2").exists()
    assert (VAULT_STACK / "post_compose_vault.py").exists()
    assert (REDIS_STACK / "ciu.defaults.toml.j2").exists()
    assert (DB_STACK / "ciu.defaults.toml.j2").exists()
    assert (APP_STACK / "ciu.defaults.toml.j2").exists()
    assert (APP_STACK / "config.toml.j2").exists()
    assert (APP_STACK / "pre_compose_app.py").exists()
    assert (APP_STACK / "files" / "demo-ca.pem").exists()
    # The obsolete v1 fixtures are gone (P11 removal).
    assert not (TEST_REPO / "applications" / "app-simple").exists()
    assert not (TEST_REPO / "applications" / "app-vault").exists()
    assert not (TEST_REPO / "infra" / "vault-core").exists()
    assert not (TEST_REPO / "infra" / "consul-core").exists()


def test_bootstrap_workspace_env_generates_env_file(monkeypatch) -> None:
    _set_env_defaults()
    _bootstrap(monkeypatch)
    assert (TEST_REPO / "ciu.env").exists()


def test_render_global_and_stack_configs(monkeypatch) -> None:
    _set_env_defaults()
    _bootstrap(monkeypatch)

    global_config = config_model.render_global_chain(TEST_REPO, TEST_REPO)

    # All four stacks render their ciu.toml (S3.4); root-key validation passes.
    for stack_path in (VAULT_STACK, REDIS_STACK, DB_STACK, APP_STACK):
        stack_config = config_model.render_stack(
            stack_path, global_config, preserve_state=True
        )
        config_model.validate_stack_shape(stack_config)
        assert (stack_path / "ciu.toml").exists()


def test_app_config_full_pipeline_runs_under_dry_run(monkeypatch) -> None:
    # app-config's four directives avoid Vault by design, so the ENTIRE engine
    # pipeline (pre_compose hook -> configfile -> leak scan -> overlay) runs
    # under --dry-run with no docker and no Vault (S8.3).
    _set_env_defaults()
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")  # ASK_EXTERNAL (S4.13)
    _clean_stack_artifacts(APP_STACK)  # deterministic hostdir creation (S6.3)
    # No docker in this path: stub the network attach step.
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

    monkeypatch.chdir(APP_STACK)
    result = engine.main_execution(
        working_dir=APP_STACK,
        dry_run=True,
        print_context=False,
        generate_env=True,
    )

    assert result.get("status") == "success"
    assert (APP_STACK / "ciu.toml").exists()

    # The pre_compose hook applied app_config.runtime_note via apply_to_config
    # (S9.4), so the rendered configfile (step 12, after the step 11 hook)
    # carries it.
    rendered_cfg = (APP_STACK / ".ciu" / "rendered" / "app" / "main").read_text()
    assert 'runtime_note = "set-by-hook"' in rendered_cfg
    # The configfile is the ONLY place a secret value may appear (S5.4): the
    # ASK_EXTERNAL license value is embedded via secret('license').
    assert 'license = "demo"' in rendered_cfg

    # The overlay declares all four secrets + the configfile mount (S4.17/S5.3).
    overlay = (APP_STACK / ".ciu" / "ciu.compose.overlay.yml").read_text()
    for name in ("api_key", "license", "run_nonce", "ca_bundle"):
        assert name in overlay
    assert "/etc/app/config.toml" in overlay


def test_app_config_secrets_list(monkeypatch) -> None:
    # `ciu secrets list` reports name/kind/locator/store/exists — never values
    # (S4.25). All four directive kinds appear.
    from ciu.secrets import directives as secret_directives
    from ciu.secrets import materialize as secret_materialize

    _set_env_defaults()
    _bootstrap(monkeypatch)

    global_config = config_model.render_global_chain(TEST_REPO, TEST_REPO)
    stack_config = config_model.render_stack(APP_STACK, global_config, preserve_state=True)
    root_key = config_model.validate_stack_shape(stack_config)
    merged = config_model.deep_merge(global_config, stack_config)
    specs = secret_directives.discover(root_key, merged)

    rows = secret_materialize.list_secrets(specs, APP_STACK, TEST_REPO)
    by_name = {r["name"]: r for r in rows}
    assert by_name["api_key"]["kind"] == "GEN_LOCAL"
    assert by_name["license"]["kind"] == "ASK_EXTERNAL"
    assert by_name["run_nonce"]["kind"] == "GEN_EPHEMERAL"
    assert by_name["ca_bundle"]["kind"] == "ASK_FILE"


def test_detects_standalone_root() -> None:
    standalone_root = TEST_REPO / "standalone" / "project"
    detected = detect_standalone_root(standalone_root / "app")
    assert detected == standalone_root


def test_deploy_render_all_configs_respects_phases(monkeypatch) -> None:
    _set_env_defaults()
    _bootstrap(monkeypatch)

    app_rendered = APP_STACK / "ciu.toml"
    if app_rendered.exists():
        app_rendered.unlink()

    # v2 render path: load global -> resolve the core_infra profile -> build a
    # phase-restricted selection -> render only those stacks (S7.1 / S8.3 step 3).
    global_config = deploy.load_global_config(TEST_REPO)
    profile = profiles_pkg.resolve_profile(global_config, "core_infra")
    selection = deploy.build_selection(profile, cli_phases={"phase_1"})
    rendered = deploy.render_selected_stacks(TEST_REPO, profile, selection)

    # Only phase_1 (Vault) is rendered; phase_2/phase_3 stacks are not.
    assert "infra/vault" in rendered
    assert "infra/redis-core" not in rendered
    assert "applications/app-config" not in rendered

    assert (VAULT_STACK / "ciu.toml").exists()
    assert not app_rendered.exists()


def test_deploy_profiles_and_phases_match_spec(monkeypatch) -> None:
    # Pin the global profile/phase wiring authored in ciu.global.defaults.toml.j2
    # (S7.1 numeric phases, S7.4 profiles, S7.5a topology_overrides).
    _set_env_defaults()
    _bootstrap(monkeypatch)
    global_config = deploy.load_global_config(TEST_REPO)

    phases = global_config["deploy"]["phases"]
    assert set(phases) == {"phase_1", "phase_2", "phase_3"}
    assert phases["phase_1"]["services"][0]["path"] == "infra/vault"
    # phase_3's app uses the string control-flag form of `enabled` (S7.2).
    assert phases["phase_3"]["services"][0]["enabled"] == "enable_app"
    assert global_config["deploy"]["control"]["enable_app"] is True

    profiles = global_config["deploy"]["profiles"]
    assert profiles["core_infra"]["phases"] == ["phase_1", "phase_2"]
    assert profiles["workers"]["phases"] == ["phase_3"]
    assert profiles["all"]["phases"] == ["phase_1", "phase_2", "phase_3"]
    # S7.5a: the workers profile carries a topology_overrides for Vault.
    workers_topo = profiles["workers"]["topology_overrides"]["services"]["vault"]
    assert workers_topo["internal_host"] == "ciudemo-dev-vault"
