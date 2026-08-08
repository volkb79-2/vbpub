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

from conftest import FakeAdapter, GitRepo, fixed_clock, make_lane, make_r1_judge

from assay import runner
from assay.errors import Outcome, ReasonCode
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
        commit="c" * 40,
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
    assert verdict.judgment.r1.base == base_rev
    # make_r1_judge's own .source_roots is a stringified copy of the SAME
    # absolute source_root_paths (conftest.py's own documented shortcut),
    # so this assertion cannot by itself distinguish "read judge.source_roots"
    # from "stringify judge.source_root_paths" -- that discriminating
    # assertion lives in test_cli_run.py, against a REAL assay.toml load,
    # where the two genuinely differ.
    assert verdict.judgment.r1.source_roots == judge.source_roots
    assert verdict.judgment.r1.language == judge.language


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
        commit="e" * 40,
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
    command exits 0 without rewriting it. If removal did not happen, R1
    would wrongly render the stale PASS -- O3's own negative."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    git_repo.write("cov.json", _cov_json({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}))
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit="f" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert not (git_repo.path / "cov.json").exists(), "removed before the command ran"
    assert verdict.claims[1].status is Outcome.ERROR
    assert verdict.claims[1].reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.claims[1].coverage is None
    assert verdict.judgment is None


def test_run_lane_a_freshly_written_artifact_is_read_for_real(git_repo: GitRepo):
    """The mirror of the previous test: once a stale copy is removed, a
    GENUINE fresh one -- written by the command itself -- is read and
    judged normally."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    git_repo.write("cov.json", _cov_json({"pkg/mod.zzz": {"executed_lines": []}}))
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=_write_cov_argv({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}}),
    )

    verdict = runner.run_lane(
        lane,
        commit="1" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.outcome is Outcome.PASS
    assert verdict.claims[1].coverage.pct == 100.0


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


# --- unsafe coverage artifact path: refused BEFORE the command runs -----------


def test_run_lane_refuses_a_symlinked_artifact_path(git_repo: GitRepo, tmp_path: Path):
    marker = tmp_path / "RAN"
    base_rev, head_rev = _seed_two_commits(git_repo)
    outside_target = tmp_path / "outside.json"
    outside_target.write_text("{}", encoding="utf-8")
    (git_repo.path / "cov.json").symlink_to(outside_target)
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

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert verdict.claims[0].status is Outcome.ERROR
    assert verdict.claims[1].status is Outcome.ERROR
    assert not marker.exists(), "the command must never have started"
    assert (git_repo.path / "cov.json").is_symlink(), "refused, never touched"


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
        commit="6" * 40,
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
    marker = tmp_path / "RAN"
    base_rev, head_rev = _seed_two_commits(git_repo)
    (git_repo.path / "cov.json").mkdir()
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", f"touch {marker}"))

    verdict = runner.run_lane(
        lane,
        commit="7" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
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
        commit="8" * 40,
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


def test_run_lane_r1_renders_unreadable_artifact_when_r0_never_starts(git_repo: GitRepo):
    """An unpermitted argv append means the process never launches at all
    (A-095) -- the pre-removed artifact is never recreated, so R1 renders
    UNREADABLE_ARTIFACT rather than being silently skipped."""
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
        commit="9" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        argv_append=("--extra",),
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.ERROR
    assert verdict.claims[0].reason_code is ReasonCode.EXEC_FAILED
    assert verdict.claims[1].status is Outcome.ERROR
    assert verdict.claims[1].reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.outcome is Outcome.ERROR


def _raise_timeout(argv, *, env, cwd, timeout):
    raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)


def test_run_lane_r1_renders_unreadable_artifact_when_r0_budget_expires(
    git_repo: GitRepo,
):
    """The same shape as EXEC_FAILED (no real wall-clock wait -- the fake
    raises immediately, AUTHORING.md §3b.A): the artifact was pre-removed
    and the process never completed, so it was never rewritten either."""
    base_rev, head_rev = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge, argv=("/bin/sh", "-c", "exit 0"))

    verdict = runner.run_lane(
        lane,
        commit="b" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        process_runner=_raise_timeout,
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.BUDGET_EXCEEDED
    assert verdict.claims[0].reason_code is ReasonCode.LANE_TIMEOUT
    assert verdict.claims[1].status is Outcome.ERROR
    assert verdict.claims[1].reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.outcome is Outcome.ERROR, "rollup: ERROR outranks BUDGET_EXCEEDED"


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
        commit="a" * 40,
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
    base_rev, _ = _seed_two_commits(git_repo)
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=(str(git_repo.path / "no-such-binary"), "--please"),
    )

    verdict = runner.run_lane(
        lane,
        commit="e" * 40,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert [c.rigor for c in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].status is Outcome.ERROR
    assert verdict.claims[0].reason_code is ReasonCode.EXEC_FAILED
    assert verdict.claims[1].reason_code is ReasonCode.UNREADABLE_ARTIFACT
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
    base_rev, _ = _seed_two_commits(git_repo)
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
        commit="f" * 40,
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
    assert verdict.judgment.r1.base == base_rev
    assert verdict.judgment.r1.fail_under == judge.fail_under
    assert verdict.judgment.r1.allow_excluded == judge.allow_excluded
    assert verdict.judgment.r1.coverage_format == judge.coverage.format
    assert verdict.judgment.r1.coverage_artifact == judge.coverage.artifact
