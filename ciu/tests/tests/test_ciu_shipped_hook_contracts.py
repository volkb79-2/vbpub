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


def test_post_compose_example_persists_its_token_into_the_secret_store(
    tmp_path: Path,
) -> None:
    """The copyable post-compose example persists exactly its advertised token.

    ciu-P46: through the SECRET store (S9.4a), not `[state]`. A copyable
    example that wrote a secret-shaped key into `[state]` would hand every
    consumer who copied it a stack `ciu check` refuses (S3.4a).
    """
    import stat

    from ciu.secrets.materialize import hook_secret_store, read_hook_manifest

    config = {"deploy": {"project_name": "sample", "environment_tag": "ci"}}
    state_path = tmp_path / "ciu.toml"
    run_hooks(
        [str(REPO_ROOT / "src/ciu/hooks/examples/post_compose_example.py")],
        "post_compose",
        config,
        _ctx(tmp_path, point="post_compose", secret_file=lambda name: tmp_path / name),
        state_path,
    )

    store = hook_secret_store(tmp_path, "root_token")
    assert store.read_text(encoding="utf-8") == "placeholder-sample-ci"
    assert stat.S_IMODE(store.stat().st_mode) == 0o440
    assert "root_token" in read_hook_manifest(tmp_path)
    # Nothing was written to [state] at all — the file is never created.
    assert not state_path.exists()


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


def test_demo_vault_hook_persists_only_the_non_secret_initialized_flag(
    tmp_path: Path,
) -> None:
    """ciu-P46/F4 — the demo bootstrap hook writes NO token into [state].

    Before ciu-P46 this hook re-read its own GEN_LOCAL root token and
    re-persisted it into `[state]`, purely so S4.16's old source #3 could find
    it there. Source #3 now reads a hook-persisted secret store file (S9.4a),
    so the second, unmasked, un-leak-scanned plaintext copy is simply gone.
    `initialized` is a plain boolean fact and stays exactly where it was.
    """
    stack = TEST_REPO / "infra" / "vault"
    declared = _declared_hook(stack, "vault_core", "post_compose")
    config = {"vault_core": {}}
    state_path = tmp_path / "ciu.toml"

    run_hooks(
        [str(stack / declared)],
        "post_compose",
        config,
        _ctx(tmp_path, point="post_compose", secret_file=_unknown_secret),
        state_path,
    )

    with state_path.open("rb") as fh:
        saved = tomllib.load(fh)
    assert config["initialized"] is True
    assert saved["state"] == {"initialized": True}
    assert "root_token" not in saved["state"]


def test_demo_vault_stack_declares_no_secret_shaped_state_key(tmp_path: Path) -> None:
    """The fixture's own `[state]` table passes S3.4a's always-on rule.

    `ciu check`'s `state-secrets` stage refuses a secret-shaped `[state]` key
    outright; CIU's own canonical reference fixture must be the first thing
    that satisfies it, or the rule ships already violated by its own example.
    """
    _ = tmp_path
    import tomllib as _tomllib

    from ciu.config_model import find_secret_shaped_keys

    text = (TEST_REPO / "infra" / "vault" / "ciu.defaults.toml.j2").read_text(
        encoding="utf-8"
    )
    parsed = _tomllib.loads(text)
    assert find_secret_shaped_keys(parsed.get("state")) == []
