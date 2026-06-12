"""CIU v2 engine -> hooks_runner wiring (S8.3 / S9).

The v1 ``engine.execute_hooks`` is withdrawn (Appendix A): hooks now run via
``hooks_runner.run_hooks`` from inside ``main_execution`` at the three S8.3 hook
points. This test exercises that wiring end-to-end with a minimal synthetic
stack run in ``--dry-run`` mode (no docker): a ``pre_compose`` hook returns the
v2 structured form, and we assert both ``apply_to_config`` (visible in the
rendered compose) and ``persist: 'state'`` (written to ciu.toml [state]) took
effect through the engine.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from ciu import engine  # noqa: E402


GLOBAL_DEFAULTS = """\
[ciu]
require_fqdn = false
require_certs = false
auto_connect_network = false

[deploy]
project_name = "hooks-test"
environment_tag = "test"
log_level = "INFO"

[deploy.env.shared]
CONTAINER_UID = "$CONTAINER_UID"
CONTAINER_GID = "$CONTAINER_GID"
DOCKER_GID = "$DOCKER_GID"
"""

STACK_DEFAULTS = """\
[demo]
name = "demo"
image = "alpine:3"

[demo.env]
SEEDED = "no"

[demo.hooks]
pre_compose = ["./hook.py"]
"""

COMPOSE = """\
services:
  {{ demo.name }}:
    image: {{ demo.image }}
    environment:
      - SEEDED={{ demo.env.SEEDED }}
"""

HOOK = """\
def run(config, ctx):
    return {
        "demo.env.SEEDED": {"value": "yes", "apply_to_config": True},
        "seeded": {"value": True, "apply_to_config": True, "persist": "state"},
    }
"""


def _write_repo(tmp_path: Path) -> Path:
    (tmp_path / "ciu-global.defaults.toml.j2").write_text(GLOBAL_DEFAULTS)
    stack = tmp_path / "applications" / "demo"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text(STACK_DEFAULTS)
    (stack / "docker-compose.yml.j2").write_text(COMPOSE)
    (stack / "hook.py").write_text(HOOK)
    (tmp_path / ".gitignore").write_text("**/.ciu/\n")
    return stack


def _set_env(tmp_path: Path) -> None:
    os.environ["REPO_ROOT"] = str(tmp_path)
    os.environ["PHYSICAL_REPO_ROOT"] = str(tmp_path)
    os.environ["DOCKER_NETWORK_INTERNAL"] = "hooks-test-net"
    os.environ["CONTAINER_UID"] = "1000"
    os.environ["CONTAINER_GID"] = "1000"
    os.environ["DOCKER_GID"] = "999"
    os.environ["SKIP_DEPENDENCY_CHECK"] = "1"
    os.environ["CIU_SKIP_DOOD_PREFLIGHT"] = "1"


def test_engine_runs_pre_compose_hook_apply_and_persist(tmp_path, monkeypatch):
    stack = _write_repo(tmp_path)
    _set_env(tmp_path)
    # Avoid generating .env.ciu (machine detection) and skip the network step.
    (tmp_path / ".env.ciu").write_text(
        "\n".join(
            f'export {k}="{os.environ[k]}"'
            for k in (
                "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
                "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID",
            )
        )
        + "\n"
    )
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

    result = engine.main_execution(
        working_dir=stack,
        dry_run=True,
        define_root=tmp_path,
        skip_hostdir_check=True,
    )

    assert result.get("status") == "success"

    # apply_to_config reached the compose render (step 13, after step 11 hook).
    rendered = (stack / "docker-compose.yml").read_text()
    assert "SEEDED=yes" in rendered

    # persist: 'state' wrote to ciu.toml [state] via hooks_runner.
    state = tomllib.loads((stack / "ciu.toml").read_text())
    assert state.get("state", {}).get("seeded") is True


def test_engine_skip_hooks_bypasses_hook(tmp_path, monkeypatch):
    stack = _write_repo(tmp_path)
    _set_env(tmp_path)
    (tmp_path / ".env.ciu").write_text(
        "\n".join(
            f'export {k}="{os.environ[k]}"'
            for k in (
                "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
                "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID",
            )
        )
        + "\n"
    )
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)

    result = engine.main_execution(
        working_dir=stack,
        dry_run=True,
        define_root=tmp_path,
        skip_hostdir_check=True,
        skip_hooks=True,
    )

    assert result.get("status") == "success"
    rendered = (stack / "docker-compose.yml").read_text()
    assert "SEEDED=no" in rendered  # hook did not run
