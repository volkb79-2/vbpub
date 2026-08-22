"""Tests for the S16.1/CIU-22 post-up join call sites in `src/ciu/engine.py`
(`main_execution` and `run_shipped`).

`worktree.connect_shared_infra_after_up`'s own contract (reference
revalidation, target discovery, Docker-STATE concurrent-connect detection,
rollback) is fully covered by `test_ciu_worktree_shared_infra.py`. This file's
job is narrower: prove the ENGINE calls it at the right moment, with the
right arguments, and nowhere else --

- only after a SUCCESSFUL non-dry-run `docker compose up` (never on
  "interrupted", never on a dry run, never when no shared-infra intent is
  recorded in this worktree's own environment);
- with the EXACT `compose_project_name(...)` value passed to Compose;
- translating a `WorktreeError` into the engine's normal up-error surface
  (`ComposeError`) while retaining its `[S16.1]` message.

Docker itself is never started: `execute_docker_compose_with_logs` is
monkeypatched to a canned result (mirrors
test_ciu_engine_execution_boundaries.py), and
`worktree.connect_shared_infra_after_up` is monkeypatched to a spy so this
file never re-exercises its internals.
"""
from __future__ import annotations

import os
import sys
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
project_name = "shinfra"
environment_tag = "test"
log_level = "INFO"

[deploy.env.shared]
CONTAINER_UID = "$CONTAINER_UID"
CONTAINER_GID = "$CONTAINER_GID"
DOCKER_GID = "$DOCKER_GID"
"""

GLOBAL_DEFAULTS_NO_PROJECT = """\
[ciu]
require_fqdn = false
require_certs = false
auto_connect_network = false

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

INTENT_OVERLAY = """\
[ciu.instance.shared_infra]
ref_path = "/repo/.worktrees/primary-ref"
network = "net-ref"
services = ["api", "worker"]
ref_projects = ["idp-dev-idp"]
"""

EXPECTED_INTENT = worktree.SharedInfraIntent(
    ref_path=Path("/repo/.worktrees/primary-ref"),
    network="net-ref",
    services=("api", "worker"),
    ref_projects=("idp-dev-idp",),
)

# A PARTIAL intent -- parse_shared_infra_config
# must raise on this (S16.1 decision table: "Partial/malformed stored fields
# | [S16.1] error"), and the engine must translate that raise into a
# ComposeError, not let it escape untranslated.
MALFORMED_INTENT_OVERLAY = """\
[ciu.instance.shared_infra]
ref_path = "/repo/.worktrees/primary-ref"
services = ["api"]
ref_projects = ["idp-dev-idp"]
"""


def _base_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("REPO_NAME", "dstdns")
    monkeypatch.setenv("INSTANCE_ID", "abc123")
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "shinfra-net")
    monkeypatch.setenv("CONTAINER_UID", str(os.getuid()))
    monkeypatch.setenv("CONTAINER_GID", str(os.getgid()))
    monkeypatch.setenv("DOCKER_GID", str(os.getgid()))
    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "1")


def _write_ciu_env(tmp_path: Path) -> None:
    body = "\n".join(
        f'export {key}="{os.environ[key]}"'
        for key in (
            "REPO_ROOT", "PHYSICAL_REPO_ROOT", "REPO_NAME", "INSTANCE_ID",
            "DOCKER_NETWORK_INTERNAL",
            "CONTAINER_UID", "CONTAINER_GID", "DOCKER_GID",
        )
    ) + "\n"
    (tmp_path / "ciu.env").write_text(body)


def _write_worktree_overlay(
    tmp_path: Path, *, with_intent: bool, malformed_intent: bool
) -> None:
    if malformed_intent:
        body = MALFORMED_INTENT_OVERLAY
    elif with_intent:
        body = INTENT_OVERLAY
    else:
        return
    (tmp_path / "ciu.global.worktree.toml.j2").write_text(body)


def _write_native_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_intent: bool = False,
    malformed_intent: bool = False, global_defaults: str = GLOBAL_DEFAULTS,
) -> Path:
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(global_defaults)
    _write_ciu_env(tmp_path)
    _write_worktree_overlay(
        tmp_path, with_intent=with_intent, malformed_intent=malformed_intent
    )
    (tmp_path / ".gitignore").write_text("**/.ciu/\n")
    stack = tmp_path / "applications" / "demo"
    stack.mkdir(parents=True)
    (stack / "ciu.defaults.toml.j2").write_text(STACK_DEFAULTS)
    (stack / "ciu.compose.yml.j2").write_text(COMPOSE)
    return stack


def _write_shipped_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_intent: bool = False,
    malformed_intent: bool = False, global_defaults: str = GLOBAL_DEFAULTS,
) -> Path:
    _base_env(tmp_path, monkeypatch)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(global_defaults)
    _write_ciu_env(tmp_path)
    _write_worktree_overlay(
        tmp_path, with_intent=with_intent, malformed_intent=malformed_intent
    )
    stack = tmp_path / "vendor" / "legacy"
    stack.mkdir(parents=True)
    (stack / SHIPPED_COMPOSE).write_text(SHIPPED_COMPOSE_BODY)
    return stack


def _success_compose(*args, **kwargs) -> dict:
    return {"status": "success", "message": "", "stdout": "up\n"}


def _interrupted_compose(*args, **kwargs) -> dict:
    return {"status": "interrupted", "message": "User interrupted execution", "stdout": ""}


class SpyConnect:
    def __init__(self, raise_error: worktree.WorktreeError | None = None):
        self.calls: list[tuple] = []
        self._raise = raise_error

    def __call__(self, repo_root, compose_project, intent):
        self.calls.append((repo_root, compose_project, intent))
        if self._raise is not None:
            raise self._raise


# ---------------------------------------------------------------------------
# engine.main_execution
# ---------------------------------------------------------------------------


class TestMainExecutionSharedInfraWiring:
    def test_success_calls_connect_with_project_and_intent(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch, with_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result["status"] == "success"
        assert spy.calls == [(tmp_path.resolve(), "shinfra-test-demo", EXPECTED_INTENT)]

    def test_no_intent_never_calls_connect(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch, with_intent=False)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result["status"] == "success"
        assert spy.calls == []

    def test_dry_run_never_calls_connect_even_with_intent(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch, with_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True, dry_run=True,
        )

        assert result["status"] == "success"
        assert spy.calls == []

    def test_interrupted_compose_never_calls_connect(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch, with_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _interrupted_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.main_execution(
            working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
        )

        assert result["status"] == "interrupted"
        assert spy.calls == []

    def test_worktree_error_translates_to_compose_error(self, tmp_path, monkeypatch):
        stack = _write_native_repo(tmp_path, monkeypatch, with_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect(raise_error=worktree.WorktreeError("[S16.1] reference is gone"))
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        with pytest.raises(engine.ComposeError, match=r"\[S16\.1\] reference is gone"):
            engine.main_execution(
                working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
            )
        assert len(spy.calls) == 1

    def test_malformed_stored_intent_translates_to_compose_error_not_whole_run_crash(
        self, tmp_path, monkeypatch
    ):
        """REQUIRED (defect 3 fix): `parse_shared_infra_config(config)`
        itself can raise `WorktreeError` (a partial/malformed stored
        intent -- S16.1's own decision table names this state). That parse
        call must go through the SAME translation as a failed join: a bare
        `WorktreeError` would miss `deploy._run_stack`'s `except
        engine.ComposeError` catch and crash the entire `ciu deploy` run
        instead of failing just this one stack. `connect_shared_infra_after_up`
        must never even be reached."""
        stack = _write_native_repo(tmp_path, monkeypatch, malformed_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        with pytest.raises(engine.ComposeError, match=r"\[S16\.1\] malformed"):
            engine.main_execution(
                working_dir=stack, define_root=tmp_path, skip_hostdir_check=True,
            )
        assert spy.calls == []


# ---------------------------------------------------------------------------
# engine.run_shipped
# ---------------------------------------------------------------------------


class TestRunShippedSharedInfraWiring:
    def test_success_calls_connect_with_project_and_intent(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch, with_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "success"
        assert spy.calls == [(tmp_path.resolve(), "shinfra-test-legacy", EXPECTED_INTENT)]

    def test_no_intent_never_calls_connect(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch, with_intent=False)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "success"
        assert spy.calls == []

    def test_dry_run_never_calls_connect_even_with_intent(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch, with_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.run_shipped(stack, define_root=tmp_path, dry_run=True)

        assert result["status"] == "success"
        assert spy.calls == []

    def test_worktree_error_translates_to_compose_error(self, tmp_path, monkeypatch):
        stack = _write_shipped_repo(tmp_path, monkeypatch, with_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect(raise_error=worktree.WorktreeError("[S16.1] target service absent"))
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        with pytest.raises(engine.ComposeError, match=r"\[S16\.1\] target service absent"):
            engine.run_shipped(stack, define_root=tmp_path)
        assert len(spy.calls) == 1

    def test_malformed_stored_intent_translates_to_compose_error_not_whole_run_crash(
        self, tmp_path, monkeypatch
    ):
        """REQUIRED (defect 3 fix): same as the main_execution sibling test,
        for the shipped path's own parse_shared_infra_config call site."""
        stack = _write_shipped_repo(tmp_path, monkeypatch, malformed_intent=True)
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        with pytest.raises(engine.ComposeError, match=r"\[S16\.1\] malformed"):
            engine.run_shipped(stack, define_root=tmp_path)
        assert spy.calls == []

    def test_identity_named_project_with_intent_joins_under_that_name(
        self, tmp_path, monkeypatch
    ):
        """S8.5/CIU-46 cutover: no deploy.project_name/environment_tag means
        `compose_project_name` cannot derive the scoped project, so the
        shipped stack runs under the WORKSPACE-IDENTITY project derived from
        its ciu.env — and a declared shared-infra join scopes its filters to
        exactly that name. (The withdrawn [S16.1] refusal existed because the
        basename fallback's name was a value CIU itself never learned; the
        identity name is computed here, so there is nothing left to refuse.)"""
        stack = _write_shipped_repo(
            tmp_path, monkeypatch, with_intent=True, global_defaults=GLOBAL_DEFAULTS_NO_PROJECT,
        )
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "success"
        assert len(spy.calls) == 1
        repo_root, compose_project, intent = spy.calls[0]
        assert compose_project == "dstdns-abc123-legacy"
        assert repo_root == tmp_path.resolve()

    def test_unresolvable_compose_project_without_intent_is_unaffected(self, tmp_path, monkeypatch):
        """The pre-existing S8.7 legacy-fallback path (no shared-infra
        declared at all) must be completely unaffected by this feature."""
        stack = _write_shipped_repo(
            tmp_path, monkeypatch, with_intent=False, global_defaults=GLOBAL_DEFAULTS_NO_PROJECT,
        )
        monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
        monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _success_compose)
        spy = SpyConnect()
        monkeypatch.setattr(engine.worktree, "connect_shared_infra_after_up", spy)

        result = engine.run_shipped(stack, define_root=tmp_path)

        assert result["status"] == "success"
        assert spy.calls == []
