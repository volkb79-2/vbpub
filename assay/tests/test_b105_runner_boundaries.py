"""Whole-source paths for runner refusal and cleanup boundaries."""

from __future__ import annotations

import subprocess
import json
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from assay import runner
from assay.adapters.python import PythonAdapter
from assay.config import ResultReportConfig
from assay.errors import AssayError, Outcome, ReasonCode
from conftest import GitRepo, make_deadline, make_lane, make_plan, prepared_snapshot


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
