"""Behavioral tests for the S16 Git-worktree lifecycle substrate.

These tests use real Git worktrees in a temporary repository while replacing
CIU environment generation and cleanup. They exercise checkout creation,
lookup, clean-before-remove ordering, and exact target-environment handling
without Docker, a network, or the wall clock.
"""
from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import queue
import stat
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402
from ciu.config_constants import GLOBAL_CONFIG_RENDERED  # noqa: E402


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
        monkeypatch.setattr(
            shared,
            "create_workspace",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                shared.WorkspaceError("cannot add", category="git-error")
            ),
        )
        with pytest.raises(worktree.WorktreeError, match="cannot add"):
            worktree.create(tmp_repo, "git-fails")

        monkeypatch.undo()
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
            "schema_version": 1,
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
        assert doc["schema_version"] == 1
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
        assert doc["schema_version"] == 1
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
        assert doc["schema_version"] == 1
        assert doc["operation"] == "remove"
        assert doc["status"] == "removed"
        assert doc["removed_path"] == str(tmp_repo / ".worktrees" / "wt1")
        assert doc["instance"]["logical_name"] == "wt1"
        assert not (tmp_repo / ".worktrees" / "wt1").exists()

    def test_remove_document_unmanaged_omits_instance(self, tmp_repo, monkeypatch):
        target = tmp_repo / "unmanaged"
        assert _git(
            ["worktree", "add", "--no-checkout", "-b", "unmanaged", str(target), "main"],
            tmp_repo,
        ).returncode == 0
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        doc = worktree.remove_document(tmp_repo, "unmanaged", force=True)
        assert doc["operation"] == "remove"
        assert doc["status"] == "removed"
        assert "instance" not in doc
        assert doc["removed_path"] == str(target)

    def test_remove_document_failed_clean_produces_no_success_document(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        create_path(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeError, match="NOT removing"):
            worktree.remove_document(tmp_repo, "wt1")


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
    # `fake_generate_env` stubs the network-existence probe itself; this test
    # is about that probe's real Docker call, so restore it (create is done).
    monkeypatch.undo()
    monkeypatch.setattr(
        worktree.procutil,
        "docker",
        lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("docker denied")),
    )

    with pytest.raises(worktree.RuntimeNetworkProbeError, match="docker denied"):
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
