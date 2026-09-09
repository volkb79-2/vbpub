"""B078 checkpoint 1 -- R0's structured-report tiebreak, at the level that
decides it (:func:`assay.runner.execute_plan`, reached through
:func:`~assay.runner.execute_command` and :func:`~assay.runner.run_lane`'s
direct R0-only path).

Every test here drives a REAL ``/bin/sh`` that writes (or deliberately fails
to write) a real file and then exits with a real code -- never a pre-seeded
fake standing in for the artifact, which is the same rule
``test_runner_run_lane.py`` already states for the coverage artifact. That
matters more than usual for this feature: the whole defect B078 addresses is
a disagreement between a process's exit code and the file it wrote, and a
test that fabricated the file could not exhibit the disagreement at all.

The fault-injection matrix (SR-5's own checkpoint-1 acceptance list) is the
spine of this module: for each way a report can be untrustworthy -- truncated
mid-write, the wrong shape, measuring nothing, never written, left over from
an earlier run -- the outcome must be A-073's exit-code rule, UNCHANGED and
in both directions, never a crash and never a silent pass. The one shape that
is allowed to override the exit code is a report that cleared every
completeness bullet, and it overrides it in both directions too.
"""

from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import FakeAdapter, GitRepo, fixed_clock, make_lane, make_r1_judge

from assay import runner, safeio
from assay.config import ResultReportConfig
from assay.errors import Outcome, ReasonCode

MOMENT_A = datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 9, 8, 10, 0, 1, tzinfo=timezone.utc)
MOMENT_C = datetime(2026, 9, 8, 10, 0, 2, tzinfo=timezone.utc)

REPORT_PATH = "vitest-report.json"
VITEST_REPORT = ResultReportConfig(format="vitest-json", path=REPORT_PATH)


def vitest_document(*, total: int, failed: int, success: bool | None = None) -> str:
    """One vitest ``--reporter=json`` document, in the shape vitest 3.2.7
    actually writes (Jest-compatible), with only the fields this reader
    consults spelled out.

    ``success`` defaults to ``failed == 0`` -- which is what vitest itself
    would write -- so a test that wants the RG-45 shape does not have to
    restate it, and a test that wants ``success`` to disagree with the counts
    has to say so explicitly.
    """
    document = {
        "numTotalTests": total,
        "numPassedTests": total - failed,
        "numFailedTests": failed,
        "testResults": [],
    }
    document["success"] = (failed == 0) if success is None else success
    return json.dumps(document)


def shell_writing(payload: str, *, exit_code: int, path: str = REPORT_PATH) -> tuple[str, ...]:
    """A real shell command that writes *payload* to *path* and then exits
    *exit_code* -- the two halves of every scenario in this module, and the
    only place their ordering is expressed.
    """
    return (
        "/bin/sh",
        "-c",
        f"printf %s {shlex.quote(payload)} > {path}; "
        f"echo 'noise on stdout'; echo 'noise on stderr' >&2; "
        f"exit {exit_code}",
    )


def run(lane, tmp_path: Path):
    return runner.execute_command(
        lane, cwd=tmp_path, clock=fixed_clock(MOMENT_A, MOMENT_B)
    )


# --- the RG-45 shape: the case this checkpoint exists to fix ------------------


def test_verified_complete_zero_failure_report_passes_over_a_nonzero_exit(
    tmp_path: Path,
):
    """RG-45, reproduced in miniature: every real test green, and a non-zero
    exit anyway.

    Live, that exit code comes from vitest's own worker<->orchestrator RPC
    heartbeat (hardcoded 60s, no config path in 3.2.7) tripping under host
    contention and being thrown as an unhandled error, which sets
    ``process.exitCode = 1`` after the reporter has already written a
    complete, all-green report. R0 must judge the report, not the exit code.
    """
    lane = make_lane(
        argv=shell_writing(vitest_document(total=140, failed=0), exit_code=1),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.returncode == 1, (
        "the real exit code stays on the artifact -- the report decides the "
        "OUTCOME, it does not rewrite what the process actually did"
    )
    assert result.stderr_tail is not None and "noise on stderr" in result.stderr_tail, (
        "a PASS that overrode a non-zero exit keeps the output tails, so the "
        "disagreement it resolved is still visible on the verdict"
    )


def test_the_r0_claim_for_the_rg45_shape_is_a_pass_claim(tmp_path: Path):
    """The tiebreak reaches the CLAIM, not just the internal result -- this is
    what a consumer's gate actually reads."""
    lane = make_lane(
        argv=shell_writing(vitest_document(total=154, failed=0), exit_code=1),
        result_report=VITEST_REPORT,
    )

    claim = runner.build_r0_claim(run(lane, tmp_path))

    assert claim.rigor == "R0"
    assert claim.status is Outcome.PASS
    assert claim.reason_code is None


# --- the other direction: a report naming failures wins over a zero exit -----


def test_verified_complete_report_naming_failures_fails_over_a_zero_exit(
    tmp_path: Path,
):
    """Reading the report at all means reading it as the ground truth, not as
    a one-directional escape hatch (SR-2). A framework that reports three
    failures and exits 0 is judged FAIL."""
    lane = make_lane(
        argv=shell_writing(
            vitest_document(total=140, failed=3, success=False), exit_code=0
        ),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED, (
        "no new ReasonCode -- COMMAND_FAILED still means 'the lane's wrapped "
        "command failed'; B078 changes WHEN it fires, never what it means"
    )
    assert result.returncode == 0


def test_verified_complete_report_naming_failures_fails_with_a_nonzero_exit(
    tmp_path: Path,
):
    """The ordinary agreeing case is unchanged -- and stays a FAIL for the
    report's reason as well as the exit code's."""
    lane = make_lane(
        argv=shell_writing(
            vitest_document(total=140, failed=3, success=False), exit_code=1
        ),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED
    assert result.returncode == 1


def test_a_report_claiming_success_while_naming_failures_still_fails(
    tmp_path: Path,
):
    """``success = true`` with a non-zero failure count is judged by the
    COUNTS.

    ``success`` is computed by the same orchestrator whose unhandled internal
    error is the defect being routed around, so it is used only as the
    format's "this run finished" marker (SR-2) and never as the verdict.
    """
    lane = make_lane(
        argv=shell_writing(
            vitest_document(total=10, failed=2, success=True), exit_code=0
        ),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED


# --- fault injection: every way a report can be untrustworthy ----------------
#
# Each shape is proven in BOTH exit-code directions, because "falls back to
# A-073" is a claim about the whole rule, not only about the failing half. A
# fallback that quietly forced FAIL would pass a one-directional test and be
# just as wrong.


TRUNCATED = '{"success": true, "numTotalTests": 140, "numFail'
WRONG_SHAPE_ARRAY = "[]"
WRONG_SHAPE_OTHER_TOOL = '{"stats": {"tests": 140, "failures": 0}}'
NO_FINISHED_MARKER = '{"numTotalTests": 140, "numFailedTests": 0}'
SUCCESS_NOT_A_BOOLEAN = '{"success": "yes", "numTotalTests": 1, "numFailedTests": 0}'
NOT_JSON_AT_ALL = "vitest exited before writing anything structured\n"
ZERO_TESTS = '{"success": true, "numTotalTests": 0, "numFailedTests": 0}'
COUNT_NOT_AN_INTEGER = '{"success": true, "numTotalTests": "140", "numFailedTests": 0}'
COUNT_IS_A_BOOLEAN = '{"success": true, "numTotalTests": true, "numFailedTests": 0}'
FAILED_COUNT_MISSING = '{"success": true, "numTotalTests": 140}'
COUNTS_CONTRADICT = '{"success": true, "numTotalTests": 3, "numFailedTests": 9}'
NEGATIVE_FAILED_COUNT = '{"success": true, "numTotalTests": 3, "numFailedTests": -1}'

UNUSABLE_REPORTS = {
    "truncated-mid-write": TRUNCATED,
    "wrong-shape-array": WRONG_SHAPE_ARRAY,
    "wrong-shape-other-tool": WRONG_SHAPE_OTHER_TOOL,
    "no-finished-marker": NO_FINISHED_MARKER,
    "success-not-a-boolean": SUCCESS_NOT_A_BOOLEAN,
    "not-json-at-all": NOT_JSON_AT_ALL,
    "zero-tests-collected": ZERO_TESTS,
    "count-not-an-integer": COUNT_NOT_AN_INTEGER,
    "count-is-a-boolean": COUNT_IS_A_BOOLEAN,
    "failed-count-missing": FAILED_COUNT_MISSING,
    "counts-contradict": COUNTS_CONTRADICT,
    "negative-failed-count": NEGATIVE_FAILED_COUNT,
}


@pytest.mark.parametrize("shape", sorted(UNUSABLE_REPORTS))
def test_an_unusable_report_falls_back_to_a073_on_a_nonzero_exit(
    tmp_path: Path, shape: str
):
    """A report assay cannot verify complete never rescues a non-zero exit.

    ``truncated-mid-write`` is the load-bearing one: the payload CLAIMS
    ``"success": true`` and is cut off before the counts, which is exactly
    what a process killed mid-flush leaves behind. It must still FAIL, and it
    must FAIL by falling back rather than by crashing.
    """
    lane = make_lane(
        argv=shell_writing(UNUSABLE_REPORTS[shape], exit_code=1),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED
    assert result.returncode == 1


@pytest.mark.parametrize("shape", sorted(UNUSABLE_REPORTS))
def test_an_unusable_report_falls_back_to_a073_on_a_zero_exit(
    tmp_path: Path, shape: str
):
    """The same fallback in the other direction: an unusable report does not
    manufacture a FAIL out of a green run either. A-073 means the exit code
    alone -- both halves of it."""
    lane = make_lane(
        argv=shell_writing(UNUSABLE_REPORTS[shape], exit_code=0),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.returncode == 0


def test_a_report_never_written_falls_back_to_a073(tmp_path: Path):
    """The genuine-crash signature: no report at all.

    A hard kill, an OOM-kill or a segfault leaves nothing at the declared
    path, and that absence is exactly what A-073 must keep failing loudly on.
    Absence is never evidence FOR a pass (SR-2).
    """
    lane = make_lane(
        argv=("/bin/sh", "-c", "exit 1"),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED
    assert not (tmp_path / REPORT_PATH).exists()


def test_a_stale_report_from_an_earlier_run_is_removed_and_never_read(
    tmp_path: Path,
):
    """The stale-artifact hole, closed by reservation (B046's own reasoning,
    one artifact over).

    This is the single most dangerous shape the feature could have: the crash
    that stops a report from being written is also the crash that leaves the
    PREVIOUS run's all-green report sitting at the declared path. Without
    arming the reservation, that stale file would be read as evidence of a run
    that never happened, and a genuine crash would be waved through as a PASS
    -- the exact hazard A-073 exists to prevent.
    """
    stale = tmp_path / REPORT_PATH
    stale.write_text(vitest_document(total=140, failed=0), encoding="utf-8")
    lane = make_lane(
        argv=("/bin/sh", "-c", "exit 1"),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL, "the stale report was not believed"
    assert result.reason_code is ReasonCode.COMMAND_FAILED
    assert not stale.exists(), (
        "arming the reservation removes the pre-run file, so 'the report I "
        "read is the report THIS command wrote' is true by construction"
    )


def test_a_symlinked_report_path_falls_back_and_is_never_followed(
    tmp_path: Path,
):
    """An unsafe object at the declared path is a fallback, not a crash.

    ``safeio``'s reservation refuses a symlink rather than following it out of
    the project; every such refusal lands on A-073, which can only ever cost a
    PASS and never grant one.
    """
    outside = tmp_path / "outside.json"
    outside.write_text(vitest_document(total=140, failed=0), encoding="utf-8")
    (tmp_path / REPORT_PATH).symlink_to(outside)
    lane = make_lane(argv=("/bin/sh", "-c", "exit 1"), result_report=VITEST_REPORT)

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert outside.exists(), "the link target outside the reservation was untouched"


def test_a_report_in_a_directory_that_does_not_exist_yet_falls_back(
    tmp_path: Path,
):
    """A DOCUMENTED limitation, asserted so it is a stated fact rather than an
    accident.

    ``safeio.reserve_output``'s ``create_missing_parents`` is contractually
    reserved for callers that own an ephemeral assay-managed snapshot, and R0
    runs in the consumer's live tree -- so assay does not create the report's
    parent directory. A lane must declare a path whose directory already
    exists (``docs/CONSUMERS.md`` says so, and recommends the project root).
    Until then the lane behaves exactly as an un-declaring one: A-073.
    """
    lane = make_lane(
        argv=shell_writing(
            vitest_document(total=140, failed=0),
            exit_code=1,
            path="reports/vitest.json",
        ),
        result_report=ResultReportConfig(
            format="vitest-json", path="reports/vitest.json"
        ),
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED


# --- a lane that does not declare a report is byte-for-byte unaffected -------


def test_an_undeclaring_lane_never_touches_the_reservation_machinery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The structural half of "byte-for-byte unaffected": for a lane with no
    ``result_report``, none of B078's code runs at all.

    Asserted by making the entry point EXPLODE rather than by comparing
    outputs -- an output comparison can only show that today's fields happen
    to match, while this shows the new path is not entered, which is the
    property SR-1 actually promises.
    """
    def explode(*args, **kwargs):  # pragma: no cover - the point is it never runs
        raise AssertionError(
            "reserve_output was called for a lane that declared no result_report"
        )

    monkeypatch.setattr(safeio, "reserve_output", explode)
    lane = make_lane(argv=shell_writing(vitest_document(total=1, failed=0), exit_code=1))

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED


def test_an_undeclaring_lane_ignores_a_perfectly_good_report_on_disk(
    tmp_path: Path,
):
    """The behavioural half: the same all-green report that turns the RG-45
    shape into a PASS above changes nothing without the declaration, and the
    file is left exactly where it was."""
    report = tmp_path / REPORT_PATH
    lane = make_lane(
        argv=shell_writing(vitest_document(total=140, failed=0), exit_code=1)
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED
    assert result.returncode == 1
    assert json.loads(report.read_text(encoding="utf-8"))["numTotalTests"] == 140, (
        "an undeclaring lane's own output file is neither read nor removed"
    )


def test_a_green_run_with_a_declared_report_is_the_unchanged_pass_shape(
    tmp_path: Path,
):
    """The happy path is field-for-field what it was before B078: no reason
    code, exit 0 recorded, and the output tails deliberately omitted."""
    lane = make_lane(
        argv=shell_writing(vitest_document(total=140, failed=0), exit_code=0),
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.PASS
    assert result.reason_code is None
    assert result.returncode == 0
    assert result.stdout_tail is None and result.stderr_tail is None


# --- the other terminals are untouched by the declaration --------------------


def test_a_declared_report_does_not_rescue_an_expired_budget(tmp_path: Path):
    """A timed-out command was KILLED, not exited -- there is no exit code to
    tiebreak, and BUDGET_EXCEEDED/LANE_TIMEOUT must survive even if a complete
    report happens to be on disk from earlier in the same run."""
    lane = make_lane(
        argv=(
            "/bin/sh",
            "-c",
            f"printf %s {shlex.quote(vitest_document(total=140, failed=0))} "
            f"> {REPORT_PATH}; sleep 30",
        ),
        budget="1s",
        budget_seconds=1.0,
        result_report=VITEST_REPORT,
    )

    result = run(lane, tmp_path)

    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert result.reason_code is ReasonCode.LANE_TIMEOUT
    assert result.returncode is None


def test_a_declared_report_does_not_rescue_a_refused_argv_append(tmp_path: Path):
    """A-095's append gate returns before anything launches, so no report can
    exist for this run -- and the reservation must not even be built, which is
    what leaves any pre-existing file alone."""
    stale = tmp_path / REPORT_PATH
    stale.write_text(vitest_document(total=140, failed=0), encoding="utf-8")
    lane = make_lane(
        argv=("/bin/sh", "-c", "exit 0"),
        allow_argv_append=False,
        result_report=VITEST_REPORT,
    )

    result = runner.execute_command(
        lane,
        argv_append=("--extra",),
        cwd=tmp_path,
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert result.outcome is Outcome.ERROR
    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert stale.exists(), "a run that never launched removes nothing"


# --- the two SHIPPED run_lane paths, one per lane shape ----------------------
#
# `run_lane` dispatches on declared rigor, and the two branches reach
# `execute_plan` through completely different call chains. Both produce the
# R0 claim, so both must apply the tiebreak -- and each needs its own
# end-to-end test, because a green suite covering only one of them is exactly
# how round 1 shipped a feature that did nothing for RG-45's actual lane.


def test_run_lane_direct_r0_path_applies_the_tiebreak(git_repo: GitRepo):
    """An R0-ONLY lane: `run_lane`'s direct branch.

    It calls :func:`~assay.runner.execute_plan` itself rather than going
    through :func:`~assay.runner.execute_command`, so wiring only the latter
    -- the site the design document names -- would have missed it.

    This is **not** the path RG-45 reproduces on; see
    :func:`test_run_lane_r0_r1_baseline_applies_the_tiebreak` for that one.
    """
    # A-140's rule, exactly as the coverage artifact follows it: this run's own
    # OUTPUT must be git-ignored, or the post-run dirt check sees it.
    git_repo.write(".gitignore", f"{REPORT_PATH}\n")
    commit = git_repo.commit_all("ignore the result report")
    lane = make_lane(
        rigor=("R0",),
        judge=None,
        argv=shell_writing(vitest_document(total=140, failed=0), exit_code=1),
        result_report=VITEST_REPORT,
    )

    verdict = runner.run_lane(
        lane,
        commit=commit,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.PASS
    assert [claim.status for claim in verdict.claims] == [Outcome.PASS]


def test_run_lane_direct_r0_path_still_fails_without_the_declaration(
    git_repo: GitRepo,
):
    """The must-fail control for the test above: the identical lane, the
    identical command, the identical report on disk -- and no declaration --
    is the FAIL it has always been. Without this, the test above could pass
    for a reason that has nothing to do with the report."""
    git_repo.write(".gitignore", f"{REPORT_PATH}\n")
    commit = git_repo.commit_all("ignore the result report")
    lane = make_lane(
        rigor=("R0",),
        judge=None,
        argv=shell_writing(vitest_document(total=140, failed=0), exit_code=1),
    )

    verdict = runner.run_lane(
        lane,
        commit=commit,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B),
    )

    assert verdict.outcome is Outcome.FAIL


# --- the path RG-45 ACTUALLY reproduces on: an R0+R1 lane's baseline unit ----
#
# Round-1 blocker 1. dstdns's `ui_unit` -- RG-45's own confirmed live
# reproduction -- declares `rigor = ["R0", "R1"]`
# (`/workspaces/dstdns/assay.toml`), so `run_lane` dispatches it to
# `_run_higher_rigor_lane` and it NEVER reaches the direct branch above. Its
# R0 command runs inside `_run_prepared_lane`'s baseline
# `_execute_snapshot_unit`, and that unit's `CommandResult` is what
# `build_r0_claim` turns into the R0 claim.
#
# The first round of this wave wired only the two paths above, on the strength
# of a false premise about `ui_unit`'s declared rigor, and shipped a fully
# green suite that did nothing for the bug it was written for. These two tests
# are the regression fence for exactly that.


def _cov_document(files: dict) -> str:
    """A real coverage.py-JSON document. The parser requires all three line
    buckets on every record, so they are filled in rather than assumed."""
    return json.dumps(
        {
            "files": {
                key: {
                    "executed_lines": list(record.get("executed_lines", [])),
                    "missing_lines": list(record.get("missing_lines", [])),
                    "excluded_lines": list(record.get("excluded_lines", [])),
                }
                for key, record in files.items()
            }
        }
    )


def _seed_r1_repo(repo: GitRepo) -> tuple[str, str]:
    """Two commits with a real changed-line diff, and both of this run's own
    OUTPUTS git-ignored (A-140) -- the coverage artifact and the result
    report alike, or `run_lane`'s whole-tree cleanliness guard refuses before
    anything runs."""
    repo.write(".gitignore", f"cov.json\n{REPORT_PATH}\n")
    repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = repo.commit_all("add pkg base")
    repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\nLINE4\nLINE5\n")
    head_rev = repo.commit_all("add pkg head")
    return base_rev, head_rev


def _r1_lane_argv(*, exit_code: int) -> tuple[str, ...]:
    """One REAL command that writes BOTH artifacts this lane declares -- the
    coverage document R1 judges and the vitest report R0 now judges -- and
    then exits *exit_code*. Nothing is pre-seeded: a PASS here proves both
    files came from this run, inside the snapshot."""
    coverage = _cov_document({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}})
    report = vitest_document(total=140, failed=0)
    return (
        "/bin/sh",
        "-c",
        f"cat > cov.json <<'COVEOF'\n{coverage}\nCOVEOF\n"
        f"printf %s {shlex.quote(report)} > {REPORT_PATH}; "
        f"echo 'noise on stderr' >&2; "
        f"exit {exit_code}",
    )


def test_run_lane_r0_r1_baseline_applies_the_tiebreak(git_repo: GitRepo):
    """**The RG-45 shape on the lane shape that actually reproduces it.**

    An R0+R1 lane whose command writes a verified-complete, zero-failure
    vitest report and then exits 1. The R0 claim must be `PASS` on the
    report's evidence -- and, because R0 no longer fails, R1 must go on to be
    evaluated at all, which is the difference between "the gate is green" and
    "the gate reports a coverage claim that was never computed".
    """
    base_rev, head_rev = _seed_r1_repo(git_repo)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(
            source_root_paths=(git_repo.path / "pkg",), base=base_rev
        ),
        argv=_r1_lane_argv(exit_code=1),
        result_report=VITEST_REPORT,
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=FakeAdapter(),
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert [claim.rigor for claim in verdict.claims] == ["R0", "R1"]
    assert verdict.claims[0].status is Outcome.PASS, (
        "the R0 claim built from the baseline unit must honour the report"
    )
    assert verdict.claims[0].reason_code is None
    assert verdict.claims[1].status is Outcome.PASS, (
        "R1 ran at all, which it cannot do behind an R0 FAIL"
    )
    assert verdict.outcome is Outcome.PASS


def test_run_lane_r0_r1_baseline_still_fails_without_the_declaration(
    git_repo: GitRepo,
):
    """The must-fail control. Identical lane, identical command, identical
    report written to the same path -- and no declaration -- is the
    `FAIL`/`COMMAND_FAILED` it has always been.

    Without this, the test above could go green for any reason unrelated to
    the report, and the round-1 defect (a green test over a path that was
    never wired) is precisely that failure mode.
    """
    base_rev, head_rev = _seed_r1_repo(git_repo)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(
            source_root_paths=(git_repo.path / "pkg",), base=base_rev
        ),
        argv=_r1_lane_argv(exit_code=1),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=FakeAdapter(),
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.FAIL
    assert verdict.claims[0].reason_code is ReasonCode.COMMAND_FAILED
    assert verdict.outcome is Outcome.FAIL


def test_run_lane_r0_r1_baseline_report_naming_failures_fails_over_a_zero_exit(
    git_repo: GitRepo,
):
    """Both directions on the baseline path too: a verified-complete report
    naming real failures is a `FAIL` even though the command exited 0.

    The tiebreak is the ground truth on this path or it is a one-directional
    escape hatch, and SR-2 forbids the latter.
    """
    base_rev, head_rev = _seed_r1_repo(git_repo)
    coverage = _cov_document({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}})
    report = vitest_document(total=140, failed=3, success=False)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(
            source_root_paths=(git_repo.path / "pkg",), base=base_rev
        ),
        argv=(
            "/bin/sh",
            "-c",
            f"cat > cov.json <<'COVEOF'\n{coverage}\nCOVEOF\n"
            f"printf %s {shlex.quote(report)} > {REPORT_PATH}; exit 0",
        ),
        result_report=VITEST_REPORT,
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=FakeAdapter(),
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.FAIL
    assert verdict.claims[0].reason_code is ReasonCode.COMMAND_FAILED


def test_run_lane_r0_r1_baseline_creates_the_reports_parent_directory(
    git_repo: GitRepo,
):
    """The snapshot half legitimately gets `create_missing_parents=True`.

    B006(b) reserves that opt-in for a call site that KNOWS it owns an
    ephemeral, assay-managed snapshot -- which the baseline unit does, and the
    direct path (measuring the consumer's live tree) does not. It matters
    here more than for coverage: a snapshot is a tracked-only checkout, so an
    untracked output directory never exists in it, and without this a lane
    declaring a subdirectory path would fall back to A-073 on EVERY run
    rather than only the first. The command itself never `mkdir`s, so this
    passes only if the reservation created the directory.
    """
    base_rev, head_rev = _seed_r1_repo(git_repo)
    git_repo.write(".gitignore", "cov.json\n.assay/\n")
    head_rev = git_repo.commit_all("ignore the report directory")
    coverage = _cov_document({"pkg/mod.zzz": {"executed_lines": [2, 3, 4, 5]}})
    report = vitest_document(total=140, failed=0)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(
            source_root_paths=(git_repo.path / "pkg",), base=base_rev
        ),
        argv=(
            "/bin/sh",
            "-c",
            f"cat > cov.json <<'COVEOF'\n{coverage}\nCOVEOF\n"
            f"printf %s {shlex.quote(report)} > .assay/report.json; exit 1",
        ),
        result_report=ResultReportConfig(
            format="vitest-json", path=".assay/report.json"
        ),
    )

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=FakeAdapter(),
        assay_version="0.1.0",
        clock=fixed_clock(MOMENT_A, MOMENT_B, MOMENT_C),
    )

    assert verdict.claims[0].status is Outcome.PASS


# --- R3's canary halves are excluded, and stay excluded ----------------------


def test_the_legacy_canary_pipeline_never_consults_a_declared_report(
    monkeypatch: pytest.MonkeyPatch,
):
    """Round-1 blocker 3. `assay.canary._run_pipeline` runs the lane's argv to
    answer a DIFFERENT question -- "did injecting this defect change the
    judgement" -- so it opts out explicitly rather than inheriting
    :func:`~assay.runner.execute_command`'s lane-declared default.

    Asserted at the seam rather than through an end-to-end canary run,
    because the property is about which argument reaches `execute_plan`: a
    behavioural test could pass while the input was silently being consulted
    and happening not to change the outcome.
    """
    seen: list[object] = []
    real = runner.execute_plan

    def record(plan, **kwargs):
        seen.append(kwargs.get("result_report"))
        return real(plan, **kwargs)

    monkeypatch.setattr(runner, "execute_plan", record)

    runner.execute_command(
        make_lane(argv=("/bin/sh", "-c", "exit 0"), result_report=VITEST_REPORT),
        cwd=Path("/tmp"),
        result_report=None,
    )

    assert seen == [None], (
        "an explicit result_report=None must reach execute_plan, so a canary "
        "half is excluded rather than inheriting the lane's declaration"
    )


def test_execute_command_honours_the_lane_declaration_by_default(
    monkeypatch: pytest.MonkeyPatch,
):
    """The must-fail control for the exclusion above: without the explicit
    argument, `execute_command` IS the R0 step and forwards the lane's own
    declaration. If the sentinel default ever collapsed to plain `None`, the
    feature would silently stop working through the public API and only this
    test would notice."""
    seen: list[object] = []
    real = runner.execute_plan

    def record(plan, **kwargs):
        seen.append(kwargs.get("result_report"))
        return real(plan, **kwargs)

    monkeypatch.setattr(runner, "execute_plan", record)

    runner.execute_command(
        make_lane(argv=("/bin/sh", "-c", "exit 0"), result_report=VITEST_REPORT),
        cwd=Path("/tmp"),
    )

    assert seen == [VITEST_REPORT]


def test_a_declaring_lane_produces_identical_canary_behaviour(tmp_path: Path):
    """The behavioural half of blocker 3: a lane declaring `result_report`
    and an otherwise identical lane that does not produce the same canary
    outcome, for the control half and the transformed half alike."""
    from assay import canary

    def outcome_for(report):
        lane = make_lane(
            rigor=("R0",),
            argv=("/bin/sh", "-c", "exit 1"),
            result_report=report,
        )
        return canary._run_pipeline(
            lane,
            repo=tmp_path,
            project_root=tmp_path,
            base_commit="d" * 40,
            adapter=None,
            infrastructure_source=None,
            infrastructure_environment=None,
            process_runner=runner.default_process_runner,
            clock=lambda: datetime(2026, 9, 8, tzinfo=timezone.utc),
        )

    assert outcome_for(VITEST_REPORT) == outcome_for(None)
