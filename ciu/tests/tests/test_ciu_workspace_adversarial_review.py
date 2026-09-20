"""Adversarial witnesses for CIU's shared workspace adapter boundaries."""
from __future__ import annotations

import builtins
import subprocess
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from ciu import composefile, workspace, workspace_env, worktree
from ciu.config_constants import GLOBAL_CONFIG_DEFAULTS


IDENTITY = {
    "repo_name": "repo",
    "instance_id": "abc123",
    "network": "repo-abc123-network",
    "physical_repo_root": "/host/repo",
    "repo_root": "/workspaces/repo",
    "public_fqdn": "repo.example",
}
MACHINE = {key: "value" for key in workspace_env.MACHINE_FACTS_KEYS}


def _fake_import_fallback(monkeypatch, module_name: str, loader):
    real_import = builtins.__import__
    first = True

    def import_once_missing(name, *args, **kwargs):
        nonlocal first
        if name == module_name and first:
            first = False
            raise ModuleNotFoundError(name)
        if name == module_name:
            return loader
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_once_missing)


def test_workspace_adapter_loader_uses_source_fallback(monkeypatch):
    fake = SimpleNamespace()
    monkeypatch.setitem(__import__("sys").modules, "worktree", fake)
    _fake_import_fallback(monkeypatch, "worktree", fake)
    assert workspace._shared() is fake


def test_workspace_adapter_loader_reports_missing_library(monkeypatch):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "worktree":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    monkeypatch.setattr(workspace.Path, "is_dir", lambda _path: False)
    with pytest.raises(ModuleNotFoundError, match="worktree"):
        workspace._shared()


def test_ciu_worktree_loader_uses_and_reports_source_fallback(monkeypatch):
    fake = SimpleNamespace()
    monkeypatch.setitem(__import__("sys").modules, "worktree", fake)
    _fake_import_fallback(monkeypatch, "worktree", fake)
    assert worktree._shared_worktree() is fake

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "worktree":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    monkeypatch.setattr(worktree.Path, "is_dir", lambda _path: False)
    with pytest.raises(ModuleNotFoundError, match="worktree"):
        worktree._shared_worktree()


def test_root_context_namespace_and_explicit_root_refusal(tmp_path):
    context = workspace.RootContext(
        workspace=SimpleNamespace(workspace_id="w1"),
        ciu_root=tmp_path, ciu_root_offset=Path("."), root_instance_id="r1",
        physical_ciu_root=tmp_path,
    )
    assert context.root_namespace == "w1-r1"
    with pytest.raises(workspace.CiuWorkspaceError, match="missing"):
        workspace.resolve_ciu_root(tmp_path, root_folder=tmp_path / "missing")


def test_root_context_is_frozen_and_root_lock_is_reentrant_for_setup(
    tmp_path,
):
    context = workspace.RootContext(
        workspace=SimpleNamespace(
            workspace_id="w1", git_common_dir=tmp_path / ".git-common"
        ),
        ciu_root=tmp_path,
        ciu_root_offset=Path("."),
        root_instance_id="r1",
        physical_ciu_root=tmp_path,
    )
    with pytest.raises(FrozenInstanceError):
        context.root_instance_id = "changed"

    # The second admission must work after the first created the lock
    # directory.  A mutated ``exist_ok=True`` would fail here.
    with workspace.root_lock(context):
        pass
    with workspace.root_lock(context):
        pass


def test_context_for_root_refuses_root_outside_discovered_git_family(monkeypatch, tmp_path):
    fake = SimpleNamespace(
        discover_git_context=lambda _root: (tmp_path / "git", tmp_path / ".git", "main", "a" * 40)
    )
    monkeypatch.setattr(workspace, "_shared", lambda: fake)
    with pytest.raises(workspace.CiuWorkspaceError, match="escapes Git root"):
        workspace.context_for_root(tmp_path / "nested")


def test_committed_root_and_marker_validation_fail_closed(monkeypatch, tmp_path):
    fail = subprocess.CompletedProcess([], 1, stdout="", stderr="bad ls-tree")
    monkeypatch.setattr(workspace.subprocess, "run", lambda *args, **kwargs: fail)
    with pytest.raises(workspace.CiuWorkspaceError, match="cannot inspect"):
        workspace.discover_committed_roots(tmp_path)

    for result, message in (
        (subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"missing"), "cannot read"),
        (subprocess.CompletedProcess([], 0, stdout=b"[x]\x00", stderr=b""), "NUL byte"),
        (subprocess.CompletedProcess([], 0, stdout=b"\xff", stderr=b""), "not UTF-8"),
        (subprocess.CompletedProcess([], 0, stdout=b"# comment\n", stderr=b""), "no TOML table"),
    ):
        monkeypatch.setattr(workspace.subprocess, "run", lambda *args, result=result, **kwargs: result)
        with pytest.raises(workspace.CiuWorkspaceError, match=message):
            workspace._validate_committed_marker(tmp_path, "HEAD", "ciu.global.defaults.toml.j2")


def test_committed_root_errors_preserve_stderr_and_subprocess_contract(monkeypatch, tmp_path):
    result = subprocess.CompletedProcess(
        [], 1, stdout="stdout-detail", stderr="stderr-detail"
    )
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(workspace.subprocess, "run", run)
    with pytest.raises(workspace.CiuWorkspaceError, match="stderr-detail"):
        workspace.discover_committed_roots(tmp_path)
    assert calls[0][1] == {
        "cwd": tmp_path.resolve(),
        "text": True,
        "capture_output": True,
        "check": False,
    }

    calls.clear()
    valid = subprocess.CompletedProcess([], 0, stdout=b"[ciu]\n", stderr=b"")
    monkeypatch.setattr(workspace.subprocess, "run", lambda *args, **kwargs: valid)
    workspace._validate_committed_marker(
        tmp_path, "HEAD", "ciu.global.defaults.toml.j2"
    )
    # The marker is bytes until the explicit UTF-8 decode, and Git failures are
    # handled as data so the adapter can report its typed refusal.
    assert calls == []

    marker_calls = []

    def marker_run(*args, **kwargs):
        marker_calls.append((args, kwargs))
        return valid

    monkeypatch.setattr(workspace.subprocess, "run", marker_run)
    workspace._validate_committed_marker(
        tmp_path, "HEAD", "ciu.global.defaults.toml.j2"
    )
    assert marker_calls[0][1] == {
        "cwd": tmp_path.resolve(),
        "text": False,
        "capture_output": True,
        "check": False,
    }


def test_tracked_blob_checks_both_git_type_outcomes(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workspace.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout="blob", stderr=""),
    )
    assert workspace._tracked_blob(tmp_path, "HEAD", "file") is True
    monkeypatch.setattr(
        workspace.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, stdout="blob", stderr=""),
    )
    assert workspace._tracked_blob(tmp_path, "HEAD", "file") is False


def test_tracked_blob_preserves_binary_git_probe_contract(monkeypatch, tmp_path):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess([], 0, stdout="blob", stderr="")

    monkeypatch.setattr(workspace.subprocess, "run", run)
    assert workspace._tracked_blob(tmp_path, "HEAD", "file") is True
    assert calls[0][1] == {
        "cwd": tmp_path.resolve(),
        "text": True,
        "capture_output": True,
        "check": False,
    }


def test_root_names_and_identity_collision_are_distinct():
    same = SimpleNamespace(workspace_id="same", root_instance_id="same")
    different = SimpleNamespace(workspace_id="w", root_instance_id="r")
    assert workspace.root_runtime_names(same, stack="App_stack")["network"] == "ciu-same-network"
    assert workspace.root_runtime_names(different, stack="App_stack")["project"] == "ciu-w-r-app-stack"
    first = SimpleNamespace(root_instance_id="short", physical_ciu_root=Path("/one"))
    second = SimpleNamespace(root_instance_id="short", physical_ciu_root=Path("/two"))
    with pytest.raises(workspace.CiuWorkspaceError, match="identity collision"):
        workspace.assert_root_identity_distinct([first, second])


def test_generated_facts_render_rejects_unknown_and_invalid_machine_values():
    with pytest.raises(workspace_env.WorkspaceEnvError, match="unknown keys"):
        workspace_env.render_generated_facts_block({**IDENTITY, "unexpected": "x"})
    with pytest.raises(workspace_env.WorkspaceEnvError, match="must be strings"):
        workspace_env.render_generated_facts_block({**IDENTITY, "repo_name": 3})
    with pytest.raises(workspace_env.WorkspaceEnvError, match="machine facts incomplete"):
        workspace_env.render_generated_facts_block(IDENTITY, {"env_type": "native"})
    with pytest.raises(workspace_env.WorkspaceEnvError, match="machine facts contain unknown"):
        workspace_env.render_generated_facts_block(IDENTITY, {**MACHINE, "other": "x"})
    with pytest.raises(workspace_env.WorkspaceEnvError, match="machine facts must be strings"):
        workspace_env.render_generated_facts_block(IDENTITY, {**MACHINE, "env_type": 3})


def test_machine_facts_rejects_missing_and_unknown_keys_together(monkeypatch, tmp_path):
    table = {"schema_version": 2, **MACHINE}
    table.pop(next(iter(MACHINE)))
    table["unexpected"] = "x"
    monkeypatch.setattr(
        workspace_env,
        "generated_facts_document",
        lambda _root: {"ciu": {"instance": {"machine": table}}},
    )
    with pytest.raises(workspace_env.WorkspaceEnvError, match="invalid"):
        workspace_env.read_generated_machine_facts(tmp_path)


def test_identity_fallback_rejects_default_repair_and_handles_partial_identity(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        workspace,
        "_shared",
        lambda: SimpleNamespace(workspace_id_for_path=lambda _path: "derived"),
    )
    one_missing = workspace_env._compute_network_name(
        tmp_path, workspace_id="explicit", root_instance_id=None
    )
    other_missing = workspace_env._compute_network_name(
        tmp_path, workspace_id=None, root_instance_id="explicit"
    )
    assert one_missing["INSTANCE_ID"] == "derived"
    assert other_missing["INSTANCE_ID"] == "derived"

    monkeypatch.setattr(workspace_env, "seed_identity_env", lambda _root: {})
    monkeypatch.setattr(
        workspace_env,
        "generate_ciu_env",
        lambda *_args, **_kwargs: pytest.fail("default repair must be refused"),
    )
    with pytest.raises(workspace_env.WorkspaceEnvError, match="no complete"):
        workspace_env._seed_identity_or_repair(tmp_path, generated=False)


def test_compose_ksm_sources_require_a_logical_repo_root(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    with pytest.raises(ValueError, match="repo_root is required for the builtin"):
        composefile.generate_overlay(
            stack, {}, [], compose_yaml_text="services:\n  app: {image: example}\n",
            governance={"enabled": True, "ksm_optin": "builtin"},
            repo_root=None, physical_root=tmp_path,
        )
    with pytest.raises(ValueError, match="repo_root is required for a relative"):
        composefile.generate_overlay(
            stack, {}, [], compose_yaml_text="services:\n  app: {image: example}\n",
            governance={"enabled": True, "ksm_optin": "hooks/ksm.so"},
            repo_root=None, physical_root=tmp_path,
        )


@pytest.mark.parametrize(
    ("parsed", "message"),
    [
        ({"ciu": "broken"}, "malformed"),
        ({"ciu": {"instance": {"machine": "broken"}}}, "is not a table"),
        ({"ciu": {"instance": {"machine": {"schema_version": 1}}}}, "schema_version"),
        ({"ciu": {"instance": {"machine": {"schema_version": 2, "other": "x"}}}}, "invalid"),
        ({"ciu": {"instance": {"machine": {"schema_version": 2, **{**MACHINE, "env_type": 3}}}}}, "not a string"),
    ],
)
def test_machine_facts_reader_rejects_malformed_documents(monkeypatch, tmp_path, parsed, message):
    monkeypatch.setattr(workspace_env, "generated_facts_document", lambda _root: parsed)
    with pytest.raises(workspace_env.WorkspaceEnvError, match=message):
        workspace_env.read_generated_machine_facts(tmp_path)


def test_machine_facts_reader_returns_empty_when_table_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_env, "generated_facts_document", lambda _root: {"ciu": {"instance": {}}})
    assert workspace_env.read_generated_machine_facts(tmp_path) == {}


def test_generate_env_does_not_swallow_git_failure_in_a_git_checkout(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(
        workspace,
        "_shared",
        lambda: SimpleNamespace(
            discover_git_context=lambda _root: (_ for _ in ()).throw(RuntimeError("git unavailable"))
        ),
    )
    with pytest.raises(RuntimeError, match="git unavailable"):
        workspace_env.generate_ciu_env(tmp_path)


def test_atomic_facts_writer_supports_unmasked_mode(tmp_path):
    path = tmp_path / "facts"
    workspace_env._atomic_write_text(path, "value\n")
    assert path.read_text(encoding="utf-8") == "value\n"


def test_bootstrap_refuses_missing_machine_facts(monkeypatch, tmp_path):
    env_file = tmp_path / workspace_env.ENV_FILE_NAME
    env_file.write_text("export REPO_ROOT=/workspaces/repo\n")
    monkeypatch.setattr(workspace_env, "resolve_env_root", lambda *args: tmp_path)
    monkeypatch.setattr(workspace_env, "_seed_identity_or_repair", lambda *args, **kwargs: None)
    monkeypatch.setattr(workspace_env, "read_generated_machine_facts", lambda _root: {})
    with pytest.raises(workspace_env.WorkspaceEnvError, match="no complete"):
        workspace_env.bootstrap_workspace_env(
            tmp_path, None, GLOBAL_CONFIG_DEFAULTS, False, False, []
        )


def _record(path: Path, *, offset: Path = Path("."), state: str = "allocating"):
    return worktree.WorktreeInstanceRecord(
        logical_name="demo", display_name="demo", branch="demo", git_worktree_path=path,
        ciu_root_offset=offset, created_at_utc="2026-01-01T00:00:00Z", base_ref="main",
        state=state,
    )


def test_shared_record_lookup_and_lease_sync_failures(monkeypatch, tmp_path):
    class SharedError(Exception):
        def __init__(self, message, category="other"):
            super().__init__(message)
            self.category = category

    shared = SimpleNamespace(
        WorkspaceError=SharedError,
        discover_git_context=lambda _path: (_ for _ in ()).throw(SharedError("not git", "git-error")),
    )
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
    assert worktree._shared_record_for_checkout(tmp_path) is None
    shared.discover_git_context = lambda _path: (_ for _ in ()).throw(SharedError("bad", "other"))
    with pytest.raises(SharedError, match="bad"):
        worktree._shared_record_for_checkout(tmp_path)
    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _path: (_ for _ in ()).throw(RuntimeError("lease write")))
    with pytest.raises(worktree.WorktreeError, match="could not update shared"):
        worktree._sync_shared_lease(tmp_path, None)


def test_finish_allocation_accepts_a_generic_git_worktree_without_ciu_marker(monkeypatch, tmp_path):
    record = _record(tmp_path)
    written = []
    monkeypatch.setattr(worktree, "_write_instance_record", lambda value: written.append(value))
    ready = worktree._finish_allocation(tmp_path, record, checkout_required=False)
    assert ready.state == "ready" and written[-1].state == "ready"


def _create_setup(monkeypatch, tmp_path, *, roots, materialize=True, failure=None):
    primary = tmp_path / "primary"
    primary.mkdir()
    target = primary / ".worktrees" / "thing"
    generic = SimpleNamespace(
        workspace_id="generic", physical_worktree_path=target,
        record_path=tmp_path / "generic.json",
    )
    from worktree.core import WorkspaceRecord
    shared_record = WorkspaceRecord(
        workspace_id="generic", source_git_root=primary, worktree_path=target,
        physical_worktree_path=target, git_common_dir=tmp_path / ".git",
        branch="thing", base_commit="a" * 40, purpose="ciu", state="ready",
        created_at_utc="2026-01-01T00:00:00Z",
    )

    def allocate(_source, allocated, **_kwargs):
        if materialize:
            for root in roots:
                relative = root.relative_to(primary)
                destination = allocated / relative
                destination.mkdir(parents=True, exist_ok=True)
                (destination / GLOBAL_CONFIG_DEFAULTS).write_text("[ciu]\n", encoding="utf-8")
        return generic

    shared = SimpleNamespace(
        physical_path=lambda path, **kwargs: Path(path),
        create_workspace=allocate,
        canonical_path=lambda path: Path(path),
        read_record=lambda _path: shared_record,
        write_record=lambda record: None,
    )
    monkeypatch.setattr(worktree, "_allocation_lock", lambda _root: nullcontext())
    monkeypatch.setattr(worktree, "_ciu_root_offset", lambda _root: Path("."))
    monkeypatch.setattr(worktree, "primary_worktree_root", lambda _root: primary)
    monkeypatch.setattr(worktree, "find_instance_record", lambda *_args: None)
    monkeypatch.setattr(worktree, "_ensure_record_is_excluded", lambda *_args: None)
    monkeypatch.setattr(worktree, "_branch_exists", lambda *_args: False)
    monkeypatch.setattr(worktree, "list_worktrees", lambda *_args: [])
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(worktree, "_write_instance_record", lambda _record: None)
    monkeypatch.setattr(worktree, "_write_worktree_overlay", lambda *_args: None)
    monkeypatch.setattr(worktree, "_finish_allocation", lambda _root, record, **_kwargs: record)
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda _root: primary)
    monkeypatch.setattr(workspace, "discover_committed_roots", lambda *_args, **_kwargs: tuple(roots))
    monkeypatch.setattr(workspace, "context_for_root", lambda root, **kwargs: SimpleNamespace(
        root_instance_id=f"root-{root.name}", workspace_id="generic", physical_ciu_root=root
    ))
    monkeypatch.setattr(workspace, "assert_root_identity_distinct", lambda _contexts: None)
    monkeypatch.setattr(workspace, "root_lock", lambda _context: nullcontext())
    monkeypatch.setattr(workspace_env, "generate_ciu_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workspace_env, "read_generated_facts", lambda _root: {"network": "net"})
    if failure is not None:
        monkeypatch.setattr(worktree, "_mark_recovery", lambda record, status: record)
    return primary, target, shared


def test_create_refuses_when_shared_allocation_fails(monkeypatch, tmp_path):
    shared = SimpleNamespace(create_workspace=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("allocator")))
    monkeypatch.setattr(worktree, "_allocation_lock", lambda _root: nullcontext())
    monkeypatch.setattr(worktree, "_ciu_root_offset", lambda _root: Path("."))
    monkeypatch.setattr(worktree, "primary_worktree_root", lambda root: root)
    monkeypatch.setattr(worktree, "find_instance_record", lambda *_args: None)
    monkeypatch.setattr(worktree, "_ensure_record_is_excluded", lambda *_args: None)
    monkeypatch.setattr(worktree, "_branch_exists", lambda *_args: False)
    monkeypatch.setattr(worktree, "list_worktrees", lambda *_args: [])
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
    with pytest.raises(worktree.WorktreeError, match="shared workspace allocation failed"):
        worktree.create(tmp_path, "demo", path=tmp_path / ".worktrees" / "demo")


def test_legacy_record_migration_wraps_shared_adoption_failure(monkeypatch, tmp_path):
    class SharedError(Exception):
        category = "workspace-error"

    shared = SimpleNamespace(
        WorkspaceError=SharedError,
        adopt_workspace=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SharedError("adoption refused")
        ),
    )
    record = SimpleNamespace(
        git_worktree_path=tmp_path / "checkout",
        logical_name="legacy",
        ciu_root_offset=Path("."),
    )
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _path: None)
    monkeypatch.setattr(worktree, "_physical_workspace_target", lambda *_args: tmp_path / "physical")
    monkeypatch.setattr(worktree, "primary_worktree_root", lambda _root: tmp_path)
    with pytest.raises(worktree.WorktreeError, match="adopt CIU checkout into shared workspace lifecycle"):
        worktree._ensure_shared_record(tmp_path, record)


def test_legacy_record_migration_preserves_shared_record_metadata(monkeypatch, tmp_path):
    captured = {}
    shared = SimpleNamespace(
        WorkspaceError=RuntimeError,
        adopt_workspace=lambda *args, **kwargs: captured.update(kwargs) or "shared",
    )
    record = SimpleNamespace(
        git_worktree_path=tmp_path / "checkout",
        logical_name="legacy",
        ciu_root_offset=Path("nested"),
    )
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
    monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _path: None)
    monkeypatch.setattr(
        worktree, "_physical_workspace_target", lambda *_args: tmp_path / "physical"
    )
    monkeypatch.setattr(worktree, "primary_worktree_root", lambda _root: tmp_path)

    assert worktree._ensure_shared_record(tmp_path, record) == "shared"
    assert captured["metadata"] == {"ciu_root_offset": "nested", "legacy_record": True}


def test_create_refuses_a_committed_root_marker_that_did_not_materialize(monkeypatch, tmp_path):
    primary, _target, _shared = _create_setup(monkeypatch, tmp_path, roots=[tmp_path / "primary"], materialize=False)
    with pytest.raises(worktree.WorktreeError, match="did not materialize"):
        worktree.create(tmp_path, "demo", path=primary / ".worktrees" / "thing")


def test_create_prepares_nested_roots_and_generates_non_ready_root(monkeypatch, tmp_path):
    primary, target, _shared = _create_setup(
        monkeypatch, tmp_path, roots=[tmp_path / "primary", tmp_path / "primary" / "nested"]
    )
    writes = []
    _shared.write_record = lambda record: writes.append(record)
    ready = worktree.create(tmp_path, "demo", path=target)
    assert ready.state == "allocating"
    entries = writes[-1].metadata["root_entries"]
    assert [entry["offset"] for entry in entries] == [".", "nested"]


def test_adopt_shared_infra_requires_every_value_before_side_effects(tmp_path):
    with pytest.raises(worktree.WorktreeError, match="all-or-nothing"):
        worktree.adopt(
            tmp_path,
            "demo",
            "target",
            shared_infra="primary",
            shared_infra_services=None,
            shared_infra_ref_projects="project",
        )


def test_create_marks_partial_multi_root_preparation_and_preserves_worktree_error(
    monkeypatch, tmp_path
):
    primary, target, shared = _create_setup(
        monkeypatch, tmp_path, roots=[tmp_path / "primary", tmp_path / "primary" / "nested"], failure=True
    )
    calls = []
    monkeypatch.setattr(worktree, "_mark_recovery", lambda record, status: calls.append(status) or record)
    count = 0

    def read_facts(_root):
        nonlocal count
        count += 1
        if count == 2:
            raise worktree.WorktreeError("nested facts invalid")
        return {"network": "net"}

    monkeypatch.setattr(workspace_env, "read_generated_facts", read_facts)
    shared.read_record = lambda _path: (_ for _ in ()).throw(RuntimeError("repair failed"))
    with pytest.raises(worktree.WorktreeError, match="nested facts invalid"):
        worktree.create(tmp_path, "demo", path=target)
    assert calls == ["env-generation-failed"]


def test_create_wraps_a_non_workspace_multi_root_failure(monkeypatch, tmp_path):
    primary, target, _shared = _create_setup(
        monkeypatch, tmp_path, roots=[tmp_path / "primary", tmp_path / "primary" / "nested"], failure=True
    )
    calls = []
    monkeypatch.setattr(worktree, "_mark_recovery", lambda record, status: calls.append(status) or record)
    count = 0

    def read_facts(_root):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("nested generation broke")
        return {"network": "net"}

    monkeypatch.setattr(workspace_env, "read_generated_facts", read_facts)
    with pytest.raises(worktree.WorktreeError, match="multi-root preparation failed"):
        worktree.create(tmp_path, "demo", path=target)
    assert calls == ["env-generation-failed"]


def _removal_fixture(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    path = repo / ".worktrees" / "demo"
    path.mkdir(parents=True)
    (path / ".git").write_text("gitdir: x\n", encoding="utf-8")
    wt = worktree.WorktreeInfo(path, "demo", "a" * 8)
    monkeypatch.setattr(worktree, "find_instance_record", lambda *_args: None)
    monkeypatch.setattr(worktree, "find_worktree", lambda *_args: wt)
    monkeypatch.setattr(worktree, "_ciu_root_offset", lambda _root: Path("."))
    monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(worktree, "release_own_lease", lambda *_args: None)
    return repo, path


def test_remove_reports_shared_ownership_lookup_failure(monkeypatch, tmp_path):
    repo, _path = _removal_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: SimpleNamespace(
        discover_git_context=lambda _path: (_ for _ in ()).throw(RuntimeError("ownership unavailable"))
    ))
    with pytest.raises(worktree.WorktreeError, match="could not inspect shared"):
        worktree.remove(repo, "demo")


def test_remove_reports_shared_legacy_removal_failure(monkeypatch, tmp_path):
    repo, _path = _removal_fixture(monkeypatch, tmp_path)
    shared = SimpleNamespace(
        discover_git_context=lambda _path: (repo, repo / ".git", "demo", "a" * 40),
        list_workspaces=lambda _common: [],
        remove_unrecorded_workspace=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("remove failed")
        ),
    )
    monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
    with pytest.raises(worktree.WorktreeError, match="shared legacy workspace removal failed"):
        worktree.remove(repo, "demo")
