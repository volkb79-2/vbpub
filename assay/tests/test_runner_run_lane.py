"""``runner.run_lane`` — the real R0+R1 pipeline (P17), at the unit level:
real git state materialised under ``tmp_path`` (the established P02
pattern), real shell commands that actually write the coverage artifact
they declare (never a pre-seeded fake standing in for one), and an injected
clock/process-runner boundary only where AUTHORING.md §3b.A requires it.

O1's installed-wheel proof lives in ``test_standalone.py``; O1's CLI-wiring
proof (registry dispatch, argument parsing) lives in ``test_cli_run.py``;
this module is where O2 (single invocation, base resolves once, timing),
O3 (stale-artifact discipline) and O4 (the six terminal shapes) are proven
against :func:`assay.runner.run_lane` directly, so a failure here always
points at the real pipeline function itself, never at CLI or packaging
wiring around it.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import FakeAdapter, GitRepo, fixed_clock, make_lane, make_r1_judge

from assay import git as git_module
from assay import runner, safeio
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import Judgment

ADAPTER = FakeAdapter()

MOMENT_A = datetime(2026, 8, 8, 9, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 8, 8, 9, 0, 1, tzinfo=timezone.utc)
MOMENT_C = datetime(2026, 8, 8, 9, 0, 2, tzinfo=timezone.utc)


def _seed_two_commits(repo: GitRepo) -> tuple[str, str]:
    # A-140: the declared coverage artifact is this run's own OUTPUT, so a
    # real consumer must git-ignore it -- otherwise it is untracked
    # worktree state and `run_lane`'s whole-tree cleanliness guard refuses
    # the run BEFORE removing anything (which is the point: the guard now
    # judges the tree as it FOUND it, never one this function pre-cleaned).
    # Committed in the BASE commit, so the base..head diff is unchanged.
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = repo.commit_all("add pkg base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\nLINE4\nLINE5\n")
    head_rev = repo.commit_all("add pkg head")
    return base_rev, head_rev


def _cov_json(files: dict) -> str:
    """A real coverage.py-JSON document -- the parser requires all three
    line buckets present on every record, even when empty (unlike some
    other fixtures' helpers, which fill them in for you)."""
    complete = {
        key: {
            "executed_lines": list(record.get("executed_lines", [])),
            "missing_lines": list(record.get("missing_lines", [])),
            "excluded_lines": list(record.get("excluded_lines", [])),
        }
        for key, record in files.items()
    }
    return json.dumps({"files": complete})


def _write_cov_argv(files: dict) -> tuple[str, ...]:
    """A REAL shell command whose own execution writes ``cov.json`` -- never
    pre-seeded, so a PASS proves the artifact came from THIS run (O3)."""
    payload = _cov_json(files)
    script = f"cat > cov.json <<'EOF'\n{payload}\nEOF"
    return ("/bin/sh", "-c", script)


# --- a full real PASS: R0 and R1 both, judgment.r1 present --------------------


def test_run_lane_full_pass_builds_judgment_and_covers_ended_after_r1(
    git_repo: GitRepo,
):
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=_write_cov_argv({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}),
    )
    clock = fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C)

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=clock,
    )

    assert verdict.outcome is Outcome.PASS
    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].status is Outcome.PASS
    assert verdict.claims[1].status is Outcome.PASS
    assert verdict.claims[1].coverage.pct == 100.0
    assert verdict.started == "2026-08-08T09:00:00+00:00", "R0's own start, unchanged"
    assert verdict.ended == "2026-08-08T09:00:02+00:00", "covers R1's own completion"
    assert isinstance(verdict.judgment, Judgment)
    # P33/V5-1: the three lane facts now live on `judgment.resolved`.
    assert verdict.judgment.resolved.base == base_rev
    # make_r1_judge's own .source_roots is a stringified copy of the SAME
    # absolute source_root_paths (conftest.py's own documented shortcut),
    # so this assertion cannot by itself distinguish "read judge.source_roots"
    # from "stringify judge.source_root_paths" -- that discriminating
    # assertion lives in test_cli_run.py, against a REAL assay.toml load,
    # where the two genuinely differ.
    assert verdict.judgment.resolved.source_roots == judge.source_roots
    assert verdict.judgment.resolved.language == judge.language


def test_run_lane_r0_only_never_builds_judgment_and_ended_is_r0s_own(
    git_repo: GitRepo,
):
    lane = make_lane(rigor=("R0",), judge=None, argv=("/bin/sh", "-c", "exit 0"))
    clock = fixed_clock(MOMENT_A, MOMENT_B)

    verdict = runner.run_lane(
        lane,
        commit="d" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="0.1.0",
        clock=clock,
    )

    assert [c.rigor for c in verdict.claims] == ["R0"]
    assert verdict.judgment is None
    assert verdict.started == "2026-08-08T09:00:00+00:00"
    assert verdict.ended == "2026-08-08T09:00:01+00:00"


# --- B006(a)/A-269 WI-2: the R0/R1+ isolation conditional, re-enforced at --
# the runner boundary (`_snapshot_policy_for_lane`) for a directly
# constructed `Lane` -- `config.py`'s loader already enforces the identical
# rule for every TOML-sourced lane, so these two shapes are reachable only
# through the public `Lane` API, never through a loaded `assay.toml`.


def test_run_lane_refuses_a_higher_rigor_lane_with_no_isolation_policy(
    git_repo: GitRepo,
):
    """A directly constructed R1+ `Lane` with `isolation=None` is refused
    before any git work -- checked BEFORE `run_lane` even chooses between
    the direct R0-only path and `_run_higher_rigor_lane`.
    """
    lane = make_lane(rigor=("R0", "R1"), judge=None, isolation=None)

    with pytest.raises(AssayError) as caught:
        runner.run_lane(
            lane,
            commit="d" * 40,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=None,
            assay_version="0.1.0",
        )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "isolation is None" in str(caught.value)

    # CONTROL: the identical rigor with an explicit policy is NOT refused by
    # this check -- it proceeds far enough to hit the next one instead (a
    # commit that does not match `git_repo`'s real HEAD), proving the
    # isolation conditional itself, and not some other refusal, is what
    # fired above.
    from assay.config import IsolationConfig

    ok_lane = make_lane(
        rigor=("R0", "R1"),
        judge=None,
        isolation=IsolationConfig(
            snapshot_selection="repository", unsafe_symlink_omissions=()
        ),
    )
    verdict = runner.run_lane(
        ok_lane,
        commit="d" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="0.1.0",
    )
    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.HEAD_CHANGED


def test_run_lane_refuses_an_r0_only_lane_that_declares_an_isolation_policy(
    git_repo: GitRepo,
):
    """A directly constructed R0-only `Lane` that ALSO declares an
    isolation policy is refused: an R0-only lane runs no snapshot at all,
    so a declared policy for one can never be honoured.
    """
    from assay.config import IsolationConfig

    policy = IsolationConfig(
        snapshot_selection="repository", unsafe_symlink_omissions=()
    )
    lane = make_lane(rigor=("R0",), judge=None, isolation=policy)

    with pytest.raises(AssayError) as caught:
        runner.run_lane(
            lane,
            commit="d" * 40,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=None,
            assay_version="0.1.0",
        )
    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "R0-only" in str(caught.value)

    # CONTROL: the identical rigor with no isolation policy at all is not
    # refused by this check and runs to completion, exactly like
    # `test_run_lane_r0_only_never_builds_judgment_and_ended_is_r0s_own`
    # above.
    ok_lane = make_lane(
        rigor=("R0",), judge=None, isolation=None, argv=("/bin/sh", "-c", "exit 0")
    )
    verdict = runner.run_lane(
        ok_lane,
        commit="d" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="0.1.0",
    )
    assert [c.rigor for c in verdict.claims] == ["R0"]


# --- O2: the lane command runs exactly once ------------------------------------


def test_run_lane_invokes_the_process_runner_exactly_once(git_repo: GitRepo):
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=_write_cov_argv({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}),
    )
    calls: list[tuple[str, ...]] = []

    def counting_runner(argv, **kwargs):
        calls.append(tuple(argv))
        return runner.default_process_runner(argv, **kwargs)

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        process_runner=counting_runner,
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.outcome is Outcome.PASS
    assert len(calls) == 1, "the invocation ledger: R1 must never re-run the lane's command"


# --- O3: stale-artifact discipline ---------------------------------------------


def test_run_lane_removes_a_stale_artifact_before_running_never_reuses_it(
    git_repo: GitRepo,
):
    """The pre-existing artifact reports a PASS-shaped 100%; the REAL run's
    command exits 0 without rewriting it. If the stale artifact leaked into
    R1's judgment, it would wrongly render the stale PASS -- O3's own
    negative.

    P23: the mechanism is no longer "assay removes it before running" -- every
    unit now starts inside an independent P22 snapshot materialised from the
    COMMIT alone, so an ignored, uncommitted artifact on the CONSUMER's own
    disk is never even copied there in the first place (A-189's "each unit
    starts artifact-absent even when the consumer has a stale ignored
    artifact"). The consumer's own stale copy being provably UNTOUCHED is the
    stronger invariant P23 replaces "removed before running" with."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    stale = _cov_json({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}})
    git_repo.write("cov.json", stale)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    # the consumer's own stale copy is untouched -- assay never reads from or
    # writes to the consumer's real tree under the higher-rigor path at all.
    assert (git_repo.path / "cov.json").read_text(encoding="utf-8") == stale
    assert verdict.claims[1].status is Outcome.NO_MEASUREMENT
    assert verdict.claims[1].reason_code is ReasonCode.EMPTY_COVERAGE
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None


def test_run_lane_a_freshly_written_artifact_is_read_for_real(git_repo: GitRepo):
    """The mirror of the previous test: a stale consumer-side copy is never
    consulted, and a GENUINE fresh artifact -- written by the command itself,
    inside its own snapshot -- is read and judged normally."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    stale = _cov_json({"pkg/mod.zzz": {"executed_lines": []}})
    git_repo.write("cov.json", stale)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=_write_cov_argv({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.outcome is Outcome.PASS
    assert verdict.claims[1].coverage.pct == 100.0
    assert (git_repo.path / "cov.json").read_text(encoding="utf-8") == stale


# --- whole-worktree cleanliness: refuses BEFORE the command runs (sol finding 6)


def test_run_lane_refuses_a_dirty_tree_for_an_r0_only_lane_before_running(
    git_repo: GitRepo, tmp_path: Path
):
    marker = tmp_path / "RAN"
    lane = make_lane(rigor=("R0",), judge=None, argv=("/bin/sh", "-c", f"touch {marker}"))
    git_repo.write("uncommitted.txt", "dirty\n")

    verdict = runner.run_lane(
        lane,
        commit="2" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.DIRTY_TREE
    assert [c.rigor for c in verdict.claims] == ["R0"]
    assert verdict.claims[0].status is Outcome.NO_MEASUREMENT
    assert not marker.exists(), "the command must never have started"


def test_run_lane_refuses_a_dirty_tree_for_an_r1_lane_on_both_claims(
    git_repo: GitRepo, tmp_path: Path
):
    marker = tmp_path / "RAN"
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))
    git_repo.write("uncommitted.txt", "dirty\n")

    verdict = runner.run_lane(
        lane,
        commit="3" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].reason_code is ReasonCode.DIRTY_TREE
    assert verdict.claims[1].reason_code is ReasonCode.DIRTY_TREE
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None
    assert not marker.exists()


def test_run_lane_dirtiness_outside_source_roots_still_refuses_the_r1_lane(
    git_repo: GitRepo, tmp_path: Path
):
    """The whole-tree check (sol finding 6) is strictly WIDER than the
    source-root-scoped one `evaluate_r1` already runs post-execution: a
    dirty file outside `judge.source_roots` entirely (an uncommitted
    `assay.toml` edit is the real-world case) still refuses the run."""
    marker = tmp_path / "RAN"
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))
    git_repo.write("outside_pkg.txt", "not under pkg/ at all\n")

    verdict = runner.run_lane(
        lane,
        commit="4" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.DIRTY_TREE
    assert not marker.exists()


def test_run_lane_refuses_a_higher_rigor_lane_whose_caller_commit_is_stale(
    git_repo: GitRepo, tmp_path: Path
):
    """The higher-rigor state machine's own step 1.2 (the handoff's terminal
    table row 3): a CLEAN tree whose caller-supplied *commit* no longer names
    the repository's real HEAD is refused before P22/the executor/the process
    ever starts -- distinct from the dirty-tree case above, which fires first
    whenever both are true (dirt keeps precedence). Without this guard P22
    would silently prepare a snapshot of the WRONG commit and the artifact
    would still claim the caller's stale one.
    """
    marker = tmp_path / "RAN"
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))

    verdict = runner.run_lane(
        lane,
        commit="5" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].reason_code is ReasonCode.HEAD_CHANGED
    assert verdict.claims[1].reason_code is ReasonCode.HEAD_CHANGED
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None
    assert not marker.exists(), "the command must never have started"
    assert json.loads(verdict.to_json())["commit"] == "5" * 40


# --- unsafe coverage artifact path: refused BEFORE the command runs -----------


def test_run_lane_refuses_a_symlinked_artifact_path(git_repo: GitRepo, tmp_path: Path):
    """P20: the reservation itself (`safeio.reserve_output`) refuses a
    symlinked destination at construction time, before any other refusal --
    so this now renders the reservation's own vocabulary,
    ERROR/UNREADABLE_ARTIFACT, rather than a bespoke BAD_LANE_CONFIG.

    P23: the symlink must be COMMITTED (force-added despite its own
    ``.gitignore`` entry, mirroring ``test_run_lane_refuses_a_tracked_
    artifact_path``'s own established pattern) rather than left on the live
    tree -- a unit only ever sees what its own P22 snapshot materialised
    from the resolved commit, so an untracked symlink on the consumer's own
    disk would simply be absent inside it, proving nothing. The target is a
    RELATIVE, lexically-contained path (A-186): an absolute/escaping symlink
    would make the whole snapshot refuse with GIT_FAILED instead of
    exercising this reservation-specific refusal.
    """
    marker = tmp_path / "RAN"
    base_rev, _ = _seed_two_commits(git_repo)
    (git_repo.path / "cov.json").symlink_to("pkg/mod.zzz")
    git_repo.git("add", "-f", "cov.json")
    head_rev = git_repo.commit_all("commit a symlinked coverage artifact")
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.claims[0].status is Outcome.ERROR
    assert verdict.claims[1].status is Outcome.ERROR
    assert not marker.exists(), "the command must never have started"
    assert (git_repo.path / "cov.json").is_symlink(), "the consumer's own repo is never touched"


def test_run_lane_refuses_a_tracked_artifact_path(git_repo: GitRepo, tmp_path: Path):
    marker = tmp_path / "RAN"
    base_rev, head_rev = _seed_two_commits(git_repo)
    # Deliberately NOT `cov.json`: `_seed_two_commits` git-ignores that name
    # (A-140), and an ignored path cannot be added by an ordinary
    # `git add -A`, so committing it here would silently no-op and this
    # test would prove nothing. A different, un-ignored name is genuinely
    # tracked -- which is the state the guard exists to refuse.
    git_repo.write("tracked_cov.json", "{}")
    git_repo.commit_all("accidentally commit the coverage artifact")
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        base=base_rev,
        coverage_artifact="tracked_cov.json",
    )
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))

    verdict = runner.run_lane(
        lane,
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert not marker.exists()


def test_run_lane_refuses_a_directory_at_the_artifact_path(git_repo: GitRepo, tmp_path: Path):
    """P20: same reservation-owned vocabulary as the symlink case above --
    a pre-existing non-regular object is ERROR/UNREADABLE_ARTIFACT.

    P23: the directory must be COMMITTED (git cannot track an empty one, so
    a placeholder file lives inside it, force-added past its own
    ``.gitignore`` entry) rather than left on the live tree, for the
    identical reason the symlink test above now commits its own object."""
    marker = tmp_path / "RAN"
    base_rev, _ = _seed_two_commits(git_repo)
    git_repo.write("cov.json/placeholder.txt", "not a coverage artifact\n")
    git_repo.git("add", "-f", "cov.json/placeholder.txt")
    head_rev = git_repo.commit_all("commit a directory at the coverage artifact path")
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert not marker.exists()


# --- R1 renders honestly regardless of R0's own outcome (work item 5) ---------


def test_run_lane_r1_still_judges_coverage_when_r0_fails(git_repo: GitRepo):
    """A real test command commonly writes coverage AND exits non-zero (some
    assertion failed) -- R1 is a fact about what executed, not about
    whether R0's own assertions passed, so it renders independently."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    payload = _cov_json({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}})
    script = f"cat > cov.json <<'EOF'\n{payload}\nEOF\nexit 3"
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", script))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.FAIL
    assert verdict.claims[0].reason_code is ReasonCode.COMMAND_FAILED
    assert verdict.claims[1].status is Outcome.PASS
    assert verdict.claims[1].coverage.pct == 100.0
    assert verdict.outcome is Outcome.FAIL, "rollup: FAIL still outranks a passing R1"
    assert isinstance(verdict.judgment, Judgment), "R1 really was judged, so its policy is recorded"


def test_run_lane_r1_renders_empty_coverage_when_r0_never_starts(git_repo: GitRepo):
    """An unpermitted argv append means the process never launches at all
    (A-095) -- the pre-armed reservation is never recreated, so R1 renders
    the P20/A-174 truthful absence (NO_MEASUREMENT/EMPTY_COVERAGE) rather
    than being silently skipped."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", "exit 0"),
        allow_argv_append=False,
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        argv_append=("--extra",),
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.ERROR
    assert verdict.claims[0].reason_code is ReasonCode.EXEC_FAILED
    assert verdict.claims[1].status is Outcome.NO_MEASUREMENT
    assert verdict.claims[1].reason_code is ReasonCode.EMPTY_COVERAGE
    assert verdict.outcome is Outcome.ERROR, "rollup: ERROR (R0) still outranks NO_MEASUREMENT (R1)"


def _raise_timeout(argv, *, env, cwd, timeout):
    raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)


def test_run_lane_r1_renders_empty_coverage_when_r0_budget_expires(
    git_repo: GitRepo,
):
    """The same shape as EXEC_FAILED (no real wall-clock wait -- the fake
    raises immediately, AUTHORING.md §3b.A): the reservation was armed but
    the process never completed, so it was never rewritten either -- P20's
    truthful NO_MEASUREMENT/EMPTY_COVERAGE, not an unreadable artifact."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        process_runner=_raise_timeout,
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.BUDGET_EXCEEDED
    assert verdict.claims[0].reason_code is ReasonCode.LANE_TIMEOUT
    assert verdict.claims[1].status is Outcome.NO_MEASUREMENT
    assert verdict.claims[1].reason_code is ReasonCode.EMPTY_COVERAGE
    assert verdict.outcome is Outcome.NO_MEASUREMENT, "rollup: NO_MEASUREMENT (R1) outranks BUDGET_EXCEEDED (R0)"


# --- malformed coverage renders a complete claim, never propagates ------------


def test_run_lane_renders_format_mismatch_as_a_complete_verdict(git_repo: GitRepo):
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", "printf 'not json at all' > cov.json"),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.claims[1].reason_code is ReasonCode.FORMAT_MISMATCH
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None


# --- A-140: a refusal must not mutate the tree it refused to judge ------------


def test_run_lane_refusing_a_dirty_tree_leaves_the_existing_artifact_intact(
    git_repo: GitRepo, tmp_path: Path
):
    """A-140's own negative. The artifact removal (work item 4) now runs
    strictly AFTER the cleanliness guard (work item 3), so a run that
    refuses to do anything really does nothing: the declared artifact is
    still on disk, byte-for-byte, when ``NO_MEASUREMENT``/``DIRTY_TREE``
    comes back.

    Reverting the order fails this test at the ``read_text`` assertion --
    which is the exact defect it was written from: reproduced against the
    installed console script, where a lane declaring an untracked regular
    file as its ``artifact`` had that file DELETED before the dirty tree
    was ever reported.
    """
    marker = tmp_path / "RAN"
    base_rev, _ = _seed_two_commits(git_repo)
    seeded = _cov_json({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}})
    git_repo.write("cov.json", seeded)  # git-ignored by _seed_two_commits
    git_repo.write("uncommitted.txt", "the real dirt\n")
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", f"touch {marker}"),
    )

    verdict = runner.run_lane(
        lane,
        commit="d" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.claims[1].reason_code is ReasonCode.DIRTY_TREE
    assert not marker.exists(), "the command ran despite a refusal"
    artifact = git_repo.path / "cov.json"
    assert artifact.exists(), "a refused run deleted the consumer's artifact"
    assert artifact.read_text(encoding="utf-8") == seeded


# --- O4's remaining two named shapes, at pipeline level -----------------------


def test_run_lane_a_missing_executable_renders_a_complete_artifact(
    git_repo: GitRepo,
):
    """O4's "missing tool" shape, through the real pipeline: the lane's own
    binary does not exist, so ``execute_command`` maps the ``OSError`` to
    ``ERROR``/``EXEC_FAILED`` -- and R1 still renders a complete claim of
    its own rather than the whole invocation dying (work item 3)."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=(str(git_repo.path / "no-such-binary"), "--please"),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].status is Outcome.ERROR
    assert verdict.claims[0].reason_code is ReasonCode.EXEC_FAILED
    assert verdict.claims[1].status is Outcome.NO_MEASUREMENT
    assert verdict.claims[1].reason_code is ReasonCode.EMPTY_COVERAGE
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None
    assert verdict.outcome is Outcome.ERROR


def test_run_lane_uncovered_changed_lines_fail_and_still_record_the_policy(
    git_repo: GitRepo,
):
    """O4's "uncovered lines" shape -- the ONLY terminal shape where R1
    renders a coverage payload on a NON-pass, so it is also the only place
    proving ``judgment.r1`` is built from "the claim carries coverage" and
    not from "the claim passed" (P16's iff-invariant, the direction a
    PASS-only suite cannot distinguish)."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=_write_cov_argv(
            {"pkg/mod.zzz": {"executed_lines": [2, 3], "missing_lines": [4, 5]}}
        ),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.outcome is Outcome.FAIL
    assert verdict.reason_code is ReasonCode.UNCOVERED_LINES
    assert verdict.claims[1].coverage.pct == 50.0
    assert verdict.claims[1].coverage.missing_lines == {
        "pkg/mod.zzz": frozenset({4, 5})
    }
    assert isinstance(verdict.judgment, Judgment)
    assert verdict.judgment.resolved.base == base_rev
    assert verdict.judgment.r1.fail_under == judge.fail_under
    assert verdict.judgment.r1.allow_excluded == judge.allow_excluded
    assert verdict.judgment.r1.coverage_format == judge.coverage.format
    assert verdict.judgment.r1.coverage_artifact == judge.coverage.artifact


# --- P20/A-175: post-command dirt on an R0-only lane ---------------------------


def test_run_lane_post_command_dirt_on_an_r0_only_lane_renders_r0_itself_dirty_tree(
    git_repo: GitRepo,
):
    """No higher rigor is declared to preserve a real claim alongside --
    R0 ITSELF carries NO_MEASUREMENT/DIRTY_TREE instead of its real PASS,
    per the handoff's own decision matrix ("For an R0-only lane, R0 itself
    is NO_MEASUREMENT/DIRTY_TREE")."""
    git_repo.write("support.txt", "clean\n")
    git_repo.commit_all("seed support")
    lane = make_lane(
        rigor=("R0",), judge=None, argv=("/bin/sh", "-c", "printf dirty >> support.txt")
    )

    verdict = runner.run_lane(
        lane,
        commit="c" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.DIRTY_TREE
    assert [c.rigor for c in verdict.claims] == ["R0"]
    assert verdict.claims[0].status is Outcome.NO_MEASUREMENT
    assert verdict.claims[0].reason_code is ReasonCode.DIRTY_TREE
    assert verdict.ended == "2026-08-08T09:00:02+00:00", "the post-dirty-check clock reading"
    assert "dirty" in (git_repo.path / "support.txt").read_text(encoding="utf-8")


# --- P20: a detected swap at arm() refuses the whole invocation ---------------


def test_run_lane_refuses_the_whole_invocation_when_the_reservation_detects_a_swap_at_arm(
    monkeypatch: pytest.MonkeyPatch, git_repo: GitRepo, tmp_path: Path
):
    """``OutputReservation.arm`` rechecks the reserved object immediately
    before removing it (safeio's own contract, proven directly in
    ``test_safeio.py``); this proves `run_lane` WIRES that failure into a
    real refusal rather than letting it escape uncaught. The detection
    itself is injected (real reservation objects have nothing left to swap
    within one single-threaded invocation) -- see the module docstring's
    own reasoning for why this is the appropriate boundary to mock."""
    marker = tmp_path / "RAN"
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))

    def raising_arm(self: safeio.OutputReservation) -> None:
        raise AssayError(
            "simulated swap detected at arm time",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        )

    monkeypatch.setattr(safeio.OutputReservation, "arm", raising_arm)

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].status is Outcome.ERROR
    assert verdict.claims[1].status is Outcome.ERROR
    assert not marker.exists(), "the command must never have started"


# --- Reviewer (P20 work item 6): the post-command comparison is against the
# --- RESOLVED PRE-RUN COMMIT, not merely against dirt --------------------------


def _lane_committing_away_its_uncovered_line(repo: GitRepo, base_rev: str):
    """A lane whose command writes real coverage AND commits away the one
    changed line that coverage reports as missing."""
    payload = _cov_json({"pkg/mod.zzz": {"executed_lines": [2], "missing_lines": [3]}})
    command = (
        f"printf '%s' '{payload}' > cov.json && "
        f"printf 'BASE\\nLINE2\\n' > pkg/mod.zzz && "
        f"git add -A && "
        f"git -c user.name=cmd -c user.email=cmd@example.invalid "
        f"commit -q -m 'the command committed'"
    )
    judge = make_r1_judge(source_root_paths=(repo.path / "pkg",), base=base_rev)
    return make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", command),
        env_passthrough=("PATH",),
    )


def test_run_lane_refuses_when_the_command_moves_head_even_though_the_tree_is_clean(
    git_repo: GitRepo,
):
    """A command that COMMITS leaves a perfectly clean tree, so a dirt-only
    post-command check passes -- while ``evaluate_r1`` re-reads HEAD live and
    would measure a commit the artifact does not name.

    This is the combined-axis attack the blind review reproduced: the same
    lane rendered ``PASS`` at 100.0% while the artifact still recorded the
    pre-run commit, where the honest verdict at that commit is
    ``FAIL``/``UNCOVERED_LINES`` at 50.0% (proved by the control below).
    P20 carried this on A-175's existing ``DIRTY_TREE`` terminal because
    schema v3 had nothing better. **P21 work item 10 / A-178 corrects the
    diagnosis**: the tree is NOT dirty here, so ``DIRTY_TREE`` was a false
    name for the fact. The refusal and its R0/higher precedence are
    unchanged; only the reason is now truthful, and the dirty case below
    proves dirt still takes precedence when both are true.

    P23: the command's own ``git commit`` now happens INSIDE its own P22
    snapshot, never against the consumer's real repository -- the
    consumer's HEAD provably never moves at all, a STRONGER guarantee than
    the old live-tree architecture had (where this exact attack really did
    move the consumer's own HEAD, which is what the assertions below used
    to check). Detection is now :func:`~assay.runner._execute_snapshot_unit`'s
    own post-run check, comparing the SNAPSHOT's HEAD against the commit it
    was built from -- never the consumer's.
    """
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = repo.commit_all("add pkg base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\n")
    recorded_head = repo.commit_all("add pkg head")

    verdict = runner.run_lane(
        _lane_committing_away_its_uncovered_line(repo, base_rev),
        commit=recorded_head,
        repo=repo.path,
        project_root=repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert repo.head() == recorded_head, "the consumer's own repository is never touched"
    assert git_module.dirty_paths(repo.path) == (), "and must leave a clean tree"

    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.reason_code is ReasonCode.HEAD_CHANGED
    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    # A-195 precedence, mirroring P20's own direct-path rule one level down:
    # the command's own real R0 claim survives...
    assert verdict.claims[0].status is Outcome.PASS
    # ...and every higher declared claim is refused without being started.
    assert verdict.claims[1].status is Outcome.NO_MEASUREMENT
    assert verdict.claims[1].reason_code is ReasonCode.HEAD_CHANGED
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None
    # The artifact still names the commit assay actually resolved before the
    # command, and the consumer's own tracked source is provably untouched --
    # the command's edit happened only inside its own disposable snapshot.
    assert json.loads(verdict.to_json())["commit"] == recorded_head
    assert (repo.path / "pkg" / "mod.zzz").read_text(encoding="utf-8") == "BASE\nLINE2\nLINE3\n"


def test_the_same_lane_without_the_commit_is_a_real_uncovered_lines_fail(
    git_repo: GitRepo,
):
    """The control that makes the test above an oracle rather than an
    assertion: identical inputs, command minus the ``git commit``, must
    still reach a genuine judged R1 FAIL. Without this, refusing everything
    unconditionally would pass the attack test."""
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = repo.commit_all("add pkg base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\n")
    recorded_head = repo.commit_all("add pkg head")

    payload = _cov_json({"pkg/mod.zzz": {"executed_lines": [2], "missing_lines": [3]}})
    judge = make_r1_judge(source_root_paths=(repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", f"printf '%s' '{payload}' > cov.json"),
    )

    verdict = runner.run_lane(
        lane,
        commit=recorded_head,
        repo=repo.path,
        project_root=repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert repo.head() == recorded_head
    assert verdict.claims[1].status is Outcome.FAIL
    assert verdict.claims[1].reason_code is ReasonCode.UNCOVERED_LINES
    assert verdict.claims[1].coverage.pct == 50.0


def test_run_lane_omits_judgment_when_evaluate_r1_renders_a_payload_free_claim(
    git_repo: GitRepo,
):
    """P16's `judgment.r1`-iff-`coverage` invariant, exercised through the
    branch where the artifact PARSES fine but `evaluate_r1`'s own guard
    sequence still declines to judge (here: the declared base resolves to
    HEAD, so there is no delta to measure).

    Distinct from the consume/parse failure path, which never calls
    `evaluate_r1` at all: this proves the invariant holds for a claim
    `evaluate_r1` itself returned. Before this test the gated suite never
    took that arm, so building `judgment.r1` unconditionally there -- a
    verdict the schema's own correspondence check would then reject -- was
    invisible to it.
    """
    repo = git_repo
    repo.write(".gitignore", "cov.json\n")
    repo.write("pkg/mod.zzz", "BASE\n")
    repo.commit_all("add pkg base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = repo.commit_all("add pkg head")

    payload = _cov_json({"pkg/mod.zzz": {"executed_lines": [2]}})
    # base == HEAD: a real, judged NO_MEASUREMENT from evaluate_r1 itself.
    judge = make_r1_judge(source_root_paths=(repo.path / "pkg",), base=head_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", f"printf '%s' '{payload}' > cov.json"),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=repo.path,
        project_root=repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[1].status is Outcome.NO_MEASUREMENT
    assert verdict.claims[1].reason_code is ReasonCode.BASE_IS_HEAD
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None
