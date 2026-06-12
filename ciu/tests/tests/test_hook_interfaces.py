"""
Contract tests for the demo hook interfaces (S9.1).

Validates that the test-repo hook modules expose the v2 ``run(config, ctx)``
callable without executing Docker/Vault operations. The v1 per-point class names
(PostComposeHook, ...) are withdrawn (S9.1); v2 hooks are a module-level ``run``
function (or a ``Hook`` class with a ``run`` method).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"


def _load_module(module_path: Path):
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_v2_run_interface(module) -> None:
    # S9.1: module-level run(config, ctx), or a Hook class exposing run.
    run = getattr(module, "run", None)
    if run is None:
        hook_cls = getattr(module, "Hook", None)
        assert hook_cls is not None, "hook must define run() or a Hook class (S9.1)"
        run = getattr(hook_cls, "run", None)
    assert callable(run), "hook 'run' must be callable (S9.1)"


def test_vault_post_compose_hook_interface() -> None:
    hook_path = TEST_REPO / "infra" / "vault" / "post_compose_vault.py"
    assert hook_path.exists(), "Vault post_compose hook file missing"
    _assert_v2_run_interface(_load_module(hook_path))


def test_app_config_pre_compose_hook_interface() -> None:
    hook_path = TEST_REPO / "applications" / "app-config" / "pre_compose_app.py"
    assert hook_path.exists(), "app-config pre_compose hook file missing"
    _assert_v2_run_interface(_load_module(hook_path))
