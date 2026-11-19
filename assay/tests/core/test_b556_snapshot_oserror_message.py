"""B556: non-git lane OSErrors name the phase, operation, errno and paths."""

from __future__ import annotations

import errno
import json
import os
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig
from assay.errors import AssayError, Outcome, ReasonCode
from conftest import (
    GitRepo,
    completed_process,
    make_lane,
    make_r1_judge,
    make_r2_judge,
)

from assay import errors, runner


def _seed_r1_lane(repo: GitRepo):
    repo.write(".gitignore", "cov.json\n")
    repo.write("src/mod.py", "def flag(value):\n    return value >= 0\n")
    base = repo.commit_all("seed B556 source")
    repo.write("src/mod.py", "def flag(value):\n    return value > 0\n")
    head = repo.commit_all("change B556 source")
    judge = make_r1_judge(
        language="python",
        source_root_paths=(repo.path / "src",),
        base=base,
        fail_under=100.0,
        coverage_artifact="cov.json",
    )
    return head, make_lane(rigor=("R0", "R1"), judge=judge)


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
    return completed_process(list(argv), 0, stdout="", stderr="")


def test_eperm_int_filename_is_resolved_through_proc(tmp_path):
    path = tmp_path / "snapshot"
    path.mkdir()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        text = errors.describe_os_error(
            PermissionError(errno.EPERM, os.strerror(errno.EPERM), fd),
            phase="cleanup",
        )
    finally:
        os.close(fd)

    assert "phase 'cleanup'" in text
    assert "[Errno 1 EPERM]" in text
    assert "operation='Operation not permitted'" in text
    assert f"path=fd {fd} -> {str(path)!r}" in text


def test_eperm_unresolvable_fd_names_the_fd_as_unresolved(monkeypatch):
    fd = 12

    def fail_readlink(_path):
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), _path)

    monkeypatch.setattr(errors.os, "readlink", fail_readlink)

    text = errors.describe_os_error(
        PermissionError(errno.EPERM, os.strerror(errno.EPERM), fd),
        phase="cleanup",
    )

    assert f"path=fd {fd} (unresolved)" in text


def test_enoent_str_filename_and_second_filename_are_named(tmp_path):
    missing = tmp_path / "missing"
    second = tmp_path / "second"
    error = FileNotFoundError(
        errno.ENOENT,
        os.strerror(errno.ENOENT),
        str(missing),
    )
    error.filename2 = str(second)

    text = errors.describe_os_error(
        error,
        phase="artifact collection",
    )

    assert "[Errno 2 ENOENT]" in text
    assert f"path={str(missing)!r}" in text
    assert f"path2={str(second)!r}" in text


def test_eacces_bytes_filename_is_shown_unchanged():
    filename = b"/tmp/non-utf8-\xff"
    text = errors.describe_os_error(
        PermissionError(errno.EACCES, os.strerror(errno.EACCES), filename),
        phase="workspace",
    )

    assert "[Errno 13 EACCES]" in text
    assert f"path={filename!r}" in text
    assert "\xff" not in text


def test_control_characters_in_a_str_filename_are_escaped():
    filename = "/tmp/line\nbreak\x1b"

    text = errors.describe_os_error(
        PermissionError(errno.EACCES, os.strerror(errno.EACCES), filename),
        phase="workspace",
    )

    assert "\n" not in text
    assert "\x1b" not in text
    assert "path='/tmp/line\\nbreak\\x1b'" in text


def test_huge_integer_filename_falls_back_to_bounded_text():
    text = errors.describe_os_error(
        OSError(errno.EPERM, "synthetic", 10**5000),
        phase="cleanup",
    )

    assert "phase 'cleanup'" in text
    assert "synthetic" in text
    assert len(text) <= 4096


def test_filename_object_whose_str_raises_cannot_mask_the_original_error():
    class StringRefusingFilename:
        def __str__(self):
            raise ValueError("filename string conversion refused")

    text = errors.describe_os_error(
        OSError(errno.EPERM, "synthetic", StringRefusingFilename()),
        phase="cleanup",
    )

    assert "phase 'cleanup'" in text
    assert "synthetic" in text
    assert "StringRefusingFilename" in text
    assert len(text) <= 4096


def test_unknown_errno_is_named():
    text = errors.describe_os_error(
        OSError(9999, "mystery operation", "/tmp/unknown"),
        phase="lane command",
    )

    assert "[Errno 9999 ?]" in text
    assert "operation='mystery operation'" in text


def test_empty_oserror_uses_an_explicit_unknown_operation():
    text = errors.describe_os_error(OSError(), phase="cleanup")

    assert "operation='unknown error'" in text


def test_no_filename_and_no_errno_are_rendered_without_inventing_either():
    text = errors.describe_os_error(
        OSError(None, os.strerror(errno.EIO)),
        phase="seed/snapshot",
    )

    assert "[Errno None ?]" in text
    assert "operation=" in text
    assert "path=" not in text


def test_lane_oserror_keeps_the_original_cause_and_git_failed_reason():
    cause = PermissionError(errno.EPERM, os.strerror(errno.EPERM), "/tmp/source")
    wrapped = runner._lane_oserror(cause, phase="cleanup")

    assert wrapped.__cause__ is cause
    assert wrapped.outcome is Outcome.ERROR
    assert wrapped.reason_code is ReasonCode.GIT_FAILED
    assert errors.describe_os_error(cause, phase="cleanup") in str(wrapped)


def _run_lane(
    git_repo: GitRepo,
    lane,
    *,
    commit=None,
    diagnostics=None,
    process_runner=None,
    progress_stream=None,
    cold_witness=False,
):
    return runner.run_lane(
        lane,
        commit=git_repo.head() if commit is None else commit,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=PythonAdapter(),
        assay_version="12.0.0",
        diagnostics=diagnostics,
        process_runner=process_runner or _write_coverage,
        progress_stream=progress_stream,
        cold_witness=cold_witness,
    )


def _failing_detail(verdict):
    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.GIT_FAILED
    failing = [claim for claim in verdict.claims if claim.status is Outcome.ERROR]
    assert failing
    assert failing[-1].reason_code is ReasonCode.GIT_FAILED
    assert failing[-1].detail is not None
    return failing[-1].detail


def test_seed_snapshot_oserror_names_its_phase(git_repo: GitRepo, monkeypatch):
    _head, lane = _seed_r1_lane(git_repo)
    cause = PermissionError(errno.EPERM, os.strerror(errno.EPERM), git_repo.path)

    def fail_snapshot(*_args, **_kwargs):
        raise cause

    monkeypatch.setattr(runner.isolation, "prepare_snapshot", fail_snapshot)
    verdict = _run_lane(git_repo, lane)

    detail = _failing_detail(verdict)
    assert "phase 'seed/snapshot'" in detail
    assert "[Errno 1 EPERM]" in detail
    assert str(git_repo.path) in detail


def test_workspace_oserror_names_its_phase(git_repo: GitRepo, monkeypatch):
    _head, lane = _seed_r1_lane(git_repo)
    cause = PermissionError(
        errno.EPERM, os.strerror(errno.EPERM), git_repo.path / "workspace"
    )

    def fail_workspace(*_args, **_kwargs):
        raise cause

    monkeypatch.setattr(runner.workspace_api, "factory_for_lane", fail_workspace)
    verdict = _run_lane(git_repo, lane)

    detail = _failing_detail(verdict)
    assert "phase 'workspace'" in detail
    assert "[Errno 1 EPERM]" in detail
    assert "workspace" in detail


def test_liveness_tempdir_oserror_names_baseline_setup(
    git_repo: GitRepo, monkeypatch
):
    _head, lane = _seed_r1_lane(git_repo)
    cause = PermissionError(
        errno.EACCES,
        os.strerror(errno.EACCES),
        git_repo.path / "assay-liveness",
    )
    real_temporary_directory = runner.tempfile.TemporaryDirectory

    def fail_liveness_tempdir(*args, **kwargs):
        if kwargs.get("prefix") == "assay-liveness-":
            raise cause
        return real_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(runner.tempfile, "TemporaryDirectory", fail_liveness_tempdir)
    verdict = _run_lane(git_repo, lane)

    detail = _failing_detail(verdict)
    assert "phase 'baseline setup'" in detail
    assert "[Errno 13 EACCES]" in detail
    assert "assay-liveness" in detail


def test_lane_command_oserror_names_its_phase(git_repo: GitRepo, monkeypatch):
    _head, lane = _seed_r1_lane(git_repo)
    cause = FileNotFoundError(
        errno.ENOENT, os.strerror(errno.ENOENT), git_repo.path / "lane-command"
    )

    def fail_command(*_args, **_kwargs):
        raise cause

    monkeypatch.setattr(runner, "execute_plan", fail_command)
    verdict = _run_lane(git_repo, lane)

    detail = _failing_detail(verdict)
    assert "phase 'lane command'" in detail
    assert "[Errno 2 ENOENT]" in detail
    assert "lane-command" in detail


def test_artifact_collection_oserror_names_its_phase(git_repo: GitRepo, monkeypatch):
    _head, lane = _seed_r1_lane(git_repo)
    cause = PermissionError(
        errno.EACCES, os.strerror(errno.EACCES), b"/snapshot/cov.json"
    )

    def fail_parse(*_args, **_kwargs):
        raise cause

    monkeypatch.setattr(runner.coverage, "parse_coverage_artifact", fail_parse)
    verdict = _run_lane(git_repo, lane)

    detail = _failing_detail(verdict)
    assert "phase 'artifact collection'" in detail
    assert "[Errno 13 EACCES]" in detail
    assert "b'/snapshot/cov.json'" in detail


def test_descendant_handling_oserror_names_its_phase(git_repo: GitRepo, monkeypatch):
    _head, lane = _seed_r1_lane(git_repo)
    cause = PermissionError(
        errno.EPERM, os.strerror(errno.EPERM), git_repo.path / "descendant"
    )

    def fail_descendants(*_args, **_kwargs):
        raise cause

    monkeypatch.setattr(runner, "_announce_r0_pressure", fail_descendants)
    verdict = _run_lane(git_repo, lane)

    detail = _failing_detail(verdict)
    assert "phase 'descendant handling'" in detail
    assert "[Errno 1 EPERM]" in detail
    assert "descendant" in detail


def test_cleanup_oserror_names_its_phase(git_repo: GitRepo, monkeypatch):
    _head, lane = _seed_r1_lane(git_repo)
    real_prepare = runner.isolation.prepare_snapshot
    cause = PermissionError(
        errno.EPERM, os.strerror(errno.EPERM), 12
    )

    @contextmanager
    def prepare_then_fail_on_cleanup(spec, *, timeout, workspace):
        with real_prepare(spec, timeout=timeout, workspace=workspace) as prepared:
            yield prepared
        raise cause

    monkeypatch.setattr(
        runner.isolation, "prepare_snapshot", prepare_then_fail_on_cleanup
    )
    verdict = _run_lane(git_repo, lane)

    detail = _failing_detail(verdict)
    assert "phase 'cleanup'" in detail
    assert "[Errno 1 EPERM]" in detail
    assert "path=fd 12 " in detail


def test_genuine_git_assayerror_message_is_byte_identical(
    git_repo: GitRepo, monkeypatch
):
    _head, lane = _seed_r1_lane(git_repo)
    message = "git rev-parse HEAD failed (128): fatal: synthetic Git failure"
    failure = AssayError(
        message,
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.GIT_FAILED,
    )

    def fail_snapshot(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(runner.isolation, "prepare_snapshot", fail_snapshot)
    diagnostics = StringIO()
    verdict = _run_lane(git_repo, lane, diagnostics=diagnostics)

    detail = _failing_detail(verdict)
    assert detail == message
    assert diagnostics.getvalue().endswith(f"ERROR/GIT_FAILED: {message}\n")


def test_a_raw_file_not_found_from_the_git_spawn_path_is_formatted(
    git_repo: GitRepo, monkeypatch
):
    head, lane = _seed_r1_lane(git_repo)
    missing_interpreter = git_repo.path / "git-wrapper"
    cause = FileNotFoundError(
        errno.ENOENT,
        os.strerror(errno.ENOENT),
        str(missing_interpreter),
    )

    def fail_spawn(*_args, **_kwargs):
        raise cause

    monkeypatch.setattr(runner.git, "_p22_spawn", fail_spawn)
    verdict = _run_lane(git_repo, lane, commit=head)

    detail = _failing_detail(verdict)
    assert verdict.exit_code == Outcome.ERROR.exit_code
    assert "phase 'seed/snapshot'" in detail
    assert "[Errno 2 ENOENT]" in detail
    assert f"path={str(missing_interpreter)!r}" in detail


def test_cold_witness_second_materialize_oserror_names_workspace(
    git_repo: GitRepo, monkeypatch
):
    git_repo.write("src/mod.py", "def flag(value):\n    return 0\n")
    base = git_repo.commit_all("seed R2 source")
    git_repo.write("src/mod.py", "def flag(value):\n    return value > 0\n")
    head = git_repo.commit_all("change R2 source")
    judge = make_r2_judge(
        source_root_paths=(git_repo.path / "src",),
        base=base,
        mutation=MutationConfig(
            max_mutants=50,
            operators=("python:compare-swap",),
            liveness="false",
        ),
    )
    lane = make_lane(
        rigor=("R0", "R2"),
        judge=judge,
        argv=("pytest", "tests", "-q"),
    )
    cause = PermissionError(
        errno.EPERM,
        os.strerror(errno.EPERM),
        git_repo.path / "second-workspace",
    )
    real_materialize = runner.isolation.SnapshotRepository.materialize
    calls = 0

    def fail_on_second_materialize(self, *, timeout):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise cause
        return real_materialize(self, timeout=timeout)

    monkeypatch.setattr(
        runner.isolation.SnapshotRepository,
        "materialize",
        fail_on_second_materialize,
    )
    monkeypatch.setattr(
        runner,
        "inject_witness_plugin",
        lambda plan, **_kwargs: SimpleNamespace(active=True, plan=plan),
    )
    monkeypatch.setattr(
        runner,
        "receipt_facts",
        lambda _receipt: SimpleNamespace(
            duplicates=0,
            runtime_fingerprint_sha256="a" * 64,
            collection_count=1,
            collection_sha256="b" * 64,
            hook_fingerprint_sha256="c" * 64,
            hook_count=0,
        ),
    )
    monkeypatch.setattr(runner, "survivor_proof_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "supports_sequential_pytest", lambda *_a, **_k: True)
    monkeypatch.setattr(
        runner.mutation, "supports_sequential_pytest", lambda *_a, **_k: True
    )
    diagnostics = StringIO()
    verdict = _run_lane(
        git_repo,
        lane,
        commit=head,
        diagnostics=diagnostics,
        process_runner=lambda argv, **_kwargs: completed_process(
            list(argv), 0, stdout="", stderr=""
        ),
        cold_witness=True,
    )

    assert verdict.outcome is Outcome.ERROR, diagnostics.getvalue()
    assert verdict.reason_code is ReasonCode.GIT_FAILED, diagnostics.getvalue()
    detail = _failing_detail(verdict)
    assert calls == 2
    assert "phase 'workspace'" in detail
    assert "[Errno 1 EPERM]" in detail
    assert "second-workspace" in detail


def test_snapshot_materialized_progress_oserror_names_progress_emission(
    git_repo: GitRepo,
):
    _head, lane = _seed_r1_lane(git_repo)
    cause = PermissionError(
        errno.EPERM,
        os.strerror(errno.EPERM),
        git_repo.path / "progress",
    )

    class FailingProgress:
        def emit(self, _event):
            raise cause

    verdict = _run_lane(git_repo, lane, progress_stream=FailingProgress())

    detail = _failing_detail(verdict)
    assert "phase 'progress emission'" in detail
    assert "[Errno 1 EPERM]" in detail
    assert "progress" in detail
