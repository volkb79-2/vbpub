"""Tests for the S16.3/CIU-24 worktree-instance concurrency-budget call
sites in `src/ciu/engine.py` (`main_execution` and `run_shipped`).

`worktree.worktree_budget_slot`'s own contract (candidate translation,
registered-and-deployed classifier, the locked critical section, the
already-deployed-may-rerun rule, real two-thread contention) is fully
covered by `test_ciu_worktree_budget.py`. This file's job is narrower: prove
the ENGINE calls it at the right moment, with the right arguments, and
nowhere else --

- the cap is resolved once, early, via `worktree.resolve_worktree_cap`;
- the budget slot wraps ONLY the real `execute_docker_compose_with_logs`
  call -- never a dry run, never `--render-toml`;
- it is called with the CURRENT `DOCKER_NETWORK_INTERNAL` and
  `working_dir.relative_to(repo_root)`, for BOTH the native and `--shipped`
  paths;
- a refusal (`WorktreeError`) translates to the engine's normal up-error
  surface (`ComposeError`) and the executor never runs;
- the lock is released (the `with` block exits) BEFORE P02's post-up
  shared-infra join runs -- the two are sequential, never nested one inside
  the other.

Per the handoff: only ENGINE WIRING tests may replace `worktree_budget_slot`
with a fake context manager (the direct helper tests in
`test_ciu_worktree_budget.py` exercise the REAL one). Docker itself is never
started: `execute_docker_compose_with_logs` is monkeypatched to a canned
result (mirrors `test_ciu_engine_shared_infra.py`).
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine, worktree  # noqa: E402
from ciu.config_constants import SHIPPED_COMPOSE  # noqa: E402


GLOBAL_DEFAULTS = """\
[ciu]
require_fqdn = false
require_certs = false
auto_connect_network = false

[deploy]
project_name = "budget"
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
"""

COMPOSE = """\
services:
  {{ demo.name }}:
    image: {{ demo.image }}
"""

SHIPPED_COMPOSE_BODY = """\
services:
  legacy:
    image: alpine:3
"""


def _base_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "budget-net")
    monkeypatch.setenv("CONTAINER_UID", str(os.getuid()))
    monkeypatch.setenv("CONTAINER_GID", str(os.getgid()))
    monkeypatch.setenv("DOCKER_GID", str(os.getgid()))
    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "1")


def _write_ciu_env(tmp_path: Path) -> None:
    body = "\n".join(
        f'export {key}="{os.environ[key]}"'
        for key in (
            "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID",
        )
    ) + "\n"
    (tmp_path / "ciu.env").write_text(body)


def _write_native_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No git repo here at all (matches this suite's own established
    pattern, e.g. `test_spec_contracts.py:build_repo`) -- the REAL
    `worktree.resolve_worktree_cap` therefore resolves to `None` (no
    worktree family) for any test that does not monkeypatch it, giving a
    free, honest "no cap configured" baseline."""
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(GLOBAL_DEFAULTS)
    _write_ciu_env(tmp_path)
    (tmp_path / ".gitignore").write_text("**/.ciu/\n")
    stack = tmp_path / "applications" / "demo"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text(STACK_DEFAULTS)
    (stack / "ciu.compose.yml.j2").write_text(COMPOSE)
    return stack


def _write_shipped_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(GLOBAL_DEFAULTS)
    _write_ciu_env(tmp_path)
    stack = tmp_path / "vendor" / "legacy"
    stack.mkdir(parents=True)
    (stack / SHIPPED_COMPOSE).write_text(SHIPPED_COMPOSE_BODY)
    return stack


def _success_compose(*args, **kwargs) -> dict:
    return {"status": "success", "message": "", "stdout": "up\n"}


def _interrupted_compose(*args, **kwargs) -> dict:
    return {"status": "interrupted", "message": "User interrupted execution", "stdout": ""}


class SpyBudgetSlot:
    """Replaces `worktree.worktree_budget_slot` with a fake context manager
    that records every call's arguments and (optionally) an ordering marker
    -- the seam the handoff explicitly permits engine wiring tests to use."""

    def __init__(self, *, raise_error: worktree.WorktreeError | None = None, order: list | None = None):
        self.calls: list[tuple] = []
        self._raise = raise_error
        self._order = order

    @contextmanager
    def __call__(self, repo_root, cap, current_network, stack_rel):
        self.calls.append((repo_root, cap, current_network, stack_rel))
        if self._raise is not None:
            raise self._raise
        if self._order is not None:
            self._order.append("budget_enter")
        yield
        if self._order is not None:
            self._order.append("budget_exit")


class SpyConnect:
    """Replaces `worktree.connect_shared_infra_after_up` -- never exercised
    by THIS file's fixtures (no shared-infra intent is ever recorded), but
    used to prove P02's join is never reached before/without a budget-slot
    success, and (with `order`) to prove ordering relative to it."""

    def __init__(self, *, order: list | None = None):
        self.calls: list[tuple] = []
        self._order = order

    def __call__(self, repo_root, compose_project, intent):
        self.calls.append((repo_root, compose_project, intent))
        if self._order is not None:
            self._order.append("p02_join")


# ---------------------------------------------------------------------------
# engine.main_execution
# ---------------------------------------------------------------------------


class TestMainExecutionBudgetWiring:
    def test_no_cap_configured_slot_called_with_none(self, tmp_path, monkeypatch):
        """The free "no worktree family" baseline: a bare, non-git repo
        resolves a real `None` cap, and the slot is still called (with
        cap=None) around the real executor -- proving the wiring itself is
        unconditional, only the enforcement inside the slot is cap-gated."""
        stack = _write_native_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result["status"] == "success"
        assert len(spy.calls) == 1
        repo_root, cap, network, stack_rel = spy.calls[0]
        assert repo_root == tmp_path.resolve()
        assert cap is None
        assert network == "budget-net"
        assert stack_rel == Path("applications/demo")

    def test_configured_cap_passed_through_verbatim(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        monkeypatch.setattr(engine.worktree, "resolve_worktree_cap", lambda repo_root: 5)
        spy = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result["status"] == "success"
        assert spy.calls[0][1] == 5

    def test_refusal_translates_to_compose_error_before_executor(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        executor_called = []
        monkeypatch.setattr(
            engine, "execute_docker_compose_with_logs",
            lambda *a, **k: executor_called.append(True) or _success_compose(),
        )
        spy = SpyBudgetSlot(raise_error=worktree.WorktreeError("[S16.3] worktree capacity refused"))
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        with pytest.raises(engine.ComposeError, match=r"\[S16\.3\] worktree capacity refused"):
            engine.main_execution(
                working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
            )
        assert executor_called == []

    def test_dry_run_never_invokes_budget_slot(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        spy = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True, dry_run=True,
        )

        assert result["status"] == "success"
        assert spy.calls == []

    def test_render_toml_never_invokes_budget_slot(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch)
        spy = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True, render_toml=True,
        )

        assert result["status"] == "success"
        assert spy.calls == []

    def test_interrupted_compose_never_calls_p02_join(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _interrupted_compose)
        spy_slot = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy_slot)
        spy_connect = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy_connect)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result["status"] == "interrupted"
        assert len(spy_slot.calls) == 1
        assert spy_connect.calls == []

    def test_budget_context_exits_before_p02_join_runs(self, tmp_path, monkeypatch):
        """The ordering requirement: the budget slot's context MUST exit
        (releasing the flock) before P02's post-up join is even attempted --
        proven by a shared ordering list, not by inspecting internals."""
        stack = _write_native_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        order: list[str] = []
        spy_slot = SpyBudgetSlot(order=order)
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy_slot)
        # Simulate a declared shared-infra intent so the P02 call site runs.
        monkeypatch.setattr(
            engine.worktree, "parse_shared_infra_intent",
            lambda values: object(),  # any non-None sentinel
        )
        spy_connect = SpyConnect(order=order)
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy_connect)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result["status"] == "success"
        assert order == ["budget_enter", "budget_exit", "p02_join"]


# ---------------------------------------------------------------------------
# engine.run_shipped
# ---------------------------------------------------------------------------


class TestRunShippedBudgetWiring:
    def test_no_cap_configured_slot_called_with_none(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "success"
        assert len(spy.calls) == 1
        repo_root, cap, network, stack_rel = spy.calls[0]
        assert repo_root == tmp_path.resolve()
        assert cap is None
        assert network == "budget-net"
        assert stack_rel == Path("vendor/legacy")

    def test_configured_cap_passed_through_verbatim(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        monkeypatch.setattr(engine.worktree, "resolve_worktree_cap", lambda repo_root: 2)
        spy = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "success"
        assert spy.calls[0][1] == 2

    def test_refusal_translates_to_compose_error_before_executor(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        executor_called = []
        monkeypatch.setattr(
            engine, "execute_docker_compose_with_logs",
            lambda *a, **k: executor_called.append(True) or _success_compose(),
        )
        spy = SpyBudgetSlot(raise_error=worktree.WorktreeError("[S16.3] worktree capacity refused"))
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        with pytest.raises(engine.ComposeError, match=r"\[S16\.3\] worktree capacity refused"):
            engine.run_shipped(stack, define_root=tmp_path)
        assert executor_called == []

    def test_dry_run_never_invokes_budget_slot(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        spy = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy)

        result = engine.run_shipped(stack, define_root=tmp_path, dry_run=True)

        assert result["status"] == "success"
        assert spy.calls == []

    def test_interrupted_compose_never_calls_p02_join(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _interrupted_compose)
        spy_slot = SpyBudgetSlot()
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy_slot)
        spy_connect = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy_connect)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "interrupted"
        assert len(spy_slot.calls) == 1
        assert spy_connect.calls == []

    def test_budget_context_exits_before_p02_join_runs(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        order: list[str] = []
        spy_slot = SpyBudgetSlot(order=order)
        monkeypatch.setattr(engine.worktree, "worktree_budget_slot", spy_slot)
        monkeypatch.setattr(
            engine.worktree, "parse_shared_infra_intent", lambda values: object(),
        )
        spy_connect = SpyConnect(order=order)
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy_connect)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "success"
        assert order == ["budget_enter", "budget_exit", "p02_join"]


# ---------------------------------------------------------------------------
# Real end-to-end wiring (no worktree_budget_slot mock) -- guards against a
# wiring bug (wrong arg types/order) that a fully-mocked spy could not catch.
# ---------------------------------------------------------------------------


def _forbid_s16_3_docker_ps(real_docker):
    """Wraps the real `procutil.docker` (delegating every OTHER call, e.g.
    S17's unrelated `docker image inspect` provenance stamping) but raises
    if S16.3's OWN `docker ps --filter label=com.docker.compose.project=...`
    query pattern is ever issued -- proving the REAL `worktree_budget_slot`
    made no Docker call at all when cap is None."""
    def wrapped(args, **kw):
        if (
            args and args[0] == "ps"
            and any(str(a).startswith("label=com.docker.compose.project=") for a in args)
        ):
            raise AssertionError(f"must not query Docker for worktree capacity when cap is None: {args}")
        return real_docker(args, **kw)
    return wrapped


class TestRealBudgetSlotEndToEnd:
    def test_native_path_real_slot_no_cap_starts_normally(self, tmp_path, monkeypatch):
        """No git repo anywhere here -- `resolve_worktree_cap` genuinely
        returns None, and the REAL `worktree_budget_slot` genuinely no-ops
        (no S16.3 Docker/lock call at all), proving the wiring end-to-end
        without mocking either S16.3 function."""
        stack = _write_native_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        monkeypatch.setattr(
            engine.worktree.procutil, "docker",
            _forbid_s16_3_docker_ps(engine.worktree.procutil.docker),
        )

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )
        assert result["status"] == "success"

    def test_shipped_path_real_slot_no_cap_starts_normally(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        monkeypatch.setattr(
            engine.worktree.procutil, "docker",
            _forbid_s16_3_docker_ps(engine.worktree.procutil.docker),
        )

        result = engine.run_shipped(stack, define_root=tmp_path)
        assert result["status"] == "success"
