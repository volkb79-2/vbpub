"""Behavioral tests for the S16 Git-worktree lifecycle substrate.

These tests use real Git worktrees in a temporary repository while replacing
CIU environment generation and cleanup. They exercise checkout creation,
lookup, clean-before-remove ordering, and exact target-environment handling
without Docker, a network, or the wall clock.
"""
from __future__ import annotations

import builtins
import errno
import hashlib
import io
import json
import os
import queue
import shutil
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, worktree, workspace_env  # noqa: E402
from ciu.config_constants import GLOBAL_CONFIG_DEFAULTS, GLOBAL_CONFIG_RENDERED  # noqa: E402

_REAL_DOCKER_NETWORK_EXISTS = worktree._docker_network_exists


def create_path(*args, **kwargs):
    """Adapt path-oriented setup to CIU's typed ``create`` API."""
    return worktree.create(*args, **kwargs).git_worktree_path


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    # Every per-checkout CIU artifact is gitignored (S3.1b): the generated
    # facts file is the one CIU reads (CIU-75), `ciu.env` its legacy export,
    # and the instance overlay the operator's own sparse override.
    (repo / ".gitignore").write_text(
        "ciu.env\nciu.global.instance.toml.j2\nciu.instance.generated.toml\n",
        encoding="utf-8",
    )
    (repo / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    assert _git(["add", "README.md", ".gitignore", "ciu.global.defaults.toml.j2"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


@pytest.fixture
def rootless_repo(tmp_path: Path) -> Path:
    """A Git family that has no CIU root marker in any committed tree."""
    repo = tmp_path / "rootless"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("generic Git family\n", encoding="utf-8")
    assert _git(["add", "README.md"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _instance_id_for(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:6]


class TestInitialCheckoutResetSafety:
    @staticmethod
    def _record(path: Path) -> worktree.WorktreeInstanceRecord:
        return worktree.WorktreeInstanceRecord(
            logical_name="demo",
            display_name="demo",
            branch="demo",
            git_worktree_path=path.resolve(),
            ciu_root_offset=Path("."),
            created_at_utc="2026-10-14T00:00:00Z",
            base_ref="main",
            state="allocating",
        )

    def test_status_inspection_failure_refuses_checkout_reset(self, tmp_path, monkeypatch):
        def fail_status(args, _cwd):
            assert args[0] == "status"
            return subprocess.CompletedProcess(args, 1, "", "status unavailable")

        monkeypatch.setattr(worktree, "_git", fail_status)
        with pytest.raises(worktree.WorktreeError, match="status unavailable"):
            worktree._assert_allocation_checkout_reset_is_safe(self._record(tmp_path))

    def test_tree_inspection_failure_refuses_checkout_reset(self, tmp_path, monkeypatch):
        def fail_tree(args, _cwd):
            if args[0] == "status":
                return subprocess.CompletedProcess(args, 0, "D  tracked.txt\0", "")
            assert args[:2] == ["ls-tree", "-r"]
            return subprocess.CompletedProcess(args, 1, "", "tree unavailable")

        monkeypatch.setattr(worktree, "_git", fail_tree)
        with pytest.raises(worktree.WorktreeError, match="tree unavailable"):
            worktree._assert_allocation_checkout_reset_is_safe(self._record(tmp_path))

    def test_ignored_file_at_a_tracked_path_refuses_checkout_reset(
        self, rootless_repo, tmp_path,
    ):
        checkout = tmp_path / "ignored-overlay"
        _git(["worktree", "add", "--no-checkout", "-b", "ignored-overlay",
              str(checkout), "main"], rootless_repo)
        excludes = tmp_path / "global-excludes"
        excludes.write_text("README.md\n", encoding="utf-8")
        _git(["config", "core.excludesFile", str(excludes)], rootless_repo)
        (checkout / "README.md").write_text("operator data\n", encoding="utf-8")

        with pytest.raises(worktree.WorktreeError, match="README.md"):
            worktree._assert_allocation_checkout_reset_is_safe(
                self._record(checkout)
            )

    def test_unexpected_files_are_reported_with_a_bounded_status_sample(
        self, tmp_path, monkeypatch
    ):
        record = self._record(tmp_path)
        record_path = record.record_path.relative_to(record.git_worktree_path).as_posix()
        tracked_paths = [f"tracked-{index}.txt" for index in range(5)]
        status_entries = [
            f"?? {record_path}",
            "?? operator.txt",
            *(f"D  {path}" for path in tracked_paths),
        ]
        status_output = "\0".join(status_entries) + "\0"
        tree_output = "\0".join(tracked_paths) + "\0"

        def fake_git(args, _cwd):
            if args[0] == "status":
                return subprocess.CompletedProcess(args, 0, status_output, "")
            assert args[:2] == ["ls-tree", "-r"]
            return subprocess.CompletedProcess(args, 0, tree_output, "")

        monkeypatch.setattr(worktree, "_git", fake_git)
        with pytest.raises(worktree.WorktreeError, match="operator.txt.*7 total entries"):
            worktree._assert_allocation_checkout_reset_is_safe(record)

    def test_short_unexpected_status_sample_omits_total_count(
        self, tmp_path, monkeypatch
    ):
        record = self._record(tmp_path)
        record_path = record.record_path.relative_to(record.git_worktree_path).as_posix()
        status_output = "\0".join([
            f"?? {record_path}", "?? operator.txt", "D  tracked.txt",
        ]) + "\0"

        def fake_git(args, _cwd):
            if args[0] == "status":
                return subprocess.CompletedProcess(args, 0, status_output, "")
            assert args[:2] == ["ls-tree", "-r"]
            return subprocess.CompletedProcess(args, 0, "tracked.txt\0", "")

        monkeypatch.setattr(worktree, "_git", fake_git)
        with pytest.raises(worktree.WorktreeError, match="operator.txt") as raised:
            worktree._assert_allocation_checkout_reset_is_safe(record)
        assert "total entries" not in str(raised.value)

    def test_record_rejects_overlay_intent_below_schema_three(self, tmp_path):
        record = replace(self._record(tmp_path), schema_version=2, overlay_intent_known=True)
        with pytest.raises(ValueError, match="requires worktree record schema version 3"):
            record.to_dict()

    def test_record_rejects_pending_marker_below_schema_four(self, tmp_path):
        record = replace(
            self._record(tmp_path), schema_version=3,
            overlay_intent_known=True, initial_overlay_pending=True,
        )
        with pytest.raises(ValueError, match="requires worktree record schema version 4"):
            record.to_dict()

    def test_record_rejects_reset_phase_below_schema_five(self, tmp_path):
        record = replace(
            self._record(tmp_path), schema_version=4,
            checkout_reset_pending=True,
        )
        with pytest.raises(ValueError, match="schema version 5"):
            record.to_dict()

    @pytest.mark.parametrize(
        ("updates", "message"),
        [
            (
                {"schema_version": 4, "initial_overlay": "payload"},
                "unknown initial overlay intent cannot carry a payload",
            ),
            (
                {
                    "schema_version": 4,
                    "initial_overlay_pending": True,
                    "adopted": False,
                },
                "pending initial overlay recovery requires known intent",
            ),
            (
                {
                    "schema_version": 4,
                    "state": "ready",
                    "overlay_intent_known": True,
                    "initial_overlay_pending": True,
                    "adopted": False,
                },
                "ready worktree record cannot have pending overlay recovery",
            ),
        ],
    )
    def test_record_writer_refuses_inconsistent_v4_overlay_state(
        self, tmp_path, updates, message
    ):
        record = replace(self._record(tmp_path), **updates)
        with pytest.raises(ValueError, match=message):
            record.to_dict()

    def test_overlay_writer_accepts_known_no_overlay_intent(self, tmp_path):
        worktree._write_rendered_worktree_overlay(tmp_path, None)
        assert not (tmp_path / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES).exists()

    def test_overlay_resume_rechecks_an_identical_file_from_a_publish_race(
        self, tmp_path, monkeypatch
    ):
        payload = "[ciu.instance]\nservice_profiles = [\"core\"]\n"
        record = replace(
            self._record(tmp_path), schema_version=3,
            initial_overlay=payload, overlay_intent_known=True,
        )
        overlay = record.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES

        def raced_publish(_root, contents):
            overlay.write_text(contents, encoding="utf-8")
            raise worktree.WorktreeError("publish lost race")

        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", raced_publish)
        worktree._ensure_initial_overlay(record)
        assert overlay.read_text(encoding="utf-8") == payload

    def test_overlay_resume_refuses_a_symlink_created_during_publish_race(
        self, tmp_path, monkeypatch
    ):
        payload = "[ciu.instance]\nservice_profiles = [\"core\"]\n"
        record = replace(
            self._record(tmp_path), schema_version=3,
            initial_overlay=payload, overlay_intent_known=True,
        )
        overlay = record.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        target = tmp_path / "operator-overlay"
        target.write_text(payload, encoding="utf-8")

        def raced_symlink(_root, _contents):
            overlay.symlink_to(target)
            raise worktree.WorktreeError("publish lost race")

        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", raced_symlink)
        with pytest.raises(worktree.WorktreeError, match="is a symlink"):
            worktree._ensure_initial_overlay(record)

    def test_overlay_resume_reports_raced_file_that_cannot_be_opened(
        self, tmp_path, monkeypatch
    ):
        payload = "[ciu.instance]\nservice_profiles = [\"core\"]\n"
        record = replace(
            self._record(tmp_path), schema_version=3,
            initial_overlay=payload, overlay_intent_known=True,
        )
        overlay = record.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        real_open = worktree.os.open

        def deny_raced_open(path, flags):
            if Path(path) == overlay and overlay.exists():
                raise PermissionError("read denied")
            return real_open(path, flags)

        def raced_publish(_root, contents):
            overlay.write_text(contents, encoding="utf-8")
            raise worktree.WorktreeError("publish lost race")

        monkeypatch.setattr(worktree.os, "open", deny_raced_open)
        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", raced_publish)
        with pytest.raises(worktree.WorktreeError, match="cannot verify.*read denied"):
            worktree._ensure_initial_overlay(record)

    def test_overlay_resume_reports_initial_open_failure(self, tmp_path, monkeypatch):
        record = replace(
            self._record(tmp_path), schema_version=3,
            initial_overlay="payload", overlay_intent_known=True,
        )
        monkeypatch.setattr(
            worktree.os, "open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="cannot verify.*denied"):
            worktree._ensure_initial_overlay(record)

    def test_overlay_resume_reports_file_read_failure(self, tmp_path, monkeypatch):
        record = replace(
            self._record(tmp_path), schema_version=3,
            initial_overlay="payload", overlay_intent_known=True,
        )
        overlay = record.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        overlay.write_text("payload", encoding="utf-8")
        monkeypatch.setattr(
            worktree.os, "fdopen",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("read failed")),
        )
        with pytest.raises(worktree.WorktreeError, match="cannot verify.*read failed"):
            worktree._ensure_initial_overlay(record)

    def test_overlay_resume_refuses_records_without_known_intent(self, tmp_path):
        record = replace(self._record(tmp_path), schema_version=3)
        with pytest.raises(worktree.WorktreeError, match="predates durable overlay intent"):
            worktree._ensure_initial_overlay(record)

    def test_overlay_writer_reports_failure_before_temp_file_creation(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            worktree.tempfile, "NamedTemporaryFile",
            lambda **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not write.*denied"):
            worktree._write_rendered_worktree_overlay(tmp_path, "payload")


class _FlushRecordingStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1
        return super().flush()


@pytest.fixture
def fake_generate_env(monkeypatch, write_instance_facts):
    """Stand in for `ciu env generate` — writing BOTH of its outputs.

    CIU-75: the real verb still writes `ciu.env` (a legacy write-only export)
    AND writes `[ciu.instance.generated]` — into its own
    `ciu.instance.generated.toml` since ciu-P47 — and only the SECOND is what
    CIU reads back. A fake that wrote only the first would let every test here
    pass against a product that no longer works.
    """
    def fake(
        path: Path,
        *,
        identity_only: bool = False,
        allocation_lock_held: bool = False,
    ) -> int:
        instance_id = _instance_id_for(path)
        (path / "ciu.env").write_text(
            f'export INSTANCE_ID="{instance_id}"\n'
            f'export DOCKER_NETWORK_INTERNAL="repo-{instance_id}-network"\n',
            encoding="utf-8",
        )
        write_instance_facts(
            path,
            instance_id=instance_id,
            network=f"repo-{instance_id}-network",
            repo_root=str(path),
            physical_repo_root=str(path),
            repo_name="repo",
        )
        return 0

    monkeypatch.setattr(worktree, "_generate_env_in", fake)
    monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
    return fake


class TestCreateRemoveList:
    def test_create_creates_worktree_on_new_branch(self, tmp_repo, fake_generate_env):
        target = create_path(tmp_repo, "feature-x", base="main")
        assert target == tmp_repo / ".worktrees" / "feature-x"
        assert target.is_dir()
        assert (target / "ciu.env").is_file()

    def test_create_rejects_invalid_names(self, tmp_repo):
        for name in ("a/b", ".hidden", ""):
            with pytest.raises(worktree.WorktreeError):
                create_path(tmp_repo, name)

    def test_create_rejects_existing_target(self, tmp_repo, fake_generate_env):
        create_path(tmp_repo, "dup", base="main")
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            create_path(tmp_repo, "dup", base="main")

    def test_create_profile_writes_durable_instance_override(self, tmp_repo, fake_generate_env):
        target = create_path(tmp_repo, "wt", base="main", profile="core,db")
        overlay_path = target / "ciu.global.instance.toml.j2"
        overlay = overlay_path.read_text(encoding="utf-8")
        assert 'service_profiles = ["core", "db"]' in overlay
        assert overlay_path.stat().st_mode & 0o777 == 0o644
        assert "CIU_SERVICES_PROFILE" not in (target / "ciu.env").read_text(encoding="utf-8")

    def test_empty_profile_refuses_before_checkout_or_exclude_mutation(self, tmp_repo):
        common = worktree._git_common_dir(tmp_repo)
        exclude = common / "info" / "exclude"
        before = exclude.read_bytes() if exclude.exists() else None

        with pytest.raises(worktree.WorktreeError, match="non-empty comma-separated"):
            worktree.create(tmp_repo, "empty-profile", profile="")

        assert worktree.find_instance_record(tmp_repo, "empty-profile") is None
        assert worktree.find_worktree(tmp_repo, "empty-profile") is None
        assert (exclude.read_bytes() if exclude.exists() else None) == before

    def test_create_generate_env_failure_raises(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(worktree, "_generate_env_in", lambda path, **_kw: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu env generate"):
            create_path(tmp_repo, "wt", base="main")

    def test_create_refuses_unresolved_base_before_allocation_mutation(self, tmp_repo):
        common = worktree._git_common_dir(tmp_repo)
        exclude = common / "info" / "exclude"
        before = exclude.read_bytes() if exclude.exists() else None

        with pytest.raises(worktree.WorktreeError, match="cannot verify selected base"):
            create_path(tmp_repo, "wt", base="missing-base")

        assert worktree.find_instance_record(tmp_repo, "wt") is None
        assert worktree.find_worktree(tmp_repo, "wt") is None
        assert (exclude.read_bytes() if exclude.exists() else None) == before

    def test_list_worktrees_shows_primary_and_added(self, tmp_repo, fake_generate_env):
        create_path(tmp_repo, "wt1", base="main")
        infos = worktree.list_worktrees(tmp_repo)
        assert any(info.is_primary for info in infos)
        assert any(not info.is_primary and info.path.name == "wt1" for info in infos)

    def test_list_worktrees_preserves_git_detached_state(self, tmp_path, monkeypatch):
        shared = worktree._shared_worktree()
        output = [
            shared.GitWorktree(
                path=tmp_path / "primary", head="1" * 40, branch="main",
                is_primary=True,
            ),
            shared.GitWorktree(
                path=tmp_path / "detached", head="2" * 40, branch=None,
                is_primary=False, is_detached=True,
            ),
        ]
        monkeypatch.setattr(
            shared,
            "list_git_worktrees",
            lambda *_args: output,
        )
        infos = worktree.list_worktrees(tmp_path)
        assert [item.branch for item in infos] == ["main", "(detached)"]
        assert [item.head for item in infos] == ["11111111", "22222222"]
        assert [item.is_primary for item in infos] == [True, False]

    def test_list_worktrees_surfaces_git_failure(self, tmp_path, monkeypatch):
        shared = worktree._shared_worktree()
        monkeypatch.setattr(
            shared,
            "list_git_worktrees",
            lambda *_args: (_ for _ in ()).throw(
                shared.WorkspaceError("no repository", category="git-error")
            ),
        )
        with pytest.raises(worktree.WorktreeError, match="no repository"):
            worktree.list_worktrees(tmp_path)

    def test_find_worktree_by_name(self, tmp_repo, fake_generate_env):
        create_path(tmp_repo, "wt1", base="main")
        found = worktree.find_worktree(tmp_repo, "wt1")
        assert found is not None and found.path.name == "wt1"
        assert worktree.find_worktree(tmp_repo, "nope") is None

    def test_remove_unknown_name_raises(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="no worktree named"):
            worktree.remove(tmp_repo, "nope")

    def test_remove_primary_refused(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="PRIMARY"):
            worktree.remove(tmp_repo, tmp_repo.name)

    def test_remove_cleans_before_git_remove(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        target = create_path(tmp_repo, "wt1", base="main")
        calls: list[str] = []
        monkeypatch.setattr(
            worktree, "_clean_in", lambda wt, *, yes: calls.append("clean") or 0
        )
        removed = worktree.remove(tmp_repo, "wt1", force=True)
        assert removed == target
        assert calls == ["clean"]
        assert not target.exists()

    def test_remove_failed_clean_aborts_without_force(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        target = create_path(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu clean.*failed"):
            worktree.remove(tmp_repo, "wt1", force=False)
        assert target.exists()

    def test_remove_failed_clean_force_proceeds_silently(
        self, tmp_repo, fake_generate_env, monkeypatch, capsys
    ):
        create_path(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        worktree.remove(tmp_repo, "wt1", force=True)
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("force", [False, True])
    def test_remove_refuses_mount_under_checkout_before_any_cleanup(
        self, tmp_repo, fake_generate_env, monkeypatch, force
    ):
        """WT-OW-01 through ciu's own surface: a mount under the checkout
        (e.g. a CoW subvolume holding VM disks) refuses removal, even with
        force, before `ciu clean` or any deletion runs."""
        target = create_path(tmp_repo, "wt1", base="main")
        mount = target / "artifacts" / "vm-lab"
        mount.mkdir(parents=True)
        (mount / "disk.img").write_text("precious", encoding="utf-8")
        shared_core = worktree._shared_worktree().core
        table = (
            "22 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
            f"101 22 0:50 / {mount} rw - btrfs /dev/vda1 rw,subvol=/vm-lab\n"
        )
        monkeypatch.setattr(shared_core, "_read_mountinfo", lambda: table)
        calls: list[str] = []
        monkeypatch.setattr(
            worktree, "_clean_in", lambda wt, *, yes: calls.append("clean") or 0
        )
        with pytest.raises(worktree.WorktreeError, match="detach it first") as caught:
            worktree.remove(tmp_repo, "wt1", force=force)
        assert str(mount) in str(caught.value)
        assert calls == []
        assert (mount / "disk.img").read_text(encoding="utf-8") == "precious"
        assert target.exists()

    def test_remove_reports_mount_probe_failure_as_lifecycle_preflight(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """A failing mount probe refuses removal in the lifecycle_preflight phase
        before any cleanup runs."""
        target = create_path(tmp_repo, "wt1", base="main")
        shared = worktree._shared_worktree()
        monkeypatch.setattr(
            shared,
            "mounts_under",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                shared.WorkspaceError("mountinfo unreadable", category="git-error")
            ),
        )
        calls: list[str] = []
        monkeypatch.setattr(
            worktree, "_clean_in", lambda wt, *, yes: calls.append("clean") or 0
        )
        with pytest.raises(worktree.WorktreeError, match="mountinfo unreadable"):
            worktree.remove(tmp_repo, "wt1")
        assert calls == []
        assert target.exists()

    def test_remove_surfaces_git_remove_failure_after_clean(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        create_path(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kw: 0)
        shared = worktree._shared_worktree()
        monkeypatch.setattr(
            shared,
            "remove_workspace",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                shared.WorkspaceError("checkout locked", category="git-error")
            ),
        )
        with pytest.raises(worktree.WorktreeError, match="checkout locked"):
            worktree.remove(tmp_repo, "wt1")


class TestManagedIdentityLifecycle:
    FIXED_NOW = datetime(2026, 8, 17, 12, 34, 56, tzinfo=timezone.utc)

    def test_create_persists_atomic_ready_record(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "logical-one", base="main")
        stored = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert stored["schema_version"] == 6
        assert stored["base_branch_ref"] == "main"
        assert stored["adopted"] is False
        assert stored["checkout_reset_pending"] is False
        assert stored["network_id"] is None
        assert stored["logical_name"] == "logical-one"
        assert stored["state"] == "ready"
        assert stored["runtime"]["instance_id"] == record.instance_id
        assert "head" not in stored
        assert "password" not in record.record_path.read_text(encoding="utf-8").lower()

    def test_record_writer_syncs_parent_directory_after_replace(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "durable-record", base="main")
        directory = record.record_path.parent
        events = []
        real_open = os.open
        real_fsync = os.fsync
        real_replace = os.replace

        def tracked_open(path, flags, *args, **kwargs):
            fd = real_open(path, flags, *args, **kwargs)
            if Path(path) == directory:
                events.append(("directory-open", fd))
            return fd

        def tracked_fsync(fd):
            kind = "directory-fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file-fsync"
            events.append((kind, fd))
            return real_fsync(fd)

        def tracked_replace(source, destination):
            result = real_replace(source, destination)
            events.append(("replace", str(destination)))
            return result

        monkeypatch.setattr(worktree.os, "open", tracked_open)
        monkeypatch.setattr(worktree.os, "fsync", tracked_fsync)
        monkeypatch.setattr(worktree.os, "replace", tracked_replace)
        worktree._write_instance_record(record)

        labels = [event[0] for event in events]
        assert labels.index("file-fsync") < labels.index("replace")
        assert labels.index("replace") < labels.index("directory-open")
        assert labels.index("directory-open") < labels.index("directory-fsync")

    def test_parent_directory_sync_failure_reports_already_replaced_record(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "directory-sync-fails")
        real_fsync = os.fsync

        def fail_directory_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("directory sync denied")
            return real_fsync(fd)

        monkeypatch.setattr(worktree.os, "fsync", fail_directory_sync)
        with pytest.raises(
            worktree.WorktreeError, match="was replaced.*durability is uncertain"
        ):
            worktree._write_instance_record(record)

        assert worktree.read_instance_record(record.record_path) == record

    def test_lease_update_cannot_restore_allocating_snapshot_after_ready(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        original = worktree.create(tmp_repo, "lease-transition", profile="core")
        allocating = replace(
            original,
            state="allocating",
            initial_overlay_pending=True,
            network_id=None,
        )
        worktree._write_instance_record(allocating)
        ready = replace(
            allocating,
            state="ready",
            initial_overlay_pending=False,
            network_id="a" * 64,
        )

        ready_write_started = threading.Event()
        allow_ready_write = threading.Event()
        ready_written = threading.Event()
        lease_started = threading.Event()
        observed = queue.Queue()
        failures = []
        real_lock = worktree._allocation_lock
        real_find = worktree.find_instance_record
        real_write = worktree._write_instance_record

        @contextmanager
        def tracked_lock(root):
            if threading.current_thread().name == "lease-updater":
                observed.put("lease-lock")
            with real_lock(root):
                yield

        def tracked_find(root, name, **kwargs):
            record = real_find(root, name, **kwargs)
            if threading.current_thread().name == "lease-updater":
                observed.put(("lease-read", record.state if record else None))
            return record

        def tracked_write(candidate):
            thread_name = threading.current_thread().name
            if thread_name == "allocator" and candidate.state == "ready":
                ready_write_started.set()
                if not allow_ready_write.wait(timeout=5):
                    raise AssertionError("test did not release ready publication")
                result = real_write(candidate)
                ready_written.set()
                return result
            if thread_name == "lease-updater" and candidate.lease is not None:
                if not ready_written.wait(timeout=5):
                    raise AssertionError("lease write did not follow ready publication")
            return real_write(candidate)

        monkeypatch.setattr(worktree, "_allocation_lock", tracked_lock)
        monkeypatch.setattr(worktree, "find_instance_record", tracked_find)
        monkeypatch.setattr(worktree, "_write_instance_record", tracked_write)

        def publish_ready():
            try:
                with worktree._allocation_lock(tmp_repo):
                    worktree._write_instance_record(ready)
            except BaseException as exc:  # surface worker failures in the test thread
                failures.append(exc)

        def update_lease():
            lease_started.set()
            try:
                worktree.apply_lease(
                    tmp_repo, "lease-transition", extend="1h"
                )
            except BaseException as exc:
                failures.append(exc)

        allocator = threading.Thread(target=publish_ready, name="allocator")
        allocator.start()
        assert ready_write_started.wait(timeout=5)
        lease_thread = threading.Thread(target=update_lease, name="lease-updater")
        lease_thread.start()
        assert lease_started.wait(timeout=5)
        first_lease_action = observed.get(timeout=5)
        allow_ready_write.set()
        allocator.join(timeout=5)
        lease_thread.join(timeout=5)

        assert not allocator.is_alive() and not lease_thread.is_alive()
        assert not failures
        assert first_lease_action == "lease-lock"
        final = worktree.read_instance_record(original.record_path)
        assert final.state == "ready"
        assert final.initial_overlay_pending is False
        assert final.network_id == "a" * 64
        assert final.lease is not None

    def test_own_lease_entrypoints_use_the_allocation_lock(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "own-lease-lock")
        lock_roots = []
        real_lock = worktree._allocation_lock

        @contextmanager
        def tracked_lock(root):
            lock_roots.append(Path(root).resolve())
            with real_lock(root):
                yield

        monkeypatch.setattr(worktree, "_allocation_lock", tracked_lock)
        assert worktree.acquire_own_lease(
            record.ciu_root, ttl_hours=1
        ).lease is not None
        assert worktree.release_own_lease(record.ciu_root).lease is None
        assert lock_roots == [record.ciu_root.resolve(), record.ciu_root.resolve()]

    def test_generated_same_second_collision_suffixes_only_second(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_utc_now", lambda: self.FIXED_NOW)
        first = worktree.create(
            tmp_repo, "logical-one", prefix="ciu", feature="exact-exec"
        )
        second = worktree.create(
            tmp_repo, "logical-two", prefix="ciu", feature="exact-exec"
        )
        assert first.display_name == "ciu-20260817_123456-exact-exec"
        assert second.display_name == "ciu-20260817_123456-exact-exec-2"
        assert first.branch == first.git_worktree_path.name == first.display_name
        assert second.branch == second.git_worktree_path.name == second.display_name

    @pytest.mark.parametrize("explicit", ["branch", "path"])
    def test_generated_create_checks_explicit_target_against_corrupt_sibling(
        self, tmp_repo, fake_generate_env, monkeypatch, explicit,
    ):
        monkeypatch.setattr(worktree, "_utc_now", lambda: self.FIXED_NOW)
        first = worktree.create(
            tmp_repo, "first-generated", prefix="ciu", feature="collision"
        )
        raw = json.loads(first.record_path.read_text(encoding="utf-8"))
        raw.pop("branch")
        first.record_path.write_text(json.dumps(raw), encoding="utf-8")

        target_value = (
            first.branch if explicit == "branch" else first.git_worktree_path
        )
        kwargs = {explicit: target_value}
        with pytest.raises(worktree.WorktreeError, match="requested checkout path"):
            worktree.create(
                tmp_repo, "second-generated", prefix="ciu", feature="collision",
                **kwargs,
            )

    def test_ready_ensure_has_no_side_effects(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        created = worktree.create(tmp_repo, "logical-one")
        before = created.record_path.read_bytes()
        monkeypatch.setattr(
            worktree, "_generate_env_in",
            lambda _path, **_kw: (_ for _ in ()).throw(AssertionError("must not regenerate")),
        )
        ensured = worktree.ensure(tmp_repo, "logical-one")
        assert ensured == created
        assert created.record_path.read_bytes() == before

    def test_ready_ensure_does_not_restore_an_operator_deleted_overlay(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "overlay-ready-missing", profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        overlay.unlink()

        ensured = worktree.ensure(tmp_repo, "overlay-ready-missing")

        assert ensured.state == "ready"
        assert not overlay.exists()

    def test_ready_ensure_preserves_operator_edits_to_the_overlay(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "overlay-ready-edited", profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        edited = b'# operator edit\r\n[ciu.instance]\r\nservice_profiles = ["db"]\r\n'
        overlay.write_bytes(edited)

        ensured = worktree.ensure(tmp_repo, "overlay-ready-edited")

        assert ensured.state == "ready"
        assert overlay.read_bytes() == edited

    def test_ready_root_repair_preserves_operator_overlay_edits(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "overlay-ready-root-repair", profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        edited = b'# operator edit\n[ciu.instance]\nservice_profiles = ["db"]\n'
        overlay.write_bytes(edited)
        generated = created.ciu_root / "ciu.instance.generated.toml"
        generated.unlink()

        ensured = worktree.ensure(tmp_repo, "overlay-ready-root-repair")

        assert ensured.state == "ready"
        assert generated.is_file()
        assert overlay.read_bytes() == edited

    @pytest.mark.parametrize("operator_state", ["edited", "deleted"])
    def test_interrupted_ready_root_repair_preserves_overlay_and_live_network(
        self, tmp_repo, fake_generate_env, monkeypatch, operator_state
    ):
        logical_name = f"overlay-ready-repair-{operator_state}"
        created = worktree.create(tmp_repo, logical_name, profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        edited = b'# operator edit\n[ciu.instance]\nservice_profiles = ["db"]\n'
        if operator_state == "edited":
            overlay.write_bytes(edited)
        else:
            overlay.unlink()
        generated = created.ciu_root / "ciu.instance.generated.toml"
        generated.unlink()

        record = worktree.read_instance_record(created.record_path)
        assert record.state == "ready"
        assert record.initial_overlay_pending is False
        monkeypatch.setattr(
            worktree,
            "_docker_network_details",
            lambda _network: {
                "id": "a" * 64,
                "labels": {worktree._NETWORK_OWNER_LABEL: created.instance_id},
            },
        )
        real_finish = worktree._finish_allocation
        monkeypatch.setattr(
            worktree,
            "_finish_allocation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt("simulated interruption during root repair")
            ),
        )
        with pytest.raises(KeyboardInterrupt, match="during root repair"):
            worktree.ensure(tmp_repo, logical_name)

        interrupted = worktree.read_instance_record(created.record_path)
        assert interrupted.state == "allocating"
        assert interrupted.initial_overlay_pending is False
        monkeypatch.setattr(worktree, "_finish_allocation", real_finish)

        repaired = worktree.ensure(tmp_repo, logical_name)

        assert repaired.state == "ready"
        assert generated.is_file()
        if operator_state == "edited":
            assert overlay.read_bytes() == edited
        else:
            assert not overlay.exists()

    def test_partial_ensure_refuses_crlf_normalized_overlay_bytes(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "overlay-ready-crlf", profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        expected = created.initial_overlay.encode("utf-8")
        crlf = expected.replace(b"\n", b"\r\n")
        overlay.write_bytes(crlf)
        partial = replace(
            created,
            state="recovery-required",
            recovery_status="env-generation-failed",
            initial_overlay_pending=True,
        )
        worktree._write_instance_record(partial)

        with pytest.raises(
            worktree.WorktreeError, match="differs from the recorded allocation intent"
        ):
            worktree.ensure(tmp_repo, "overlay-ready-crlf")

        assert overlay.read_bytes() == crlf

    def test_partial_ensure_refuses_symlink_overlay_without_following_it(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "overlay-ready-symlink", profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        target = tmp_repo / "operator-overlay.toml"
        target.write_text("operator-owned\n", encoding="utf-8")
        overlay.unlink()
        overlay.symlink_to(target)
        partial = replace(
            created,
            state="recovery-required",
            recovery_status="env-generation-failed",
            initial_overlay_pending=True,
        )
        worktree._write_instance_record(partial)

        with pytest.raises(worktree.WorktreeError, match="is a symlink"):
            worktree.ensure(tmp_repo, "overlay-ready-symlink")

        assert overlay.is_symlink()
        assert target.read_text(encoding="utf-8") == "operator-owned\n"

    def test_partial_ensure_refuses_nonregular_overlay_without_replacing_it(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "overlay-ready-directory", profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        overlay.unlink()
        overlay.mkdir()
        partial = replace(
            created,
            state="recovery-required",
            recovery_status="env-generation-failed",
            initial_overlay_pending=True,
        )
        worktree._write_instance_record(partial)

        with pytest.raises(worktree.WorktreeError, match="not a regular file"):
            worktree.ensure(tmp_repo, "overlay-ready-directory")

        assert overlay.is_dir()

    def test_ready_v2_repair_resumes_after_interruption_without_overlay_intent(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        created = worktree.create(tmp_repo, "legacy-ready")
        legacy = replace(
            created, base_branch_ref=None, schema_version=2, initial_overlay=None,
            overlay_intent_known=False,
            checkout_reset_pending=None, network_id=None,
        )
        worktree._write_instance_record(legacy)
        generated = legacy.ciu_root / "ciu.instance.generated.toml"
        generated.unlink()
        monkeypatch.setattr(
            worktree,
            "_docker_network_details",
            lambda _network: {
                "id": "a" * 64,
                "labels": {worktree._NETWORK_OWNER_LABEL: created.instance_id},
            },
        )
        real_finish = worktree._finish_allocation
        monkeypatch.setattr(
            worktree,
            "_finish_allocation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt("simulated interruption during legacy repair")
            ),
        )

        with pytest.raises(KeyboardInterrupt, match="during legacy repair"):
            worktree.ensure(tmp_repo, "legacy-ready")

        interrupted = worktree.read_instance_record(legacy.record_path)
        assert interrupted.state == "allocating"
        assert interrupted.schema_version == 5
        assert interrupted.base_branch_ref is None
        assert interrupted.overlay_intent_known is False
        assert interrupted.initial_overlay_pending is False
        assert interrupted.adopted is None
        monkeypatch.setattr(worktree, "_finish_allocation", real_finish)

        ensured = worktree.ensure(tmp_repo, "legacy-ready")

        assert ensured.state == "ready"
        assert ensured.schema_version == 5
        assert ensured.base_branch_ref is None
        assert ensured.overlay_intent_known is False
        assert generated.is_file()

    def test_ensure_refuses_incomplete_v3_record_with_ambiguous_recovery_state(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "legacy-v3-partial", profile="core")
        legacy = replace(
            created,
            base_branch_ref=None, schema_version=3,
            state="allocating",
            recovery_status=None,
            checkout_reset_pending=None,
            network_id=None,
        )
        worktree._write_instance_record(legacy)
        before = legacy.record_path.read_bytes()

        with pytest.raises(worktree.WorktreeError, match="schema-v3 allocation"):
            worktree.ensure(tmp_repo, "legacy-v3-partial")

        assert legacy.record_path.read_bytes() == before
        overlay = legacy.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        assert overlay.read_text(encoding="utf-8") == created.initial_overlay

    def test_ensure_repairs_ready_record_while_its_network_exists(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        created = worktree.create(tmp_repo, "logical-one", profile="core")
        overlay = created.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        original_overlay = overlay.read_bytes()
        legacy_v3 = replace(
            created, base_branch_ref=None, schema_version=3, initial_overlay_pending=False,
            checkout_reset_pending=None, network_id=None,
        )
        worktree._write_instance_record(legacy_v3)
        context = worktree._ensure_shared_record(tmp_repo, created)
        shared = worktree._shared_worktree()
        shared_record = shared.read_record(context.record_path)
        shared.write_record(replace(
            shared_record,
            metadata={**dict(shared_record.metadata), "root_entries": []},
        ))
        facts = created.ciu_root / "ciu.instance.generated.toml"
        facts.unlink()

        monkeypatch.setattr(
            worktree,
            "_docker_network_details",
            lambda network: (
                {"id": "a" * 64, "labels": {
                    worktree._NETWORK_OWNER_LABEL: created.instance_id
                }} if network == created.network else None
            ),
        )

        repaired = worktree.ensure(tmp_repo, "logical-one")

        assert repaired.state == "ready"
        # The migration upgrades to v5 only and never invents a branch.
        assert repaired.schema_version == 5
        assert repaired.base_branch_ref is None
        assert repaired.instance_id == created.instance_id
        assert repaired.network == created.network
        assert facts.is_file()
        assert overlay.read_bytes() == original_overlay
        assert shared.read_record(context.record_path).metadata["root_entries"]

    def test_ensure_constraint_mismatch_fails_closed(self, tmp_repo, fake_generate_env):
        worktree.create(tmp_repo, "logical-one", display_name="visible-one")
        with pytest.raises(worktree.WorktreeError, match="ensure mismatch"):
            worktree.ensure(tmp_repo, "logical-one", display_name="different")

    def test_ensure_refuses_explicit_overlay_options_for_existing_worktree(
        self, tmp_repo, fake_generate_env
    ):
        worktree.create(tmp_repo, "logical-one", profile="core")

        with pytest.raises(
            worktree.WorktreeError,
            match="initial overlay options are not reapplied",
        ):
            worktree.ensure(tmp_repo, "logical-one", profile="db")

    def test_env_failure_is_inspectable_and_ensure_resumes(
        self, tmp_repo, fake_generate_env, monkeypatch, write_instance_facts
    ):
        monkeypatch.setattr(worktree, "_generate_env_in", lambda _path, **_kw: 7)
        with pytest.raises(worktree.WorktreeError, match="env generate.*failed"):
            worktree.create(tmp_repo, "logical-one", profile="core,db")
        failed = worktree.find_instance_record(tmp_repo, "logical-one")
        assert failed is not None
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "env-generation-failed"
        assert (failed.ciu_root / "ciu.global.instance.toml.j2").is_file()

        def recover(path: Path, **_kw) -> int:
            write_instance_facts(
                path,
                instance_id="recover1",
                network="repo-recover1-network",
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", recover)
        ready = worktree.ensure(tmp_repo, "logical-one")
        assert ready.state == "ready"
        assert ready.instance_id == "recover1"

    def test_runtime_collision_never_marks_second_ready(
        self, tmp_repo, monkeypatch, write_instance_facts
    ):
        def collide(path: Path, **_kw) -> int:
            write_instance_facts(path, instance_id="same-id", network="same-network")
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", collide)
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        worktree.create(tmp_repo, "logical-one")
        with pytest.raises(
            worktree.RuntimeNetworkOwnershipError, match="already belongs"
        ):
            worktree.create(tmp_repo, "logical-two")
        failed = worktree.find_instance_record(tmp_repo, "logical-two")
        assert failed is not None
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "runtime-collision"

    def test_runtime_registry_failure_is_not_classified_as_collision(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_scan = worktree._scan_instance_records
        scan_calls = 0

        def unavailable(_root, **_kwargs):
            nonlocal scan_calls
            scan_calls += 1
            if scan_calls == 2:
                raise worktree.WorktreeError("Git worktree inventory unavailable")
            return real_scan(_root)

        monkeypatch.setattr(worktree, "_scan_instance_records", unavailable)
        with pytest.raises(
            worktree.RuntimeNetworkVerificationError,
            match="identity uniqueness.*inventory unavailable",
        ):
            worktree.create(tmp_repo, "registry-unavailable")

        assert scan_calls == 2
        monkeypatch.setattr(worktree, "_scan_instance_records", real_scan)
        failed = worktree.find_instance_record(tmp_repo, "registry-unavailable")
        assert failed is not None
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "runtime-verification-failed"

    def test_unreadable_ciu_root_sibling_blocks_runtime_identity_admission(
        self, tmp_repo, fake_generate_env
    ):
        damaged = worktree.create(tmp_repo, "damaged-ciu-sibling")
        damaged.record_path.write_text('{"logical_name":"damaged-ciu-sibling",\n', encoding="utf-8")
        (damaged.ciu_root / worktree.GLOBAL_CONFIG_DEFAULTS).unlink()

        with pytest.raises(
            worktree.RuntimeNetworkVerificationError,
            match="cannot verify runtime identity uniqueness.*damaged-ciu-sibling",
        ):
            worktree.create(tmp_repo, "new-runtime-instance")

        partial = worktree.find_instance_record(tmp_repo, "new-runtime-instance")
        assert partial is not None
        assert partial.state == "recovery-required"
        assert partial.recovery_status == "runtime-verification-failed"

    def test_missing_checkout_record_recovers_the_checkpointed_initial_overlay(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_write = worktree._write_instance_record

        def interrupt_record_write(_record):
            raise KeyboardInterrupt("stop between shared and CIU record writes")

        monkeypatch.setattr(worktree, "_write_instance_record", interrupt_record_write)
        with pytest.raises(KeyboardInterrupt, match="between shared and CIU"):
            worktree.create(tmp_repo, "record-gap-recovery", profile="core,db")

        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, "record-gap-recovery"
        )
        assert shared_record is not None
        recovered_intent = shared_record.metadata[worktree._CIU_WORKTREE_RECORD_METADATA]
        assert recovered_intent["initial_overlay_pending"] is True
        assert recovered_intent["initial_overlay"] is not None

        monkeypatch.setattr(worktree, "_write_instance_record", real_write)
        ready = worktree.ensure(tmp_repo, "record-gap-recovery")

        assert ready.state == "ready"
        assert ready.initial_overlay_pending is False
        assert (
            ready.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        ).read_text(encoding="utf-8") == recovered_intent["initial_overlay"]

    def test_missing_ready_record_refuses_direct_up_then_restores_from_shared_snapshot(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "missing-ready-record", profile="core")
        expected_overlay = (
            record.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        ).read_bytes()
        record.record_path.unlink()
        monkeypatch.setattr(
            worktree,
            "_docker_network_details",
            lambda _network: pytest.fail("missing record must refuse before Docker"),
        )

        with pytest.raises(
            worktree.RuntimeNetworkVerificationError,
            match="has no CIU instance record.*worktree ensure",
        ):
            worktree.verify_own_runtime_network(
                record.ciu_root,
                expected_instance_id=record.instance_id,
                expected_network=record.network,
            )

        monkeypatch.setattr(worktree, "_docker_network_details", lambda _network: None)
        restored = worktree.ensure(tmp_repo, "missing-ready-record")
        assert restored.state == "ready"
        assert restored.initial_overlay is None
        assert restored.initial_overlay_pending is False
        assert (
            restored.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        ).read_bytes() == expected_overlay

    def test_runtime_network_probe_failure_has_recoverable_status(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        def unavailable(_network):
            raise worktree.RuntimeNetworkProbeError("docker permission denied")

        monkeypatch.setattr(worktree, "_docker_network_details", unavailable)
        with pytest.raises(
            worktree.RuntimeNetworkProbeError, match="permission denied"
        ):
            worktree.create(tmp_repo, "network-probe-unavailable")

        failed = worktree.find_instance_record(tmp_repo, "network-probe-unavailable")
        assert failed is not None
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "runtime-verification-failed"

    def test_network_collision_is_independent_of_instance_id(
        self, tmp_repo, monkeypatch, write_instance_facts
    ):
        current = {"id": "one"}

        def collide_network(path: Path, **_kw) -> int:
            write_instance_facts(
                path, instance_id=current["id"], network="same-network"
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", collide_network)
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        worktree.create(tmp_repo, "logical-one")
        current["id"] = "two"
        with pytest.raises(worktree.WorktreeError, match="runtime network"):
            worktree.create(tmp_repo, "logical-two")

    def test_adopt_is_only_path_for_existing_unmanaged_checkout(
        self, tmp_repo, fake_generate_env
    ):
        target = tmp_repo / ".worktrees" / "existing"
        assert _git(["worktree", "add", "-b", "existing", str(target), "main"], tmp_repo).returncode == 0
        with pytest.raises(worktree.WorktreeError, match="occupied"):
            worktree.create(tmp_repo, "logical-one", display_name="existing")
        adopted = worktree.adopt(tmp_repo, "logical-one", str(target))
        assert adopted.state == "ready"
        assert adopted.branch == "existing"
        assert adopted.git_worktree_path == target.resolve()
        shared = worktree._shared_worktree()
        common = worktree._git_common_dir(tmp_repo)
        shared_records = shared.list_workspaces(common)
        assert [record.worktree_path for record in shared_records] == [target.resolve()]

    def test_nested_ciu_root_keeps_exact_offset(
        self, tmp_path, monkeypatch, write_instance_facts
    ):
        git_root = tmp_path / "mono"
        ciu_root = git_root / "component"
        ciu_root.mkdir(parents=True)
        assert _git(["init", "-b", "main"], git_root).returncode == 0
        assert _git(["config", "user.email", "t@example.com"], git_root).returncode == 0
        assert _git(["config", "user.name", "Test"], git_root).returncode == 0
        (ciu_root / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
        (git_root / ".gitignore").write_text(
            "ciu.env\nciu.global.instance.toml.j2\nciu.instance.generated.toml\n"
        )
        assert _git(["add", "."], git_root).returncode == 0
        assert _git(["commit", "-m", "init"], git_root).returncode == 0

        def nested_env(path: Path, **_kw) -> int:
            assert path.name == "component"
            write_instance_facts(
                path,
                instance_id="nested1",
                network="mono-nested1-network",
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", nested_env)
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        record = worktree.create(ciu_root, "nested", profile="core")
        assert record.git_worktree_path == git_root / ".worktrees" / "nested"
        assert record.ciu_root_offset == Path("component")
        assert record.record_path == record.git_worktree_path / "component" / worktree.WORKTREE_INSTANCE_RECORD
        assert (record.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES).is_file()

    def test_overlay_refuses_a_root_not_committed_at_the_selected_base(self, rootless_repo):
        with pytest.raises(worktree.WorktreeError, match="not a committed CIU root"):
            worktree.create(rootless_repo, "profiled-rootless", profile="core")
        assert worktree.find_instance_record(rootless_repo, "profiled-rootless") is None
        assert worktree.find_worktree(rootless_repo, "profiled-rootless") is None

    def test_ensure_absent_creates_and_generated_constraints_are_stable(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_utc_now", lambda: self.FIXED_NOW)
        created = worktree.ensure(
            tmp_repo, "logical-one", prefix="ciu", feature="resume"
        )
        ensured = worktree.ensure(
            tmp_repo, "logical-one", prefix="ciu", feature="resume"
        )
        assert ensured == created
        with pytest.raises(worktree.WorktreeError, match="generated-name mismatch"):
            worktree.ensure(
                tmp_repo, "logical-one", prefix="other", feature="resume"
            )
        with pytest.raises(worktree.WorktreeError, match="supplied together"):
            worktree.ensure(tmp_repo, "logical-one", prefix="ciu")

    def test_ensure_checks_branch_and_relative_or_absolute_paths(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(
            tmp_repo, "logical-one", branch="branch-one",
            path=Path(".worktrees/custom-path"), display_name="visible",
        )
        assert worktree.ensure(
            tmp_repo, "logical-one", branch="branch-one",
            path=Path(".worktrees/custom-path"),
        ) == record
        assert worktree.ensure(
            tmp_repo, "logical-one", path=record.git_worktree_path,
        ) == record
        with pytest.raises(worktree.WorktreeError, match="ensure mismatch for branch"):
            worktree.ensure(tmp_repo, "logical-one", branch="wrong")

    def test_create_validates_identity_option_groups_before_side_effects(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="supplied together"):
            worktree.create(tmp_repo, "logical", prefix="ciu")
        with pytest.raises(worktree.WorktreeError, match="conflicts"):
            worktree.create(
                tmp_repo, "logical", display_name="shown", prefix="ciu", feature="feat"
            )
        with pytest.raises(worktree.WorktreeError, match="cannot be empty"):
            worktree.create(tmp_repo, "logical", branch="")

    def test_create_invalid_overlay_refuses_before_any_allocation_side_effect(
        self, tmp_repo
    ):
        common = worktree._git_common_dir(tmp_repo)
        exclude = common / "info" / "exclude"
        exclude_before = exclude.read_bytes() if exclude.exists() else None
        shared = worktree._shared_worktree()

        with pytest.raises(worktree.WorktreeError, match="blank items"):
            worktree.create(tmp_repo, "invalid-overlay", profile="core,,db")

        assert not (tmp_repo / ".worktrees" / "invalid-overlay").exists()
        assert not worktree._branch_exists(tmp_repo, "invalid-overlay")
        assert worktree.find_instance_record(tmp_repo, "invalid-overlay") is None
        assert shared.list_workspaces(common) == []
        assert (exclude.read_bytes() if exclude.exists() else None) == exclude_before

    def test_create_resume_restores_exact_recorded_overlay_after_interruption(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_writer = worktree._write_rendered_worktree_overlay

        def interrupt_before_overlay(*_args, **_kwargs):
            raise KeyboardInterrupt("simulated interruption")

        monkeypatch.setattr(
            worktree, "_write_rendered_worktree_overlay", interrupt_before_overlay
        )
        with pytest.raises(KeyboardInterrupt, match="simulated interruption"):
            worktree.create(tmp_repo, "overlay-resume", profile="core,db")

        partial = worktree.find_instance_record(tmp_repo, "overlay-resume")
        assert partial is not None and partial.state == "allocating"
        assert partial.overlay_intent_known is True
        assert partial.initial_overlay is not None
        assert partial.initial_overlay_pending is True
        assert partial.fork_point_sha == _git(
            ["rev-parse", "HEAD"], partial.git_worktree_path
        ).stdout.strip()
        overlay = partial.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        assert not overlay.exists()

        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", real_writer)
        resumed = worktree.ensure(tmp_repo, "overlay-resume")
        assert resumed.state == "ready"
        assert resumed.initial_overlay_pending is False
        assert overlay.read_text(encoding="utf-8") == partial.initial_overlay

    def test_create_resume_checks_out_a_recorded_but_unpopulated_workspace(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_finish = worktree._finish_allocation
        monkeypatch.setattr(
            worktree, "_finish_allocation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt("simulated stop before checkout")
            ),
        )
        with pytest.raises(KeyboardInterrupt, match="stop before checkout"):
            worktree.create(tmp_repo, "overlay-before-checkout", profile="core")

        partial = worktree.find_instance_record(tmp_repo, "overlay-before-checkout")
        assert partial is not None and partial.state == "allocating"
        assert partial.fork_point_sha is None
        overlay = partial.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        assert not overlay.exists()

        monkeypatch.setattr(worktree, "_finish_allocation", real_finish)
        resumed = worktree.ensure(tmp_repo, "overlay-before-checkout")

        assert resumed.state == "ready"
        assert resumed.fork_point_sha == _git(
            ["rev-parse", "HEAD"], resumed.git_worktree_path
        ).stdout.strip()
        assert overlay.read_text(encoding="utf-8") == partial.initial_overlay

    def test_create_resume_refuses_to_replace_conflicting_overlay(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        def interrupt_before_overlay(*_args, **_kwargs):
            raise KeyboardInterrupt("simulated interruption")

        real_writer = worktree._write_rendered_worktree_overlay
        monkeypatch.setattr(
            worktree, "_write_rendered_worktree_overlay", interrupt_before_overlay
        )
        with pytest.raises(KeyboardInterrupt):
            worktree.create(tmp_repo, "overlay-conflict", profile="core")
        partial = worktree.find_instance_record(tmp_repo, "overlay-conflict")
        assert partial is not None
        overlay = partial.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        overlay.write_text("operator-owned\n", encoding="utf-8")

        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", real_writer)
        with pytest.raises(
            worktree.WorktreeError, match="differs from the recorded allocation intent"
        ):
            worktree.ensure(tmp_repo, "overlay-conflict")
        assert overlay.read_text(encoding="utf-8") == "operator-owned\n"

    def test_legacy_partial_record_without_overlay_intent_refuses_resume(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "legacy-overlay-intent")
        # A genuinely legacy partial record: written before overlay intent
        # (and the one-time completion marker, schema v4+) existed. A v4+
        # record with a cleared pending marker is a completed allocation and
        # takes the repair path instead.
        partial = replace(
            record,
            base_branch_ref=None, schema_version=2,
            state="recovery-required",
            recovery_status="env-generation-failed",
            initial_overlay=None,
            overlay_intent_known=False,
            checkout_reset_pending=None,
            network_id=None,
        )
        worktree._write_instance_record(partial)

        with pytest.raises(
            worktree.WorktreeError, match="predates durable overlay intent"
        ):
            worktree.ensure(tmp_repo, "legacy-overlay-intent")
        assert not (
            partial.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        ).exists()

    def test_create_rejects_duplicate_logical_identity(self, tmp_repo, fake_generate_env):
        worktree.create(tmp_repo, "logical-one", display_name="first")
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            worktree.create(tmp_repo, "logical-one", display_name="second")

    def test_git_add_and_overlay_failures_remain_attributable(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        shared = worktree._shared_worktree()
        with monkeypatch.context() as m:
            m.setattr(
                shared,
                "create_workspace",
                lambda *_a, **_kw: (_ for _ in ()).throw(
                    shared.WorkspaceError("cannot add", category="git-error")
                ),
            )
            with pytest.raises(worktree.WorktreeError, match="cannot add"):
                worktree.create(tmp_repo, "git-fails")

        monkeypatch.setattr(worktree, "_clean_in", lambda *_a, **_kw: 0)
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        monkeypatch.setattr(
            worktree, "_write_rendered_worktree_overlay",
            lambda *_a, **_kw: (_ for _ in ()).throw(worktree.WorktreeError("overlay failed")),
        )
        with pytest.raises(worktree.WorktreeError, match="overlay failed"):
            worktree.create(tmp_repo, "overlay-fails", profile="core")
        record = worktree.find_instance_record(tmp_repo, "overlay-fails")
        assert record is not None and record.recovery_status == "env-generation-failed"
        assert record.fork_point_sha == _git(
            ["rev-parse", "HEAD"], record.git_worktree_path
        ).stdout.strip()

    def test_generated_advanced_override_must_retain_one_to_one_name(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_utc_now", lambda: self.FIXED_NOW)
        with pytest.raises(worktree.WorktreeError, match="must be identical"):
            worktree.create(
                tmp_repo, "logical-one", prefix="ciu", feature="feat",
                branch="different",
            )

    def test_checkout_failure_leaves_closed_recovery_record(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_git = worktree._git

        def fail_reset(args, cwd):
            if args[:2] == ["reset", "--hard"]:
                return subprocess.CompletedProcess(args, 1, "", "checkout failed")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", fail_reset)
        with pytest.raises(worktree.WorktreeError, match="checkout.*failed"):
            worktree.create(tmp_repo, "logical-one")
        failed = worktree.find_instance_record(tmp_repo, "logical-one")
        assert failed is not None and failed.recovery_status == "checkout-incomplete"

    def test_full_env_failure_and_identity_change_are_recoverable_states(
        self, tmp_repo, monkeypatch, write_instance_facts
    ):
        calls = 0

        def full_failure(
            path: Path, *, identity_only: bool = False, allocation_lock_held: bool = False
        ) -> int:
            nonlocal calls
            calls += 1
            write_instance_facts(
                path, instance_id="stable1", network="stable-network"
            )
            return 0 if identity_only else 9

        monkeypatch.setattr(worktree, "_generate_env_in", full_failure)
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        with pytest.raises(worktree.WorktreeError, match="full.*failed"):
            worktree.create(tmp_repo, "logical-one")
        failed = worktree.find_instance_record(tmp_repo, "logical-one")
        assert failed is not None and failed.recovery_status == "env-generation-failed"
        assert failed.instance_id == "stable1"

        # A fresh repository isolates the second terminal state.
        other = tmp_repo.parent / "other"
        other.mkdir()
        assert _git(["init", "-b", "main"], other).returncode == 0
        assert _git(["config", "user.email", "t@example.com"], other).returncode == 0
        assert _git(["config", "user.name", "Test"], other).returncode == 0
        (other / "README.md").write_text("x\n")
        (other / "ciu.global.defaults.toml.j2").write_text("[ciu]\n")
        assert _git(["add", "."], other).returncode == 0
        assert _git(["commit", "-m", "init"], other).returncode == 0

        def changes(
            path: Path, *, identity_only: bool = False, allocation_lock_held: bool = False
        ) -> int:
            suffix = "one" if identity_only else "two"
            write_instance_facts(
                path, instance_id=suffix, network=f"network-{suffix}"
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", changes)
        with pytest.raises(worktree.WorktreeError, match="identity changed"):
            worktree.create(other, "logical-two")
        changed = worktree.find_instance_record(other, "logical-two")
        assert changed is not None
        assert changed.recovery_status == "runtime-verification-failed"

    def test_host_network_collision_refuses_before_full_bootstrap(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(
            worktree,
            "_docker_network_details",
            lambda _network: {"id": "a" * 64, "labels": {}},
        )
        with pytest.raises(worktree.WorktreeError, match="cannot prove ownership"):
            worktree.create(tmp_repo, "logical-one")

    def test_create_retry_does_not_claim_a_network_created_after_failure(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_docker_network_details", lambda _network: None)

        def fail_full_generation(
            root: Path, *, identity_only: bool = False, allocation_lock_held: bool = False
        ) -> int:
            if identity_only:
                return fake_generate_env(root, identity_only=True)
            return 9

        monkeypatch.setattr(worktree, "_generate_env_in", fail_full_generation)
        with pytest.raises(worktree.WorktreeError, match="full.*failed"):
            worktree.create(tmp_repo, "network-retry")

        partial = worktree.find_instance_record(tmp_repo, "network-retry")
        assert partial is not None
        assert partial.adopted is False
        assert partial.recovery_status == "env-generation-failed"
        assert partial.initial_overlay_pending is True

        monkeypatch.setattr(worktree, "_generate_env_in", fake_generate_env)
        monkeypatch.setattr(
            worktree,
            "_docker_network_details",
            lambda _network: {"id": "b" * 64, "labels": {}},
        )
        with pytest.raises(worktree.WorktreeError, match="cannot prove ownership"):
            worktree.ensure(tmp_repo, "network-retry")

        refused = worktree.read_instance_record(partial.record_path)
        assert refused.state == "recovery-required"
        # An unlabeled same-name network is *unproven* ownership (distinct from
        # a network labeled for another instance, which is `runtime-collision`).
        assert refused.recovery_status == "runtime-verification-failed"
        assert refused.adopted is False
        assert refused.logical_name == "network-retry"


class TestForkPointProvenance:
    """CIU-106: new allocations snapshot their requested base commit.

    Historical records may still hold symbolic base refs. A new record stores
    the resolved commit in `base_ref`, and the `fork_point_sha` is read back
    from the checkout after reset. A moving branch cannot change the tree
    validated during preflight or the tree allocated.
    """

    def test_create_records_where_base_pointed(self, tmp_repo, fake_generate_env):
        expected = _git(["rev-parse", "main"], tmp_repo).stdout.strip()
        record = worktree.create(tmp_repo, "logical-one", base="main")
        assert record.fork_point_sha == expected
        stored = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert stored["fork_point_sha"] == expected

    def test_the_recorded_fork_point_survives_a_reread(
        self, tmp_repo, fake_generate_env
    ):
        """It is only useful to a consumer that reads it back off disk."""
        record = worktree.create(tmp_repo, "logical-one", base="main")
        again = worktree.find_instance_record(tmp_repo, "logical-one")
        assert again.fork_point_sha == record.fork_point_sha

    def test_a_later_base_commit_does_not_change_the_record(
        self, tmp_repo, fake_generate_env
    ):
        """The whole point: the record is a SNAPSHOT. `main` moving on is
        exactly the situation the field exists to survive."""
        record = worktree.create(tmp_repo, "logical-one", base="main")
        fork = record.fork_point_sha
        (tmp_repo / "later.txt").write_text("after the fork\n", encoding="utf-8")
        assert _git(["add", "-A"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "unrelated main work"], tmp_repo).returncode == 0
        assert _git(["rev-parse", "main"], tmp_repo).stdout.strip() != fork
        again = worktree.find_instance_record(tmp_repo, "logical-one")
        assert again.fork_point_sha == fork

    def test_a_merge_back_into_base_is_detectable_only_with_this_field(
        self, tmp_repo, fake_generate_env
    ):
        """The real repro, end to end — the scenario three run-gate review
        rounds each failed to detect from `base_ref` alone.

        Fork, commit on the branch, merge it back into `main` --no-ff, commit
        again. `merge-base(main, HEAD)` is now the PRE-MERGE branch tip, not
        the fork; only comparing it against the recorded fork point tells the
        two apart.
        """
        record = worktree.create(tmp_repo, "logical-one", base="main")
        fork, wt = record.fork_point_sha, record.git_worktree_path
        assert _git(["checkout", record.branch], wt).returncode == 0
        (wt / "work.txt").write_text("branch work\n", encoding="utf-8")
        assert _git(["add", "-A"], wt).returncode == 0
        assert _git(["commit", "-m", "branch work"], wt).returncode == 0

        # healthy: merge-base is still exactly the recorded fork point
        assert _git(["merge-base", "main", "HEAD"], wt).stdout.strip() == fork

        assert _git(["merge", "--no-ff", "-m", "merge", record.branch],
                    tmp_repo).returncode == 0
        (wt / "after.txt").write_text("follow-up\n", encoding="utf-8")
        assert _git(["add", "-A"], wt).returncode == 0
        assert _git(["commit", "-m", "follow-up"], wt).returncode == 0

        # spent: merge-base has moved past the fork, and says so
        assert _git(["merge-base", "main", "HEAD"], wt).stdout.strip() != fork

    def test_adopt_records_no_fork_point(self, tmp_repo, fake_generate_env):
        """An adopted checkout has no knowable fork commit — `None` is the
        honest answer, and `None` is what a consumer must fail closed on."""
        wt_path = tmp_repo.parent / "adopted"
        assert _git(["worktree", "add", "-b", "adopted", str(wt_path), "main"],
                    tmp_repo).returncode == 0
        record = worktree.adopt(tmp_repo, "adopted-one", str(wt_path))
        assert record.fork_point_sha is None
        assert record.adopted is True
        stored = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert "fork_point_sha" not in stored

    def test_a_partial_adopt_resumes_without_checkout_when_head_is_unchanged(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        wt_path = tmp_repo.parent / "adopted-same-head"
        assert _git(["worktree", "add", "-b", "adopted-same-head", str(wt_path), "main"],
                    tmp_repo).returncode == 0
        adopted_head = _git(["rev-parse", "HEAD"], wt_path).stdout.strip()
        monkeypatch.setattr(
            worktree, "_write_rendered_worktree_overlay",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("overlay failed")),
        )
        with pytest.raises(worktree.WorktreeError, match="multi-root preparation failed"):
            worktree.adopt(
                tmp_repo, "adopted-same-head", str(wt_path), profile="core"
            )
        monkeypatch.setattr(
            worktree, "_write_rendered_worktree_overlay", lambda *_a, **_kw: None
        )

        resumed = worktree.ensure(tmp_repo, "adopted-same-head")
        assert resumed.state == "ready"
        assert _git(["rev-parse", "HEAD"], wt_path).stdout.strip() == adopted_head
        assert resumed.fork_point_sha is None

    def test_a_partial_adopt_refuses_resume_after_head_moves(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """Regression (round-5 review): `adopt` wrote its record and THEN
        called `_write_worktree_overlay` unguarded. `ensure` decides
        `checkout_required` from `recovery_status in (None,
        "checkout-incomplete")`, and that record carries `None` — so a failed
        overlay write left a record whose resume did `git reset --hard <the
        adopted HEAD>` in the operator's own checkout, DESTROYING anything
        committed there since, and (CIU-106) stamped a `fork_point_sha` onto
        an adopt-shaped record the docs promise never carries one."""
        wt_path = tmp_repo.parent / "adopted"
        assert _git(["worktree", "add", "-b", "adopted", str(wt_path), "main"],
                    tmp_repo).returncode == 0
        adopted_head = _git(["rev-parse", "HEAD"], wt_path).stdout.strip()

        def boom(*args, **kwargs):
            raise OSError("no space left on device")

        real_writer = worktree._write_rendered_worktree_overlay
        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", boom)
        with pytest.raises(worktree.WorktreeError, match="multi-root preparation failed"):
            worktree.adopt(tmp_repo, "adopted-one", str(wt_path), profile="core")
        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", real_writer)

        partial = worktree.find_instance_record(tmp_repo, "adopted-one")
        assert partial is not None
        assert partial.recovery_status == "env-generation-failed"
        assert partial.fork_point_sha is None

        # A process can be interrupted just after ensure republishes the
        # in-progress record. An adopted checkout must keep the no-reset
        # marker across that boundary; otherwise the next resume sees an
        # ordinary allocating record and may reset the operator's branch.
        write_instance_record = worktree._write_instance_record

        def interrupt_after_record(record):
            write_instance_record(record)
            raise KeyboardInterrupt("simulated interruption after record write")

        monkeypatch.setattr(worktree, "_write_instance_record", interrupt_after_record)
        with pytest.raises(KeyboardInterrupt, match="simulated interruption"):
            worktree.ensure(tmp_repo, "adopted-one")
        monkeypatch.setattr(worktree, "_write_instance_record", write_instance_record)
        interrupted = worktree.find_instance_record(tmp_repo, "adopted-one")
        assert interrupted is not None
        assert interrupted.state == "recovery-required"
        assert interrupted.recovery_status == "env-generation-failed"
        assert interrupted.fork_point_sha is None

        # The operator keeps working in their own checkout before resuming.
        (wt_path / "mine.txt").write_text("do not lose this\n", encoding="utf-8")
        assert _git(["add", "-A"], wt_path).returncode == 0
        assert _git(["commit", "-m", "operator work"], wt_path).returncode == 0
        work = _git(["rev-parse", "HEAD"], wt_path).stdout.strip()
        assert work != adopted_head

        with pytest.raises(worktree.WorktreeError, match=r"\[CIU-107\].*HEAD moved") as refusal:
            worktree.ensure(tmp_repo, "adopted-one")
        assert f"HEAD is {work}" in str(refusal.value)
        # The new commit remains exactly where the operator made it; resume
        # never attempts a hard reset against the recorded adoption target.
        assert _git(["rev-parse", "HEAD"], wt_path).stdout.strip() == work
        assert (wt_path / "mine.txt").exists()

    def test_a_record_without_the_field_stays_readable(self, tmp_path):
        """Every record written before CIU-106 lacks the key. Absence is
        legal — this is why the field needed no schema_version bump."""
        raw = TestManagedRecordValidation.raw(tmp_path)
        assert "fork_point_sha" not in raw
        loaded = worktree._record_from_dict(raw, tmp_path / "r.json")
        assert loaded.fork_point_sha is None

    def test_a_record_with_the_field_round_trips(self, tmp_path):
        sha = "0123456789abcdef0123456789abcdef01234567"
        raw = {**TestManagedRecordValidation.raw(tmp_path), "fork_point_sha": sha}
        loaded = worktree._record_from_dict(raw, tmp_path / "r.json")
        assert loaded.fork_point_sha == sha
        assert loaded.to_dict()["fork_point_sha"] == sha

    def test_a_none_fork_point_is_omitted_from_the_serialized_shape(self, tmp_path):
        """Not `"fork_point_sha": null` — absence, so that a pre-CIU-106
        record and an `adopt` record share one shape a reader need not tell
        apart."""
        raw = TestManagedRecordValidation.raw(tmp_path)
        assert "fork_point_sha" not in worktree._record_from_dict(
            raw, tmp_path / "r.json"
        ).to_dict()

    @pytest.mark.parametrize(
        "bad",
        ["", "not-a-sha", "0123456789ABCDEF0123456789ABCDEF01234567",
         "0123456789abcdef0123456789abcdef0123456", 42, [], {}, True],
    )
    def test_a_present_but_malformed_fork_point_is_refused(self, tmp_path, bad):
        """Absent is legal; present-and-unusable is not. A consumer's whole
        use of this field is an equality test against a fresh `merge-base`,
        so a value that can never match would masquerade as a spent record
        forever instead of being reported."""
        raw = {**TestManagedRecordValidation.raw(tmp_path), "fork_point_sha": bad}
        with pytest.raises(worktree.WorktreeError, match="fork_point_sha"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_an_unknown_key_is_still_refused(self, tmp_path):
        """The optional key widened the schema by exactly one name — the
        closed-key-set check is otherwise untouched."""
        raw = {**TestManagedRecordValidation.raw(tmp_path), "invented": 1}
        with pytest.raises(worktree.WorktreeError, match="unknown"):
            worktree._record_from_dict(raw, tmp_path / "r.json")

    def test_create_refuses_when_the_allocated_head_cannot_be_resolved(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """Without an exact commit CIU cannot prove which nested roots need
        preparation, so the allocated worktree must never become ready."""
        real = worktree._git

        def flaky(args, cwd, **kwargs):
            if args[:2] == ["rev-parse", "--verify"] and "HEAD^{commit}" in args:
                # Only the post-allocation HEAD probe; the earlier base
                # verification (`<base>^{commit}`) must still succeed.
                return subprocess.CompletedProcess(args, 128, "", "boom")
            return real(args, cwd, **kwargs)

        monkeypatch.setattr(worktree, "_git", flaky)
        with pytest.raises(
            worktree.WorktreeError,
            match="could not checkpoint its exact HEAD",
        ):
            worktree.create(tmp_repo, "logical-one", base="main")
        record = worktree.find_instance_record(tmp_repo, "logical-one")
        assert record is not None
        assert record.fork_point_sha is None
        assert record.state == "recovery-required"
        # The initial reset already ran, so the record is parked as
        # checkout-incomplete (never re-reset automatically).
        assert record.recovery_status == "checkout-incomplete"

    @pytest.mark.parametrize(
        "output", ["", "   \n", "not-a-sha", "deadbeef",
                   "0123456789ABCDEF0123456789ABCDEF01234567",
                   "0123456789abcdef0123456789abcdef01234567 extra",
                   "0123456789abcdef0123456789abcdef01234567junk"],
    )
    def test_create_refuses_when_rev_parse_succeeds_with_a_non_sha(
        self, tmp_repo, fake_generate_env, monkeypatch, output
    ):
        """Exit 0 is not proof — the OUTPUT has to be a real object name.
        A malformed answer cannot identify the tree whose nested roots must
        be prepared, so create leaves a recoverable non-ready record.

        The last two cases are a valid 40-hex PREFIX with a tail: they pin
        `fullmatch` specifically, which `match` would wave through."""
        real = worktree._git

        def odd(args, cwd, **kwargs):
            if args[:2] == ["rev-parse", "--verify"] and "HEAD^{commit}" in args:
                # Only the post-allocation HEAD probe; the earlier base
                # verification (`<base>^{commit}`) must still succeed.
                return subprocess.CompletedProcess(args, 0, output, "")
            return real(args, cwd, **kwargs)

        monkeypatch.setattr(worktree, "_git", odd)
        with pytest.raises(
            worktree.WorktreeError,
            match="could not checkpoint its exact HEAD",
        ):
            worktree.create(tmp_repo, "logical-one", base="main")
        record = worktree.find_instance_record(tmp_repo, "logical-one")
        assert record is not None
        assert record.fork_point_sha is None
        assert record.state == "recovery-required"
        # The initial reset already ran, so the record is parked as
        # checkout-incomplete (never re-reset automatically).
        assert record.recovery_status == "checkout-incomplete"

    def test_base_ref_is_snapshotted_before_allocation_and_validation(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """A moving branch cannot change the tree validated or checked out."""
        before = _git(["rev-parse", "main"], tmp_repo).stdout.strip()
        real_write_record = worktree._write_instance_record
        raced = False

        def racing_record(record):
            nonlocal raced
            real_write_record(record)
            if not raced and record.state == "allocating" and record.fork_point_sha is None:
                raced = True
                (tmp_repo / "ciu.global.defaults.toml.j2").unlink()
                (tmp_repo / "raced.txt").write_text(
                    "another session\n", encoding="utf-8"
                )
                assert _git(["add", "-A"], tmp_repo).returncode == 0
                assert _git(["commit", "-m", "raced commit"], tmp_repo).returncode == 0

        monkeypatch.setattr(worktree, "_write_instance_record", racing_record)
        record = worktree.create(
            tmp_repo, "logical-one", base="main", profile="core"
        )
        after = _git(["rev-parse", "main"], tmp_repo).stdout.strip()
        assert raced
        assert after != before                       # the race really happened
        head = _git(["rev-parse", "HEAD"], record.git_worktree_path).stdout.strip()
        assert record.base_ref == before
        assert record.fork_point_sha == before
        assert head == before
        assert (record.ciu_root / "ciu.global.defaults.toml.j2").is_file()
        assert (
            record.ciu_root / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        ).is_file()
        # ... and the record is therefore LIVE, not born spent
        assert _git(["merge-base", "main", "HEAD"],
                    record.git_worktree_path).stdout.strip() == record.fork_point_sha

    def test_a_partial_allocation_already_carries_it_and_ensure_keeps_it(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """Capturing at the CHECKOUT rather than at entry also fixes the
        resume path, with no race involved: the checkout is the first thing
        `_finish_allocation` does, so even a record left behind by a create
        that died later already carries the right fork point — and the
        `ensure` that completes it (checkout_required=False for
        env-generation-failed) must carry it through, not drop it."""
        real_generate = worktree._generate_env_in
        calls = {"n": 0}

        def fail_once(*args, **kwargs):
            calls["n"] += 1
            return 1 if calls["n"] == 1 else real_generate(*args, **kwargs)

        monkeypatch.setattr(worktree, "_generate_env_in", fail_once)
        with pytest.raises(worktree.WorktreeError):
            worktree.create(tmp_repo, "logical-one", base="main")
        partial = worktree.find_instance_record(tmp_repo, "logical-one")
        assert partial is not None
        head = _git(["rev-parse", "HEAD"], partial.git_worktree_path).stdout.strip()
        assert partial.fork_point_sha == head
        assert partial.recovery_status == "env-generation-failed"

        monkeypatch.setattr(worktree, "_generate_env_in", real_generate)
        resumed = worktree.ensure(tmp_repo, "logical-one", base="main")
        assert resumed.state == "ready"
        assert resumed.fork_point_sha == head      # survived the replace() chain

    def test_ensure_refuses_to_reset_after_uncheckpointed_reset_and_staged_deletions(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_git = worktree._git
        reset_calls = 0
        fail_head_checkpoint = False

        def interrupt_after_reset(args, cwd):
            nonlocal reset_calls, fail_head_checkpoint
            result = real_git(args, cwd)
            if args[:2] == ["reset", "--hard"] and result.returncode == 0:
                reset_calls += 1
                fail_head_checkpoint = True
            elif (
                fail_head_checkpoint
                and args[:3] == ["rev-parse", "--verify", "--quiet"]
            ):
                fail_head_checkpoint = False
                return subprocess.CompletedProcess(args, 1, "", "simulated interruption")
            return result

        monkeypatch.setattr(worktree, "_git", interrupt_after_reset)
        with pytest.raises(worktree.WorktreeError, match="exact HEAD"):
            worktree.create(tmp_repo, "reset-interrupted")

        partial = worktree.find_instance_record(tmp_repo, "reset-interrupted")
        assert partial is not None
        assert partial.checkout_reset_pending is True
        assert partial.fork_point_sha is None
        assert reset_calls == 1

        # Reproduce the ambiguous porcelain shape: after a successful reset,
        # the operator stages deletion of every tracked path. It is identical
        # to a pristine --no-checkout worktree, so ensure must refuse to reset.
        removed = real_git(["rm", "-r", "."], partial.git_worktree_path)
        assert removed.returncode == 0
        before_status = real_git(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
            partial.git_worktree_path,
        ).stdout

        with pytest.raises(worktree.WorktreeError, match="will not repeat"):
            worktree.ensure(tmp_repo, "reset-interrupted")

        after_status = real_git(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
            partial.git_worktree_path,
        ).stdout
        assert after_status == before_status
        assert reset_calls == 1

    def test_ensure_base_must_resolve_to_the_saved_allocation_commit(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "base-constrained", base="main")
        (tmp_repo / "later.txt").write_text("later base\n", encoding="utf-8")
        assert _git(["add", "later.txt"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "move main"], tmp_repo).returncode == 0

        with pytest.raises(worktree.WorktreeError, match="recorded allocation commit"):
            worktree.ensure(tmp_repo, "base-constrained", base="main")

        assert worktree.read_instance_record(record.record_path).state == "ready"

    def test_ensure_worktree_dir_must_match_existing_path(
        self, tmp_repo, fake_generate_env
    ):
        worktree.create(tmp_repo, "dir-constrained", worktree_dir=".worktrees")

        with pytest.raises(worktree.WorktreeError, match="ensure mismatch for worktree_dir"):
            worktree.ensure(
                tmp_repo, "dir-constrained", worktree_dir=".other-worktrees"
            )


class TestBaseBranchRef:
    """Schema v6: ``base_branch_ref`` is decided once, at allocation."""

    @staticmethod
    def _stored(record):
        return json.loads(record.record_path.read_text(encoding="utf-8"))

    def test_local_branch_name_is_recorded(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "by-name", base="main")
        assert record.schema_version == 6
        assert record.base_branch_ref == "main"
        assert self._stored(record)["base_branch_ref"] == "main"
        assert record.base_ref == _git(["rev-parse", "main"], tmp_repo).stdout.strip()

    def test_refs_heads_spelling_is_canonicalised(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "by-ref", base="refs/heads/main")
        assert record.base_branch_ref == "main"

    def test_tag_sha_head_and_remote_tracking_names_record_null(
        self, tmp_repo, fake_generate_env
    ):
        sha = _git(["rev-parse", "main"], tmp_repo).stdout.strip()
        assert _git(["tag", "release-1", sha], tmp_repo).returncode == 0
        assert _git(
            ["update-ref", "refs/remotes/origin/main", sha], tmp_repo
        ).returncode == 0
        for logical, base in (
            ("by-tag", "release-1"),
            ("by-sha", sha),
            ("by-head", "HEAD"),
            ("by-remote", "origin/main"),
            ("by-full-remote", "refs/remotes/origin/main"),
            # Revision syntax resolves to the same commit but is not a branch name.
            ("by-tilde", "main~0"),
            ("by-caret", "main^0"),
            ("by-peel", "main^{commit}"),
            ("by-reflog", "main@{0}"),
        ):
            record = worktree.create(tmp_repo, logical, base=base)
            assert record.base_branch_ref is None, base
            assert self._stored(record)["base_branch_ref"] is None, base
            assert record.base_ref == sha

    def test_branch_moved_between_resolution_and_check_records_null(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_git = worktree._git

        def moved(args, cwd):
            if any(str(a) == "refs/heads/main^{commit}" for a in args):
                return subprocess.CompletedProcess(args, 0, "f" * 40 + "\n", "")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", moved)
        record = worktree.create(tmp_repo, "moved", base="main")
        assert record.base_branch_ref is None

    def test_missing_branch_is_null(self, tmp_repo):
        sha = _git(["rev-parse", "main"], tmp_repo).stdout.strip()
        assert worktree._local_base_branch(tmp_repo, "no-such-branch", sha) is None
        assert worktree._local_base_branch(tmp_repo, "main", "e" * 40) is None
        assert worktree._local_base_branch(tmp_repo, "main", sha) == "main"

    def test_ensure_keeps_the_persisted_value(self, tmp_repo, fake_generate_env):
        created = worktree.create(tmp_repo, "kept", base="main")
        ensured = worktree.ensure(tmp_repo, "kept")
        assert ensured.base_branch_ref == "main"
        (tmp_repo / "later.txt").write_text("later\n", encoding="utf-8")
        assert _git(["add", "later.txt"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "advance main"], tmp_repo).returncode == 0
        again = worktree.ensure(tmp_repo, "kept")
        assert again.base_branch_ref == "main"
        assert again.base_ref == created.base_ref
        assert self._stored(again)["base_branch_ref"] == "main"

    def test_adopt_records_null(self, tmp_repo, fake_generate_env):
        wt_path = tmp_repo.parent / "adopted-v6"
        assert _git(["worktree", "add", "-b", "adopted-v6", str(wt_path), "main"],
                    tmp_repo).returncode == 0
        record = worktree.adopt(tmp_repo, "adopted-v6", str(wt_path))
        assert record.schema_version == 6
        assert record.base_branch_ref is None
        assert self._stored(record)["base_branch_ref"] is None


class TestRunGateReadsTheRealRecord:
    """The seam itself, run for real ONCE rather than trusted.

    `ciu.worktree-instance.json` is consumed by vbpub's installed Run-Gate
    wheel as a
    loose FILE-FORMAT contract — by well-known filename, with stdlib `json`,
    deliberately never by importing `ciu` (separate projects; Run-Gate's
    supported CLI remains a separately installed wheel). The cost of that choice is
    that BOTH sides' unit tests use hand-written fixtures of the other side's
    output, which is exactly the drift TESTING-METHODOLOGY's "no drifted
    fixtures at a seam" row warns about: run-gate's suite writes records by
    hand and this suite reads them by hand, and neither would notice if
    `to_dict()` and the reader stopped agreeing.

    So: produce a record with the REAL `worktree.create()`, and feed it to
    the REAL reader from the installed `run-gate` wheel. A missing Run-Gate
    package FAILS the test (a missing optional dependency is never a skip).
    """

    @staticmethod
    def _run_gate():
        try:
            import run_gate
        except ModuleNotFoundError as exc:
            if exc.name != "run_gate":
                raise
            pytest.fail(
                "run_gate is not importable: the seam tests need a Run-Gate "
                "reader that understands record schema 6"
            )
        return run_gate

    def test_a_real_ciu_record_is_ACCEPTED_by_run_gates_real_reader(
        self, tmp_repo, fake_generate_env
    ):
        rg = self._run_gate()
        record = worktree.create(tmp_repo, "logical-one", base="main")
        tree = record.git_worktree_path
        # A worktree with no commits of its own is refused on purpose (base
        # contains HEAD => nothing to judge), so give it real work first.
        (tree / "feature.txt").write_text("work\n", encoding="utf-8")
        assert _git(["add", "-A"], tree).returncode == 0
        assert _git(["commit", "-m", "branch work"], tree).returncode == 0

        base, found, why = rg.recorded_worktree_base(tree, tree, record.ciu_root)
        assert why == ""
        assert found == record.record_path
        # What comes back is the verified fork COMMIT, not the branch name.
        assert base == record.fork_point_sha
        assert rg.FULL_SHA_RE.fullmatch(base)

    def test_a_real_ciu_record_is_REFUSED_once_main_absorbs_the_branch(
        self, tmp_repo, fake_generate_env
    ):
        """The false-green three review rounds kept re-finding, end to end."""
        rg = self._run_gate()
        record = worktree.create(tmp_repo, "logical-one", base="main")
        tree = record.git_worktree_path
        (tree / "feature.txt").write_text("work\n", encoding="utf-8")
        assert _git(["add", "-A"], tree).returncode == 0
        assert _git(["commit", "-m", "branch work"], tree).returncode == 0
        assert _git(["merge", "--no-ff", "-m", "merge", record.branch],
                    tmp_repo).returncode == 0
        (tree / "after.txt").write_text("more\n", encoding="utf-8")
        assert _git(["add", "-A"], tree).returncode == 0
        assert _git(["commit", "-m", "one more"], tree).returncode == 0

        base, found, why = rg.recorded_worktree_base(tree, tree, record.ciu_root)
        assert base is None
        assert found == record.record_path
        assert "MOVED since the record was written" in why

    def test_an_adopt_record_is_refused_by_the_real_reader(
        self, tmp_repo, fake_generate_env
    ):
        """`adopt()` records no fork point and a null `base_branch_ref`; the
        real reader refuses on the null branch with the explicit-`--base`
        remedy. Pinning WHICH clause fires matters: if the ordering ever
        changed so that adopt fell through to a later clause, this would go
        red rather than silently relying on a guard that no longer runs."""
        rg = self._run_gate()
        wt_path = tmp_repo.parent / "adopted"
        assert _git(["worktree", "add", "-b", "adopted", str(wt_path), "main"],
                    tmp_repo).returncode == 0
        record = worktree.adopt(tmp_repo, "adopted-one", str(wt_path))
        assert record.fork_point_sha is None
        assert record.base_branch_ref is None
        base, found, why = rg.recorded_worktree_base(
            record.git_worktree_path, record.git_worktree_path, record.ciu_root
        )
        assert base is None
        assert found == record.record_path
        assert "'base_branch_ref' is null" in why
        assert "--base" in why

    def test_a_schema_five_sha_record_is_refused_with_the_base_remedy(
        self, tmp_repo, fake_generate_env
    ):
        """Records written by ciu 7.17.0 keep a frozen-SHA `base_ref` and no
        `base_branch_ref`; they stay valid for ciu but the real reader
        refuses them and names the explicit `--base` remedy."""
        rg = self._run_gate()
        created = worktree.create(tmp_repo, "legacy-five", base="main")
        v5 = replace(created, schema_version=5, base_branch_ref=None)
        worktree._write_instance_record(v5)
        tree = created.git_worktree_path
        (tree / "feature.txt").write_text("work\n", encoding="utf-8")
        assert _git(["add", "-A"], tree).returncode == 0
        assert _git(["commit", "-m", "branch work"], tree).returncode == 0
        stored = json.loads(created.record_path.read_text(encoding="utf-8"))
        assert stored["schema_version"] == 5
        assert "base_branch_ref" not in stored

        base, found, why = rg.recorded_worktree_base(tree, tree, created.ciu_root)

        assert base is None
        assert found == created.record_path
        assert "--base" in why


class TestManagedRecordValidation:
    @staticmethod
    def raw(tmp_path: Path) -> dict:
        return {
            "schema_version": 1,
            "logical_name": "logical",
            "display_name": "display",
            "branch": "branch",
            "git_worktree_path": str((tmp_path / "checkout").resolve()),
            "ciu_root_offset": ".",
            "created_at_utc": "2026-08-17T12:34:56Z",
            "base_ref": "main",
            "state": "ready",
            "runtime": {"instance_id": "abc123", "network": "repo-network"},
            "recovery_status": None,
        }

    @classmethod
    def raw_v5(cls, tmp_path: Path) -> dict:
        return {
            **cls.raw(tmp_path),
            "schema_version": 5,
            "lease": None,
            "initial_overlay": None,
            "overlay_intent_known": True,
            "initial_overlay_pending": False,
            "adopted": False,
            "checkout_reset_pending": False,
            "network_id": "a" * 64,
            "fork_point_sha": "b" * 40,
        }

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda raw: [], "JSON object"),
            (lambda raw: {k: v for k, v in raw.items() if k != "branch"}, "missing"),
            (lambda raw: {**raw, "schema_version": 2}, "schema_version"),
            (lambda raw: {**raw, "runtime": []}, "runtime identity"),
            (lambda raw: {**raw, "branch": ""}, "required string"),
            (lambda raw: {**raw, "state": "future"}, "lifecycle state"),
            (lambda raw: {**raw, "recovery_status": "future"}, "recovery status"),
            (lambda raw: {
                **raw, "schema_version": 4, "lease": None,
                "initial_overlay": None, "overlay_intent_known": True,
                "initial_overlay_pending": False, "adopted": 1,
            }, "adopted must be boolean"),
            (lambda raw: {**raw, "runtime": {"instance_id": 3, "network": "n"}}, "runtime.instance_id"),
            (lambda raw: {**raw, "runtime": {"instance_id": None, "network": "n"}}, "incomplete runtime identity"),
            (lambda raw: {**raw, "state": "allocating", "runtime": {"instance_id": "i", "network": None}}, "incomplete runtime identity"),
            (lambda raw: {**raw, "state": "allocating", "recovery_status": "checkout-incomplete"}, "carries recovery"),
            (lambda raw: {**raw, "ciu_root_offset": "../escape"}, "unsafe ciu_root_offset"),
            (lambda raw: {**raw, "git_worktree_path": "relative"}, "not absolute"),
        ],
    )
    def test_closed_schema_rejects_malformed_records(self, tmp_path, change, message):
        raw = self.raw(tmp_path)
        candidate = change(raw)
        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._record_from_dict(candidate, tmp_path / "record.json")

    def test_read_rejects_invalid_json(self, tmp_path):
        path = tmp_path / "record.json"
        path.write_text("{not-json", encoding="utf-8")
        with pytest.raises(worktree.WorktreeError, match="valid instance record"):
            worktree.read_instance_record(path)

    @pytest.mark.parametrize(
        ("updates", "message"),
        [
            ({"overlay_intent_known": 1}, "overlay_intent_known must be boolean"),
            ({"initial_overlay_pending": 1}, "initial_overlay_pending must be boolean"),
            ({"initial_overlay": []}, "expected a string or null"),
            (
                {"initial_overlay": "payload", "overlay_intent_known": False},
                "unknown intent cannot carry a payload",
            ),
            (
                {"initial_overlay_pending": True, "overlay_intent_known": False},
                "pending recovery requires known intent",
            ),
            (
                {"initial_overlay_pending": True},
                "ready record cannot have pending recovery",
            ),
        ],
    )
    def test_schema_four_rejects_malformed_overlay_intent(
        self, tmp_path, updates, message
    ):
        raw = self.raw(tmp_path)
        raw.update(
            schema_version=4,
            lease=None,
            initial_overlay=None,
            overlay_intent_known=True,
            initial_overlay_pending=False,
            adopted=False,
        )
        raw.update(updates)
        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._record_from_dict(raw, tmp_path / "record.json")

    def test_schema_five_network_and_checkout_markers_round_trip(self, tmp_path):
        raw = self.raw_v5(tmp_path)

        record = worktree._record_from_dict(raw, tmp_path / "record.json")

        assert record.checkout_reset_pending is False
        assert record.network_id == "a" * 64
        assert record.to_dict() == raw

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda raw: {key: value for key, value in raw.items() if key != "network_id"}, "missing"),
            (lambda raw: {**raw, "checkout_reset_pending": 1}, "checkout_reset_pending must be boolean"),
            (lambda raw: {**raw, "network_id": "abc"}, "full 64-hex Docker id"),
            (lambda raw: {**raw, "runtime": {"instance_id": None, "network": None}}, "network_id requires a runtime network name"),
            (lambda raw: {**raw, "checkout_reset_pending": True, "adopted": True, "state": "recovery-required", "recovery_status": "checkout-incomplete", "fork_point_sha": None}, "requires a created allocation"),
        ],
    )
    def test_schema_five_rejects_malformed_recovery_and_network_identity(
        self, tmp_path, change, message
    ):
        raw = change(self.raw_v5(tmp_path))
        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._record_from_dict(raw, tmp_path / "record.json")

    @classmethod
    def raw_v6(cls, tmp_path: Path, base_branch_ref="main") -> dict:
        return {
            **cls.raw_v5(tmp_path),
            "schema_version": 6,
            "base_branch_ref": base_branch_ref,
        }

    @pytest.mark.parametrize("value", ["main", "feature/x", None])
    def test_schema_six_base_branch_ref_round_trips(self, tmp_path, value):
        raw = self.raw_v6(tmp_path, value)

        record = worktree._record_from_dict(raw, tmp_path / "record.json")

        assert record.schema_version == 6
        assert record.base_branch_ref == value
        assert record.to_dict() == raw

    def test_schema_five_json_has_no_base_branch_ref_key(self, tmp_path):
        record = worktree._record_from_dict(
            self.raw_v5(tmp_path), tmp_path / "record.json"
        )
        assert record.base_branch_ref is None
        assert "base_branch_ref" not in record.to_dict()

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            (lambda raw: {k: v for k, v in raw.items() if k != "base_branch_ref"}, "missing"),
            (lambda raw: {**raw, "base_branch_ref": ""}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": " main"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "main "}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "ma in"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "ma\x07in"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "ma\x7fin"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": 7}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": ["main"]}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "main~0"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "main^0"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "main@{0}"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "a..b"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "-main"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "refs/heads/main"}, "malformed base_branch_ref"),
            (lambda raw: {**raw, "base_branch_ref": "ma:in"}, "malformed base_branch_ref"),
        ],
    )
    def test_schema_six_rejects_malformed_base_branch_ref(
        self, tmp_path, change, message
    ):
        raw = change(self.raw_v6(tmp_path))
        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._record_from_dict(raw, tmp_path / "record.json")

    @pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
    def test_base_branch_ref_is_rejected_before_schema_six(self, tmp_path, version):
        raw = self.raw_v5(tmp_path)
        raw["schema_version"] = version
        if version < 5:
            raw.pop("checkout_reset_pending")
            raw.pop("network_id")
        if version < 4:
            raw.pop("initial_overlay_pending")
            raw.pop("adopted")
        if version < 3:
            raw.pop("initial_overlay")
            raw.pop("overlay_intent_known")
        if version < 2:
            raw.pop("lease")
        raw["base_branch_ref"] = "main"
        with pytest.raises(worktree.WorktreeError, match="unknown=.*base_branch_ref"):
            worktree._record_from_dict(raw, tmp_path / "record.json")

    def test_to_dict_refuses_base_branch_ref_before_schema_six(self, tmp_path):
        record = worktree._record_from_dict(
            self.raw_v5(tmp_path), tmp_path / "record.json"
        )
        with pytest.raises(ValueError, match="schema version 6"):
            replace(record, base_branch_ref="main").to_dict()

    @pytest.mark.parametrize("value", ["", " main", "ma in", 7])
    def test_to_dict_refuses_malformed_base_branch_ref_at_schema_six(
        self, tmp_path, value
    ):
        record = worktree._record_from_dict(
            self.raw_v6(tmp_path), tmp_path / "record.json"
        )
        with pytest.raises(ValueError, match="clean branch name or null"):
            replace(record, base_branch_ref=value).to_dict()

    def test_reading_a_schema_five_record_does_not_upgrade_it(self, tmp_path):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        raw = self.raw_v5(tmp_path)
        raw["git_worktree_path"] = str(checkout)
        raw["state"] = "allocating"
        raw["checkout_reset_pending"] = False
        raw["fork_point_sha"] = "b" * 40
        path = checkout / worktree.WORKTREE_INSTANCE_RECORD
        text = json.dumps(raw, indent=2, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8")

        record = worktree.read_instance_record(path)

        assert record.schema_version == 5
        assert record.base_branch_ref is None
        assert path.read_text(encoding="utf-8") == text

    def test_generated_name_rejects_naive_clock(self):
        with pytest.raises(worktree.WorktreeError, match="timezone-aware"):
            worktree.generated_worktree_name(
                "ciu", "feature", now=datetime(2026, 8, 17, 12, 0, 0)
            )

    def test_atomic_record_write_reports_replace_failure(self, tmp_path, monkeypatch):
        record = worktree._record_from_dict(self.raw(tmp_path), tmp_path / "source")
        monkeypatch.setattr(
            worktree.os, "replace",
            lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="atomically write.*denied"):
            worktree._write_instance_record(record)
        assert not any(tmp_path.glob(".*.tmp"))

    def test_rootless_reader_checks_marker_file_type_and_symlink_target(self, tmp_path):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        path = checkout / worktree.WORKTREE_INSTANCE_RECORD
        raw = self.raw(tmp_path)
        raw["git_worktree_path"] = str(checkout)
        raw["runtime"] = {"instance_id": None, "network": None}

        target = checkout / "defaults"
        target.write_text("[ciu]\n", encoding="utf-8")
        marker = checkout / worktree.GLOBAL_CONFIG_DEFAULTS
        marker.symlink_to(target)
        with pytest.raises(worktree.WorktreeError, match="ready record"):
            worktree._record_from_dict(raw, path)
        marker.unlink()

        marker.symlink_to(checkout / "missing")
        with pytest.raises(worktree.WorktreeError, match="could not inspect CIU root marker"):
            worktree._record_from_dict(raw, path)
        marker.unlink()

        marker.symlink_to(checkout, target_is_directory=True)
        with pytest.raises(worktree.WorktreeError, match="not a regular file"):
            worktree._record_from_dict(raw, path)
        marker.unlink()

        marker.mkdir()
        with pytest.raises(worktree.WorktreeError, match="not a regular file"):
            worktree._record_from_dict(raw, path)

    def test_rootless_reader_wraps_marker_lstat_oserror(self, tmp_path, monkeypatch):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        path = checkout / worktree.WORKTREE_INSTANCE_RECORD
        raw = self.raw(tmp_path)
        raw["git_worktree_path"] = str(checkout)
        raw["runtime"] = {"instance_id": None, "network": None}
        marker = checkout / worktree.GLOBAL_CONFIG_DEFAULTS
        original = Path.lstat

        def denied(candidate):
            if candidate == marker:
                raise PermissionError("denied")
            return original(candidate)

        monkeypatch.setattr(Path, "lstat", denied)
        with pytest.raises(worktree.WorktreeError, match="could not inspect CIU root marker"):
            worktree._record_from_dict(raw, path)

    def test_read_instance_record_wraps_unexpected_reader_errors(self, tmp_path, monkeypatch):
        path = tmp_path / "record.json"
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(
            worktree, "_record_from_dict",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("reader bug")),
        )
        with pytest.raises(worktree.WorktreeError, match="reader bug"):
            worktree.read_instance_record(path)

    def test_clean_in_routes_outdated_identity_and_strips_primary_environment(
        self, tmp_path, monkeypatch,
    ):
        from ciu import workspace_env

        monkeypatch.setattr(
            workspace_env, "outdated_generated_identity",
            lambda _worktree: {"instance_id": "old123"},
        )
        monkeypatch.setattr(
            workspace_env, "read_instance_identity_env",
            lambda *_a: pytest.fail("old identity must use label cleanup"),
        )
        monkeypatch.setenv("INSTANCE_ID", "primary-id")
        calls = []

        def run(argv, *, cwd, env, check):
            assert check is False, "CIU must preserve the child's exit status"
            calls.append((argv, cwd, env, check))
            return type("Completed", (), {"returncode": 17})()

        monkeypatch.setattr(worktree.subprocess, "run", run)
        assert worktree._clean_in(tmp_path, yes=True) == 17
        assert worktree._clean_in(tmp_path, yes=False) == 17
        assert calls[0][0][-2:] == ["old123", "-y"]
        assert calls[1][0][-2:] == ["old123", "--yes"]
        assert all(call[2].get("INSTANCE_ID") is None for call in calls)

        monkeypatch.setattr(workspace_env, "outdated_generated_identity", lambda _worktree: None)
        monkeypatch.setattr(
            workspace_env, "read_instance_identity_env",
            lambda *_a: (_ for _ in ()).throw(workspace_env.WorkspaceEnvError("unreadable")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not read"):
            worktree._clean_in(tmp_path, yes=True)

    def test_clean_in_reports_old_identity_when_child_cannot_start(
        self, tmp_path, monkeypatch,
    ):
        from ciu import workspace_env

        monkeypatch.setattr(
            workspace_env, "outdated_generated_identity",
            lambda _worktree: {"instance_id": "old123"},
        )

        def fail_to_spawn(*_args, **_kwargs):
            raise OSError("spawn failed")

        monkeypatch.setattr(worktree.subprocess, "run", fail_to_spawn)
        with pytest.raises(
            worktree.WorktreeError,
            match=r"ciu clean --identity old123.*spawn failed",
        ):
            worktree._clean_in(tmp_path, yes=True)

    @pytest.mark.parametrize("contents", ["[]", "not-json"])
    def test_record_logical_hint_returns_none_for_non_object_or_invalid_json(
        self, tmp_path, contents,
    ):
        path = tmp_path / "record.json"
        path.write_text(contents, encoding="utf-8")
        assert worktree._record_logical_name_hint(path) is None

    def test_family_scan_rejects_record_git_fact_mismatches(
        self, tmp_repo, fake_generate_env, capsys
    ):
        record = worktree.create(tmp_repo, "logical-one")
        original = json.loads(record.record_path.read_text(encoding="utf-8"))
        mutations = [
            ("git_worktree_path", str(tmp_repo / "wrong"), "claims Git path"),
            ("ciu_root_offset", "nested", "claims CIU-root offset"),
            ("branch", "wrong", "claims branch"),
        ]
        for key, value, message in mutations:
            changed = dict(original)
            changed[key] = value
            record.record_path.write_text(json.dumps(changed), encoding="utf-8")
            assert worktree.list_instance_records(tmp_repo) == []
            assert message in capsys.readouterr().err
            with pytest.raises(worktree.WorktreeError, match=message):
                worktree.find_instance_record(tmp_repo, record.logical_name)
        record.record_path.write_text(json.dumps(original), encoding="utf-8")

    def test_family_scan_rejects_duplicate_logical_identity(
        self, tmp_repo, fake_generate_env, capsys
    ):
        first = worktree.create(tmp_repo, "logical-one")
        second = worktree.create(tmp_repo, "logical-two")
        raw = json.loads(second.record_path.read_text(encoding="utf-8"))
        raw["logical_name"] = first.logical_name
        second.record_path.write_text(json.dumps(raw), encoding="utf-8")
        assert len(worktree.list_instance_records(tmp_repo)) == 2
        assert "duplicate logical" in capsys.readouterr().err
        with pytest.raises(worktree.WorktreeError, match="duplicate logical"):
            worktree.find_instance_record(tmp_repo, first.logical_name)

    def test_rootless_create_round_trips_and_keeps_record_format(
        self, rootless_repo, monkeypatch
    ):
        real_write = worktree._write_instance_record
        writes = []

        def checked_write(record):
            real_write(record)
            assert worktree.read_instance_record(record.record_path) == record
            writes.append(record)

        monkeypatch.setattr(worktree, "_write_instance_record", checked_write)
        first = worktree.create(rootless_repo, "generic-one")
        second = worktree.create(rootless_repo, "generic-two")

        assert [record.state for record in writes] == [
            "allocating", "allocating", "allocating", "ready",
            "allocating", "allocating", "allocating", "ready",
        ]
        for record in (first, second):
            assert record.state == "ready"
            assert record.instance_id is None
            assert record.network is None
            raw = json.loads(record.record_path.read_text(encoding="utf-8"))
            assert raw["runtime"] == {"instance_id": None, "network": None}
            assert worktree.read_instance_record(record.record_path) == record

    def test_ciu_root_create_round_trips_every_written_state(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_write = worktree._write_instance_record
        writes = []

        def checked_write(record):
            real_write(record)
            assert worktree.read_instance_record(record.record_path) == record
            writes.append(record)

        monkeypatch.setattr(worktree, "_write_instance_record", checked_write)
        ready = worktree.create(tmp_repo, "ciu-root-one")

        assert ready.state == "ready"
        assert ready.instance_id
        assert ready.network
        assert [record.state for record in writes] == [
            "allocating", "allocating", "allocating", "allocating",
            "allocating", "ready",
        ]

    def test_ciu_root_ready_record_still_rejects_null_identity(self, tmp_path):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
        path = checkout / worktree.WORKTREE_INSTANCE_RECORD
        raw = self.raw(tmp_path)
        raw["runtime"] = {"instance_id": None, "network": None}

        with pytest.raises(worktree.WorktreeError, match="ready record"):
            worktree._record_from_dict(raw, path)

    def test_unreadable_sibling_does_not_block_create_list_lease_branches_reap_or_rm(
        self, rootless_repo, capsys, monkeypatch
    ):
        rootless = worktree.create(rootless_repo, "rootless")
        damaged = worktree.create(rootless_repo, "damaged")
        damaged.record_path.write_text('{"logical_name":"damaged",\n', encoding="utf-8")

        # An unrelated malformed sibling is visible but does not prevent a
        # fresh allocation or a complete listing of readable records.
        created = worktree.create(rootless_repo, "new-instance")
        assert created.state == "ready"
        document = worktree.list_instances(rootless_repo)
        assert {row["instance"]["logical_name"] for row in document["instances"]} == {
            "rootless", "new-instance",
        }
        assert "damaged" in capsys.readouterr().err

        inspected = worktree.inspect_instance(rootless_repo, "rootless")
        assert inspected["git"]["registered"] is True
        assert "damaged" in capsys.readouterr().err

        # The rootless record supports record-only lease handling. Branch
        # hygiene protects the unreadable checkout as possibly managed.
        released = worktree.apply_lease(rootless_repo, "rootless", release=True)
        assert released.instance_id is None
        branches = worktree.branch_hygiene(rootless_repo)
        damaged_branch = next(row for row in branches["branches"] if row["name"] == damaged.branch)
        assert damaged_branch["category"] == "managed-instance"

        monkeypatch.setattr(worktree, "_reap_docker_rows", lambda *_a, **_kw: [])
        reap = worktree.reap_groups(rootless_repo, yes=True, dry_run=True)
        assert reap["status"] == "dry-run"
        assert not reap["identity_complete"]
        assert str(damaged.git_worktree_path) in reap["unresolved_checkouts"]
        assert str(rootless.git_worktree_path) not in reap["unresolved_checkouts"]

        # A corrupt sibling at another path does not block clean Git removal
        # of this rootless managed checkout. Its malformed record remains.
        removed = worktree.remove(rootless_repo, "rootless", yes=True)
        assert removed == rootless.git_worktree_path
        assert not rootless.git_worktree_path.exists()
        assert damaged.record_path.exists()

    def test_corrupt_record_at_requested_name_or_path_refuses_create_and_rm(
        self, tmp_repo, fake_generate_env
    ):
        damaged = worktree.create(tmp_repo, "damaged")
        raw = json.loads(damaged.record_path.read_text(encoding="utf-8"))
        raw.pop("branch")
        damaged.record_path.write_text(json.dumps(raw), encoding="utf-8")

        with pytest.raises(worktree.WorktreeError, match="requested logical identity"):
            worktree.create(tmp_repo, "damaged")
        with pytest.raises(worktree.WorktreeError, match="requested checkout path"):
            worktree.remove(tmp_repo, str(damaged.git_worktree_path), yes=True)
        assert damaged.git_worktree_path.exists()

    def test_corrupt_record_at_explicit_create_path_refuses_before_git_changes(
        self, tmp_repo, fake_generate_env
    ):
        damaged = worktree.create(tmp_repo, "occupied-path")
        raw = json.loads(damaged.record_path.read_text(encoding="utf-8"))
        raw.pop("branch")
        damaged.record_path.write_text(json.dumps(raw), encoding="utf-8")
        branch_before = worktree._git(
            ["branch", "--list"], tmp_repo
        ).stdout

        with pytest.raises(worktree.WorktreeError, match="requested checkout path"):
            worktree.create(
                tmp_repo,
                "different-logical-name",
                path=damaged.git_worktree_path,
            )

        assert damaged.git_worktree_path.exists()
        assert worktree._git(["branch", "--list"], tmp_repo).stdout == branch_before

    def test_family_scan_counts_stat_failure_without_blocking_other_reads(
        self, tmp_repo, fake_generate_env, monkeypatch, capsys,
    ):
        record = worktree.create(tmp_repo, "stat-denied")
        record_path = record.record_path
        original_stat = Path.stat

        def denied(path, *args, **kwargs):
            if path == record_path:
                raise PermissionError("permission denied")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", denied)
        assert worktree.list_instance_records(tmp_repo) == []
        assert "could not inspect instance record" in capsys.readouterr().err

    def test_family_scan_reports_nonfile_and_unhinted_corrupt_record(
        self, tmp_repo, fake_generate_env, capsys,
    ):
        record = worktree.create(tmp_repo, "malformed")
        record.record_path.unlink()
        record.record_path.mkdir()
        assert worktree.list_instance_records(tmp_repo) == []
        assert "is not a file" in capsys.readouterr().err

        record.record_path.rmdir()
        record.record_path.write_text("{}", encoding="utf-8")
        scan = worktree._scan_instance_records(tmp_repo)
        assert len(scan.problems) == 1 and scan.problems[0].logical_name is None
        assert worktree.list_instance_records(tmp_repo) == []
        assert "malformed" in capsys.readouterr().err

    def test_family_scan_isolates_unexpected_validation_exception(
        self, tmp_repo, fake_generate_env, monkeypatch, capsys,
    ):
        worktree.create(tmp_repo, "validator-error")
        monkeypatch.setattr(
            worktree, "_registered_record_problem",
            lambda *_a: (_ for _ in ()).throw(RuntimeError("validator failed")),
        )
        assert worktree.list_instance_records(tmp_repo) == []
        assert "could not validate instance record" in capsys.readouterr().err


class TestManagedHelperRefusals:
    def test_shared_infra_config_rejects_wrong_table_and_scalar_types(self):
        for config, message in [
            ({"ciu": []}, r"\[ciu\]"),
            ({"ciu": {"instance": []}}, r"\[ciu.instance\]"),
            ({"ciu": {"instance": {"shared_infra": []}}}, "shared_infra.*table"),
            ({"ciu": {"instance": {"shared_infra": {
                "ref_path": 4, "network": "n", "services": ["s"], "ref_projects": ["p"]
            }}}}, "ref_path"),
            ({"ciu": {"instance": {"shared_infra": {
                "ref_path": "/x", "network": "", "services": ["s"], "ref_projects": ["p"]
            }}}}, "network"),
        ]:
            with pytest.raises(worktree.WorktreeError, match=message):
                worktree.parse_shared_infra_config(config)

    def test_overlay_refuses_existing_and_reports_write_failure(self, tmp_path, monkeypatch):
        path = tmp_path / "ciu.global.instance.toml.j2"
        path.write_text("[ciu.instance]\n", encoding="utf-8")
        with pytest.raises(worktree.WorktreeError, match="refusing to overwrite"):
            worktree._write_worktree_overlay(tmp_path, "core", None)
        path.unlink()
        monkeypatch.setattr(
            worktree.os, "link",
            lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not write.*denied"):
            worktree._write_worktree_overlay(tmp_path, "core", None)

    def test_overlay_publish_does_not_clobber_file_created_after_preflight(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        real_link = worktree.os.link

        def race_create(source, target):
            Path(target).write_text("operator-owned\n", encoding="utf-8")
            return real_link(source, target)

        monkeypatch.setattr(worktree.os, "link", race_create)
        with pytest.raises(worktree.WorktreeError, match="refusing to overwrite"):
            worktree._write_worktree_overlay(tmp_path, "core", None)
        assert path.read_text(encoding="utf-8") == "operator-owned\n"

    def test_overlay_can_hold_shared_intent_without_profile_at_helper_boundary(self):
        intent = worktree.SharedInfraIntent(
            ref_path=Path("/ref"), network="net", services=("api",),
            ref_projects=("project",),
        )
        text = worktree._worktree_overlay_text(None, intent)
        assert text is not None and "shared_infra" in text

    def test_docker_network_probe_distinguishes_absence_failure_and_match(self, monkeypatch):
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError()),
        )
        assert worktree._docker_network_exists("wanted") is False
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("permission denied")),
        )
        with pytest.raises(worktree.RuntimeNetworkProbeError, match="permission denied"):
            worktree._docker_network_exists("wanted")
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda *_a, **_kw: subprocess.CompletedProcess([], 1, "", "denied"),
        )
        with pytest.raises(worktree.WorktreeError, match="uniqueness.*denied"):
            worktree._docker_network_exists("wanted")
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda *_a, **_kw: subprocess.CompletedProcess([], 0, "other\nwanted\n", ""),
        )
        assert worktree._docker_network_exists("wanted") is True
        assert worktree._docker_network_exists("missing") is False

    def test_docker_network_probe_uses_substring_filter_and_exact_membership(
        self, monkeypatch
    ):
        calls = []

        def docker(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(
                argv, 0, "prefix-wanted-suffix\nwanted\n", ""
            )

        monkeypatch.setattr(worktree.procutil, "docker", docker)
        assert worktree._docker_network_exists("wanted") is True
        assert worktree._docker_network_exists("prefix-wanted-suffix") is True
        assert worktree._docker_network_exists("prefix") is False
        assert [call[0][3] for call in calls] == [
            "name=wanted", "name=prefix-wanted-suffix", "name=prefix",
        ]

    def test_docker_network_details_read_the_exact_object_id_and_labels(
        self, monkeypatch
    ):
        calls = []

        def docker(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ["network", "ls"]:
                return subprocess.CompletedProcess(argv, 0, "wanted\n", "")
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([{
                    "Name": "wanted",
                    "Id": "a" * 64,
                    "Labels": {worktree._NETWORK_OWNER_LABEL: "instance-7"},
                }]),
                "",
            )

        monkeypatch.setattr(worktree.procutil, "docker", docker)

        assert worktree._docker_network_details("wanted") == {
            "id": "a" * 64,
            "labels": {worktree._NETWORK_OWNER_LABEL: "instance-7"},
        }
        assert calls[1][:3] == ["network", "inspect", "wanted"]

    def test_runtime_identity_reader_rejects_missing_and_malformed_env(
        self, tmp_path, write_instance_facts
    ):
        """CIU-75 — the reader's source is the generated facts file, so both
        refusals are proven against THAT file, not against `ciu.env`."""
        (tmp_path / "ciu.instance.generated.toml").write_text(
            "[ciu.instance.generated]\ninstance_id = not-a-toml-value\n",
            encoding="utf-8",
        )
        with pytest.raises(worktree.WorktreeError, match="could not read generated"):
            worktree._runtime_identity(tmp_path)
        write_instance_facts(tmp_path, instance_id="only")
        with pytest.raises(worktree.WorktreeError, match="lacks instance_id"):
            worktree._runtime_identity(tmp_path)

    def test_local_exclude_handles_non_newline_and_reports_write_failure(
        self, tmp_repo, monkeypatch
    ):
        raw_common = _git(["rev-parse", "--git-common-dir"], tmp_repo).stdout.strip()
        common = Path(raw_common)
        if not common.is_absolute():
            common = (tmp_repo / common).resolve()
        exclude = common / "info" / "exclude"
        exclude.write_text("existing-pattern", encoding="utf-8")
        worktree._ensure_record_is_excluded(tmp_repo, Path("."))
        assert exclude.read_text(encoding="utf-8").endswith(
            "existing-pattern\n/ciu.worktree-instance.json\n"
        )
        exclude.write_text("", encoding="utf-8")
        worktree._ensure_record_is_excluded(tmp_repo, Path("nested"))
        assert exclude.read_text(encoding="utf-8") == "/nested/ciu.worktree-instance.json\n"
        monkeypatch.setattr(
            worktree.Path, "read_text",
            lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not exclude.*denied"):
            worktree._ensure_record_is_excluded(tmp_repo, Path("other"))

    def test_allocation_lock_reports_open_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(worktree, "_git_common_dir", lambda _root: tmp_path)
        real_open = builtins.open

        def fail_lock(path, *args, **kwargs):
            if Path(path).name == "ciu-worktree-allocation.lock":
                raise PermissionError("denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fail_lock)
        with pytest.raises(worktree.WorktreeError, match="allocation lock.*denied"):
            with worktree._allocation_lock(tmp_path):
                pass


class TestAdoptRefusals:
    def test_adopt_invalid_overlay_refuses_before_record_or_exclusion_write(
        self, tmp_repo
    ):
        target = tmp_repo / ".worktrees" / "invalid-profile-adopt"
        assert _git(
            ["worktree", "add", "-b", "invalid-profile-adopt", str(target), "main"],
            tmp_repo,
        ).returncode == 0
        before_head = _git(["rev-parse", "HEAD"], target).stdout.strip()
        common = worktree._git_common_dir(tmp_repo)
        exclude = common / "info" / "exclude"
        exclude_before = exclude.read_bytes() if exclude.exists() else None
        shared = worktree._shared_worktree()
        shared_before = shared.list_workspaces(common)

        with pytest.raises(worktree.WorktreeError, match="blank items"):
            worktree.adopt(
                tmp_repo, "invalid-profile-adopt", str(target), profile="core,,db"
            )

        assert _git(["rev-parse", "HEAD"], target).stdout.strip() == before_head
        assert worktree.find_instance_record(tmp_repo, "invalid-profile-adopt") is None
        assert worktree._shared_record_for_checkout(target) is None
        assert not (target / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES).exists()
        assert shared.list_workspaces(common) == shared_before
        assert (exclude.read_bytes() if exclude.exists() else None) == exclude_before

    def test_adopt_refuses_partial_options_unknown_primary_and_managed(
        self, tmp_repo, fake_generate_env
    ):
        with pytest.raises(worktree.WorktreeError, match="all-or-nothing"):
            worktree.adopt(tmp_repo, "logical", "missing", shared_infra="ref")
        with pytest.raises(worktree.WorktreeError, match="not a registered"):
            worktree.adopt(tmp_repo, "logical", "missing")
        with pytest.raises(worktree.WorktreeError, match="non-primary"):
            worktree.adopt(tmp_repo, "logical", str(tmp_repo))

        managed = worktree.create(tmp_repo, "managed")
        with pytest.raises(worktree.WorktreeError, match="already has a managed"):
            worktree.adopt(tmp_repo, "different-logical", str(managed.git_worktree_path))
        with pytest.raises(worktree.WorktreeError, match="already managed"):
            worktree.adopt(tmp_repo, "managed", str(managed.git_worktree_path))

    def test_adopt_refuses_head_failure_and_existing_overlay_replacement(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        target = tmp_repo / ".worktrees" / "existing"
        assert _git(["worktree", "add", "-b", "existing", str(target), "main"], tmp_repo).returncode == 0
        real_git = worktree._git

        def fail_head(args, cwd):
            if args == ["rev-parse", "HEAD"] and Path(cwd) == target:
                return subprocess.CompletedProcess(args, 1, "", "no head")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", fail_head)
        with pytest.raises(worktree.WorktreeError, match="derive adopted.*no head"):
            worktree.adopt(tmp_repo, "head-fails", str(target))

        monkeypatch.setattr(worktree, "_git", real_git)
        (target / "ciu.global.instance.toml.j2").write_text(
            "[ciu.instance]\nservice_profiles = [\"existing\"]\n", encoding="utf-8"
        )
        with pytest.raises(worktree.WorktreeError, match="refusing to replace"):
            worktree.adopt(tmp_repo, "overlay-conflict", str(target), profile="core")

    def test_adopt_shared_infra_intent_is_written(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        target = tmp_repo / ".worktrees" / "existing"
        assert _git(["worktree", "add", "-b", "existing", str(target), "main"], tmp_repo).returncode == 0
        intent = worktree.SharedInfraIntent(
            ref_path=tmp_repo, network="reference-network",
            services=("api",), ref_projects=("reference-project",),
        )
        monkeypatch.setattr(
            worktree, "_preflight_shared_infra_for_add", lambda *_a, **_kw: intent
        )
        adopted = worktree.adopt(
            tmp_repo, "logical", str(target), profile="core",
            shared_infra="ref", shared_infra_services="api",
            shared_infra_ref_projects="reference-project",
        )
        assert "reference-network" in (
            adopted.ciu_root / "ciu.global.instance.toml.j2"
        ).read_text(encoding="utf-8")


class TestWorktreeSubprocessEnvironment:
    # CIU-85: PUBLIC_FQDN added — one of the six `[ciu.instance.generated]`
    # identity facts since CIU-47, previously absent from both this list AND
    # the production `_CIU_IDENTITY_ENV_KEYS` it mirrors.
    _IDENTITY_KEYS = (
        "REPO_ROOT",
        "PHYSICAL_REPO_ROOT",
        "DOCKER_NETWORK_INTERNAL",
        "INSTANCE_ID",
        "REPO_NAME",
        "PUBLIC_FQDN",
        "CIU_SERVICES_PROFILE",
    )

    def test_identity_env_keys_match_the_canonical_fact_table_plus_profile(self):
        """CIU-85: `_CIU_IDENTITY_ENV_KEYS` is DERIVED from
        `GENERATED_FACT_ENV_KEYS.values()` (the canonical fact->env-name
        table) plus the one hand-added non-fact member, `CIU_SERVICES_PROFILE`
        — not a second, independently hand-maintained literal. Pinned here so
        a future fact added to the canonical table joins this tuple BY
        CONSTRUCTION, the exact property this fix exists to establish."""
        from ciu.workspace_env import GENERATED_FACT_ENV_KEYS

        assert set(worktree._CIU_IDENTITY_ENV_KEYS) == set(
            GENERATED_FACT_ENV_KEYS.values()
        ) | {"CIU_SERVICES_PROFILE"}
        assert "PUBLIC_FQDN" in worktree._CIU_IDENTITY_ENV_KEYS

    def test_generate_env_strips_primary_instance_identity(self, tmp_path, monkeypatch):
        for key in self._IDENTITY_KEYS:
            monkeypatch.setenv(key, "primary-value")
        seen: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._generate_env_in(tmp_path) == 0
        env = seen["env"]
        assert isinstance(env, dict)
        for key in self._IDENTITY_KEYS:
            assert key not in env
        assert seen["cwd"] == str(tmp_path)
        assert seen["check"] is False

    def test_identity_only_generation_uses_non_bootstrap_child_mode(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._generate_env_in(tmp_path, identity_only=True) == 0
        assert seen["argv"][-1] == "--identity-only"

    def test_allocation_lock_generation_uses_private_direct_entrypoint(
        self, tmp_path, monkeypatch,
    ):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._generate_env_in(tmp_path, allocation_lock_held=True) == 0
        argv = seen["argv"]
        assert argv[1] == "-c"
        assert "--allocation-lock-held" not in argv
        assert "_generate_env_with_lock_held" in argv[2]

    def test_locked_env_generation_keeps_cli_error_diagnostic(
        self, tmp_path, monkeypatch, capsys,
    ):
        from ciu import deploy
        from ciu.workspace_env import WorkspaceEnvError

        def fail_generation(*_args, **_kwargs):
            raise WorkspaceEnvError("workspace identity is malformed")

        monkeypatch.setattr(deploy, "action_generate_env", fail_generation)
        monkeypatch.chdir(tmp_path)

        assert worktree._generate_env_with_lock_held() == 1
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "[ERROR] workspace identity is malformed\n"

    def test_clean_strips_the_callers_service_profile_selection(
        self, tmp_path, monkeypatch, write_instance_facts
    ):
        """CIU-85: `_clean_in` did not strip `_CIU_IDENTITY_ENV_KEYS` before
        overlaying the target's identity facts, unlike its two siblings
        (`_sanitized_target_env`, `_resolve_budget_candidates`). Neutralized
        for the five overlay-fact keys (`identity` always overwrites them
        once the empty-table refusal above has already fired), but
        `CIU_SERVICES_PROFILE` is NOT an overlay fact — it is never in
        `identity` — so it used to leak straight through from the CALLER's
        ambient environment into the child `ciu clean`. This is the
        controlled-wrong-implementation proof: reverting the strip (`env =
        dict(os.environ); env.update(identity)`) makes this assertion fail
        with `env["CIU_SERVICES_PROFILE"] == "primary-profile"`.
        """
        write_instance_facts(tmp_path, repo_root="/target", instance_id="target-id")
        monkeypatch.setenv("CIU_SERVICES_PROFILE", "primary-profile")
        seen: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._clean_in(tmp_path, yes=True) == 0
        env = seen["env"]
        assert "CIU_SERVICES_PROFILE" not in env

    def test_clean_strips_a_stale_ambient_public_fqdn_not_carried_by_target(
        self, tmp_path, monkeypatch
    ):
        """CIU-85's `PUBLIC_FQDN` half, made genuinely discriminating.

        An earlier version of this test used `write_instance_facts(...,
        public_fqdn="")`, which — per that fixture's own contract — backfills
        EVERY `GENERATED_FACTS_KEYS` member, so `identity` always carried an
        explicit `PUBLIC_FQDN` key (`""`) regardless of whether `_clean_in`'s
        strip ran at all: `env.update(identity)` alone was already enough to
        overwrite the ambient value. A fresh adversarial review (2026-08-31)
        hand-reverted just the strip and reproduced that the test still
        passed — a real gap, not a nitpick: this test added no discriminating
        power over the overlay-fact path already proven by
        `test_clean_uses_target_worktree_env_not_primary` and friends.

        Fixed by monkeypatching `read_instance_identity_env` directly to
        return a target identity dict that OMITS `PUBLIC_FQDN` entirely —
        simulating an overlay record that predates CIU-47 (or is otherwise
        missing the key), which `identity_env_from_facts` faithfully drops
        rather than backfilling. Now the STRIP is the only thing standing
        between the ambient `PUBLIC_FQDN` and the leak: reverting `_clean_in`
        back to `env = dict(os.environ)` makes this assertion fail with
        `env["PUBLIC_FQDN"] == "primary.example.com"` — manually reverted and
        confirmed.
        """
        from ciu import workspace_env

        monkeypatch.setattr(
            workspace_env,
            "read_instance_identity_env",
            lambda _root: {"REPO_ROOT": "/target", "INSTANCE_ID": "target-id"},
        )
        monkeypatch.setenv("PUBLIC_FQDN", "primary.example.com")
        seen: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._clean_in(tmp_path, yes=True) == 0
        env = seen["env"]
        assert "PUBLIC_FQDN" not in env

    def test_clean_uses_target_worktree_env_not_primary(
        self, tmp_path, monkeypatch, write_instance_facts
    ):
        write_instance_facts(tmp_path, repo_root="/target", instance_id="target-id")
        monkeypatch.setenv("REPO_ROOT", "/primary")
        seen: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)
        assert worktree._clean_in(tmp_path, yes=True) == 0
        env = seen["env"]
        assert isinstance(env, dict)
        assert env["REPO_ROOT"] == "/target"
        assert seen["argv"][-1] == "-y"

    def test_clean_refuses_to_guess_when_target_env_is_missing(self, tmp_path):
        with pytest.raises(
            worktree.WorktreeError, match="carries no generated instance identity"
        ):
            worktree._clean_in(tmp_path, yes=False)

    def test_clean_refuses_when_no_generated_facts_exist(
        self, tmp_path
    ):
        """CIU-75 — a checkout that was never through `ciu env generate` is NOT
        an identity, and (ciu-P47) the operator's own instance overlay does not
        become one however much it carries."""
        (tmp_path / "ciu.global.instance.toml.j2").write_text(
            '[ciu]\nservice_profiles = ["core"]\n', encoding="utf-8"
        )
        with pytest.raises(
            worktree.WorktreeError, match="carries no generated instance identity"
        ):
            worktree._clean_in(tmp_path, yes=False)

    def test_clean_surfaces_unreadable_target_env(self, tmp_path, monkeypatch):
        (tmp_path / "ciu.instance.generated.toml").mkdir()
        with pytest.raises(worktree.WorktreeError, match="could not read"):
            worktree._clean_in(tmp_path, yes=False)

    def test_clean_surfaces_a_malformed_target_env_entry(self, tmp_path):
        """CIU-62 — the COMMON failure this site used to miss. `except
        OSError` alone let `WorkspaceEnvError` (a `ValueError` subclass)
        escape as a raw traceback, and this function exists precisely so a
        clean never runs under a half-known identity. CIU-75 moved the source
        to the generated facts; the refusal contract is unchanged."""
        (tmp_path / "ciu.instance.generated.toml").write_text(
            "[ciu.instance.generated]\nrepo_root = not-a-toml-value\n",
            encoding="utf-8",
        )
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\] could not read"):
            worktree._clean_in(tmp_path, yes=False)

    def test_clean_surfaces_a_non_utf8_target_env(self, tmp_path):
        """CIU-62 — the byte-level half: `UnicodeDecodeError` is a SIBLING of
        `WorkspaceEnvError` under `ValueError`, so naming either one alone
        still leaves this open."""
        (tmp_path / "ciu.instance.generated.toml").write_bytes(
            b'[ciu.instance.generated]\nrepo_root = "\xff\xfe"\n'
        )
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\] could not read"):
            worktree._clean_in(tmp_path, yes=False)


class TestBestEffortCleanupArcs:
    """The three arcs formerly hidden behind `pragma: no cover` (from
    checkpoint 71f5ec79) — reachable, so tested rather than excluded: the
    best-effort tmp-unlink OSError arcs in the two atomic writers and the
    defensive ambiguous-identity arc in find_instance_record."""

    def _record(self, tmp_path: Path) -> worktree.WorktreeInstanceRecord:
        return worktree._record_from_dict(
            {
                "schema_version": 1,
                "logical_name": "logical-one",
                "display_name": "display-one",
                "branch": "display-one",
                "git_worktree_path": str(tmp_path),
                "ciu_root_offset": ".",
                "created_at_utc": "2026-08-17T12:00:00Z",
                "base_ref": "main",
                "state": "allocating",
                "runtime": {"instance_id": None, "network": None},
                "recovery_status": None,
            },
            tmp_path / "source",
        )

    def _replace_fails(self, monkeypatch) -> None:
        monkeypatch.setattr(
            worktree.os, "replace",
            lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
        )

    def _unlink_fails(self, monkeypatch) -> None:
        monkeypatch.setattr(
            Path, "unlink",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("unlink denied")),
        )

    def test_record_write_unlink_failure_is_best_effort(self, tmp_path, monkeypatch):
        """os.replace fails AND the tmp cleanup unlink fails too — the inner
        `except OSError: pass` must swallow the cleanup failure and still
        surface the atomic-write error."""
        self._replace_fails(monkeypatch)
        self._unlink_fails(monkeypatch)
        with pytest.raises(worktree.WorktreeError, match="atomically write"):
            worktree._write_instance_record(self._record(tmp_path))

    def test_overlay_write_unlink_failure_is_best_effort(self, tmp_path, monkeypatch):
        """Same double-failure arc for the worktree-overlay writer."""
        monkeypatch.setattr(
            worktree.os, "link",
            lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
        )
        self._unlink_fails(monkeypatch)
        with pytest.raises(worktree.WorktreeError, match="could not write"):
            worktree._write_worktree_overlay(tmp_path, "core", None)

    def test_overlay_write_succeeds_when_post_publish_tmp_cleanup_fails(
        self, tmp_path, monkeypatch
    ):
        self._unlink_fails(monkeypatch)
        worktree._write_worktree_overlay(tmp_path, "core", None)
        path = tmp_path / worktree.GLOBAL_CONFIG_INSTANCE_OVERRIDES
        assert path.is_file()
        assert 'service_profiles = ["core"]' in path.read_text(encoding="utf-8")

    def test_find_instance_record_ambiguous_constructed_duplicate(
        self, tmp_path, monkeypatch
    ):
        """len(matches) > 1 is defensive (list_instance_records refuses first),
        so it is exercised with a constructed duplicate-record list."""
        rec = worktree.WorktreeInstanceRecord(
            logical_name="dup", display_name="a", branch="a",
            git_worktree_path=tmp_path / "a", ciu_root_offset=Path("."),
            created_at_utc="2026-08-17T12:00:00Z", base_ref="main",
            state="ready", instance_id="x", network="y",
        )
        monkeypatch.setattr(
            worktree, "list_instance_records", lambda repo_root, **kwargs: [rec, rec]
        )
        with pytest.raises(worktree.WorktreeError, match="ambiguous logical identity"):
            worktree.find_instance_record(tmp_path, "dup")


class TestStructuredControlDocuments:
    """S16.4/S16.5 — versioned JSON documents, removal envelope, and the
    closed capability allowlist (D-009)."""

    def _record(self, tmp_path: Path, logical: str = "logical-one") -> worktree.WorktreeInstanceRecord:
        return worktree.WorktreeInstanceRecord(
            logical_name=logical, display_name=logical, branch=logical,
            git_worktree_path=tmp_path / ".worktrees" / logical,
            ciu_root_offset=Path("."), created_at_utc="2026-08-17T12:00:00Z",
            base_ref="main", state="ready", instance_id="abc123",
            network="repo-abc123-network",
        )

    # -- capabilities (O3) ---------------------------------------------------

    def test_capabilities_document_is_versioned_and_closed(self):
        doc = worktree.capabilities_document()
        assert doc["schema_version"] == 1
        assert doc["capabilities"] == sorted(worktree.WORKTREE_CAPABILITIES)
        assert doc["capabilities"] == [
            "worktree.branches.v1",
            "worktree.exec-local.v1",
            "worktree.exec-target.v1",
            "worktree.identity.v1",
            "worktree.inspect.v1",
            # ciu-P27: the lease (shipped by ciu-P26) and the reap verb that
            # reads it are advertised together — a consumer that can reap must
            # be able to declare a lease first.
            "worktree.lease.v1",
            "worktree.lifecycle-json.v1",
            "worktree.reap.v1",
            "worktree.up.v1",
        ]

    def test_capabilities_advertise_exactly_the_shipped_contracts(self):
        assert set(worktree.WORKTREE_CAPABILITIES) == {
            "worktree.branches.v1",
            "worktree.identity.v1",
            "worktree.inspect.v1",
            "worktree.lease.v1",
            "worktree.lifecycle-json.v1",
            "worktree.reap.v1",
            "worktree.up.v1",
            "worktree.exec-local.v1",
            "worktree.exec-target.v1",
        }
        assert not any("future" in c for c in worktree.WORKTREE_CAPABILITIES)

    # -- document builder (O1/O2) -------------------------------------------

    def test_build_instance_document_without_git_facts_is_lifecycle_envelope(self, tmp_path):
        record = self._record(tmp_path)
        doc = worktree.build_instance_document("create", record)
        assert doc == {
            "schema_version": 2,
            "operation": "create",
            "status": "ready",
            "instance": record.to_dict(),
        }
        assert "git" not in doc

    def test_build_instance_document_rejects_unknown_operation(self, tmp_path):
        with pytest.raises(worktree.WorktreeError, match="closed vocabulary"):
            worktree.build_instance_document("explode", self._record(tmp_path))

    # -- inspect (O1) --------------------------------------------------------

    def test_inspect_document_reports_fresh_git_facts(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "logical-one")
        doc = worktree.inspect_instance(tmp_repo, "logical-one")
        assert doc["schema_version"] == 2
        assert doc["operation"] == "inspect"
        assert doc["status"] == "ready"
        assert doc["instance"]["logical_name"] == "logical-one"
        git = doc["git"]
        assert git == {
            "registered": True,
            "path": str(record.git_worktree_path),
            "branch": record.branch,
            "detached": False,
            "primary": False,
            "head": git["head"],
            "dirty": False,
        }

    def test_inspect_reports_dirty_worktree(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "logical-one")
        (record.git_worktree_path / "scratch.txt").write_text("x", encoding="utf-8")
        assert worktree.inspect_instance(tmp_repo, "logical-one")["git"]["dirty"] is True

    def test_inspect_unknown_name_refuses(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="no managed worktree instance"):
            worktree.inspect_instance(tmp_repo, "ghost")

    def test_inspect_unreadable_git_status_refuses(self, tmp_repo, fake_generate_env, monkeypatch):
        worktree.create(tmp_repo, "logical-one")
        real_git = worktree._git

        def fail_status(args, cwd):
            if args and args[0] == "status":
                return subprocess.CompletedProcess(["git"], 1, "", "corrupt repo")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", fail_status)
        with pytest.raises(worktree.WorktreeError, match="could not read git status"):
            worktree.inspect_instance(tmp_repo, "logical-one")

    def test_current_git_facts_refuses_unregistered_path(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "logical-one")
        phantom = worktree.replace(record, git_worktree_path=tmp_repo / "phantom")
        with pytest.raises(worktree.WorktreeError, match="no longer registers"):
            worktree._current_git_facts(tmp_repo, phantom)

    # -- managed list (O1) ---------------------------------------------------

    def test_list_instances_empty(self, tmp_repo):
        doc = worktree.list_instances(tmp_repo)
        assert doc["schema_version"] == 2
        assert doc["operation"] == "list"
        assert doc["status"] == "ready"
        assert doc["instances"] == []

    def test_list_instances_includes_managed_with_git_facts(self, tmp_repo, fake_generate_env):
        worktree.create(tmp_repo, "logical-one")
        doc = worktree.list_instances(tmp_repo)
        assert len(doc["instances"]) == 1
        entry = doc["instances"][0]
        assert entry["operation"] == "inspect"
        assert entry["status"] == "ready"
        assert entry["instance"]["logical_name"] == "logical-one"
        assert entry["git"]["dirty"] is False

    # -- removal (O2) --------------------------------------------------------

    def test_remove_document_managed_success(self, tmp_repo, fake_generate_env, monkeypatch):
        create_path(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        doc = worktree.remove_document(tmp_repo, "wt1")
        assert doc["schema_version"] == 2
        assert doc["operation"] == "remove"
        assert doc["status"] == "removed"
        assert doc["removed_path"] == str(tmp_repo / ".worktrees" / "wt1")
        assert doc["instance"]["logical_name"] == "wt1"
        assert doc["phase_report"] == {
            "schema_version": 2,
            "phases": {
                "target_resolution": "complete",
                "resource_cleanup": "complete",
                "lease_release": "complete",
                "lifecycle_preflight": "complete",
                "checkout_deletion": "complete",
                "privileged_retry": "skipped",
                "git_registration": "complete",
                "branch_removal": "deleted",
                "ciu_record_removal": "complete",
                "shared_record_removal": "complete",
            },
        }
        assert not (tmp_repo / ".worktrees" / "wt1").exists()

    def test_remove_document_unmanaged_omits_instance(self, tmp_repo, monkeypatch):
        target = tmp_repo / "unmanaged"
        assert _git(
            ["worktree", "add", "--no-checkout", "-b", "unmanaged", str(target), "main"],
            tmp_repo,
        ).returncode == 0
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        doc = worktree.remove_document(
            tmp_repo, "unmanaged", force=True, discard_changes="unmanaged"
        )
        assert doc["operation"] == "remove"
        assert doc["status"] == "removed"
        assert "instance" not in doc
        assert doc["removed_path"] == str(target)
        assert doc["phase_report"]["phases"]["shared_record_removal"] == "created-and-removed"

    def test_remove_document_unknown_target_does_not_assume_no_record(self, tmp_repo):
        with pytest.raises(worktree.WorktreeRemovalError) as exc:
            worktree.remove_document(tmp_repo, "missing")

        assert exc.value.phase_report["phases"]["target_resolution"] == "failed"
        assert exc.value.phase_report["phases"]["ciu_record_removal"] == "skipped"

    def test_remove_document_failed_clean_produces_no_success_document(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        create_path(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeRemovalError, match="NOT removing") as exc:
            worktree.remove_document(tmp_repo, "wt1")
        assert exc.value.document["status"] == "failed"
        assert exc.value.document["phase_report"]["schema_version"] == 2
        assert exc.value.document["phase_report"]["phases"]["target_resolution"] == "complete"
        assert exc.value.document["phase_report"]["phases"]["resource_cleanup"] == "failed"
        assert exc.value.document["phase_report"]["phases"]["checkout_deletion"] == "skipped"
        assert exc.value.document["branch"]["outcome"] == "not-deleted"

    def test_remove_document_preserves_deleted_branch_outcome_after_later_error(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "branch-removed-before-later-error")
        tip = _git(["rev-parse", record.branch], tmp_repo).stdout.strip()
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(
            worktree, "_branch_removal_plan",
            lambda *_args, **_kwargs: {
                "name": record.branch, "tip": tip, "outcome": "deleted",
                "delete": True, "reason": "merged",
            },
        )
        shared = worktree._shared_worktree()
        remove_workspace = shared.remove_workspace

        def fail_after_branch_removal(shared_record, **kwargs):
            report_phase = kwargs["on_phase"]

            def fail_after_delete(phase, status):
                report_phase(phase, status)
                if phase == "branch_removal" and status == "deleted":
                    raise RuntimeError("post-branch failure")

            kwargs["on_phase"] = fail_after_delete
            return remove_workspace(shared_record, **kwargs)

        monkeypatch.setattr(shared, "remove_workspace", fail_after_branch_removal)

        with pytest.raises(
            worktree.WorktreeRemovalError, match="post-branch failure"
        ) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        assert exc.value.document["branch"]["outcome"] == "deleted"
        assert exc.value.phase_report["phases"]["branch_removal"] == "deleted"
        assert not record.git_worktree_path.exists()
        assert _git(
            ["show-ref", "--verify", f"refs/heads/{record.branch}"], tmp_repo
        ).returncode != 0

    @pytest.mark.parametrize(
        ("label_owner", "forge_primary_identity", "removed"),
        [("target", False, True), ("primary", False, False), ("target", True, False)],
    )
    def test_remove_fallback_cleans_only_the_recorded_checkout_identity(
        self,
        tmp_repo,
        fake_generate_env,
        write_instance_facts,
        monkeypatch,
        label_owner,
        forge_primary_identity,
        removed,
    ):
        fake_generate_env(tmp_repo)
        record = worktree.create(tmp_repo, "old-root", base="main")
        marker = record.ciu_root / GLOBAL_CONFIG_DEFAULTS
        assert marker.is_file()
        marker.unlink()
        if not forge_primary_identity:
            record.record_path.unlink()
        primary_id = workspace_env.read_generated_facts(
            tmp_repo, allow_repair=False
        )["instance_id"]
        physical_roots = {
            tmp_repo.resolve(): Path("/physical-host/vbpub"),
            record.ciu_root.resolve(): Path("/physical-host/vbpub/.worktrees/old-root"),
        }
        primary_facts = workspace_env.read_generated_facts(tmp_repo, allow_repair=False)
        target_facts = workspace_env.read_generated_facts(
            record.ciu_root, allow_repair=False
        )
        write_instance_facts(
            tmp_repo,
            **{
                **primary_facts,
                "physical_repo_root": str(physical_roots[tmp_repo.resolve()]),
            },
        )
        write_instance_facts(
            record.ciu_root,
            **{
                **target_facts,
                "physical_repo_root": str(physical_roots[record.ciu_root.resolve()]),
            },
        )
        monkeypatch.setattr(
            workspace_env,
            "_detect_physical_repo_root",
            lambda root: physical_roots[Path(root).resolve()],
        )
        cleanup_calls = []
        owner = str(
            physical_roots[record.ciu_root.resolve()]
            if label_owner == "target" else physical_roots[tmp_repo.resolve()]
        )

        def docker(args, **kwargs):
            cleanup_calls.append(list(args))
            if (
                args[:2] == ["volume", "ls"]
                and f"label=ciu.instance={primary_id if forge_primary_identity else record.instance_id}"
                in args
            ):
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"old-volume\t{owner}\t<no value>\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(deploy.procutil, "docker", docker)

        def clean_from_primary(
            primary_root, instance_id, *, expected_checkout, yes
        ):
            return deploy.action_clean_identity(
                primary_root,
                instance_id,
                expected_checkout=expected_checkout,
                yes=yes,
            )

        monkeypatch.setattr(worktree, "_clean_with_primary_identity", clean_from_primary)
        monkeypatch.setattr(
            worktree, "_clean_in",
            lambda *_args, **_kwargs: pytest.fail("markerless checkout must use primary config"),
        )
        if forge_primary_identity:
            real_find_record = worktree.find_instance_record
            monkeypatch.setattr(
                worktree,
                "find_instance_record",
                lambda *args, **kwargs: replace(
                    real_find_record(*args, **kwargs), instance_id=primary_id
                ),
            )

        if removed:
            document = worktree.remove_document(
                tmp_repo,
                "old-root",
                yes=True,
                keep_branch=True,
                discard_changes=record.branch,
            )
            assert document["phase_report"]["phases"]["resource_cleanup"] == "complete"
            assert not record.git_worktree_path.exists()
        else:
            with pytest.raises(worktree.WorktreeRemovalError):
                worktree.remove_document(
                    tmp_repo,
                    "old-root",
                    yes=True,
                    keep_branch=True,
                    discard_changes=record.branch,
                )
            assert record.git_worktree_path.is_dir()

        volume_removals = [call for call in cleanup_calls if call[:2] == ["volume", "rm"]]
        assert volume_removals == ([ ["volume", "rm", "old-volume"] ] if removed else [])
        if forge_primary_identity:
            assert cleanup_calls == []
        if removed:
            assert _git(["rev-parse", "--verify", record.branch], tmp_repo).returncode == 0

    def test_rm_refuses_protected_assay_state_before_clean_without_exact_disposition(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        (tmp_repo / ".gitignore").write_text(
            (tmp_repo / ".gitignore").read_text(encoding="utf-8") + ".assay/\n",
            encoding="utf-8",
        )
        _git(["add", ".gitignore"], tmp_repo)
        _git(["commit", "-m", "ignore assay state"], tmp_repo)
        record = worktree.create(tmp_repo, "protected-rm", base="main")
        state = record.git_worktree_path / ".assay" / "state" / "resume.json"
        state.parent.mkdir(parents=True)
        state.write_text("resume\n", encoding="utf-8")
        clean_calls = []
        monkeypatch.setattr(
            worktree, "_clean_in",
            lambda *_args, **_kwargs: clean_calls.append(True) or 0,
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="--discard-protected-state") as exc:
            worktree.remove_document(tmp_repo, "protected-rm", yes=True)

        assert "reason=name" in str(exc.value)
        assert state.read_text(encoding="utf-8") == "resume\n"
        assert record.git_worktree_path.is_dir()
        assert clean_calls == []
        assert exc.value.phase_report["phases"]["resource_cleanup"] == "skipped-protected-state"

    def test_rm_discard_protected_state_requires_exact_branch_and_reports_loss(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        (tmp_repo / ".gitignore").write_text(
            (tmp_repo / ".gitignore").read_text(encoding="utf-8") + ".assay/\n",
            encoding="utf-8",
        )
        _git(["add", ".gitignore"], tmp_repo)
        _git(["commit", "-m", "ignore assay state"], tmp_repo)
        record = worktree.create(tmp_repo, "protected-discard", base="main")
        state = record.git_worktree_path / ".assay" / "state" / "resume.json"
        state.parent.mkdir(parents=True)
        state.write_text("resume\n", encoding="utf-8")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        document = worktree.remove_document(
            tmp_repo, "protected-discard", yes=True,
            discard_protected_state=record.branch,
        )

        assay_state = next(
            item for item in document["discarded_state"] if item["path"] == ".assay"
        )
        assert assay_state == {
            "path": ".assay", "class": "protected", "file_count": 1,
            "bytes": len("resume\n"), "capped": False,
            "protected_reason": "name",
        }
        assert document["phase_report"]["phases"]["branch_removal"] == "deleted"
        assert not record.git_worktree_path.exists()

    def test_rm_protects_volume_directory_below_nested_ciu_root(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        nested = tmp_repo / "mattermost-server"
        nested.mkdir()
        (nested / GLOBAL_CONFIG_DEFAULTS).write_text("[ciu]\n", encoding="utf-8")
        (tmp_repo / GLOBAL_CONFIG_DEFAULTS).unlink()
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + "mattermost-server/vol-*/\n",
            encoding="utf-8",
        )
        _git(["add", "-A"], tmp_repo)
        _git(["commit", "-m", "move ciu root below project directory"], tmp_repo)

        record = worktree.create(nested, "nested-volume", base="main")
        data = (
            record.git_worktree_path
            / "mattermost-server"
            / "vol-postgres-data"
            / "database.bin"
        )
        data.parent.mkdir(parents=True)
        data.write_bytes(b"db")
        monkeypatch.setattr(
            worktree, "_clean_in", lambda *_args, **_kwargs: pytest.fail("must refuse first")
        )

        with pytest.raises(
            worktree.WorktreeRemovalError, match="mattermost-server/vol-postgres-data"
        ) as exc:
            worktree.remove_document(nested, record.logical_name, yes=True)

        protected = next(
            item for item in exc.value.document["ignored_state"]
            if item["path"] == "mattermost-server/vol-postgres-data"
        )
        assert protected == {
            "path": "mattermost-server/vol-postgres-data",
            "class": "protected",
            "file_count": 1,
            "bytes": 2,
            "capped": False,
            "protected_reason": "name",
        }
        assert record.git_worktree_path.is_dir()
        assert data.read_bytes() == b"db"

    @pytest.mark.parametrize(
        ("relative", "contents"),
        [
            ("nested/.assay/progress.jsonl", b"progress\n"),
            ("nested/vol-postgres/base/backup.bin", b"stack data\n"),
        ],
    )
    def test_rm_requires_consent_for_protected_state_hidden_by_ignored_parent(
        self, tmp_repo, fake_generate_env, monkeypatch, relative, contents
    ):
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + "cache/\n",
            encoding="utf-8",
        )
        _git(["add", ".gitignore"], tmp_repo)
        _git(["commit", "-m", "ignore scratch caches"], tmp_repo)
        record = worktree.create(tmp_repo, "protected-cache-child", base="main")
        state = record.git_worktree_path / "cache" / relative
        state.parent.mkdir(parents=True)
        state.write_bytes(contents)
        monkeypatch.setattr(
            worktree, "_clean_in",
            lambda *_args, **_kwargs: pytest.fail("must require consent before clean"),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError, match="--discard-protected-state"
        ) as exc:
            worktree.remove_document(
                tmp_repo, record.logical_name, yes=True
            )

        expected_cache_state = {
            "path": "cache", "class": "protected", "file_count": 1,
            "bytes": len(contents), "capped": False,
            "protected_reason": "descendant",
        }
        assert expected_cache_state in exc.value.document["ignored_state"]
        assert state.read_bytes() == contents
        assert record.git_worktree_path.is_dir()

        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        document = worktree.remove_document(
            tmp_repo, record.logical_name, yes=True,
            discard_protected_state=record.branch,
        )
        assert expected_cache_state in document["discarded_state"]
        assert not record.git_worktree_path.exists()

    def test_rm_requires_exact_consent_for_tracked_and_untracked_changes(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        for name, path, contents in (
            ("dirty-tracked", "README.md", "changed\n"),
            ("dirty-untracked", "operator.txt", "untracked\n"),
        ):
            record = worktree.create(tmp_repo, name, base="main")
            change = record.git_worktree_path / path
            change.write_text(contents, encoding="utf-8")
            clean_calls = []
            monkeypatch.setattr(
                worktree, "_clean_in",
                lambda *_args, **_kwargs: clean_calls.append(True) or 0,
            )

            with pytest.raises(
                worktree.WorktreeRemovalError, match=f"uncommitted changes.*{path}"
            ) as exc:
                worktree.remove_document(tmp_repo, name)

            assert exc.value.phase_report["phases"]["resource_cleanup"] == "skipped-dirty-changes"
            assert clean_calls == []
            assert change.is_file()
            document = worktree.remove_document(
                tmp_repo, name, discard_changes=record.branch
            )
            assert document["discarded_changes"] == [path]
            assert document["status"] == "removed"

    @pytest.mark.parametrize(
        ("disposition", "match"),
        [
            ("wrong-branch", "exact checkout branch"),
            ("all", "not 'all'"),
            (["one", "two"], "one exact --discard-changes"),
        ],
    )
    def test_rm_rejects_invalid_change_dispositions(
        self, tmp_repo, fake_generate_env, disposition, match
    ):
        record = worktree.create(tmp_repo, "change-disposition", branch="exact-branch")
        (record.git_worktree_path / "work.txt").write_text("work\n", encoding="utf-8")

        with pytest.raises(worktree.WorktreeRemovalError, match=match):
            worktree.remove_document(
                tmp_repo, record.logical_name, discard_changes=disposition
            )

    def test_rm_accepts_exact_change_disposition_when_checkout_is_clean(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "clean-change-disposition")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        with pytest.raises(worktree.WorktreeRemovalError, match="exact checkout branch"):
            worktree.remove_document(
                tmp_repo, record.logical_name, discard_changes="wrong-branch"
            )
        document = worktree.remove_document(
            tmp_repo, record.logical_name, yes=True,
            discard_changes=record.branch,
        )
        assert document["discarded_changes"] == []
        assert document["status"] == "removed"

    def test_worktree_change_paths_reports_renames_and_skips_only_its_record(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "change-path-parser")
        record_path = record.record_path.relative_to(record.git_worktree_path).as_posix()
        status = (
            " M tracked.txt\0?? untracked.txt\0R  renamed.txt\0old-name.txt\0"
            "C  copied.txt\0source.txt\0?? " + record_path
            + "\0R  unknown-rename.txt\0"
        )
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess([], 0, status, ""),
        )

        assert worktree._worktree_change_paths(record.git_worktree_path, record) == [
            "copied.txt", "old-name.txt", "renamed.txt", "source.txt",
            "tracked.txt", "unknown-rename.txt", "untracked.txt",
        ]

    @pytest.mark.parametrize(
        ("returncode", "stderr", "match"),
        [(1, "status unavailable", "could not inspect uncommitted changes"),
         (0, "", "CIU record .* is outside checkout")],
    )
    def test_worktree_change_paths_refuses_unknown_status_or_external_record(
        self, tmp_repo, fake_generate_env, monkeypatch, returncode, stderr, match
    ):
        record = worktree.create(tmp_repo, "change-path-refusal")
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess([], returncode, "", stderr),
        )
        target_record = (
            replace(record, git_worktree_path=tmp_repo / "elsewhere")
            if returncode == 0 else record
        )

        with pytest.raises(worktree.WorktreeError, match=match):
            worktree._worktree_change_paths(record.git_worktree_path, target_record)

    def test_ignored_inventory_allows_only_a_verified_nested_lease_record_rewrite(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "nested-lease-record")
        leased = replace(
            worktree.acquire_lease(record, ttl_hours=24, holder="test-holder"),
            ciu_root_offset=Path("mattermost-server"),
        )
        initial = [{
            "path": "mattermost-server/ciu.worktree-instance.json",
            "class": "regenerable", "file_count": 1, "bytes": 980,
            "capped": False,
        }]
        latest = [{**initial[0], "bytes": 1080}]

        assert worktree._ignored_state_changed(
            initial, latest, checkout=record.git_worktree_path, record=leased
        ) is False
        assert worktree._ignored_state_changed(
            initial, latest, checkout=record.git_worktree_path,
            record=worktree.release_lease(leased),
        ) is True

    @pytest.mark.parametrize(
        ("initial", "latest", "changed"),
        [
            ([{"path": "old", "class": "unclassified", "file_count": 2,
               "bytes": 8, "capped": False}], [], False),
            ([{"path": "smaller", "class": "protected", "file_count": 3,
               "bytes": 30, "capped": False}],
             [{"path": "smaller", "class": "unclassified", "file_count": 2,
               "bytes": 20, "capped": False}], False),
            ([{"path": "same", "class": "unclassified", "file_count": 1,
               "bytes": 8, "capped": False}],
             [{"path": "same", "class": "protected", "file_count": 1,
               "bytes": 8, "capped": False}], True),
            ([{"path": "same", "class": "protected", "file_count": 1,
               "bytes": 8, "capped": False}],
             [{"path": "same", "class": "protected", "file_count": 2,
               "bytes": 8, "capped": False}], True),
            ([{"path": "same", "class": "protected", "file_count": 1,
               "bytes": 8, "capped": False}],
             [{"path": "same", "class": "protected", "file_count": 1,
               "bytes": 9, "capped": False}], True),
            ([{"path": "same", "class": "protected", "file_count": 0,
               "bytes": 0, "capped": True}],
             [{"path": "same", "class": "protected", "file_count": 0,
               "bytes": 0, "capped": True,
               "size_error": "PermissionError: denied"}], True),
            ([{"path": "same", "class": "protected", "file_count": 1,
               "bytes": 8, "capped": False}],
             [{"path": "same", "class": "protected", "file_count": 1,
               "bytes": 8, "capped": True}], True),
            ([], [{"path": "new", "class": "unclassified", "file_count": 0,
                  "bytes": 0, "capped": False}], True),
        ],
    )
    def test_ignored_inventory_refuses_only_appearance_growth_or_class_change(
        self, tmp_repo, initial, latest, changed
    ):
        assert worktree._ignored_state_changed(
            initial, latest, checkout=tmp_repo, record=None
        ) is changed

    def test_physical_cleanup_owner_uses_resolver_when_facts_are_absent(
        self, tmp_repo, monkeypatch
    ):
        physical = Path("/physical-host/old-worktree")
        monkeypatch.setattr(
            workspace_env, "_detect_physical_repo_root", lambda _root: physical
        )
        monkeypatch.setattr(workspace_env, "read_generated_facts", lambda *_a, **_kw: {})

        assert worktree._physical_checkout_for_identity_clean(
            tmp_repo, "old123"
        ) == physical

    def test_physical_cleanup_owner_refuses_unverified_generated_facts(
        self, tmp_repo, monkeypatch
    ):
        detected = Path("/new/physical")
        facts = {
            "repo_root": str(tmp_repo.resolve()),
            "physical_repo_root": "/old/physical",
            "instance_id": "old123",
        }
        monkeypatch.setattr(
            workspace_env, "_detect_physical_repo_root", lambda _root: detected
        )
        monkeypatch.setattr(
            workspace_env, "read_generated_facts", lambda *_a, **_kw: facts
        )

        with pytest.raises(
            worktree.WorktreeError, match="does not match the resolved physical root"
        ):
            worktree._physical_checkout_for_identity_clean(tmp_repo, "old123")

        facts["repo_root"] = "/other/logical"
        facts["physical_repo_root"] = str(detected)
        with pytest.raises(
            worktree.WorktreeError, match="do not match the recorded checkout root"
        ):
            worktree._physical_checkout_for_identity_clean(tmp_repo, "old123")

    def test_physical_cleanup_owner_reports_resolver_failure(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(
            workspace_env,
            "_detect_physical_repo_root",
            lambda *_a: (_ for _ in ()).throw(
                workspace_env.WorkspaceEnvError("mount mapping unavailable")
            ),
        )

        with pytest.raises(
            worktree.WorktreeError, match="could not resolve the physical owner path"
        ):
            worktree._physical_checkout_for_identity_clean(tmp_repo, "old123")

    def test_rm_refuses_unexpected_worktree_record_changes_during_cleanup(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "record-changed-during-clean")
        worktree.apply_lease(tmp_repo, record.logical_name, extend="24h")

        def clean_and_change_record(_checkout, *, yes):
            current = worktree.read_instance_record(record.record_path)
            worktree._write_instance_record(
                replace(current, display_name="concurrent-edit")
            )
            return 0

        monkeypatch.setattr(worktree, "_clean_in", clean_and_change_record)

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="worktree record changed during cleanup",
        ) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        assert exc.value.phase_report["phases"]["lease_release"] == "failed"
        assert record.git_worktree_path.is_dir()

    def test_rm_rechecks_ignored_inventory_after_cleanup(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") + ".assay/\n", encoding="utf-8")
        _git(["add", ".gitignore"], tmp_repo)
        _git(["commit", "-m", "ignore assay state"], tmp_repo)
        record = worktree.create(tmp_repo, "inventory-changed")

        def clean_and_write_assay(_checkout, *, yes):
            progress = record.git_worktree_path / ".assay" / "progress.jsonl"
            progress.parent.mkdir()
            progress.write_text("new progress\n", encoding="utf-8")
            return 0

        monkeypatch.setattr(worktree, "_clean_in", clean_and_write_assay)
        with pytest.raises(
            worktree.WorktreeRemovalError, match="ignored state changed since the initial inventory"
        ) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        assay_state = next(
            item for item in exc.value.document["ignored_state"]
            if item["path"] == ".assay"
        )
        assert assay_state == {
            "path": ".assay", "class": "protected", "file_count": 1,
            "bytes": len("new progress\n"), "capped": False,
            "protected_reason": "name",
        }
        assert exc.value.document["discarded_state"] == []
        assert record.git_worktree_path.is_dir()

    def test_rm_reports_ignored_state_removed_by_clean_and_discards_the_rest(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + "ciu.compose.yml\nvol-*/\n",
            encoding="utf-8",
        )
        stack_source = tmp_repo / "services" / "db"
        stack_source.mkdir(parents=True)
        (stack_source / "README.md").write_text("stack\n", encoding="utf-8")
        _git(["add", ".gitignore", "services/db/README.md"], tmp_repo)
        _git(["commit", "-m", "ignore rendered and volume state"], tmp_repo)
        record = worktree.create(tmp_repo, "clean-removes-ignored", base="main")
        stack = record.git_worktree_path / "services" / "db"
        stack.mkdir(parents=True, exist_ok=True)
        rendered = stack / "ciu.compose.yml"
        rendered.write_text("generated\n", encoding="utf-8")
        volume = stack / "vol-data"
        volume.mkdir()
        data = volume / "data.bin"
        data.write_bytes(b"state")

        def clean_and_remove_ignored(_checkout, *, yes):
            rendered.unlink()
            data.unlink()
            volume.rmdir()
            return 0

        monkeypatch.setattr(worktree, "_clean_in", clean_and_remove_ignored)

        document = worktree.remove_document(
            tmp_repo, record.logical_name, yes=True,
            discard_protected_state=record.branch,
        )

        assert document["discarded_state"] == document["ignored_state"]
        assert not any(
            item["path"].startswith("services/db/")
            for item in document["discarded_state"]
        )
        assert [item["path"] for item in document["removed_by_clean"]] == [
            "services", "services/db/vol-data",
        ]
        assert not record.git_worktree_path.exists()

    def test_rm_retries_after_clean_removed_consented_state(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + ".assay/\n", encoding="utf-8"
        )
        _git(["add", ".gitignore"], tmp_repo)
        _git(["commit", "-m", "ignore assay progress"], tmp_repo)
        record = worktree.create(tmp_repo, "retry-cleaned-state", base="main")
        state = record.git_worktree_path / ".assay" / "progress.jsonl"
        state.parent.mkdir()
        state.write_text("progress\n", encoding="utf-8")
        shared = worktree._shared_worktree()
        remove_workspace = shared.remove_workspace
        attempts = 0

        def fail_first_remove(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("simulated interruption after clean")
            return remove_workspace(*args, **kwargs)

        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        monkeypatch.setattr(shared, "remove_workspace", fail_first_remove)

        def clean_once(_checkout, *, yes):
            if state.exists():
                state.unlink()
                state.parent.rmdir()
            return 0

        monkeypatch.setattr(worktree, "_clean_in", clean_once)

        with pytest.raises(
            worktree.WorktreeRemovalError, match="simulated interruption after clean"
        ) as first:
            worktree.remove_document(
                tmp_repo, record.logical_name, yes=True,
                discard_protected_state=record.branch,
            )

        assert first.value.document["removed_by_clean"][0]["path"] == ".assay"
        assert record.git_worktree_path.is_dir()
        second = worktree.remove_document(
            tmp_repo, record.logical_name, yes=True,
            discard_protected_state=record.branch,
        )
        assert second["removed_by_clean"] == []
        assert not record.git_worktree_path.exists()

    def test_rm_allows_clean_to_remove_an_authorized_dirty_path(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "clean-removes-dirty", base="main")
        note = record.git_worktree_path / "operator-note.txt"
        note.write_text("temporary work\n", encoding="utf-8")
        monkeypatch.setattr(
            worktree, "_clean_in",
            lambda *_args, **_kwargs: (note.unlink() is None) and 0,
        )

        document = worktree.remove_document(
            tmp_repo, record.logical_name, yes=True, keep_branch=True,
            discard_changes=record.branch,
        )

        assert document["discarded_changes"] == ["operator-note.txt"]
        assert not record.git_worktree_path.exists()

    def test_rm_retry_reuses_exact_dirty_change_disposition_after_clean(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "retry-cleaned-change", base="main")
        note = record.git_worktree_path / "operator-note.txt"
        note.write_text("temporary work\n", encoding="utf-8")
        shared = worktree._shared_worktree()
        remove_workspace = shared.remove_workspace
        attempts = 0

        def fail_first_remove(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("simulated interruption after clean")
            return remove_workspace(*args, **kwargs)

        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        monkeypatch.setattr(shared, "remove_workspace", fail_first_remove)

        def clean_once(_checkout, *, yes):
            if note.exists():
                note.unlink()
            return 0

        monkeypatch.setattr(worktree, "_clean_in", clean_once)

        with pytest.raises(
            worktree.WorktreeRemovalError, match="simulated interruption after clean"
        ) as first:
            worktree.remove_document(
                tmp_repo, record.logical_name, yes=True, keep_branch=True,
                discard_changes=record.branch,
            )

        assert not note.exists()
        assert record.git_worktree_path.is_dir()

        second = worktree.remove_document(
            tmp_repo, record.logical_name, yes=True, keep_branch=True,
            discard_changes=record.branch,
        )
        assert second["discarded_changes"] == []
        assert second["status"] == "removed"
        assert not record.git_worktree_path.exists()

    def test_rm_bounds_dirty_path_message_but_keeps_full_json_list(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "many-dirty-paths", base="main")
        paths = [f"operator-{index:02}.txt" for index in range(25)]
        for path in paths:
            (record.git_worktree_path / path).write_text("work\n", encoding="utf-8")

        with pytest.raises(worktree.WorktreeRemovalError) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        error = exc.value.document["error"]
        assert "operator-00.txt" in error and "operator-19.txt" in error
        assert "operator-20.txt" not in error
        assert "25 total" in error
        assert exc.value.document["uncommitted_changes"] == paths
        assert exc.value.document["discarded_changes"] == []
        assert record.git_worktree_path.is_dir()

    def test_unreadable_protected_ignored_entry_is_retained_as_unsized(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(ignore.read_text(encoding="utf-8") + "vol-*/\n", encoding="utf-8")
        _git(["add", ".gitignore"], tmp_repo)
        _git(["commit", "-m", "ignore volumes"], tmp_repo)
        record = worktree.create(tmp_repo, "unreadable-volume")
        volume = record.git_worktree_path / "vol-postgres-data"
        volume.mkdir()
        (volume / "hidden.bin").write_bytes(b"hidden")
        real_scandir = worktree.os.scandir

        def denied_scandir(path):
            if Path(path) == volume:
                raise PermissionError("denied")
            return real_scandir(path)

        monkeypatch.setattr(worktree.os, "scandir", denied_scandir)
        monkeypatch.setattr(
            worktree, "_clean_in", lambda *_args, **_kwargs: pytest.fail("must refuse first")
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="reason=unreadable, size unavailable: PermissionError: denied",
        ) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        item = next(
            item for item in exc.value.document["ignored_state"]
            if item["path"] == "vol-postgres-data"
        )
        assert item == {
            "path": "vol-postgres-data", "class": "protected", "file_count": 0,
            "bytes": 0, "capped": True, "size_error": "PermissionError: denied",
            "protected_reason": "unreadable",
        }
        assert record.git_worktree_path.is_dir()

    def test_rm_reports_ignored_inventory_recheck_failure(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "ignored-recheck-failure")
        original = worktree._ignored_state_summary
        calls = 0

        def fail_second_inventory(checkout):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise worktree.WorktreeError("second inventory unavailable")
            return original(checkout)

        monkeypatch.setattr(worktree, "_ignored_state_summary", fail_second_inventory)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        with pytest.raises(
            worktree.WorktreeRemovalError, match="second inventory unavailable"
        ) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        assert exc.value.phase_report["phases"]["lifecycle_preflight"] == "failed"
        assert record.git_worktree_path.is_dir()

    def test_rm_reports_early_status_recheck_failure_before_any_cleanup(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "early-status-recheck-failure")
        original = worktree._worktree_change_paths
        calls = 0
        cleaned: list[object] = []

        def fail_second_status(checkout, managed):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise worktree.WorktreeError("early status unavailable")
            return original(checkout, managed)

        monkeypatch.setattr(worktree, "_worktree_change_paths", fail_second_status)
        monkeypatch.setattr(
            worktree, "_clean_in", lambda *a, **k: cleaned.append(a) or 0
        )

        with pytest.raises(
            worktree.WorktreeRemovalError, match="early status unavailable"
        ):
            worktree.remove_document(tmp_repo, record.logical_name)

        assert cleaned == []
        assert record.git_worktree_path.is_dir()

    def test_rm_reports_status_recheck_failure(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "status-recheck-failure")
        original = worktree._worktree_change_paths
        calls = 0

        def fail_second_status(checkout, managed):
            nonlocal calls
            calls += 1
            # Call 2 is the early (pre-sibling-clean) recheck; call 3 is the
            # final recheck this test targets.
            if calls == 3:
                raise worktree.WorktreeError("second status unavailable")
            return original(checkout, managed)

        monkeypatch.setattr(worktree, "_worktree_change_paths", fail_second_status)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        with pytest.raises(
            worktree.WorktreeRemovalError, match="second status unavailable"
        ) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        assert exc.value.phase_report["phases"]["lifecycle_preflight"] == "failed"
        assert record.git_worktree_path.is_dir()

    def test_rm_refuses_changes_created_during_cleanup(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "changes-during-clean")

        def clean_and_create_change(_checkout, *, yes):
            (record.git_worktree_path / "during-clean.txt").write_text(
                "created during cleanup\n", encoding="utf-8"
            )
            return 0

        monkeypatch.setattr(worktree, "_clean_in", clean_and_create_change)

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="uncommitted changes changed since the initial inventory",
        ) as exc:
            worktree.remove_document(tmp_repo, record.logical_name)

        assert exc.value.document["ignored_state"]
        assert record.git_worktree_path.is_dir()
        assert (record.git_worktree_path / "during-clean.txt").is_file()


class TestPermissionRemovalRetry:
    @staticmethod
    def _permission_failed_checkout(
        tmp_repo, fake_generate_env, monkeypatch, *, consent=False,
        privileged_rmtree=None, inventory=None, fail_retry_checkpoint=False,
        residual_guard_path=None, deny_blocker_scandir=False,
        checkpointed_ignored_parent=False,
    ):
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8")
            + "locked/\n"
            + ("cache/\n" if checkpointed_ignored_parent else ""),
            encoding="utf-8",
        )
        assert _git(["add", ".gitignore"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "ignore permission fixture"], tmp_repo).returncode == 0
        record = worktree.create(tmp_repo, "permission-checkpoint", base="main")
        checkout = record.git_worktree_path
        (checkout / "unmerged-work.txt").write_text("keep this branch\n", encoding="utf-8")
        assert _git(["add", "unmerged-work.txt"], checkout).returncode == 0
        assert _git(["commit", "-m", "keep unmerged work"], checkout).returncode == 0
        if checkpointed_ignored_parent:
            checkpointed_state = (
                checkout / "cache" / "vol-postgres-data" / ".assay"
                / "prior-run" / "state.json"
            )
            checkpointed_state.parent.mkdir(parents=True)
            checkpointed_state.write_text("checkpointed", encoding="utf-8")
        blocked = checkout / "locked"
        blocked.mkdir()
        (blocked / "payload").write_text("state", encoding="utf-8")
        blocked.chmod(0o500)
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        admin_path = Path(
            worktree._checkout_admin_identity(
                checkout, Path(shared_record.git_common_dir)
            )["path"]
        )
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        actual_run = subprocess.run
        attempts = []

        def unregister_then_report_permission(argv, *args, **kwargs):
            if argv[:3] == ["git", "worktree", "remove"] and not attempts:
                attempts.append(argv)
                shutil.rmtree(admin_path)
                record.record_path.unlink(missing_ok=True)
                return subprocess.CompletedProcess(
                    argv, 1, "", f"fatal: cannot remove '{blocked}': Permission denied"
                )
            return actual_run(argv, *args, **kwargs)

        monkeypatch.setattr(worktree.subprocess, "run", unregister_then_report_permission)
        if fail_retry_checkpoint:
            real_store = worktree._store_removal_checkpoint
            store_calls = []

            def fail_second_checkpoint(shared, shared_record, checkpoint):
                store_calls.append(checkpoint)
                if len(store_calls) == 2:
                    raise RuntimeError("retry checkpoint storage failed")
                return real_store(shared, shared_record, checkpoint)

            monkeypatch.setattr(
                worktree, "_store_removal_checkpoint", fail_second_checkpoint
            )
        if inventory is not None:
            monkeypatch.setattr(
                worktree, "_permission_retry_candidates", lambda *_args: inventory
            )
        if deny_blocker_scandir:
            real_candidates = worktree._permission_retry_candidates
            real_scandir = worktree.os.scandir

            def candidates_with_unreadable_blocker(*args):
                result = real_candidates(*args)

                def deny_blocker(path):
                    if not isinstance(path, int) and Path(path) == blocked:
                        raise PermissionError(errno.EACCES, "scan denied", str(path))
                    return real_scandir(path)

                monkeypatch.setattr(worktree.os, "scandir", deny_blocker)
                return result

            monkeypatch.setattr(
                worktree, "_permission_retry_candidates",
                candidates_with_unreadable_blocker,
            )
        if privileged_rmtree is not None:
            from ciu import engine
            monkeypatch.setattr(
                engine, "privileged_rmtree",
                lambda physical_target, **_identities: privileged_rmtree(
                    Path(physical_target), checkout, blocked
                ),
            )
        if residual_guard_path is not None:
            real_shared = worktree._shared_worktree()

            class SharedProxy:
                def __getattr__(self, name):
                    return getattr(real_shared, name)

                def remove_workspace(self, record, **kwargs):
                    kwargs["residual_checkout_guard"](residual_guard_path)

            monkeypatch.setattr(worktree, "_shared_worktree", lambda: SharedProxy())
        try:
            outcome = worktree.remove_document(
                tmp_repo, record.logical_name,
                root_escalation=record.branch if consent else None,
                discard_protected_state=(
                    record.branch if checkpointed_ignored_parent else None
                ),
                json_output=True,
            )
        except worktree.WorktreeRemovalError as exc:
            outcome = exc
        if not consent:
            assert isinstance(outcome, worktree.WorktreeRemovalError)
            assert (
                outcome.document["privileged_retry"]["status"] == "offered"
            ), outcome.document
        shared = worktree._shared_worktree()
        durable = shared.find_workspace(
            Path(shared_record.git_common_dir), checkout
        )
        if isinstance(outcome, worktree.WorktreeRemovalError):
            assert durable is not None
        return record, checkout, blocked, shared, durable, admin_path, outcome

    @staticmethod
    def _rewrite_checkpoint(shared, record, update, *, branch=None):
        current = shared.find_workspace(record.git_common_dir, record.worktree_path)
        assert current is not None
        checkpoint = dict(current.metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA])
        update(checkpoint)
        metadata = dict(current.metadata)
        metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA] = checkpoint
        if branch is not None:
            current = replace(current, branch=branch)
        shared.write_record(replace(current, metadata=metadata))
        return checkpoint

    @staticmethod
    def _verified_retry_identity_fixture(tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "verified-identity-case", base="main")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        admin = worktree._checkout_admin_identity(
            record.git_worktree_path, Path(shared_record.git_common_dir)
        )
        checkpoint = {
            "source_git_root": str(shared_record.source_git_root),
            "git_common_dir": str(shared_record.git_common_dir),
            "worktree_path": str(shared_record.worktree_path),
            "physical_worktree_path": str(shared_record.physical_worktree_path),
            "branch": shared_record.branch,
            "logical_name": record.logical_name,
            "checkout_identity": worktree._path_identity(record.git_worktree_path),
            "git_admin_path": admin["path"],
            "git_admin_identity": {"device": admin["device"], "inode": admin["inode"]},
        }
        return record, shared_record, admin, checkpoint

    def test_json_cleanup_child_keeps_prose_off_stdout(self, tmp_path, monkeypatch, capsys):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "clean progress\n", "warning\n")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)

        assert worktree._run_cleanup_child(
            ["ciu", "clean"], cwd=tmp_path, env={}, json_output=True
        ) == 0

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == "clean progress\nwarning\n"
        assert calls == [(
            ["ciu", "clean"],
            {"cwd": str(tmp_path), "env": {}, "text": True, "capture_output": True,
             "check": False},
        )]

    def test_json_cleanup_child_handles_children_with_no_output(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            worktree.subprocess, "run",
            lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
        )

        assert worktree._run_cleanup_child(
            ["ciu", "clean"], cwd=tmp_path, env={}, json_output=True
        ) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_permission_candidates_skip_empty_readonly_dirs_and_symlink_escapes(
        self, tmp_path
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        blocked = checkout / "cache"
        blocked.mkdir()
        (blocked / "payload").write_text("state", encoding="utf-8")
        blocked.chmod(0o500)
        empty = checkout / "empty-readonly"
        empty.mkdir()
        empty.chmod(0o500)
        external = tmp_path / "external"
        external.mkdir()
        (external / "keep").write_text("keep", encoding="utf-8")
        (checkout / "escape").symlink_to(external, target_is_directory=True)

        candidates, capped = worktree._permission_retry_candidates(
            checkout, f"fatal: cannot remove '{blocked}/payload': Permission denied"
        )

        assert capped is False
        assert [item["path"] for item in candidates] == ["cache"]
        assert (external / "keep").read_text(encoding="utf-8") == "keep"

    def test_permission_error_parser_only_accepts_explicit_quoted_paths(self):
        paths = worktree._permission_error_path(
            "warning: 'ignored': disk full\n"
            "fatal: cannot remove '/checkout/.coverage': Permission denied\n"
            'fatal: cannot unlink "relative/file": Operation not permitted\n'
            "fatal: cannot remove 'not-a-path': EACCES"
        )
        assert paths == [Path("/checkout/.coverage"), Path("relative/file")]

    def test_permission_candidates_reject_non_directory_and_unsafe_git_paths(
        self, tmp_path
    ):
        regular_file = tmp_path / "file"
        regular_file.touch()
        with pytest.raises(worktree.WorktreeError, match="not a real directory"):
            worktree._permission_retry_candidates(regular_file, "")

        checkout = tmp_path / "checkout"
        checkout.mkdir()
        target = checkout / "safe"
        target.touch()
        outside = tmp_path / "outside"
        outside.touch()
        (checkout / "internal-link").symlink_to(target)
        (checkout / "escape").symlink_to(outside)
        candidates, capped = worktree._permission_retry_candidates(
            checkout,
            f"cannot remove '{checkout}': Permission denied\n"
            f"cannot remove '{checkout / 'missing'}': Permission denied\n"
            f"cannot remove '{checkout / 'escape' / 'outside'}': Permission denied\n"
            f"cannot remove '{checkout / 'internal-link'}': Permission denied\n"
            "cannot remove '/unrelated': Permission denied\n"
            "cannot remove 'safe': Permission denied",
        )
        assert capped is False
        assert [item["path"] for item in candidates] == ["safe"]

    @pytest.mark.parametrize("case", ["scandir", "entry-stat"])
    def test_permission_inventory_ignores_non_permission_scan_errors(
        self, case, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        unreadable = checkout / "unreadable-dir"
        unreadable.mkdir()
        (unreadable / "payload").touch()
        denied_stat = checkout / "denied-stat"
        denied_stat.touch()
        real_scandir = worktree.os.scandir

        class EntryProxy:
            def __init__(self, entry):
                self.entry = entry
                self.path = entry.path

            def stat(self, *, follow_symlinks):
                if case == "entry-stat" and Path(self.path) == denied_stat:
                    raise OSError(errno.EIO, "I/O failure", self.path)
                return self.entry.stat(follow_symlinks=follow_symlinks)

        @contextmanager
        def failing_scandir(directory):
            directory = Path(directory)
            if case == "scandir" and directory == unreadable:
                raise OSError(errno.EIO, "I/O failure", str(directory))
            with real_scandir(directory) as entries:
                yield [EntryProxy(entry) for entry in entries]

        monkeypatch.setattr(worktree.os, "scandir", failing_scandir)
        candidates, capped = worktree._permission_retry_candidates(checkout, "")
        assert candidates == []
        assert capped is False

    @pytest.mark.parametrize("case", ["symlink-race", "unreadable-race"])
    def test_permission_inventory_rechecks_candidate_components_after_resolve(
        self, case, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        candidate = checkout / "candidate"
        candidate.touch()
        real_lstat = Path.lstat
        calls = 0

        def raced_lstat(path, *args, **kwargs):
            nonlocal calls
            if Path(path) == candidate:
                calls += 1
                if calls == 2:
                    if case == "symlink-race":
                        return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
                    raise PermissionError(errno.EACCES, "raced", str(path))
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", raced_lstat)
        candidates, capped = worktree._permission_retry_candidates(
            checkout, f"cannot remove '{candidate}': Permission denied"
        )
        assert candidates == []
        assert capped is False

    def test_permission_inventory_skips_a_queued_directory_replaced_by_file(
        self, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        child = checkout / "child"
        child.mkdir()
        (child / "payload").touch()
        real_lstat = Path.lstat
        monkeypatch.setattr(
            Path, "lstat",
            lambda path, *args, **kwargs: SimpleNamespace(st_mode=stat.S_IFREG)
            if Path(path) == child else real_lstat(path, *args, **kwargs),
        )

        candidates, capped = worktree._permission_retry_candidates(checkout, "")
        assert candidates == []
        assert capped is False

    def test_permission_inventory_cap_marks_unscanned_entries_protected(
        self, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "first").touch()
        monkeypatch.setattr(worktree, "_IGNORED_STATE_MAX_TOTAL_ENTRIES", 0)

        candidates, capped = worktree._permission_retry_candidates(checkout, "")

        assert candidates == []
        assert capped is True

    def test_permission_inventory_cap_with_pending_directories_is_protected(
        self, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "a").mkdir()
        (checkout / "b").mkdir()
        (checkout / "a" / "item").touch()
        (checkout / "b" / "item").touch()
        monkeypatch.setattr(worktree, "_IGNORED_STATE_MAX_TOTAL_ENTRIES", 2)

        candidates, capped = worktree._permission_retry_candidates(checkout, "")

        assert candidates == []
        assert capped is True

    def test_permission_inventory_collects_unreadable_child_and_entry(
        self, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        unreadable = checkout / "unreadable-dir"
        unreadable.mkdir()
        (unreadable / "payload").touch()
        denied_stat = checkout / "denied-stat"
        denied_stat.touch()
        real_scandir = worktree.os.scandir

        class EntryProxy:
            def __init__(self, entry):
                self.entry = entry
                self.path = entry.path

            def stat(self, *, follow_symlinks):
                if Path(self.path) == denied_stat:
                    raise PermissionError(errno.EACCES, "denied", self.path)
                return self.entry.stat(follow_symlinks=follow_symlinks)

        @contextmanager
        def fake_scandir(directory):
            directory = Path(directory)
            if directory == unreadable:
                raise PermissionError(errno.EPERM, "denied", str(directory))
            with real_scandir(directory) as entries:
                yield [EntryProxy(entry) for entry in entries]

        monkeypatch.setattr(worktree.os, "scandir", fake_scandir)

        candidates, capped = worktree._permission_retry_candidates(checkout, "")

        assert capped is False
        assert {item["path"] for item in candidates} == {
            "unreadable-dir", "denied-stat",
        }

    def test_permission_inventory_selects_nonempty_unwritable_directory(
        self, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        blocked = checkout / "blocked"
        blocked.mkdir()
        (blocked / "payload").touch()
        real_access = worktree.os.access
        monkeypatch.setattr(
            worktree.os, "access",
            lambda path, mode, **kwargs: False if Path(path) == blocked
            else real_access(path, mode, **kwargs),
        )

        candidates, capped = worktree._permission_retry_candidates(checkout, "")

        assert capped is False
        assert [item["path"] for item in candidates] == ["blocked"]

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("missing-checkout", "checkout disappeared"),
            ("file-checkout", "not a real directory"),
            ("replaced-checkout", "path was replaced"),
            ("invalid-relative", "target is unsafe"),
            ("missing-target", "target disappeared"),
            ("escape-link", "escapes the checkout"),
            ("inside-link", "contains a symlink"),
            ("replaced-target", "target was replaced"),
        ("replaced-parent", "parent was replaced"),
            ("checkout-target", "may not target the checkout root"),
        ],
    )
    def test_validated_permission_target_refuses_changed_or_unsafe_paths(
        self, case, message, tmp_path
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        (checkout / "target").write_text("data", encoding="utf-8")
        identity = worktree._path_identity(checkout)
        item = {"path": "target", **worktree._path_identity(checkout / "target"),
                "mode": stat.S_IFMT((checkout / "target").lstat().st_mode),
                "parent_device": identity["device"], "parent_inode": identity["inode"]}

        if case == "missing-checkout":
            shutil.rmtree(checkout)
            checkout = tmp_path / "missing"
            with pytest.raises(worktree.WorktreeError, match=message):
                worktree._validated_retry_target(
                    checkout, item, expected_checkout_identity=identity
                )
            return
        if case == "file-checkout":
            shutil.rmtree(checkout)
            checkout.write_text("file", encoding="utf-8")
            identity = worktree._path_identity(checkout)
        elif case == "replaced-checkout":
            identity = {"device": -1, "inode": -1}
        elif case == "invalid-relative":
            item["path"] = "../outside"
        elif case == "missing-target":
            item["path"] = "missing"
        elif case == "escape-link":
            outside = tmp_path / "outside"
            outside.touch()
            (checkout / "link").symlink_to(outside)
            item["path"] = "link"
        elif case == "inside-link":
            (checkout / "inside").touch()
            (checkout / "link").symlink_to(checkout / "inside")
            item["path"] = "link"
        elif case == "replaced-target":
            item["device"] = -1
        elif case == "replaced-parent":
            item["parent_inode"] = -1
        elif case == "checkout-target":
            item = {"path": ".", **identity,
                    "mode": stat.S_IFMT(checkout.lstat().st_mode)}

        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._validated_retry_target(
                checkout, item, expected_checkout_identity=identity
            )

    def test_validated_permission_target_allows_only_recorded_absence(
        self, tmp_path
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        identity = worktree._path_identity(checkout)
        assert worktree._validated_retry_target(
            checkout, {"path": "gone"}, expected_checkout_identity=identity,
            allow_missing=True,
        ) is None
        assert worktree._validated_retry_target(
            tmp_path / "gone-checkout", {"path": "gone"},
            expected_checkout_identity=identity, allow_missing=True,
        ) is None

    def test_validated_permission_target_rejects_missing_or_malformed_inventory_path(
        self, tmp_path
    ):
        checkout = tmp_path / "checkout"
        checkout.mkdir()
        identity = worktree._path_identity(checkout)
        for raw in (None, ""):
            with pytest.raises(worktree.WorktreeError, match="has no relative path"):
                worktree._validated_retry_target(
                    checkout, {"path": raw}, expected_checkout_identity=identity
                )

    def test_physical_retry_target_requires_the_recorded_checkout(
        self, tmp_path, monkeypatch
    ):
        checkout = tmp_path / "logical"
        checkout.mkdir()
        target = checkout / "locked"
        target.touch()
        shared = SimpleNamespace(
            worktree_path=checkout, physical_worktree_path=tmp_path / "host" / "checkout"
        )
        monkeypatch.setattr(
            worktree, "_physical_workspace_target",
            lambda _repo_root, _checkout: tmp_path / "host" / "checkout",
        )
        assert worktree._physical_retry_target(shared, tmp_path / "repo", checkout, target) == (
            tmp_path / "host" / "checkout" / "locked"
        )
        with pytest.raises(worktree.WorktreeError, match="differs from shared record"):
            worktree._physical_retry_target(
                shared, tmp_path / "repo", tmp_path / "other", target
            )
        with pytest.raises(worktree.WorktreeError, match="translated retry target escapes"):
            worktree._physical_retry_target(
                shared, tmp_path / "repo", checkout, checkout / ".." / "outside"
            )
        wrong_shared = SimpleNamespace(
            worktree_path=checkout,
            physical_worktree_path=tmp_path / "existing-but-wrong-host-root",
        )
        with pytest.raises(
            worktree.WorktreeError, match="differs from shared record physical path"
        ):
            worktree._physical_retry_target(
                wrong_shared, tmp_path / "repo", checkout, target
            )

    def test_resume_refuses_a_forged_existing_physical_checkout_before_helper(
        self, tmp_repo, fake_generate_env, monkeypatch, tmp_path
    ):
        from ciu import engine

        record, checkout, blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        wrong_root = tmp_path / "other-existing-checkout"
        wrong_root.mkdir()
        protected_payload = wrong_root / "must-survive"
        protected_payload.write_text("unrelated host data", encoding="utf-8")
        recorded_physical = Path(durable.physical_worktree_path)
        monkeypatch.setattr(
            worktree, "_physical_workspace_target",
            lambda _repo_root, _checkout: wrong_root,
        )
        helper_calls = []
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **_identities: helper_calls.append(target),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="differs from shared record physical path",
        ):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

        assert helper_calls == []
        assert blocked.exists()
        assert Path(recorded_physical) != wrong_root
        assert protected_payload.read_text(encoding="utf-8") == "unrelated host data"

    def test_consent_retries_only_the_permission_paths_in_the_same_rm_call(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        helper_targets = []

        def fake_privileged_rmtree(
            physical_target, _checkout, local_blocked, **_identities
        ):
            physical_target = Path(physical_target)
            helper_targets.append(physical_target)
            assert physical_target.name == "locked"
            local_blocked.chmod(0o700)
            shutil.rmtree(local_blocked)

        record, checkout, blocked, _shared, _durable, admin_path, document = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch, consent=True,
                privileged_rmtree=fake_privileged_rmtree,
            )
        )

        assert isinstance(document, dict)
        assert document["status"] == "removed"
        assert document["privileged_retry"]["status"] == "complete"
        assert len(helper_targets) == 1 and helper_targets[0].name == "locked"
        assert not checkout.exists()
        assert not admin_path.exists()

    def test_same_call_retry_calls_helper_for_unreadable_blocker_directory(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        helper_targets = []
        record, checkout, blocked, _shared, durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch, consent=True,
                deny_blocker_scandir=True,
                privileged_rmtree=lambda target, _checkout, _blocked: (
                    helper_targets.append(target)
                ),
            )
        )

        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert (
            "helper returned but blocked target remains" in str(failure)
        ), failure.document.get("error")
        assert len(helper_targets) == 1
        assert helper_targets == [
            Path(durable.physical_worktree_path) / blocked.name
        ]
        assert checkout.is_dir()

    def test_resume_retry_calls_helper_for_unreadable_blocker_directory(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, _shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        real_scandir = worktree.os.scandir

        def deny_blocker(path):
            if Path(path) == blocked:
                raise PermissionError(errno.EACCES, "scan denied", str(path))
            return real_scandir(path)

        monkeypatch.setattr(worktree.os, "scandir", deny_blocker)
        helper_targets = []
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **_identities: helper_targets.append(target),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="helper returned but blocked target remains",
        ):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

        assert len(helper_targets) == 1
        assert helper_targets == [
            Path(durable.physical_worktree_path) / blocked.name
        ]
        assert checkout.is_dir()

    def test_same_call_retry_reports_target_and_checkout_when_helper_lies(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        calls = []
        record, checkout, blocked, _shared, _durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch, consent=True,
                privileged_rmtree=lambda target, _checkout, _blocked: calls.append(target),
            )
        )

        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert "remaining paths:" in str(failure)
        assert len(calls) == 1 and calls[0].name == "locked"
        retry = failure.document["privileged_retry"]
        assert retry["status"] == "failed"
        assert retry["remaining"] == [str(blocked)]
        assert blocked.exists()
        assert checkout.exists()

    def test_same_call_retry_refuses_new_protected_state_before_library_rmtree(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_refresh = worktree._refresh_permission_retry_checkpoint_time
        new_state: list[Path] = []

        def add_new_state_after_checkpoint(checkout, checkpoint, before, candidates):
            real_refresh(checkout, checkpoint, before, candidates)
            state = checkout / ".assay" / "vol-new-campaign"
            state.mkdir(parents=True)
            payload = state / "mutants.json"
            payload.write_text("preserve", encoding="utf-8")
            new_state.append(payload)

        monkeypatch.setattr(
            worktree, "_refresh_permission_retry_checkpoint_time",
            add_new_state_after_checkpoint,
        )

        def remove_only_blocked(_physical, _checkout, blocked, **_identities):
            blocked.chmod(0o700)
            shutil.rmtree(blocked)

        record, checkout, _blocked, _shared, _durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch, consent=True,
                privileged_rmtree=remove_only_blocked,
            )
        )

        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert "permission-specific checkout retry failed" in str(failure)
        assert "changed after the permission checkpoint" in str(failure)
        assert checkout.is_dir()
        assert new_state[0].read_text(encoding="utf-8") == "preserve"

    def test_residual_guard_refuses_a_different_checkout_path(
        self, tmp_repo, fake_generate_env, monkeypatch, tmp_path
    ):
        other_checkout = tmp_path / "other-checkout"
        other_checkout.mkdir()

        def remove_blocked(_physical, _checkout, blocked):
            blocked.chmod(0o700)
            shutil.rmtree(blocked)

        record, checkout, _blocked, _shared, _durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo,
                fake_generate_env,
                monkeypatch,
                consent=True,
                privileged_rmtree=remove_blocked,
                residual_guard_path=other_checkout,
            )
        )

        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert "no matching residual-checkout checkpoint" in str(failure)
        assert checkout.is_dir()
        assert other_checkout.is_dir()

    def test_same_call_retry_refuses_a_target_that_disappears_before_the_helper(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        helper_calls = []
        monkeypatch.setattr(worktree, "_validated_retry_target", lambda *_args, **_kwargs: None)
        record, checkout, blocked, _shared, _durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch, consent=True,
                privileged_rmtree=lambda target, _checkout, _blocked: helper_calls.append(target),
            )
        )

        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert "target disappeared before helper call" in str(failure)
        assert helper_calls == []
        assert blocked.exists()
        assert checkout.exists()

    @pytest.mark.parametrize(
        ("inventory", "expected"),
        [([], "refused-no-blocked-target"), (None, "refused-inventory-capped")],
    )
    def test_permission_failure_without_a_complete_inventory_never_calls_helper(
        self, inventory, expected, tmp_repo, fake_generate_env, monkeypatch
    ):
        helper_calls = []
        candidates = ([], True) if inventory is None else (inventory, False)
        record, checkout, _blocked, _shared, _durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch, consent=True,
                privileged_rmtree=lambda target, _checkout, _blocked: helper_calls.append(target),
                inventory=candidates,
            )
        )
        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert helper_calls == []
        assert failure.document["privileged_retry"]["status"] == expected
        assert checkout.exists()

    def test_permission_retry_reports_checkpoint_persistence_failure(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, blocked, _shared, _durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch,
                fail_retry_checkpoint=True,
            )
        )

        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert "could not persist retry checkpoint: retry checkpoint storage failed" in str(failure)
        retry = failure.document["privileged_retry"]
        assert retry["checkpoint_error"] == "retry checkpoint storage failed"
        assert str(blocked) in retry["remaining"]
        assert checkout.exists()

    def test_failed_retry_reports_the_checkout_after_the_candidate_was_removed(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        def remove_then_fail(_physical, _checkout, blocked):
            blocked.chmod(0o700)
            (blocked / "payload").unlink()
            blocked.rmdir()
            raise RuntimeError("helper transport lost")

        record, checkout, blocked, _shared, _durable, _admin, failure = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch, consent=True,
                privileged_rmtree=remove_then_fail,
            )
        )

        assert isinstance(failure, worktree.WorktreeRemovalError)
        assert not blocked.exists()
        assert failure.document["privileged_retry"]["remaining"] == [str(checkout)]

    def test_resume_reports_an_unreadable_checkout_after_retry_failure(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        armed = False

        def fail_helper(_target):
            nonlocal armed
            armed = True
            raise RuntimeError("helper unavailable")

        monkeypatch.setattr(
            engine, "privileged_rmtree", lambda _target, **_ids: fail_helper(_target)
        )
        real_lstat = Path.lstat

        def unreadable_checkout_stat(path, *args, **kwargs):
            if armed and Path(path) == checkout:
                raise PermissionError(errno.EACCES, "denied", str(path))
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", unreadable_checkout_stat)

        with pytest.raises(worktree.WorktreeRemovalError, match="remaining paths:") as failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )
        remaining = failure.value.document["privileged_retry"]["remaining"]
        assert str(blocked) in remaining
        assert str(checkout) in remaining

    def test_detached_checkout_offers_paths_without_an_unusable_branch_command(
        self, tmp_repo, monkeypatch
    ):
        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + "locked/\n", encoding="utf-8"
        )
        assert _git(["add", ".gitignore"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "ignore detached permission fixture"], tmp_repo).returncode == 0
        checkout = tmp_repo.parent / "detached-permission-checkout"
        assert _git(
            ["worktree", "add", "--detach", str(checkout), "main"], tmp_repo
        ).returncode == 0
        blocked = checkout / "locked"
        blocked.mkdir()
        (blocked / "payload").touch()
        blocked.chmod(0o500)
        common = Path(worktree._shared_worktree().discover_git_root(tmp_repo)[1])
        admin = Path(worktree._checkout_admin_identity(checkout, common)["path"])
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        actual_run = subprocess.run
        attempts = []

        def fail_remove(argv, *args, **kwargs):
            if argv[:3] == ["git", "worktree", "remove"] and not attempts:
                attempts.append(argv)
                shutil.rmtree(admin)
                return subprocess.CompletedProcess(
                    argv, 1, "", f"cannot remove '{blocked}': Permission denied"
                )
            return actual_run(argv, *args, **kwargs)

        monkeypatch.setattr(worktree.subprocess, "run", fail_remove)
        with pytest.raises(worktree.WorktreeRemovalError) as failure:
            worktree.remove_document(tmp_repo, str(checkout))

        assert "privileged_retry" in failure.value.document, failure.value.document
        retry = failure.value.document["privileged_retry"]
        assert retry["status"] == "offered"
        assert retry["targets"] == ["locked"]
        assert "command" not in retry
        assert checkout.exists()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("version", 2),
            ("logical_name", "some-other-worktree"),
            ("resource_cleanup", "running"),
            ("branch_plan", None),
        ],
    )
    def test_resume_refuses_checkpoint_that_does_not_match_shared_record(
        self, field, value, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, _checkout, _blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        self._rewrite_checkpoint(
            shared, durable, lambda checkpoint: checkpoint.__setitem__(field, value)
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="durable permission checkpoint"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("permission_blockers", "not-an-inventory", "no blocked-target inventory"),
            ("checkout_identity", None, "missing checkout or Git-admin identity"),
            ("git_admin_identity", None, "missing checkout or Git-admin identity"),
            ("git_admin_gitdir_link", "/tmp/other/.git", "no longer proves"),
        ],
    )
    def test_resume_refuses_missing_inventory_and_git_identity(
        self, field, value, message, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, _checkout, _blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        self._rewrite_checkpoint(
            shared, durable, lambda checkpoint: checkpoint.__setitem__(field, value)
        )

        with pytest.raises(worktree.WorktreeRemovalError, match=message):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

    def test_resume_requires_a_local_branch_for_exact_consent(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, _checkout, _blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        self._rewrite_checkpoint(
            shared, durable, lambda checkpoint: checkpoint.__setitem__("branch", "HEAD"),
            branch="HEAD",
        )
        monkeypatch.setattr(worktree, "_instance_record_from_shared", lambda *_args: None)

        with pytest.raises(worktree.WorktreeRemovalError, match="no local branch"):
            worktree.remove_document(tmp_repo, record.logical_name)

    def test_resume_rejects_a_checkout_that_was_replaced(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, _blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        (checkout / "locked").chmod(0o700)
        checkout.rename(checkout.with_name("old-checkout"))
        checkout.mkdir()

        with pytest.raises(worktree.WorktreeRemovalError, match="checkout path was replaced"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

    def test_resume_refuses_a_registration_that_reappears(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, _blocked, shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        monkeypatch.setattr(worktree, "find_worktree", lambda *_args: None)
        monkeypatch.setattr(
            shared, "list_git_worktrees",
            lambda *_args: [SimpleNamespace(
                path=checkout, branch=record.branch, head="abc",
                is_primary=False, is_detached=False, is_bare=False,
            )],
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="again registers"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

    def test_resume_can_finish_when_git_already_removed_all_checkout_paths(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, _blocked, _shared, _durable, admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        (checkout / "locked").chmod(0o700)
        shutil.rmtree(checkout)

        document = worktree.remove_document(
            tmp_repo, record.logical_name, root_escalation=record.branch,
            json_output=True,
        )

        assert document["status"] == "removed"
        assert document["resumed"] is True
        assert not checkout.exists()
        assert not admin.exists()

    def test_resume_refuses_a_target_the_helper_did_not_remove(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        calls = []
        identities = []
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **values: (calls.append(target), identities.append(values)),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="helper returned but blocked target remains") as failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )

        assert len(calls) == 1 and Path(calls[0]).name == "locked"
        blocked_info = blocked.lstat()
        parent_info = blocked.parent.lstat()
        assert identities == [{
            "target_identity": (blocked_info.st_dev, blocked_info.st_ino),
            "parent_identity": (parent_info.st_dev, parent_info.st_ino),
        }]
        retry = failure.value.document["privileged_retry"]
        assert retry["status"] == "failed"
        assert str(checkout) in retry["remaining"]

    def test_resume_refusal_reports_recreated_git_admin_path(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, blocked, _shared, _durable, admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        admin.mkdir()

        with pytest.raises(worktree.WorktreeRemovalError, match=str(admin)) as failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )

        remaining = failure.value.document["privileged_retry"]["remaining"]
        assert str(blocked) in remaining
        assert str(checkout) in remaining
        assert str(admin) in remaining

    def test_resume_requires_a_permission_failure_checkpoint(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, _checkout, _blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        self._rewrite_checkpoint(
            shared, durable, lambda checkpoint: checkpoint.__setitem__("state", "attempting")
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="no durable successful-cleanup"):
            worktree.remove_document(tmp_repo, record.logical_name)

    def test_resume_refuses_new_protected_state_even_when_mtime_is_preserved(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, _blocked, _shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        checkpoint = durable.metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA]
        old_time = checkpoint["checkpoint_time_ns"] - 1
        protected = checkout / ".assay" / "vol-new-run"
        protected.mkdir(parents=True)
        payload = protected / "state.json"
        payload.write_text("keep this new state", encoding="utf-8")
        for path in (payload, protected, protected.parent, checkout):
            os.utime(path, ns=(old_time, old_time))
        helper_calls = []
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **_identities: helper_calls.append(target),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="new protected-state component: \\.assay",
        ):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

        assert helper_calls == []
        assert payload.read_text(encoding="utf-8") == "keep this new state"

    def test_resume_accepts_checkpointed_protected_state_inside_ignored_parent(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, _shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(
                tmp_repo, fake_generate_env, monkeypatch,
                checkpointed_ignored_parent=True,
            )
        )
        checkpoint = durable.metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA]
        assert {
            "path": "cache",
            "class": "protected",
            "file_count": 1,
            "bytes": len("checkpointed"),
            "capped": False,
            "protected_reason": "descendant",
        } in checkpoint["ignored_state"]

        helper_targets = []

        def remove_blocker(target, _checkout, local_blocked, **_identities):
            helper_targets.append(target)
            local_blocked.chmod(0o700)
            shutil.rmtree(local_blocked)

        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **identities: remove_blocker(
                target, checkout, blocked, **identities
            ),
        )

        document = worktree.remove_document(
            tmp_repo, record.logical_name,
            root_escalation=record.branch,
            discard_protected_state=record.branch,
            json_output=True,
        )

        assert document["status"] == "removed"
        assert document["resumed"] is True
        assert len(helper_targets) == 1
        assert Path(helper_targets[0]) == Path(durable.physical_worktree_path) / "locked"
        assert not checkout.exists()

    def test_resume_refuses_a_file_modified_after_checkpoint_before_helper(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, _blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        changed = checkout / "unmerged-work.txt"
        changed.write_text("new work after the checkpoint\n", encoding="utf-8")
        helper_calls = []
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **_identities: helper_calls.append(target),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="changed after the permission checkpoint: unmerged-work.txt",
        ):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

        assert helper_calls == []
        assert changed.read_text(encoding="utf-8") == "new work after the checkpoint\n"

    def test_resume_refuses_when_leftover_checkout_walk_reaches_its_limit(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, _blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        helper_calls = []
        monkeypatch.setattr(worktree, "_PERMISSION_RETRY_WALK_MAX_ENTRIES", 1)
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **_identities: helper_calls.append(target),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="checkout walk reached its 1-entry limit",
        ):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

        assert helper_calls == []
        assert checkout.is_dir()

    def test_resume_supports_unrecorded_legacy_checkpoint_without_ciu_snapshot(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        metadata = dict(durable.metadata)
        metadata.pop(worktree._CIU_WORKTREE_RECORD_METADATA, None)
        shared.write_record(replace(durable, metadata=metadata))

        def remove_blocked(_target):
            blocked.chmod(0o700)
            (blocked / "payload").unlink()
            blocked.rmdir()

        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda _target, **_ids: remove_blocked(_target),
        )
        document = worktree.remove_document(
            tmp_repo, record.logical_name, root_escalation=record.branch,
            json_output=True,
        )

        assert document["status"] == "removed"
        assert document["resumed"] is True
        assert document.get("record") is None
        assert not checkout.exists()

    def test_root_escalation_accepts_one_exact_branch_and_rejects_multiple(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "root-consent-list", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        document = worktree.remove_document(
            tmp_repo, record.logical_name, root_escalation=[record.branch],
            json_output=True,
        )
        assert document["status"] == "removed"

        with pytest.raises(worktree.WorktreeRemovalError, match="one exact --root-escalation"):
            worktree.remove(tmp_repo, "missing", root_escalation=("one", "two"))

    def test_root_escalation_must_match_the_selected_checkout_branch(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "root-consent-mismatch", base="main")

        with pytest.raises(worktree.WorktreeRemovalError, match="must name the exact checkout branch"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation="another-branch"
            )

    def test_resume_refuses_an_unreadable_registration_inventory(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, _checkout, _blocked, shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        monkeypatch.setattr(worktree, "find_worktree", lambda *_args: None)
        monkeypatch.setattr(worktree, "find_instance_record", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            shared, "list_git_worktrees",
            lambda *_args: (_ for _ in ()).throw(worktree.WorktreeError("inventory unreadable")),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="could not recheck Git registration"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

    def test_resume_reports_an_unreadable_checkout_path(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, _blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        monkeypatch.setattr(worktree, "find_worktree", lambda *_args: None)
        monkeypatch.setattr(worktree, "find_instance_record", lambda *_args, **_kwargs: None)
        real_lstat = Path.lstat

        def fail_checkout_stat(path, *args, **kwargs):
            if Path(path) == checkout:
                raise PermissionError(errno.EACCES, "denied", str(path))
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", fail_checkout_stat)

        with pytest.raises(worktree.WorktreeRemovalError, match="could not inspect remaining checkout"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

    def test_resume_rejects_a_malformed_target_record(self,
        tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        blocked.chmod(0o700)
        shutil.rmtree(checkout)
        self._rewrite_checkpoint(
            shared, durable,
            lambda checkpoint: checkpoint.__setitem__("permission_blockers", [None]),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="malformed target") as failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )
        assert failure.value.document["privileged_retry"]["remaining"] == []

    def test_resume_revalidates_retry_target_identity_after_snapshot(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        checkpoint = durable.metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA]
        old_time = checkpoint["checkpoint_time_ns"] - 1
        replacement = checkout / "replacement"
        replacement.mkdir()
        (replacement / "payload").write_text("new", encoding="utf-8")
        replacement.chmod(0o500)
        os.utime(replacement / "payload", ns=(old_time, old_time))
        os.utime(replacement, ns=(old_time, old_time))
        blocked.chmod(0o700)
        shutil.rmtree(blocked)
        replacement.rename(blocked)
        os.utime(checkout, ns=(old_time, old_time))
        helper_calls = []
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **_identities: helper_calls.append(target),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="target was replaced"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )

        assert helper_calls == []
        assert (blocked / "payload").read_text(encoding="utf-8") == "new"

    def test_resume_skips_an_already_removed_target_and_keeps_unmerged_branch(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        blocked.chmod(0o700)
        (blocked / "payload").unlink()
        blocked.rmdir()
        self._rewrite_checkpoint(
            shared, durable,
            lambda checkpoint: checkpoint.__setitem__(
                "checkpoint_time_ns", worktree.time.time_ns()
            ),
        )
        from ciu import engine
        helper_calls = []
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda target, **_identities: helper_calls.append(target),
        )

        document = worktree.remove_document(
            tmp_repo, record.logical_name, root_escalation=record.branch,
            json_output=True,
        )

        assert document["resumed"] is True
        assert document["branch"]["outcome"] == "kept-unmerged"
        assert helper_calls == []
        assert not checkout.exists()

    def test_resume_reports_checkout_removal_oserror(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )

        def remove_blocked(_target):
            blocked.chmod(0o700)
            (blocked / "payload").unlink()
            blocked.rmdir()

        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda _target, **_ids: remove_blocked(_target),
        )
        real_rmtree = shutil.rmtree

        def fail_checkout_rmtree(path, *args, **kwargs):
            if Path(path) == checkout:
                raise PermissionError(errno.EACCES, "cannot remove checkout", str(path))
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", fail_checkout_rmtree)

        with pytest.raises(worktree.WorktreeRemovalError, match="checkout could not be removed"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )

    def test_resume_reports_checkout_that_remains_after_rmtree_returns(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )

        def remove_blocked(_target):
            blocked.chmod(0o700)
            (blocked / "payload").unlink()
            blocked.rmdir()

        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda _target, **_ids: remove_blocked(_target),
        )
        real_rmtree = shutil.rmtree
        monkeypatch.setattr(
            shutil, "rmtree",
            lambda path, *args, **kwargs: None if Path(path) == checkout
            else real_rmtree(path, *args, **kwargs),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="checkout remains after permission retry"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )

    def test_resume_lists_no_checkout_paths_when_final_record_removal_fails(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, blocked, shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        blocked.chmod(0o700)
        (blocked / "payload").unlink()
        blocked.rmdir()
        self._rewrite_checkpoint(
            shared, _durable,
            lambda checkpoint: checkpoint.__setitem__(
                "checkpoint_time_ns", worktree.time.time_ns()
            ),
        )
        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        monkeypatch.setattr(
            shared, "finish_workspace_removal",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("record finish failed")),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="no inventoried checkout or Git-admin path remains"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )
        assert not checkout.exists()

    def test_resume_keeps_a_path_in_the_remaining_list_when_it_cannot_stat_it(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, blocked, _shared, _durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        armed = False

        def fail_helper(_target):
            nonlocal armed
            armed = True
            raise RuntimeError("helper unavailable")

        monkeypatch.setattr(
            engine, "privileged_rmtree", lambda _target, **_ids: fail_helper(_target)
        )
        real_lstat = Path.lstat

        def unreadable_target_stat(path, *args, **kwargs):
            if armed and Path(path) == blocked:
                raise PermissionError(errno.EACCES, "denied", str(path))
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", unreadable_target_stat)

        with pytest.raises(worktree.WorktreeRemovalError, match="remaining paths:") as failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )
        assert str(blocked) in failure.value.document["privileged_retry"]["remaining"]
        assert str(checkout) in failure.value.document["privileged_retry"]["remaining"]

    def test_resume_caps_remaining_path_diagnostic_but_keeps_full_json(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        record, checkout, _blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        checkpoint = dict(
            durable.metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA]
        )
        candidates = list(checkpoint["permission_blockers"])
        for index in range(24):
            target = checkout / f"blocked-{index:02d}"
            target.mkdir()
            payload = target / "payload"
            payload.write_text("keep", encoding="utf-8")
            info = target.lstat()
            parent = target.parent.lstat()
            candidates.append({
                "path": target.name,
                "device": info.st_dev,
                "inode": info.st_ino,
                "mode": stat.S_IFMT(info.st_mode),
                "parent_device": parent.st_dev,
                "parent_inode": parent.st_ino,
            })
        checkpoint["permission_blockers"] = candidates
        checkpoint["checkpoint_time_ns"] = worktree.time.time_ns()
        metadata = dict(durable.metadata)
        metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA] = checkpoint
        shared.write_record(replace(durable, metadata=metadata))
        monkeypatch.setattr(
            engine, "privileged_rmtree",
            lambda _target, **_identities: (_ for _ in ()).throw(
                RuntimeError("helper transport lost")
            ),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="showing first 20 of 26 total paths",
        ) as failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )

        remaining = failure.value.document["privileged_retry"]["remaining"]
        assert len(remaining) == 26
        assert str(checkout) in remaining
        assert str(checkout / "blocked-23") in remaining

    def test_resume_lists_unreadable_git_admin_and_checkout_paths(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, blocked, _shared, _durable, admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        real_lstat = Path.lstat

        def unreadable_admin_stat(path, *args, **kwargs):
            if Path(path) == admin:
                raise PermissionError(errno.EACCES, "denied", str(path))
            return real_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", unreadable_admin_stat)

        with pytest.raises(worktree.WorktreeRemovalError, match="remaining paths:") as failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )
        remaining = failure.value.document["privileged_retry"]["remaining"]
        assert str(blocked) in remaining
        assert str(checkout) in remaining
        assert str(admin) in remaining

    def test_resume_avoids_duplicating_checkout_in_remaining_target_paths(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, checkout, _blocked, shared, durable, _admin, _outcome = (
            self._permission_failed_checkout(tmp_repo, fake_generate_env, monkeypatch)
        )
        self._rewrite_checkpoint(
            shared, durable,
            lambda checkpoint: checkpoint.__setitem__("permission_blockers", [{"path": "."}]),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="permission retry failed"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )

    def test_checkout_admin_identity_requires_both_gitdir_links(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "admin-identity", base="main")
        shared = worktree._shared_record_for_logical_name(tmp_repo, record.logical_name)
        assert shared is not None
        identity = worktree._checkout_admin_identity(
            record.git_worktree_path, Path(shared.git_common_dir)
        )
        assert Path(identity["path"]).is_dir()
        assert Path(identity["path"]).parent == Path(shared.git_common_dir) / "worktrees"
        assert worktree._resolved_gitdir_link(
            record.git_worktree_path, identity["gitdir_link"]
        ) == (record.git_worktree_path / ".git").resolve()
        assert worktree._resolved_gitdir_link(checkout := record.git_worktree_path, ".git") == (
            checkout / ".git"
        ).resolve()

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("marker-type", "not a regular file"),
            ("marker-text", "has no gitdir link"),
            ("outside-admin", "could not verify linked-worktree"),
            ("missing-admin", "could not verify linked-worktree"),
            ("file-admin", "not a directory"),
            ("wrong-reverse-link", "reverse link"),
        ],
    )
    def test_checkout_admin_identity_refuses_malformed_git_metadata(
        self, case, message, tmp_repo, fake_generate_env, tmp_path
    ):
        record = worktree.create(tmp_repo, f"admin-{case}", base="main")
        shared = worktree._shared_record_for_logical_name(tmp_repo, record.logical_name)
        assert shared is not None
        checkout = record.git_worktree_path
        common = Path(shared.git_common_dir)
        git_file = checkout / ".git"
        admin = Path(worktree._checkout_admin_identity(checkout, common)["path"])
        if case == "marker-type":
            git_file.unlink()
            git_file.mkdir()
        elif case == "marker-text":
            git_file.write_text("not a gitdir link\n", encoding="utf-8")
        elif case == "outside-admin":
            git_file.write_text(f"gitdir: {tmp_path / 'outside'}\n", encoding="utf-8")
        elif case == "missing-admin":
            git_file.write_text(
                f"gitdir: {common / 'worktrees' / 'missing'}\n", encoding="utf-8"
            )
        elif case == "file-admin":
            fake_admin = common / "worktrees" / "not-a-directory"
            fake_admin.write_text("file\n", encoding="utf-8")
            git_file.write_text(f"gitdir: {fake_admin}\n", encoding="utf-8")
        elif case == "wrong-reverse-link":
            (admin / "gitdir").write_text(f"{tmp_path / 'wrong' / '.git'}\n", encoding="utf-8")

        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._checkout_admin_identity(checkout, common)

    def test_removal_checkpoint_is_identity_bound_and_cleared_atomically(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "checkpoint", base="main")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        state = {"record": shared_record}
        writes = []

        class Ledger:
            def workspace_lock(self, _common):
                return nullcontext()

            def read_record(self, _path):
                return state["record"]

            def write_record(self, value):
                writes.append(value)
                state["record"] = value

        checkpoint = {"workspace_id": shared_record.workspace_id,
                      "worktree_path": str(shared_record.worktree_path),
                      "branch": shared_record.branch,
                      "checkout_identity": {"device": 1, "inode": 2}}
        worktree._store_removal_checkpoint(Ledger(), shared_record, checkpoint)
        assert state["record"].metadata[worktree._CIU_WORKTREE_REMOVAL_METADATA] == checkpoint
        worktree._store_removal_checkpoint(Ledger(), shared_record, None)
        assert worktree._CIU_WORKTREE_REMOVAL_METADATA not in state["record"].metadata
        assert len(writes) == 2

    def test_removal_checkpoint_refuses_shared_identity_drift_and_foreign_checkpoint(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "checkpoint-drift", base="main")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        checkpoint = {
            "workspace_id": shared_record.workspace_id,
            "worktree_path": str(shared_record.worktree_path),
            "branch": shared_record.branch,
            "checkout_identity": {"device": 1, "inode": 2},
        }

        class Ledger:
            def __init__(self, current):
                self.current = current

            def workspace_lock(self, _common):
                return nullcontext()

            def read_record(self, _path):
                return self.current

            def write_record(self, _value):
                raise AssertionError("a refused checkpoint must not be written")

        changed = replace(shared_record, workspace_id="replacement")
        with pytest.raises(worktree.WorktreeError, match="identity changed"):
            worktree._store_removal_checkpoint(Ledger(changed), shared_record, checkpoint)

        foreign = replace(shared_record, metadata={
            worktree._CIU_WORKTREE_REMOVAL_METADATA: {
                **checkpoint, "workspace_id": "foreign",
            },
        })
        with pytest.raises(worktree.WorktreeError, match="different removal checkpoint"):
            worktree._store_removal_checkpoint(Ledger(foreign), shared_record, checkpoint)

    def test_permission_retry_identity_accepts_unchanged_managed_checkout(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "verified-retry", base="main")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        admin = worktree._checkout_admin_identity(
            record.git_worktree_path, Path(shared_record.git_common_dir)
        )
        checkpoint = {
            "source_git_root": str(shared_record.source_git_root),
            "git_common_dir": str(shared_record.git_common_dir),
            "worktree_path": str(shared_record.worktree_path),
            "physical_worktree_path": str(shared_record.physical_worktree_path),
            "branch": shared_record.branch,
            "logical_name": record.logical_name,
            "checkout_identity": worktree._path_identity(record.git_worktree_path),
            "git_admin_path": admin["path"],
            "git_admin_identity": {"device": admin["device"], "inode": admin["inode"]},
        }

        worktree._verify_permission_retry_identity(
            tmp_repo, record.logical_name, record, shared_record, checkpoint,
            Path(admin["path"]),
        )

    def test_permission_retry_identity_refuses_checkpoint_and_ledger_drift(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "verified-drift", base="main")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        admin = worktree._checkout_admin_identity(
            record.git_worktree_path, Path(shared_record.git_common_dir)
        )
        checkpoint = {
            "source_git_root": str(shared_record.source_git_root),
            "git_common_dir": str(shared_record.git_common_dir),
            "worktree_path": str(shared_record.worktree_path),
            "physical_worktree_path": str(shared_record.physical_worktree_path),
            "branch": shared_record.branch,
            "logical_name": record.logical_name,
            "checkout_identity": worktree._path_identity(record.git_worktree_path),
            "git_admin_path": admin["path"],
            "git_admin_identity": {"device": admin["device"], "inode": admin["inode"]},
        }
        wrong_checkpoint = {**checkpoint, "logical_name": "other"}
        with pytest.raises(worktree.WorktreeError, match="no longer identifies"):
            worktree._verify_permission_retry_identity(
                tmp_repo, record.logical_name, record, shared_record,
                wrong_checkpoint, Path(admin["path"]),
            )

        shared = worktree._shared_worktree()
        monkeypatch.setattr(shared, "find_workspace", lambda *_args: None)
        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        with pytest.raises(worktree.WorktreeError, match="record is missing or changed"):
            worktree._verify_permission_retry_identity(
                tmp_repo, record.logical_name, record, shared_record,
                checkpoint, Path(admin["path"]),
            )

    @pytest.mark.parametrize(
        ("case", "message"),
        [
            ("missing-checkout-identity", "no checkout inode identity"),
            ("checkout-stat-error", "could not inspect verified checkout"),
            ("checkout-replaced", "path was replaced"),
            ("gitfile-read-error", "could not verify checkout Git link"),
            ("gitfile-malformed", "Git link is malformed"),
            ("admin-path-changed", "Git link changed"),
            ("missing-admin-identity", "no Git admin inode identity"),
            ("admin-stat-error", "could not inspect Git admin directory"),
            ("admin-reverse-read-error", "could not recheck Git admin reverse link"),
            ("admin-reverse-link", "reverse link no longer identifies"),
            ("registration-branch", "registration branch changed"),
            ("other-registration-owns-admin", "another registered checkout"),
            ("local-record-stat-error", "could not inspect CIU record"),
            ("local-record-corrupt", "could not recheck CIU record"),
            ("local-record-changed", "identity record changed"),
        ],
    )
    def test_permission_retry_identity_rejects_every_replaced_or_unreadable_fact(
        self, case, message, tmp_repo, fake_generate_env, tmp_path, monkeypatch
    ):
        record, shared_record, admin, checkpoint = self._verified_retry_identity_fixture(
            tmp_repo, fake_generate_env
        )
        shared = worktree._shared_worktree()
        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        checkout = record.git_worktree_path
        admin_path = Path(admin["path"])
        git_file = checkout / ".git"
        admin_gitfile = admin_path / "gitdir"
        record_path = record.record_path

        if case == "missing-checkout-identity":
            checkpoint.pop("checkout_identity")
        elif case == "checkout-stat-error":
            monkeypatch.setattr(
                worktree, "_path_identity",
                lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
            )
        elif case == "checkout-replaced":
            checkpoint["checkout_identity"] = {"device": -1, "inode": -1}
        elif case in {"gitfile-read-error", "admin-reverse-read-error"}:
            real_read_text = Path.read_text
            target = git_file if case == "gitfile-read-error" else admin_gitfile

            def fail_target_read(path, *args, **kwargs):
                if Path(path) == target:
                    raise PermissionError(errno.EACCES, "denied", str(target))
                return real_read_text(path, *args, **kwargs)

            monkeypatch.setattr(Path, "read_text", fail_target_read)
        elif case == "gitfile-malformed":
            git_file.write_text("not a gitdir link\n", encoding="utf-8")
        elif case == "admin-path-changed":
            checkpoint["git_admin_path"] = str(tmp_path / "other-admin")
        elif case == "missing-admin-identity":
            checkpoint.pop("git_admin_identity")
        elif case == "admin-stat-error":
            real_lstat = Path.lstat

            def fail_admin_stat(path, *args, **kwargs):
                if Path(path) == admin_path:
                    raise PermissionError(errno.EACCES, "denied", str(path))
                return real_lstat(path, *args, **kwargs)

            monkeypatch.setattr(Path, "lstat", fail_admin_stat)
        elif case == "admin-reverse-link":
            admin_gitfile.write_text(f"{tmp_path / 'other' / '.git'}\n", encoding="utf-8")
        elif case == "registration-branch":
            monkeypatch.setattr(shared, "list_git_worktrees", lambda *_args: [
                SimpleNamespace(path=checkout, branch="different-branch"),
            ])
        elif case == "other-registration-owns-admin":
            other = tmp_path / "other-checkout"
            other.mkdir()
            (other / ".git").write_text(f"gitdir: {admin_path}\n", encoding="utf-8")
            monkeypatch.setattr(shared, "list_git_worktrees", lambda *_args: [
                SimpleNamespace(path=checkout, branch=shared_record.branch),
                SimpleNamespace(path=other, branch="other-branch"),
            ])
        elif case in {"local-record-stat-error", "local-record-corrupt"}:
            error = (
                PermissionError(errno.EACCES, "denied", str(record_path))
                if case == "local-record-stat-error"
                else worktree.WorktreeError("corrupt record")
            )
            monkeypatch.setattr(
                worktree, "read_instance_record",
                lambda *_args: (_ for _ in ()).throw(error),
            )
            if case == "local-record-stat-error":
                real_lstat = Path.lstat

                def fail_record_stat(path, *args, **kwargs):
                    if Path(path) == record_path:
                        raise PermissionError(errno.EACCES, "denied", str(path))
                    return real_lstat(path, *args, **kwargs)

                monkeypatch.setattr(Path, "lstat", fail_record_stat)
        elif case == "local-record-changed":
            monkeypatch.setattr(worktree, "read_instance_record", lambda *_args: SimpleNamespace(
                logical_name="different-name", git_worktree_path=checkout,
                branch=record.branch, instance_id=record.instance_id,
                ciu_root_offset=record.ciu_root_offset,
            ))

        with pytest.raises(worktree.WorktreeError, match=message):
            worktree._verify_permission_retry_identity(
                tmp_repo, record.logical_name, record, shared_record,
                checkpoint, admin_path,
            )

    def test_permission_retry_identity_tolerates_only_absent_checkout_record(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, shared_record, admin, checkpoint = self._verified_retry_identity_fixture(
            tmp_repo, fake_generate_env
        )
        record.record_path.unlink()

        worktree._verify_permission_retry_identity(
            tmp_repo, record.logical_name, record, shared_record, checkpoint,
            Path(admin["path"]),
        )

    def test_permission_retry_identity_ignores_another_checkout_with_another_admin(
        self, tmp_repo, fake_generate_env, tmp_path, monkeypatch
    ):
        record, shared_record, admin, checkpoint = self._verified_retry_identity_fixture(
            tmp_repo, fake_generate_env
        )
        shared = worktree._shared_worktree()
        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        other = tmp_path / "other-checkout-with-unrelated-admin"
        other.mkdir()
        (other / ".git").write_text(
            f"gitdir: {tmp_path / 'unrelated-admin'}\n", encoding="utf-8"
        )
        monkeypatch.setattr(shared, "list_git_worktrees", lambda *_args: [
            SimpleNamespace(
                path=record.git_worktree_path, branch=shared_record.branch,
            ),
            SimpleNamespace(path=other, branch="other-branch"),
        ])

        worktree._verify_permission_retry_identity(
            tmp_repo, record.logical_name, record, shared_record,
            checkpoint, Path(admin["path"]),
        )

    def test_remove_recreates_missing_shared_record_for_a_managed_checkout(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "shared-record-recovery", base="main")
        shared = worktree._shared_worktree()
        real_find = shared.find_workspace
        lookups = []

        def temporarily_missing(common, path):
            lookups.append((common, path))
            return None if len(lookups) == 1 else real_find(common, path)

        monkeypatch.setattr(shared, "find_workspace", temporarily_missing)
        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        monkeypatch.setattr(worktree, "_shared_record_for_logical_name", lambda *_args: None)
        monkeypatch.setattr(worktree, "_ensure_shared_record", lambda *_args: None)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        document = worktree.remove_document(
            tmp_repo, record.logical_name, json_output=True
        )

        assert document["status"] == "removed"
        assert document["phase_report"]["phases"]["shared_record_removal"] == "created-and-removed"
        assert len(lookups) >= 2

    def test_remove_refuses_shared_adoption_that_returns_no_record(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "missing-shared-record", base="main")
        shared = worktree._shared_worktree()
        monkeypatch.setattr(shared, "find_workspace", lambda *_args: None)
        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        monkeypatch.setattr(worktree, "_shared_record_for_logical_name", lambda *_args: None)
        monkeypatch.setattr(worktree, "_ensure_shared_record", lambda *_args: None)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        with pytest.raises(worktree.WorktreeRemovalError, match="without an exact shared record"):
            worktree.remove_document(tmp_repo, record.logical_name)

    def test_remove_reports_failure_to_create_shared_removal_evidence(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "shared-evidence-error", base="main")
        shared = worktree._shared_worktree()
        monkeypatch.setattr(shared, "find_workspace", lambda *_args: None)
        monkeypatch.setattr(worktree, "_shared_worktree", lambda: shared)
        monkeypatch.setattr(worktree, "_shared_record_for_logical_name", lambda *_args: None)
        monkeypatch.setattr(
            worktree, "_ensure_shared_record",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("legacy adoption failed")),
        )
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        with pytest.raises(worktree.WorktreeRemovalError, match="could not create exact shared removal evidence"):
            worktree.remove_document(tmp_repo, record.logical_name)

    def test_remove_refuses_to_start_git_removal_without_saved_checkpoint(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "checkpoint-write-error", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(
            worktree, "_store_removal_checkpoint",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("checkpoint disk failure")),
        )

        with pytest.raises(worktree.WorktreeRemovalError, match="could not save exact removal recovery evidence"):
            worktree.remove_document(tmp_repo, record.logical_name)

    def test_consent_resumes_after_git_dropped_registration_and_uses_physical_path(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        from ciu import engine

        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + "locked/\n", encoding="utf-8"
        )
        assert _git(["add", ".gitignore"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "ignore permission fixture"], tmp_repo).returncode == 0
        record = worktree.create(tmp_repo, "permission-resume", base="main")
        checkout = record.git_worktree_path
        blocked = checkout / "locked"
        blocked.mkdir()
        (blocked / "root-owned.bin").write_bytes(b"state")
        blocked.chmod(0o500)
        shared = worktree._shared_record_for_logical_name(tmp_repo, record.logical_name)
        assert shared is not None
        common = shared.git_common_dir
        admin_path = Path(
            worktree._checkout_admin_identity(checkout, common)["path"]
        )
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        actual_run = subprocess.run
        removal_calls = []

        def unregister_then_report_permission(argv, *args, **kwargs):
            if argv[:3] == ["git", "worktree", "remove"] and not removal_calls:
                removal_calls.append(argv)
                # Model Git's observed partial-failure order: its admin record
                # and checkout-local CIU record are gone while the checkout's
                # .git file and protected data remain.
                shutil.rmtree(admin_path)
                record.record_path.unlink(missing_ok=True)
                return subprocess.CompletedProcess(
                    argv, 1, "", f"fatal: cannot remove '{blocked}': Permission denied"
                )
            return actual_run(argv, *args, **kwargs)

        monkeypatch.setattr(worktree.subprocess, "run", unregister_then_report_permission)
        helper_targets = []

        def fake_privileged_rmtree(physical_target, **_identities):
            physical_target = Path(physical_target)
            helper_targets.append(physical_target)
            physical_root = Path(shared.physical_worktree_path)
            relative = physical_target.relative_to(physical_root)
            assert relative == Path("locked")
            local_target = checkout / relative
            local_target.chmod(0o700)
            shutil.rmtree(local_target)

        monkeypatch.setattr(engine, "privileged_rmtree", fake_privileged_rmtree)

        with pytest.raises(worktree.WorktreeRemovalError) as offered:
            worktree.remove_document(tmp_repo, record.logical_name, json_output=True)

        retry_doc = offered.value.document.get("privileged_retry")
        assert retry_doc is not None, offered.value.document
        assert retry_doc["status"] == "offered"
        assert retry_doc["targets"] == ["locked"]
        assert retry_doc["remaining"] == [str(blocked)]
        assert retry_doc["command"] == (
            f"ciu worktree rm {record.logical_name} --root-escalation={record.branch}"
        )
        assert helper_targets == []
        assert offered.value.phase_report["phases"]["privileged_retry"] == "offered"
        assert checkout.is_dir()
        assert not admin_path.exists()
        assert not record.record_path.exists()

        with pytest.raises(worktree.WorktreeRemovalError, match="exact"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation="wrong-branch"
            )
        assert helper_targets == []

        replacement = tmp_repo.parent / "replacement" / checkout.name
        replacement.parent.mkdir()
        assert _git(
            ["worktree", "add", "-b", "replacement-owner", str(replacement), "main"],
            tmp_repo,
        ).returncode == 0
        replacement_admin = Path(
            worktree._checkout_admin_identity(replacement, common)["path"]
        )
        assert replacement_admin == admin_path
        with pytest.raises(worktree.WorktreeRemovalError, match="admin.*replaced"):
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch
            )
        assert helper_targets == []
        assert replacement.is_dir()
        assert _git(["worktree", "remove", "--force", str(replacement)], tmp_repo).returncode == 0

        def fail_privileged_rmtree(_physical_target, **_identities):
            raise RuntimeError("simulated helper failure")

        monkeypatch.setattr(engine, "privileged_rmtree", fail_privileged_rmtree)
        with pytest.raises(worktree.WorktreeRemovalError, match="remaining paths:") as retry_failure:
            worktree.remove_document(
                tmp_repo, record.logical_name, root_escalation=record.branch,
                json_output=True,
            )
        failed_retry = retry_failure.value.document["privileged_retry"]
        assert failed_retry["status"] == "failed"
        assert str(blocked) in failed_retry["remaining"]
        assert str(checkout) in failed_retry["remaining"]
        assert str(blocked) in str(retry_failure.value)

        monkeypatch.setattr(engine, "privileged_rmtree", fake_privileged_rmtree)
        resumed = worktree.remove_document(
            tmp_repo, record.logical_name, root_escalation=record.branch,
            json_output=True,
        )

        assert resumed["status"] == "removed"
        assert resumed["resumed"] is True
        assert resumed["privileged_retry"]["status"] == "complete"
        assert resumed["privileged_retry"]["targets"] == ["locked"]
        assert helper_targets == [Path(shared.physical_worktree_path) / "locked"]
        assert not checkout.exists()
        assert not admin_path.exists()
        assert not shared.record_path.exists()
        assert _git(["show-ref", "--verify", f"refs/heads/{record.branch}"], tmp_repo).returncode != 0

    def test_unrecorded_checkout_gets_temporary_durable_removal_evidence(
        self, tmp_repo, monkeypatch
    ):
        from ciu import engine

        ignore = tmp_repo / ".gitignore"
        ignore.write_text(
            ignore.read_text(encoding="utf-8") + "locked/\n", encoding="utf-8"
        )
        assert _git(["add", ".gitignore"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "ignore permission fixture"], tmp_repo).returncode == 0
        checkout = tmp_repo.parent / "unrecorded-permission-checkout"
        branch = "unrecorded-permission-checkout"
        assert _git(
            ["worktree", "add", "-b", branch, str(checkout), "main"], tmp_repo
        ).returncode == 0
        blocked = checkout / "locked"
        blocked.mkdir()
        (blocked / "payload").write_text("state", encoding="utf-8")
        blocked.chmod(0o500)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(
            worktree, "_physical_workspace_target",
            lambda _repo_root, target: Path(target),
        )

        shared = worktree._shared_worktree()
        common = shared.discover_git_root(tmp_repo)[1]
        admin_path = Path(
            worktree._checkout_admin_identity(checkout, common)["path"]
        )
        actual_run = subprocess.run
        attempts = []

        def unregister_then_fail(argv, *args, **kwargs):
            if argv[:3] == ["git", "worktree", "remove"] and not attempts:
                attempts.append(argv)
                shutil.rmtree(admin_path)
                return subprocess.CompletedProcess(
                    argv, 1, "", f"cannot remove '{blocked}': Operation not permitted"
                )
            return actual_run(argv, *args, **kwargs)

        monkeypatch.setattr(worktree.subprocess, "run", unregister_then_fail)
        helper_targets = []

        def fake_privileged_rmtree(physical_target, **_identities):
            physical_target = Path(physical_target)
            helper_targets.append(physical_target)
            assert physical_target.name == "locked"
            local_target = checkout / physical_target.relative_to(
                Path(worktree._physical_workspace_target(tmp_repo, checkout))
            )
            local_target.chmod(0o700)
            shutil.rmtree(local_target)

        monkeypatch.setattr(engine, "privileged_rmtree", fake_privileged_rmtree)

        document = worktree.remove_document(
            tmp_repo, branch, force=True, root_escalation=branch
        )

        assert document["status"] == "removed"
        assert document["phase_report"]["phases"]["shared_record_removal"] == "created-and-removed"
        assert document["privileged_retry"]["status"] == "complete"
        assert helper_targets == [Path(worktree._physical_workspace_target(tmp_repo, checkout)) / "locked"]
        assert not checkout.exists()
        assert not admin_path.exists()
        assert shared.find_workspace(common, checkout) is None


class TestRemovalBranchSafety:
    def _created_branch(self, tmp_repo, fake_generate_env, name):
        record = worktree.create(tmp_repo, name, base="main", branch=name)
        target = record.git_worktree_path
        (target / "branch-work.txt").write_text("work\n", encoding="utf-8")
        _git(["add", "branch-work.txt"], target)
        _git(["commit", "-m", f"work on {name}"], target)
        return record, _git(
            ["rev-parse", "--verify", f"refs/heads/{record.branch}"], tmp_repo
        ).stdout.strip()

    def test_rm_keeps_unmerged_branch_and_reports_tip(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, tip = self._created_branch(tmp_repo, fake_generate_env, "feature-unmerged")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        document = worktree.remove_document(tmp_repo, record.logical_name)

        assert _git(
            ["rev-parse", "--verify", f"refs/heads/{record.branch}"], tmp_repo
        ).stdout.strip() == tip
        assert document["branch"]["name"] == record.branch
        assert document["branch"]["tip"] == tip
        assert document["branch"]["outcome"] == "kept-unmerged"
        assert "not contained" in document["branch"]["reason"]
        assert document["phase_report"]["phases"]["branch_removal"] == "kept-unmerged"

    def test_rm_deletes_merged_branch_by_default(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, _tip = self._created_branch(tmp_repo, fake_generate_env, "feature-merged")
        _git(["merge", "--no-ff", "-m", "merge feature", record.branch], tmp_repo)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        document = worktree.remove_document(tmp_repo, record.logical_name)

        assert _git(["rev-parse", "--verify", record.branch], tmp_repo).returncode != 0
        assert document["branch"]["outcome"] == "deleted"

    def test_keep_branch_preserves_even_a_merged_branch(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, tip = self._created_branch(tmp_repo, fake_generate_env, "feature-kept")
        _git(["merge", "--no-ff", "-m", "merge feature", record.branch], tmp_repo)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        document = worktree.remove_document(
            tmp_repo, record.logical_name, keep_branch=True
        )

        assert _git(
            ["rev-parse", "--verify", f"refs/heads/{record.branch}"], tmp_repo
        ).stdout.strip() == tip
        assert document["branch"]["outcome"] == "kept-by-option"

    def test_delete_branch_explicitly_removes_unmerged_branch_and_reports_tip(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, tip = self._created_branch(tmp_repo, fake_generate_env, "feature-delete")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        document = worktree.remove_document(
            tmp_repo, record.logical_name, delete_branch=True
        )

        assert _git(["rev-parse", "--verify", record.branch], tmp_repo).returncode != 0
        assert document["branch"]["name"] == record.branch
        assert document["branch"]["tip"] == tip
        assert document["branch"]["outcome"] == "deleted"

    def test_rm_reports_compare_and_delete_failure_without_claiming_deletion(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, expected_tip = self._created_branch(
            tmp_repo, fake_generate_env, "feature-raced-delete"
        )
        _git(["merge", "--no-ff", "-m", "merge feature", record.branch], tmp_repo)
        tree = _git(["rev-parse", f"{expected_tip}^{{tree}}"], tmp_repo).stdout.strip()
        advanced_tip = _git(
            ["commit-tree", tree, "-p", expected_tip, "-m", "raced tip"], tmp_repo
        ).stdout.strip()
        original_run = subprocess.run
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        def move_before_delete(argv, *args, **kwargs):
            if argv[:3] == ["git", "update-ref", "-d"]:
                assert _git(
                    ["update-ref", f"refs/heads/{record.branch}", advanced_tip], tmp_repo
                ).returncode == 0
            return original_run(argv, *args, **kwargs)

        monkeypatch.setattr(worktree.subprocess, "run", move_before_delete)
        with pytest.raises(worktree.WorktreeRemovalError, match="branch removal") as exc:
            worktree.remove_document(tmp_repo, record.logical_name)
        assert _git(
            ["rev-parse", f"refs/heads/{record.branch}"], tmp_repo
        ).stdout.strip() == advanced_tip
        assert exc.value.document["branch"]["tip"] == expected_tip
        assert exc.value.document["branch"]["outcome"] == "failed"
        assert exc.value.phase_report["phases"]["branch_removal"] == "failed"

    def test_rm_preserves_branch_when_shared_record_removal_fails(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record, _tip = self._created_branch(
            tmp_repo, fake_generate_env, "feature-record-cleanup-fails"
        )
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        real_unlink = Path.unlink

        def fail_shared_record_unlink(path, *args, **kwargs):
            if path == shared_record.record_path:
                raise PermissionError("shared record is locked")
            return real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_shared_record_unlink)

        with pytest.raises(
            worktree.WorktreeRemovalError, match="shared worktree removal failed"
        ) as exc:
            worktree.remove_document(
                tmp_repo, record.logical_name, delete_branch=True
            )

        assert exc.value.phase_report["phases"]["branch_removal"] == "skipped"
        assert exc.value.phase_report["phases"]["shared_record_removal"] == "failed"
        assert exc.value.document["branch"]["outcome"] == "not-deleted"
        assert not record.git_worktree_path.exists()
        assert _git(
            ["rev-parse", "--verify", f"refs/heads/{record.branch}"], tmp_repo
        ).returncode == 0

    def test_rm_reports_detached_branch_as_skipped(self, tmp_repo, monkeypatch):
        target = tmp_repo.parent / "detached-remove"
        assert _git(
            ["worktree", "add", "--detach", str(target), "main"], tmp_repo
        ).returncode == 0
        exclude = tmp_repo / ".git" / "info" / "exclude"
        exclude.write_text(exclude.read_text(encoding="utf-8") + "\n.assay/\n", encoding="utf-8")
        state = target / ".assay" / "progress.jsonl"
        state.parent.mkdir()
        state.write_text("resume\n", encoding="utf-8")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)

        with pytest.raises(
            worktree.WorktreeRemovalError, match="no branch name for the exact discard option"
        ):
            worktree.remove_document(tmp_repo, target.name, force=True)
        assert state.read_text(encoding="utf-8") == "resume\n"

        state.unlink()
        state.parent.rmdir()
        document = worktree.remove_document(tmp_repo, target.name, force=True)

        assert document["branch"]["name"] is None
        assert document["branch"]["outcome"] == "skipped-detached"
        assert document["phase_report"]["phases"]["branch_removal"] == "skipped-detached"
        assert not target.exists()

    def test_rm_requires_local_branch_consent_for_dirty_detached_checkout(
        self, tmp_repo, monkeypatch
    ):
        target = tmp_repo.parent / "dirty-detached-remove"
        assert _git(
            ["worktree", "add", "--detach", str(target), "main"], tmp_repo
        ).returncode == 0
        changed = target / "README.md"
        changed.write_text("local edit\n", encoding="utf-8")
        monkeypatch.setattr(
            worktree, "_clean_in",
            lambda *_args, **_kwargs: pytest.fail("must refuse before cleanup"),
        )

        with pytest.raises(
            worktree.WorktreeRemovalError,
            match="uncommitted changes remain in detached checkout.*attach it",
        ):
            worktree.remove_document(tmp_repo, target.name)
        assert changed.is_file()
        assert target.is_dir()


class TestRemovalHelpers:
    def test_safe_root_offset_requires_relative_repository_path(self):
        assert worktree._safe_root_offset("services/ciu", label="test") == Path(
            "services/ciu"
        )
        for value in (None, "", "/absolute", "../outside"):
            with pytest.raises(worktree.WorktreeError, match="CIU-root offset"):
                worktree._safe_root_offset(value, label="test")

    def test_shared_record_reads_checkout_file_and_snapshot(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "shared-record")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        assert worktree._instance_record_from_shared(
            shared_record, record.logical_name
        ) == record

        record.record_path.unlink()
        restored = worktree._instance_record_from_shared(
            shared_record, record.logical_name
        )
        assert restored == record

    def test_shared_record_refuses_mismatches_and_unsafe_metadata(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "shared-mismatch")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        assert worktree._instance_record_from_shared(
            replace(shared_record, purpose="generic"), record.logical_name
        ) is None
        assert worktree._instance_record_from_shared(
            replace(shared_record, labels={}), record.logical_name
        ) is None
        for offset in (None, "../escape"):
            metadata = dict(shared_record.metadata)
            if offset is None:
                metadata.pop("ciu_root_offset", None)
            else:
                metadata["ciu_root_offset"] = offset
            invalid = replace(shared_record, metadata=metadata)
            with pytest.raises(worktree.WorktreeError, match="CIU-root offset"):
                worktree._instance_record_from_shared(invalid, record.logical_name)
        with pytest.raises(worktree.WorktreeError, match="disagrees"):
            worktree._instance_record_from_shared(
                replace(shared_record, branch="wrong-branch"), record.logical_name
            )

    def test_shared_record_refuses_nonregular_unreadable_and_missing_records(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "shared-record-errors")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        record_path = record.record_path
        original_lstat = Path.lstat

        def directory_record(path):
            if path == record_path:
                return SimpleNamespace(st_mode=stat.S_IFDIR)
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", directory_record)
        with pytest.raises(worktree.WorktreeError, match="not a regular file"):
            worktree._instance_record_from_shared(shared_record, record.logical_name)
        monkeypatch.setattr(Path, "lstat", original_lstat)

        def unreadable_record(path):
            if path == record_path:
                raise PermissionError("denied")
            return original_lstat(path)

        monkeypatch.setattr(Path, "lstat", unreadable_record)
        with pytest.raises(worktree.WorktreeError, match="could not inspect CIU record"):
            worktree._instance_record_from_shared(shared_record, record.logical_name)
        monkeypatch.setattr(Path, "lstat", original_lstat)

        with monkeypatch.context() as m:
            m.setattr(
                worktree, "read_instance_record",
                lambda *_args: (_ for _ in ()).throw(ValueError("bad record")),
            )
            with pytest.raises(
                worktree.WorktreeError, match="could not read the shared CIU record"
            ):
                worktree._instance_record_from_shared(
                    shared_record, record.logical_name
                )

        record.record_path.unlink()
        metadata = dict(shared_record.metadata)
        metadata.pop(worktree._CIU_WORKTREE_RECORD_METADATA, None)
        with pytest.raises(worktree.WorktreeError, match="neither a readable checkout"):
            worktree._instance_record_from_shared(
                replace(shared_record, metadata=metadata), record.logical_name
            )

    def test_primary_root_resolution_and_identity_clean_command(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "primary-clean")
        assert worktree._primary_ciu_root_for_record(tmp_repo, record) == tmp_repo
        monkeypatch.setattr(
            worktree, "_ciu_root_committed_at_checkout", lambda *_args: False
        )
        with pytest.raises(worktree.WorktreeError, match="does not contain the recorded"):
            worktree._primary_ciu_root_for_record(tmp_repo, record)

        monkeypatch.setattr(
            worktree, "_ciu_root_committed_at_checkout", lambda *_args: True
        )
        (tmp_repo / GLOBAL_CONFIG_DEFAULTS).unlink()
        with pytest.raises(worktree.WorktreeError, match="cannot find the shared CIU root"):
            worktree._primary_ciu_root_for_record(tmp_repo, record)
        (tmp_repo / GLOBAL_CONFIG_DEFAULTS).write_text("[ciu]\n", encoding="utf-8")

        calls = []
        monkeypatch.setenv("CIU_SERVICES_PROFILE", "wrong-profile")
        monkeypatch.setattr(
            worktree.subprocess, "run",
            lambda argv, **kwargs: (calls.append((argv, kwargs)) or
                                    subprocess.CompletedProcess(argv, 0)),
        )
        assert worktree._clean_with_primary_identity(
            tmp_repo,
            "right-id",
            expected_checkout=tmp_repo / "old-checkout",
            yes=True,
        ) == 0
        argv, kwargs = calls[0]
        assert argv[-1] == "-y" and "right-id" in argv
        assert argv[argv.index("--expected-checkout") + 1] == str(
            tmp_repo / "old-checkout"
        )
        assert kwargs["cwd"] == str(tmp_repo)
        assert kwargs["env"].get("CIU_SERVICES_PROFILE") is None
        monkeypatch.setattr(
            worktree.subprocess, "run",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no python")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not run identity-scoped clean"):
            worktree._clean_with_primary_identity(
                tmp_repo,
                "right-id",
                expected_checkout=tmp_repo / "old-checkout",
                yes=False,
            )

    def test_branch_removal_plan_fails_closed_for_missing_merge_evidence(
        self, tmp_repo, monkeypatch
    ):
        checkout = worktree.WorktreeInfo(tmp_repo / "wt", "feature", "head", False)
        monkeypatch.setattr(
            worktree, "_git",
            lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "no branch"),
        )
        with pytest.raises(worktree.WorktreeError, match="could not read branch tip"):
            worktree._branch_removal_plan(
                tmp_repo, checkout, None, keep_branch=False, delete_branch=False
            )

        branch_tip = "a" * 40

        def no_base(args, _cwd):
            if args[:2] == ["rev-parse", "--verify"] and args[-1] == "refs/heads/feature":
                return subprocess.CompletedProcess(args, 0, branch_tip, "")
            return subprocess.CompletedProcess(args, 1, "", "missing")

        monkeypatch.setattr(worktree, "_git", no_base)
        monkeypatch.setattr(worktree, "_default_branch", lambda *_args: None)
        plan = worktree._branch_removal_plan(
            tmp_repo, checkout, None, keep_branch=False, delete_branch=False
        )
        assert plan["outcome"] == "kept-unmerged"
        assert plan["reason"] == "no local base branch proves this tip merged"

        monkeypatch.setattr(worktree, "_default_branch", lambda *_args: "deleted-base")
        plan = worktree._branch_removal_plan(
            tmp_repo, checkout, None, keep_branch=False, delete_branch=False
        )
        assert "unavailable" in plan["reason"]

    def test_branch_removal_plan_uses_legacy_base_ref_when_available(
        self, tmp_repo, monkeypatch
    ):
        legacy = worktree.WorktreeInstanceRecord(
            logical_name="legacy", display_name="legacy", branch="feature",
            git_worktree_path=tmp_repo / "feature", ciu_root_offset=Path("."),
            created_at_utc="2026-11-02T00:00:00Z", base_ref="main",
            state="ready", schema_version=5,
        )
        checkout = worktree.WorktreeInfo(
            legacy.git_worktree_path, legacy.branch, "head", False
        )
        calls = []

        def git(args, _cwd):
            calls.append(args)
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 0, "a" * 40, "")

        monkeypatch.setattr(worktree, "_git", git)
        plan = worktree._branch_removal_plan(
            tmp_repo, checkout, legacy, keep_branch=False, delete_branch=False
        )
        assert plan["delete"] is True
        assert plan["outcome"] == "deleted"
        assert any(args[-1] == "refs/heads/main" for args in calls)

    def test_branch_removal_plan_ignores_missing_legacy_base_ref(
        self, tmp_repo, monkeypatch
    ):
        assert _git(["branch", "feature"], tmp_repo).returncode == 0
        legacy = worktree.WorktreeInstanceRecord(
            logical_name="legacy", display_name="legacy", branch="feature",
            git_worktree_path=tmp_repo / "feature", ciu_root_offset=Path("."),
            created_at_utc="2026-11-02T00:00:00Z", base_ref="retired-base",
            state="ready", schema_version=5,
        )
        checkout = worktree.WorktreeInfo(
            legacy.git_worktree_path, legacy.branch, "head", False
        )
        real_git = worktree._git
        calls = []

        def git(args, cwd):
            calls.append(args)
            return real_git(args, cwd)

        # Observe the stale-base lookup and prove mergedness against the live
        # local mainline selected after that saved legacy ref is unavailable.
        monkeypatch.setattr(worktree, "_git", git)
        plan = worktree._branch_removal_plan(
            tmp_repo, checkout, legacy, keep_branch=False, delete_branch=False
        )

        assert plan["delete"] is True
        assert "base branch 'main'" in plan["reason"]
        assert ["rev-parse", "--verify", "refs/heads/retired-base"] in calls
        assert any(
            args[:2] == ["merge-base", "--is-ancestor"]
            and args[-1] == "refs/heads/main"
            for args in calls
        )


class TestRemovalRefusals:
    def test_rm_rejects_conflicting_branch_intents_and_multiple_state_names(self, tmp_repo):
        with pytest.raises(worktree.WorktreeRemovalError, match="cannot be used together"):
            worktree.remove(
                tmp_repo, "missing", keep_branch=True, delete_branch=True
            )
        with pytest.raises(worktree.WorktreeRemovalError, match="one exact"):
            worktree.remove(
                tmp_repo, "missing", discard_protected_state=["one", "two"]
            )

    def test_rm_reports_missing_and_primary_checkouts(self, tmp_repo):
        with pytest.raises(worktree.WorktreeRemovalError, match="no worktree named"):
            worktree.remove(tmp_repo, "missing")
        with pytest.raises(worktree.WorktreeRemovalError, match="PRIMARY checkout"):
            worktree.remove(tmp_repo, str(tmp_repo))

    def test_rm_rejects_discard_authorization_without_local_branch(self, tmp_repo):
        target = tmp_repo.parent / "detached-disposition"
        assert _git(["worktree", "add", "--detach", str(target), "main"], tmp_repo).returncode == 0
        with pytest.raises(worktree.WorktreeRemovalError, match="applies only to a checkout attached"):
            worktree.remove(
                tmp_repo, target.name, force=True, discard_protected_state="main"
            )
        with pytest.raises(worktree.WorktreeRemovalError, match="applies only to a checkout on a local branch"):
            worktree.remove(
                tmp_repo, target.name, force=True, discard_changes="main"
            )

    def test_rm_resolves_logical_shared_record_without_local_branch_name(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(
            tmp_repo, "logical-only", branch="physical-branch"
        )
        real_find_worktree = worktree.find_worktree
        monkeypatch.setattr(
            worktree, "find_worktree",
            lambda root, name: (
                None if name == "logical-only" else real_find_worktree(root, name)
            ),
        )
        shared = worktree._shared_worktree()
        monkeypatch.setattr(shared, "find_workspace", lambda *_args: None)
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kw: 1)
        with pytest.raises(worktree.WorktreeRemovalError, match="NOT removing") as exc:
            worktree.remove_document(tmp_repo, "logical-only")
        assert exc.value.phase_report["phases"]["target_resolution"] == "complete"
        assert exc.value.document["instance"]["logical_name"] == "logical-only"
        assert record.git_worktree_path.is_dir()

    def test_rm_refuses_changed_git_identity_and_shared_record_facts(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "identity-check")
        shared = worktree._shared_worktree()
        original_discover = shared.discover_git_context

        def changed_context(path):
            if Path(path).resolve() == record.git_worktree_path.resolve():
                return record.git_worktree_path.resolve(), Path("/wrong/common"), record.branch, "head"
            return original_discover(path)

        monkeypatch.setattr(shared, "discover_git_context", changed_context)
        with pytest.raises(worktree.WorktreeRemovalError, match="could not verify shared Git ownership"):
            worktree.remove(tmp_repo, record.logical_name)

        monkeypatch.setattr(shared, "discover_git_context", original_discover)
        actual_shared = shared.find_workspace(
            record.git_worktree_path.parent.parent / ".git", record.git_worktree_path
        )
        if actual_shared is None:
            actual_shared = worktree._shared_record_for_logical_name(
                tmp_repo, record.logical_name
            )
        assert actual_shared is not None
        monkeypatch.setattr(
            shared, "find_workspace",
            lambda *_args: replace(actual_shared, branch="wrong-branch"),
        )
        with pytest.raises(worktree.WorktreeRemovalError, match="shared worktree record"):
            worktree.remove(tmp_repo, record.logical_name)

    def test_rm_refuses_logical_path_and_instance_record_mismatches(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "path-mismatch")
        shared_record = worktree._shared_record_for_logical_name(
            tmp_repo, record.logical_name
        )
        assert shared_record is not None
        wrong_path = replace(
            shared_record, worktree_path=tmp_repo / "some-other-checkout"
        )
        with monkeypatch.context() as m:
            m.setattr(
                worktree, "_shared_record_for_logical_name", lambda *_args: wrong_path
            )
            with pytest.raises(
                worktree.WorktreeRemovalError, match="shared record.*names"
            ):
                worktree.remove(tmp_repo, record.logical_name)

        original_find_record = worktree.find_instance_record
        monkeypatch.setattr(
            worktree, "find_instance_record",
            lambda *args, **kwargs: replace(
                original_find_record(*args, **kwargs), branch="wrong-branch"
            ),
        )
        with pytest.raises(worktree.WorktreeRemovalError, match="CIU identity record"):
            worktree.remove(tmp_repo, record.logical_name)

    def test_rm_reports_ignored_state_and_branch_plan_failures(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "preflight-errors")
        with monkeypatch.context() as m:
            m.setattr(
                worktree, "_ignored_state_summary",
                lambda *_args: (_ for _ in ()).throw(
                    worktree.WorktreeError("index unavailable")
                ),
            )
            with pytest.raises(
                worktree.WorktreeRemovalError, match="index unavailable"
            ) as exc:
                worktree.remove_document(tmp_repo, record.logical_name)
            assert (
                exc.value.phase_report["phases"]["resource_cleanup"]
                == "skipped-ignored-state-unknown"
            )

        with monkeypatch.context() as m:
            m.setattr(
                worktree, "_worktree_change_paths",
                lambda *_args: (_ for _ in ()).throw(
                    worktree.WorktreeError("status unavailable")
                ),
            )
            with pytest.raises(
                worktree.WorktreeRemovalError, match="status unavailable"
            ) as exc:
                worktree.remove_document(tmp_repo, record.logical_name)
            assert (
                exc.value.phase_report["phases"]["resource_cleanup"]
                == "failed-status-inspection"
            )

        monkeypatch.setattr(
            worktree, "_branch_removal_plan",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(worktree.WorktreeError("tip missing")),
        )
        with pytest.raises(worktree.WorktreeRemovalError, match="tip missing"):
            worktree.remove_document(tmp_repo, record.logical_name)

    def test_rm_reports_indeterminate_ciu_root_marker(self, tmp_repo, fake_generate_env, monkeypatch):
        record = worktree.create(tmp_repo, "marker-inspection")
        monkeypatch.setattr(
            worktree, "_ciu_root_marker_present",
            lambda *_args: (_ for _ in ()).throw(
                worktree.WorktreeError("marker unreadable")
            ),
        )
        with pytest.raises(worktree.WorktreeRemovalError, match="marker unreadable") as exc:
            worktree.remove_document(tmp_repo, record.logical_name)
        assert exc.value.phase_report["phases"]["resource_cleanup"] == "failed"
        assert record.git_worktree_path.is_dir()

    @pytest.mark.parametrize(
        ("disposition", "match"),
        [
            ("wrong-branch", "exact checkout branch"),
            ("all", "not 'all'"),
        ],
    )
    def test_rm_rejects_invalid_protected_state_dispositions(
        self, tmp_repo, fake_generate_env, monkeypatch, disposition, match
    ):
        record = worktree.create(
            tmp_repo, "no-protected-state", branch="physical-branch"
        )
        with pytest.raises(worktree.WorktreeRemovalError, match=match):
            worktree.remove(
                tmp_repo, record.logical_name,
                discard_protected_state=disposition,
            )

    def test_rm_reports_clean_and_lease_release_failures(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "clean-failure")
        with monkeypatch.context() as m:
            m.setattr(
                worktree, "_clean_in",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    worktree.WorktreeError("clean raised")
                ),
            )
            with pytest.raises(worktree.WorktreeRemovalError, match="clean raised"):
                worktree.remove_document(tmp_repo, record.logical_name)

        record = worktree.create(tmp_repo, "lease-failure")
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kwargs: 0)
        monkeypatch.setattr(
            worktree, "release_own_lease",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("lease locked")),
        )
        with pytest.raises(worktree.WorktreeRemovalError, match="could not release") as exc:
            worktree.remove_document(tmp_repo, record.logical_name)
        assert exc.value.phase_report["phases"]["lease_release"] == "failed"

    @pytest.mark.parametrize("failure", ["missing-id", "primary-root", "clean-status"])
    def test_rm_markerless_managed_fallback_reports_each_failure(
        self, tmp_repo, fake_generate_env, monkeypatch, failure
    ):
        record = worktree.create(tmp_repo, f"markerless-{failure}")
        (record.ciu_root / GLOBAL_CONFIG_DEFAULTS).unlink()
        if failure == "missing-id":
            original_find_record = worktree.find_instance_record
            monkeypatch.setattr(
                worktree, "find_instance_record",
                lambda *args, **kwargs: replace(
                    original_find_record(*args, **kwargs),
                    state="allocating", instance_id=None, network="recorded-network",
                ),
            )
            message = "no instance id"
        elif failure == "primary-root":
            record.record_path.unlink()
            monkeypatch.setattr(
                worktree, "_primary_ciu_root_for_record",
                lambda *_args: (_ for _ in ()).throw(
                    worktree.WorktreeError("primary CIU root missing")
                ),
            )
            message = "primary CIU root missing"
        else:
            monkeypatch.setattr(
                workspace_env,
                "_detect_physical_repo_root",
                lambda _root: record.ciu_root,
            )
            monkeypatch.setattr(
                worktree, "_clean_with_primary_identity",
                lambda *_args, **_kwargs: 1,
            )
            message = "ciu clean.*failed"

        with pytest.raises(worktree.WorktreeRemovalError, match=message) as exc:
            worktree.remove_document(
                tmp_repo,
                record.logical_name,
                discard_changes=record.branch,
            )
        assert exc.value.phase_report["phases"]["resource_cleanup"] == "failed"
        assert record.git_worktree_path.is_dir()


class TestExactWorktreeControl:
    """S16.6 — `worktree up` and `worktree exec` with an exact sanitized
    target environment, exact argv, and exact child exit-code propagation."""

    @pytest.fixture
    def ready(self, tmp_repo, fake_generate_env, write_instance_facts):
        """A ready managed instance whose generated facts file carries the
        FULL required identity vocabulary (repo_root, physical_repo_root,
        repo_name, instance_id, network), all matching the record."""
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
    def fake_run(self, monkeypatch):
        seen = {"calls": []}

        def fake_run(argv, cwd, env):
            seen["last"] = (argv, {"cwd": cwd, "env": env})
            seen["calls"].append(argv)
            return subprocess.CompletedProcess(argv, 0, "out", "")

        monkeypatch.setattr(worktree, "_run_child", fake_run)
        return seen

    # -- sanitized target environment ----------------------------------------

    def test_sanitized_env_strips_sibling_identity_and_overlays_target(self, ready, monkeypatch):
        repo_root, record = ready
        monkeypatch.setenv("REPO_ROOT", "/sibling-A")
        monkeypatch.setenv("INSTANCE_ID", "sibling-id-A")
        monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "sibling-A-network")
        monkeypatch.setenv("CIU_SERVICES_PROFILE", "core")
        monkeypatch.setenv("KEEP_ME", "ambient")
        env = worktree._sanitized_target_env(repo_root, record)
        assert env["REPO_ROOT"] == str(record.ciu_root)
        assert env["INSTANCE_ID"] == record.instance_id
        assert env["DOCKER_NETWORK_INTERNAL"] == record.network
        assert "CIU_SERVICES_PROFILE" not in env
        assert env["KEEP_ME"] == "ambient"

    def test_sanitized_env_refuses_missing_required_key(
        self, ready, write_instance_facts
    ):
        repo_root, record = ready
        write_instance_facts(
            record.ciu_root,
            repo_root=str(record.ciu_root),
            physical_repo_root=str(record.ciu_root),
            repo_name="",
            instance_id=record.instance_id,
            network=record.network,
        )
        with pytest.raises(
            worktree.WorktreeError, match="lacks required identity fact.*repo_name"
        ):
            worktree._sanitized_target_env(repo_root, record)

    def test_sanitized_env_refuses_root_instance_or_network_mismatch(
        self, ready, write_instance_facts
    ):
        repo_root, record = ready
        good = {
            "repo_root": str(record.ciu_root),
            "physical_repo_root": str(record.ciu_root),
            "repo_name": "repo",
            "instance_id": record.instance_id,
            "network": record.network,
        }
        cases = [
            ("repo_root", "/elsewhere", r"repo_root.*does not match"),
            ("instance_id", "wrong-id", r"instance_id.*does not match"),
            ("network", "wrong-net", r"network.*does not match"),
        ]
        for key, value, match in cases:
            write_instance_facts(record.ciu_root, **{**good, key: value})
            with pytest.raises(worktree.WorktreeError, match=match):
                worktree._sanitized_target_env(repo_root, record)
            write_instance_facts(record.ciu_root, **good)

    def test_sanitized_env_surfaces_unreadable_env(self, ready):
        repo_root, record = ready
        (record.ciu_root / "ciu.instance.generated.toml").write_text(
            "[ciu.instance.generated]\nrepo_root = not-a-toml-value\n",
            encoding="utf-8",
        )
        with pytest.raises(worktree.WorktreeError, match="could not read.*malformed"):
            worktree._sanitized_target_env(repo_root, record)

    # -- up_instance ---------------------------------------------------------

    def test_up_invokes_existing_up_in_target_root(self, ready, fake_run):
        repo_root, record = ready
        assert worktree.up_instance(repo_root, "ctrl") == 0
        argv, kwargs = fake_run["last"]
        assert argv == [sys.executable, "-m", "ciu.cli", "up"]
        assert kwargs["cwd"] == record.ciu_root
        assert kwargs["env"]["REPO_ROOT"] == str(record.ciu_root)
        assert not (record.ciu_root / GLOBAL_CONFIG_RENDERED).exists()

    def test_up_propagates_exact_child_exit_code(self, ready, monkeypatch, capsys):
        repo_root, record = ready
        monkeypatch.setattr(
            worktree, "_run_child",
            lambda argv, cwd, env: subprocess.CompletedProcess(argv, 17, "out", ""),
        )
        assert worktree.up_instance(repo_root, "ctrl") == 17
        capsys.readouterr()  # output captured; the exit code is still 17

    def test_up_uses_all_declared_profiles_in_one_child_invocation(
        self, ready, monkeypatch, fake_run,
    ):
        repo_root, record = ready
        config = {
            "ciu": {"worktree": {"up": ["core", "db"]}},
            "deploy": {"profiles": {
                "core": {"stacks": ["infra/core"]},
                "db": {"stacks": ["infra/db"]},
            }},
        }
        monkeypatch.setattr(
            worktree.config_model,
            "render_global_chain",
            lambda *_args, **_kwargs: config,
        )

        assert worktree.up_instance(repo_root, "ctrl") == 0
        argv, kwargs = fake_run["last"]
        assert argv == [
            sys.executable, "-m", "ciu.cli", "up",
            "--profile", "core", "--profile", "db", "--deploy", "--healthcheck",
        ]
        assert kwargs["cwd"] == record.ciu_root

    def test_up_refuses_same_name_network_replacement_before_child_start(
        self, ready, fake_run, monkeypatch
    ):
        repo_root, record = ready
        recorded = replace(record, network_id="4" * 64)
        worktree._write_instance_record(recorded)
        monkeypatch.setattr(
            worktree,
            "_docker_network_details",
            lambda _network: {"id": "5" * 64, "labels": {}},
        )

        with pytest.raises(
            worktree.RuntimeNetworkOwnershipUnprovenError,
            match="cannot prove ownership",
        ):
            worktree.up_instance(repo_root, "ctrl")

        assert fake_run["calls"] == []
        failed = worktree.read_instance_record(record.record_path)
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "runtime-verification-failed"
        assert failed.network_id == "4" * 64

    def test_up_probe_failure_refuses_without_demoting_ready_record(
        self, ready, fake_run, monkeypatch
    ):
        repo_root, record = ready

        def probe_failure(_network):
            raise worktree.RuntimeNetworkProbeError("docker permission denied")

        monkeypatch.setattr(worktree, "_docker_network_details", probe_failure)
        with pytest.raises(
            worktree.RuntimeNetworkProbeError, match="permission denied"
        ):
            worktree.up_instance(repo_root, "ctrl")

        assert fake_run["calls"] == []
        current = worktree.read_instance_record(record.record_path)
        assert current.state == "ready"
        assert current.recovery_status is None

    def test_up_all_ignores_declared_profiles(self, ready, monkeypatch, fake_run):
        repo_root, _record = ready
        monkeypatch.setattr(
            worktree.config_model,
            "render_global_chain",
            lambda *_args, **_kwargs: {
                "ciu": {"worktree": {"up": ["core"]}},
                "deploy": {"profiles": {"core": {"stacks": ["infra/core"]}}},
            },
        )

        assert worktree.up_instance(repo_root, "ctrl", all_profiles=True) == 0
        argv, _kwargs = fake_run["last"]
        assert argv == [sys.executable, "-m", "ciu.cli", "up"]

    def test_up_refuses_missing_instance(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError, match="no managed worktree instance"):
            worktree.up_instance(tmp_repo, "ghost")

    def test_up_refuses_not_ready_instance(self, ready, monkeypatch):
        repo_root, record = ready
        allocating = worktree.replace(record, state="allocating")
        monkeypatch.setattr(
            worktree, "find_instance_record", lambda *_a, **_kw: allocating
        )
        with pytest.raises(worktree.WorktreeError, match="allocating, not ready"):
            worktree.up_instance(repo_root, "ctrl")

    def test_up_surfaces_child_start_failure(self, ready, monkeypatch):
        repo_root, record = ready
        monkeypatch.setattr(
            worktree, "_run_child",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no exec")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not run `ciu worktree up ctrl`"):
            worktree.up_instance(repo_root, "ctrl")

    # -- exec_instance -------------------------------------------------------

    def test_exec_passes_exact_hostile_argv_without_shell(self, ready, fake_run):
        repo_root, record = ready
        hostile = ["--", "echo", "a b", "$(whoami)", ";", "-x", "*"]
        assert worktree.exec_instance(repo_root, "ctrl", hostile) == 0
        argv, kwargs = fake_run["last"]
        assert argv == ["echo", "a b", "$(whoami)", ";", "-x", "*"]
        assert kwargs["cwd"] == record.ciu_root
        assert "shell" not in kwargs

    def test_exec_propagates_exact_exit_code_even_when_output_captured(self, ready, monkeypatch, capsys):
        repo_root, record = ready
        monkeypatch.setattr(
            worktree, "_run_child",
            lambda argv, cwd, env: subprocess.CompletedProcess(argv, 17, "out", ""),
        )
        assert worktree.exec_instance(repo_root, "ctrl", ["--", "true"]) == 17
        capsys.readouterr()

    def test_exec_requires_separator_and_argv(self, ready):
        repo_root, record = ready
        with pytest.raises(worktree.WorktreeError, match="requires a `--` separator"):
            worktree.exec_instance(repo_root, "ctrl", [])
        with pytest.raises(worktree.WorktreeError, match="requires a `--` separator"):
            worktree.exec_instance(repo_root, "ctrl", ["echo", "hi"])
        with pytest.raises(worktree.WorktreeError, match="at least one argv element after `--`"):
            worktree.exec_instance(repo_root, "ctrl", ["--"])

    def test_exec_never_starts_cleans_or_renders_implicitly(self, ready, fake_run):
        repo_root, record = ready
        worktree.exec_instance(repo_root, "ctrl", ["--", "pwd"])
        assert fake_run["calls"] == [["pwd"]]

    def test_exec_real_child_propagates_exact_exit_code(self, ready):
        """A REAL subprocess integration fixture: exec_instance runs the child
        through the actual _run_child/subprocess path, and the child's own
        exit code (23) is what comes back — no wrapper masking."""
        repo_root, record = ready
        argv = ["--", sys.executable, "-c", "import sys; sys.exit(23)"]
        assert worktree.exec_instance(repo_root, "ctrl", argv) == 23

    def test_exec_surfaces_child_start_failure(self, ready, monkeypatch):
        repo_root, record = ready
        monkeypatch.setattr(
            worktree, "_run_child",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no exec")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not run exec argv"):
            worktree.exec_instance(repo_root, "ctrl", ["--", "pwd"])


class TestExecTargets:
    """S16.7 — declared container targets for `worktree exec --target`:
    exact config grammar (O1), exact running-container selection + worktree
    mount proof + shell-free docker exec (O2), capability after ship (O3)."""

    GLOBAL_TEMPLATE = """\
[ciu]
env = "test"

[deploy]
project_name = "myapp"
environment_tag = "dev"
network_name = "$DOCKER_NETWORK_INTERNAL"

[ciu.worktree.exec_targets.tester]
stack = "test"
service = "tester"
workdir = "/workspace"

[ciu.worktree.exec_targets.utility]
stack = "test"
service = "utility"
workdir = "/opt"
requires_worktree_mount = false
"""

    @pytest.fixture
    def target_repo(self, tmp_path, fake_generate_env):
        repo = tmp_path / "repo"
        repo.mkdir()
        assert _git(["init", "-b", "main"], repo).returncode == 0
        assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
        assert _git(["config", "user.name", "Test"], repo).returncode == 0
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        (repo / ".gitignore").write_text("ciu.env\n", encoding="utf-8")
        (repo / "ciu.global.defaults.toml.j2").write_text(
            self.GLOBAL_TEMPLATE, encoding="utf-8"
        )
        assert _git(["add", "."], repo).returncode == 0
        assert _git(["commit", "-m", "init"], repo).returncode == 0
        worktree.create(repo, "ctrl")
        record = worktree.find_instance_record(repo, "ctrl")
        assert record is not None and record.state == "ready"
        (record.ciu_root / "ciu.env").write_text(
            f'export REPO_ROOT="{record.ciu_root}"\n'
            f'export PHYSICAL_REPO_ROOT="{record.ciu_root}"\n'
            f'export REPO_NAME="repo"\n'
            f'export INSTANCE_ID="{record.instance_id}"\n'
            f'export DOCKER_NETWORK_INTERNAL="{record.network}"\n',
            encoding="utf-8",
        )
        return repo, record

    def _fake_docker(
        self, monkeypatch, *, ps_ids=(), mounts=None, exec_rc=0, exec_log=None,
        inspect_rc=0, inspect_out=None, exec_error=None, inspect_error=None,
    ):
        calls = {"ps": [], "inspect": [], "exec": []}

        def fake(args, **kw):
            verb = args[0]
            calls[verb].append(args)
            if verb == "ps":
                out = "\n".join(ps_ids) + ("\n" if ps_ids else "")
                return subprocess.CompletedProcess(args, 0, out, "")
            if verb == "inspect":
                if inspect_error is not None:
                    raise inspect_error
                if inspect_out is not None:
                    return subprocess.CompletedProcess(args, inspect_rc, inspect_out, "")
                return subprocess.CompletedProcess(args, inspect_rc, json.dumps(mounts or []), "")
            if verb == "exec":
                if exec_error is not None:
                    raise exec_error
                if exec_log is not None:
                    exec_log.append(args)
                return subprocess.CompletedProcess(args, exec_rc, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(worktree.procutil, "docker", fake)
        return calls

    def _mount(self, record, workdir="/workspace"):
        return {"Source": str(record.git_worktree_path), "Destination": workdir}

    # -- O1: config grammar -------------------------------------------------

    def test_parse_exec_targets_valid_and_mount_defaults_true(self):
        targets = worktree.parse_exec_targets(
            {"tester": {"stack": "test", "service": "tester", "workdir": "/workspace"}}
        )
        t = targets["tester"]
        assert (t.alias, t.stack, t.service, t.workdir) == ("tester", "test", "tester", "/workspace")
        assert t.requires_worktree_mount is True

    def test_parse_exec_targets_mount_false_is_explicit_opt_out(self):
        targets = worktree.parse_exec_targets(
            {"tester": {"stack": "s", "service": "s", "workdir": "/w", "requires_worktree_mount": False}}
        )
        assert targets["tester"].requires_worktree_mount is False

    def test_parse_exec_targets_rejects_unknown_key(self):
        with pytest.raises(worktree.WorktreeError, match="unknown key.*ports"):
            worktree.parse_exec_targets(
                {"tester": {"stack": "s", "service": "s", "workdir": "/w", "ports": [1]}}
            )

    def test_parse_exec_targets_rejects_empty_service(self):
        with pytest.raises(worktree.WorktreeError, match="non-empty string 'service'"):
            worktree.parse_exec_targets(
                {"tester": {"stack": "s", "service": "", "workdir": "/w"}}
            )

    def test_parse_exec_targets_rejects_non_string_stack(self):
        with pytest.raises(worktree.WorktreeError, match="non-empty string 'stack'"):
            worktree.parse_exec_targets(
                {"tester": {"stack": 3, "service": "s", "workdir": "/w"}}
            )

    def test_parse_exec_targets_rejects_non_bool_mount_flag(self):
        with pytest.raises(worktree.WorktreeError, match="must be a boolean"):
            worktree.parse_exec_targets(
                {"tester": {"stack": "s", "service": "s", "workdir": "/w", "requires_worktree_mount": "yes"}}
            )

    def test_parse_exec_targets_rejects_invalid_alias(self):
        with pytest.raises(worktree.WorktreeError, match="invalid exec target alias"):
            worktree.parse_exec_targets({"bad/alias": {"stack": "s", "service": "s", "workdir": "/w"}})

    def test_parse_exec_targets_rejects_non_table_entry(self):
        with pytest.raises(worktree.WorktreeError, match="must be a table"):
            worktree.parse_exec_targets({"tester": "nope"})

    def test_resolve_exec_targets_config_shapes(self):
        good = {"ciu": {"worktree": {"exec_targets": {"t": {"stack": "s", "service": "s", "workdir": "/w"}}}}}
        assert "t" in worktree.resolve_exec_targets_config(good)
        assert worktree.resolve_exec_targets_config({}) == {}
        assert worktree.resolve_exec_targets_config({"ciu": {"worktree": None}}) == {}
        assert worktree.resolve_exec_targets_config({"ciu": {"worktree": {}}}) == {}
        with pytest.raises(worktree.WorktreeError, match=r"\[ciu\] must be a table"):
            worktree.resolve_exec_targets_config({"ciu": []})
        with pytest.raises(worktree.WorktreeError, match=r"\[ciu.worktree\] must be a table"):
            worktree.resolve_exec_targets_config({"ciu": {"worktree": []}})
        with pytest.raises(worktree.WorktreeError, match=r"\[ciu.worktree.exec_targets\] must be a table"):
            worktree.resolve_exec_targets_config({"ciu": {"worktree": {"exec_targets": []}}})

    # -- O2: exact selection + mount proof + docker exec --------------------

    def test_exec_target_success_constructs_exact_docker_exec(self, target_repo, monkeypatch):
        repo, record = target_repo
        exec_log = []
        calls = self._fake_docker(
            monkeypatch, ps_ids=["abc123"], mounts=[self._mount(record)], exec_rc=23, exec_log=exec_log,
        )
        assert worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd", "a b"]) == 23
        assert calls["ps"][0] == [
            "ps",
            "--filter", "label=com.docker.compose.project=myapp-dev-test",
            "--filter", "label=com.docker.compose.service=tester",
            "--filter", f"network={record.network}",
            "--format", "{{.ID}}",
        ]
        assert calls["inspect"][0] == ["inspect", "--format", "{{json .Mounts}}", "abc123"]
        # No `--` after the container id: docker exec treats post-CONTAINER
        # tokens verbatim, so a `--` there would be executed as the command
        # (checkpoint-B review, measured live: exit 127).
        assert exec_log[0] == ["exec", "-w", "/workspace", "abc123", "pwd", "a b"]

    def test_exec_target_zero_containers_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        self._fake_docker(monkeypatch, ps_ids=[])
        with pytest.raises(worktree.WorktreeError, match="exactly one running container.*found 0"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_multiple_containers_refuse(self, target_repo, monkeypatch):
        repo, _ = target_repo
        self._fake_docker(monkeypatch, ps_ids=["a", "b"])
        with pytest.raises(worktree.WorktreeError, match="found 2"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_wrong_mount_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        self._fake_docker(
            monkeypatch, ps_ids=["abc123"],
            mounts=[{"Source": "/some/other/checkout", "Destination": "/workspace"}],
        )
        with pytest.raises(worktree.WorktreeError, match="does not mount the selected worktree"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_non_dict_mount_entry_is_skipped(self, target_repo, monkeypatch):
        repo, record = target_repo
        calls = self._fake_docker(
            monkeypatch, ps_ids=["abc123"],
            mounts=[["not", "a", "mount"], self._mount(record)],
        )
        assert worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"]) == 0
        assert calls["exec"][0][0] == "exec"

    def test_exec_target_non_string_mount_source_is_skipped(self, target_repo, monkeypatch):
        repo, record = target_repo
        calls = self._fake_docker(
            monkeypatch, ps_ids=["abc123"],
            mounts=[{"Source": 123, "Destination": "/workspace"}, self._mount(record)],
        )
        assert worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"]) == 0
        assert calls["exec"][0][0] == "exec"

    def test_exec_target_correct_source_wrong_destination_refuses(self, target_repo, monkeypatch):
        """Source matches the selected worktree but the destination does not
        contain the declared workdir — must refuse, not pass."""
        repo, record = target_repo
        self._fake_docker(
            monkeypatch, ps_ids=["abc123"],
            mounts=[{"Source": str(record.git_worktree_path), "Destination": "/other"}],
        )
        with pytest.raises(worktree.WorktreeError, match="does not mount the selected worktree"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_inspect_unreachable_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        self._fake_docker(
            monkeypatch, ps_ids=["abc123"], inspect_error=OSError("daemon gone")
        )
        with pytest.raises(worktree.WorktreeError, match="could not inspect target container"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_mount_opt_out_skips_proof_but_keeps_uniqueness(self, target_repo, monkeypatch):
        repo, _ = target_repo
        calls = self._fake_docker(monkeypatch, ps_ids=["xyz"], mounts=[])
        assert worktree.exec_target_instance(repo, "ctrl", "utility", ["--", "pwd"]) == 0
        assert calls["inspect"] == []
        assert calls["ps"][0][0] == "ps"

    def test_exec_target_ps_query_uses_exact_identity_filters(self, target_repo, monkeypatch):
        repo, record = target_repo
        calls = self._fake_docker(monkeypatch, ps_ids=["abc123"], mounts=[self._mount(record)])
        worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])
        ps_args = calls["ps"][0]
        # a sibling worktree's service (different network) can never match
        assert f"label=com.docker.compose.project=myapp-dev-test" in ps_args
        assert f"label=com.docker.compose.service=tester" in ps_args
        assert f"network={record.network}" in ps_args

    def test_exec_target_unknown_alias_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        self._fake_docker(monkeypatch)
        with pytest.raises(worktree.WorktreeError, match="no exec target alias 'ghost'.*declared: tester, utility"):
            worktree.exec_target_instance(repo, "ctrl", "ghost", ["--", "pwd"])

    def test_exec_target_requires_separator_and_argv(self, target_repo):
        repo, _ = target_repo
        with pytest.raises(worktree.WorktreeError, match="requires a `--` separator"):
            worktree.exec_target_instance(repo, "ctrl", "tester", [])
        with pytest.raises(worktree.WorktreeError, match="at least one argv element after `--`"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--"])

    def test_exec_target_ps_failure_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "daemon down"),
        )
        with pytest.raises(worktree.WorktreeError, match="docker ps.*failed"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_ps_unreachable_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        monkeypatch.setattr(
            worktree.procutil, "docker",
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("docker")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not query target container"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_inspect_failure_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        self._fake_docker(monkeypatch, ps_ids=["abc123"], inspect_rc=1, inspect_out="oops")
        with pytest.raises(worktree.WorktreeError, match="docker inspect.*failed"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_inspect_unparseable_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        self._fake_docker(monkeypatch, ps_ids=["abc123"], inspect_out="not json")
        with pytest.raises(worktree.WorktreeError, match="unparseable mounts"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_exec_unreachable_refuses(self, target_repo, monkeypatch):
        repo, record = target_repo
        self._fake_docker(
            monkeypatch, ps_ids=["abc123"], mounts=[self._mount(record)],
            exec_error=OSError("no exec"),
        )
        with pytest.raises(worktree.WorktreeError, match="could not run `docker exec`"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_exec_target_compose_project_undefinable_refuses(self, target_repo, monkeypatch):
        repo, _ = target_repo
        monkeypatch.setattr(
            worktree.config_model, "render_global_chain",
            lambda *a, **kw: {
                "ciu": {"worktree": {"exec_targets": {
                    "tester": {"stack": "test", "service": "tester", "workdir": "/workspace"},
                }}}
            },
        )
        with pytest.raises(worktree.WorktreeError, match="could not derive the compose project"):
            worktree.exec_target_instance(repo, "ctrl", "tester", ["--", "pwd"])

    def test_workdir_within_component_semantics(self):
        assert worktree._workdir_within("/workspace", "/workspace") is True
        assert worktree._workdir_within("/workspace/sub dir", "/workspace") is True
        assert worktree._workdir_within("/workspaceX", "/workspace") is False
        assert worktree._workdir_within("/other", "/workspace") is False


def test_registry_warning_flushes_each_diagnostic(monkeypatch, tmp_path):
    stream = _FlushRecordingStream()
    monkeypatch.setattr(worktree.sys, "stderr", stream)
    problem = worktree._InstanceRecordProblem(
        record_path=tmp_path / "bad-record.json",
        worktree_path=tmp_path / "checkout",
        logical_name="broken",
        detail="invalid shape",
    )

    worktree._warn_registry_problems((problem,))

    assert "continuing with the readable records" in stream.getvalue()
    assert str(problem.record_path) in stream.getvalue()
    assert "invalid shape" in stream.getvalue()
    assert stream.flush_count == 2


def test_record_logical_name_hint_ignores_truthy_non_string_names(tmp_path):
    path = tmp_path / "record.json"
    path.write_text('{"logical_name": 17}', encoding="utf-8")
    assert worktree._record_logical_name_hint(path) is None

    path.write_text('{"logical_name": ""}', encoding="utf-8")
    assert worktree._record_logical_name_hint(path) is None


def test_worktree_up_profiles_reject_whitespace_only_names():
    with pytest.raises(worktree.WorktreeError, match="non-empty array"):
        worktree._validate_worktree_table({"up": ["   "]})


@pytest.mark.parametrize(
    ("head", "base_commit", "expected_parts"),
    [
        (
            SimpleNamespace(returncode=128, stdout="stdout detail", stderr="stderr detail"),
            "different-base",
            ("stderr detail",),
        ),
        (
            SimpleNamespace(returncode=128, stdout="", stderr=""),
            "",
            ("workspace base=missing", "current HEAD=unreadable"),
        ),
    ],
)
def test_allocated_commit_refusal_preserves_complete_provenance(
    monkeypatch, tmp_path, head, base_commit, expected_parts,
):
    record = SimpleNamespace(
        fork_point_sha=None,
        base_ref="not-a-commit-ref",
        git_worktree_path=tmp_path,
    )
    monkeypatch.setattr(worktree, "_git", lambda *_a, **_kw: head)
    with pytest.raises(worktree.WorktreeError) as exc_info:
        worktree._allocated_commit_sha(
            record, SimpleNamespace(base_commit=base_commit)
        )
    message = str(exc_info.value)
    for expected in expected_parts:
        assert expected in message


def test_allocated_commit_error_prefers_git_error_over_workspace_fallback(
    monkeypatch, tmp_path,
):
    workspace_base = "a" * 40
    record = SimpleNamespace(
        fork_point_sha=None,
        base_ref="not-a-commit-ref",
        git_worktree_path=tmp_path,
    )
    monkeypatch.setattr(
        worktree, "_git",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=128, stdout="secondary output", stderr="primary git error"
        ),
    )
    with pytest.raises(worktree.WorktreeError) as exc_info:
        worktree._allocated_commit_sha(
            record, SimpleNamespace(base_commit=workspace_base)
        )
    assert "primary git error" in str(exc_info.value)
    assert "secondary output" not in str(exc_info.value)


def test_generated_allocation_recovery_does_not_apply_adopt_head_guard(
    tmp_repo, fake_generate_env, monkeypatch,
):
    base_sha = _git(["rev-parse", "HEAD"], tmp_repo).stdout.strip()
    attempts = 0

    def fail_first_generation(path, *, identity_only=False, allocation_lock_held=False):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return 1
        return fake_generate_env(path, identity_only=identity_only)

    monkeypatch.setattr(worktree, "_generate_env_in", fail_first_generation)
    with pytest.raises(worktree.WorktreeError, match="ciu env generate.*failed"):
        worktree.create(tmp_repo, "full-sha-base", base=base_sha)

    partial = worktree.find_instance_record(tmp_repo, "full-sha-base")
    assert partial is not None
    assert partial.base_ref == base_sha
    assert partial.fork_point_sha == base_sha

    (partial.git_worktree_path / "operator-work.txt").write_text(
        "keep this resumed commit\n", encoding="utf-8"
    )
    assert _git(["add", "operator-work.txt"], partial.git_worktree_path).returncode == 0
    assert _git(["commit", "-m", "operator work"], partial.git_worktree_path).returncode == 0
    operator_head = _git(
        ["rev-parse", "HEAD"], partial.git_worktree_path
    ).stdout.strip()
    assert operator_head != base_sha

    resumed = worktree.ensure(tmp_repo, "full-sha-base")
    assert resumed.state == "ready"
    assert _git(["rev-parse", "HEAD"], partial.git_worktree_path).stdout.strip() == operator_head
    assert (partial.git_worktree_path / "operator-work.txt").is_file()


@pytest.mark.parametrize(
    ("recovery_status", "fork_point", "base_ref", "adopted"),
    [
        ("checkout-incomplete", None, "full-sha", False),
        ("env-generation-failed", "full-sha", "full-sha", False),
        ("env-generation-failed", None, "main", False),
        ("env-generation-failed", None, "full-sha", True),
    ],
)
def test_adopted_record_fact_controls_the_no_reset_marker(
    tmp_repo, fake_generate_env, monkeypatch,
    recovery_status, fork_point, base_ref, adopted,
):
    base_sha = _git(["rev-parse", "HEAD"], tmp_repo).stdout.strip()
    record = worktree.create(tmp_repo, "recovery-shape", base=base_sha)
    edited = replace(
        record,
        state="recovery-required",
        recovery_status=recovery_status,
        fork_point_sha=(base_sha if fork_point == "full-sha" else None),
        base_ref=(base_sha if base_ref == "full-sha" else base_ref),
        adopted=adopted,
    )
    # A created record that never checkpointed its post-reset HEAD resumes the
    # initial reset only while BOTH durable markers still prove the reset has
    # not started: the local pending flag and the shared `not-started` phase.
    # (Otherwise recovery is operator-directed and never repeats the reset.)
    resumes_reset = (
        fork_point is None and recovery_status == "checkout-incomplete"
        and not adopted
    )
    if resumes_reset:
        edited = replace(edited, checkout_reset_pending=True)
    worktree._write_instance_record(edited)
    if resumes_reset:
        worktree._write_shared_ciu_record_snapshot(
            worktree._ensure_shared_record(tmp_repo, edited),
            edited,
            checkout_reset_phase="not-started",
        )
        # Make the checkout genuinely unpopulated, as `--no-checkout` leaves
        # it: empty index, no working-tree files except Git's own pointer and
        # the (untracked, excluded) CIU record.
        checkout = edited.git_worktree_path
        assert _git(["read-tree", "--empty"], checkout).returncode == 0
        for child in checkout.iterdir():
            if child.name in {".git", worktree.WORKTREE_INSTANCE_RECORD}:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    real_write = worktree._write_instance_record
    attempted = []

    def interrupt_after_transition(candidate):
        real_write(candidate)
        attempted.append(candidate)
        if resumes_reset and len(attempted) == 1:
            # The resumed reset first checkpoints its exact HEAD (clearing the
            # pending marker); the recovery classification is the NEXT write.
            assert candidate.fork_point_sha == base_sha
            assert candidate.checkout_reset_pending is False
            return
        raise KeyboardInterrupt("pause after recovery classification")

    monkeypatch.setattr(worktree, "_write_instance_record", interrupt_after_transition)
    with pytest.raises(KeyboardInterrupt, match="recovery classification"):
        worktree.ensure(tmp_repo, "recovery-shape")

    assert attempted
    classified = attempted[-1]
    if adopted:
        assert classified.state == "recovery-required"
        assert classified.recovery_status == "env-generation-failed"
    else:
        assert classified.state == "allocating"
        assert classified.recovery_status is None


@pytest.mark.parametrize("resume", [False, True])
def test_adopt_allows_an_existing_network_for_its_own_checkout(
    tmp_repo, fake_generate_env, monkeypatch, resume,
):
    target = tmp_repo.parent / f"adopted-network-{resume}"
    assert _git(
        ["worktree", "add", "-b", target.name, str(target), "main"], tmp_repo
    ).returncode == 0
    expected_network = f"repo-{_instance_id_for(target)}-network"
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda network: (
            {
                "id": "c" * 64,
                "labels": {
                    worktree._NETWORK_OWNER_LABEL: _instance_id_for(target)
                },
            }
            if network == expected_network else None
        ),
    )

    if resume:
        write_overlay = worktree._write_rendered_worktree_overlay
        monkeypatch.setattr(
            worktree, "_write_rendered_worktree_overlay",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("interrupted overlay")),
        )
        with pytest.raises(worktree.WorktreeError, match="interrupted overlay"):
            worktree.adopt(tmp_repo, target.name, str(target), profile="core")
        monkeypatch.setattr(worktree, "_write_rendered_worktree_overlay", write_overlay)
        ready = worktree.ensure(tmp_repo, target.name)
    else:
        ready = worktree.adopt(tmp_repo, target.name, str(target))

    assert ready.state == "ready"
    assert ready.network == expected_network
    assert ready.network_id == "c" * 64


def test_adopt_requires_explicit_claim_for_an_unlabeled_legacy_network(
    tmp_repo, fake_generate_env, monkeypatch,
):
    target = tmp_repo.parent / "adopted-legacy-network"
    assert _git(
        ["worktree", "add", "-b", target.name, str(target), "main"], tmp_repo
    ).returncode == 0
    expected_network = f"repo-{_instance_id_for(target)}-network"
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda network: (
            {"id": "d" * 64, "labels": {}}
            if network == expected_network else None
        ),
    )

    with pytest.raises(worktree.WorktreeError, match="cannot prove ownership"):
        worktree.adopt(tmp_repo, target.name, str(target))

    partial = worktree.find_instance_record(tmp_repo, target.name)
    assert partial is not None
    assert partial.state == "recovery-required"
    assert partial.network_id is None

    ready = worktree.ensure(
        tmp_repo, target.name, adopt_existing_network=True
    )
    assert ready.state == "ready"
    assert ready.network_id == "d" * 64


def test_explicit_network_claim_refuses_when_no_network_object_exists(
    tmp_repo, fake_generate_env, monkeypatch
):
    target = tmp_repo.parent / "adopted-missing-network"
    assert _git(
        ["worktree", "add", "-b", target.name, str(target), "main"], tmp_repo
    ).returncode == 0
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _network: None)

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="no existing runtime network.*available to adopt",
    ):
        worktree.adopt(
            tmp_repo, target.name, str(target), adopt_existing_network=True
        )

    partial = worktree.find_instance_record(tmp_repo, target.name)
    assert partial is not None
    assert partial.state == "recovery-required"
    assert partial.recovery_status == "runtime-verification-failed"
    assert partial.network_id is None


def test_direct_managed_startup_verifies_and_saves_the_network_object_id(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "direct-startup-network")
    docker_id = "a" * 64
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda network: (
            {
                "id": docker_id,
                "labels": {worktree._NETWORK_OWNER_LABEL: record.instance_id},
            }
            if network == record.network else None
        ),
    )

    verified = worktree.verify_own_runtime_network(
        record.ciu_root,
        expected_instance_id=record.instance_id,
        expected_network=record.network,
    )

    assert verified is not None
    assert verified.network_id == docker_id
    saved = worktree.read_instance_record(record.record_path)
    assert saved.network_id == docker_id


def test_final_managed_startup_verification_requires_network_to_still_exist(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "direct-startup-network-disappeared")
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _network: None)

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="disappeared before Compose startup",
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
            require_network_present=True,
        )

    saved = worktree.read_instance_record(record.record_path)
    assert saved.state == "ready"
    assert saved.recovery_status is None


def test_direct_managed_startup_records_unknown_network_as_verification_failure(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "direct-startup-replacement")
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda network: (
            {"id": "b" * 64, "labels": {}}
            if network == record.network else None
        ),
    )

    with pytest.raises(
        worktree.RuntimeNetworkOwnershipUnprovenError,
        match="cannot prove ownership",
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
        )

    failed = worktree.read_instance_record(record.record_path)
    assert failed.state == "recovery-required"
    assert failed.recovery_status == "runtime-verification-failed"


def test_direct_managed_startup_refuses_generated_identity_mismatch_before_probe(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "direct-startup-identity-mismatch")
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda _network: pytest.fail("identity mismatch must refuse before Docker"),
    )

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="generated runtime identity.*does not match",
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id="different-instance",
            expected_network=record.network,
        )

    saved = worktree.read_instance_record(record.record_path)
    assert saved.state == "ready"
    assert saved.recovery_status is None


def test_direct_managed_startup_refuses_duplicate_sibling_identity_before_probe(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "direct-startup-family-owner")
    sibling = worktree.create(tmp_repo, "direct-startup-family-duplicate")
    duplicate = replace(
        worktree.read_instance_record(sibling.record_path),
        instance_id=record.instance_id,
        network=record.network,
    )
    worktree._write_instance_record(duplicate)
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda _network: pytest.fail("family conflict must refuse before Docker"),
    )

    with pytest.raises(
        worktree.RuntimeNetworkOwnershipError,
        match="already belongs to logical instance",
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
        )


def test_direct_managed_startup_refuses_unreadable_committed_ciu_sibling(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "direct-startup-family-incomplete")
    sibling = worktree.create(tmp_repo, "direct-startup-family-corrupt")
    sibling.record_path.write_text('{"logical_name":', encoding="utf-8")
    (sibling.ciu_root / worktree.GLOBAL_CONFIG_DEFAULTS).unlink()
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda _network: pytest.fail("unreadable sibling must refuse before Docker"),
    )

    with pytest.raises(
        worktree.RuntimeNetworkVerificationError,
        match="cannot verify runtime identity uniqueness.*CIU-root sibling",
    ):
        worktree.verify_own_runtime_network(
            record.ciu_root,
            expected_instance_id=record.instance_id,
            expected_network=record.network,
        )


def test_ready_ensure_refuses_same_name_replacement_network(
    tmp_repo, fake_generate_env, monkeypatch,
):
    record = worktree.create(tmp_repo, "network-replaced")
    record = replace(record, network_id="e" * 64)
    worktree._write_instance_record(record)
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda _network: {"id": "f" * 64, "labels": {}},
    )

    with pytest.raises(worktree.WorktreeError, match="cannot prove ownership"):
        worktree.ensure(tmp_repo, "network-replaced")

    failed = worktree.read_instance_record(record.record_path)
    assert failed.state == "recovery-required"
    assert failed.recovery_status == "runtime-verification-failed"
    assert failed.network_id == "e" * 64


def test_ready_ensure_marks_a_conflicting_owner_label_as_collision(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "network-foreign-label")
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda _network: {
            "id": "6" * 64,
            "labels": {worktree._NETWORK_OWNER_LABEL: "foreign-instance"},
        },
    )

    with pytest.raises(
        worktree.RuntimeNetworkOwnershipError, match="carries owner instance"
    ):
        worktree.ensure(tmp_repo, "network-foreign-label")

    failed = worktree.read_instance_record(record.record_path)
    assert failed.state == "recovery-required"
    assert failed.recovery_status == "runtime-collision"


def test_ready_ensure_keeps_last_network_id_while_network_is_absent(
    tmp_repo, fake_generate_env, monkeypatch,
):
    record = worktree.create(tmp_repo, "network-outage")
    record = replace(record, network_id="1" * 64)
    worktree._write_instance_record(record)
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _network: None)

    ready = worktree.ensure(tmp_repo, "network-outage")

    assert ready.state == "ready"
    assert ready.network_id == "1" * 64


def test_ensure_probe_permission_error_is_not_network_absence(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "network-probe-denied")
    # The fixture suppresses Docker probes during creation. Restore only the
    # probe under test; keep its fake env generator and the other fixtures.
    with monkeypatch.context() as m:
        m.setattr(worktree, "_docker_network_exists", _REAL_DOCKER_NETWORK_EXISTS)
        m.setattr(
            worktree.procutil,
            "docker",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                PermissionError("docker denied")
            ),
        )
        with pytest.raises(
            worktree.RuntimeNetworkProbeError, match="docker denied"
        ):
            worktree.ensure(tmp_repo, "network-probe-denied")

    assert worktree.read_instance_record(record.record_path).state == "ready"


def test_explicit_network_claim_cannot_replace_saved_object_id(
    tmp_repo, fake_generate_env, monkeypatch
):
    record = worktree.create(tmp_repo, "network-claim-once")
    record = replace(record, network_id="2" * 64)
    worktree._write_instance_record(record)
    monkeypatch.setattr(
        worktree,
        "_docker_network_details",
        lambda _network: {"id": "3" * 64, "labels": {}},
    )

    with pytest.raises(
        worktree.RuntimeNetworkOwnershipUnprovenError,
        match="cannot prove ownership",
    ):
        worktree.ensure(
            tmp_repo, "network-claim-once", adopt_existing_network=True
        )

    failed = worktree.read_instance_record(record.record_path)
    assert failed.network_id == "2" * 64
    assert failed.state == "recovery-required"


def test_permission_target_parent_inspection_failure_is_reported(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    target = checkout / "target"
    target.touch()
    identity = worktree._path_identity(checkout)
    target_identity = worktree._path_identity(target)
    target_info = target.lstat()
    item = {
        "path": "target",
        **target_identity,
        "mode": stat.S_IFMT(target_info.st_mode),
        "parent_device": identity["device"],
        "parent_inode": identity["inode"],
    }
    real_lstat = Path.lstat
    checkout_calls = 0

    def fail_parent_lstat(path, *args, **kwargs):
        nonlocal checkout_calls
        if Path(path) == checkout:
            checkout_calls += 1
            if checkout_calls == 2:
                raise PermissionError(errno.EACCES, "parent denied", str(path))
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fail_parent_lstat)
    with pytest.raises(worktree.WorktreeError, match="could not inspect permission retry target parent"):
        worktree._validated_retry_target(
            checkout, item, expected_checkout_identity=identity
        )


def test_permission_inventory_does_not_record_a_target_below_a_symlink_parent(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    real_directory = checkout / "real-directory"
    real_directory.mkdir()
    target = real_directory / "target"
    target.touch()
    linked_parent = checkout / "linked-parent"
    linked_parent.symlink_to(real_directory, target_is_directory=True)
    monkeypatch.setattr(
        worktree, "_permission_error_path", lambda _message: [linked_parent / "target"]
    )

    candidates, capped = worktree._permission_retry_candidates(
        checkout, "permission denied"
    )

    assert candidates == []
    assert capped is False


@pytest.mark.parametrize(
    ("checkpoint", "message"),
    [
        ({}, "no valid checkpoint time"),
        ({"checkpoint_time_ns": True, "ignored_state": []}, "no valid checkpoint time"),
        ({"checkpoint_time_ns": -1, "ignored_state": []}, "no valid checkpoint time"),
        ({"checkpoint_time_ns": 2**63 - 1}, "no ignored-state inventory"),
        ({"checkpoint_time_ns": 2**63 - 1, "ignored_state": None}, "no ignored-state inventory"),
        ({"checkpoint_time_ns": 2**63 - 1, "ignored_state": [None]}, "malformed ignored-state inventory"),
        ({"checkpoint_time_ns": 2**63 - 1, "ignored_state": [{"path": 7}]}, "malformed ignored-state inventory"),
        ({"checkpoint_time_ns": 2**63 - 1, "ignored_state": [{"path": "/absolute"}]}, "unsafe ignored-state path"),
        ({"checkpoint_time_ns": 2**63 - 1, "ignored_state": [{"path": "../escape"}]}, "unsafe ignored-state path"),
        ({"checkpoint_time_ns": 2**63 - 1, "ignored_state": [{"path": ""}]}, "unsafe ignored-state path"),
    ],
)
def test_permission_retry_snapshot_rejects_malformed_checkpoints(
    tmp_path, checkpoint, message
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    with pytest.raises(worktree.WorktreeError, match=message):
        worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])


def test_permission_retry_snapshot_accepts_checkpointed_protected_components(tmp_path):
    checkout = tmp_path / "checkout"
    protected = checkout / ".assay" / "prior-run"
    protected.mkdir(parents=True)
    (protected / "state.json").write_text("checkpointed", encoding="utf-8")
    checkpoint = {
        "checkpoint_time_ns": 2**63 - 1,
        "ignored_state": [{
            "path": ".assay/prior-run/state.json",
            "class": "protected",
            "protected_reason": "name",
        }],
    }

    snapshot = worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])

    assert ".assay" in snapshot
    assert ".assay/prior-run/state.json" in snapshot


def test_permission_retry_snapshot_accepts_protected_components_below_checkpointed_ignored_parent(
    tmp_path,
):
    checkout = tmp_path / "checkout"
    cache = checkout / "cache"
    volume = cache / "vol-postgres-data"
    assay = volume / ".assay" / "prior-run"
    assay.mkdir(parents=True)
    (assay / "state.json").write_text("checkpointed", encoding="utf-8")
    checkpoint = {
        "checkpoint_time_ns": 2**63 - 1,
        "ignored_state": [{
            "path": "cache",
            "class": "protected",
            "protected_reason": "descendant",
        }],
    }

    snapshot = worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])

    assert "cache/vol-postgres-data" in snapshot
    assert "cache/vol-postgres-data/.assay" in snapshot
    assert "cache/vol-postgres-data/.assay/prior-run/state.json" in snapshot


@pytest.mark.parametrize(
    ("entry_class", "protected_reason"),
    [
        ("unclassified", "descendant"),
        ("protected", "name"),
        ("protected", "walk-capped"),
        ("protected", "unreadable"),
    ],
)
def test_permission_retry_snapshot_refuses_nested_protected_component_without_descendant_evidence(
    tmp_path, entry_class, protected_reason,
):
    checkout = tmp_path / "checkout"
    state = checkout / "cache" / "vol-postgres-data" / ".assay" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("must remain", encoding="utf-8")
    checkpoint = {
        "checkpoint_time_ns": 2**63 - 1,
        "ignored_state": [{
            "path": "cache",
            "class": entry_class,
            "protected_reason": protected_reason,
        }],
    }

    with pytest.raises(
        worktree.WorktreeError,
        match="new protected-state component: cache/vol-postgres-data",
    ):
        worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])


def test_permission_retry_snapshot_refuses_protected_component_outside_ignored_state(
    tmp_path,
):
    checkout = tmp_path / "checkout"
    state = checkout / "outside" / "vol-new-data" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("must remain", encoding="utf-8")
    checkpoint = {"checkpoint_time_ns": 2**63 - 1, "ignored_state": []}

    with pytest.raises(
        worktree.WorktreeError,
        match="new protected-state component: outside/vol-new-data",
    ):
        worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])


def test_permission_retry_snapshot_still_refuses_newer_nested_protected_state(
    tmp_path,
):
    checkout = tmp_path / "checkout"
    state = checkout / "cache" / "vol-postgres-data" / ".assay" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("changed after checkpoint", encoding="utf-8")
    checkpoint = {
        "checkpoint_time_ns": 1,
        "ignored_state": [{
            "path": "cache",
            "class": "protected",
            "protected_reason": "descendant",
        }],
    }

    with pytest.raises(
        worktree.WorktreeError,
        match="changed after the permission checkpoint",
    ):
        worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])


def test_permission_retry_snapshot_records_but_does_not_descend_into_blocker(
    tmp_path,
):
    checkout = tmp_path / "checkout"
    blocker = checkout / "locked"
    (blocker / "nested").mkdir(parents=True)
    (blocker / "nested" / "payload").write_text("state", encoding="utf-8")
    checkpoint = {"checkpoint_time_ns": 2**63 - 1, "ignored_state": []}
    candidates = [{"path": "locked"}]

    snapshot = worktree._permission_retry_tree_snapshot(
        checkout, checkpoint, candidates
    )

    blocker_info = blocker.lstat()
    assert "locked" in snapshot
    assert snapshot["locked"] == (
        blocker_info.st_dev,
        blocker_info.st_ino,
        stat.S_IFMT(blocker_info.st_mode),
        stat.S_IMODE(blocker_info.st_mode),
        blocker_info.st_size,
        blocker_info.st_mtime_ns,
        True,
    )
    assert "locked/nested" not in snapshot
    assert "locked/nested/payload" not in snapshot


def test_permission_retry_snapshot_still_refuses_unreadable_non_target_directory(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    blocker = checkout / "locked"
    blocker.mkdir(parents=True)
    other = checkout / "other"
    other.mkdir()
    checkpoint = {"checkpoint_time_ns": 2**63 - 1, "ignored_state": []}
    real_scandir = worktree.os.scandir

    def deny_non_target(path):
        if Path(path) == other:
            raise PermissionError(errno.EACCES, "scan denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(worktree.os, "scandir", deny_non_target)
    with pytest.raises(
        worktree.WorktreeError,
        match="could not enumerate leftover checkout directory",
    ):
        worktree._permission_retry_tree_snapshot(
            checkout, checkpoint, [{"path": "locked"}]
        )


def test_permission_retry_snapshot_reports_unreadable_entries_and_directories(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    checkpoint = {"checkpoint_time_ns": 2**63 - 1, "ignored_state": []}
    real_lstat = Path.lstat
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda path, *args, **kwargs: (
            (_ for _ in ()).throw(PermissionError(errno.EACCES, "lstat denied"))
            if Path(path) == checkout else real_lstat(path, *args, **kwargs)
        ),
    )
    with pytest.raises(worktree.WorktreeError, match="could not inspect leftover checkout entry"):
        worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])

    monkeypatch.setattr(Path, "lstat", real_lstat)
    monkeypatch.setattr(
        worktree.os,
        "scandir",
        lambda _path: (_ for _ in ()).throw(PermissionError(errno.EACCES, "scan denied")),
    )
    with pytest.raises(worktree.WorktreeError, match="could not enumerate leftover checkout directory"):
        worktree._permission_retry_tree_snapshot(checkout, checkpoint, [])


@pytest.mark.parametrize(
    ("before", "after", "candidates", "message"),
    [
        ({"lost": (1, 2, 3, 4, 5, 6, False)}, {}, [], "disappeared outside a retry target"),
        ({"same": (1, 2, 3, 4, 5, 6, False)}, {"same": (8, 2, 3, 4, 5, 6, False)}, [], "changed during permission retry"),
        ({"file": (1, 2, 3, 4, 5, 6, False)}, {"file": (1, 2, 3, 4, 7, 8, False)}, [], "file changed during permission retry"),
        ({}, {"new": (1, 2, 3, 4, 5, 6, False)}, [], "new leftover checkout entry appeared"),
    ],
)
def test_permission_retry_snapshot_comparison_refuses_unapproved_changes(
    before, after, candidates, message
):
    with pytest.raises(worktree.WorktreeError, match=message):
        worktree._compare_permission_retry_snapshots(before, after, candidates)


@pytest.mark.parametrize("candidate", [{"path": "../escape"}, {"path": "/absolute"}, {}])
def test_permission_retry_snapshot_comparison_rejects_malformed_targets(candidate):
    with pytest.raises(worktree.WorktreeError, match="malformed retry target"):
        worktree._compare_permission_retry_snapshots({}, {}, [candidate])


@pytest.mark.parametrize(
    ("candidate", "message"),
    [
        (None, "malformed target"),
        ({}, "malformed target"),
        ({"path": ""}, "malformed target"),
        ({"path": "/absolute"}, "unsafe retry target"),
        ({"path": "../escape"}, "unsafe retry target"),
        ({"path": "."}, "unsafe retry target"),
    ],
)
def test_permission_retry_snapshot_rejects_malformed_blocker_candidates(
    tmp_path, candidate, message
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    checkpoint = {"checkpoint_time_ns": 2**63 - 1, "ignored_state": []}

    with pytest.raises(worktree.WorktreeError, match=message):
        worktree._permission_retry_tree_snapshot(checkout, checkpoint, [candidate])


def test_permission_retry_checkpoint_time_refresh_rolls_back_when_recheck_fails(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "existing.txt").write_text("data", encoding="utf-8")
    checkpoint = {"checkpoint_time_ns": 1, "ignored_state": []}
    before = worktree._permission_retry_tree_snapshot(
        checkout, checkpoint, [], enforce_checkpoint_time=False
    )
    monkeypatch.setattr(worktree.time, "time_ns", lambda: 0)

    with pytest.raises(worktree.WorktreeError, match="changed after the permission checkpoint"):
        worktree._refresh_permission_retry_checkpoint_time(
            checkout, checkpoint, before, []
        )

    assert checkpoint["checkpoint_time_ns"] == 1
