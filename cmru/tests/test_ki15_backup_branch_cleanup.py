"""KI-15: cleanup must not print ``error:``/``failed to push`` on a
SUCCESSFUL run.

Every observed run -- including a successful dry run that exited 0 -- ended
with:

    error: unable to delete 'cmru/release/<id>': remote ref does not exist
    error: failed to push some refs to '...'

because ``remove_backup_branch`` attempted an unconditional best-effort
delete on paths where ``push_backup_branch`` was never called: a dry run, a
"nothing to release" run, and (once KI-12 landed) a refused release plan
(S12.2a/S12.2b) all exit 0/1 without ever pushing a backup.

**The fix is transaction STATE, not a per-caller special case**
(:func:`transaction.mark_backup_pushed` / :func:`transaction.backup_was_pushed`,
written and read exactly where the existing ``mark_plan_refused`` /
``plan_was_refused`` pair already lives). The paired risk explicitly called
out for this fix: getting the "did we push it?" state wrong in the OTHER
direction is worse than the original bug -- a real backup branch left on
origin forever, silently, is far more expensive to notice than a stray
`error:` line. This module tests BOTH directions against a real ``origin``
remote, not mocks -- "is the branch actually still on origin afterward" is
exactly the distinction under test.
"""
from __future__ import annotations

import subprocess
import tempfile
import shutil
from pathlib import Path

from cmru import transaction


# ---------------------------------------------------------------------------
# Real origin + real worktree harness -- push_backup_branch/remove_backup_branch
# both shell out to real git against a real remote; create_workspace makes a
# real `git worktree`, exactly like production.
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(f"git {args} failed:\n{result.stderr}")
    return result.stdout.strip()


class _OriginAndRepo:
    def __enter__(self) -> "_OriginAndRepo":
        self.tmp = Path(tempfile.mkdtemp(prefix="cmru_ki15_test_"))
        seed = self.tmp / "seed"
        seed.mkdir()
        _git("init", "-q", "-b", "main", cwd=seed)
        _git("config", "user.email", "test@example.invalid", cwd=seed)
        _git("config", "user.name", "test", cwd=seed)
        (seed / "README.md").write_text("init\n")
        _git("add", ".", cwd=seed)
        _git("commit", "-q", "-m", "chore: initial", cwd=seed)

        self.origin = self.tmp / "origin.git"
        _git("clone", "-q", "--bare", str(seed), str(self.origin), cwd=self.tmp)

        self.repo_root = self.tmp / "repo"
        _git("clone", "-q", str(self.origin), str(self.repo_root), cwd=self.tmp)
        _git("config", "user.email", "test@example.invalid", cwd=self.repo_root)
        _git("config", "user.name", "test", cwd=self.repo_root)
        return self

    def branch_exists_on_origin(self, branch: str) -> bool:
        out = _git("ls-remote", "--heads", "origin", branch, cwd=self.repo_root)
        return bool(out.strip())

    def __exit__(self, *_exc) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Direction 1: this transaction DID push a backup -> cleanup must actually
# delete it. Getting this direction wrong is the worse failure mode (an
# orphaned branch, silently left on origin forever).
# ---------------------------------------------------------------------------

def test_a_pushed_backup_is_actually_deleted_from_origin():
    with _OriginAndRepo() as h:
        ws = transaction.create_workspace(h.repo_root, purpose="release")

        transaction.push_backup_branch(ws)
        # Must-succeed control: confirm it is REALLY there before testing removal --
        # otherwise "gone afterward" would be trivially true for the wrong reason.
        assert h.branch_exists_on_origin(ws.branch) is True
        assert transaction.backup_was_pushed(h.repo_root, ws) is True

        transaction.remove_backup_branch(ws)

        assert h.branch_exists_on_origin(ws.branch) is False


# ---------------------------------------------------------------------------
# Direction 2: this transaction NEVER pushed a backup -> cleanup must be a
# true no-op: no delete attempt, no output, nothing on origin either way.
# ---------------------------------------------------------------------------

def test_a_never_pushed_backup_is_never_attempted_and_prints_nothing(capfd):
    with _OriginAndRepo() as h:
        ws = transaction.create_workspace(h.repo_root, purpose="release")
        assert transaction.backup_was_pushed(h.repo_root, ws) is False

        capfd.readouterr()  # drop create_workspace's own (silent, but be safe) output
        transaction.remove_backup_branch(ws)  # must not raise

        assert h.branch_exists_on_origin(ws.branch) is False  # never was, still isn't
        captured = capfd.readouterr()
        assert captured.out == ""
        assert captured.err == ""  # THE KI-15 regression: no `error: ...` leak


# ---------------------------------------------------------------------------
# Even a genuine best-effort failure (state says "pushed", but the ref is
# gone from origin some other way) must stay silent -- capture_output, not a
# printed `error:`/`failed to push` leak (KI-15's literal, reported symptom).
# ---------------------------------------------------------------------------

def test_a_best_effort_delete_that_still_fails_prints_nothing(capfd):
    with _OriginAndRepo() as h:
        ws = transaction.create_workspace(h.repo_root, purpose="release")
        # Simulate the transaction's own state disagreeing with reality (the
        # exact defect class KI-15 warns is worse than the original bug) --
        # marked pushed, but nothing was actually ever pushed under this name.
        transaction.mark_backup_pushed(h.repo_root, ws)
        assert h.branch_exists_on_origin(ws.branch) is False

        capfd.readouterr()
        transaction.remove_backup_branch(ws)  # git's real delete genuinely fails here

        captured = capfd.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert "error" not in (captured.out + captured.err).lower()


# ---------------------------------------------------------------------------
# The exact scenario named in the KI-15 write-up: a refused release plan
# (S12.2a/S12.2b) never calls push_backup_branch at all -- cleanup on that
# workspace must be silent too, not just "the parent happens not to call it".
# ---------------------------------------------------------------------------

def test_a_workspace_whose_plan_was_refused_never_pushed_a_backup_either(capfd):
    with _OriginAndRepo() as h:
        ws = transaction.create_workspace(h.repo_root, purpose="release")
        transaction.mark_plan_refused(h.repo_root, ws)  # exactly what the child does on refusal
        assert transaction.plan_was_refused(h.repo_root, ws) is True
        assert transaction.backup_was_pushed(h.repo_root, ws) is False  # never reached push_backup_branch

        capfd.readouterr()
        transaction.remove_backup_branch(ws)

        captured = capfd.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert h.branch_exists_on_origin(ws.branch) is False


# ---------------------------------------------------------------------------
# forget_release_scope must clean up the new marker too, like it already
# does for plan-refused/progress/results -- otherwise it accumulates forever
# in the shared git-common-dir scope directory.
# ---------------------------------------------------------------------------

def test_forget_release_scope_clears_the_backup_pushed_marker_too():
    with _OriginAndRepo() as h:
        ws = transaction.create_workspace(h.repo_root, purpose="release")
        transaction.push_backup_branch(ws)
        assert transaction.backup_was_pushed(h.repo_root, ws) is True

        transaction.forget_release_scope(h.repo_root, ws)

        assert transaction.backup_was_pushed(h.repo_root, ws) is False
