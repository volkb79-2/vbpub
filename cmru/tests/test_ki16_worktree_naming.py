"""KI-16: chronologically-sortable, 1:1-derivable release/build transaction naming.

    branch:     cmru/<purpose>/<YYYYMMDD-HHMMSS>-<scope>-<uuid8>
    directory:  .worktrees/cmru-<purpose>-<YYYYMMDD-HHMMSS>-<scope>-<uuid8>

(SPEC S-CLI.5b). The directory name is the branch name with every ``/``
replaced by ``-`` — derivable in both directions — and the trailing uuid8
(not the old scheme's 12-hex token) is what keeps two transactions from ever
colliding, which is what lets cleanup assume it exclusively owns whatever it
created. Retained worktrees from the OLD ``cmru/<purpose>/<12-hex>`` naming
must keep working unchanged: nothing in discovery/cleanup parses a directory
name, only the branch prefix and whatever ``git worktree list --porcelain``
itself reports.
"""
from __future__ import annotations

import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cmru import transaction


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    """A real git repo with a real ``origin`` remote (a bare clone) — several
    functions under test (``resume_workspace``) fetch ``origin/main``, so a
    remote-less repo would fail for an unrelated reason."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "README.md").write_text("init\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "chore: initial")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(root), str(origin))
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "fetch", "-q", "origin")
    return root


_BRANCH_RE = re.compile(
    r"^cmru/(release|build)/(\d{8})-(\d{6})-([a-z0-9-]+)-([0-9a-f]{8})$"
)


def _dirname_to_branch(dirname: str) -> str:
    """The documented reverse derivation (SPEC S-CLI.5b): only the first
    ``cmru-<purpose>-`` needs its dashes turned back into slashes."""
    return re.sub(r"^cmru-(release|build)-", r"cmru/\1/", dirname, count=1)


# ---------------------------------------------------------------------------
# _sanitize_scope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "all"),
        ("", "all"),
        ("   ", "all"),
        ("---", "all"),
        ("assay", "assay"),
        ("Assay", "assay"),
        ("My Project!!", "my-project"),
        (" -Foo- ", "foo"),
        ("ex@mple_name!!", "ex-mple-name"),
        ("already-fine123", "already-fine123"),
    ],
)
def test_sanitize_scope_normalises_or_falls_back_to_all(raw, expected):
    assert transaction._sanitize_scope(raw) == expected


# ---------------------------------------------------------------------------
# _new_transaction_branch / _worktree_dirname — pure, no git required
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("purpose", ["release", "build"])
def test_new_transaction_branch_matches_the_documented_shape(purpose):
    branch = transaction._new_transaction_branch(purpose, "assay")
    match = _BRANCH_RE.match(branch)
    assert match, branch
    assert match.group(1) == purpose
    assert match.group(4) == "assay"


def test_new_transaction_branch_defaults_unscoped_run_to_all():
    branch = transaction._new_transaction_branch("release", None)
    match = _BRANCH_RE.match(branch)
    assert match.group(4) == "all"


def test_new_transaction_branch_uuid8_is_random_never_deterministic():
    first = transaction._new_transaction_branch("release", "assay")
    second = transaction._new_transaction_branch("release", "assay")
    assert first != second
    first_uuid = _BRANCH_RE.match(first).group(5)
    second_uuid = _BRANCH_RE.match(second).group(5)
    assert first_uuid != second_uuid
    assert len(first_uuid) == 8


def test_new_transaction_branch_timestamp_is_utc_in_yyyymmdd_hhmmss_order():
    """Mutation guard: nothing in the regex-shape tests above pins the
    timestamp to UTC specifically, or to YYYYMMDD-then-HHMMSS ordering (any
    other zero-padded 8+6 digit split with a dash would still match). Freeze
    "now" to a known instant and demand the EXACT string: a mutant that swaps
    `datetime.now(timezone.utc)` for local time, or reorders/relabels the
    strftime fields, changes this literal value and gets caught."""
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is timezone.utc, "the branch timestamp must request UTC explicitly"
            # Deliberately distinguishing field values: hour=23 cannot be
            # mistaken for a month/day, so a field-order mutation is caught
            # too, not just a timezone one.
            return cls(2026, 8, 18, 23, 5, 7, tzinfo=timezone.utc)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(transaction, "datetime", _FixedDatetime)
        branch = transaction._new_transaction_branch("release", "demo")

    assert branch == "cmru/release/20260818-230507-demo-" + branch.rsplit("-", 1)[-1]
    match = _BRANCH_RE.match(branch)
    assert match.group(2) == "20260818"
    assert match.group(3) == "230507"


def test_worktree_dirname_is_the_branch_with_slashes_as_dashes():
    branch = "cmru/release/20260818-195012-assay-a3ae580d"
    dirname = transaction._worktree_dirname(branch)
    assert dirname == "cmru-release-20260818-195012-assay-a3ae580d"
    assert dirname == branch.replace("/", "-")
    assert _dirname_to_branch(dirname) == branch


def test_worktree_dirname_for_build_purpose_round_trips_too():
    branch = "cmru/build/20260818-195012-all-7f2c1b04"
    dirname = transaction._worktree_dirname(branch)
    assert dirname == "cmru-build-20260818-195012-all-7f2c1b04"
    assert _dirname_to_branch(dirname) == branch


# ---------------------------------------------------------------------------
# create_workspace: real git — 1:1 derivability, prefix compatibility, and
# a must-succeed control for every probe below.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("purpose", ["release", "build"])
@pytest.mark.parametrize("scope", [None, "Assay", "weird name!!"])
def test_create_workspace_branch_and_directory_are_1to1_derivable(tmp_path, purpose, scope):
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    workspace = transaction.create_workspace(root, base=base, purpose=purpose, scope=scope)
    try:
        assert _BRANCH_RE.match(workspace.branch), workspace.branch
        assert workspace.path.name == workspace.branch.replace("/", "-")
        assert _dirname_to_branch(workspace.path.name) == workspace.branch
        # Existing code/refspecs glob on this prefix (transaction.py ~149/~713/~729) —
        # the new scheme must keep satisfying it.
        assert workspace.branch.startswith(f"cmru/{purpose}/")
    finally:
        transaction.remove_workspace(workspace)


def test_create_workspace_fails_closed_when_target_path_already_exists_nonempty(tmp_path, monkeypatch):
    """Probe: a (contrived) name collision on a NON-EMPTY directory must
    abort loudly, never silently reuse or overwrite — proving
    `create_workspace` no longer pre-creates the directory itself
    (mkdtemp+rmdir is gone) and explicitly refuses before ever calling git."""
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    branch = transaction._new_transaction_branch("release", "demo")
    monkeypatch.setattr(transaction, "_new_transaction_branch", lambda purpose, scope: branch)
    collision = root / ".worktrees" / transaction._worktree_dirname(branch)
    collision.mkdir(parents=True)
    (collision / "occupied.txt").write_text("already here\n")

    with pytest.raises(RuntimeError, match="worktree path already exists"):
        transaction.create_workspace(root, base=base, purpose="release", scope="demo")


def test_create_workspace_fails_closed_when_target_path_already_exists_empty(tmp_path, monkeypatch):
    """The gap `git worktree add` itself does NOT close: git silently
    ADOPTS a pre-existing EMPTY directory instead of refusing it (a NON-empty
    one is the only case git itself fails on), which would violate "this ONE
    transaction exclusively owns the name it created" if a uuid8 collision or
    a stale leftover ever left an empty directory in the way. `create_workspace`
    must refuse this itself, not rely on git's inconsistent behaviour."""
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    branch = transaction._new_transaction_branch("release", "demo")
    monkeypatch.setattr(transaction, "_new_transaction_branch", lambda purpose, scope: branch)
    collision = root / ".worktrees" / transaction._worktree_dirname(branch)
    collision.mkdir(parents=True)  # empty -- git itself would silently adopt this

    with pytest.raises(RuntimeError, match="worktree path already exists"):
        transaction.create_workspace(root, base=base, purpose="release", scope="demo")


def test_create_workspace_succeeds_normally_without_a_collision(tmp_path):
    """Must-succeed control for the probe above: same repo, no pre-existing
    directory in the way — create_workspace works exactly as before."""
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")
    workspace = transaction.create_workspace(root, base=base, purpose="release", scope="demo")
    try:
        assert workspace.path.is_dir()
        assert workspace.branch.startswith("cmru/release/")
    finally:
        transaction.remove_workspace(workspace)


# ---------------------------------------------------------------------------
# Old-style (<12-hex>) worktrees must remain fully supported: discoverable,
# resumable, and removable, coexisting alongside new-style ones.
# ---------------------------------------------------------------------------

def test_old_style_worktrees_remain_discoverable_resumable_and_removable(tmp_path):
    root = _repo(tmp_path)
    base = _git(root, "rev-parse", "HEAD")

    # Recreate the OLD naming scheme by hand (transaction.py's predecessor):
    # cmru/<purpose>/<12-hex>, directory cmru-<purpose>-<12-hex>-<suffix>.
    # ~10 of these are retained in the real estate right now and must not be
    # stranded by this change.
    old_release_token = uuid.uuid4().hex[:12]
    old_release_branch = f"cmru/release/{old_release_token}"
    old_release_path = root / ".worktrees" / f"cmru-release-{old_release_token}-legacy"
    _git(root, "worktree", "add", "-q", "-b", old_release_branch, str(old_release_path), base)

    old_build_token = uuid.uuid4().hex[:12]
    old_build_branch = f"cmru/build/{old_build_token}"
    old_build_path = root / ".worktrees" / f"cmru-build-{old_build_token}-legacy"
    _git(root, "worktree", "add", "-q", "-b", old_build_branch, str(old_build_path), base)

    # Control: a brand-new-style workspace coexists in the very same listing.
    new_workspace = transaction.create_workspace(root, base=base, purpose="release", scope="demo")

    by_branch = {w.branch: w for w in transaction.list_cmru_workspaces(root)}
    assert old_release_branch in by_branch
    assert old_build_branch in by_branch
    assert new_workspace.branch in by_branch

    retained_branches = {w.branch for w in transaction.list_retained_workspaces(root)}
    assert old_release_branch in retained_branches
    assert new_workspace.branch in retained_branches
    assert old_build_branch not in retained_branches  # build worktrees aren't "retained release"

    resumed = transaction.resume_workspace(root, old_release_path)
    assert resumed.branch == old_release_branch

    dry = transaction.discard_build_workspace(root, old_build_path, dry_run=True)
    assert dry.path == old_build_path
    assert old_build_path.is_dir()  # dry run: untouched

    transaction.remove_workspace(resumed)
    assert not old_release_path.exists()

    transaction.discard_build_workspace(root, old_build_path, dry_run=False)
    assert not old_build_path.exists()

    transaction.remove_workspace(new_workspace)
    assert not new_workspace.path.exists()
