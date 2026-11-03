from __future__ import annotations

import subprocess
import shutil
from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path

import pytest

from worktree import (
    WorkspaceCollisionError,
    acquire_lease,
    create_workspace,
    ensure_workspace,
    find_workspace,
    list_git_worktrees,
    physical_path,
    read_record,
    remove_orphan_workspace,
    remove_workspace,
    release_lease,
    workspace_id_for_path,
)
import worktree.core as core


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Workspace Test")
    (repo / "README").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_identity_is_six_lowercase_base36(tmp_path: Path) -> None:
    value = workspace_id_for_path(tmp_path / "checkout")
    assert len(value) == 6
    assert value == value.lower()
    assert all(char in "0123456789abcdefghijklmnopqrstuvwxyz" for char in value)


def test_find_workspace_matches_exact_path_and_distinguishes_absence(repository, tmp_path):
    context = create_workspace(
        repository,
        tmp_path / "linked",
        branch="find/by-path",
        base="HEAD",
    )

    record = find_workspace(context.git_common_dir, context.worktree_path)

    assert record is not None
    assert record.context() == context
    assert find_workspace(context.git_common_dir, tmp_path / "not-registered") is None
    alternate_spelling = context.worktree_path.parent / "absent" / ".." / context.worktree_path.name
    assert find_workspace(context.git_common_dir, alternate_spelling) is None
    with pytest.raises(core.WorkspaceError, match="must be absolute"):
        find_workspace(context.git_common_dir, Path("relative/worktree"))
    remove_workspace(context)


def test_find_workspace_refuses_duplicate_record_ownership(monkeypatch, repository, tmp_path):
    context = create_workspace(
        repository,
        tmp_path / "linked",
        branch="find/duplicate",
        base="HEAD",
    )
    record = read_record(context.record_path)

    with monkeypatch.context() as scoped:
        scoped.setattr(core, "list_workspaces", lambda _common: [record, record])
        with pytest.raises(
            core.WorkspaceError, match="multiple shared workspace records"
        ) as exc:
            find_workspace(context.git_common_dir, context.worktree_path)

    assert exc.value.category == "invalid-record"
    remove_workspace(context)


def test_list_workspaces_refuses_duplicate_path_ownership(monkeypatch, repository, tmp_path):
    context = create_workspace(
        repository,
        tmp_path / "linked",
        branch="find/list-duplicate",
        base="HEAD",
    )
    record = read_record(context.record_path)

    with monkeypatch.context() as scoped:
        scoped.setattr(core, "_records", lambda _common: [record, record])
        with pytest.raises(
            core.WorkspaceError, match="multiple shared workspace records"
        ) as exc:
            core.list_workspaces(context.git_common_dir)

    assert exc.value.category == "invalid-record"
    remove_workspace(context)


def test_git_inventory_is_primary_first_and_preserves_opaque_paths(repository, tmp_path):
    odd_path = tmp_path / "linked with space\nand\ttab"
    _git(repository, "worktree", "add", "--detach", str(odd_path), "HEAD")
    _git(repository, "worktree", "lock", "--reason", "inventory test", str(odd_path))
    inventory = list_git_worktrees(repository)

    assert len(inventory) == 2
    assert inventory[0].path == repository
    assert inventory[0].is_primary
    assert inventory[0].branch == "main"
    assert not inventory[0].is_detached
    assert inventory[0].head == _git(repository, "rev-parse", "HEAD")
    assert inventory[1].path == odd_path
    assert not inventory[1].is_primary
    assert inventory[1].branch is None
    assert inventory[1].is_detached
    assert inventory[1].is_locked
    assert inventory[1].head == inventory[0].head
    _git(repository, "worktree", "unlock", str(odd_path))
    _git(repository, "worktree", "remove", "--force", str(odd_path))


def test_git_inventory_uses_git_prunable_fact_without_statting_recorded_path(
    repository, tmp_path, monkeypatch
):
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-b", "inventory/prunable", str(linked))
    shutil.rmtree(linked)
    real_stat = core.os.stat

    def forbid_recorded_path_stat(path, *args, **kwargs):
        if Path(path) == linked:
            pytest.fail(f"inventory must not stat the Git path {path}")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        core.os,
        "stat",
        forbid_recorded_path_stat,
    )
    inventory = list_git_worktrees(repository)
    assert len(inventory) == 2
    assert inventory[1].path == linked
    assert inventory[1].is_prunable
    _git(repository, "worktree", "prune", "--expire", "now")


def test_git_prunable_marker_does_not_mean_checkout_directory_is_missing(repository, tmp_path):
    linked = tmp_path / "linked-with-broken-gitfile"
    _git(repository, "worktree", "add", "-b", "inventory/broken-gitfile", str(linked))
    head = _git(repository, "rev-parse", "inventory/broken-gitfile")

    # Leave the checkout directory in place but break its administrative
    # back-link. Git reports a prunable record even though this path exists.
    (linked / ".git").unlink()
    assert linked.is_dir()

    entry = next(
        item for item in list_git_worktrees(repository)
        if item.path == linked
    )
    assert entry.is_prunable
    assert entry.head == head


def test_git_inventory_marks_bare_repository_without_primary(tmp_path):
    bare = tmp_path / "bare.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-q")

    inventory = list_git_worktrees(bare)

    assert len(inventory) == 1
    assert inventory[0].path == bare
    assert inventory[0].is_bare
    assert not inventory[0].is_primary
    assert inventory[0].head is None


def test_git_inventory_accepts_head_on_bare_repository():
    head = b"a" * 40

    inventory = core._parse_git_worktrees(
        b"worktree /repo.git\0bare\0HEAD " + head + b"\0\0"
    )

    assert inventory[0].is_bare
    assert inventory[0].head == head.decode()


def test_git_inventory_accepts_a_file_inside_the_worktree(repository):
    assert list_git_worktrees(repository / "README")[0].path == repository


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"", "empty or unterminated"),
        (b"HEAD " + b"a" * 40 + b"\0\0", "before its worktree path"),
        (b"worktree\0\0", "malformed worktree path field"),
        (b"worktree relative\0bare\0\0", "non-absolute path"),
        (
            b"worktree /repo\0worktree /second\0\0",
            "malformed worktree path field",
        ),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0HEAD " + b"a" * 40 + b"\0\0",
            "duplicate 'HEAD' field",
        ),
        (b"worktree /repo\0HEAD bad\0branch refs/heads/main\0\0", "invalid HEAD"),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0branch refs/heads/\0\0",
            "invalid branch",
        ),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0branch other\0\0",
            "invalid branch",
        ),
        (
            b"worktree /repo\0HEAD " + b"a" * 40 + b"\0branch refs/heads/main\0detached\0\0",
            "inconsistent branch state",
        ),
        (b"worktree /repo\0bare\0detached\0\0", "invalid bare-worktree record"),
        (b"worktree /repo.git\0bare\0HEAD bad\0\0", "invalid HEAD for bare repository"),
        (b"\0\0", "incomplete or empty inventory"),
    ],
)
def test_git_inventory_rejects_malformed_porcelain(payload, message):
    with pytest.raises(core.WorkspaceError, match=message):
        core._parse_git_worktrees(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b"worktree\0\0",
        b"worktree \0\0",
    ],
)
def test_git_inventory_rejects_each_malformed_path_predicate(payload):
    with pytest.raises(core.WorkspaceError, match="malformed worktree path field"):
        core._parse_git_worktrees(payload)


def test_git_inventory_rejects_each_invalid_bare_record_axis():
    head = b"a" * 40
    normal = b"worktree /repo\0HEAD " + head + b"\0branch refs/heads/main\0\0"
    second_bare = normal + b"worktree /bare.git\0bare\0\0"
    with pytest.raises(core.WorkspaceError, match="invalid bare-worktree record"):
        core._parse_git_worktrees(second_bare)

    with pytest.raises(core.WorkspaceError, match="invalid bare-worktree record"):
        core._parse_git_worktrees(b"worktree /bare.git\0bare\0detached\0\0")


def test_git_inventory_tracks_bare_record_state():
    head = b"a" * 40
    payload = b"worktree /first.git\0bare\0HEAD " + head + b"\0\0"
    inventory = core._parse_git_worktrees(payload)
    assert inventory[0].is_bare is True


def test_git_inventory_requires_record_terminator():
    with pytest.raises(core.WorkspaceError, match="empty or unterminated"):
        core._parse_git_worktrees(b"worktree /repo\0")


def test_git_inventory_surfaces_git_and_startup_failures(monkeypatch, tmp_path):
    calls = []

    def failing_run(*args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(
            args[0], 128, stdout=b"", stderr=b"not a repository"
        )

    monkeypatch.setattr(
        core.subprocess,
        "run", failing_run,
    )
    with pytest.raises(core.WorkspaceError, match="not a repository"):
        list_git_worktrees(tmp_path)
    assert calls[0]["check"] is False

    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("git missing")),
    )
    with pytest.raises(core.WorkspaceError, match="could not start.*git missing"):
        list_git_worktrees(tmp_path)


def test_physical_translation_does_not_require_target_namespace(tmp_path: Path) -> None:
    translated = physical_path(
        "/logical/repo/nested", logical_root="/logical/repo", physical_root="/host/repo"
    )
    assert translated == Path("/host/repo/nested")


def test_allocate_resume_lease_and_remove(repository: Path, tmp_path: Path) -> None:
    target = tmp_path / "checkout"
    context = create_workspace(
        repository,
        target,
        branch="workspace/test",
        purpose="test",
    )
    record = read_record(context.record_path)
    with pytest.raises(FrozenInstanceError):
        record.branch = "mutated"
    assert record.workspace_id == context.workspace_id
    assert ensure_workspace(record).worktree_path == target.resolve()

    leased = acquire_lease(record, holder="pytest", ttl=timedelta(minutes=5))
    assert leased.lease is not None
    assert release_lease(leased).lease is None

    remove_workspace(context)
    assert not target.exists()
    assert not context.record_path.exists()


def test_remove_orphan_workspace_uses_another_checkout_in_the_same_git_family(
    repository: Path, tmp_path: Path,
) -> None:
    source = tmp_path / "source-worktree"
    _git(repository, "worktree", "add", "--quiet", "-b", "orphan-source", str(source), "HEAD")
    target = tmp_path / "orphan-target"
    context = create_workspace(
        source,
        target,
        branch="workspace/orphan-target",
        purpose="test-orphan-cleanup",
    )
    record = read_record(context.record_path)

    _git(repository, "worktree", "remove", "--force", str(source))
    remove_orphan_workspace(record, executor_git_root=repository)

    assert not target.exists()
    assert not context.record_path.exists()
    branch = subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/workspace/orphan-target"],
        cwd=repository, capture_output=True, text=True, check=False,
    )
    # `show-ref --verify` exits 1 on older Git and 128 on newer: only "not a valid ref" matters.
    assert branch.returncode != 0 and "not a valid ref" in branch.stderr


def test_remove_orphan_workspace_refuses_when_source_exists_or_lease_is_live(
    repository: Path, tmp_path: Path,
) -> None:
    source = tmp_path / "source-worktree"
    _git(repository, "worktree", "add", "--quiet", "-b", "live-source", str(source), "HEAD")
    target = tmp_path / "orphan-target"
    context = create_workspace(
        source,
        target,
        branch="workspace/live-orphan-target",
        purpose="test-orphan-cleanup",
    )
    with pytest.raises(core.WorkspaceError, match="recorded source checkout still exists"):
        remove_orphan_workspace(context, executor_git_root=repository)

    _git(repository, "worktree", "remove", "--force", str(source))
    leased = acquire_lease(context, holder="live-owner", ttl=timedelta(minutes=5))
    with pytest.raises(core.WorkspaceError, match="active lease"):
        remove_orphan_workspace(leased, executor_git_root=repository)
    assert target.is_dir()
    release_lease(leased)
    remove_orphan_workspace(context, executor_git_root=repository)


def _orphan_target(repository: Path, tmp_path: Path, name: str):
    source = tmp_path / f"{name}-source"
    _git(repository, "worktree", "add", "--quiet", "-b", f"{name}-source", str(source), "HEAD")
    context = create_workspace(
        source, tmp_path / f"{name}-target", branch=f"workspace/{name}",
        purpose="test-orphan-cleanup",
    )
    return source, context


def test_remove_orphan_workspace_removes_when_git_reports_source_prunable(
    repository: Path, tmp_path: Path,
) -> None:
    source, context = _orphan_target(repository, tmp_path, "prunable")
    shutil.rmtree(source)  # still registered, but Git reports it prunable

    remove_orphan_workspace(context, executor_git_root=repository)

    assert not context.worktree_path.exists()
    assert not context.record_path.exists()


def _gone_source(repository: Path, tmp_path: Path, name: str):
    source, context = _orphan_target(repository, tmp_path, name)
    _git(repository, "worktree", "remove", "--force", str(source))
    return context


def test_remove_orphan_workspace_refuses_an_unusable_or_foreign_executor(
    repository: Path, tmp_path: Path,
) -> None:
    context = _gone_source(repository, tmp_path, "executor")
    with pytest.raises(core.WorkspaceError, match="executor is not a directory"):
        remove_orphan_workspace(context, executor_git_root=tmp_path / "absent")
    other = tmp_path / "other-family"
    other.mkdir()
    _git(other, "init", "-b", "main")
    _git(
        other, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
        "commit", "--allow-empty", "-m", "other family",
    )
    with pytest.raises(core.WorkspaceError, match="different Git family"):
        remove_orphan_workspace(context, executor_git_root=other)
    assert context.worktree_path.is_dir() and context.record_path.exists()


def test_remove_orphan_workspace_refuses_a_missing_or_mismatched_checkout(
    repository: Path, tmp_path: Path,
) -> None:
    context = _gone_source(repository, tmp_path, "mismatch")
    record = read_record(context.record_path)
    core.write_record(core.replace(record, branch="workspace/not-this-one"))
    with pytest.raises(core.WorkspaceError, match="does not match record"):
        remove_orphan_workspace(context, executor_git_root=repository)
    core.write_record(record)
    shutil.rmtree(context.worktree_path)
    with pytest.raises(core.WorkspaceError, match="checkout is missing"):
        remove_orphan_workspace(context, executor_git_root=repository)


def test_remove_orphan_workspace_reports_failed_git_removal_and_branch_deletion(
    repository: Path, tmp_path: Path,
) -> None:
    locked = _gone_source(repository, tmp_path, "locked-target")
    _git(repository, "worktree", "lock", str(locked.worktree_path))
    with pytest.raises(
        core.WorkspaceError, match=r"git worktree remove failed \(\d+\): (fatal|error): .*locked working tree",
    ):
        remove_orphan_workspace(locked, executor_git_root=repository)
    assert locked.worktree_path.is_dir()

    stuck = _gone_source(repository, tmp_path, "stuck-branch")
    lock_file = repository / ".git" / "refs" / "heads" / "workspace" / "stuck-branch.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("", encoding="utf-8")
    with pytest.raises(
        core.WorkspaceError,
        match=r"git branch -D workspace/stuck-branch failed \(\d+\): (fatal|error): .*(cannot lock ref|Unable to create)",
    ):
        remove_orphan_workspace(stuck, executor_git_root=repository)


def test_remove_orphan_workspace_refuses_each_independent_checkout_mismatch(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _gone_source(repository, tmp_path, "independent")
    real = core.discover_git_context
    wrong_common = tmp_path / "somewhere-else" / ".git"

    def skewed(field: str):
        def discover(path):
            top, common, branch, head = real(path)
            if core.canonical_path(path) == context.worktree_path:
                if field == "top":
                    top = top / "elsewhere"
                elif field == "common":
                    common = wrong_common
                else:
                    branch = "another-branch"
            return top, common, branch, head
        return discover

    for field in ("top", "common", "branch"):
        monkeypatch.setattr(core, "discover_git_context", skewed(field))
        with pytest.raises(core.WorkspaceError, match="does not match record"):
            remove_orphan_workspace(context, executor_git_root=repository)
        assert context.worktree_path.is_dir()


def test_remove_orphan_workspace_refuses_a_source_git_still_treats_as_live(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _gone_source(repository, tmp_path, "live-entry")
    record = read_record(context.record_path)
    real = core.list_git_worktrees

    def listed(locked: bool, prunable: bool):
        def inventory(path):
            entries = real(path)
            entry = core.GitWorktree(
                path=record.source_git_root, head="0" * 40, branch="x",
                is_primary=False, is_prunable=prunable, is_locked=locked,
            )
            return [*entries, entry]
        return inventory

    # Registered, unlocked, and NOT prunable (a live checkout we merely cannot see),
    # and registered AND locked even though Git also reports it prunable: both refuse.
    for locked, prunable in ((False, False), (True, True)):
        monkeypatch.setattr(core, "list_git_worktrees", listed(locked, prunable))
        with pytest.raises(core.WorkspaceError, match="still registered with Git"):
            remove_orphan_workspace(context, executor_git_root=repository)
    monkeypatch.setattr(core, "list_git_worktrees", listed(False, True))
    remove_orphan_workspace(context, executor_git_root=repository)  # unlocked + prunable: removed
    assert not context.worktree_path.exists()


def test_remove_orphan_workspace_can_keep_the_branch(repository: Path, tmp_path: Path) -> None:
    context = _gone_source(repository, tmp_path, "keep-branch")
    remove_orphan_workspace(context, executor_git_root=repository, delete_branch=False)
    assert not context.worktree_path.exists()
    assert _git(repository, "branch", "--list", "workspace/keep-branch")


def test_remove_orphan_workspace_refuses_a_locked_registered_source(
    repository: Path, tmp_path: Path,
) -> None:
    source, context = _orphan_target(repository, tmp_path, "locked")
    _git(repository, "worktree", "lock", str(source))
    shutil.rmtree(source)

    with pytest.raises(core.WorkspaceError, match="still registered with Git"):
        remove_orphan_workspace(context, executor_git_root=repository)

    assert context.worktree_path.is_dir()
    assert context.record_path.exists()


def test_explicit_identity_path_is_durable_and_rechecked(repository: Path, tmp_path: Path) -> None:
    target = tmp_path / "checkout"
    identity_path = tmp_path / "allocation-seed"
    context = create_workspace(
        repository,
        target,
        branch="workspace/explicit-identity",
        purpose="test",
        identity_path=identity_path,
    )
    record = read_record(context.record_path)
    original_metadata = dict(record.metadata)
    assert context.workspace_id == workspace_id_for_path(identity_path)
    assert record.metadata["workspace.identity_path"] == str(identity_path.resolve())
    assert ensure_workspace(record).workspace_id == context.workspace_id

    updated = ensure_workspace(record, metadata={"adapter": "value"})
    assert updated.workspace_id == context.workspace_id
    assert read_record(context.record_path).metadata["workspace.identity_path"] == str(
        identity_path.resolve()
    )

    with pytest.raises(core.WorkspaceError, match="cannot replace durable"):
        ensure_workspace(
            record,
            metadata={"workspace.identity_path": str(tmp_path / "different-seed")},
        )

    record.metadata["workspace.identity_path"] = str(tmp_path / "different-seed")
    core.write_record(record)
    with pytest.raises(core.WorkspaceError, match="identity does not match"):
        ensure_workspace(record)
    core.write_record(core.replace(record, metadata=original_metadata))
    remove_workspace(context)


@pytest.mark.parametrize(
    "identity_path",
    [None, "", 7, "relative/seed", "/tmp/seed/../canonicalized"],
    ids=("null", "empty", "wrong-type", "relative", "not-normalized"),
)
def test_malformed_persisted_identity_refuses_resume_inspect_and_remove(
    repository: Path, tmp_path: Path, identity_path
) -> None:
    context = create_workspace(
        repository, tmp_path / "checkout", branch="workspace/bad-identity", purpose="test"
    )
    record = read_record(context.record_path)
    malformed_metadata = dict(record.metadata)
    malformed_metadata["workspace.identity_path"] = identity_path
    malformed = core.replace(record, metadata=malformed_metadata)
    core.write_record(malformed)
    cleanup_calls = []

    for operation in (
        lambda: ensure_workspace(context),
        lambda: core.inspect_workspace(context),
        lambda: remove_workspace(
            context,
            cleanup=lambda _context: cleanup_calls.append("ran"),
            force=True,
        ),
    ):
        with pytest.raises(core.WorkspaceError, match="workspace.identity_path"):
            operation()
    assert cleanup_calls == []
    assert context.worktree_path.is_dir()
    core.write_record(core.replace(record, metadata=dict(record.metadata)))
    remove_workspace(context, force=True)


def test_identity_collision_names_both_paths(repository: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(core, "workspace_id_for_path", lambda _path: "abc123")
    first = create_workspace(repository, tmp_path / "one", branch="one", purpose="test")
    with pytest.raises(WorkspaceCollisionError) as raised:
        create_workspace(repository, tmp_path / "two", branch="two", purpose="test")
    assert "one" in str(raised.value)
    assert "two" in str(raised.value)
    remove_workspace(first)


# --- WT-OW-01: refuse removal when a mount sits at or under the checkout ---


def _mountinfo_row(mount_point: str, mount_id: int = 100) -> str:
    return (
        f"{mount_id} 29 0:{mount_id} / {mount_point} rw,relatime shared:1 - "
        "btrfs /dev/vda1 rw,subvol=/vm-lab"
    )


def _mountinfo(*points: str) -> str:
    rows = ["22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw"]
    rows.extend(_mountinfo_row(point, 100 + index) for index, point in enumerate(points))
    return "\n".join(rows) + "\n"


def _linked(repository: Path, tmp_path: Path, name: str = "linked"):
    return create_workspace(
        repository, tmp_path / name, branch=f"mounts/{name}", base="HEAD",
        purpose="test-mounts",
    )


def test_mounts_under_reports_nested_and_exact_mounts_only(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    (root / "artifacts" / "vm-lab").mkdir(parents=True)
    sibling = tmp_path / "checkout-sibling"
    table = _mountinfo(
        str(root / "artifacts" / "vm-lab"),
        str(root),
        str(sibling),  # shares a string prefix but is NOT under root
        str(tmp_path),  # parent mount: not under root
    )
    assert core.mounts_under(root, mountinfo_reader=lambda: table) == [
        root, root / "artifacts" / "vm-lab",
    ]
    assert core.mounts_under(root, mountinfo_reader=lambda: _mountinfo()) == []


def test_mounts_under_decodes_octal_escapes_in_mount_points(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    spaced = root / "dir with space" / "tab\there" / "back\\slash"
    escaped = (
        str(root) + "/dir\\040with\\040space/tab\\011here/back\\134slash"
    )
    table = _mountinfo(escaped)
    assert core.mounts_under(root, mountinfo_reader=lambda: table) == [spaced]


def test_mounts_under_resolves_symlinked_worktree_path(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real)
    table = _mountinfo(str(real / "sub"))
    assert core.mounts_under(link, mountinfo_reader=lambda: table) == [real / "sub"]


def test_mounts_under_fails_closed_on_unreadable_or_malformed_table(tmp_path: Path) -> None:
    def unreadable() -> str:
        raise PermissionError("denied")

    with pytest.raises(core.WorkspaceError, match="could not read the mount table"):
        core.mounts_under(tmp_path, mountinfo_reader=unreadable)
    with pytest.raises(core.WorkspaceError, match="malformed mount table row"):
        core.mounts_under(tmp_path, mountinfo_reader=lambda: "1 2 3\n")


def test_mounts_under_accepts_a_row_with_exactly_the_five_leading_fields(tmp_path: Path) -> None:
    """Boundary: mount point is field five; a row ending there is still a row."""
    root = tmp_path / "checkout"
    table = f"1 2 3:4 / {root}/inner\n"
    assert core.mounts_under(root, mountinfo_reader=lambda: table) == [root / "inner"]
    with pytest.raises(core.WorkspaceError, match="malformed mount table row"):
        core.mounts_under(root, mountinfo_reader=lambda: "1 2 3:4 /\n")


def test_mounts_under_reads_the_real_mount_table_by_default(tmp_path: Path) -> None:
    assert core.mounts_under(tmp_path / "definitely-not-a-mount") == []
    # The real table always has "/" and never reports it as under a subpath.
    assert Path("/") not in core.mounts_under(tmp_path)


def test_remove_workspace_refuses_mount_before_cleanup_even_with_force(
    repository: Path, tmp_path: Path,
) -> None:
    context = _linked(repository, tmp_path)
    mount = context.worktree_path / "artifacts" / "vm-lab"
    mount.mkdir(parents=True)
    (mount / "disk.img").write_text("precious", encoding="utf-8")
    table = _mountinfo(str(mount))
    cleaned: list[object] = []
    phases: list[tuple[str, str]] = []

    for force in (False, True):
        with pytest.raises(core.WorkspaceError, match="detach it first") as caught:
            remove_workspace(
                context, force=force, cleanup=cleaned.append,
                on_phase=lambda phase, status: phases.append((phase, status)),
                mountinfo_reader=lambda: table,
            )
        assert caught.value.category == "cleanup-refusal"
        assert str(mount) in str(caught.value)

    assert cleaned == [] and phases == []  # nothing ran, no phase event
    assert (mount / "disk.img").read_text(encoding="utf-8") == "precious"
    assert context.record_path.exists()
    assert context.worktree_path.is_dir()


def test_remove_workspace_names_every_mount(repository: Path, tmp_path: Path) -> None:
    context = _linked(repository, tmp_path)
    first = context.worktree_path / "a"
    second = context.worktree_path / "sub" / "b"
    table = _mountinfo(str(first), str(second))
    with pytest.raises(core.WorkspaceError) as caught:
        remove_workspace(context, mountinfo_reader=lambda: table)
    assert str(first) in str(caught.value) and str(second) in str(caught.value)
    assert "2 mount(s)" in str(caught.value)


def test_remove_workspace_passes_when_mounts_are_elsewhere(
    repository: Path, tmp_path: Path,
) -> None:
    context = _linked(repository, tmp_path)
    table = _mountinfo(str(tmp_path / "elsewhere"), str(tmp_path))
    remove_workspace(context, mountinfo_reader=lambda: table)
    assert not context.worktree_path.exists()
    assert not context.record_path.exists()


def test_remove_unrecorded_workspace_forwards_the_mount_refusal(
    repository: Path, tmp_path: Path,
) -> None:
    target = tmp_path / "legacy"
    _git(repository, "worktree", "add", "--quiet", "-b", "legacy-mounts", str(target), "HEAD")
    table = _mountinfo(str(target / "vol"))
    with pytest.raises(core.WorkspaceError, match="detach it first"):
        core.remove_unrecorded_workspace(
            repository, target, force=True, mountinfo_reader=lambda: table,
        )
    assert target.is_dir()


def test_remove_orphan_workspace_refuses_mount_even_with_force(
    repository: Path, tmp_path: Path,
) -> None:
    source = tmp_path / "source-worktree"
    _git(repository, "worktree", "add", "--quiet", "-b", "mnt-source", str(source), "HEAD")
    target = tmp_path / "orphan-target"
    context = create_workspace(
        source, target, branch="workspace/mnt-orphan", purpose="test-orphan-cleanup",
    )
    record = read_record(context.record_path)
    _git(repository, "worktree", "remove", "--force", str(source))
    table = _mountinfo(str(target / "mnt"))
    with pytest.raises(core.WorkspaceError, match="detach it first"):
        remove_orphan_workspace(
            record, executor_git_root=repository, force=True,
            mountinfo_reader=lambda: table,
        )
    assert target.is_dir() and context.record_path.exists()
    remove_orphan_workspace(record, executor_git_root=repository)
    assert not target.exists()


def _late_mount_reader(mount: Path, armed: list[bool]):
    """Mount table that shows ``mount`` only once ``armed`` is truthy."""

    def reader() -> str:
        return _mountinfo(str(mount)) if armed else _mountinfo()

    return reader


def test_remove_workspace_rechecks_mounts_right_before_the_git_deletion(
    repository: Path, tmp_path: Path,
) -> None:
    context = _linked(repository, tmp_path, "late")
    mount = context.worktree_path / "vol"
    mount.mkdir()
    armed: list[bool] = []
    reader = _late_mount_reader(mount, armed)
    # The mount appears while the adapter cleanup callback runs.
    with pytest.raises(core.WorkspaceError, match="detach it first"):
        remove_workspace(
            context, cleanup=lambda _ctx: armed.append(True), mountinfo_reader=reader,
        )
    assert armed == [True]
    assert context.worktree_path.is_dir() and context.record_path.exists()


def test_permission_retry_rechecks_mounts_before_git_retry(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _linked(repository, tmp_path, "retry")
    mount = context.worktree_path / "vol"
    mount.mkdir()
    armed: list[bool] = []
    original = core.subprocess.run
    removes: list[list[str]] = []

    def deny_remove(argv, *args, **kwargs):
        if argv[:3] == ["git", "worktree", "remove"]:
            removes.append(argv)
            return subprocess.CompletedProcess(argv, 1, "", "Permission denied")
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", deny_remove)
    with pytest.raises(core.WorkspaceError, match="detach it first"):
        remove_workspace(
            context,
            checkout_recovery=lambda *_args: armed.append(True) or True,
            mountinfo_reader=_late_mount_reader(mount, armed),
        )
    assert len(removes) == 1  # the retry never ran
    assert context.worktree_path.is_dir() and context.record_path.exists()


def test_residual_rmtree_rechecks_mounts_before_deleting(
    repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _linked(repository, tmp_path, "residual")
    record = read_record(context.record_path)
    admin = core._workspace_admin_snapshot(record)[0]
    mount = context.worktree_path / "vol"
    mount.mkdir()
    (mount / "disk.img").write_text("precious", encoding="utf-8")
    armed: list[bool] = []
    original = core.subprocess.run
    calls: list[list[str]] = []

    def unregister_and_fail(argv, *args, **kwargs):
        if argv[:3] == ["git", "worktree", "remove"] and not calls:
            calls.append(argv)
            shutil.rmtree(admin)
            return subprocess.CompletedProcess(argv, 1, "", "Permission denied")
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", unregister_and_fail)
    with pytest.raises(core.WorkspaceError, match="detach it first"):
        remove_workspace(
            context,
            checkout_recovery=lambda *_args: armed.append(True) or True,
            mountinfo_reader=_late_mount_reader(mount, armed),
        )
    assert (mount / "disk.img").read_text(encoding="utf-8") == "precious"
