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
    # Every per-checkout CIU artifact is gitignored (S3.1b): the generated
    # facts file is the one CIU reads (CIU-75), `ciu.env` its legacy export,
    # and the instance overlay the operator's own sparse override.
    (repo / ".gitignore").write_text(
        "ciu.env\nciu.global.instance.toml.j2\nciu.instance.generated.toml\n",
        encoding="utf-8",
    )
    assert _git(["add", "README.md", ".gitignore"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _instance_id_for(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:6]


@pytest.fixture
def fake_generate_env(monkeypatch, write_instance_facts):
    """Stand in for `ciu env generate` — writing BOTH of its outputs.

    CIU-75: the real verb still writes `ciu.env` (a legacy write-only export)
    AND writes `[ciu.instance.generated]` — into its own
    `ciu.instance.generated.toml` since ciu-P47 — and only the SECOND is what
    CIU reads back. A fake that wrote only the first would let every test here
    pass against a product that no longer works.
    """
    def fake(path: Path, *, identity_only: bool = False) -> int:
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

    def test_add_profile_writes_durable_instance_override(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "wt", base="main", profile="core,db")
        overlay = (target / "ciu.global.instance.toml.j2").read_text(encoding="utf-8")
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
        with pytest.raises(worktree.WorktreeError, match="already belongs"):
            worktree.create(tmp_repo, "logical-two")
        failed = worktree.find_instance_record(tmp_repo, "logical-two")
        assert failed is not None
        assert failed.state == "recovery-required"
        assert failed.recovery_status == "runtime-collision"

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
        self, tmp_repo, monkeypatch, write_instance_facts
    ):
        calls = 0

        def full_failure(path: Path, *, identity_only: bool = False) -> int:
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
        assert _git(["add", "."], other).returncode == 0
        assert _git(["commit", "-m", "init"], other).returncode == 0

        def changes(path: Path, *, identity_only: bool = False) -> int:
            suffix = "one" if identity_only else "two"
            write_instance_facts(
                path, instance_id=suffix, network=f"network-{suffix}"
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
        path = tmp_path / "ciu.global.instance.toml.j2"
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
        self._replace_fails(monkeypatch)
        self._unlink_fails(monkeypatch)
        with pytest.raises(worktree.WorktreeError, match="could not write"):
            worktree._write_worktree_overlay(tmp_path, "core", None)

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
            worktree, "list_instance_records", lambda repo_root: [rec, rec]
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
        worktree.add(tmp_repo, "wt1", base="main")
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
        worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeError, match="NOT removing"):
            worktree.remove_document(tmp_repo, "wt1")


class TestExactWorktreeControl:
    """S16.6 — `worktree up` and `worktree exec` with an exact sanitized
    target environment, exact argv, and exact child exit-code propagation."""

    @pytest.fixture
    def ready(self, tmp_repo, fake_generate_env, write_instance_facts):
        """A ready managed instance whose generated overlay table carries the
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

    def test_up_propagates_exact_child_exit_code(self, ready, monkeypatch, capsys):
        repo_root, record = ready
        monkeypatch.setattr(
            worktree, "_run_child",
            lambda argv, cwd, env: subprocess.CompletedProcess(argv, 17, "out", ""),
        )
        assert worktree.up_instance(repo_root, "ctrl") == 17
        capsys.readouterr()  # output captured; the exit code is still 17

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
        with pytest.raises(worktree.WorktreeError, match="could not run `ciu up`"):
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
