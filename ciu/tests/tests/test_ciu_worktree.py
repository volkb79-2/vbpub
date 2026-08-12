"""Tests for src/ciu/worktree.py — S16 `ciu worktree add|rm|list` and S16.2's
namespaced data isolation (CIU-23, O4).

This is the FIRST test module for worktree.py (it shipped with zero coverage,
per the handoff). Two concerns are covered:

- The pre-existing S16 add/remove/list machinery, exercised against REAL git
  worktrees in a throwaway `tmp_path` repo (deterministic — plain local git
  operations, no network/clock/docker) with `_generate_env_in`/`_clean_in`
  monkeypatched so tests don't need a full CIU environment or docker.
- S16.2's data-isolation seam: naming (INSTANCE_ID-based, not name-based),
  ordering (drop BEFORE `ciu clean`), force semantics (drop failures do NOT
  silently succeed — unlike `_clean_in`'s own force path), and the
  idempotent-retry contract. `DataIsolationProvisioner` IS the test seam
  (named in the LOG): every behavioural test below injects a FAKE
  implementation, since `tester-unified:local` has no live Postgres server
  (CIU-26 tracks the deferred real-server proof).
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, check=False)


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throwaway git repo on branch 'main' with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    # ciu.env is gitignored in every real CIU repo (S1.8-adjacent convention,
    # this repo's own .gitignore: `**/ciu.env`) — without it, `git worktree
    # remove` refuses on the untracked file the fake generator writes.
    (repo / ".gitignore").write_text("ciu.env\n", encoding="utf-8")
    assert _git(["add", "README.md", ".gitignore"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _instance_id_for(path: Path) -> str:
    """The REAL formula (workspace_env._compute_network_name) — kept in sync
    here deliberately, so the collision test below is faithful to production
    naming without paying for a real `ciu env generate` subprocess."""
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:6]


@pytest.fixture
def fake_generate_env(monkeypatch):
    """Replace the real (subprocess) env generation with one that writes a
    synthetic ciu.env carrying the REAL INSTANCE_ID formula for the target's
    own physical path — deterministic, no docker/subprocess dependency."""
    def fake(path: Path) -> int:
        instance_id = _instance_id_for(path)
        (path / "ciu.env").write_text(
            f'export INSTANCE_ID="{instance_id}"\n', encoding="utf-8"
        )
        return 0
    monkeypatch.setattr(worktree, "_generate_env_in", fake)
    return fake


class FakeProvisioner:
    """The O4 test seam: an in-memory DataIsolationProvisioner."""

    def __init__(self, *, fail_provision_for=(), fail_drop_for=()):
        self.entities: dict[str, bool] = {}
        self.calls: list[tuple] = []
        self.fail_provision_for = set(fail_provision_for)
        self.fail_drop_for = set(fail_drop_for)

    def provision(self, entity: str, profile: str) -> str:
        self.calls.append(("provision", entity, profile))
        if entity in self.fail_provision_for:
            raise worktree.DataIsolationError(f"simulated provision failure for {entity}")
        self.entities[entity] = True
        return f"fake-dsn://{profile}/{entity}"

    def drop(self, entity: str, profile: str) -> None:
        self.calls.append(("drop", entity, profile))
        if entity in self.entities and entity in self.fail_drop_for:
            raise worktree.DataIsolationError(f"simulated drop failure for {entity}")
        # idempotent: dropping an absent entity is a no-op success
        self.entities.pop(entity, None)


# ---------------------------------------------------------------------------
# Pre-existing S16 machinery (first-ever coverage for this module)
# ---------------------------------------------------------------------------


class TestAddRemoveList:
    def test_add_creates_worktree_on_new_branch(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "feature-x", base="main")
        assert target == tmp_repo / ".worktrees" / "feature-x"
        assert target.is_dir()
        assert (target / "ciu.env").is_file()

    def test_add_rejects_invalid_names(self, tmp_repo):
        with pytest.raises(worktree.WorktreeError):
            worktree.add(tmp_repo, "a/b")
        with pytest.raises(worktree.WorktreeError):
            worktree.add(tmp_repo, ".hidden")
        with pytest.raises(worktree.WorktreeError):
            worktree.add(tmp_repo, "")

    def test_add_rejects_existing_target(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "dup", base="main")
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            worktree.add(tmp_repo, "dup", base="main")

    def test_add_profile_writes_services_profile(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "wt", base="main", profile="core,db")
        env_text = (target / "ciu.env").read_text(encoding="utf-8")
        assert 'CIU_SERVICES_PROFILE="core,db"' in env_text

    def test_add_generate_env_failure_raises(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(worktree, "_generate_env_in", lambda path: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu env generate"):
            worktree.add(tmp_repo, "wt", base="main")

    def test_add_surfaces_git_worktree_creation_failure(self, tmp_repo, monkeypatch):
        monkeypatch.setattr(
            worktree, "_git",
            lambda *_args: subprocess.CompletedProcess(["git"], 1, "", "branch exists"),
        )
        with pytest.raises(worktree.WorktreeError, match="branch exists"):
            worktree.add(tmp_repo, "wt", base="main")

    def test_list_worktrees_shows_primary_and_added(self, tmp_repo, fake_generate_env):
        worktree.add(tmp_repo, "wt1", base="main")
        infos = worktree.list_worktrees(tmp_repo)
        assert any(i.is_primary for i in infos)
        assert any((not i.is_primary) and i.path.name == "wt1" for i in infos)

    def test_list_worktrees_preserves_git_detached_state(self, tmp_path, monkeypatch):
        output = (
            f"worktree {tmp_path / 'primary'}\nHEAD 11111111\nbranch refs/heads/main\n\n"
            f"worktree {tmp_path / 'detached'}\nHEAD 22222222\ndetached\n\n"
        )
        monkeypatch.setattr(
            worktree, "_git",
            lambda *_args: subprocess.CompletedProcess(["git"], 0, output, ""),
        )

        assert [info.branch for info in worktree.list_worktrees(tmp_path)] == ["main", "(detached)"]

    def test_list_worktrees_surfaces_git_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            worktree, "_git",
            lambda *_args: subprocess.CompletedProcess(["git"], 1, "", "no repository"),
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

    def test_remove_cleans_before_git_remove(self, tmp_repo, fake_generate_env, monkeypatch):
        target = worktree.add(tmp_repo, "wt1", base="main")
        calls: list[str] = []
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: calls.append("clean") or 0)
        removed = worktree.remove(tmp_repo, "wt1", force=True)
        assert removed == target
        assert calls == ["clean"]
        assert not target.exists()

    def test_remove_failed_clean_aborts_without_force(self, tmp_repo, fake_generate_env, monkeypatch):
        target = worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        with pytest.raises(worktree.WorktreeError, match="ciu clean.*failed"):
            worktree.remove(tmp_repo, "wt1", force=False)
        assert target.exists()  # NOT removed

    def test_remove_failed_clean_force_proceeds_silently(
        self, tmp_repo, fake_generate_env, monkeypatch, capsys
    ):
        """Pre-existing S16 behaviour (not O4's subject): `_clean_in`'s own
        force path is silent — no [WARN]. Contrast with the data-isolation
        force path below, which is NOT silent."""
        worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)
        worktree.remove(tmp_repo, "wt1", force=True)
        assert capsys.readouterr().out == ""

    def test_remove_surfaces_git_remove_failure_after_clean(self, tmp_repo, fake_generate_env, monkeypatch):
        worktree.add(tmp_repo, "wt1", base="main")
        real_git = worktree._git
        monkeypatch.setattr(worktree, "_clean_in", lambda *_args, **_kw: 0)

        def fail_remove(args, cwd):
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(["git"], 1, "", "checkout locked")
            return real_git(args, cwd)

        monkeypatch.setattr(worktree, "_git", fail_remove)
        with pytest.raises(worktree.WorktreeError, match="checkout locked"):
            worktree.remove(tmp_repo, "wt1")


class TestWorktreeSubprocessEnvironment:
    def test_generate_env_strips_primary_instance_identity(self, tmp_path, monkeypatch):
        for key in (
            "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
            "INSTANCE_ID", "REPO_NAME", "CIU_SERVICES_PROFILE",
        ):
            monkeypatch.setenv(key, "primary-value")
        seen: dict[str, object] = {}

        def fake_run(argv, **kwargs):
            seen.update(argv=argv, **kwargs)
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(worktree.subprocess, "run", fake_run)

        assert worktree._generate_env_in(tmp_path) == 0
        env = seen["env"]
        assert isinstance(env, dict)
        for key in (
            "REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
            "INSTANCE_ID", "REPO_NAME", "CIU_SERVICES_PROFILE",
        ):
            assert key not in env
        assert seen["cwd"] == str(tmp_path)
        assert seen["check"] is False

    def test_clean_uses_the_target_worktree_env_not_the_primary(self, tmp_path, monkeypatch):
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

    def test_clean_surfaces_an_unreadable_target_env(self, tmp_path, monkeypatch):
        env_file = tmp_path / "ciu.env"
        env_file.write_text('export REPO_ROOT="/target"\n', encoding="utf-8")
        from ciu import workspace_env

        monkeypatch.setattr(
            workspace_env, "parse_workspace_env",
            lambda _path: (_ for _ in ()).throw(PermissionError("denied")),
        )
        with pytest.raises(worktree.WorktreeError, match="could not read.*denied"):
            worktree._clean_in(tmp_path, yes=False)


# ---------------------------------------------------------------------------
# S16.2 / CIU-23 — namespaced data isolation
# ---------------------------------------------------------------------------


class TestDataIsolationEntityNaming:
    def test_pure_function_of_instance_id(self):
        assert worktree._data_isolation_entity("abc123") == worktree._data_isolation_entity("abc123")

    def test_different_instance_ids_never_collide(self):
        assert worktree._data_isolation_entity("aaa") != worktree._data_isolation_entity("bbb")

    def test_two_different_physical_clones_same_worktree_name_no_collision(
        self, tmp_path, fake_generate_env
    ):
        """REQUIRED negative-clause case (O4): a same-repo collision test is
        untestable (`add` already refuses a duplicate name in ONE repo). The
        real attack is two CLONES choosing the SAME worktree name — different
        physical paths -> different INSTANCE_ID -> a correct implementation
        has no collision, a name-keyed one would."""
        repo_a = tmp_path / "clone-a"
        repo_b = tmp_path / "clone-b"
        for repo in (repo_a, repo_b):
            repo.mkdir()
            _git(["init", "-b", "main"], repo)
            _git(["config", "user.email", "t@example.com"], repo)
            _git(["config", "user.name", "Test"], repo)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            _git(["add", "README.md"], repo)
            _git(["commit", "-m", "init"], repo)

        fake = FakeProvisioner()
        worktree.add(repo_a, "shared-name", base="main", data_isolation="postgres", provisioner=fake)
        worktree.add(repo_b, "shared-name", base="main", data_isolation="postgres", provisioner=fake)

        entities = [call[1] for call in fake.calls if call[0] == "provision"]
        assert len(entities) == 2
        assert entities[0] != entities[1], "two different clones collided on one entity name"


class TestDataIsolationAdd:
    def test_without_data_isolation_writes_no_entity_lines(self, tmp_repo, fake_generate_env):
        target = worktree.add(tmp_repo, "wt1", base="main")
        env_text = (target / "ciu.env").read_text(encoding="utf-8")
        assert "CIU_DATA_ISOLATION_ENTITY" not in env_text

    def test_with_data_isolation_provisions_and_writes_ciu_env(self, tmp_repo, fake_generate_env):
        fake = FakeProvisioner()
        target = worktree.add(
            tmp_repo, "wt1", base="main", data_isolation="postgres", provisioner=fake
        )
        expected_entity = worktree._data_isolation_entity(_instance_id_for(target))
        assert fake.calls == [("provision", expected_entity, "postgres")]

        env_text = (target / "ciu.env").read_text(encoding="utf-8")
        assert f'CIU_DATA_ISOLATION_ENTITY="{expected_entity}"' in env_text
        assert 'CIU_DATA_ISOLATION_PROFILE="postgres"' in env_text
        assert f"CIU_DATA_ISOLATION_DSN=\"fake-dsn://postgres/{expected_entity}\"" in env_text

    def test_credential_bearing_warning_is_present_in_ciu_env(self, tmp_repo, fake_generate_env):
        """docs/SPEC.md and the LOG both state this value MAY be
        credential-bearing and must never be an env_passthrough candidate;
        the same warning must travel with the value itself."""
        fake = FakeProvisioner()
        target = worktree.add(
            tmp_repo, "wt1", base="main", data_isolation="postgres", provisioner=fake
        )
        env_text = (target / "ciu.env").read_text(encoding="utf-8")
        assert "credential-bearing" in env_text
        assert "env_passthrough" in env_text

    def test_missing_instance_id_in_ciu_env_raises(self, tmp_repo, monkeypatch):
        """_read_instance_id's own guard: ciu.env generation always writes
        INSTANCE_ID (S2.7) -- a generator that doesn't is a defect this must
        catch rather than silently deriving a bogus entity name."""
        def fake_generate_no_instance_id(path):
            (path / "ciu.env").write_text("export REPO_ROOT=\"/x\"\n", encoding="utf-8")
            return 0
        monkeypatch.setattr(worktree, "_generate_env_in", fake_generate_no_instance_id)

        with pytest.raises(worktree.WorktreeError, match="no INSTANCE_ID"):
            worktree.add(tmp_repo, "wt1", base="main", data_isolation="postgres")

    def test_provision_failure_raises_and_names_the_checkout(self, tmp_repo, fake_generate_env):
        fake = FakeProvisioner()
        # force a failure regardless of the (unknown ahead of time) entity name
        real_provision = fake.provision
        def always_fail(entity, profile):
            fake.calls.append(("provision", entity, profile))
            raise worktree.DataIsolationError("boom")
        fake.provision = always_fail

        with pytest.raises(worktree.WorktreeError, match="provisioning the isolated database failed"):
            worktree.add(tmp_repo, "wt1", base="main", data_isolation="postgres", provisioner=fake)
        # the checkout itself still exists (created before provisioning ran)
        assert (tmp_repo / ".worktrees" / "wt1").is_dir()


class TestDataIsolationRemove:
    def test_remove_without_data_isolation_never_calls_drop(self, tmp_repo, fake_generate_env, monkeypatch):
        worktree.add(tmp_repo, "wt1", base="main")
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        fake = FakeProvisioner()
        worktree.remove(tmp_repo, "wt1", provisioner=fake)
        assert fake.calls == []

    def test_remove_drops_before_clean(self, tmp_repo, fake_generate_env, monkeypatch):
        fake = FakeProvisioner()
        worktree.add(tmp_repo, "wt1", base="main", data_isolation="postgres", provisioner=fake)
        fake.calls.clear()

        order: list[str] = []
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: order.append("clean") or 0)
        real_drop = fake.drop
        def tracking_drop(entity, profile):
            order.append("drop")
            return real_drop(entity, profile)
        fake.drop = tracking_drop

        worktree.remove(tmp_repo, "wt1", provisioner=fake)
        assert order == ["drop", "clean"]

    def test_drop_success_then_clean_fail_force_false_aborts_and_retry_is_idempotent(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        """REQUIRED case (O4): drop SUCCEEDS x _clean_in FAILS x force=False.
        Pins the terminal-state/retry contract: the entity is gone, ciu.env
        (and its now-dead DSN) remain, and a RETRIED `worktree rm` must
        succeed — the drop re-runs as a no-op, then `_clean_in` is retried."""
        fake = FakeProvisioner()
        worktree.add(tmp_repo, "wt1", base="main", data_isolation="postgres", provisioner=fake)
        entity = next(c[1] for c in fake.calls if c[0] == "provision")
        assert entity in fake.entities

        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 1)  # clean FAILS
        fake.calls.clear()
        with pytest.raises(worktree.WorktreeError, match="ciu clean.*failed"):
            worktree.remove(tmp_repo, "wt1", force=False, provisioner=fake)

        # drop SUCCEEDED (entity now absent) even though removal aborted
        assert entity not in fake.entities
        assert fake.calls == [("drop", entity, "postgres")]
        # the checkout and its ciu.env (with the now-dead DSN) remain
        target = tmp_repo / ".worktrees" / "wt1"
        assert target.is_dir()
        assert f'CIU_DATA_ISOLATION_ENTITY="{entity}"' in (target / "ciu.env").read_text()

        # RETRY: clean now succeeds; the drop must be a no-op (idempotent)
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        fake.calls.clear()
        removed = worktree.remove(tmp_repo, "wt1", force=False, provisioner=fake)
        assert removed == target
        assert fake.calls == [("drop", entity, "postgres")]  # re-ran, no-op
        assert not target.exists()

    def test_force_masks_failed_drop_with_a_warning_naming_the_entity(
        self, tmp_repo, fake_generate_env, monkeypatch, capsys
    ):
        """ROUND-3 correction: unlike `_clean_in`'s own SILENT force path,
        O4 IMPROVES on it — a force-masked drop failure warns, naming the
        entity that was not dropped, and states it is now the operator's
        problem."""
        fake = FakeProvisioner()
        worktree.add(tmp_repo, "wt1", base="main", data_isolation="postgres", provisioner=fake)
        entity = next(c[1] for c in fake.calls if c[0] == "provision")
        fake.fail_drop_for.add(entity)

        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        worktree.remove(tmp_repo, "wt1", force=True, provisioner=fake)

        out = capsys.readouterr().out
        assert "[WARN]" in out
        assert entity in out
        assert "operator's problem" in out
        # the entity is still "present" in the fake since the drop failed
        assert entity in fake.entities

    def test_no_ciu_env_at_all_is_a_no_op(self, tmp_repo, monkeypatch):
        """A worktree with no ciu.env at all (e.g. `ciu env generate` never
        ran) -- _drop_data_isolation_in must no-op, not crash; _clean_in's own
        (separate) ciu.env check surfaces the real problem."""
        # add() WITHOUT the fake_generate_env fixture would try a real
        # subprocess; instead exercise remove() directly against a worktree
        # dir that has no ciu.env, via a real (uncleaned) git worktree.
        res = subprocess.run(
            ["git", "worktree", "add", "-b", "wt1", str(tmp_repo / ".worktrees" / "wt1"), "main"],
            cwd=str(tmp_repo), capture_output=True, text=True,
        )
        assert res.returncode == 0
        fake = FakeProvisioner()
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        worktree.remove(tmp_repo, "wt1", provisioner=fake)
        assert fake.calls == []

    def test_unreadable_ciu_env_is_a_no_op_not_a_crash(self, tmp_repo, fake_generate_env, monkeypatch):
        """ciu.env exists (passes the is_file() check) but reading it raises
        OSError (permission denied) -- must no-op, leaving _clean_in's own
        error to surface the real problem, rather than crashing removal."""
        target = worktree.add(tmp_repo, "wt1", base="main")
        env_file = target / "ciu.env"
        env_file.chmod(0o000)

        fake = FakeProvisioner()
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: 0)
        # force=True: `git worktree remove` deletes the whole directory
        # (including the now-permission-000 file) regardless of its own mode.
        worktree.remove(tmp_repo, "wt1", provisioner=fake, force=True)
        assert fake.calls == []

    def test_failed_drop_without_force_aborts_before_clean_runs(
        self, tmp_repo, fake_generate_env, monkeypatch
    ):
        fake = FakeProvisioner()
        worktree.add(tmp_repo, "wt1", base="main", data_isolation="postgres", provisioner=fake)
        entity = next(c[1] for c in fake.calls if c[0] == "provision")
        fake.fail_drop_for.add(entity)

        clean_calls = []
        monkeypatch.setattr(worktree, "_clean_in", lambda wt, *, yes: clean_calls.append(1) or 0)

        with pytest.raises(worktree.WorktreeError, match="could not drop data-isolation entity"):
            worktree.remove(tmp_repo, "wt1", force=False, provisioner=fake)
        assert clean_calls == [], "_clean_in must not run when the drop failed and force=False"


# ---------------------------------------------------------------------------
# PostgresProvisioner — the shipped real default (thin; not live-tested here,
# CIU-26 tracks the deferred real-server proof). These confirm it is actually
# WIRED as the default and its mechanics are structurally sound.
# ---------------------------------------------------------------------------


class TestPostgresProvisionerShippedDefault:
    def test_default_provisioner_is_the_real_postgres_class(self):
        assert isinstance(worktree._default_provisioner(), worktree.PostgresProvisioner)

    def test_provision_uses_docker_exec_psql_against_the_named_profile(self, monkeypatch):
        """`-c` cannot mix a SQL statement with a `\\gexec` meta-command in one
        argument -- psql rejects that with a syntax error unconditionally, so
        the create-if-not-exists SQL MUST travel on stdin (`-f -`), not as a
        `-c` argv token. Asserting only argv[:3] (as an earlier version of
        this test did) is exactly how a syntax-broken implementation passed:
        it never looked at the SQL itself or how it was delivered."""
        calls = []

        def fake_docker(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        import ciu.procutil as procutil
        monkeypatch.setattr(procutil, "docker", fake_docker)

        prov = worktree.PostgresProvisioner()
        dsn = prov.provision("ciu_abc123", "postgres")

        cmd, kw = calls[0]
        # docker exec -i <container> ... -- -i is REQUIRED or stdin is closed
        # and `-f -` reads EOF immediately (a silent no-op, never creates
        # anything).
        assert cmd[:4] == ["exec", "-i", "postgres", "psql"]
        # The SQL is NOT embedded in argv anywhere (no `-c`) -- it travels via
        # stdin, read from a script (`-f -`).
        assert "-c" not in cmd
        assert cmd[-2:] == ["-f", "-"]
        assert not any("gexec" in arg for arg in cmd)
        # The actual SQL content, delivered as `input=`, carries the real
        # create-if-not-exists pattern -- entity-specific, idempotent shape.
        sql = kw.get("input") or ""
        assert "ciu_abc123" in sql
        assert "CREATE DATABASE" in sql
        assert "WHERE NOT EXISTS" in sql
        assert "\\gexec" in sql
        assert "ciu_abc123" in dsn

    def test_drop_uses_if_exists_for_idempotency(self, monkeypatch):
        calls = []

        def fake_docker(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        import ciu.procutil as procutil
        monkeypatch.setattr(procutil, "docker", fake_docker)

        prov = worktree.PostgresProvisioner()
        prov.drop("ciu_abc123", "postgres")
        joined = " ".join(calls[0])
        assert "DROP DATABASE" in joined and "IF EXISTS" in joined

    def test_drop_failure_raises_data_isolation_error(self, monkeypatch):
        def fake_docker(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "permission denied")

        import ciu.procutil as procutil
        monkeypatch.setattr(procutil, "docker", fake_docker)

        prov = worktree.PostgresProvisioner()
        with pytest.raises(worktree.DataIsolationError, match="permission denied"):
            prov.drop("ciu_abc123", "postgres")

    def test_provision_failure_raises_data_isolation_error(self, monkeypatch):
        def fake_docker(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "connection refused")

        import ciu.procutil as procutil
        monkeypatch.setattr(procutil, "docker", fake_docker)

        prov = worktree.PostgresProvisioner()
        with pytest.raises(worktree.DataIsolationError, match="connection refused"):
            prov.provision("ciu_abc123", "postgres")

    def test_empty_profile_falls_back_to_postgres_container(self, monkeypatch):
        calls = []

        def fake_docker(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        import ciu.procutil as procutil
        monkeypatch.setattr(procutil, "docker", fake_docker)

        prov = worktree.PostgresProvisioner()
        prov.drop("ciu_abc123", "")
        assert calls[0][1] == "postgres"


class TestDataIsolationProvisionerDefaultStubs:
    """The Protocol's own method bodies refuse loudly (NotImplementedError)
    rather than silently no-op'ing when a concrete subclass forgets to
    override one — exercised directly via a trivial subclass, since a
    Protocol is a normal instantiable class once explicitly subclassed."""

    def test_provision_stub_refuses_when_not_overridden(self):
        class Incomplete(worktree.DataIsolationProvisioner):
            pass

        with pytest.raises(NotImplementedError, match="provision"):
            Incomplete().provision("ciu_abc123", "postgres")

    def test_drop_stub_refuses_when_not_overridden(self):
        class Incomplete(worktree.DataIsolationProvisioner):
            pass

        with pytest.raises(NotImplementedError, match="drop"):
            Incomplete().drop("ciu_abc123", "postgres")
