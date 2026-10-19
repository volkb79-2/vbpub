"""Refusal and boundary behaviour of ``cmru.gate_snapshot`` below the lane level.

Each test names one real condition the exact-HEAD sidecar must refuse (or tolerate)
and builds that condition with real Git worktrees; nothing here fakes Git.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

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
    # The policy APPENDS to a caller's GIT_CONFIG_COUNT (the sibling test pins that), and
    # the tester lane starts with one injected entry (`safe.directory`). Start empty so
    # the expectation below is about this test's subject, not about where it runs.
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    with gs.host_git_hooks_disabled(repo):
        assert os.environ["GIT_WORK_TREE"] == str(repo.resolve())
        assert "worktrees" in os.environ["GIT_DIR"]
        assert os.environ["GIT_CONFIG_KEY_0"] == "core.hooksPath"
    assert os.environ["GIT_DIR"] == "/preset"
    assert "GIT_WORK_TREE" not in os.environ


def _intended_and_decoy(tmp_path: Path):
    """An intended linked worktree and a decoy repository with distinct HEAD/index/objects/refs."""
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "init", "--quiet", "--initial-branch=main")
    _git(primary, "config", "user.name", "CMRU Gate Test")
    _git(primary, "config", "user.email", "cmru-gate@example.invalid")
    (primary / "tracked.txt").write_text("intended\n", encoding="utf-8")
    _git(primary, "add", "tracked.txt")
    _git(primary, "commit", "--quiet", "-m", "intended base")
    base = _git(primary, "rev-parse", "HEAD")
    (primary / "second.txt").write_text("intended second\n", encoding="utf-8")
    _git(primary, "add", "second.txt")
    _git(primary, "commit", "--quiet", "-m", "intended head")
    _git(primary, "tag", "intended-v1")
    intended = tmp_path / "intended"
    _git(primary, "worktree", "add", "--quiet", "-b", "gate-source", str(intended), "HEAD")
    (intended / "untracked-note.txt").write_text("not yet committed\n", encoding="utf-8")

    decoy = tmp_path / "decoy"
    decoy.mkdir()
    _git(decoy, "init", "--quiet", "--initial-branch=main")
    _git(decoy, "config", "user.name", "Decoy")
    _git(decoy, "config", "user.email", "decoy@example.invalid")
    (decoy / "decoy-only.txt").write_text("decoy\n", encoding="utf-8")
    _git(decoy, "add", "decoy-only.txt")
    _git(decoy, "commit", "--quiet", "-m", "decoy")
    decoy_head = _git(decoy, "rev-parse", "HEAD")
    _git(decoy, "tag", "decoy-v9")
    _git(decoy, "update-ref", "refs/namespaces/decoyns/refs/heads/gate-source", decoy_head)
    (decoy / "decoy-staged.txt").write_text("staged in the decoy index\n", encoding="utf-8")
    _git(decoy, "add", "decoy-staged.txt")
    (decoy / "ignore-everything").write_text("*\n", encoding="utf-8")
    return SimpleNamespace(
        intended=intended, base=base, head=_git(intended, "rev-parse", "HEAD"),
        tree=_git(intended, "rev-parse", "HEAD^{tree}"), decoy=decoy, decoy_head=decoy_head,
    )


def _point_ambient_git_at(monkeypatch, decoy: Path) -> None:
    """Every repository-location, ref-namespace and config-injection channel aims at the decoy."""
    admin = decoy / ".git"
    for key, value in {
        "GIT_DIR": admin, "GIT_WORK_TREE": decoy, "GIT_COMMON_DIR": admin,
        "GIT_INDEX_FILE": admin / "index", "GIT_OBJECT_DIRECTORY": admin / "objects",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": admin / "objects", "GIT_NAMESPACE": "decoyns",
        # Run-Gate's ownership policy (kept) and an injected status override (dropped).
        "GIT_CONFIG_COUNT": 2,
        "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*",
        "GIT_CONFIG_KEY_1": "status.showUntrackedFiles", "GIT_CONFIG_VALUE_1": "no",
        "GIT_CONFIG_PARAMETERS": f"'core.excludesfile'='{decoy / 'ignore-everything'}'",
    }.items():
        monkeypatch.setenv(key, str(value))


def test_snapshot_git_reads_the_pinned_checkout_under_hostile_ambient_git(tmp_path, monkeypatch):
    repos = _intended_and_decoy(tmp_path)
    _point_ambient_git_at(monkeypatch, repos.decoy)

    def read(*args: str) -> bytes:
        return gs._git(repos.intended, *args).stdout

    # The intended checkout's own commit, tree, index, worktree status and refs.
    assert read("rev-parse", "HEAD").decode().strip() == repos.head
    assert read("rev-parse", "HEAD^{tree}").decode().strip() == repos.tree
    assert read("ls-files") == b"second.txt\ntracked.txt\n"
    assert read("status", "--porcelain=v1") == b"?? untracked-note.txt\n"
    assert read("for-each-ref", "--format=%(refname)").decode().split() == [
        "refs/heads/gate-source", "refs/heads/main", "refs/tags/intended-v1",
    ]
    assert gs._git_is_ancestor(repos.intended, repos.base, repos.head) is True
    # The decoy's object store is not reachable, even through alternates.
    with pytest.raises(RuntimeError, match="git cat-file -e"):
        gs._git(repos.intended, "cat-file", "-e", repos.decoy_head)
    # The ownership policy survives; the injected config does not.
    assert read("config", "--get-all", "safe.directory") == b"*\n"
    with pytest.raises(RuntimeError, match="git config --get status.showUntrackedFiles"):
        gs._git(repos.intended, "config", "--get", "status.showUntrackedFiles")
    # Transport and credential variables are not this policy's business.
    monkeypatch.setenv("GIT_ASKPASS", "/usr/local/bin/askpass")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    environment = gs._snapshot_git_environment(repos.intended)
    assert environment["GIT_ASKPASS"] == "/usr/local/bin/askpass"
    assert environment["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"


def test_lifecycle_git_policy_clears_repository_redirection_and_restores_it(tmp_path, monkeypatch):
    repos = _intended_and_decoy(tmp_path)
    _point_ambient_git_at(monkeypatch, repos.decoy)

    def plain_git(*args: str) -> str:
        # Inherits os.environ, as the worktree lifecycle library's Git calls do.
        return subprocess.run(
            ["git", *args], cwd=repos.intended, capture_output=True, text=True, check=True,
        ).stdout

    with gs.host_git_hooks_disabled(repos.intended):
        assert plain_git("rev-parse", "HEAD").strip() == repos.head
        assert plain_git("ls-files") == "second.txt\ntracked.txt\n"
        assert plain_git("config", "--get-all", "safe.directory") == "*\n"
        assert "GIT_NAMESPACE" not in os.environ
    assert os.environ["GIT_INDEX_FILE"] == str(repos.decoy / ".git" / "index")
    assert os.environ["GIT_NAMESPACE"] == "decoyns"


def test_snapshot_policy_clears_every_repository_variable_the_release_gate_clears(monkeypatch):
    from tests.test_release_gate import run_release_gate

    candidates = (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_ASKPASS",
    )
    for key in candidates:
        monkeypatch.setenv(key, "/hostile")
    hardened = run_release_gate._hardened_git_environment()

    assert {key for key in candidates if key not in hardened} <= set(gs._AMBIENT_REPOSITORY_ENV)
    snapshot = gs._hardened_snapshot_environment()
    assert not set(gs._AMBIENT_REPOSITORY_ENV) & set(snapshot)


@pytest.mark.parametrize(("stdout", "message"), [
    (b"malformed ref record\n", "malformed main/tag ref record"),
    (b"refs/heads/unrelated\0" + b"a" * 40 + b"\n", "out-of-scope"),
    (b"refs/tags/example\0not-an-object-id\n", "invalid object id"),
])
def test_anchor_ref_parser_rejects_malformed_and_out_of_scope_records(
    tmp_path, monkeypatch, stdout, message,
):
    monkeypatch.setattr(gs, "_git", lambda *_args: SimpleNamespace(stdout=stdout))

    with pytest.raises(RuntimeError, match=message):
        gs._anchor_ref_values(tmp_path)


def test_main_ancestry_probe_reports_git_failure_without_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gs, "_linked_git_context", lambda *_args, **_kwargs: (tmp_path, tmp_path),
    )
    monkeypatch.setattr(gs, "_host_git_policy", lambda: nullcontext())
    monkeypatch.setattr(
        gs.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stderr="", stdout=""),
    )

    with pytest.raises(
        RuntimeError,
        match="cannot verify main fast-forward ancestry: no diagnostic output",
    ):
        gs._git_is_ancestor(tmp_path, "a" * 40, "b" * 40)


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


def test_an_existing_snapshot_without_main_and_tag_anchors_refuses_resume(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    del document["anchor_refs"]
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="no trusted main/tag ref baseline"):
        gs.open_gate_snapshot(repo)


def test_an_existing_snapshot_with_malformed_main_tag_anchors_refuses_resume(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = _closed_snapshot(repo)
    manifest = repo / "cmru" / ".assay" / "release-gate-snapshot.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["anchor_refs"] = {"refs/heads/main": "not-an-object-id"}
    manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="invalid main/tag ref anchors"):
        gs.open_gate_snapshot(repo)


def test_a_new_snapshot_requires_local_main_anchor(tmp_path, monkeypatch):
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    _git(repo, "update-ref", "-d", "refs/heads/main")

    with pytest.raises(RuntimeError, match="requires a local refs/heads/main anchor"):
        gs.open_gate_snapshot(repo)
    assert not _expected_target(repo).exists()


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
        # The byte-level comparison with the exact commit runs first and names the file.
        with pytest.raises(
            RuntimeError, match=r"tracked snapshot file differs from exact commit .*README\.md",
        ):
            validate()
        _git(root, "checkout", "--quiet", "--", "README.md")

        (root / "README.md").write_text("hidden tampering\n", encoding="utf-8")
        _git(root, "update-index", "--assume-unchanged", "README.md")
        with pytest.raises(RuntimeError, match="assume-unchanged or skip-worktree"):
            validate()
        _git(root, "update-index", "--no-assume-unchanged", "README.md")
        with pytest.raises(RuntimeError, match="differs from exact commit"):
            gs._verify_tracked_files_match_commit(root, snapshot.commit)
        _git(root, "checkout", "--quiet", "--", "README.md")

        plain = tmp_path / "plain-worktree"
        _git(tmp_path / "primary", "worktree", "add", "--quiet", "-b", "plain", str(plain), "HEAD")
        with pytest.raises(RuntimeError, match="no CMRU workspace record"):
            gs.validate_lane_outputs(plain, snapshot.commit, protected_refs=())
    finally:
        gs.close_gate_snapshot(snapshot)


def test_a_staged_change_hidden_by_restored_bytes_still_fails_the_lane(tmp_path, monkeypatch):
    """The working-tree bytes can equal the judged commit while the INDEX no longer
    does (a lane staged an edit, then wrote the original bytes back). The byte
    proof passes, so only the status scan can refuse the snapshot."""
    repo, *_rest = _fixture(tmp_path, monkeypatch)
    snapshot = gs.open_gate_snapshot(repo)
    root = snapshot.worktree_path
    try:
        original = (root / "README.md").read_bytes()
        (root / "README.md").write_text("staged by a lane\n", encoding="utf-8")
        _git(root, "add", "README.md")
        (root / "README.md").write_bytes(original)
        # The exact-tree proof alone is satisfied: every byte equals the commit.
        gs._verify_tracked_files_match_commit(root, snapshot.commit)

        with pytest.raises(
            RuntimeError, match="a release gate lane changed tracked snapshot files or HEAD",
        ):
            gs.validate_lane_outputs(
                root, snapshot.commit, protected_refs=snapshot.protected_refs,
            )
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
