"""B006(a)/A-269 WI-3 -- the ONE runner-level collision check (§3.4), and
the cross-unit proof that a single immutable ``IsolationConfig`` governs
every unit of a higher-rigor lane, not only the first (O3).

This module deliberately does NOT re-test §3.3's own P22 mechanism (P22's
commit validation, the index skip-worktree invariants, the symlink outcome
matrix) -- ``test_isolation_unsafe_symlink_omissions.py`` already is O2's
own acceptance module for that. What is new here is runner-level wiring
landed on TOP of that already-working machinery:

1. :func:`assay.runner._refuse_coverage_artifact_omission_collision` --
   reachable ONLY through the public frozen :class:`~assay.config.Lane`
   dataclass (a loaded ``assay.toml`` cannot reach it at all,
   ``config._load_coverage`` already refuses the live-symlink-escape shape
   at load time), so every test below constructs that public API directly
   rather than writing a lane file.
2. The cross-unit proof (O3): a ``process_runner`` double that inspects its
   own live ``cwd`` -- while the snapshot is still open, never after
   cleanup -- and proves the identical omission policy held for the
   baseline, the one generated mutant, and BOTH canary halves.

§3.4 explicitly forbids adding parallel containment checks for source
roots, cwd, mutation candidates, canary targets, or B005 targets -- this
module does not add any.

3. §6 WI-3's own embargo, half (b) only. WI-3 imposed two halves: (a) no
   LIVE checked-in lane may declare omission mode, and (b) no assay release
   may be cut, until WI-4 lands the v6 ``snapshot_policy`` record. **Wave-1
   WI-4 is this very commit** -- the v6 record now exists, so half (a)'s own
   trigger condition is satisfied and ``test_no_live_lane_declares_
   omission_mode_yet`` is RETIRED here, deliberately, not left failing and
   not silently deleted: keeping it would turn a legitimate future lane's
   real opt-in to omission mode into a spurious failure, for a prohibition
   that no longer exists. Half (b) is a different kind of check -- an audit
   of git tag HISTORY, not of WI-4's landing state -- and stays exactly as
   written and exactly as binding: it protects the window between WI-1
   landing lane schema v2 and this branch actually merging, so a release
   cut from an unmerged intermediate state still has no v6 policy record to
   attest it until the gate is green and this branch lands on the trunk
   history the tag check walks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest
from conftest import (
    PROJECT_ROOT,
    FakeAdapter,
    GitRepo,
    make_lane,
    make_r1_judge,
    write_coverage_json,
)

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import (
    CanaryConfig,
    CoverageConfig,
    IsolationConfig,
    JudgeConfig,
    Lane,
    MutationConfig,
)
from assay.errors import AssayError, Outcome, ReasonCode
from assay.isolation import DEFAULT_SNAPSHOT_LIMITS, SnapshotSpec, prepare_snapshot

# ---------------------------------------------------------------------------
# Shared IsolationConfig builders (mirroring test_isolation_unsafe_symlink_
# omissions.py's own _repository_policy/_omission_policy, kept local rather
# than imported -- an independent oracle, not a shared one, per that
# module's own stated rationale).
# ---------------------------------------------------------------------------


def _repository_policy() -> IsolationConfig:
    return IsolationConfig(snapshot_selection="repository", unsafe_symlink_omissions=())


def _omission_policy(*paths: str) -> IsolationConfig:
    return IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=tuple(sorted(paths, key=lambda p: p.encode("utf-8"))),
    )


def _r1_lane(
    *,
    coverage_artifact: str,
    omissions: IsolationConfig,
    rigor: tuple[str, ...] = ("R0", "R1"),
) -> Lane:
    """A directly constructed R1 ``Lane`` -- the public API shape §3.4 says
    is the only reachable source for the collision branch. Values other
    than *coverage_artifact*/*omissions* are irrelevant filler:
    :func:`~assay.runner._refuse_coverage_artifact_omission_collision`
    never touches the filesystem, so ``source_root_paths`` need not even
    exist.
    """
    judge = JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(Path("/nonexistent/pkg"),),
        fail_under=100.0,
        allow_excluded=False,
        coverage=CoverageConfig(format="coverage-py-json", artifact=coverage_artifact),
        mutation=None,
        canary=None,
        base="main",
    )
    return make_lane(rigor=rigor, judge=judge, isolation=omissions)


# ---------------------------------------------------------------------------
# The collision check's own branches, driven directly (no filesystem, no
# git, no run_lane) -- exactly the shape §3.4's own reachability note names.
# ---------------------------------------------------------------------------


def test_repository_mode_is_a_strict_noop_even_with_a_colliding_looking_artifact() -> None:
    """Repository mode declares no omissions at all, so there is nothing to
    collide with -- proven by a coverage artifact spelled to LOOK like it
    is beneath something, alongside the real omission-mode collision this
    same spelling WOULD trip (the differential control for this branch).
    """
    lane = _r1_lane(coverage_artifact="danger/coverage.json", omissions=_repository_policy())

    runner._refuse_coverage_artifact_omission_collision(
        lane=lane, snapshot_policy=lane.isolation, project_prefix=PurePosixPath("cmru")
    )

    # DIFFERENTIAL CONTROL: the identical artifact spelling, under omission
    # mode, WITH that exact leaf declared, DOES raise -- proving the no-op
    # above is caused by the selection, not by some accident of the path.
    colliding = _r1_lane(
        coverage_artifact="danger/coverage.json",
        omissions=_omission_policy("cmru/danger"),
    )
    with pytest.raises(AssayError) as caught:
        runner._refuse_coverage_artifact_omission_collision(
            lane=colliding,
            snapshot_policy=colliding.isolation,
            project_prefix=PurePosixPath("cmru"),
        )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_a_lane_with_no_judge_at_all_is_a_noop() -> None:
    """A directly constructed ``Lane`` may pair a higher-rigor ``rigor``
    tuple with ``judge=None`` -- nothing this function owns validates that
    combination (§3.4 forbids exactly this kind of parallel containment
    check), so it must not crash; it has nothing to compare."""
    lane = make_lane(rigor=("R0", "R2"), judge=None, isolation=_omission_policy("cmru/danger"))

    runner._refuse_coverage_artifact_omission_collision(
        lane=lane, snapshot_policy=lane.isolation, project_prefix=PurePosixPath("cmru")
    )


def test_an_r2_only_lane_with_no_declared_coverage_artifact_is_a_noop() -> None:
    """The REAL reachable shape of "no coverage artifact": an R2-only lane
    (``JUDGE_FIELDS_BY_RIGOR["R2"]`` never requires ``coverage``) declared
    alongside the identical omission a coverage-bearing lane WOULD collide
    with -- proving the guard is keyed on "is there an artifact at all",
    not merely "was R1 declared".
    """
    judge = JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(Path("/nonexistent/pkg"),),
        fail_under=None,
        allow_excluded=None,
        coverage=None,
        mutation=MutationConfig(jobs=1, max_mutants=10, operators=("python:compare-swap",)),
        canary=None,
        base="main",
    )
    lane = make_lane(rigor=("R0", "R2"), judge=judge, isolation=_omission_policy("cmru/danger"))

    runner._refuse_coverage_artifact_omission_collision(
        lane=lane, snapshot_policy=lane.isolation, project_prefix=PurePosixPath("cmru")
    )


@pytest.mark.parametrize(
    "artifact,expect_collision",
    [
        (".assay/coverage.json", True),  # beneath the declared omission
        (".assay", True),  # exactly equal to the declared omission
        (".assay2/coverage.json", False),  # a sibling name, NOT beneath it
        ("cov.json", False),  # unrelated entirely
    ],
    ids=["beneath", "equal", "sibling-not-beneath", "unrelated"],
)
def test_is_relative_to_not_a_string_prefix(artifact: str, expect_collision: bool) -> None:
    """§3.4 requires ``PurePosixPath.is_relative_to``, never a string
    prefix comparison -- ``.assay2/...`` is a byte-prefix match for
    ``.assay`` but is NOT beneath it as a path, and must NOT collide. Every
    row shares the SAME declared omission set, isolating the artifact
    spelling as the only variable.
    """
    lane = _r1_lane(
        coverage_artifact=artifact,
        omissions=_omission_policy("cmru/.assay", "topos/unsafe_absolute"),
    )

    if expect_collision:
        with pytest.raises(AssayError) as caught:
            runner._refuse_coverage_artifact_omission_collision(
                lane=lane, snapshot_policy=lane.isolation, project_prefix=PurePosixPath("cmru")
            )
        assert caught.value.outcome is Outcome.ERROR
        assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
        assert artifact in str(caught.value)
        assert "cmru/.assay" in str(caught.value)
    else:
        runner._refuse_coverage_artifact_omission_collision(
            lane=lane, snapshot_policy=lane.isolation, project_prefix=PurePosixPath("cmru")
        )


def test_a_later_non_colliding_omission_in_the_list_does_not_shadow_an_earlier_miss() -> None:
    """Multiple declared omissions: the loop must check EVERY one, not
    stop after the first non-match. Three omissions declared, only the
    LAST alphabetically collides -- so an implementation that only checked
    ``omissions[0]`` would wrongly pass.
    """
    lane = _r1_lane(
        coverage_artifact="zzz-danger/coverage.json",
        omissions=_omission_policy("cmru/aaa", "cmru/mmm", "cmru/zzz-danger"),
    )

    with pytest.raises(AssayError) as caught:
        runner._refuse_coverage_artifact_omission_collision(
            lane=lane, snapshot_policy=lane.isolation, project_prefix=PurePosixPath("cmru")
        )
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "cmru/zzz-danger" in str(caught.value)


# ---------------------------------------------------------------------------
# The wiring proof: the collision negative AND its must-succeed control, in
# the SAME test, sharing everything except the one field that differs --
# the report's own "a probe passed for the wrong reason" lesson: pairing a
# real, legitimately-declared unsafe link with a control that actually
# reaches process_runner is what proves the refusal has exactly ONE
# possible cause.
# ---------------------------------------------------------------------------


def _one_unsafe_link_repo(git_repo: GitRepo) -> tuple[str, str]:
    """One project (``cmru/``) with one real, committed, genuinely unsafe
    (absolute-target) symlink -- ``cmru/unsafe_link`` -- plus a real
    two-commit ``.zzz`` source diff so R1 has something to measure.
    """
    git_repo.write("cmru/.gitignore", "cov.json\n")
    git_repo.write("cmru/src/mod.zzz", "LINE1\n")
    os.symlink("/etc/passwd", git_repo.path / "cmru" / "unsafe_link")
    git_repo.git("add", "-A")
    git_repo.git("commit", "-q", "-m", "base with one real unsafe link")
    base_rev = git_repo.head()

    git_repo.write("cmru/src/mod.zzz", "LINE1\nLINE2\n")
    git_repo.git("add", "cmru/src/mod.zzz")
    git_repo.git("commit", "-q", "-m", "change source")
    head_rev = git_repo.head()
    return base_rev, head_rev


def _recording_process_runner(cov_target: PurePosixPath | None = None):
    calls: list[Path] = []

    def process_runner(argv, *, env, cwd, timeout):
        calls.append(Path(cwd))
        if cov_target is not None:
            # PROJECT-relative key (B006's own fix, WI-5's real finding): a
            # real coverage tool run with `cwd=cwd` (exactly what this
            # double's own `cwd` parameter already IS) never carries the
            # "cmru/" prefix `git diff`'s own repo-top-relative spelling
            # (A-145) uses -- `evaluate.py`'s own `_normalized_profile_files`
            # is what prepends that prefix, never this fixture.
            write_coverage_json(
                Path(cwd) / cov_target, {"src/mod.zzz": {"executed_lines": [1, 2]}}
            )
        return subprocess.CompletedProcess(list(argv), returncode=0, stdout="", stderr="")

    return process_runner, calls


def test_collision_refuses_before_any_process_runner_call_with_a_must_succeed_control(
    git_repo: GitRepo,
) -> None:
    """The collision negative and its must-succeed control. Both lanes
    declare the SAME single, genuinely unsafe, correctly declared symlink
    (``cmru/unsafe_link``) against the SAME real commit -- the only
    difference between them is the declared coverage artifact's own
    spelling. That isolates the refusal to exactly one possible cause: the
    negative's artifact is beneath the omission, the control's is not.
    """
    base_rev, head_rev = _one_unsafe_link_repo(git_repo)
    policy = _omission_policy("cmru/unsafe_link")

    # --- NEGATIVE: the declared artifact is BENEATH the declared omission
    negative_judge = make_r1_judge(
        source_root_paths=(git_repo.path / "cmru" / "src",),
        base=base_rev,
        coverage_artifact="unsafe_link/coverage.json",
    )
    negative_lane = make_lane(
        rigor=("R0", "R1"),
        judge=negative_judge,
        isolation=policy,
        argv=("/bin/sh", "-c", "exit 0"),
    )
    negative_runner, negative_calls = _recording_process_runner()

    verdict = runner.run_lane(
        negative_lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path / "cmru",
        adapter=FakeAdapter(),
        assay_version="0.1.0",
        process_runner=negative_runner,
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.claims[-1].reason_code is ReasonCode.BAD_LANE_CONFIG
    assert negative_calls == [], (
        "the collision must refuse strictly BEFORE the first "
        "prepared.materialize(), so process_runner must never be called "
        "at all -- this is the ONE observable that distinguishes a real "
        "pre-materialize refusal from a same-outcome refusal reached some "
        "other way"
    )

    # --- CONTROL: identical everything else; a non-colliding artifact ---
    control_judge = make_r1_judge(
        source_root_paths=(git_repo.path / "cmru" / "src",),
        base=base_rev,
        coverage_artifact="cov.json",
    )
    control_lane = make_lane(
        rigor=("R0", "R1"),
        judge=control_judge,
        isolation=policy,
        argv=("/bin/sh", "-c", "exit 0"),
    )
    control_runner, control_calls = _recording_process_runner(
        cov_target=PurePosixPath("cov.json")
    )

    control_verdict = runner.run_lane(
        control_lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path / "cmru",
        adapter=FakeAdapter(),
        assay_version="0.1.0",
        process_runner=control_runner,
    )

    assert control_verdict.outcome is Outcome.PASS, control_verdict
    assert len(control_calls) == 1, (
        "the control's coverage artifact does not collide, so its command "
        "genuinely runs -- proving the negative's refusal above really was "
        "caused by the collision alone, not by the fixture, the isolation "
        "policy, or R1 itself being broken"
    )
    # O2/A-186's own real-repository-untouched proof, unchanged by this check.
    assert git_repo.head() == head_rev
    assert git_repo.git("status", "--porcelain") == ""


# ---------------------------------------------------------------------------
# O3 -- the cross-unit runner proof: one immutable IsolationConfig governs
# the baseline, the one generated mutant, and BOTH canary halves.
# ---------------------------------------------------------------------------

_ROOT_FILE_A = "cmru.project.sample.toml"
_ROOT_FILE_B = "cmru.release.sh"
_ROOT_FILE_A_TEXT = "root dependency sample\n"
_ROOT_FILE_B_TEXT = "#!/bin/sh\necho release\n"
_TOPOS_ORDINARY_TEXT = "an ordinary, unrelated Topos file\n"

_ABSOLUTE_LEAF = "topos/unsafe_absolute"
_ESCAPE_LEAF = "topos/unsafe_escape"
_EMPTY_LEAF = "topos/unsafe_empty"
_OMITTED_LEAVES: tuple[str, ...] = tuple(
    sorted((_ABSOLUTE_LEAF, _ESCAPE_LEAF, _EMPTY_LEAF), key=lambda p: p.encode("utf-8"))
)
#: SHA-1 of the empty blob (``git hash-object --stdin < /dev/null``) --
#: a fixed, well-known Git constant, not derived at test time.
_EMPTY_BLOB_OID = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"

#: The always-present original source's own line count -- everything past
#: this, in ANY unit's current ``pkg/mod.py``, only exists because the
#: canary's own ``inject_uncovered_line`` appended it.
_ORIGINAL_LINE_COUNT = 2


def _seed_cross_unit_repo(git_repo: GitRepo) -> tuple[str, str]:
    """The M3/M8-shaped fixture, scaled down to a hermetic repo: a ``cmru``
    project, two real repo-TOP dependencies outside it (mirroring
    ``cmru.project.sample.toml``/``cmru.release.sh``), a sibling ``topos``
    project holding the three declarable unsafe leaves plus one ordinary
    file, and a real one-line compare-swap diff inside ``cmru/pkg/mod.py``.
    """
    git_repo.write(_ROOT_FILE_A, _ROOT_FILE_A_TEXT)
    git_repo.write(_ROOT_FILE_B, _ROOT_FILE_B_TEXT)
    git_repo.write("topos/ordinary.txt", _TOPOS_ORDINARY_TEXT)
    git_repo.write("cmru/.gitignore", "cov.json\n")
    git_repo.write("cmru/pkg/mod.py", "def f(x):\n    return 0\n")
    git_repo.write("cmru/tests/test_mod.py", "# placeholder -- never executed by this test\n")
    os.symlink("/etc/passwd", git_repo.path / _ABSOLUTE_LEAF)
    os.symlink("../../../etc/shadow", git_repo.path / _ESCAPE_LEAF)
    git_repo.git("add", "-A")

    empty_seed = git_repo.path / "_empty_blob_seed"
    empty_seed.write_bytes(b"")
    oid = git_repo.git("hash-object", "-w", "-t", "blob", str(empty_seed)).strip()
    empty_seed.unlink()
    assert oid == _EMPTY_BLOB_OID
    git_repo.git("update-index", "--add", "--cacheinfo", f"120000,{oid},{_EMPTY_LEAF}")
    # `_EMPTY_LEAF` is an INDEX-only entry -- no real symlink syscall can
    # produce an empty target (`os.symlink("", path)` raises ENOENT), which
    # is exactly why WI-2's own fixture built it via raw `--cacheinfo`. That
    # otherwise makes THIS repository (the CONSUMER's own, not any private
    # snapshot) look permanently dirty to a plain `git status`, which would
    # trip `run_lane`'s OWN pre-snapshot consumer-cleanliness guard before
    # any of this test's own subject even runs. A source-repo `--skip-
    # worktree` bit (unrelated to, and independent from, the skip-worktree
    # bits P22 sets inside its own PRIVATE snapshot below) is the standard
    # git answer to "this tracked path has no worktree file, on purpose".
    git_repo.git("update-index", "--skip-worktree", "--", _EMPTY_LEAF)
    git_repo.git("commit", "-q", "-m", "cross-unit base")
    base_rev = git_repo.head()

    git_repo.write("cmru/pkg/mod.py", "def f(x):\n    return x > 0\n")
    # A TARGETED add, deliberately NOT `-A`: `_EMPTY_LEAF` is an index-only
    # entry with no working-tree file, so a blanket `-A` here would read its
    # absence from the working tree as a deletion and stage it away --
    # exactly the trap test_isolation_unsafe_symlink_omissions.py's own
    # `_repo_with_undeclared_unsafe_link` documents.
    git_repo.git("add", "cmru/pkg/mod.py")
    git_repo.git("commit", "-q", "-m", "introduce a compare-swap site")
    head_rev = git_repo.head()
    return base_rev, head_rev


def _parse_skip_worktree(raw: bytes) -> frozenset[bytes]:
    """The same NUL-delimited, uppercase-``S``-only parse
    ``test_isolation_unsafe_symlink_omissions.py`` uses, independently
    reimplemented here rather than imported -- an oracle that imports the
    thing it checks cannot catch a bug shared between them."""
    skipped: set[bytes] = set()
    for record in raw.split(b"\x00"):
        if record[:2] == b"S ":
            skipped.add(record[2:])
    return frozenset(skipped)


def _assert_policy_holds_live(cwd: Path) -> None:
    """Every invariant O3 requires, checked against the snapshot's REAL,
    currently-open repository root (``cwd``'s own parent, since
    ``project_prefix`` here is the single component ``cmru``) -- called
    from INSIDE the process_runner double, i.e. while the snapshot is
    still live, never after its context has closed.
    """
    repo_root = cwd.parent
    for leaf in _OMITTED_LEAVES:
        assert not os.path.lexists(repo_root / leaf), f"{leaf} must be absent"
    assert (repo_root / _ROOT_FILE_A).read_text(encoding="utf-8") == _ROOT_FILE_A_TEXT
    assert (repo_root / _ROOT_FILE_B).read_text(encoding="utf-8") == _ROOT_FILE_B_TEXT
    assert (
        repo_root / "topos" / "ordinary.txt"
    ).read_text(encoding="utf-8") == _TOPOS_ORDINARY_TEXT

    status = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain=v1"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert status == "", f"expected a clean tree, got {status!r}"

    raw = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-v", "-z"],
        capture_output=True, check=True,
    ).stdout
    skip = _parse_skip_worktree(raw)
    assert skip == {leaf.encode("utf-8") for leaf in _OMITTED_LEAVES}


def _write_fake_coverage(project_root: Path, text: str) -> None:
    """A coverage.py-JSON document derived from *text*'s OWN current
    content: the first ``_ORIGINAL_LINE_COUNT`` lines (always present,
    already-tested code) are executed; any blank line beyond that is
    skipped (coverage.py never reports a blank line either); any ``def``
    line beyond that is executed (a module-level function's own ``def``
    line runs at import time); anything else beyond that (the canary's own
    injected function BODY) is genuinely missing -- mirroring
    :func:`assay.adapters.python._inject_uncovered_line`'s own documented
    "the def line is reached, the two body statements are executed by no
    test" fact, rather than an arbitrary line number.
    """
    executed: list[int] = []
    missing: list[int] = []
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if i <= _ORIGINAL_LINE_COUNT:
            executed.append(i)
        elif not stripped:
            continue
        elif stripped.startswith("def "):
            executed.append(i)
        else:
            missing.append(i)
    # PROJECT-relative key (B006's own fix, WI-5's real finding): a real
    # coverage tool always runs with `cwd = project_root`
    # (`assay.runner.evaluate_r1`), so its own raw JSON key never carries the
    # `cmru/` prefix `git diff`'s repo-top-relative spelling (A-145) uses --
    # `assay.evaluate._normalized_profile_files` is what reconciles the two,
    # by prepending the project's own repo-relative prefix, never by this
    # fixture pre-applying it (the exact gap WI-5's real, subprocess-driven
    # run exposed: every earlier project_prefix test fabricated this key
    # already repo-top-relative, which is not what the real tool emits).
    write_coverage_json(
        project_root / "cov.json",
        {"pkg/mod.py": {"executed_lines": executed, "missing_lines": missing}},
    )


def _make_cross_unit_process_runner():
    calls: list[Path] = []

    def process_runner(argv, *, env, cwd, timeout):
        cwd_path = Path(cwd)
        _assert_policy_holds_live(cwd_path)
        calls.append(cwd_path)
        text = (cwd_path / "pkg" / "mod.py").read_text(encoding="utf-8")
        _write_fake_coverage(cwd_path, text)
        returncode = 1 if ">=" in text else 0
        return subprocess.CompletedProcess(list(argv), returncode=returncode, stdout="", stderr="")

    return process_runner, calls


def test_live_command_observes_exact_policy_in_every_unit(git_repo: GitRepo) -> None:
    """O3's own acceptance node. R0+R1+R2+R3 all declared together (the
    real WI-5/CMRU shape): the baseline (R0+R1), the ONE generated
    ``python:compare-swap`` mutant (R2), and BOTH the canary control and
    transformed halves (R3, ``uncovered-line`` -- the one mechanism that
    also exercises R1 a second and third time) -- FOUR live process_runner
    invocations in total, each independently proving the identical
    omission set held.
    """
    base_rev, head_rev = _seed_cross_unit_repo(git_repo)
    policy = _omission_policy(*_OMITTED_LEAVES)
    judge = JudgeConfig(
        language="python",
        source_roots=("pkg",),
        source_root_paths=(git_repo.path / "cmru" / "pkg",),
        fail_under=100.0,
        allow_excluded=False,
        coverage=CoverageConfig(format="coverage-py-json", artifact="cov.json"),
        mutation=MutationConfig(jobs=1, max_mutants=50, operators=("python:compare-swap",)),
        canary=CanaryConfig(mechanism="uncovered-line", target="pkg/mod.py"),
        base=base_rev,
    )
    lane = make_lane(
        rigor=("R0", "R1", "R2", "R3"),
        judge=judge,
        isolation=policy,
        argv=("noop",),
    )
    process_runner, calls = _make_cross_unit_process_runner()

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path / "cmru",
        adapter=PythonAdapter(),
        assay_version="0.1.0",
        process_runner=process_runner,
    )

    # Every declared unit really ran through the real process_runner double
    # -- baseline, the one mutant, and both canary halves -- and every one
    # of those four calls independently survived `_assert_policy_holds_live`
    # above (a failed assertion there would have raised INSIDE the run and
    # this test would already have failed with THAT traceback).
    assert len(calls) == 4, calls

    claims_by_rigor = {claim.rigor: claim for claim in verdict.claims}
    assert claims_by_rigor["R0"].status is Outcome.PASS
    assert claims_by_rigor["R1"].status is Outcome.PASS
    r2 = claims_by_rigor["R2"]
    assert r2.status is Outcome.PASS
    assert r2.mutation is not None
    assert r2.mutation.total == 1
    assert len(r2.mutation.killed) == 1
    r3 = claims_by_rigor["R3"]
    assert r3.status is Outcome.PASS
    assert r3.canary is not None
    assert r3.canary.attempts[0].control_outcome is Outcome.PASS
    assert r3.canary.attempts[0].transformed_outcome is Outcome.FAIL
    assert r3.canary.attempts[0].expected_reason_code is ReasonCode.UNCOVERED_LINES
    assert r3.canary.attempts[0].observed_reason_code is ReasonCode.UNCOVERED_LINES

    # The consumer's OWN repository -- never touched by any of this.
    assert git_repo.head() == head_rev
    assert git_repo.git("status", "--porcelain") == ""
    # The two REAL on-disk leaves are unaffected in the consumer's own
    # working tree -- an omission only ever applies to a disjoint private
    # snapshot. `_EMPTY_LEAF` never had a consumer-side working-tree file
    # to begin with (it is index-only, by construction, in BOTH the
    # consumer repository and every prepared snapshot alike), so its own
    # "still there, unaffected" proof is that its blob remains reachable.
    for leaf in (_ABSOLUTE_LEAF, _ESCAPE_LEAF):
        assert os.path.lexists(git_repo.path / leaf), (
            f"the consumer's own {leaf} must be unaffected by an omission "
            f"that only ever applies to a disjoint private snapshot"
        )
    assert git_repo.git("show", f"HEAD:{_EMPTY_LEAF}") == ""


# ---------------------------------------------------------------------------
# O5 -- the dstdns incident shape (§7, M10): a vendored container artifact
# symlinked at its EXACT real repository path,
# `infra-global/reverse-proxy/etc-nginx/modules -> /usr/lib/nginx/modules`,
# with no relationship to any Python source root. Before omission mode
# existed, this link blocked dstdns's entire coverage effort on day one, and
# the only fix available was to stop tracking a real artifact to satisfy the
# tool. This module -- not the P22 mechanism module -- is where this belongs:
# what O5 proves is runner-shaped ("a lane judging its own code is no longer
# hostage to a vendored artifact somewhere else"), the same wiring-level
# claim `test_live_command_observes_exact_policy_in_every_unit` above proves
# for the CMRU shape. A generic `link -> /etc/passwd` would not witness this
# incident; the exact spelling is what lets a dstdns reader recognise their
# own case (a fourth undeclared-unsafe-link negative already exists in
# `test_isolation_unsafe_symlink_omissions.py`, so this test does not repeat
# it).
# ---------------------------------------------------------------------------

_DSTDNS_LINK = "infra-global/reverse-proxy/etc-nginx/modules"
_DSTDNS_LINK_TARGET = "/usr/lib/nginx/modules"
_DSTDNS_SIBLING = "infra-global/reverse-proxy/etc-nginx/nginx.conf"
_DSTDNS_SIBLING_TEXT = "user www-data;\n"
_DSTDNS_PROJECT_FILE_TEXT = "print('unrelated project command ran')\n"


def _dstdns_shaped_repo(git_repo: GitRepo) -> str:
    """dstdns's own measured incident (M10), scaled into a hermetic repo:
    the exact real vendored-artifact symlink, one ordinary sibling file
    beneath the SAME ``infra-global/`` tree, and a small, genuinely
    unrelated ``project/`` this test's own command actually runs against.
    """
    git_repo.write(_DSTDNS_SIBLING, _DSTDNS_SIBLING_TEXT)
    os.symlink(_DSTDNS_LINK_TARGET, git_repo.path / _DSTDNS_LINK)
    git_repo.write("project/app.py", _DSTDNS_PROJECT_FILE_TEXT)
    git_repo.git("add", "-A")
    git_repo.git("commit", "-q", "-m", "dstdns-shaped fixture: vendored nginx modules symlink")
    return git_repo.head()


def _dstdns_spec(repo: Path, scratch: Path, commit: str, policy: IsolationConfig) -> SnapshotSpec:
    """The ONE :class:`SnapshotSpec` shape shared by both the negative and
    the control below -- ``project_prefix`` is fixed at ``"project"`` for
    BOTH (P22's structural walk covers the whole commit regardless of
    ``project_prefix``; see ``isolation.py``'s own manifest builder, which
    only consults it afterwards to check the prefix names a real tree), so
    the declared *policy* is the only variable that differs between them.
    """
    return SnapshotSpec(
        repo_top=repo.resolve(),
        commit=commit,
        project_prefix=PurePosixPath("project"),
        scratch_root=scratch.resolve(),
        snapshot_policy=policy,
        limits=DEFAULT_SNAPSHOT_LIMITS,
    )


def test_dstdns_nginx_link_is_an_exact_omittable_leaf(
    git_repo: GitRepo, tmp_path: Path
) -> None:
    """O5's own acceptance node (§7). Repository mode still refuses the
    whole tree -- the state dstdns was ACTUALLY in -- naming the exact
    link. Omission mode, declaring ONLY that one path, runs a real command
    against a small, genuinely unrelated ``project/`` elsewhere in the same
    fixture repo, with the link absent (checked by ``lstat``, never
    inferred from the run having proceeded), the ordinary
    ``infra-global/`` sibling untouched -- the half that matters most,
    since it is what distinguishes an omission from deleting the vendored
    directory outright, which is exactly what dstdns actually lost -- and a
    clean ``git status``. The negative and the control share EVERYTHING
    (the same commit, the same ``project_prefix``, the same scratch
    parent) except the one declared policy, so a repository-mode refusal
    here cannot be blamed on anything else in the fixture.
    """
    commit = _dstdns_shaped_repo(git_repo)

    # --- NEGATIVE: repository mode refuses, naming the exact link. ---
    repo_scratch = tmp_path / "repo-mode"
    repo_scratch.mkdir()
    with pytest.raises(AssayError) as caught:
        with prepare_snapshot(
            _dstdns_spec(git_repo.path, repo_scratch, commit, _repository_policy()),
            timeout=60.0,
        ):
            pytest.fail("repository mode must refuse dstdns's own vendored symlink")
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.GIT_FAILED
    assert _DSTDNS_LINK in str(caught.value)
    assert list(repo_scratch.iterdir()) == []

    # --- CONTROL: omission mode, declaring that ONE path, genuinely runs a
    # command against the unrelated `project/` directory inside the
    # private snapshot. ---
    omission_scratch = tmp_path / "omission-mode"
    omission_scratch.mkdir()
    policy = _omission_policy(_DSTDNS_LINK)
    with prepare_snapshot(
        _dstdns_spec(git_repo.path, omission_scratch, commit, policy), timeout=60.0
    ) as prepared:
        with prepared.materialize(timeout=60.0) as snapshot:
            root = snapshot.root

            # The link is absent, proven directly by `lstat` -- independent
            # of whether the command below runs at all.
            assert not os.path.lexists(root / _DSTDNS_LINK), _DSTDNS_LINK

            # The ordinary `infra-global/` sibling survives untouched --
            # the exact thing dstdns lost by untracking the whole artifact
            # instead of just the one unsafe leaf.
            assert (
                root / _DSTDNS_SIBLING
            ).read_bytes() == _DSTDNS_SIBLING_TEXT.encode("utf-8")

            status = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain=v1"],
                capture_output=True, text=True, check=True,
            ).stdout
            assert status == "", f"expected a clean tree, got {status!r}"

            # The command genuinely runs, entirely unrelated to
            # `infra-global/`: a lane judging its own project is no longer
            # hostage to a vendored artifact somewhere else in the tree.
            result = subprocess.run(
                [sys.executable, "app.py"],
                cwd=str(snapshot.project_root),
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout == "unrelated project command ran\n"

        assert not snapshot.root.exists()
    assert list(omission_scratch.iterdir()) == []

    # The consumer's OWN repository -- never touched by any of this: the
    # real vendored link is still exactly where it was.
    assert os.path.lexists(git_repo.path / _DSTDNS_LINK)
    assert os.readlink(git_repo.path / _DSTDNS_LINK) == _DSTDNS_LINK_TARGET


# ---------------------------------------------------------------------------
# §6 WI-3's own embargo, half (b), asserted mechanically against the REAL
# enclosing repository (never a fixture). The hazard it exists to prevent:
# a verdict that reports omission-mode evidence with no v6 `snapshot_policy`
# record to attest which policy it used. Half (a) -- no live checked-in lane
# may declare omission mode -- was retired in the same commit that landed
# WI-4's v6 record; see the module docstring.
#
# (A-278) REPHRASED IN WAVE 2, because the original phrasing could not
# survive its own success. It read "no assay release may be cut between
# WI-1 landing lane schema v2 and this branch merging with a green gate"
# and was implemented as an unconditional ban on any `assay-v*` tag
# descending from WI-1's landing commit -- with nothing that ever closed
# the window. Wave 1 then merged green and `assay-v2.0.0` was cut FROM that
# merge, carrying lane v2 and the v6 record together, which is precisely
# the release the embargo was protecting. The test failed anyway, and it
# could not have failed any earlier: no `assay-v*` tag existed while wave 1
# was gating, so the assertion was unreachable in the only window anyone
# ran it.
#
# What is asserted now is the PROPERTY rather than the proxy: every release
# tag that descends from WI-1's landing commit must itself carry the v6
# `snapshot_policy` record. A release cut inside the real window still
# fails; the release that discharged the embargo passes; and the check
# stays live for every future tag instead of becoming a line someone has to
# delete.
# ---------------------------------------------------------------------------

#: The monorepo top -- one level above assay's own project root, matching
#: `test_distribution_build_release.py`'s own established `REPO_ROOT`
#: convention -- where `cmru/assay.toml` and every other project's lane
#: file live alongside assay's own.
_REPO_ROOT = PROJECT_ROOT.parent

#: WI-1's own landing commit, exactly as this work item's own written brief
#: names it: the point after which lane schema v2 (and therefore omission
#: mode's own config surface) exists at all, and the earliest possible
#: commit any real release could have shipped it from.
_WI1_LANDING_COMMIT = "c56a13ea"


def _repo_git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


#: The path, inside the repository, of the verdict schema that carries
#: WI-4's record. Read out of a COMMIT via `git show`, never off the
#: working tree, so the question asked is "what did that release actually
#: ship" rather than "what is checked out right now".
_SCHEMA_IN_REPO = "assay/src/assay/schemas/verdict.schema.json"

#: The record whose absence is the hazard. Deliberately the field NAME and
#: not a schema version: a v7 that keeps the record still discharges the
#: embargo, and pinning "6" here would turn the next migration red for no
#: reason connected to what this test protects.
_WI4_POLICY_RECORD = "snapshot_policy"


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
    ).returncode == 0


def _carries_wi4_policy_record(ref: str) -> bool:
    """Did the tree at `ref` ship WI-4's v6 `snapshot_policy` record?

    A `git show` that fails yields empty stdout and therefore False, which
    is the fail-CLOSED direction: an unreadable release reads as one that
    did not carry the record, so the audit goes red rather than silently
    passing a tag it could not inspect. Deliberately not a separate
    returncode branch -- that branch would be unreachable from any real
    repository state and this project forbids checks that cannot fire.
    """
    shown = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "show", f"{ref}:{_SCHEMA_IN_REPO}"],
        capture_output=True, text=True,
    )
    return _WI4_POLICY_RECORD in shown.stdout


def test_every_release_since_wi1_landed_carries_wi4s_policy_record() -> None:
    """Embargo half (b), as the property rather than the proxy (A-278).

    Any ``assay-v*`` tag that descends from WI-1's landing commit ships
    lane schema v2, and therefore omission mode's config surface. Such a
    release MUST also ship the v6 ``snapshot_policy`` record, or a verdict
    it produced could report omission-mode evidence with nothing to attest
    which policy produced it. Checked against real tagged history, never a
    hardcoded "as of today" assertion: a release cut tomorrow without the
    record fails this test the next time it runs.
    """
    # Sanity precondition: if this ever stopped being true, the ancestry
    # check below would be vacuous (an unreachable commit is never anyone's
    # ancestor), so this asserts the embargo commit itself is still real,
    # reachable history rather than letting that failure mode pass silently.
    assert _is_ancestor(_WI1_LANDING_COMMIT, "HEAD"), (
        f"{_WI1_LANDING_COMMIT!r} is not an ancestor of HEAD -- the embargo "
        f"commit itself is not the reachable history this audit assumes"
    )

    tags = [t for t in _repo_git("tag", "--list", "assay-v*").splitlines() if t]
    assert tags, "expected at least one real, tagged assay release to check against"
    # Computed as an unconditional map and then combined, rather than as a
    # filtered comprehension: every `assay-v*` tag that exists today is
    # in-window, so a filter's skip arc would be unreachable, and an
    # unreachable arc in a project at 100% branch coverage is a check that
    # cannot fire dressed up as thoroughness.
    descends = [_is_ancestor(_WI1_LANDING_COMMIT, tag) for tag in tags]
    assert any(descends), (
        "no assay release tag descends from WI-1's landing commit, so this "
        "audit examined nothing -- the embargo's subject has disappeared"
    )
    for tag, in_window in zip(tags, descends):
        assert not in_window or _carries_wi4_policy_record(tag), (
            f"release tag {tag!r} descends from WI-1's own landing commit "
            f"{_WI1_LANDING_COMMIT!r} -- so it ships lane schema v2 and "
            f"omission mode's config surface -- but its {_SCHEMA_IN_REPO} "
            f"carries no {_WI4_POLICY_RECORD!r} record, which is exactly "
            f"the state §6 WI-3's embargo forbids"
        )


def test_wi1s_own_landing_commit_is_the_state_the_embargo_forbids() -> None:
    """The must-fail control for the audit above, run through the IDENTICAL
    reader (A-278). WI-1's landing commit is the real, reachable commit
    that is in the hazardous state by construction: it already carries
    ``LANE_SCHEMA_VERSION = 2`` and does NOT yet carry WI-4's record. If a
    release had been cut there, the assertion above would have caught it --
    which is what makes the passing case above evidence rather than a
    tautology satisfied by any input.
    """
    assert not _carries_wi4_policy_record(_WI1_LANDING_COMMIT)
    assert "LANE_SCHEMA_VERSION = 2" in _repo_git(
        "show", f"{_WI1_LANDING_COMMIT}:assay/src/assay/config.py"
    )
