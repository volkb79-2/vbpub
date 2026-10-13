"""Refusal and boundary behaviour of ``cmru.gate_snapshot`` below the lane level.

Each test names one real condition the exact-HEAD sidecar must refuse (or tolerate)
and builds that condition with real Git worktrees; nothing here fakes Git.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
from pathlib import Path

import pytest
import worktree

from cmru import gate_snapshot as gs
from tests.test_release_gate import _closed_snapshot, _fixture, _git


def _rewrite_record(snapshot, **changes):
    record = worktree.read_record(snapshot.context.record_path)
    worktree.write_record(dataclasses.replace(record, **changes))


def _rewrite_metadata(snapshot, **metadata):
    record = worktree.read_record(snapshot.context.record_path)
    worktree.write_record(
        dataclasses.replace(record, metadata={**record.metadata, **metadata}),
    )


# --- Git environment policy ---------------------------------------------------


def test_git_policy_refuses_a_malformed_config_count_and_restores_the_environment(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "many")
    with pytest.raises(RuntimeError, match="GIT_CONFIG_COUNT is malformed"):
        with gs._host_git_policy():
            pass
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "0")
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    with gs._host_git_policy():
        assert os.environ["GIT_CONFIG_COUNT"] == "3"
        assert os.environ["GIT_CONFIG_KEY_1"] == "core.hooksPath"
        assert os.environ["GIT_CONFIG_NOSYSTEM"] == "1"
    assert os.environ["GIT_CONFIG_COUNT"] == "1"
    assert os.environ["GIT_CONFIG_NOSYSTEM"] == "0"
    assert "GIT_CONFIG_GLOBAL" not in os.environ


def test_host_git_hooks_disabled_pins_git_to_the_verified_worktree_and_restores(
    tmp_path, monkeypatch,
):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("GIT_DIR", "/preset")
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    with gs.host_git_hooks_disabled(repo):
        assert os.environ["GIT_WORK_TREE"] == str(repo.resolve())
        assert "worktrees" in os.environ["GIT_DIR"]
        assert os.environ["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert os.environ["GIT_DIR"] == "/preset"
    assert "GIT_WORK_TREE" not in os.environ


def test_a_failing_git_command_reports_its_arguments_and_output(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match=r"Git command failed \(\d+\): git rev-parse --verify"):
        gs._git(repo, "rev-parse", "--verify", "no-such-ref")


# --- the Git pointer files the sidecar trusts ---------------------------------


def test_linked_context_refuses_every_pointer_it_cannot_verify(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    admin, common = gs._linked_git_context(repo)
    marker = repo / ".git"
    original = marker.read_text(encoding="utf-8")

    def refuses(text: str, match: str) -> None:
        marker.write_text(text, encoding="utf-8")
        with pytest.raises(RuntimeError, match=match):
            gs._linked_git_context(repo)
        marker.write_text(original, encoding="utf-8")

    refuses("garbage\n", "marker is malformed")
    refuses(f"gitdir: {admin}\nextra\n", "marker is malformed")
    refuses(f"gitdir: {tmp_path / 'nowhere'}\n", "cannot inspect linked-worktree admin dir")
    (tmp_path / "a-file").write_text("not a directory", encoding="utf-8")
    refuses(f"gitdir: {tmp_path / 'a-file'}\n", "admin path is not a real directory")

    marker.write_text(f"gitdir: {os.path.relpath(admin, repo)}\n", encoding="utf-8")
    assert gs._linked_git_context(repo) == (admin, common)  # a relative marker is fine
    marker.write_text(original, encoding="utf-8")

    commondir = admin / "commondir"
    saved_common = commondir.read_text(encoding="utf-8")
    for text in ("\n", "a\nb\n"):
        commondir.write_text(text, encoding="utf-8")
        with pytest.raises(RuntimeError, match="not a regular one-line file"):
            gs._linked_git_context(repo)
    commondir.write_text(saved_common, encoding="utf-8")

    gitdir = admin / "gitdir"
    saved_gitdir = gitdir.read_text(encoding="utf-8")
    other = tmp_path / "other-checkout"
    other.mkdir()
    (other / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    gitdir.write_text(f"{other / '.git'}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="gitdir pointer disagrees"):
        gs._linked_git_context(repo)
    gitdir.write_text("x" * 5000, encoding="utf-8")
    with pytest.raises(RuntimeError, match="is too large"):
        gs._linked_git_context(repo)
    gitdir.write_bytes(b"\xff\xfe\xfd")
    with pytest.raises(RuntimeError, match="cannot read linked-worktree pointer"):
        gs._linked_git_context(repo)
    gitdir.write_text(saved_gitdir, encoding="utf-8")

    with pytest.raises(RuntimeError, match="disagrees with recorded common dir"):
        gs._linked_git_context(repo, expected_common=tmp_path)

    stray = tmp_path / "stray-admin"
    shutil.copytree(admin, stray)
    (stray / "commondir").write_text(f"{common}\n", encoding="utf-8")
    (stray / "gitdir").write_text(f"{repo / '.git'}\n", encoding="utf-8")
    marker.write_text(f"gitdir: {stray}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="outside its common worktrees dir"):
        gs._linked_git_context(repo)


# --- what a source must be before it can be snapshotted -----------------------


def _refuses_open(repo: Path, match: str) -> None:
    with pytest.raises(RuntimeError, match=match):
        gs.open_gate_snapshot(repo)


def test_a_main_checkout_a_detached_head_and_dirty_state_are_refused(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _refuses_open(tmp_path / "primary", "marker is not a regular file")

    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    _refuses_open(repo, "release gate source has tracked changes")
    _git(repo, "checkout", "--quiet", "--", "README.md")

    _git(repo, "update-index", "--assume-unchanged", "README.md")
    _refuses_open(repo, "assume-unchanged or skip-worktree")
    _git(repo, "update-index", "--no-assume-unchanged", "README.md")

    _git(repo, "checkout", "--quiet", "--detach")
    _refuses_open(repo, "must be attached to a branch")


def test_a_configured_git_filter_is_refused_before_checkout(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    (repo / ".gitattributes").write_text("*.md filter=evil\n", encoding="utf-8")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "--quiet", "-m", "declare a filter")
    _refuses_open(repo, "declares a Git filter that could execute during checkout: README.md")


def test_an_unreadable_or_mistyped_host_manifest_is_refused(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{broken", encoding="utf-8")
    _refuses_open(repo, "release-gate snapshot manifest is invalid")
    manifest.write_text("[]", encoding="utf-8")
    _refuses_open(repo, "manifest is not an object")


# --- admitting, resuming and upgrading a retained snapshot ----------------------


def _expected_target(repo: Path) -> Path:
    head = _git(repo, "rev-parse", "HEAD")
    identity = hashlib.sha256(
        f"{repo.resolve()}\0cmru-release-gate\0{head}".encode("utf-8")
    ).hexdigest()[:20]
    return repo.resolve().parent / f"cmru-gate-{identity}"


def test_an_unrecorded_directory_at_the_snapshot_path_is_never_adopted(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _expected_target(repo).mkdir()
    _refuses_open(repo, "exists without a workspace record")


def test_a_retained_snapshot_whose_record_identity_changed_is_refused(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    _rewrite_record(snapshot, purpose="not-a-gate")
    _refuses_open(repo, "existing gate snapshot identity does not match this source")


def test_a_corrupt_or_disagreeing_ref_digest_is_refused_and_releases_the_lease(
    tmp_path, monkeypatch,
):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)

    _rewrite_metadata(snapshot, refs_sha256="not-a-digest")
    _refuses_open(repo, "invalid Git-ref digest")
    assert worktree.read_record(snapshot.context.record_path).lease is None  # not left held

    _rewrite_metadata(snapshot, refs_sha256=snapshot.refs_sha256)
    gs.write_snapshot_manifest(snapshot, "passed")
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(snapshot.refs_sha256, "f" * 64),
        encoding="utf-8",
    )
    _refuses_open(repo, "disagrees with the host manifest")


def test_a_snapshot_recorded_under_another_digest_scope_is_upgraded(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    record = worktree.read_record(snapshot.context.record_path)
    metadata = {k: v for k, v in record.metadata.items() if k != "refs_scope"}
    worktree.write_record(dataclasses.replace(
        record, metadata={**metadata, "refs_sha256": "0" * 64},
    ))

    reopened = gs.open_gate_snapshot(repo)
    try:
        assert reopened.refs_sha256 == snapshot.refs_sha256
    finally:
        gs.close_gate_snapshot(reopened)
    upgraded = worktree.read_record(snapshot.context.record_path).metadata
    assert upgraded["refs_scope"] == "protected-refs/1"
    assert upgraded["refs_sha256"] == snapshot.refs_sha256


def test_a_moved_snapshot_or_source_is_detected_during_the_run(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = gs.open_gate_snapshot(repo)
    try:
        gs.assert_source_head(snapshot)
        gs._verify_snapshot(snapshot.context, snapshot.commit)
        _rewrite_record(snapshot, branch="someone-elses-branch")
        with pytest.raises(RuntimeError, match="workspace identity changed during admission"):
            gs._verify_snapshot(snapshot.context, snapshot.commit)
        _rewrite_record(snapshot, branch=snapshot.branch)
        _git(repo, "commit", "--allow-empty", "-q", "-m", "the source moved")
        with pytest.raises(RuntimeError, match="source HEAD changed during the run"):
            gs.assert_source_head(snapshot)
        with pytest.raises(RuntimeError, match="source HEAD changed during the run"):
            gs._verify_snapshot(snapshot.context, snapshot.commit)
    finally:
        gs.close_gate_snapshot(snapshot)


# --- copying evidence ----------------------------------------------------------


def test_evidence_files_must_be_private_regular_files_of_bounded_size(tmp_path):
    plain = tmp_path / "plain"
    plain.write_text("data", encoding="utf-8")
    assert gs._safe_regular_file(plain).st_size == 4
    link = tmp_path / "link"
    link.symlink_to(plain)
    with pytest.raises(RuntimeError, match="not a private regular file"):
        gs._safe_regular_file(link)
    shared = tmp_path / "shared"
    os.link(plain, shared)
    with pytest.raises(RuntimeError, match="not a private regular file"):
        gs._safe_regular_file(plain)
    big = tmp_path / "big"
    big.write_text("12345", encoding="utf-8")
    with pytest.raises(RuntimeError, match="exceeds the size limit"):
        gs._safe_regular_file(big, max_bytes=1)
    assert gs._sha256_file(big) == hashlib.sha256(b"12345").hexdigest()
    with pytest.raises(FileNotFoundError):
        gs._safe_regular_file(tmp_path / "absent")


def test_copy_file_replaces_atomically_and_refuses_unsafe_endpoints(tmp_path, monkeypatch):
    source = tmp_path / "source.json"
    source.write_text("new", encoding="utf-8")
    destination = tmp_path / "out" / "dest.json"
    gs._copy_file(tmp_path / "absent.json", destination)  # nothing to copy: no error
    assert not destination.exists()
    gs._copy_file(source, destination)
    assert destination.read_text(encoding="utf-8") == "new"
    assert (destination.stat().st_mode & 0o777) == 0o600
    source.write_text("newer", encoding="utf-8")
    gs._copy_file(source, destination)
    assert destination.read_text(encoding="utf-8") == "newer"

    linked_destination = tmp_path / "out" / "linked.json"
    linked_destination.symlink_to(source)
    with pytest.raises(RuntimeError, match="not a private regular file"):
        gs._copy_file(source, linked_destination)

    linked_source = tmp_path / "linked-source.json"
    linked_source.symlink_to(source)
    with pytest.raises(RuntimeError, match="not a private regular file"):
        gs._copy_file(linked_source, destination)

    monkeypatch.setattr(gs, "_MAX_EVIDENCE_BYTES", 3)
    with pytest.raises(RuntimeError, match="exceeds the size limit"):
        gs._copy_file(source, destination)
    monkeypatch.undo()

    def refuse_replace(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(gs.os, "replace", refuse_replace)
    with pytest.raises(OSError, match="disk full"):
        gs._copy_file(source, tmp_path / "out" / "fresh.json")
    assert not [p for p in (tmp_path / "out").iterdir() if p.name.startswith(".fresh.json.")]


def test_the_evidence_directory_must_be_a_real_directory(tmp_path):
    gs._ensure_real_directory(tmp_path / "a" / "b")
    assert (tmp_path / "a" / "b").is_dir()
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "symlinked").symlink_to(real)
    with pytest.raises(RuntimeError, match="not a real directory"):
        gs._ensure_real_directory(tmp_path / "symlinked")


def test_run_gate_state_may_hold_only_private_regular_files(tmp_path, monkeypatch):
    state = tmp_path / ".run-gate"
    gs._validate_run_gate_state(state)  # absent state is fine
    state.mkdir()
    (state / "history").mkdir()
    (state / "history" / "run.json").write_text("{}", encoding="utf-8")
    gs._validate_run_gate_state(state)

    (state / "alias").symlink_to(state / "history")
    with pytest.raises(RuntimeError, match="contains a symlink"):
        gs._validate_run_gate_state(state)
    (state / "alias").unlink()

    (state / "history" / "cmru.secret.toml").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="contains a publisher secret"):
        gs._validate_run_gate_state(state)
    (state / "history" / "cmru.secret.toml").unlink()

    os.link(state / "history" / "run.json", tmp_path / "second-name")
    with pytest.raises(RuntimeError, match="not a private regular file"):
        gs._validate_run_gate_state(state)
    (tmp_path / "second-name").unlink()

    def unreadable(_path, followlinks, onerror):
        onerror(OSError("denied"))
        return iter(())

    monkeypatch.setattr(gs.os, "walk", unreadable)
    with pytest.raises(RuntimeError, match="cannot inspect snapshot Run-Gate state: denied"):
        gs._validate_run_gate_state(state)
    monkeypatch.undo()

    shutil.rmtree(state)
    state.write_text("a file, not a directory", encoding="utf-8")
    with pytest.raises(RuntimeError, match="state is not a real directory"):
        gs._validate_run_gate_state(state)


# --- what a lane may leave in its snapshot ---------------------------------------


def test_lane_output_validation_names_each_kind_of_unapproved_residue(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = gs.open_gate_snapshot(repo)
    root = snapshot.worktree_path

    def validate():
        gs.validate_lane_outputs(
            root, snapshot.commit, protected_refs=snapshot.protected_refs,
        )

    try:
        # Generated residue and Run-Gate state are expected and tolerated.
        for relative in (
            ".run-gate/history/run.json", "cmru/.assay/verdict-cmru.json",
            "cmru/.run-gate/history.json", "cmru/.run-gate/inflight/inflight.lock",
            "cmru/__pycache__/x.cpython-314.pyc", "cmru/.coverage", "cmru/.coverage.host.1",
            "cmru/cmru.egg-info/PKG-INFO", "cmru/stale.pyc",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
        validate()

        (root / "cmru" / "cmru.secret.toml").write_text("secret", encoding="utf-8")
        with pytest.raises(RuntimeError, match=r"unapproved .*cmru/cmru\.secret\.toml"):
            validate()
        (root / "cmru" / "cmru.secret.toml").unlink()

        (root / "README.md").write_text("tampered\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="changed tracked snapshot files or HEAD"):
            validate()
        _git(root, "checkout", "--quiet", "--", "README.md")

        plain = tmp_path / "plain-worktree"
        _git(tmp_path / "primary", "worktree", "add", "--quiet", "-b", "plain", str(plain), "HEAD")
        with pytest.raises(RuntimeError, match="no CMRU workspace record"):
            gs.validate_lane_outputs(plain, snapshot.commit, protected_refs=())
    finally:
        gs.close_gate_snapshot(snapshot)


# --- the host-only canary ------------------------------------------------------------


def test_the_canary_is_removed_only_when_it_is_the_exact_file_this_gate_planted(
    tmp_path, monkeypatch,
):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = gs.open_gate_snapshot(repo)
    try:
        gs.remove_source_canary(snapshot)  # nothing planted yet: nothing to do
        gs.plant_source_canary(snapshot)
        canary = snapshot.source_canary
        assert canary.read_text(encoding="ascii").startswith("CMRU-GATE-SOURCE-CANARY-")
        assert canary.name.startswith(".cmru-gate-canary-") and canary.name.endswith(".txt")
        gs.remove_source_canary(snapshot)
        assert not canary.exists()
        gs.remove_source_canary(snapshot)  # already gone: still fine

        gs.plant_source_canary(snapshot)
        replaced = snapshot.source_canary
        imposter = replaced.with_name("imposter.txt")
        imposter.write_text("someone else's file", encoding="utf-8")
        os.replace(imposter, replaced)  # a different inode, created while the canary lived
        with pytest.raises(RuntimeError, match="canary path changed during the live lane probe"):
            gs.remove_source_canary(snapshot)
        assert replaced.read_text(encoding="utf-8") == "someone else's file"
        replaced.unlink()
    finally:
        gs.close_gate_snapshot(snapshot)


# --- Run-Gate's per-snapshot Assay state ------------------------------------------------


def test_snapshot_assay_state_is_removed_in_both_layouts_and_never_through_a_link(
    tmp_path, monkeypatch,
):
    _fixture(tmp_path, monkeypatch)
    primary = tmp_path / "primary"

    # The snapshot lives OUTSIDE the primary checkout: external identity.
    outside = _closed_snapshot(tmp_path / "repository")
    record = worktree.read_record(outside.context.record_path)
    project_root = (record.worktree_path / "cmru").resolve(strict=False)
    external = (
        primary / ".run-gate" / "assay-state-external"
        / hashlib.sha256(os.fsencode(str(project_root))).hexdigest()
    )
    external.mkdir(parents=True)
    (external / "state.json").write_text("{}", encoding="utf-8")
    gs._remove_snapshot_assay_state(record)
    assert not external.exists()
    gs._remove_snapshot_assay_state(record)  # already gone: fine

    # A symlinked .run-gate would redirect the deletion: refused, nothing removed.
    elsewhere = tmp_path / "elsewhere"
    victim = elsewhere / "assay-state-external" / external.name
    victim.mkdir(parents=True)
    (victim / "keep.json").write_text("{}", encoding="utf-8")
    shutil.rmtree(primary / ".run-gate")
    (primary / ".run-gate").symlink_to(elsewhere)
    with pytest.raises(RuntimeError, match="not a plain directory chain"):
        gs._remove_snapshot_assay_state(record)
    assert (victim / "keep.json").exists()
    (primary / ".run-gate").unlink()

    # The snapshot lives INSIDE the primary checkout: path-derived identity.
    inside_source = primary / ".worktrees" / "inside-source"
    _git(primary, "worktree", "add", "--quiet", "-b", "inside-source", str(inside_source), "HEAD")
    inside = _closed_snapshot(inside_source)
    inside_record = worktree.read_record(inside.context.record_path)
    state = (
        primary / ".run-gate" / "assay-state" / ".worktrees" / inside.worktree_path.name / "cmru"
    )
    state.mkdir(parents=True)
    (state / "state.json").write_text("{}", encoding="utf-8")
    assert gs._snapshot_assay_state_dir(inside_record) == state
    gs.remove_gate_snapshot(
        inside.context.record_path, executor_git_root=primary, include_current=True,
    )
    assert not state.exists() and not inside.worktree_path.exists()


# --- remaining cleanup and manifest boundaries -------------------------------------


def test_a_malformed_manifest_digest_is_ignored_not_trusted(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    gs.write_snapshot_manifest(snapshot, "passed")
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(snapshot.refs_sha256, "xyz"),
        encoding="utf-8",
    )
    reopened = gs.open_gate_snapshot(repo)
    try:
        assert reopened.refs_sha256 == snapshot.refs_sha256
    finally:
        gs.close_gate_snapshot(reopened)


def test_a_failed_manifest_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)

    def refuse(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(gs.os, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        gs.write_snapshot_manifest(snapshot, "running")
    assert not list((repo / "cmru" / ".assay").glob(".release-gate-snapshot.*"))


def test_remove_gate_snapshot_releases_a_provably_dead_holders_lease(tmp_path, monkeypatch):
    import subprocess
    import sys

    from tests.test_release_gate import _lease_snapshot

    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    _lease_snapshot(
        snapshot, f"cmru-gate:{child.pid}:1:abc:{gs._local_namespace_identity()}", hours=1,
    )
    assert worktree.read_record(snapshot.context.record_path).lease is not None
    gs.remove_gate_snapshot(
        snapshot.context.record_path, executor_git_root=repo, include_current=True,
    )
    assert not snapshot.worktree_path.exists()


def test_cleanup_for_a_source_refuses_a_record_outside_its_snapshot_location(
    tmp_path, monkeypatch,
):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    stray = tmp_path / "elsewhere" / "cmru-gate-stray"
    worktree.create_workspace(
        repo, stray, branch="cmru-gate-stray", purpose="cmru-gate",
    )
    with pytest.raises(RuntimeError, match="outside its owned snapshot location"):
        gs.remove_gate_snapshots_for_source(repo)
    assert stray.is_dir()
