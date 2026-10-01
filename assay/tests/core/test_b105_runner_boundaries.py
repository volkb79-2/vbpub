"""Whole-source paths for runner refusal and cleanup boundaries."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import CanaryConfig, MutationConfig, ResultReportConfig
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import Claim
from conftest import (
    GitRepo,
    make_deadline,
    make_lane,
    make_plan,
    make_r1_judge,
    make_r2_judge,
    prepared_snapshot,
)


def _seed_r1_lane(repo: GitRepo, *, rigor=("R0", "R1"), canary=None):
    repo.write(".gitignore", "cov.json\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    base = repo.commit_all("seed R1 boundary source")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0\n")
    head = repo.commit_all("change R1 boundary source")
    judge = make_r1_judge(
        language="python",
        source_root_paths=(repo.path / "src",),
        base=base,
        fail_under=100.0,
        coverage_artifact="cov.json",
    )
    if canary is not None:
        from dataclasses import replace

        judge = replace(judge, canary=canary)
    return head, make_lane(rigor=rigor, judge=judge)


def _seed_r2_lane(
    repo: GitRepo,
    *,
    targets=("src/mod.py",),
    argv=("python", "-m", "pytest", "-q"),
    pytest_config: str | None = None,
    language="python",
):
    repo.write("src/mod.py", "def flag(value):\n    return value > 0\n")
    if pytest_config is not None:
        repo.write("pytest.ini", pytest_config)
    head = repo.commit_all("seed R2 boundary source")
    judge = make_r2_judge(
        language=language,
        source_root_paths=(repo.path / "src",),
        base=head,
        mutation=MutationConfig(
            jobs=1,
            max_mutants=10,
            operators=(f"{language}:compare-swap",),
        ),
    )
    judge = replace(judge, mode="whole_target", targets=targets, base=None)
    return head, make_lane(rigor=("R0", "R2"), judge=judge, argv=argv)


def _write_coverage(argv, *, env, cwd, timeout):
    payload = {
        "files": {
            "src/mod.py": {
                "executed_lines": [1, 2],
                "missing_lines": [],
                "excluded_lines": [],
            }
        }
    }
    (Path(cwd) / "cov.json").write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.CompletedProcess(list(argv), 0, stdout="", stderr="")


def test_bounded_command_tail_preserves_utf8_at_its_byte_boundary():
    suffix_bytes = runner.COMMAND_TAIL_BYTES - 1
    raw = "x" * 10 + "π" + "y" * suffix_bytes

    tail, removed = runner._bounded_tail(raw)

    assert tail == "y" * suffix_bytes
    assert removed == 12
    assert len(tail.encode("utf-8")) == runner.COMMAND_TAIL_BYTES - 1


def test_command_plan_refuses_an_unrecognized_infrastructure_source():
    lane = make_lane(infrastructure={"image": "invented:deploy.image"})

    with pytest.raises(AssayError) as caught:
        runner.resolve_command_plan(lane, passthrough_source={})

    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "invalid declaration" in str(caught.value)


def test_probe_refusal_renders_diagnostics_and_keeps_the_document_detail():
    lane = SimpleNamespace(
        name="package", environment_command=("python", "-m", "pytest")
    )
    probe = SimpleNamespace(
        reason_code=ReasonCode.COMMAND_FAILED,
        returncode=7,
        stderr_tail="probe stderr\n",
        stdout_tail="probe stdout\n",
    )
    diagnostics = StringIO()

    detail = runner._report_probe_refusal(
        lane,
        probe,
        status=Outcome.ERROR,
        reason_code=ReasonCode.BAD_LANE_CONFIG,
        diagnostics=diagnostics,
    )

    assert detail is not None
    assert "probe exited 7" in detail.text
    assert "probe stderr: probe stderr" in diagnostics.getvalue()
    assert "probe stdout: probe stdout" in diagnostics.getvalue()

    # The document still gets its explanation when the caller has no stream;
    # only the human-facing lines are omitted.
    without_stream = runner._report_probe_refusal(
        lane,
        probe,
        status=Outcome.ERROR,
        reason_code=ReasonCode.BAD_LANE_CONFIG,
        diagnostics=None,
    )
    assert without_stream == detail

    empty_diagnostics = StringIO()
    runner._report_probe_refusal(
        lane,
        SimpleNamespace(
            reason_code=ReasonCode.EXEC_FAILED,
            returncode=None,
            stderr_tail="",
            stdout_tail="",
        ),
        status=Outcome.ERROR,
        reason_code=ReasonCode.BAD_LANE_CONFIG,
        diagnostics=empty_diagnostics,
    )
    assert empty_diagnostics.getvalue().startswith("assay: ERROR/BAD_LANE_CONFIG")
    assert "probe stderr:" not in empty_diagnostics.getvalue()
    assert "probe stdout:" not in empty_diagnostics.getvalue()


def test_result_report_arm_failure_closes_the_reservation_and_falls_back(
    tmp_path, monkeypatch
):
    class Reservation:
        closed = False

        def arm(self):
            raise OSError("report destination disappeared")

        def close(self):
            self.closed = True

    reservation = Reservation()
    monkeypatch.setattr(runner.safeio, "reserve_output", lambda *_a, **_k: reservation)

    result = runner._reserve_result_report(
        tmp_path,
        ResultReportConfig(format="vitest-json", path="report.json"),
    )

    assert result is None
    assert reservation.closed


def test_result_report_consume_failure_is_no_usable_report():
    class Reservation:
        def consume(self):
            raise OSError("report file changed during read")

    result = runner._consume_result_report(
        Reservation(), ResultReportConfig(format="vitest-json", path="report.json")
    )

    assert result is None


def test_worktree_integrity_uses_the_named_fallback_when_project_is_outside_repo(
    tmp_path, monkeypatch
):
    repo_top = tmp_path / "repository"
    project_root = tmp_path / "outside-project"
    repo_top.mkdir()
    project_root.mkdir()
    monkeypatch.setattr(runner.git, "dirty_paths", lambda *_a, **_k: ("other.py",))

    with pytest.raises(AssayError) as caught:
        runner._resolve_snapshot_worktree_integrity(
            repo=repo_top,
            repo_top=repo_top,
            project_root=project_root,
            dirty_ignore=(),
            allow_dirty=False,
        )

    assert caught.value.reason_code is ReasonCode.DIRTY_TREE


def test_direct_r0_plan_refusal_emits_a_complete_verdict(git_repo: GitRepo):
    git_repo.write("README.md", "committed\n")
    commit = git_repo.commit_all("seed direct plan refusal")
    lane = make_lane(infrastructure={"image": "invented:deploy.image"})

    verdict = runner.run_lane(
        lane,
        commit=commit,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="7.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert verdict.argv_declared == lane.argv
    assert verdict.argv_effective == lane.argv
    assert verdict.claims[0].detail is not None
    assert verdict.claims[0].reason_code is ReasonCode.BAD_LANE_CONFIG


def test_direct_r0_execution_refusal_without_result_emits_a_complete_verdict(
    git_repo: GitRepo, monkeypatch
):
    git_repo.write("README.md", "committed\n")
    commit = git_repo.commit_all("seed direct execution refusal")
    failure = AssayError(
        "execution plan refused before it returned a result",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.EXEC_FAILED,
    )

    def refuse_before_result(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(runner, "execute_plan", refuse_before_result)
    verdict = runner.run_lane(
        make_lane(argv=("check",)),
        commit=commit,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="7.1.0",
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.EXEC_FAILED
    assert verdict.claims[0].detail == str(failure)


def test_snapshot_preparation_oserror_becomes_a_named_refusal(
    git_repo: GitRepo, monkeypatch
):
    git_repo.write("src/mod.py", "value = 1\n")
    base = git_repo.commit_all("seed higher-rigor source")
    git_repo.write("src/mod.py", "value = 2\n")
    head = git_repo.commit_all("change higher-rigor source")
    judge = make_r1_judge(source_root_paths=(git_repo.path / "src",), base=base)
    lane = make_lane(rigor=("R0", "R1"), judge=judge)

    def fail_snapshot(*_args, **_kwargs):
        raise OSError("snapshot mount disappeared")

    monkeypatch.setattr(runner.isolation, "prepare_snapshot", fail_snapshot)
    diagnostics = StringIO()
    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        diagnostics=diagnostics,
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.GIT_FAILED
    assert "snapshot preparation or cleanup failed" in diagnostics.getvalue()


def test_cleanup_oserror_replaces_the_highest_completed_rigor_claim(
    git_repo: GitRepo, monkeypatch
):
    head, lane = _seed_r1_lane(git_repo)
    real_prepare = runner.isolation.prepare_snapshot

    @contextmanager
    def prepare_then_fail_on_cleanup(spec, *, timeout):
        with real_prepare(spec, timeout=timeout) as prepared:
            yield prepared
        raise OSError("snapshot cleanup failed after the lane completed")

    monkeypatch.setattr(
        runner.isolation, "prepare_snapshot", prepare_then_fail_on_cleanup
    )
    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        process_runner=_write_coverage,
    )

    assert verdict.claims[0].status is Outcome.PASS
    assert verdict.claims[1].status is Outcome.ERROR
    assert verdict.claims[1].reason_code is ReasonCode.GIT_FAILED
    assert "cleanup failed after the lane completed" in verdict.claims[1].detail


def test_snapshot_r1_base_identity_mismatch_is_refused(
    git_repo: GitRepo, monkeypatch
):
    head, lane = _seed_r1_lane(git_repo)
    real_evaluate = runner.evaluate_r1

    def disagree_with_resolved_base(*args, on_base_resolved=None, **kwargs):
        return real_evaluate(
            *args,
            on_base_resolved=lambda _resolved: on_base_resolved("f" * 40),
            **kwargs,
        )

    monkeypatch.setattr(runner, "evaluate_r1", disagree_with_resolved_base)
    diagnostics = StringIO()
    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        process_runner=_write_coverage,
        diagnostics=diagnostics,
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.GIT_FAILED
    assert "differs from the one resolved against the consumer repository" in (
        diagnostics.getvalue()
    )


def test_uncovered_line_canary_records_a_broken_r1_control_without_running_it(
    git_repo: GitRepo, monkeypatch
):
    head, lane = _seed_r1_lane(
        git_repo,
        rigor=("R0", "R1", "R3"),
        canary=CanaryConfig(mechanism="uncovered-line", target="src/mod.py"),
    )

    def refused_r1(_lane, *, resolved_base, on_base_resolved, **_kwargs):
        on_base_resolved(resolved_base)
        return Claim(
            rigor="R1",
            source="computed",
            status=Outcome.ERROR,
            verified_by_assay=True,
            reason_code=ReasonCode.GIT_FAILED,
        )

    monkeypatch.setattr(runner, "evaluate_r1", refused_r1)
    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        process_runner=_write_coverage,
    )

    r3 = next(claim for claim in verdict.claims if claim.rigor == "R3")
    assert r3.canary is not None
    assert r3.canary.attempts[0].control_outcome is Outcome.ERROR
    assert r3.canary.attempts[0].transformed_outcome is None
    assert "baseline R1 coverage measurement did not PASS" in (
        r3.canary.attempts[0].description
    )


def test_whole_mutation_targets_deduplicate_and_refuse_invalid_scope(
    git_repo: GitRepo, tmp_path
):
    git_repo.write("src/mod.py", "value = True\n")
    git_repo.write("src/tests/test_mod.py", "value = False\n")
    git_repo.write("src/package/keep.py", "")
    git_repo.commit_all("seed whole-target validation paths")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        arguments = {
            "prepared": prepared,
            "snapshot_repo_top": git_repo.path,
            "project_prefix": PurePosixPath("."),
            "deadline": make_deadline(),
            "adapter": PythonAdapter(),
            "source_root_paths": (git_repo.path / "src",),
        }
        targets = runner._mutation_targets_whole(
            **arguments, targets=("src/mod.py", "src/mod.py")
        )
        assert tuple(target.path for target in targets) == ("src/mod.py",)

        with pytest.raises(AssayError, match="outside judge.source_roots"):
            runner._mutation_targets_whole(**arguments, targets=("README.md",))
        with pytest.raises(AssayError, match="never a directory"):
            runner._mutation_targets_whole(**arguments, targets=("src/package",))

        excluded_adapter = SimpleNamespace(
            excluded_dir_names=frozenset({"tests"}),
            source_globs=PythonAdapter().source_globs,
            is_test_path=lambda _path: False,
        )
        with pytest.raises(AssayError, match="excluded directory"):
            runner._mutation_targets_whole(
                **{**arguments, "adapter": excluded_adapter},
                targets=("src/tests/test_mod.py",),
            )


def test_snapshot_unit_refuses_a_symlink_as_its_declared_cwd(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    external_cwd = tmp_path / "external"
    external_cwd.mkdir()
    (project_root / "workspace").symlink_to(external_cwd, target_is_directory=True)
    snapshot = SimpleNamespace(
        project_root=project_root,
        tracked_directories=frozenset({PurePosixPath("workspace")}),
        commit="a" * 40,
    )

    with pytest.raises(AssayError, match="is a SYMLINK in the snapshot") as caught:
        runner._execute_snapshot_unit(
            plan=make_plan(make_lane(cwd="workspace")),
            snapshot=snapshot,
            deadline=make_deadline(),
            wants_coverage=False,
            coverage_artifact=None,
            coverage_format=None,
            coverage_producer=None,
            process_runner=lambda *_a, **_k: subprocess.CompletedProcess([], 0),
            clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
        )

    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG


@pytest.mark.parametrize(
    (
        "tracked_name",
        "kill_signal_artifact",
        "wants_coverage",
        "equivalence_artifact",
    ),
    [
        ("tracked-report.json", None, True, "new-equivalence.json"),
        (
            "tracked-signal.json",
            "tracked-signal.json",
            True,
            "new-equivalence.json",
        ),
        ("tracked-report.json", None, False, None),
    ],
)
def test_tracked_mutation_artifact_refusal_closes_every_live_reservation(
    git_repo: GitRepo,
    tmp_path,
    monkeypatch,
    tracked_name: str,
    kill_signal_artifact: str | None,
    wants_coverage: bool,
    equivalence_artifact: str | None,
):
    git_repo.write("src/mod.py", "value = True\n")
    git_repo.write("tracked-report.json", "{}\n")
    git_repo.write("tracked-signal.json", "{}\n")
    git_repo.commit_all("seed tracked mutation artifacts")
    reservations = []

    class Reservation:
        def __init__(self):
            self.closed = False
            self.armed = False

        def arm(self):
            self.armed = True

        def close(self):
            self.closed = True

    def reserve(*_args, **_kwargs):
        reservation = Reservation()
        reservations.append(reservation)
        return reservation

    monkeypatch.setattr(runner.safeio, "reserve_output", reserve)
    scratch = tmp_path / tracked_name
    scratch.mkdir()
    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            with pytest.raises(AssayError, match="tracked by git") as caught:
                runner._execute_snapshot_unit(
                    plan=make_plan(make_lane()),
                    snapshot=snapshot,
                    deadline=make_deadline(),
                    wants_coverage=wants_coverage,
                    coverage_artifact=(
                        "new-coverage.json" if wants_coverage else None
                    ),
                    coverage_format=(
                        "coverage-py-json" if wants_coverage else None
                    ),
                    coverage_producer=None,
                    process_runner=lambda *_a, **_k: pytest.fail(
                        "tracked artifacts must refuse before execution"
                    ),
                    clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
                    create_missing_parents=True,
                    equivalence_artifact=equivalence_artifact,
                    kill_signal_artifact=kill_signal_artifact,
                    mutation_artifact=(
                        "tracked-report.json"
                        if kill_signal_artifact is None
                        else "new-report.json"
                    ),
                )

    assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    expected_reservations = 3 if wants_coverage else 1
    assert len(reservations) == expected_reservations
    assert all(item.closed for item in reservations)
    if kill_signal_artifact is not None:
        # The tracked kill signal is checked only after every output
        # destination has been reserved and armed.
        assert all(item.armed for item in reservations)
    elif wants_coverage:
        # The tracked mutation report is rejected immediately after its
        # reservation is created, before that destination is armed.
        assert [item.armed for item in reservations] == [True, True, False]
    else:
        # With no coverage or equivalence output, only the report reservation
        # exists and it is rejected before arming.
        assert [item.armed for item in reservations] == [False]


def test_mutation_report_reservation_read_error_is_retained(
    git_repo: GitRepo, tmp_path, monkeypatch
):
    git_repo.write("src/mod.py", "value = True\n")
    git_repo.commit_all("seed mutation report read")
    expected = AssayError(
        "reserved mutation report changed",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )

    class Reservation:
        closed = False

        def arm(self):
            pass

        def consume(self):
            raise expected

        def close(self):
            self.closed = True

    reservation = Reservation()
    monkeypatch.setattr(
        runner.safeio, "reserve_output", lambda *_a, **_k: reservation
    )
    monkeypatch.setattr(runner.git, "dirty_paths", lambda *_a, **_k: ())

    scratch = tmp_path / "snapshot"
    scratch.mkdir()
    with prepared_snapshot(git_repo, scratch_root=scratch) as prepared:
        with prepared.materialize(timeout=60) as snapshot:
            plan = make_plan(make_lane(argv=("/bin/sh", "-c", "exit 0")))
            monkeypatch.setattr(
                runner.git, "head_rev", lambda *_a, **_k: snapshot.commit
            )
            result = runner._execute_snapshot_unit(
                plan=plan,
                snapshot=snapshot,
                deadline=make_deadline(),
                wants_coverage=False,
                coverage_artifact=None,
                coverage_format=None,
                coverage_producer=None,
                process_runner=runner.default_process_runner,
                clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
                create_missing_parents=True,
                mutation_artifact="mutation-report.json",
            )

    assert result.mutation_report_error is expected
    assert reservation.closed


def test_non_utf8_ingested_mutation_report_is_a_named_artifact_refusal():
    lane = make_lane()

    with pytest.raises(AssayError, match="mutation report is not valid UTF-8") as caught:
        runner._ingest_r2_report(
            b"\xff",
            lane=lane,
            relocated_lane=lane,
            plan=make_plan(lane),
            snapshot=None,
            prepared=None,
            deadline=make_deadline(),
            added=None,
            project_prefix=PurePosixPath("."),
        )

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


@pytest.mark.parametrize("has_diagnostics", [True, False])
def test_whole_target_r2_refusal_names_the_lane_and_resolution_step(
    git_repo: GitRepo, has_diagnostics: bool
):
    head, lane = _seed_r2_lane(git_repo, targets=("README.md",), argv=("check",))
    diagnostics = StringIO() if has_diagnostics else None

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0, stdout="", stderr=""
        ),
        diagnostics=diagnostics,
    )

    assert verdict.outcome is Outcome.ERROR
    assert verdict.claims[-1].reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "outside judge.source_roots" in (verdict.claims[-1].detail or "")
    if diagnostics is not None:
        assert "in lane 'package', resolving R2 whole-target mutation targets" in (
            diagnostics.getvalue()
        )


@pytest.mark.parametrize(
    ("cold_start", "complete", "message"),
    [
        (True, False, "v12 cold start"),
        (False, False, "not a complete unsharded native campaign"),
        (False, True, None),
    ],
)
def test_reuse_execution_reports_the_loaded_prior_campaign_state(
    git_repo: GitRepo,
    monkeypatch,
    cold_start: bool,
    complete: bool,
    message: str | None,
):
    from assay import reuse

    head, lane = _seed_r2_lane(git_repo, argv=("python", "-m", "pytest", "-q"))
    source = SimpleNamespace(
        schema_version=12,
        cold_start=cold_start,
        complete_unsharded_native=complete,
    )
    monkeypatch.setattr(reuse, "load_reuse_source", lambda _path: source)
    monkeypatch.setattr(reuse, "eligible_witnesses", lambda *_a, **_k: {})
    monkeypatch.setattr(
        runner.mutation,
        "run_mutation",
        lambda **_kwargs: runner.mutation.UNSUPPORTED,
    )
    diagnostics = StringIO()

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0, stdout="", stderr=""
        ),
        reuse_from="prior-verdict.json",
        diagnostics=diagnostics,
    )

    assert verdict.claims[-1].reason_code is ReasonCode.MUTATION_UNSUPPORTED
    if message is None:
        assert "--reuse-from" not in diagnostics.getvalue()
    else:
        assert message in diagnostics.getvalue()


@pytest.mark.parametrize("has_diagnostics", [True, False])
def test_reuse_execution_refuses_witness_replay_when_snapshot_pytest_config_is_parallel(
    git_repo: GitRepo, monkeypatch, has_diagnostics: bool
):
    from assay import reuse

    head, lane = _seed_r2_lane(
        git_repo,
        argv=("python", "-m", "pytest", "-q"),
        pytest_config="[pytest]\naddopts = -n 2\n",
    )
    source = SimpleNamespace(cold_start=False, complete_unsharded_native=True)
    monkeypatch.setattr(reuse, "load_reuse_source", lambda _path: source)
    monkeypatch.setattr(reuse, "eligible_witnesses", lambda *_a, **_k: pytest.fail(
        "parallel pytest must not replay a witness"
    ))
    monkeypatch.setattr(
        runner.mutation,
        "run_mutation",
        lambda **_kwargs: runner.mutation.UNSUPPORTED,
    )
    diagnostics = StringIO() if has_diagnostics else None

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0, stdout="", stderr=""
        ),
        reuse_from="prior-verdict.json",
        diagnostics=diagnostics,
    )

    assert verdict.claims[-1].reason_code is ReasonCode.MUTATION_UNSUPPORTED
    if diagnostics is not None:
        assert "witness replay is unsupported" in diagnostics.getvalue()


def test_reuse_source_read_refusal_is_rendered_as_a_complete_verdict(
    git_repo: GitRepo, monkeypatch
):
    from assay import reuse

    head, lane = _seed_r2_lane(git_repo, argv=("check",))
    failure = AssayError(
        "prior verdict is unreadable",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
    monkeypatch.setattr(
        reuse,
        "load_reuse_source",
        lambda _path: (_ for _ in ()).throw(failure),
    )
    diagnostics = StringIO()

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        reuse_from="bad-prior.json",
        diagnostics=diagnostics,
    )

    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "prior verdict is unreadable" in diagnostics.getvalue()


def test_reuse_cannot_be_combined_with_a_shard(git_repo: GitRepo, tmp_path):
    head, lane = _seed_r2_lane(git_repo, argv=("check",))
    diagnostics = StringIO()

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        reuse_from=tmp_path / "unused-prior.json",
        shard="0/1",
        diagnostics=diagnostics,
    )

    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "cannot be combined with --shard" in diagnostics.getvalue()


def test_unsupported_native_r2_result_keeps_a_payload_free_claim(
    git_repo: GitRepo, monkeypatch
):
    head, lane = _seed_r2_lane(git_repo, argv=("check",))
    monkeypatch.setattr(
        runner.mutation,
        "run_mutation",
        lambda **_kwargs: runner.mutation.UNSUPPORTED,
    )

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="7.1.0",
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0, stdout="", stderr=""
        ),
    )

    r2 = next(claim for claim in verdict.claims if claim.rigor == "R2")
    assert r2.reason_code is ReasonCode.MUTATION_UNSUPPORTED
    assert r2.mutation is None
    assert verdict.judgment.r2 is not None
    assert verdict.judgment.r2.producer == "native"


def test_reuse_refusal_covers_a_lane_without_native_r2_policy(git_repo: GitRepo, tmp_path):
    git_repo.write("README.md", "committed\n")
    head = git_repo.commit_all("seed non-R2 reuse refusal")
    lane = make_lane(argv=("check",))
    diagnostics = StringIO()

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="7.1.0",
        reuse_from=tmp_path / "unused-prior.json",
        diagnostics=diagnostics,
    )

    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "requires a native R2 mutation lane" in diagnostics.getvalue()


def test_reuse_refusal_covers_an_ingested_r2_policy(git_repo: GitRepo, tmp_path):
    git_repo.write("src/mod.js", "export const flag = true;\n")
    head = git_repo.commit_all("seed ingested R2 reuse refusal")
    judge = make_r2_judge(
        language="javascript",
        source_root_paths=(git_repo.path / "src",),
        base=head,
        mutation=MutationConfig(
            format="mutation-report-json",
            artifact="report.json",
            fail_under=100.0,
        ),
    )
    lane = make_lane(
        rigor=("R0", "R2"), judge=replace(judge, mode="whole_target", targets=("src/mod.js",), base=None)
    )
    diagnostics = StringIO()

    verdict = runner.run_lane(
        lane,
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="7.1.0",
        reuse_from=tmp_path / "unused-prior.json",
        diagnostics=diagnostics,
    )

    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "requires a native R2 mutation lane" in diagnostics.getvalue()


def test_malformed_shard_returns_a_complete_direct_r0_verdict(
    git_repo: GitRepo,
):
    git_repo.write("README.md", "committed\n")
    head = git_repo.commit_all("seed malformed shard refusal")

    verdict = runner.run_lane(
        make_lane(argv=("check",)),
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="7.1.0",
        shard="not-a-shard",
    )

    assert verdict.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert verdict.claims[0].detail is not None
    assert "not a shard spec" in verdict.claims[0].detail


def test_direct_r0_post_command_refusal_keeps_the_completed_result(
    git_repo: GitRepo, monkeypatch
):
    git_repo.write("README.md", "committed\n")
    head = git_repo.commit_all("seed post-command refusal")
    failure = AssayError(
        "post-command dirty comparison failed",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.GIT_FAILED,
    )
    observations = 0

    def dirty_paths(*_args, **_kwargs):
        nonlocal observations
        observations += 1
        if observations == 1:
            return ()
        raise failure

    monkeypatch.setattr(runner.git, "dirty_paths", dirty_paths)
    verdict = runner.run_lane(
        make_lane(argv=("check",)),
        commit=head,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="7.1.0",
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0, stdout="", stderr=""
        ),
    )

    assert observations == 2
    assert verdict.outcome is Outcome.ERROR
    assert verdict.claims[0].reason_code is ReasonCode.GIT_FAILED
    assert verdict.argv_effective == ("check",)
    assert verdict.result_stdout_tail is None


def test_direct_r0_lane_can_own_its_progress_artifact(git_repo: GitRepo, tmp_path):
    git_repo.write("assay.toml", "committed lane policy\n")
    git_repo.commit_all("commit the direct-run fixture")
    progress = tmp_path / "direct-progress.jsonl"

    verdict = runner.run_lane(
        make_lane(argv=("/bin/sh", "-c", "exit 0")),
        commit=git_repo.head(),
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=None,
        assay_version="7.1.0",
        deadline=make_deadline(),
        process_runner=lambda argv, *, env, cwd, timeout: subprocess.CompletedProcess(
            list(argv), returncode=0
        ),
        clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
        progress_artifact=progress,
    )

    assert verdict.outcome is Outcome.PASS
    records = [
        json.loads(line)
        for line in progress.read_text(encoding="utf-8").splitlines()
    ]
    assert records[0]["event"] == "run"
