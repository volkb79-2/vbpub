"""Behavioral tests for the S16 Git-worktree lifecycle substrate.

These tests use real Git worktrees in a temporary repository while replacing
CIU environment generation and cleanup. They exercise checkout creation,
lookup, clean-before-remove ordering, and exact target-environment handling
without Docker, a network, or the wall clock.
"""
from __future__ import annotations

import hashlib
import json
import builtins
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402


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
    (repo / ".gitignore").write_text("ciu.env\n", encoding="utf-8")
    assert _git(["add", "README.md", ".gitignore"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _instance_id_for(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:6]


@pytest.fixture
def fake_generate_env(monkeypatch):
    def fake(path: Path, *, identity_only: bool = False) -> int:
        instance_id = _instance_id_for(path)
        (path / "ciu.env").write_text(
            f'export INSTANCE_ID="{instance_id}"\n'
            f'export DOCKER_NETWORK_INTERNAL="repo-{instance_id}-network"\n',
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(worktree, "_generate_env_in", fake)
    monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
    return fake


class TestAddRemoveList:
    def test_add_creates_worktree_on_new_branch(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "feature-x", base="main")
        assert target == tmp_repo / ".worktrees" / "feature-x"
        assert target.is_dir()
        assert (target / "ciu.env").is_file()

    def test_add_rejects_invalid_names(self, tmp_repo):
        for name in ("a/b", ".hidden", ""):
            with pytest.raises(worktree.WorktreeError):
                worktree.add(tmp_repo, name)

    def test_add_rejects_existing_target(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "dup", base="main")
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            worktree.add(tmp_repo, "dup", base="main")

    def test_add_profile_writes_durable_worktree_override(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "wt", base="main", profile="core,db")
        overlay = (target / "ciu.global.worktree.toml.j2").read_text(encoding="utf-8")
        assert 'service_profiles = ["core", "db"]' in overlay
        assert "CIU_SERVICES_PROFILE" not in (target / "ciu.env").read_text(encoding="utf-8")

    def test_add_generate_env_failure_raises(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(worktree, "_generate_env_in", lambda path, **_kw: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu env generate"):
            worktree.add(tmp_repo, "wt", base="main")

    def test_add_surfaces_git_worktree_creation_failure(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess(
                ["git"], 1, "", "branch exists"
            ),
        )
        with pytest.raises(worktree.WorktreeError, match="branch exists"):
            worktree.add(tmp_repo, "wt", base="main")

    def test_list_worktrees_shows_primary_and_added(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "wt1", base="main")
        infos = worktree.list_worktrees(tmp_repo)
        assert any(info.is_primary for info in infos)
        assert any(not info.is_primary and info.path.name == "wt1" for info in infos)

    def test_list_worktrees_preserves_git_detached_state(self, tmp_path, monkeypatch):
        output = (
            f"worktree {tmp_path / 'primary'}\nHEAD 11111111\nbranch refs/heads/main\n\n"
            f"worktree {tmp_path / 'detached'}\nHEAD 22222222\ndetached\n\n"
        )
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess(["git"], 0, output, ""),
        )
        assert [item.branch for item in worktree.list_worktrees(tmp_path)] == [
            "main",
            "(detached)",
        ]

    def test_list_worktrees_surfaces_git_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            worktree,
            "_git",
            lambda *_args: subprocess.CompletedProcess(
                ["git"], 1, "", "no repository"
            ),
        )
        with pytest.raises(worktree.WorktreeError, match="no repository"):
            worktree.list_worktrees(tmp_path)

    def test_find_worktree_by_name(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "wt1", base="main")
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
        target = worktree.add(tmp_repo, "wt1", base="main")
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
        target = worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu clean.*failed"):
            worktree.remove(tmp_repo, "wt1", force=False)
        assert target.exists()

    def test_remove_failed_clean_force_proceeds_silently(
        self, tmp_repo, fake_generate_env, monkeypatch, capsys
    ):
        worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        worktree.remove(tmp_repo, "wt1", force=True)
        assert capsys.readouterr().out == ""

    def test_remove_surfaces_git_remove_failure_after_clean(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        worktree.add(tmp_repo, "wt1", base="main")
        real_git = worktree._git
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kw: 0)

        def fail_remove(args, cwd):
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(
                    ["git"], 1, "", "checkout locked"
                )
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", fail_remove)
        with pytest.raises(worktree.WorktreeError, match="checkout locked"):
            worktree.remove(tmp_repo, "wt1")


class TestManagedIdentityLifecycle:
    FIXED_NOW = datetime(2026, 8, 17, 12, 34, 56, tzinfo=timezone.utc)

    def test_create_persists_atomic_ready_record(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "logical-one", base="main")
        stored = json.loads(record.record_path.read_text(encoding="utf-8"))
        assert stored["schema_version"] == 1
        assert stored["logical_name"] == "logical-one"
        assert stored["state"] == "ready"
        assert stored["runtime"]["instance_id"] == record.instance_id
        assert "head" not in stored
        assert "password" not in record.record_path.read_text(encoding="utf-8").lower()

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

    def test_ensure_constraint_mismatch_fails_closed(self, tmp_repo, fake_generate_env):
        worktree.create(tmp_repo, "logical-one", display_name="visible-one")
        with pytest.raises(worktree.WorktreeError, match="ensure mismatch"):
            worktree.ensure(tmp_repo, "logical-one", display_name="different")

    def test_env_failure_is_inspectable_and_ensure_resumes(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_generate_env_in", lambda _path, **_kw: 7)
        with pytest.raises(worktree.WorktreeError, match="env generate.*failed"):
            worktree.create(tmp_repo, "logical-one", profile="core,db")
        failed = worktree.find_instance_record(tmp_repo, "logical-one")
        assert failed is not None
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "env-generation-failed"
        assert (failed.ciu_root / "ciu.global.worktree.toml.j2").is_file()

        def recover(path: Path, **_kw) -> int:
            (path / "ciu.env").write_text(
                'export INSTANCE_ID="recover1"\n'
                'export DOCKER_NETWORK_INTERNAL="repo-recover1-network"\n',
                encoding="utf-8",
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", recover)
        ready = worktree.ensure(tmp_repo, "logical-one")
        assert ready.state == "ready"
        assert ready.instance_id == "recover1"

    def test_runtime_collision_never_marks_second_ready(self, tmp_repo, monkeypatch):
        def collide(path: Path, **_kw) -> int:
            (path / "ciu.env").write_text(
                'export INSTANCE_ID="same-id"\n'
                'export DOCKER_NETWORK_INTERNAL="same-network"\n',
                encoding="utf-8",
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", collide)
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        worktree.create(tmp_repo, "logical-one")
        with pytest.raises(worktree.WorktreeError, match="already belongs"):
            worktree.create(tmp_repo, "logical-two")
        failed = worktree.find_instance_record(tmp_repo, "logical-two")
        assert failed is not None
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "runtime-collision"

    def test_network_collision_is_independent_of_instance_id(self, tmp_repo, monkeypatch):
        current = {"id": "one"}

        def collide_network(path: Path, **_kw) -> int:
            (path / "ciu.env").write_text(
                f'export INSTANCE_ID="{current["id"]}"\n'
                'export DOCKER_NETWORK_INTERNAL="same-network"\n', encoding="utf-8"
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

    def test_nested_ciu_root_keeps_exact_offset(self, tmp_path, monkeypatch):
        git_root = tmp_path / "mono"
        ciu_root = git_root / "component"
        ciu_root.mkdir(parents=True)
        assert _git(["init", "-b", "main"], git_root).returncode == 0
        assert _git(["config", "user.email", "t@example.com"], git_root).returncode == 0
        assert _git(["config", "user.name", "Test"], git_root).returncode == 0
        (ciu_root / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
        (git_root / ".gitignore").write_text("ciu.env\nciu.global.worktree.toml.j2\n")
        assert _git(["add", "."], git_root).returncode == 0
        assert _git(["commit", "-m", "init"], git_root).returncode == 0

        def nested_env(path: Path, **_kw) -> int:
            assert path.name == "component"
            (path / "ciu.env").write_text(
                'export INSTANCE_ID="nested1"\n'
                'export DOCKER_NETWORK_INTERNAL="mono-nested1-network"\n',
                encoding="utf-8",
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", nested_env)
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: False)
        record = worktree.create(ciu_root, "nested")
        assert record.git_worktree_path == git_root / ".worktrees" / "nested"
        assert record.ciu_root_offset == Path("component")
        assert record.record_path == record.git_worktree_path / "component" / worktree.WORKTREE_INSTANCE_RECORD

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

    def test_create_rejects_duplicate_logical_identity(self, tmp_repo, fake_generate_env):
        worktree.create(tmp_repo, "logical-one", display_name="first")
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            worktree.create(tmp_repo, "logical-one", display_name="second")

    def test_git_add_and_overlay_failures_remain_attributable(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        real_git = worktree._git

        def fail_add(args, cwd):
            if args[:2] == ["worktree", "add"]:
                return subprocess.CompletedProcess(args, 1, "", "cannot add")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", fail_add)
        with pytest.raises(worktree.WorktreeError, match="cannot add"):
            worktree.create(tmp_repo, "git-fails")

        monkeypatch.setattr(worktree, "_git", real_git)
        monkeypatch.setattr(
            worktree, "_write_worktree_overlay",
            lambda *_a, **_kw: (_ for _ in ()).throw(worktree.WorktreeError("overlay failed")),
        )
        with pytest.raises(worktree.WorktreeError, match="overlay failed"):
            worktree.create(tmp_repo, "overlay-fails")
        record = worktree.find_instance_record(tmp_repo, "overlay-fails")
        assert record is not None and record.recovery_status == "checkout-incomplete"

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
        self, tmp_repo, monkeypatch
    ):
        calls = 0

        def full_failure(path: Path, *, identity_only: bool = False) -> int:
            nonlocal calls
            calls += 1
            (path / "ciu.env").write_text(
                'export INSTANCE_ID="stable1"\n'
                'export DOCKER_NETWORK_INTERNAL="stable-network"\n', encoding="utf-8"
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
        assert _git(["add", "."], other).returncode == 0
        assert _git(["commit", "-m", "init"], other).returncode == 0

        def changes(path: Path, *, identity_only: bool = False) -> int:
            suffix = "one" if identity_only else "two"
            (path / "ciu.env").write_text(
                f'export INSTANCE_ID="{suffix}"\n'
                f'export DOCKER_NETWORK_INTERNAL="network-{suffix}"\n', encoding="utf-8"
            )
            return 0

        monkeypatch.setattr(worktree, "_generate_env_in", changes)
        with pytest.raises(worktree.WorktreeError, match="identity changed"):
            worktree.create(other, "logical-two")
        changed = worktree.find_instance_record(other, "logical-two")
        assert changed is not None and changed.recovery_status == "runtime-collision"

    def test_host_network_collision_refuses_before_full_bootstrap(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_docker_network_exists", lambda _network: True)
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            worktree.create(tmp_repo, "logical-one")
        failed = worktree.find_instance_record(tmp_repo, "logical-one")
        assert failed is not None and failed.recovery_status == "runtime-collision"


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
            (lambda raw: {**raw, "runtime": {"instance_id": 3, "network": "n"}}, "runtime.instance_id"),
            (lambda raw: {**raw, "runtime": {"instance_id": None, "network": "n"}}, "ready record"),
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

    def test_family_scan_rejects_record_git_fact_mismatches(
        self, tmp_repo, fake_generate_env
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
            with pytest.raises(worktree.WorktreeError, match=message):
                worktree.list_instance_records(tmp_repo)
        record.record_path.write_text(json.dumps(original), encoding="utf-8")

    def test_family_scan_rejects_duplicate_logical_identity(
        self, tmp_repo, fake_generate_env
    ):
        first = worktree.create(tmp_repo, "logical-one")
        second = worktree.create(tmp_repo, "logical-two")
        raw = json.loads(second.record_path.read_text(encoding="utf-8"))
        raw["logical_name"] = first.logical_name
        second.record_path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(worktree.WorktreeError, match="duplicate logical"):
            worktree.list_instance_records(tmp_repo)


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
        path = tmp_path / "ciu.global.worktree.toml.j2"
        path.write_text("[ciu.instance]\n", encoding="utf-8")
        with pytest.raises(worktree.WorktreeError, match="refusing to overwrite"):
            worktree._write_worktree_overlay(tmp_path, "core", None)
        path.unlink()
        monkeypatch.setattr(
            worktree.os, "replace",
            lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not write.*denied"):
            worktree._write_worktree_overlay(tmp_path, "core", None)

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

    def test_runtime_identity_reader_rejects_missing_and_malformed_env(self, tmp_path):
        (tmp_path / "ciu.env").write_text("not shell syntax = x y\n", encoding="utf-8")
        with pytest.raises(worktree.WorktreeError, match="could not read generated"):
            worktree._runtime_identity(tmp_path)
        (tmp_path / "ciu.env").write_text('export INSTANCE_ID="only"\n', encoding="utf-8")
        with pytest.raises(worktree.WorktreeError, match="lacks INSTANCE_ID"):
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
        (target / "ciu.global.worktree.toml.j2").write_text(
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
            adopted.ciu_root / "ciu.global.worktree.toml.j2"
        ).read_text(encoding="utf-8")


class TestWorktreeSubprocessEnvironment:
    _IDENTITY_KEYS = (
        "REPO_ROOT",
        "PHYSICAL_REPO_ROOT",
        "DOCKER_NETWORK_INTERNAL",
        "INSTANCE_ID",
        "REPO_NAME",
        "CIU_SERVICES_PROFILE",
    )

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

    def test_clean_uses_target_worktree_env_not_primary(self, tmp_path, monkeypatch):
        (tmp_path / "ciu.env").write_text(
            'export REPO_ROOT="/target"\nexport INSTANCE_ID="target-id"\n',
            encoding="utf-8",
        )
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
        with pytest.raises(worktree.WorktreeError, match="does not exist"):
            worktree._clean_in(tmp_path, yes=False)

    def test_clean_surfaces_unreadable_target_env(self, tmp_path, monkeypatch):
        env_file = tmp_path / "ciu.env"
        env_file.write_text('export REPO_ROOT="/target"\n', encoding="utf-8")
        from ciu import workspace_env

        monkeypatch.setattr(
            workspace_env,
            "parse_workspace_env",
            lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not read.*denied"):
            worktree._clean_in(tmp_path, yes=False)
