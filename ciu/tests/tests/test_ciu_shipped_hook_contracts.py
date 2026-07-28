"""Executable contracts for CIU's shipped and test-repo hook templates.

The generic runner tests deliberately use tiny synthetic hooks.  These tests
exercise the files users are instructed to copy or that CIU ships in its demo
repository through the real runner, so a stale path, signature, result shape,
or state/config side effect cannot be hidden by a mere interface check.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"
sys.path.insert(0, str(REPO_ROOT / "src"))

from ciu.hooks_runner import HookContext, HookExecutionError, run_hooks  # noqa: E402


def _ctx(tmp_path: Path, *, point: str, secret_file) -> HookContext:
    return HookContext(
        point=point,
        stack_dir=tmp_path,
        repo_root=tmp_path,
        secret_file=secret_file,
    )


def _declared_hook(stack: Path, root_key: str, point: str) -> str:
    """Read the hook path from the actual committed stack template."""
    with (stack / "ciu.defaults.toml.j2").open("rb") as fh:
        config = tomllib.load(fh)
    return config[root_key]["hooks"][point][0]


def _unknown_secret(name: str) -> Path:
    raise KeyError(name)


def test_pre_compose_example_applies_value_visible_to_templates(tmp_path: Path) -> None:
    """The copyable example's documented dotted config update is executable."""
    config = {"deploy": {"project_name": "sample"}}
    run_hooks(
        [str(REPO_ROOT / "src/ciu/hooks/examples/pre_compose_example.py")],
        "pre_compose",
        config,
        _ctx(tmp_path, point="pre_compose", secret_file=lambda name: tmp_path / name),
        tmp_path / "ciu.toml",
    )

    assert config["deploy"]["computed_tag"] == "sample-ready"


def test_post_compose_example_persists_project_runtime_state(tmp_path: Path) -> None:
    """The copyable post-compose example persists exactly its advertised token."""
    config = {"deploy": {"project_name": "sample", "environment_tag": "ci"}}
    state_path = tmp_path / "ciu.toml"
    run_hooks(
        [str(REPO_ROOT / "src/ciu/hooks/examples/post_compose_example.py")],
        "post_compose",
        config,
        _ctx(tmp_path, point="post_compose", secret_file=lambda name: tmp_path / name),
        state_path,
    )

    with state_path.open("rb") as fh:
        saved = tomllib.load(fh)
    assert saved["state"] == {"root_token": "placeholder-sample-ci"}


def test_demo_app_hook_path_applies_runtime_note_for_configfile_render(tmp_path: Path) -> None:
    """The hook declared by app-config changes the value its config template reads."""
    stack = TEST_REPO / "applications" / "app-config"
    declared = _declared_hook(stack, "app_config", "pre_compose")
    config = {"app_config": {"runtime_note": "not-run"}}

    run_hooks(
        [str(stack / declared)],
        "pre_compose",
        config,
        _ctx(tmp_path, point="pre_compose", secret_file=lambda name: tmp_path / name),
        tmp_path / "ciu.toml",
    )

    assert config["app_config"]["runtime_note"] == "set-by-hook"
    assert 'runtime_note = "{{ app_config.runtime_note }}"' in (
        stack / "config.toml.j2"
    ).read_text(encoding="utf-8")


def test_demo_vault_hook_reads_secret_and_persists_bootstrap_state(tmp_path: Path) -> None:
    """The declared bootstrap hook carries its resolved token into state."""
    stack = TEST_REPO / "infra" / "vault"
    declared = _declared_hook(stack, "vault_core", "post_compose")
    secret_path = tmp_path / "root_token"
    secret_path.write_text("s.demo-token\n", encoding="utf-8")
    config = {"vault_core": {}}
    state_path = tmp_path / "ciu.toml"

    run_hooks(
        [str(stack / declared)],
        "post_compose",
        config,
        _ctx(
            tmp_path,
            point="post_compose",
            secret_file=lambda name: secret_path if name == "root_token" else _unknown_secret(name),
        ),
        state_path,
    )

    with state_path.open("rb") as fh:
        saved = tomllib.load(fh)
    assert config["initialized"] is True
    assert saved["state"] == {"initialized": True, "root_token": "s.demo-token"}


def test_demo_vault_hook_fails_when_required_secret_context_is_unavailable(
    tmp_path: Path,
) -> None:
    """A broken secret-file context is a runtime hook failure, never a green no-op."""
    stack = TEST_REPO / "infra" / "vault"
    declared = _declared_hook(stack, "vault_core", "post_compose")

    with pytest.raises(HookExecutionError, match="root_token"):
        run_hooks(
            [str(stack / declared)],
            "post_compose",
            {"vault_core": {}},
            _ctx(
                tmp_path,
                point="post_compose",
                secret_file=_unknown_secret,
            ),
            tmp_path / "ciu.toml",
        )
