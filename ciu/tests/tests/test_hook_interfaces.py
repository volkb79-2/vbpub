"""
Contract tests for the demo hook interfaces (S9.1).

Validates that the test-repo hook modules expose the v2 ``run(config, ctx)``
callable without executing Docker/Vault operations. The v1 per-point class names
(PostComposeHook, ...) are withdrawn (S9.1); v2 hooks are a module-level ``run``
function (or a ``Hook`` class with a ``run`` method).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"

sys.path.insert(0, str(REPO_ROOT / "src"))
from ciu.deploy_pkg import health  # noqa: E402
from ciu.hooks_runner import HookContext  # noqa: E402


class _FakeClock:
    """Deterministic monotonic clock: yields the given values in order, then
    repeats the last. Makes the readiness poll loops time-travel without sleeping.
    """

    def __init__(self, steps: list[float]) -> None:
        self._steps = list(steps)
        self._i = 0

    def __call__(self) -> float:
        v = self._steps[min(self._i, len(self._steps) - 1)]
        self._i += 1
        return v


def _no_sleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# S9.3 / CIU-4 — readiness probes (ctx.wait_tcp / ctx.wait_healthy)
# ---------------------------------------------------------------------------


class TestReadinessProbes:
    def test_wait_tcp_returns_true_on_open_port(self) -> None:
        # connect_fn succeeds immediately → ready, no real socket used.
        ok = health.wait_tcp(
            "host", 1234,
            connect_fn=lambda h, p: True,
            sleep_fn=_no_sleep,
            clock=_FakeClock([0.0]),
        )
        assert ok is True

    def test_wait_tcp_times_out_on_closed_port(self) -> None:
        def refuse(_h, _p):
            raise OSError("connection refused")

        ok = health.wait_tcp(
            "host", 1234,
            timeout_s=30.0,
            connect_fn=refuse,
            sleep_fn=_no_sleep,
            clock=_FakeClock([0.0, 100.0]),  # second read is past the deadline
        )
        assert ok is False

    def test_wait_healthy_returns_true_when_healthy(self) -> None:
        ok = health.wait_healthy(
            lambda: "healthy", sleep_fn=_no_sleep, clock=_FakeClock([0.0])
        )
        assert ok is True

    def test_wait_healthy_treats_no_healthcheck_as_ready(self) -> None:
        # Nothing to poll → must not block.
        calls = {"n": 0}

        def status():
            calls["n"] += 1
            return "no-healthcheck"

        assert health.wait_healthy(status, sleep_fn=_no_sleep, clock=_FakeClock([0.0])) is True
        assert calls["n"] == 1

    def test_wait_healthy_times_out_while_starting(self) -> None:
        ok = health.wait_healthy(
            lambda: "starting",
            timeout_s=30.0,
            sleep_fn=_no_sleep,
            clock=_FakeClock([0.0, 100.0]),
        )
        assert ok is False

    def test_hookcontext_exposes_readiness_fields(self) -> None:
        # Fields exist and default to None (engine wires them at runtime, S9.3).
        ctx = HookContext(
            point="post_compose",
            stack_dir=Path("/tmp"),
            repo_root=Path("/tmp"),
            secret_file=lambda name: Path("/tmp") / name,
        )
        assert ctx.wait_healthy is None
        assert ctx.wait_tcp is None
        # A hook author calls them through ctx once wired:
        ctx.wait_tcp = lambda host, port, **kw: True
        assert ctx.wait_tcp("redis-core", 6379) is True


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
