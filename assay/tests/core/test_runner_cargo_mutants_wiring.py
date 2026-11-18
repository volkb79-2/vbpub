"""B245 U5a-2 sub-step A: the runner wiring of the cargo-mutants directory
artifact -- the four-state handle, ``execute_plan``'s ``directory_report`` and
the directory branch of ``_execute_snapshot_unit``.

The report is the REAL committed ``basefail-test`` cargo-mutants 27.1.0 run
(a failing baseline, zero mutants), retimed into the fake clock's window: the
tool's own ``start_time``/``end_time`` are what the window rule reads, and the
fixture was produced on another day.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import (
    GitRepo,
    TESTS_ROOT,
    completed_process,
    fixed_clock,
    make_deadline,
    make_lane,
    make_plan,
    prepared_snapshot,
    timeout_expired,
)

from assay import mutation_parsers, runner, safeio
from assay.config import ResultReportConfig
from assay.errors import AssayError, Outcome, ReasonCode
from assay.result_reports import ReportUnusable

FORMAT = "cargo-mutants-outcomes"
FIXTURE_OUT = TESTS_ROOT / "fixtures" / "rust" / "cargo-mutants" / "basefail-test" / "mutants.out"
OUT = "out"
REPORT = ResultReportConfig(format=FORMAT, path=OUT)

MOMENT_A = datetime(2026, 11, 18, 10, 0, 0, tzinfo=timezone.utc)
MOMENT_B = datetime(2026, 11, 18, 10, 0, 2, tzinfo=timezone.utc)
INSIDE = ("2026-11-18T10:00:00.500000000Z", "2026-11-18T10:00:01.500000000Z")
STALE = ("2026-11-17T10:00:00.500000000Z", "2026-11-17T10:00:01.500000000Z")


def _document(times: tuple[str, str] = INSIDE, *, baseline_ok: bool = False) -> dict:
    document = json.loads((FIXTURE_OUT / "outcomes.json").read_text(encoding="utf-8"))
    document["start_time"], document["end_time"] = times
    if baseline_ok:
        baseline = document["outcomes"][0]
        baseline["summary"] = "Success"
        for phase in baseline["phase_results"]:
            phase["process_status"] = "Success"
    return document


def _report_bytes(times: tuple[str, str] = INSIDE, *, baseline_ok: bool = False) -> bytes:
    return json.dumps(_document(times, baseline_ok=baseline_ok)).encode()


def _write_out(cwd: Path, times: tuple[str, str] = INSIDE, *, baseline_ok: bool = False) -> None:
    child = Path(cwd) / OUT / "mutants.out"
    shutil.copytree(FIXTURE_OUT, child)
    (child / "outcomes.json").write_bytes(_report_bytes(times, baseline_ok=baseline_ok))


def _reservation(root: Path, *, create_missing_parents: bool = True):
    reservation = safeio.reserve_directory_output(
        root,
        OUT,
        child="mutants.out",
        rotated="mutants.out.old",
        report_name="outcomes.json",
        report_limit=mutation_parsers.MAX_MUTATION_REPORT_BYTES,
        member_limit=256 * 1024,
        max_members=10,
        max_total_bytes=1024 * 1024,
        create_missing_parents=create_missing_parents,
    )
    reservation.arm()
    return reservation


def _artifact(report: bytes) -> safeio.DirectoryArtifact:
    return safeio.DirectoryArtifact(
        artifact=OUT + "/mutants.out", identity=(1, 2), report=report, members={}
    )


class _FakeReservation:
    """Stands in for ``DirectoryArtifactReservation``: one scripted consume."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.consumed = 0
        self.closed = 0

    def consume(self, *, members):
        self.consumed += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def close(self) -> None:
        self.closed += 1


def _consume(handle: runner._DirectoryReportHandle):
    return handle.consume_once(
        members=mutation_parsers.cargo_mutants_outcomes.diff_paths,
        result_report_format=FORMAT,
        started_at=MOMENT_A,
        ended_at=MOMENT_B,
    )


# --- the handle's four states -------------------------------------------------


def test_a_fresh_handle_is_not_consumed():
    handle = runner._DirectoryReportHandle(_FakeReservation(safeio.ABSENT))

    assert handle.state == runner._DirectoryNotConsumed()
    assert handle.summary is None


def test_absent_is_a_state_of_its_own():
    fake = _FakeReservation(safeio.ABSENT)
    handle = runner._DirectoryReportHandle(fake)

    assert _consume(handle) == runner._DirectoryAbsent()
    assert handle.summary is None


def test_a_refusal_is_stored_not_raised_and_never_consumed_twice():
    refusal = AssayError(
        "rotated", outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
    )
    fake = _FakeReservation(refusal)
    handle = runner._DirectoryReportHandle(fake)

    first = _consume(handle)
    second = _consume(handle)

    assert first == runner._DirectoryRefused(error=refusal)
    assert second is first
    assert fake.consumed == 1
    assert handle.summary is None


def test_a_usable_report_is_present_with_its_summary_and_clears_the_memo():
    fake = _FakeReservation(_artifact(_report_bytes()))
    handle = runner._DirectoryReportHandle(fake)

    state = _consume(handle)

    assert isinstance(state, runner._DirectoryPresent)
    assert state.unusable is None
    assert state.summary is not None
    assert state.summary.failed == 1  # the failing baseline
    assert handle.summary is state.summary
    assert mutation_parsers.cargo_mutants_outcomes.load_document.cache_info().currsize == 0
    assert _consume(handle) is state
    assert fake.consumed == 1


def test_an_unusable_report_is_present_with_the_reason_and_no_summary():
    fake = _FakeReservation(_artifact(_report_bytes(STALE)))
    handle = runner._DirectoryReportHandle(fake)

    state = _consume(handle)

    assert isinstance(state, runner._DirectoryPresent)
    assert state.summary is None
    assert "outside this command's window" in state.unusable
    assert handle.summary is None
    assert mutation_parsers.cargo_mutants_outcomes.load_document.cache_info().currsize == 0


def test_close_delegates_to_the_reservation():
    fake = _FakeReservation(safeio.ABSENT)
    runner._DirectoryReportHandle(fake).close()

    assert fake.closed == 1


# --- execute_plan with a directory report -------------------------------------


def _execute(tmp_path: Path, process_runner, handle, *, clock=None):
    return runner.execute_plan(
        make_plan(make_lane(argv=("/bin/true",), result_report=None)),
        cwd=tmp_path,
        timeout=60.0,
        process_runner=process_runner,
        clock=clock or fixed_clock(MOMENT_A, MOMENT_B),
        result_report=REPORT,
        directory_report=handle,
    )


def test_a_failed_baseline_in_the_report_fails_r0_decided_by_the_report(tmp_path: Path):
    def writes_the_report_and_exits_zero(argv, *, env, cwd, timeout):
        _write_out(cwd)
        return completed_process(argv, 0, stdout="", stderr="")

    handle = runner._DirectoryReportHandle(_reservation(tmp_path))

    result = _execute(tmp_path, writes_the_report_and_exits_zero, handle)

    assert result.outcome is Outcome.FAIL
    assert result.reason_code is ReasonCode.COMMAND_FAILED
    assert result.decided_by_report is True
    assert isinstance(handle.state, runner._DirectoryPresent)
    assert handle.state.artifact.artifact == OUT + "/mutants.out"
    assert not (tmp_path / "vitest-report.json").exists()


def test_a_successful_baseline_in_the_report_passes_r0_decided_by_the_report(tmp_path: Path):
    def writes_the_report_and_exits_one(argv, *, env, cwd, timeout):
        _write_out(cwd, baseline_ok=True)
        return completed_process(argv, 1, stdout="", stderr="")

    handle = runner._DirectoryReportHandle(_reservation(tmp_path))

    result = _execute(tmp_path, writes_the_report_and_exits_one, handle)

    assert result.outcome is Outcome.PASS
    assert result.returncode == 1
    assert result.decided_by_report is True
    assert handle.summary is not None and handle.summary.failed == 0


def test_an_unusable_report_leaves_r0_to_the_exit_code(tmp_path: Path):
    def writes_a_stale_report(argv, *, env, cwd, timeout):
        _write_out(cwd, STALE)
        return completed_process(argv, 0, stdout="", stderr="")

    handle = runner._DirectoryReportHandle(_reservation(tmp_path))

    result = _execute(tmp_path, writes_a_stale_report, handle)

    assert result.outcome is Outcome.PASS
    assert result.decided_by_report is False
    assert isinstance(handle.state, runner._DirectoryPresent)
    assert handle.state.summary is None and handle.state.unusable


def test_a_command_that_writes_nothing_is_absent_and_the_exit_code_decides(tmp_path: Path):
    handle = runner._DirectoryReportHandle(_reservation(tmp_path))

    result = _execute(
        tmp_path,
        lambda argv, *, env, cwd, timeout: completed_process(argv, 0, stdout="", stderr=""),
        handle,
    )

    assert result.outcome is Outcome.PASS
    assert result.decided_by_report is False
    assert handle.state == runner._DirectoryAbsent()


def test_a_rotated_directory_is_a_refusal_not_an_exit_code_change(tmp_path: Path):
    def leaves_a_rotated_directory(argv, *, env, cwd, timeout):
        _write_out(cwd)
        (Path(cwd) / OUT / "mutants.out.old").mkdir()
        return completed_process(argv, 0, stdout="", stderr="")

    handle = runner._DirectoryReportHandle(_reservation(tmp_path))

    result = _execute(tmp_path, leaves_a_rotated_directory, handle)

    assert result.outcome is Outcome.PASS
    assert isinstance(handle.state, runner._DirectoryRefused)
    assert handle.state.error.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_a_timed_out_run_leaves_the_handle_not_consumed(tmp_path: Path):
    def times_out(argv, *, env, cwd, timeout):
        raise timeout_expired(argv, timeout)

    handle = runner._DirectoryReportHandle(_reservation(tmp_path))

    result = _execute(tmp_path, times_out, handle)

    assert result.outcome is Outcome.BUDGET_EXCEEDED
    assert handle.state == runner._DirectoryNotConsumed()


def test_a_refused_argv_append_closes_the_handle_without_launching(tmp_path: Path):
    fake = _FakeReservation(safeio.ABSENT)
    handle = runner._DirectoryReportHandle(fake)
    plan = make_plan(
        make_lane(argv=("/bin/true",), allow_argv_append=False),
        argv_append=("--extra",),
    )

    result = runner.execute_plan(
        plan,
        cwd=tmp_path,
        timeout=60.0,
        process_runner=lambda *_a, **_k: pytest.fail("must not launch"),
        clock=fixed_clock(MOMENT_A, MOMENT_B),
        result_report=REPORT,
        directory_report=handle,
    )

    assert result.reason_code is ReasonCode.EXEC_FAILED
    assert fake.closed == 1
    assert fake.consumed == 0


def test_the_handle_is_closed_by_execute_plan_even_when_the_run_raises(tmp_path: Path):
    fake = _FakeReservation(safeio.ABSENT)
    handle = runner._DirectoryReportHandle(fake)

    def explodes(argv, *, env, cwd, timeout):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _execute(tmp_path, explodes, handle)

    assert fake.closed == 1


# --- _execute_snapshot_unit: the directory branch ------------------------------


def _unit(git_repo: GitRepo, tmp_path: Path, monkeypatch, process_runner, **extra):
    scratch = tmp_path / "snapshot"
    scratch.mkdir()
    monkeypatch.setattr(runner.git, "dirty_paths", lambda *_a, **_k: ())
    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            monkeypatch.setattr(runner.git, "head_rev", lambda *_a, **_k: snapshot.commit)
            return runner._execute_snapshot_unit(
                plan=make_plan(make_lane(argv=("/bin/true",))),
                snapshot=snapshot,
                deadline=make_deadline(),
                wants_coverage=False,
                coverage_artifact=None,
                coverage_format=None,
                coverage_producer=None,
                process_runner=process_runner,
                clock=fixed_clock(MOMENT_A, MOMENT_B),
                create_missing_parents=True,
                result_report=REPORT,
                mutation_artifact=OUT,
                mutation_format=FORMAT,
                **extra,
            )


def test_the_snapshot_unit_reserves_one_directory_artifact_and_exposes_the_handle(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    git_repo.write("src/mod.rs", "fn main() {}\n")
    git_repo.commit_all("seed")

    def writes_the_report(argv, *, env, cwd, timeout):
        _write_out(cwd)
        return completed_process(argv, 0, stdout="", stderr="")

    unit = _unit(git_repo, tmp_path, monkeypatch, writes_the_report)

    assert unit.result.outcome is Outcome.FAIL
    assert unit.result.decided_by_report is True
    assert isinstance(unit.directory_report.state, runner._DirectoryPresent)
    # The file-kind slots stay empty: nothing else consumed the report.
    assert unit.mutation_report_bytes is None
    assert unit.mutation_report_error is None


@pytest.mark.parametrize("with_others", [False, True])
def test_a_tracked_directory_artifact_refuses_and_closes_everything(
    git_repo: GitRepo, tmp_path: Path, monkeypatch, with_others: bool
):
    git_repo.write("src/mod.rs", "fn main() {}\n")
    git_repo.write(f"{OUT}/keep.txt", "tracked\n")
    git_repo.commit_all("seed tracked out")
    closed: list[str] = []

    class Reservation:
        def __init__(self, name: str) -> None:
            self.name = name

        def arm(self) -> None:
            pass

        def close(self) -> None:
            closed.append(self.name)

    monkeypatch.setattr(
        runner.safeio, "reserve_directory_output", lambda *_a, **_k: Reservation("dir")
    )
    monkeypatch.setattr(
        runner.safeio, "reserve_output", lambda *_a, **_k: Reservation("file")
    )
    extra = {"equivalence_artifact": "eq.json"} if with_others else {}
    scratch = tmp_path / "snapshot"
    scratch.mkdir()
    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            with pytest.raises(AssayError, match="tracked by git") as caught:
                runner._execute_snapshot_unit(
                    plan=make_plan(make_lane(argv=("/bin/true",))),
                    snapshot=snapshot,
                    deadline=make_deadline(),
                    wants_coverage=with_others,
                    coverage_artifact="cov.json" if with_others else None,
                    coverage_format="coverage-py-json" if with_others else None,
                    coverage_producer=None,
                    process_runner=lambda *_a, **_k: pytest.fail("must refuse first"),
                    clock=fixed_clock(MOMENT_A, MOMENT_B),
                    create_missing_parents=True,
                    result_report=REPORT,
                    mutation_artifact=OUT,
                    mutation_format=FORMAT,
                    **extra,
                )

    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert closed.count("dir") == 1
    assert closed.count("file") == (2 if with_others else 0)


def test_a_tracked_kill_signal_closes_the_directory_handle(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    git_repo.write("src/mod.rs", "fn main() {}\n")
    git_repo.write("signal.json", "{}\n")
    git_repo.commit_all("seed tracked signal")
    closed = []

    class Reservation:
        def arm(self) -> None:
            pass

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        runner.safeio, "reserve_directory_output", lambda *_a, **_k: Reservation()
    )
    scratch = tmp_path / "snapshot"
    scratch.mkdir()
    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            with pytest.raises(AssayError, match="tracked by git"):
                runner._execute_snapshot_unit(
                    plan=make_plan(make_lane(argv=("/bin/true",))),
                    snapshot=snapshot,
                    deadline=make_deadline(),
                    wants_coverage=False,
                    coverage_artifact=None,
                    coverage_format=None,
                    coverage_producer=None,
                    process_runner=lambda *_a, **_k: pytest.fail("must refuse first"),
                    clock=fixed_clock(MOMENT_A, MOMENT_B),
                    create_missing_parents=True,
                    result_report=REPORT,
                    mutation_artifact=OUT,
                    mutation_format=FORMAT,
                    kill_signal_artifact="signal.json",
                )

    assert closed == [True]
