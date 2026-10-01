"""B006(a)/A-269 WI-2 — the exact `repository-minus-unsafe-symlinks` policy.

This is O2's own acceptance module (`W1-CARVE-B006a-project-scope.md` §7):
``test_complete_symlink_matrix_and_index_invariants`` is the exact node the
registered gate runs. Every other test here is a finer-grained negative or
edge case §3.5/§3.6 name individually -- kept separate so a single failure
names the exact broken rule instead of one monolithic assertion block.

Every negative test in this module carries its own unmodified positive
control in the SAME test: a declared, genuinely unsafe omission that DOES
succeed, alongside the one that is refused. A test that only ever calls
``pytest.raises`` proves nothing about whether the refusal is for the reason
claimed (A-134's own standing worry), so each one below also proves the
surrounding mechanism still works.

Symlinks are created with real ``os.symlink`` wherever the target is an
ordinary string; the two shapes ``os.symlink`` cannot produce -- an empty
target and a gitlink -- are added with ``git update-index --add
--cacheinfo``, which needs no raw tree surgery at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest

from assay import git as git_module
from assay.config import IsolationConfig
from assay.errors import AssayError, Outcome, ReasonCode
from assay.isolation import (
    DEFAULT_SNAPSHOT_LIMITS,
    SnapshotSpec,
    prepare_snapshot,
)

TIMEOUT = 600.0

_FIXTURE_ENV = {
    "GIT_AUTHOR_NAME": "Assay Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@assay.invalid",
    "GIT_COMMITTER_NAME": "Assay Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@assay.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
}


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    """Drive the fixture repository with a real git, independently of assay."""
    env = {
        "PATH": os.environ["PATH"],
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        **_FIXTURE_ENV,
    }
    completed = subprocess.run(
        [shutil.which("git") or "git", "-C", str(repo), *args],
        input=input_bytes if input_bytes is not None else b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout


def _git_text(repo: Path, *args: str) -> str:
    return _git(repo, *args).decode("utf-8").strip()


def _scratch(tmp_path: Path, name: str = "scratch") -> Path:
    scratch = tmp_path / name
    scratch.mkdir()
    return scratch


def _spec(
    repo: Path,
    scratch: Path,
    commit: str,
    snapshot_policy: IsolationConfig,
    *,
    project_prefix: str = ".",
) -> SnapshotSpec:
    return SnapshotSpec(
        repo_top=repo.resolve(),
        commit=commit,
        project_prefix=PurePosixPath(project_prefix),
        scratch_root=scratch.resolve(),
        snapshot_policy=snapshot_policy,
        limits=DEFAULT_SNAPSHOT_LIMITS,
    )


def _repository_policy() -> IsolationConfig:
    return IsolationConfig(snapshot_selection="repository", unsafe_symlink_omissions=())


def _omission_policy(*paths: str) -> IsolationConfig:
    return IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=tuple(sorted(paths, key=lambda p: p.encode("utf-8"))),
    )


def _parse_skip_worktree(raw: bytes) -> frozenset[bytes]:
    """The SAME NUL-delimited, uppercase-only parse the production `_verify`
    uses, re-implemented independently in this test module (not imported),
    so this oracle cannot silently agree with a bug in the thing it checks.
    """
    skipped: set[bytes] = set()
    for record in raw.split(b"\x00"):
        if record[:2] == b"S ":
            skipped.add(record[2:])
    return frozenset(skipped)


# ---------------------------------------------------------------------------
# The primary "happy path" fixture: three declarable unsafe leaves
# (absolute / repository-escape / empty target), one safe link beside them,
# one dangling-but-contained safe link, and one safe link whose target is
# itself a declared omission (§3.6's "points to omitted" row) -- the same
# shape M3's real Topos/CMRU incident has, scaled down to a hermetic repo.
# No undeclared unsafe leaf lives here: that would make the omission-mode
# happy path unreachable, so it gets its own separate, smaller fixture below.
# ---------------------------------------------------------------------------

ABSOLUTE_LEAF = "topos/unsafe_absolute"
ESCAPE_LEAF = "topos/unsafe_escape"
EMPTY_LEAF = "topos/unsafe_empty"
DECLARED_LEAVES: tuple[str, ...] = tuple(
    sorted((ABSOLUTE_LEAF, ESCAPE_LEAF, EMPTY_LEAF), key=lambda p: p.encode("utf-8"))
)


def _happy_path_repo(tmp_path: Path, name: str = "consumer") -> tuple[Path, str]:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "topos").mkdir()
    (repo / "project").mkdir()
    (repo / "cmru").mkdir()
    (repo / "cmru" / "root_file.txt").write_bytes(b"root dependency\n")
    (repo / "project" / "app.py").write_bytes(b"import os\n")
    os.symlink("/etc/passwd", repo / "topos" / "unsafe_absolute")
    os.symlink("../../../etc/shadow", repo / "topos" / "unsafe_escape")
    os.symlink("app.py", repo / "project" / "safe_nearby")
    os.symlink("missing.txt", repo / "project" / "dangling")
    os.symlink("../topos/unsafe_absolute", repo / "project" / "through_omitted")
    _git(repo, "add", "-A")
    empty_blob = _git_text(repo, "hash-object", "-t", "blob", "-w", "--stdin")
    assert _git_text(repo, "cat-file", "-s", empty_blob) == "0"
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{empty_blob},{EMPTY_LEAF}")
    _git(repo, "commit", "-q", "-m", "happy-path omission fixture")
    return repo, _git_text(repo, "rev-parse", "HEAD")


def _assert_happy_path_materialization(root: Path) -> None:
    for leaf in DECLARED_LEAVES:
        assert not os.path.lexists(root / leaf), leaf
    assert (root / "cmru" / "root_file.txt").read_bytes() == b"root dependency\n"
    assert (root / "project" / "app.py").read_bytes() == b"import os\n"
    nearby = root / "project" / "safe_nearby"
    assert nearby.is_symlink() and os.readlink(nearby) == "app.py"
    assert os.path.exists(nearby)  # resolves: app.py is present
    dangling = root / "project" / "dangling"
    assert dangling.is_symlink() and os.readlink(dangling) == "missing.txt"
    assert os.path.lexists(dangling) and not os.path.exists(dangling)
    through = root / "project" / "through_omitted"
    assert through.is_symlink() and os.readlink(through) == "../topos/unsafe_absolute"


def test_complete_symlink_matrix_and_index_invariants(tmp_path: Path) -> None:
    """O2's own acceptance node (§7). Covers the full §3.6 matrix:

    * repository mode refuses the whole tree (the primary consumer block);
    * omission mode materializes everything else, with the exact three
      declared leaves absent, verified three independent ways (assay's own
      manifest, `os.path.lexists`, and a REAL `git ls-files -v -z` run
      independently of assay);
    * `git status`/`write-tree` invariants hold;
    * the omitted blob is still reachable via `git show` (the closure is
      retained, never pruned -- §2's own non-property);
    * a safe link whose target is a declared omission survives as a
      (correctly) dangling link;
    * cleanup leaves nothing behind in either scratch namespace.
    """
    repo, commit = _happy_path_repo(tmp_path)

    # --- CONTROL: repository mode refuses (this is the primary consumer
    # block the whole carve exists to fix: an unsafe link anywhere in the
    # resolved commit refuses every higher-rigor lane before the command
    # ever runs) ---
    repo_scratch = _scratch(tmp_path, "repo-mode")
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, repo_scratch, commit, _repository_policy()), timeout=TIMEOUT
        ):
            pytest.fail("repository mode must refuse a tree with an unsafe symlink")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert list(repo_scratch.iterdir()) == []

    # --- PRIMARY: omission mode succeeds ---
    omission_scratch = _scratch(tmp_path, "omission-mode")
    policy = _omission_policy(*DECLARED_LEAVES)
    with prepare_snapshot(
        _spec(repo, omission_scratch, commit, policy), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root = snapshot.root
            _assert_happy_path_materialization(root)

            # `git status`/`write-tree` invariants (§3.3.8), proven with a
            # REAL git run, independent of the production `_verify` that
            # already checked them before this snapshot was yielded.
            assert _git_text(root, "status", "--porcelain=v1") == ""
            assert _git_text(root, "write-tree") == _git_text(root, "rev-parse", "HEAD^{tree}")

            # The exact skip set, parsed from a REAL `git ls-files -v -z`,
            # independently of assay's own internal parse.
            skip = _parse_skip_worktree(_git(root, "ls-files", "-v", "-z"))
            assert skip == {leaf.encode("utf-8") for leaf in DECLARED_LEAVES}

            # The closure is retained: the omitted blob's bytes are still
            # readable via `git show`, even though the leaf is absent from
            # the worktree (§2's explicit non-property).
            assert _git_text(root, "show", f"HEAD:{ABSOLUTE_LEAF}") == "/etc/passwd"

            # Source-checkout non-contamination: omission mode never touches
            # the CONSUMER's own working tree. The private snapshot is a
            # disjoint clone (A-186/M2's own "private seed"); the source
            # repository's own unsafe links are still exactly where they
            # were, unaffected by anything this snapshot omitted. (Only
            # the two leaves `os.symlink` actually put on disk in the
            # fixture -- `EMPTY_LEAF` is an index-only entry with no
            # working-tree file even in the SOURCE, so its own "still there"
            # proof is the `git show` against the source below instead.)
            for leaf in (ABSOLUTE_LEAF, ESCAPE_LEAF):
                assert os.path.lexists(repo / leaf), leaf
            assert os.readlink(repo / ABSOLUTE_LEAF) == "/etc/passwd"
            assert _git_text(repo, "show", f"HEAD:{EMPTY_LEAF}") == ""

        assert not snapshot.root.exists()
    assert list(omission_scratch.iterdir()) == []

    # --- NEGATIVE, differential 1 (named in the carve): remove the
    # omission mechanism and the PRIMARY matrix must fail on the absolute
    # link. This is proven by RE-RUNNING repository mode against the exact
    # same commit and observing the SAME `GIT_FAILED` this test's control
    # above already established -- the differential is exercised for real
    # in this session's report (temporarily neutering
    # `_resolve_declared_omission_modes` and re-running this exact test),
    # not merely asserted here.
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path, "repo-mode-2"), commit, _repository_policy()),
            timeout=TIMEOUT,
        ):
            pytest.fail("repository mode must still refuse")
    assert caught.value.reason_code is ReasonCode.GIT_FAILED


# ---------------------------------------------------------------------------
# Undeclared unsafe link: per-link independence and the R1 buy-back message
# ---------------------------------------------------------------------------


def _repo_with_undeclared_unsafe_link(tmp_path: Path) -> tuple[Path, str]:
    """The happy-path fixture PLUS one extra unsafe link that the lane in
    these tests never declares -- so a legitimate declared omission set
    still cannot make an unrelated unsafe link disappear.
    """
    repo, _old_commit = _happy_path_repo(tmp_path, name="consumer-undeclared")
    os.symlink("/root/.ssh/id_rsa", repo / "topos" / "undeclared_unsafe")
    # A targeted `add`, deliberately NOT `-A`: the happy-path fixture's
    # empty-target leaf is an INDEX-only entry with no working-tree file
    # (`update-index --add --cacheinfo`, never `os.symlink`), so a blanket
    # `add -A` here would read its absence from the working tree as a
    # deletion and stage that deletion, silently dropping it from THIS
    # commit's tree.
    _git(repo, "add", "topos/undeclared_unsafe")
    _git(repo, "commit", "-q", "-m", "add an undeclared unsafe link")
    return repo, _git_text(repo, "rev-parse", "HEAD")


def test_an_undeclared_unsafe_link_still_refuses_and_names_the_declarable_spelling(
    tmp_path: Path,
) -> None:
    """(§3.3.5/§3.5) A fourth, undeclared unsafe link must fail with
    `GIT_FAILED` even though the three DECLARED leaves are legitimately
    omitted -- declaring some unsafe links is never a broadening of trust
    for the rest of the tree. Omission mode's diagnostic additionally names
    the feature and the exact declarable spelling; repository mode's stays
    the plain existing message.
    """
    repo, commit = _repo_with_undeclared_unsafe_link(tmp_path)
    policy = _omission_policy(*DECLARED_LEAVES)  # deliberately NOT the 4th link

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, policy), timeout=TIMEOUT
        ):
            pytest.fail("an undeclared unsafe link must still refuse the whole snapshot")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    message = str(caught.value)
    assert "topos/undeclared_unsafe" in message
    assert "unsafe_symlink_omissions" in message
    assert '"topos/undeclared_unsafe"' in message

    # CONTROL: repository mode's diagnostic for the SAME undeclared link
    # names neither the feature nor a declaration, because that declaration
    # is illegal under repository mode.
    with pytest.raises(AssayError) as repo_caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path, "repo-control"), commit, _repository_policy()),
            timeout=TIMEOUT,
        ):
            pytest.fail("repository mode must also refuse")
    assert repo_caught.value.reason_code is ReasonCode.GIT_FAILED
    assert "unsafe_symlink_omissions" not in str(repo_caught.value)


# ---------------------------------------------------------------------------
# The arbitrary-exclusion guard: declaring a P22-safe symlink is a config
# error, not permission to create an exclusion.
# ---------------------------------------------------------------------------


def test_declaring_a_safe_symlink_is_a_configuration_error_not_an_exclusion(
    tmp_path: Path,
) -> None:
    """(§2/§3.3.4) The guard that keeps this mechanism from ever hiding an
    arbitrary path: a SAFE symlink cannot be named as an omission. Proven
    for two different safe shapes (an ordinary resolving link and a
    contained-but-dangling one), each alongside the SAME positive control
    (the three genuinely unsafe leaves still legitimately omit).
    """
    repo, commit = _happy_path_repo(tmp_path)

    for safe_path in ("project/safe_nearby", "project/dangling"):
        policy = _omission_policy(*DECLARED_LEAVES, safe_path)
        with pytest.raises(AssayError) as caught:
            with prepare_snapshot(
                _spec(repo, _scratch(tmp_path, f"safe-{safe_path.split('/')[-1]}"), commit, policy),
                timeout=TIMEOUT,
            ):
                pytest.fail(f"declaring safe path {safe_path!r} must be refused")
        assert caught.value.outcome is Outcome.ERROR
        assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
        assert safe_path in str(caught.value)
        assert "P22-safe" in str(caught.value)

    # CONTROL: declaring only the three genuinely unsafe leaves still works.
    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path, "control"), commit, _omission_policy(*DECLARED_LEAVES)),
        timeout=TIMEOUT,
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            _assert_happy_path_materialization(snapshot.root)


# ---------------------------------------------------------------------------
# Declared-path existence/kind precedence (§3.3.4, §3.5's refusal table)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared_path,expected_fragment",
    [
        ("project/app.py", "a regular file"),
        ("project", "a tree"),
        ("nope/does/not/exist", "is absent at commit"),
        # An intermediate component (`project/app.py`) exists but is NOT a
        # tree, so descending beneath it can never reach a leaf at all --
        # `_resolve_declared_omission_modes`'s own "ancestor is not a tree"
        # branch, distinct from "the leaf's own mode is wrong" above.
        ("project/app.py/deeper", "is absent at commit"),
    ],
    ids=["regular-file", "tree", "absent", "absent-through-non-tree-ancestor"],
)
def test_a_declared_omission_naming_the_wrong_kind_is_a_config_error(
    tmp_path: Path, declared_path: str, expected_fragment: str
) -> None:
    """(§3.5) A declared tree/regular file/absent path is `BAD_LANE_CONFIG`,
    decided BEFORE the general structural walk runs at all -- proven
    alongside the SAME positive control every other negative in this module
    carries.
    """
    repo, commit = _happy_path_repo(tmp_path)
    policy = _omission_policy(*DECLARED_LEAVES, declared_path)
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, policy), timeout=TIMEOUT
        ):
            pytest.fail(f"declaring {declared_path!r} must be refused")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert declared_path in str(caught.value)
    assert expected_fragment in str(caught.value)

    # CONTROL
    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path, "control"), commit, _omission_policy(*DECLARED_LEAVES)),
        timeout=TIMEOUT,
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            _assert_happy_path_materialization(snapshot.root)


def _gitlink_repo(tmp_path: Path) -> tuple[Path, str]:
    """One ordinary file, one declarable unsafe link, and one gitlink --
    built with `update-index --add --cacheinfo`, which needs no real
    submodule and accepts an arbitrary commit OID for the gitlink target
    (measured: git does not require that OID to resolve to anything).
    """
    repo = tmp_path / "gitlink-consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_bytes(b"ordinary\n")
    (repo / "topos").mkdir()
    os.symlink("/etc/passwd", repo / "topos" / "bad_link")
    _git(repo, "add", "-A")
    fake_commit = "a" * 40
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{fake_commit},vendor/submod")
    _git(repo, "commit", "-q", "-m", "gitlink fixture")
    return repo, _git_text(repo, "rev-parse", "HEAD")


def test_a_declared_gitlink_is_bad_lane_config_before_the_generic_refusal_runs(
    tmp_path: Path,
) -> None:
    """(§3.3.4's own named ordering) At a DECLARED pathname, "must be mode
    120000" runs before the generic gitlink refusal, so a declared gitlink
    is deterministically `BAD_LANE_CONFIG` -- never the plain `GIT_FAILED`
    the SAME gitlink gets when it is not named at all (the control below).
    """
    repo, commit = _gitlink_repo(tmp_path)

    declared = IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=("vendor/submod",),
    )
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path, "declared-gitlink"), commit, declared),
            timeout=TIMEOUT,
        ):
            pytest.fail("a declared gitlink must be refused as BAD_LANE_CONFIG")
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "vendor/submod" in str(caught.value)
    assert "gitlink" in str(caught.value)

    # CONTROL: the SAME gitlink, undeclared -- and a real unsafe link
    # legitimately declared alongside it -- still fails, but as the
    # EXISTING plain `GIT_FAILED` gitlink refusal, unchanged (§3.5's own
    # "existing malformed tree name/mode, gitlink... existing GIT_FAILED,
    # unchanged"). This proves a gitlink can never be legalised by
    # declaring something else in the same tree.
    undeclared = IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=("topos/bad_link",),
    )
    with pytest.raises(AssayError) as undeclared_caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path, "undeclared-gitlink"), commit, undeclared),
            timeout=TIMEOUT,
        ):
            pytest.fail("an undeclared gitlink must still refuse the whole snapshot")
    assert undeclared_caught.value.reason_code is ReasonCode.GIT_FAILED
    assert "gitlink" in str(undeclared_caught.value)
    assert "unsafe_symlink_omissions" not in str(undeclared_caught.value)


# ---------------------------------------------------------------------------
# Pathological pathnames: any byte except NUL/`/` is legal in a declared
# omission (Controller's own measurement of the WI-1 loader). `-z` is what
# keeps both the materialisation and the verification parse exact.
# ---------------------------------------------------------------------------


def test_a_declared_omission_pathname_may_contain_a_newline(tmp_path: Path) -> None:
    """A tracked symlink pathname may hold any byte except NUL or `/` --
    including a literal newline. It must materialize correctly (absent),
    appear EXACTLY once in the real `git ls-files -v -z` skip set, and
    leave `git status` clean, proving the whole pipeline (declared-path
    resolution, `update-index --skip-worktree -z --stdin`, and the `_verify`
    parse) is NUL-delimited end to end, not line-oriented anywhere.
    """
    repo = tmp_path / "newline-consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_bytes(b"ordinary\n")
    odd_name = "topos/weird\nlink"
    odd_path = repo / "topos"
    odd_path.mkdir()
    os.symlink("/etc/passwd", repo / odd_name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "newline-pathname fixture")
    commit = _git_text(repo, "rev-parse", "HEAD")

    policy = IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=(odd_name,),
    )
    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, policy), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root = snapshot.root
            assert not os.path.lexists(root / "topos" / "weird\nlink")
            assert (root / "f.txt").read_bytes() == b"ordinary\n"
            assert _git_text(root, "status", "--porcelain=v1") == ""
            skip = _parse_skip_worktree(_git(root, "ls-files", "-v", "-z"))
            assert skip == {odd_name.encode("utf-8")}


# ---------------------------------------------------------------------------
# Replacement preservation (§3.3.7): a mutant/replacement commit keeps the
# omitted symlink entries and every ordinary sibling.
# ---------------------------------------------------------------------------


def test_a_replacement_commit_preserves_the_omitted_entries(tmp_path: Path) -> None:
    repo, commit = _happy_path_repo(tmp_path)
    policy = _omission_policy(*DECLARED_LEAVES)
    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, policy), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize_replacement(
            path=PurePosixPath("project/app.py"),
            expected=b"import os\n",
            replacement=b"import sys\n",
            timeout=TIMEOUT,
        ) as changed:
            root = changed.root
            assert (root / "project" / "app.py").read_bytes() == b"import sys\n"
            for leaf in DECLARED_LEAVES:
                assert not os.path.lexists(root / leaf), leaf
            assert (root / "cmru" / "root_file.txt").read_bytes() == b"root dependency\n"
            assert _git_text(root, "status", "--porcelain=v1") == ""
            assert _git_text(root, "write-tree") == _git_text(root, "rev-parse", "HEAD^{tree}")
            skip = _parse_skip_worktree(_git(root, "ls-files", "-v", "-z"))
            assert skip == {leaf.encode("utf-8") for leaf in DECLARED_LEAVES}
            assert changed.commit != commit

        # the concurrently unrelated base snapshot is unaffected
        with prepared.materialize(timeout=TIMEOUT) as base:
            _assert_happy_path_materialization(base.root)


# ---------------------------------------------------------------------------
# Repository mode is unaffected by an empty/default IsolationConfig -- the
# omission plumbing is a strict no-op when there is nothing declared.
# ---------------------------------------------------------------------------


def test_repository_mode_never_creates_a_skip_worktree_entry(tmp_path: Path) -> None:
    repo = tmp_path / "plain-consumer"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_bytes(b"ordinary\n")
    os.symlink("f.txt", repo / "link")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "plain repository-mode fixture")
    commit = _git_text(repo, "rev-parse", "HEAD")

    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, _repository_policy()), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root = snapshot.root
            assert (root / "link").is_symlink()
            skip = _parse_skip_worktree(_git(root, "ls-files", "-v", "-z"))
            assert skip == frozenset()
            assert _git_text(root, "write-tree") == _git_text(root, "rev-parse", "HEAD^{tree}")


# ---------------------------------------------------------------------------
# `_verify`'s three NEW proofs (§3.3.8), driven directly. These guards are
# defence in depth, matching test_isolation.py's own
# `test_the_pre_yield_verification_catches_a_damaged_snapshot`: the SAME
# rationale applies here, and one of the three ("write-tree equals
# HEAD^{tree}") genuinely CANNOT be violated by any honest tampering once
# `git status` is clean -- status-clean already means the index's
# (mode, oid, path) triples exactly equal HEAD's tree content, and
# `write-tree` is a pure function of that same content, so it is driven with
# a stubbed `write-tree` answer rather than a real corrupted repository. The
# other two ARE reachable by real tampering (skip-worktree is an index FLAG,
# invisible to both `write-tree` and, because of what skip-worktree means,
# to `git status` itself) and are driven that way instead.
# ---------------------------------------------------------------------------


def _real_run(prepared, git_dir: Path, root: Path):
    def run(*args: str, stdin_bytes: bytes = b"", identity: bool = False) -> bytes:
        return git_module._p22_git(
            prepared._source.git_executable,
            git_dir=git_dir,
            work_tree=root,
            args=args,
            deadline=git_module._P22Deadline(TIMEOUT),
            cwd=root,
            stdin_bytes=stdin_bytes,
            identity=identity,
        )

    return run


def test_verify_catches_a_lying_write_tree_that_diverges_from_head(tmp_path: Path) -> None:
    repo, commit = _happy_path_repo(tmp_path)
    policy = _omission_policy(*DECLARED_LEAVES)
    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, policy), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root, git_dir = snapshot.root, snapshot.root / ".git"
            real = _real_run(prepared, git_dir, root)

            def lying(*args: str, stdin_bytes: bytes = b"", identity: bool = False) -> bytes:
                if args[:1] == ("write-tree",):
                    return b"0" * 40 + b"\n"
                return real(*args, stdin_bytes=stdin_bytes, identity=identity)

            with pytest.raises(AssayError, match="does not match HEAD") as caught:
                prepared._verify(root, git_dir, commit, lying)
            assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_verify_catches_a_skip_worktree_set_that_diverges_from_the_declared_omissions(
    tmp_path: Path,
) -> None:
    repo, commit = _happy_path_repo(tmp_path)
    policy = _omission_policy(*DECLARED_LEAVES)
    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, policy), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root, git_dir = snapshot.root, snapshot.root / ".git"
            # Clear the skip bit on ONE declared omission and materialize
            # matching content: `write-tree` is unaffected (flags are not
            # tree content) and `git status` stays clean (the worktree now
            # genuinely matches the un-skipped index entry), so only the
            # exact skip-SET comparison can catch the divergence. Every
            # declared leaf under `topos/` was omitted, so `topos/` itself
            # was never materialized (§3.3's own "may leave an empty parent
            # directory" -- here it is simply absent) and must be recreated
            # before a file can be placed under it.
            (root / "topos").mkdir(parents=True, exist_ok=True)
            _git(root, "update-index", "--no-skip-worktree", "--", ABSOLUTE_LEAF)
            os.symlink("/etc/passwd", root / ABSOLUTE_LEAF)
            assert _git_text(root, "status", "--porcelain=v1") == ""

            run = _real_run(prepared, git_dir, root)
            with pytest.raises(AssayError, match="skip-worktree entries") as caught:
                prepared._verify(root, git_dir, commit, run)
            assert caught.value.reason_code is ReasonCode.GIT_FAILED


def test_verify_catches_a_materialized_omitted_leaf(tmp_path: Path) -> None:
    repo, commit = _happy_path_repo(tmp_path)
    policy = _omission_policy(*DECLARED_LEAVES)
    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, policy), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root, git_dir = snapshot.root, snapshot.root / ".git"
            # Recreate a file at a declared omission's path WITHOUT clearing
            # its skip-worktree bit. `git status` cannot see this at all --
            # skip-worktree tells git to assume the working tree already
            # matches -- so only a direct filesystem check catches it
            # (measured: M5/M7's own basis for this exact fact). `topos/`
            # itself was never materialized (every leaf under it was
            # omitted), so it is recreated first.
            (root / "topos").mkdir(parents=True, exist_ok=True)
            os.symlink("/etc/passwd", root / ABSOLUTE_LEAF)
            assert _git_text(root, "status", "--porcelain=v1") == ""

            run = _real_run(prepared, git_dir, root)
            with pytest.raises(AssayError, match="materialised the") as caught:
                prepared._verify(root, git_dir, commit, run)
            assert caught.value.reason_code is ReasonCode.GIT_FAILED
