"""B556: a non-git lane OSError names its last stage and any known fd path."""

from __future__ import annotations

import errno
import os

from assay.adapters.python import PythonAdapter
from assay.errors import Outcome, ReasonCode
from conftest import make_lane, make_r1_judge

from assay import runner


def _lane(repo):
    repo.write("src/mod.py", "value = 1\n")
    base = repo.commit_all("seed B556 source")
    repo.write("src/mod.py", "value = 2\n")
    head = repo.commit_all("change B556 source")
    judge = make_r1_judge(source_root_paths=(repo.path / "src",), base=base)
    return head, make_lane(rigor=("R0", "R1"), judge=judge)


def _run(repo, lane, head):
    return runner.run_lane(
        lane,
        commit=head,
        repo=repo.path,
        project_root=repo.path,
        adapter=PythonAdapter(),
        assay_version="12.0.0",
    )


def _failure_detail(verdict):
    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.GIT_FAILED
    failing = [claim for claim in verdict.claims if claim.status is Outcome.ERROR]
    assert failing
    assert failing[-1].reason_code is ReasonCode.GIT_FAILED
    assert failing[-1].detail is not None
    return failing[-1].detail


def _fail_with(exc):
    def fail(*_args, **_kwargs):
        raise exc

    return fail


def test_int_filename_is_resolved_at_the_reporting_boundary(
    git_repo, tmp_path, monkeypatch
):
    head, lane = _lane(git_repo)
    target = tmp_path / "fd-target"
    target.write_text("x", encoding="utf-8")
    with target.open("rb") as stream:
        fd = stream.fileno()
        exc = PermissionError(errno.EPERM, os.strerror(errno.EPERM), fd)
        monkeypatch.setattr(
            runner.isolation, "prepare_snapshot", _fail_with(exc)
        )
        detail = _failure_detail(_run(git_repo, lane, head))

    assert detail == f"seed/snapshot: {exc} (fd {fd} -> {target})"


def test_str_filename_has_no_fd_note(git_repo, monkeypatch):
    head, lane = _lane(git_repo)
    exc = PermissionError(
        errno.EPERM,
        os.strerror(errno.EPERM),
        str(git_repo.path / "snapshot"),
    )
    monkeypatch.setattr(runner.isolation, "prepare_snapshot", _fail_with(exc))

    detail = _failure_detail(_run(git_repo, lane, head))

    assert detail == f"seed/snapshot: {exc}"
    assert "(fd " not in detail


def test_last_stage_entered_names_the_workspace(git_repo, monkeypatch):
    head, lane = _lane(git_repo)
    exc = PermissionError(
        errno.EACCES,
        os.strerror(errno.EACCES),
        str(git_repo.path / "workspace"),
    )
    monkeypatch.setattr(runner.workspace_api, "factory_for_lane", _fail_with(exc))

    detail = _failure_detail(_run(git_repo, lane, head))

    assert detail == f"workspace: {exc}"


def test_unresolvable_fd_names_only_the_number(git_repo, monkeypatch):
    head, lane = _lane(git_repo)
    fd = 999_999
    exc = PermissionError(errno.EPERM, os.strerror(errno.EPERM), fd)
    monkeypatch.setattr(runner.isolation, "prepare_snapshot", _fail_with(exc))

    detail = _failure_detail(_run(git_repo, lane, head))

    assert detail == f"seed/snapshot: {exc} (fd {fd}, path unresolvable)"
