"""Coverage-fill wave A: CLI root selection, reset safety, managed-network
verification entry points and the worktree record schema/recovery branches.

Docker is replaced at the ``procutil`` / network-details seam; Git, the
shared-workspace records and the allocation lock are real.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, engine, workspace_env, worktree  # noqa: E402
from ciu.config_constants import GLOBAL_CONFIG_DEFAULTS  # noqa: E402

from test_ciu_worktree import (  # noqa: E402,F401
    _git,
    fake_generate_env,
    rootless_repo,
    tmp_repo,
)

NET_ID = "a" * 64
SHA = "b" * 40


# --------------------------------------------------------------------------
# cli._resolve_worktree_ciu_root_cli
# --------------------------------------------------------------------------

def test_explicit_nested_ciu_root_is_selected_as_is(tmp_path):
    family = tmp_path / "family"
    nested = family / "svc"
    nested.mkdir(parents=True)
    (nested / GLOBAL_CONFIG_DEFAULTS).write_text("[ciu]\n", encoding="utf-8")

    selected = cli._resolve_worktree_ciu_root_cli(family, nested, family)

    assert selected == nested.resolve()


# --------------------------------------------------------------------------
# engine._require_no_compose_project_containers / reset_service
# --------------------------------------------------------------------------

def test_inspection_oserror_is_reported_and_preserves_stack(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    with patch.object(
        engine.procutil, "run_cmd", side_effect=OSError("docker socket gone")
    ):
        with pytest.raises(RuntimeError) as exc:
            engine._require_no_compose_project_containers("proj", "boom", stack)
    text = str(exc.value)
    assert "[CIU-137]" in text
    assert "'proj'" in text
    assert "boom" in text
    assert "cannot inspect whether its containers remain (docker socket gone)" in text
    assert f"Preserving host volume directories and rendered configuration under {stack}" in text
    assert isinstance(exc.value.__cause__, OSError)


def test_reset_refuses_volume_whose_resolution_fails_and_keeps_it(tmp_path, monkeypatch):
    from ciu.workspace_env import GENERATED_FACTS_KEYS, write_generated_facts

    facts = {key: "" for key in GENERATED_FACTS_KEYS}
    facts["repo_name"] = "dstdns"
    facts["instance_id"] = "abc123"
    write_generated_facts(tmp_path, facts)
    stack = tmp_path / "stack"
    stack.mkdir()
    volume = stack / "vol-data"
    volume.mkdir()
    (volume / "keep.txt").write_text("preserve")
    generated = stack / "ciu.compose.yml"
    generated.touch()

    real_resolve = Path.resolve

    def flaky_resolve(self, strict=False):
        # The directory vanishes between the is_dir() probe and resolution.
        if strict and self.name == "vol-data":
            raise FileNotFoundError(2, "No such file or directory", str(self))
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)
    with (
        patch.object(
            engine.procutil, "run_cmd",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ),
        patch.object(engine, "_rmtree_with_fallback") as remove_volume,
    ):
        with pytest.raises(RuntimeError) as exc:
            engine.reset_service(
                {"deploy": {"project_name": "p", "labels": {"prefix": "dstdns"}}},
                stack, assume_yes=True, repo_root=tmp_path,
            )

    text = str(exc.value)
    assert "[CIU-137] refusing to remove volume path" in text
    assert str(volume) in text
    assert "The path is preserved." in text
    assert isinstance(exc.value.__cause__, OSError)
    remove_volume.assert_not_called()
    assert (volume / "keep.txt").read_text() == "preserve"
    assert generated.is_file()


# --------------------------------------------------------------------------
# workspace_env: verify_managed_record / verify_workspace_runtime_network /
# bootstrap_env_init
# --------------------------------------------------------------------------

@pytest.fixture
def managed_gate_open(monkeypatch):
    """Let the managed-verification code run (the suite gate normally skips it)."""
    monkeypatch.setattr(workspace_env, "_test_suite_gate_active", lambda: False)


def test_managed_verification_without_repo_root_is_refused(managed_gate_open, monkeypatch):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    created = []
    monkeypatch.setattr(
        workspace_env, "_ensure_network_exists", lambda *a, **k: created.append(a)
    )
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.ensure_workspace_network(
            "net-x", auto_connect=False, owner_instance_id="id1",
            verify_managed_instance=True,
        )
    assert "cannot verify managed runtime network without REPO_ROOT" in str(exc.value)
    assert created == []  # refused before any network could be created


def test_final_runtime_network_check_translates_worktree_refusal(
    managed_gate_open, monkeypatch, tmp_path
):
    seen = {}

    def refuse(root, **kwargs):
        seen["root"] = root
        seen["kwargs"] = kwargs
        raise worktree.WorktreeError("[S16] network vanished")

    monkeypatch.setattr(worktree, "verify_own_runtime_network", refuse)
    monkeypatch.setenv("INSTANCE_ID", "id9")
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "repo-id9-network")

    with pytest.raises(workspace_env.WorkspaceEnvError) as exc:
        workspace_env.verify_workspace_runtime_network(tmp_path)

    assert str(exc.value) == "[S16] network vanished"
    assert isinstance(exc.value.__cause__, worktree.WorktreeError)
    assert seen["root"] == tmp_path
    assert seen["kwargs"] == {
        "expected_instance_id": "id9",
        "expected_network": "repo-id9-network",
        "require_network_present": True,
    }


def test_bootstrap_env_init_propagates_managed_verification_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace_env, "generate_ciu_env", lambda root: tmp_path / "ciu.env")
    monkeypatch.setattr(workspace_env, "seed_generated_env", lambda root: None)
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "repo-x-network")
    monkeypatch.setenv("INSTANCE_ID", "x")
    warned = []
    monkeypatch.setattr(workspace_env, "_log_warn", warned.append)
    tls = []
    monkeypatch.setattr(workspace_env, "_check_tls_access", lambda: tls.append(1))

    def drifted(*args, **kwargs):
        assert kwargs["verify_managed_instance"] is True
        raise workspace_env.ManagedRuntimeVerificationError("[S16] identity drifted")

    monkeypatch.setattr(workspace_env, "ensure_workspace_network", drifted)

    with pytest.raises(workspace_env.ManagedRuntimeVerificationError, match="identity drifted"):
        workspace_env.bootstrap_env_init(tmp_path)

    assert warned == []  # not downgraded to the "network setup skipped" warning
    assert tls == []  # env setup did not continue past the failure


# --------------------------------------------------------------------------
# worktree._path_may_have_git_context
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "key", ["GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"]
)
def test_external_git_selector_means_git_context_may_exist(key, monkeypatch, tmp_path):
    for other in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.delenv(other, raising=False)
    monkeypatch.setenv(key, "/somewhere")
    assert worktree._path_may_have_git_context(tmp_path) is True


def test_file_inside_a_checkout_has_git_context(tmp_repo, monkeypatch):
    for other in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.delenv(other, raising=False)
    assert worktree._path_may_have_git_context(tmp_repo / "README.md") is True


def test_unreadable_git_marker_is_indeterminate_not_unmanaged(tmp_path, monkeypatch):
    for other in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.delenv(other, raising=False)
    real_lstat = Path.lstat

    def denied(self):
        if self.name == ".git":
            raise PermissionError(13, "Permission denied", str(self))
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", denied)
    assert worktree._path_may_have_git_context(tmp_path) is True


def test_nonexistent_path_is_indeterminate_not_unmanaged(tmp_path, monkeypatch):
    for other in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        monkeypatch.delenv(other, raising=False)
    assert worktree._path_may_have_git_context(tmp_path / "does" / "not" / "exist") is True


# --------------------------------------------------------------------------
# worktree._shared_record_for_logical_name
# --------------------------------------------------------------------------

def test_shared_record_lookup_outside_git_family_is_refused(tmp_path):
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree._shared_record_for_logical_name(tmp_path, "ghost")
    assert "cannot inspect shared worktree records while recovering 'ghost'" in str(exc.value)


def test_two_shared_records_for_one_logical_name_are_ambiguous(tmp_repo, monkeypatch):
    shared = worktree._shared_worktree()

    def ciu_record(path):
        return SimpleNamespace(
            purpose="ciu", labels={"ciu.logical_name": "dup"}, worktree_path=path
        )

    monkeypatch.setattr(
        shared, "list_workspaces",
        lambda _common: [ciu_record("/a"), ciu_record("/b")],
    )
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree._shared_record_for_logical_name(tmp_repo, "dup")
    assert "multiple shared CIU workspaces claim logical identity 'dup'" in str(exc.value)


# --------------------------------------------------------------------------
# worktree._restore_missing_instance_record
# --------------------------------------------------------------------------

@pytest.fixture
def created(tmp_repo, fake_generate_env):
    """A really created instance plus its genuine shared record."""
    worktree.create(tmp_repo, "ctrl")
    record = worktree.find_instance_record(tmp_repo, "ctrl")
    assert record is not None
    shared_record = worktree._shared_record_for_logical_name(tmp_repo, "ctrl")
    assert shared_record is not None
    return tmp_repo, record, shared_record


def _with_metadata(shared_record, **changes):
    metadata = dict(shared_record.metadata)
    for key, value in changes.items():
        if value is _DELETE:
            metadata.pop(key, None)
        else:
            metadata[key] = value
    return replace(shared_record, metadata=metadata)


_DELETE = object()


def _restore_with(monkeypatch, repo, shared_record):
    monkeypatch.setattr(
        worktree, "_shared_record_for_logical_name", lambda _r, _n: shared_record
    )
    return worktree._restore_missing_instance_record(repo, "ctrl")


def test_restore_without_any_shared_record_returns_none(tmp_repo):
    assert worktree._restore_missing_instance_record(tmp_repo, "ghost") is None


def test_restore_refuses_shared_workspace_without_ciu_snapshot(created, monkeypatch):
    repo, _record, shared_record = created
    broken = _with_metadata(shared_record, **{worktree._CIU_WORKTREE_RECORD_METADATA: _DELETE})
    with pytest.raises(worktree.WorktreeError) as exc:
        _restore_with(monkeypatch, repo, broken)
    assert "has no durable CIU record snapshot" in str(exc.value)
    assert "refusing to create a second checkout" in str(exc.value)


def test_restore_refuses_missing_root_offset(created, monkeypatch):
    repo, _record, shared_record = created
    broken = _with_metadata(shared_record, ciu_root_offset=7)
    with pytest.raises(worktree.WorktreeError, match="has no valid root offset"):
        _restore_with(monkeypatch, repo, broken)


@pytest.mark.parametrize("offset", ["../escape", "/abs/root"])
def test_restore_refuses_unsafe_root_offset(created, monkeypatch, offset):
    repo, _record, shared_record = created
    broken = _with_metadata(shared_record, ciu_root_offset=offset)
    with pytest.raises(worktree.WorktreeError, match="has unsafe root offset"):
        _restore_with(monkeypatch, repo, broken)


def test_restore_refuses_malformed_snapshot(created, monkeypatch):
    repo, _record, shared_record = created
    broken = _with_metadata(
        shared_record, **{worktree._CIU_WORKTREE_RECORD_METADATA: {"schema_version": 6}}
    )
    with pytest.raises(worktree.WorktreeError) as exc:
        _restore_with(monkeypatch, repo, broken)
    assert "cannot safely restore CIU record for 'ctrl'" in str(exc.value)
    assert "malformed" in str(exc.value)


def test_restore_refuses_workspace_that_cannot_be_ensured(created, monkeypatch):
    repo, _record, shared_record = created
    shared = worktree._shared_worktree()

    def cannot_ensure(_record):
        raise shared.WorkspaceError("checkout vanished")

    monkeypatch.setattr(shared, "ensure_workspace", cannot_ensure)
    with pytest.raises(worktree.WorktreeError) as exc:
        _restore_with(monkeypatch, repo, shared_record)
    assert "cannot safely restore CIU record for 'ctrl': checkout vanished" in str(exc.value)


def test_restore_refuses_snapshot_that_disagrees_with_shared_record(created, monkeypatch):
    repo, _record, shared_record = created
    disagreeing = replace(shared_record, branch="some-other-branch")
    with pytest.raises(worktree.WorktreeError) as exc:
        _restore_with(monkeypatch, repo, disagreeing)
    assert "disagrees with its saved CIU allocation snapshot" in str(exc.value)


def test_restore_refuses_incomplete_allocation_with_unknown_reset_phase(created, monkeypatch):
    repo, record, shared_record = created
    snapshot = record.to_dict()
    snapshot.pop("fork_point_sha", None)
    snapshot.update(state="allocating", adopted=False, lease=None)
    snapshot["runtime"] = {"instance_id": None, "network": None}
    snapshot["network_id"] = None
    metadata = _with_metadata(
        shared_record,
        **{
            worktree._CIU_WORKTREE_RECORD_METADATA: snapshot,
            worktree._CIU_CHECKOUT_RESET_PHASE_METADATA: "started",
        },
    )
    with pytest.raises(worktree.WorktreeError) as exc:
        _restore_with(monkeypatch, repo, metadata)
    assert "cannot restore incomplete allocation 'ctrl'" in str(exc.value)
    assert "'started'" in str(exc.value)


def test_restore_refuses_not_started_phase_when_snapshot_lacks_pending_marker(
    created, monkeypatch
):
    repo, record, shared_record = created
    snapshot = record.to_dict()
    snapshot.pop("fork_point_sha", None)
    snapshot.update(state="allocating", adopted=False, lease=None,
                    checkout_reset_pending=False)
    snapshot["runtime"] = {"instance_id": None, "network": None}
    snapshot["network_id"] = None
    metadata = _with_metadata(
        shared_record,
        **{
            worktree._CIU_WORKTREE_RECORD_METADATA: snapshot,
            worktree._CIU_CHECKOUT_RESET_PHASE_METADATA: "not-started",
        },
    )
    with pytest.raises(worktree.WorktreeError, match="cannot restore incomplete allocation"):
        _restore_with(monkeypatch, repo, metadata)


# --------------------------------------------------------------------------
# WorktreeInstanceRecord.to_dict validation
# --------------------------------------------------------------------------

def _record(**overrides) -> worktree.WorktreeInstanceRecord:
    base = dict(
        logical_name="demo",
        display_name="demo",
        branch="demo",
        git_worktree_path=Path("/abs/demo"),
        ciu_root_offset=Path("."),
        created_at_utc="2026-10-14T00:00:00Z",
        base_ref=SHA,
        state="allocating",
        schema_version=6,
        overlay_intent_known=True,
        initial_overlay_pending=False,
        adopted=False,
        checkout_reset_pending=False,
    )
    base.update(overrides)
    return worktree.WorktreeInstanceRecord(**base)


def test_valid_v6_record_serializes_its_fields():
    doc = _record(network="n", instance_id="i", network_id=NET_ID).to_dict()
    assert doc["schema_version"] == 6
    assert doc["network_id"] == NET_ID
    assert doc["checkout_reset_pending"] is False
    assert doc["base_branch_ref"] is None


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"initial_overlay_pending": "yes"}, "initial overlay pending marker must be boolean"),
        ({"adopted": "no"}, "worktree adopted marker must be boolean or null"),
        (
            {"initial_overlay_pending": True, "overlay_intent_known": True, "adopted": None},
            "pending initial overlay recovery requires known allocation origin",
        ),
        ({"checkout_reset_pending": None}, "checkout reset pending marker must be boolean"),
        (
            {"network": "n", "instance_id": "i", "network_id": "not-a-docker-id"},
            "runtime network id must be a full 64-hex Docker id or null",
        ),
        (
            {"network": "n", "instance_id": "i", "network_id": 12345},
            "runtime network id must be a full 64-hex Docker id or null",
        ),
        ({"network_id": NET_ID}, "runtime network id requires a runtime network name"),
        (
            {"checkout_reset_pending": True, "adopted": True},
            "a pending checkout reset requires a created allocation",
        ),
        (
            {"checkout_reset_pending": True, "fork_point_sha": SHA},
            "a pending checkout reset requires a created allocation",
        ),
    ],
)
def test_record_serialization_refuses_inconsistent_state(overrides, message):
    with pytest.raises(ValueError, match=message):
        _record(**overrides).to_dict()


def test_pending_checkout_reset_on_a_ready_record_is_refused():
    with pytest.raises(ValueError, match="a pending checkout reset requires a created allocation"):
        _record(state="ready", checkout_reset_pending=True).to_dict()


def test_pre_v6_record_cannot_carry_base_branch_ref():
    with pytest.raises(ValueError, match="base_branch_ref requires worktree record schema version 6"):
        _record(schema_version=5, base_branch_ref="main").to_dict()


# --------------------------------------------------------------------------
# worktree._record_from_dict
# --------------------------------------------------------------------------

def _raw_base(version) -> dict:
    return {
        "schema_version": version,
        "logical_name": "demo",
        "display_name": "demo",
        "branch": "demo",
        "git_worktree_path": "/abs/demo",
        "ciu_root_offset": ".",
        "created_at_utc": "2026-10-14T00:00:00Z",
        "base_ref": SHA,
        "state": "allocating",
        "runtime": {"instance_id": None, "network": None},
        "recovery_status": None,
    }


@pytest.mark.parametrize("version", [99, True])
def test_unsupported_schema_version_is_refused(version):
    # Version 99 and the boolean True both carry only the base key set, so the
    # key check passes and the version check itself must refuse them.
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree._record_from_dict(_raw_base(version), Path("/abs/demo/rec.json"))
    assert "unsupported worktree record schema_version" in str(exc.value)
    assert repr(version) in str(exc.value)


def test_pending_overlay_recovery_with_unknown_origin_is_refused():
    raw = _raw_base(4)
    raw.update(
        lease=None,
        initial_overlay="payload",
        overlay_intent_known=True,
        initial_overlay_pending=True,
        adopted=None,
    )
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree._record_from_dict(raw, Path("/abs/demo/rec.json"))
    assert "pending initial overlay recovery requires known allocation origin" in str(exc.value)


def test_valid_v4_record_round_trips_through_the_parser():
    raw = _raw_base(4)
    raw.update(
        lease=None,
        initial_overlay="payload",
        overlay_intent_known=True,
        initial_overlay_pending=True,
        adopted=False,
    )
    parsed = worktree._record_from_dict(raw, Path("/abs/demo/rec.json"))
    assert parsed.initial_overlay_pending is True
    assert parsed.adopted is False
    assert parsed.schema_version == 4


# --------------------------------------------------------------------------
# worktree._ciu_root_committed_at_checkout
# --------------------------------------------------------------------------

def test_committed_root_membership_outside_git_is_refused(tmp_path):
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree._ciu_root_committed_at_checkout(tmp_path, Path("."))
    assert "could not determine committed CIU-root membership for" in str(exc.value)
    assert str(tmp_path) in str(exc.value)


# --------------------------------------------------------------------------
# worktree._generate_env_in
# --------------------------------------------------------------------------

def test_identity_only_generation_cannot_reuse_allocation_lock(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(worktree.WorktreeError) as exc:
        worktree._generate_env_in(
            tmp_path, identity_only=True, allocation_lock_held=True
        )
    assert "identity-only env generation cannot reuse the allocation lock" in str(exc.value)
    assert ran == []  # no child was started


# --------------------------------------------------------------------------
# worktree.up_instance network verification
# --------------------------------------------------------------------------

@pytest.fixture
def ready(tmp_repo, fake_generate_env, write_instance_facts):
    worktree.create(tmp_repo, "ctrl")
    record = worktree.find_instance_record(tmp_repo, "ctrl")
    assert record is not None and record.state == "ready"
    write_instance_facts(
        record.ciu_root,
        repo_root=str(record.ciu_root),
        physical_repo_root=str(record.ciu_root),
        repo_name="repo",
        instance_id=record.instance_id,
        network=record.network,
    )
    return tmp_repo, record


@pytest.fixture
def child_calls(monkeypatch):
    calls = []

    def fake_run(argv, cwd, env):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(worktree, "_run_child", fake_run)
    return calls


def test_up_marks_collision_and_does_not_start_when_network_has_foreign_owner(
    ready, child_calls, monkeypatch
):
    repo, record = ready
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda _n: {"id": NET_ID, "labels": {worktree._NETWORK_OWNER_LABEL: "somebody-else"}},
    )
    with pytest.raises(worktree.RuntimeNetworkOwnershipError, match="expected"):
        worktree.up_instance(repo, "ctrl")

    assert child_calls == []
    failed = worktree.read_instance_record(record.record_path)
    assert failed.state == "recovery-required"
    assert failed.recovery_status == "runtime-collision"


def test_up_records_first_verified_network_object_before_starting(
    ready, child_calls, monkeypatch
):
    repo, record = ready
    assert record.network_id is None
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda _n: {"id": NET_ID, "labels": {worktree._NETWORK_OWNER_LABEL: record.instance_id}},
    )
    assert worktree.up_instance(repo, "ctrl") == 0

    assert len(child_calls) == 1
    saved = worktree.read_instance_record(record.record_path)
    assert saved.network_id == NET_ID
    assert saved.state == "ready"


def test_up_leaves_record_untouched_when_network_object_already_verified(
    ready, child_calls, monkeypatch
):
    repo, record = ready
    worktree._write_instance_record(replace(record, network_id=NET_ID))
    writes = []
    real_write = worktree._write_instance_record
    monkeypatch.setattr(
        worktree, "_write_instance_record",
        lambda rec: (writes.append(rec), real_write(rec))[1],
    )
    monkeypatch.setattr(
        worktree, "_docker_network_details",
        lambda _n: {"id": NET_ID, "labels": {}},
    )
    assert worktree.up_instance(repo, "ctrl") == 0
    assert writes == []
    assert len(child_calls) == 1


def test_up_skips_network_verification_for_record_without_runtime_identity(
    ready, child_calls, monkeypatch
):
    repo, record = ready
    identityless = replace(record, instance_id=None, network=None)
    monkeypatch.setattr(worktree, "_require_ready_record", lambda _r, _n: identityless)
    monkeypatch.setattr(worktree, "_sanitized_target_env", lambda _r, _rec: {})

    def no_docker(_network):
        raise AssertionError("network must not be inspected without an identity")

    monkeypatch.setattr(worktree, "_docker_network_details", no_docker)
    assert worktree.up_instance(repo, "ctrl") == 0
    assert len(child_calls) == 1


# --------------------------------------------------------------------------
# worktree._check_runtime_collision
# --------------------------------------------------------------------------

def _problem(i: int):
    return worktree._InstanceRecordProblem(
        record_path=Path(f"/fam/wt{i}/ciu.worktree.json"),
        worktree_path=Path(f"/fam/wt{i}"),
        logical_name=None,
        detail=f"broken {i}",
    )


def _scan(*problems):
    return worktree._InstanceRegistryScan(records=(), problems=tuple(problems))


def test_collision_check_wraps_failure_to_classify_unreadable_sibling(ready, monkeypatch):
    repo, record = ready
    monkeypatch.setattr(worktree, "_scan_instance_records", lambda _r: _scan(_problem(1)))

    def cannot_classify(path, offset):
        raise worktree.WorktreeError("git unavailable")

    monkeypatch.setattr(worktree, "_ciu_root_committed_at_checkout", cannot_classify)
    with pytest.raises(worktree.RuntimeNetworkVerificationError) as exc:
        worktree._check_runtime_collision(repo, record, "i", "n")
    assert "could not determine whether unreadable sibling /fam/wt1 has runtime identity" in str(exc.value)
    assert "git unavailable" in str(exc.value)


def test_unreadable_sibling_without_a_committed_ciu_root_is_ignored(ready, monkeypatch):
    repo, record = ready
    monkeypatch.setattr(worktree, "_scan_instance_records", lambda _r: _scan(_problem(1)))
    monkeypatch.setattr(worktree, "_ciu_root_committed_at_checkout", lambda p, o: False)
    # No exception: a sibling that never had a CIU root carries no runtime identity.
    assert worktree._check_runtime_collision(repo, record, "i", "n") is None


def test_unreadable_sibling_report_is_capped_at_three_with_remainder_count(ready, monkeypatch):
    repo, record = ready
    monkeypatch.setattr(
        worktree, "_scan_instance_records",
        lambda _r: _scan(*(_problem(i) for i in range(1, 6))),
    )
    monkeypatch.setattr(worktree, "_ciu_root_committed_at_checkout", lambda p, o: True)
    with pytest.raises(worktree.RuntimeNetworkVerificationError) as exc:
        worktree._check_runtime_collision(repo, record, "i", "n")
    text = str(exc.value)
    for i in (1, 2, 3):
        assert f"broken {i}" in text
    assert "broken 4" not in text and "broken 5" not in text
    assert "and 2 more unreadable CIU-root record(s)" in text
