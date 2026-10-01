"""B041(b) — ``[lanes.<n>.isolation] link_paths``: the declared speed path,
rules 1–6, with the teardown canary the design review flagged as risk #1.

A snapshot is built by ``git read-tree`` from committed objects, so a
gitignored ``node_modules/`` is absent from it by construction (A-161/A-184).
That is correct and stays. ``link_paths`` is the DECLARED, recorded escape
hatch: named directories from the invoking checkout are symlinked into every
snapshot the lane creates, and the verdict then states plainly that its
snapshot was not purely committed objects.

**Rule 6 is why this module exists.** A wrong implementation — a teardown that
follows the link — deletes the consumer's real ``node_modules``. "Python's
``shutil.rmtree`` unlinks symlinks rather than recursing" is a true sentence
about the stdlib and it is NOT the proof B041(b) asks for: it is a claim about
code assay does not own, it would not survive someone switching to a manual
walker, and it says nothing about the exception-path teardown beside it. So
this module plants a REAL symlink to a REAL directory outside the snapshot,
puts a canary file inside that directory, runs the REAL teardown — both the
success path and the exception path — and asserts the canary is still there
with its bytes intact.

Every git fact here is checked with a REAL ``git`` subprocess driven
independently of assay (the established pattern in
``test_isolation_unsafe_symlink_omissions.py``, whose harness this module
reuses): asserting assay's own view of a snapshot with assay's own parser
would prove nothing about the snapshot.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest
from conftest import Project

from assay.config import IsolationConfig, LaneConfigError, load_lane_file
from assay.errors import AssayError, Outcome, ReasonCode
from assay.isolation import (
    DEFAULT_SNAPSHOT_LIMITS,
    SnapshotSpec,
    _remove_owned_tree,
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


def _git(repo: Path, *args: str) -> bytes:
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
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout


def _git_text(repo: Path, *args: str) -> str:
    return _git(repo, *args).decode("utf-8").strip()


def _seed_repo(tmp_path: Path, *, ignore_rule: str = "app/node_modules") -> tuple[Path, str]:
    """A repository whose committed ``.gitignore`` ignores ``app/node_modules``
    — the realistic shape, and the one that makes the link invisible to
    assay's own dirt check without any special pleading.

    *ignore_rule* is a parameter, not a constant, because the DEFAULT here is
    the corrected spelling and the trailing-slash spelling is a real, measured
    trap that one test below pins deliberately.
    """
    repo = tmp_path / "repo"
    (repo / "app" / "src").mkdir(parents=True)
    (repo / "app" / "src" / "index.ts").write_text("export const x = 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text(f"{ignore_rule}\n", encoding="utf-8")
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Fixture", "-c", "user.email=f@a.invalid", "commit", "-q", "-m", "seed")
    return repo.resolve(), _git_text(repo, "rev-parse", "HEAD")


def _install_closure(repo: Path, *, name: str = "app/node_modules") -> Path:
    """The directory the ENVIRONMENT provides — present in the checkout,
    absent from the commit. A ``.marker`` file inside it is the canary rule 6
    is about."""
    closure = repo / name
    (closure / "left-pad").mkdir(parents=True)
    (closure / "left-pad" / "index.js").write_text(
        "module.exports = () => 'real'\n", encoding="utf-8"
    )
    return closure


def _scratch(tmp_path: Path, name: str = "scratch") -> Path:
    scratch = tmp_path / name
    scratch.mkdir()
    return scratch


def _policy(*link_paths: str, selection: str = "repository") -> IsolationConfig:
    return IsolationConfig(
        snapshot_selection=selection,
        unsafe_symlink_omissions=(),
        link_paths=tuple(link_paths),
    )


def _spec(repo: Path, scratch: Path, commit: str, policy: IsolationConfig) -> SnapshotSpec:
    return SnapshotSpec(
        repo_top=repo,
        commit=commit,
        project_prefix=PurePosixPath("."),
        scratch_root=scratch.resolve(),
        snapshot_policy=policy,
        limits=DEFAULT_SNAPSHOT_LIMITS,
    )


# --------------------------------------------------------------------------
# Rule 3: materialised as a symlink, for every snapshot the lane creates
# --------------------------------------------------------------------------


def test_a_declared_link_is_a_symlink_to_the_checkout_and_reads_through(
    tmp_path: Path,
):
    repo, commit = _seed_repo(tmp_path)
    closure = _install_closure(repo)

    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules")),
        timeout=TIMEOUT,
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            planted = snapshot.root / "app" / "node_modules"
            assert planted.is_symlink()
            assert planted.resolve() == closure.resolve()
            # It READS THROUGH: the point of the feature is a usable closure,
            # not a dangling entry that merely looks right.
            assert (planted / "left-pad" / "index.js").read_text(
                encoding="utf-8"
            ) == "module.exports = () => 'real'\n"


def test_every_snapshot_the_repository_yields_carries_the_link(tmp_path: Path):
    """Rule 3 says "for every snapshot the lane creates (R3 canary snapshots
    included)". Both snapshots below come from the SAME prepared repository
    through the same ``materialize`` entry point every consumer uses, which is
    where the property is actually structural."""
    repo, commit = _seed_repo(tmp_path)
    _install_closure(repo)
    spec = _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules"))

    with prepare_snapshot(spec, timeout=TIMEOUT) as prepared:
        for _ in range(2):
            with prepared.materialize(timeout=TIMEOUT) as snapshot:
                assert (snapshot.root / "app" / "node_modules").is_symlink()


def test_a_replacement_snapshot_also_carries_the_link(tmp_path: Path):
    """An R2 mutant's snapshot is a ``materialize_replacement`` call, not a
    plain ``materialize`` — a second entry point, and the one a
    per-call-site implementation would most easily have missed."""
    repo, commit = _seed_repo(tmp_path)
    _install_closure(repo)
    spec = _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules"))
    original = b"export const x = 1\n"

    with prepare_snapshot(spec, timeout=TIMEOUT) as prepared:
        with prepared.materialize_replacement(
            path=PurePosixPath("app/src/index.ts"),
            expected=original,
            replacement=b"export const x = 2\n",
            timeout=TIMEOUT,
        ) as snapshot:
            assert (snapshot.root / "app" / "node_modules").is_symlink()
            assert (snapshot.root / "app" / "src" / "index.ts").read_bytes() == (
                b"export const x = 2\n"
            )


def test_a_lane_declaring_no_link_paths_plants_nothing(tmp_path: Path):
    repo, commit = _seed_repo(tmp_path)
    _install_closure(repo)

    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, _policy()), timeout=TIMEOUT
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            assert not (snapshot.root / "app" / "node_modules").exists()


def test_the_snapshot_stays_clean_to_a_real_git_with_the_link_in_place(
    tmp_path: Path,
):
    """Checked with a REAL git, independently of assay's own ``_verify``.

    This is the property that keeps the runner's post-command dirt check from
    reporting the lane's own declared link as ``DIRTY_TREE``."""
    repo, commit = _seed_repo(tmp_path)
    _install_closure(repo)

    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules")),
        timeout=TIMEOUT,
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            assert _git_text(snapshot.root, "status", "--porcelain=v1") == ""
            assert _git_text(snapshot.root, "write-tree") == _git_text(
                snapshot.root, "rev-parse", "HEAD^{tree}"
            )


# --------------------------------------------------------------------------
# Rule 6 — the teardown canary. The review's #1 flagged risk.
# --------------------------------------------------------------------------


def test_teardown_removes_the_link_and_never_its_target(tmp_path: Path):
    """A REAL symlink to a REAL directory outside the snapshot, a canary file
    inside that directory, the REAL teardown, and the canary asserted intact
    afterwards — never "stdlib rmtree unlinks symlinks, so this is fine"."""
    repo, commit = _seed_repo(tmp_path)
    closure = _install_closure(repo)
    canary = closure / "left-pad" / "index.js"
    canary_bytes = canary.read_bytes()

    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules")),
        timeout=TIMEOUT,
    ) as prepared:
        with prepared.materialize(timeout=TIMEOUT) as snapshot:
            root = snapshot.root
            assert (root / "app" / "node_modules").is_symlink()
        # Teardown has run: the snapshot is gone...
        assert not root.exists()

    # ...and the TARGET is untouched, contents and bytes.
    assert closure.is_dir()
    assert canary.is_file()
    assert canary.read_bytes() == canary_bytes


def test_the_exception_path_teardown_also_spares_the_target(tmp_path: Path):
    """``_materialize`` has TWO teardowns: the clean one above and a
    best-effort ``rmtree(root, ignore_errors=True)`` on the exception path. A
    rule-6 proof that covers only the first covers the path a consumer hits
    when everything works, and not the one they hit when it does not."""
    repo, commit = _seed_repo(tmp_path)
    closure = _install_closure(repo)
    canary = closure / "left-pad" / "index.js"
    canary_bytes = canary.read_bytes()

    with prepare_snapshot(
        _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules")),
        timeout=TIMEOUT,
    ) as prepared:
        with pytest.raises(RuntimeError, match="deliberate"):
            with prepared.materialize(timeout=TIMEOUT) as snapshot:
                assert (snapshot.root / "app" / "node_modules").is_symlink()
                raise RuntimeError("deliberate: exercise the exception teardown")

    assert closure.is_dir()
    assert canary.read_bytes() == canary_bytes


def test_the_teardown_helper_itself_spares_a_symlinked_target(tmp_path: Path):
    """``_remove_owned_tree`` in isolation, on a hand-built tree — so the
    property is pinned on the FUNCTION, not only on the one composition above
    that happens to call it."""
    target = tmp_path / "outside"
    (target / "deep").mkdir(parents=True)
    canary = target / "deep" / "canary.txt"
    canary.write_text("survive me\n", encoding="utf-8")

    owned = tmp_path / "owned"
    (owned / "nested").mkdir(parents=True)
    os.symlink(target, owned / "linked", target_is_directory=True)
    os.symlink(target, owned / "nested" / "also-linked", target_is_directory=True)

    _remove_owned_tree(owned)

    assert not owned.exists()
    assert canary.read_text(encoding="utf-8") == "survive me\n"


# --------------------------------------------------------------------------
# Rules 1, 2, 4 — the refusals, each with its own terminal
# --------------------------------------------------------------------------


def test_an_absent_target_is_no_measurement_missing_external_tool(tmp_path: Path):
    """Rule 1. NOT ``BAD_LANE_CONFIG``: the lane file is right, and the
    environment did not provide what it declared."""
    repo, commit = _seed_repo(tmp_path)  # no _install_closure

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules")),
            timeout=TIMEOUT,
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("an absent link target must refuse")
    assert caught.value.outcome is Outcome.NO_MEASUREMENT
    assert caught.value.reason_code is ReasonCode.MISSING_EXTERNAL_TOOL
    assert "app/node_modules" in str(caught.value)


def test_a_target_that_is_a_file_is_refused_the_same_way(tmp_path: Path):
    repo, commit = _seed_repo(tmp_path)
    (repo / "app" / "node_modules").write_text("not a directory\n", encoding="utf-8")

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules")),
            timeout=TIMEOUT,
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("a non-directory link target must refuse")
    assert caught.value.reason_code is ReasonCode.MISSING_EXTERNAL_TOOL


def test_a_tracked_path_is_refused_bad_lane_config(tmp_path: Path):
    """Rule 2. Linking a tracked path would replace committed content with
    working-tree content — the exact substitution a committed-object snapshot
    exists to prevent."""
    repo, commit = _seed_repo(tmp_path)

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, _policy("app/src")),
            timeout=TIMEOUT,
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("a tracked link path must refuse")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "TRACKED" in str(caught.value)


def test_a_tracked_FILE_path_is_refused_too(tmp_path: Path):
    repo, commit = _seed_repo(tmp_path)

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, _policy("app/src/index.ts")),
            timeout=TIMEOUT,
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("a tracked link path must refuse")
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_a_missing_parent_is_refused_and_is_never_created(tmp_path: Path):
    """Rule 4. Never ``mkdir -p`` into the snapshot: a generated parent is
    tree structure the commit does not have."""
    repo, commit = _seed_repo(tmp_path)
    (repo / "nowhere").mkdir()
    (repo / "nowhere" / "node_modules").mkdir()

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, _policy("nowhere/node_modules")),
            timeout=TIMEOUT,
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("a link path with no tracked parent must refuse")
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "parent" in str(caught.value)


def test_a_link_the_repository_does_not_ignore_is_refused_by_name(tmp_path: Path):
    """Not one of B041(b)'s six rules, and here on purpose: without it the
    feature self-refuses LATER with a misnamed terminal. ``dirty_paths``
    consults committed ignore policy only (A-177/A-290), so an un-ignored link
    surfaces as ``DIRTY_TREE`` after the lane's command — blaming the command
    for something the lane declared."""
    repo, commit = _seed_repo(tmp_path)
    # A closure at a path the committed .gitignore does not cover.
    (repo / "app" / "vendored").mkdir()
    (repo / "app" / "vendored" / "thing.js").write_text("x\n", encoding="utf-8")

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, _policy("app/vendored")),
            timeout=TIMEOUT,
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("an un-ignored link must refuse")
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert ".gitignore" in str(caught.value)


def test_a_trailing_slash_ignore_rule_does_not_cover_the_link_and_is_refused(
    tmp_path: Path,
):
    """**Measured while implementing B041(b), and not anticipated by its own
    contract text.**

    The realistic consumer rule is ``node_modules/`` — with the trailing
    slash, which is what every JS project's ``.gitignore`` actually carries.
    Git treats a trailing-slash pattern as DIRECTORY-ONLY, and what assay
    plants is a SYMLINK, which git does not treat as a directory. So the
    consumer's existing, correct-looking ignore rule does NOT cover the link,
    and both halves of ``dirty_paths``' query report it.

    Measured directly with a real git: with ``app/node_modules/`` both
    ``git status --porcelain`` and
    ``git ls-files --others --exclude-per-directory=.gitignore`` report
    ``app/node_modules``; with ``app/node_modules`` both report nothing.

    Pinned as a REFUSAL rather than papered over: the honest outcome is a
    named error at materialisation naming the trailing slash and the fix, not
    a ``DIRTY_TREE`` after the lane's command that blames the command.
    """
    repo, commit = _seed_repo(tmp_path, ignore_rule="app/node_modules/")
    _install_closure(repo)

    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _spec(repo, _scratch(tmp_path), commit, _policy("app/node_modules")),
            timeout=TIMEOUT,
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("a trailing-slash ignore rule must refuse")
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "TRAILING SLASH" in str(caught.value)


def test_a_refusal_leaves_no_snapshot_behind(tmp_path: Path):
    repo, commit = _seed_repo(tmp_path)
    scratch = _scratch(tmp_path)

    with pytest.raises(AssayError):
        with prepare_snapshot(
            _spec(repo, scratch, commit, _policy("app/node_modules")), timeout=TIMEOUT
        ) as prepared:
            with prepared.materialize(timeout=TIMEOUT):
                pytest.fail("must refuse")
    assert list(scratch.iterdir()) == []


# --------------------------------------------------------------------------
# Rule 5's config half, and the model/loader grammar
# --------------------------------------------------------------------------


def test_link_paths_is_independent_of_selection(tmp_path: Path):
    """A-366: linking content IN is orthogonal to the unsafe-symlink omission
    policy, so both selections accept the key."""
    for selection, omissions in (
        ("repository", ()),
        ("repository-minus-unsafe-symlinks", ("x",)),
    ):
        policy = IsolationConfig(
            snapshot_selection=selection,
            unsafe_symlink_omissions=omissions,
            link_paths=("app/node_modules",),
        )
        assert policy.link_paths == ("app/node_modules",)


def test_an_unsorted_link_paths_list_is_refused_never_silently_sorted():
    with pytest.raises(LaneConfigError, match="strictly ascending"):
        IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            link_paths=("b", "a"),
        )


def test_a_duplicate_link_path_is_refused():
    with pytest.raises(LaneConfigError, match="strictly ascending"):
        IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            link_paths=("a", "a"),
        )


@pytest.mark.parametrize(
    "bad", ["", "/abs", "../outside", "a/../b", "./a", "a/", ".git/x", "a\\b"]
)
def test_a_bad_link_path_spelling_is_refused(bad: str):
    with pytest.raises(LaneConfigError):
        IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            link_paths=(bad,),
        )


def test_more_than_sixty_four_entries_is_refused():
    entries = tuple(f"d{index:03d}" for index in range(65))
    with pytest.raises(LaneConfigError, match="ceiling"):
        IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            link_paths=entries,
        )


def test_the_declared_lane_round_trips_and_omits_an_undeclared_key(project: Project):
    from conftest import R1_LANE

    text = R1_LANE.replace(
        'snapshot_selection = "repository"\n',
        'snapshot_selection = "repository"\nlink_paths = ["app/node_modules"]\n',
        1,
    )
    lane = load_lane_file(project.write(text)).lane("package")
    assert lane.isolation is not None
    assert lane.isolation.link_paths == ("app/node_modules",)
    assert lane.isolation.as_declared()["link_paths"] == ["app/node_modules"]

    plain = load_lane_file(project.write(R1_LANE)).lane("package")
    assert plain.isolation is not None
    assert plain.isolation.link_paths == ()
    assert "link_paths" not in plain.isolation.as_declared()


def test_an_empty_declared_list_is_refused_rather_than_meaning_absent(
    project: Project,
):
    from conftest import R1_LANE

    text = R1_LANE.replace(
        'snapshot_selection = "repository"\n',
        'snapshot_selection = "repository"\nlink_paths = []\n',
        1,
    )
    with pytest.raises(LaneConfigError, match="declared but empty"):
        load_lane_file(project.write(text))


# --------------------------------------------------------------------------
# Rule 5 — the verdict says its snapshot was not purely committed objects
# --------------------------------------------------------------------------


def test_the_verdict_records_link_paths_end_to_end(git_repo):
    """Rule 5, through the REAL runner: a lane that links a closure produces a
    verdict whose ``snapshot_policy.link_paths`` names it, so a reviewer can
    tell a purely-committed-objects verdict from this one without re-running
    anything."""
    from assay import runner
    from assay.adapters.python import PythonAdapter
    from assay.config import IsolationConfig as _IsolationConfig
    from conftest import make_lane

    git_repo.write(".gitignore", "app/node_modules\n")
    git_repo.write("app/src/index.ts", "export const x = 1\n")
    git_repo.commit_all("seed link fixture")
    (git_repo.path / "app" / "node_modules" / "left-pad").mkdir(parents=True)

    lane = make_lane(
        rigor=("R0", "R2"),
        argv=("/bin/sh", "-c", "test -d app/node_modules/left-pad"),
        isolation=_IsolationConfig(
            snapshot_selection="repository",
            unsafe_symlink_omissions=(),
            link_paths=("app/node_modules",),
        ),
        judge=_r2_judge_for(git_repo),
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.snapshot_policy is not None
    assert verdict.snapshot_policy.link_paths == ("app/node_modules",)
    assert verdict.to_dict()["snapshot_policy"]["link_paths"] == ["app/node_modules"]
    # The lane's command PROVED the link was usable from inside the snapshot:
    # `test -d app/node_modules/left-pad` only passes if it read through.
    r0_claim = next(claim for claim in verdict.claims if claim.rigor == "R0")
    assert r0_claim.status is Outcome.PASS


def test_a_lane_without_link_paths_omits_the_key_entirely(git_repo):
    from conftest import make_lane

    from assay import runner
    from assay.adapters.python import PythonAdapter

    git_repo.write("app/src/index.ts", "export const x = 1\n")
    git_repo.commit_all("seed link fixture")

    lane = make_lane(
        rigor=("R0", "R2"),
        argv=("/bin/sh", "-c", "exit 0"),
        judge=_r2_judge_for(git_repo),
    )

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="0.1.0",
    )

    assert verdict.snapshot_policy is not None
    assert verdict.snapshot_policy.link_paths is None
    assert "link_paths" not in verdict.to_dict()["snapshot_policy"]


def _r2_judge_for(git_repo):
    """A minimal R2 judge over an empty diff -- enough to make the lane
    higher-rigor (so it materialises a snapshot at all) without dragging a
    mutation fixture into a module about isolation."""
    from assay.config import MutationConfig
    from conftest import make_r2_judge

    return make_r2_judge(
        source_root_paths=(git_repo.path / "app",),
        base=git_repo.head(),
        mutation=MutationConfig(
            jobs=1, max_mutants=10, operators=("python:compare-swap",)
        ),
    )
