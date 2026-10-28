"""Coverage-fill behavior tests (wave C) for the managed-worktree substrate.

Targets in ``ciu.worktree``: the explicit ``ensure --base`` comparison, the
shared-record snapshot/phase helpers, ``_finish_allocation`` refusals, the
runtime-network failure translation in selected-root preparation, and the
``ensure`` / ``adopt`` argument and readiness edges. Real Git worktrees in a
temporary repository; CIU environment generation and Docker probes are stubbed.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _instance_id_for(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:6]


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        "ciu.env\nciu.global.instance.toml.j2\nciu.instance.generated.toml\n",
        encoding="utf-8",
    )
    (repo / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    assert _git(
        ["add", "README.md", ".gitignore", "ciu.global.defaults.toml.j2"], repo
    ).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


@pytest.fixture
def rootless_repo(tmp_path: Path) -> Path:
    """A Git family with no CIU root marker in any committed tree."""
    repo = tmp_path / "rootless"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("generic Git family\n", encoding="utf-8")
    assert _git(["add", "README.md"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


@pytest.fixture
def fake_generate_env(monkeypatch, write_instance_facts):
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
    monkeypatch.setattr(worktree, "_docker_network_details", lambda _network: None)
    return fake


def _head(path: Path) -> str:
    return _git(["rev-parse", "HEAD"], path).stdout.strip()


def _context(repo: Path, record):
    return worktree._shared_record_for_checkout(record.git_worktree_path)


class TestExplicitEnsureBase:
    def test_unresolvable_base_is_refused(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "base-missing")
        with pytest.raises(
            worktree.WorktreeError,
            match="ensure --base 'no-such-ref' does not resolve to a commit",
        ):
            worktree._assert_explicit_ensure_base_matches(
                tmp_repo, record, "no-such-ref"
            )

    def test_full_sha_base_ref_stands_in_for_a_missing_fork_point(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "base-sha")
        sha = _head(tmp_repo)
        legacy = replace(record, fork_point_sha=None, base_ref=sha)

        assert worktree._assert_explicit_ensure_base_matches(
            tmp_repo, legacy, "main"
        ) is None

        (tmp_repo / "later.txt").write_text("later\n", encoding="utf-8")
        assert _git(["add", "later.txt"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "later"], tmp_repo).returncode == 0
        later = _head(tmp_repo)
        with pytest.raises(worktree.WorktreeError, match="ensure base mismatch") as raised:
            worktree._assert_explicit_ensure_base_matches(tmp_repo, legacy, "main")
        assert later in str(raised.value)
        assert sha in str(raised.value)

    def test_shared_workspace_base_is_used_when_head_is_still_at_it(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "base-shared")
        sha = _head(tmp_repo)
        legacy = replace(record, fork_point_sha=None, base_ref="main")
        assert _context(tmp_repo, legacy).base_commit == sha

        assert worktree._assert_explicit_ensure_base_matches(
            tmp_repo, legacy, sha
        ) is None

        (tmp_repo / "later.txt").write_text("later\n", encoding="utf-8")
        assert _git(["add", "later.txt"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "later"], tmp_repo).returncode == 0
        with pytest.raises(worktree.WorktreeError, match="ensure base mismatch") as raised:
            worktree._assert_explicit_ensure_base_matches(
                tmp_repo, legacy, _head(tmp_repo)
            )
        assert f"recorded allocation commit is {sha}" in str(raised.value)

    def test_unprovable_allocation_commit_is_refused_when_head_moved(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "base-moved")
        sha = _head(tmp_repo)
        legacy = replace(record, fork_point_sha=None, base_ref="main")
        wt = record.git_worktree_path
        (wt / "work.txt").write_text("work\n", encoding="utf-8")
        assert _git(["add", "work.txt"], wt).returncode == 0
        assert _git(["commit", "-m", "work"], wt).returncode == 0

        with pytest.raises(
            worktree.WorktreeError,
            match="allocation commit is not recorded or provable",
        ):
            worktree._assert_explicit_ensure_base_matches(tmp_repo, legacy, sha)

    def test_unprovable_allocation_commit_without_shared_record_is_refused(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        record = worktree.create(tmp_repo, "base-unowned")
        sha = _head(tmp_repo)
        legacy = replace(record, fork_point_sha=None, base_ref="main")
        monkeypatch.setattr(worktree, "_shared_record_for_checkout", lambda _p: None)

        with pytest.raises(
            worktree.WorktreeError, match="not recorded or provable"
        ):
            worktree._assert_explicit_ensure_base_matches(tmp_repo, legacy, sha)


class TestSharedRecordHelpers:
    def test_snapshot_requires_a_shared_workspace(self, tmp_repo, fake_generate_env):
        record = worktree.create(tmp_repo, "snap-none")
        with pytest.raises(
            worktree.WorktreeError, match="without a shared workspace"
        ):
            worktree._write_shared_ciu_record_snapshot(None, record)

    def test_snapshot_rejects_an_unknown_reset_phase_and_writes_nothing(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "snap-phase")
        context = _context(tmp_repo, record)
        before = worktree._shared_checkout_reset_phase(context)

        with pytest.raises(
            worktree.WorktreeError,
            match="invalid durable checkout-reset phase 'bogus'",
        ):
            worktree._write_shared_ciu_record_snapshot(
                context, record, checkout_reset_phase="bogus"
            )
        assert worktree._shared_checkout_reset_phase(context) == before

        worktree._write_shared_ciu_record_snapshot(
            context, record, checkout_reset_phase="started"
        )
        assert worktree._shared_checkout_reset_phase(context) == "started"

    def test_reset_phase_read_requires_a_shared_workspace(self):
        with pytest.raises(
            worktree.WorktreeError, match="without its shared workspace record"
        ):
            worktree._shared_checkout_reset_phase(None)

    def test_reset_phase_read_rejects_a_corrupt_shared_phase(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "phase-corrupt")
        context = _context(tmp_repo, record)
        shared = worktree._shared_worktree()
        generic = shared.read_record(context.record_path)
        shared.write_record(
            replace(
                generic,
                metadata={
                    **dict(generic.metadata),
                    worktree._CIU_CHECKOUT_RESET_PHASE_METADATA: "exploded",
                },
            )
        )
        with pytest.raises(
            worktree.WorktreeError,
            match="invalid checkout-reset phase 'exploded'",
        ):
            worktree._shared_checkout_reset_phase(context)


class TestFinishAllocationRefusals:
    def test_missing_durable_reset_marker_refuses_the_reset(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "finish-marker")
        context = _context(tmp_repo, record)
        unmarked = replace(record, checkout_reset_pending=False)

        with pytest.raises(
            worktree.WorktreeError, match="its durable reset phase is missing"
        ):
            worktree._finish_allocation(
                tmp_repo, unmarked, checkout_required=True,
                workspace_context=context,
            )

    def test_shared_phase_other_than_not_started_refuses_the_reset(
        self, tmp_repo, fake_generate_env
    ):
        record = worktree.create(tmp_repo, "finish-phase")
        context = _context(tmp_repo, record)
        pending = replace(record, checkout_reset_pending=True)
        # create() leaves the family phase at "complete"; the reset must not
        # be repeated, and no Git reset may run.
        assert worktree._shared_checkout_reset_phase(context) != "not-started"

        with pytest.raises(
            worktree.WorktreeError,
            match="its shared phase is 'complete', not definitely `not-started`",
        ):
            worktree._finish_allocation(
                tmp_repo, pending, checkout_required=True,
                workspace_context=context,
            )


class _NetworkScript:
    """Docker network details that change once full env generation runs."""

    def __init__(self, before, after):
        self.before = before
        self.after = after
        self.full_generation_done = False

    def details(self, _network):
        return self.after if self.full_generation_done else self.before


class TestSelectedRootNetworkFailures:
    def test_preflight_ownership_conflict_is_a_runtime_collision(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        # The pre-bootstrap collision scan passes; the ownership proof that
        # follows it finds a foreign owner label on the same-name network.
        monkeypatch.setattr(worktree, "_check_runtime_collision", lambda *_a: None)
        monkeypatch.setattr(
            worktree, "_docker_network_details",
            lambda _network: {
                "id": "a" * 64,
                "labels": {worktree._NETWORK_OWNER_LABEL: "foreign-instance"},
            },
        )
        with pytest.raises(
            worktree.RuntimeNetworkOwnershipError, match="carries owner instance"
        ):
            worktree.create(tmp_repo, "pre-collision")

        record = worktree.find_instance_record(tmp_repo, "pre-collision")
        assert record is not None
        assert record.state == "recovery-required"
        assert record.recovery_status == "runtime-collision"

    def test_preflight_unprovable_network_is_a_verification_failure(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        monkeypatch.setattr(worktree, "_check_runtime_collision", lambda *_a: None)
        monkeypatch.setattr(
            worktree, "_docker_network_details",
            lambda _network: {"id": "b" * 64, "labels": {}},
        )
        with pytest.raises(worktree.RuntimeNetworkVerificationError):
            worktree.create(tmp_repo, "pre-unproven")

        record = worktree.find_instance_record(tmp_repo, "pre-unproven")
        assert record is not None
        assert record.recovery_status == "runtime-verification-failed"

    @pytest.mark.parametrize(
        ("late_details", "error", "status"),
        [
            (
                {"id": "c" * 64,
                 "labels": {worktree._NETWORK_OWNER_LABEL: "foreign-instance"}},
                worktree.RuntimeNetworkOwnershipError,
                "runtime-collision",
            ),
            (
                {"id": "d" * 64, "labels": {}},
                worktree.RuntimeNetworkVerificationError,
                "runtime-verification-failed",
            ),
        ],
    )
    def test_network_appearing_during_full_generation_is_recorded(
        self, tmp_repo, fake_generate_env, monkeypatch,
        late_details, error, status,
    ):
        script = _NetworkScript(before=None, after=late_details)

        def generate(root, *, identity_only=False, allocation_lock_held=False):
            rc = fake_generate_env(root, identity_only=identity_only)
            if not identity_only:
                script.full_generation_done = True
            return rc

        monkeypatch.setattr(worktree, "_generate_env_in", generate)
        monkeypatch.setattr(worktree, "_docker_network_details", script.details)

        with pytest.raises(error):
            worktree.create(tmp_repo, "late-network")

        record = worktree.find_instance_record(tmp_repo, "late-network")
        assert record is not None
        assert record.state == "recovery-required"
        assert record.recovery_status == status


class TestEnsureEdges:
    def test_non_boolean_adopt_existing_network_is_refused(
        self, tmp_repo, fake_generate_env
    ):
        with pytest.raises(
            worktree.WorktreeError, match="adopt_existing_network must be boolean"
        ):
            worktree.ensure(tmp_repo, "anything", adopt_existing_network="yes")
        assert worktree.find_instance_record(tmp_repo, "anything") is None

    def test_matching_worktree_dir_resumes_the_ready_record(
        self, tmp_repo, fake_generate_env
    ):
        created = worktree.create(tmp_repo, "dir-match", worktree_dir=".worktrees")
        ensured = worktree.ensure(
            tmp_repo, "dir-match", worktree_dir=".worktrees"
        )
        assert ensured.state == "ready"
        assert ensured.git_worktree_path == created.git_worktree_path

    def test_adopt_network_without_a_live_network_is_refused(
        self, tmp_repo, fake_generate_env
    ):
        worktree.create(tmp_repo, "adopt-no-net")
        with pytest.raises(
            worktree.RuntimeNetworkVerificationError,
            match="no existing runtime network .* to adopt",
        ):
            worktree.ensure(tmp_repo, "adopt-no-net", adopt_existing_network=True)

    def test_adopting_an_unlabeled_network_persists_its_object_id(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        created = worktree.create(tmp_repo, "adopt-net")
        assert created.network_id is None
        monkeypatch.setattr(
            worktree, "_docker_network_details",
            lambda _network: {"id": "e" * 64, "labels": {}},
        )

        ensured = worktree.ensure(
            tmp_repo, "adopt-net", adopt_existing_network=True
        )

        assert ensured.network_id == "e" * 64
        stored = worktree.read_instance_record(created.record_path)
        assert stored.network_id == "e" * 64
        snapshot = worktree._shared_record_for_checkout(
            created.git_worktree_path
        )
        generic = worktree._shared_worktree().read_record(snapshot.record_path)
        shared_copy = generic.metadata[worktree._CIU_WORKTREE_RECORD_METADATA]
        assert shared_copy["network_id"] == "e" * 64

    def test_ready_record_without_runtime_identity_cannot_adopt_a_network(
        self, rootless_repo
    ):
        created = worktree.create(rootless_repo, "no-identity")
        assert created.instance_id is None and created.network is None

        with pytest.raises(
            worktree.WorktreeError,
            match="record without runtime identity",
        ):
            worktree.ensure(rootless_repo, "no-identity", adopt_existing_network=True)

    def test_ready_record_without_runtime_identity_is_returned_unchanged(
        self, rootless_repo
    ):
        created = worktree.create(rootless_repo, "no-identity-plain")
        assert created.instance_id is None and created.network is None

        ensured = worktree.ensure(rootless_repo, "no-identity-plain")

        assert ensured == created
        assert worktree.read_instance_record(created.record_path) == created

    def test_adopt_network_for_an_absent_worktree_is_refused(
        self, tmp_repo, fake_generate_env
    ):
        with pytest.raises(
            worktree.WorktreeError,
            match="cannot adopt a runtime network for absent worktree 'ghost'",
        ):
            worktree.ensure(tmp_repo, "ghost", adopt_existing_network=True)
        assert worktree.find_instance_record(tmp_repo, "ghost") is None

    def test_absent_ensure_forwards_an_explicit_base_to_create(
        self, tmp_repo, fake_generate_env
    ):
        first = _head(tmp_repo)
        (tmp_repo / "second.txt").write_text("second\n", encoding="utf-8")
        assert _git(["add", "second.txt"], tmp_repo).returncode == 0
        assert _git(["commit", "-m", "second"], tmp_repo).returncode == 0

        record = worktree.ensure(tmp_repo, "absent-based", base=first)

        assert record.state == "ready"
        assert record.fork_point_sha == first
        assert _head(record.git_worktree_path) == first


class TestAdoptEdges:
    def test_non_boolean_adopt_existing_network_is_refused(
        self, tmp_repo, fake_generate_env
    ):
        with pytest.raises(
            worktree.WorktreeError, match="adopt_existing_network must be boolean"
        ):
            worktree.adopt(
                tmp_repo, "adopt-bool", str(tmp_repo.parent / "nowhere"),
                adopt_existing_network="yes",
            )

    def test_unverifiable_head_refuses_the_requested_initial_overlay(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        wt_path = tmp_repo.parent / "adopt-short-head"
        assert _git(
            ["worktree", "add", "-b", "adopt-short-head", str(wt_path), "main"],
            tmp_repo,
        ).returncode == 0
        real_git = worktree._git

        def short_head(args, cwd):
            if list(args) == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, "abc123\n", "")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", short_head)
        with pytest.raises(
            worktree.WorktreeError,
            match="cannot verify the adopted checkout's exact HEAD",
        ):
            worktree.adopt(
                tmp_repo, "adopt-short-head", str(wt_path), profile="core"
            )
        assert worktree.find_instance_record(tmp_repo, "adopt-short-head") is None
