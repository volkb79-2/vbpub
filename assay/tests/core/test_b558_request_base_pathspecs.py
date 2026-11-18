"""B558: request-base diffs are limited to the lane's judged paths.

The guard-exceeding fixtures keep the repository small enough for ordinary
tests: the real 64 MiB Git-output bound is monkeypatched to 64 KiB and the
out-of-root file is 128 KiB. The unlimited control is expected to refuse;
the limited diff is expected to reach exactly the in-root candidates.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import (
    FakeAdapter,
    GitRepo,
    completed_process,
    make_lane,
    make_r1_judge,
    make_r2_judge,
    write_coverage_json,
)

from assay import cli, git, runner
from assay.adapters.python import PythonAdapter
from assay.config import MutationConfig
from assay.diff import parse_added_lines
from assay.errors import AssayError, Outcome, ReasonCode

SMALL_GUARD = 64 * 1024
LARGE_OUT_OF_ROOT_BYTES = 128 * 1024


def _seed_guard_exceeding_change(
    repo: GitRepo, *, source: str, base_text: str, head_text: str
) -> str:
    """Commit the source at *base_text*, then change it to *head_text* and
    add an out-of-root file larger than the monkeypatched output guard."""
    repo.write(".gitignore", "cov.json\n.assay/\n")
    repo.write(source, base_text)
    base = repo.commit_all("seed the in-root source")
    repo.write(source, head_text)
    repo.write("outside/huge.txt", "x" * LARGE_OUT_OF_ROOT_BYTES + "\n")
    repo.commit_all("small in-root edit and oversized out-of-root addition")
    return base


def _assert_unlimited_diff_exceeds_the_guard(
    repo: GitRepo, base: str, head: str
) -> None:
    with pytest.raises(AssayError) as excinfo:
        git.run(repo.path, "diff", "--unified=0", base, head)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert str(SMALL_GUARD) in str(excinfo.value)


def _r1_lane(repo: GitRepo):
    judge = make_r1_judge(source_root_paths=(repo.path / "src",))
    return make_lane(rigor=("R0", "R1"), judge=judge)


def test_r1_coverage_diff_is_limited_before_the_output_guard(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """R1's ``evaluate_r1`` diff call site succeeds with the oversized
    out-of-root change and judges only the one in-root source."""
    base = _seed_guard_exceeding_change(
        git_repo,
        source="src/mod.zzz",
        base_text="BASE\n",
        head_text="BASE\nLINE2\n",
    )
    write_coverage_json(
        git_repo.path / "cov.json",
        {"src/mod.zzz": {"executed_lines": [1, 2]}},
    )
    lane = _r1_lane(git_repo)
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", SMALL_GUARD)
    _assert_unlimited_diff_exceeds_the_guard(git_repo, base, git_repo.head())

    with monkeypatch.context() as patched:
        patched.setattr(
            runner,
            "_request_base_diff_pathspecs",
            lambda *args, **kwargs: (),
        )
        refused = runner.evaluate_r1(
            lane,
            repo=git_repo.path,
            project_root=git_repo.path,
            base=base,
            adapter=FakeAdapter(),
        )
    assert refused.status is Outcome.ERROR
    assert refused.reason_code is ReasonCode.GIT_FAILED

    accepted = runner.evaluate_r1(
        lane,
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base,
        adapter=FakeAdapter(),
    )
    assert accepted.status is Outcome.PASS
    assert accepted.coverage is not None
    assert accepted.coverage.considered == 1


def _r2_lane(repo: GitRepo):
    mutation = MutationConfig(
        max_mutants=50,
        operators=("python:compare-swap",),
    )
    judge = make_r2_judge(
        source_root_paths=(repo.path / "src",),
        base=None,
        mutation=mutation,
    )
    judge = replace(judge, base_source="request")
    return make_lane(rigor=("R0", "R2"), judge=judge, argv=("check",))


def _kill_when_original_source_survives():
    def decide(argv, *, env, cwd, timeout):
        text = (Path(cwd) / "src" / "mod.py").read_text(encoding="utf-8")
        return completed_process(
            list(argv),
            returncode=0 if "x > 0" in text else 1,
            stdout="",
            stderr="",
        )

    return decide


def test_assay_run_r2_diff_is_limited_before_the_output_guard(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """The R2-without-R1 baseline-snapshot diff call site reaches its one
    compare-swap candidate instead of refusing the oversized full diff."""
    base = _seed_guard_exceeding_change(
        git_repo,
        source="src/mod.py",
        base_text="def f(x):\n    return 0\n",
        head_text="def f(x):\n    return x > 0\n",
    )
    head = git_repo.head()
    lane = _r2_lane(git_repo)
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", SMALL_GUARD)
    _assert_unlimited_diff_exceeds_the_guard(git_repo, base, head)

    def run():
        return runner.run_lane(
            lane,
            commit=head,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=PythonAdapter(),
            assay_version="0.1.0",
            process_runner=_kill_when_original_source_survives(),
            request_base=base,
        )

    with monkeypatch.context() as patched:
        patched.setattr(
            runner,
            "_request_base_diff_pathspecs",
            lambda *args, **kwargs: (),
        )
        refused = run()
    assert refused.outcome is Outcome.ERROR
    assert refused.reason_code is ReasonCode.GIT_FAILED

    accepted = run()
    r2 = next(claim for claim in accepted.claims if claim.rigor == "R2")
    assert accepted.outcome is Outcome.PASS
    assert r2.status is Outcome.PASS
    assert r2.mutation is not None
    assert r2.mutation.total == 1
    assert len(r2.mutation.killed) == 1


def _r2_lane_text() -> str:
    return """\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "true"]
env = {}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base_source = "request"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["python:compare-swap"]
"""


def _cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_assay_plan_diff_is_limited_before_the_output_guard(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """The CLI plan discovery diff call site plans the one in-root
    compare-swap candidate despite the oversized out-of-root addition."""
    base = _seed_guard_exceeding_change(
        git_repo,
        source="src/mod.py",
        base_text="def f(x):\n    return 0\n",
        head_text="def f(x):\n    return x > 0\n",
    )
    lane_file = git_repo.write("assay.toml", _r2_lane_text())
    git_repo.commit_all("declare the request-base R2 lane")
    monkeypatch.setattr(git, "MAX_GIT_OUTPUT_BYTES", SMALL_GUARD)
    _assert_unlimited_diff_exceeds_the_guard(git_repo, base, git_repo.head())
    argv = [
        "plan",
        "--json",
        "package",
        "--file",
        str(lane_file),
        "--request-base",
        base,
    ]

    with monkeypatch.context() as patched:
        patched.setattr(
            runner,
            "_request_base_diff_pathspecs",
            lambda *args, **kwargs: (),
        )
        code, _out, err = _cli(argv)
    assert code == 2
    assert "ERROR/GIT_FAILED" in err

    code, out, err = _cli(argv)
    assert code == 0, err
    payload = json.loads(out)
    assert [candidate["path"] for candidate in payload["candidates"]] == ["src/mod.py"]


def _limited_added(
    repo: GitRepo, lane, base: str, head: str
) -> dict[str, frozenset[int]]:
    text = git.run(
        repo.path,
        *runner._request_base_diff_args(
            lane,
            repo_top=repo.path,
            project_root=repo.path,
            base_rev=base,
            head_rev=head,
        ),
    )
    return dict(parse_added_lines(text).by_file)


def _unlimited_added(repo: GitRepo, base: str, head: str) -> dict[str, frozenset[int]]:
    text = git.run(
        repo.path,
        "diff",
        "--unified=0",
        "--no-renames",
        base,
        head,
    )
    return dict(parse_added_lines(text).by_file)


@pytest.mark.parametrize(
    ("case", "source_roots", "expected"),
    [
        ("inside-edit", ("src",), {"src/a.py": frozenset({2})}),
        ("outside-edit", ("src",), {}),
        ("rename-into", ("src",), {"src/new.py": frozenset({1, 2})}),
        ("rename-out", ("src",), {}),
        ("rename-within", ("src",), {"src/new.py": frozenset({1, 2})}),
        ("symlink-root", ("alias",), {"real/src/a.py": frozenset({2})}),
        ("file-root", ("src/only.py",), {"src/only.py": frozenset({2})}),
    ],
)
def test_limited_diff_matches_the_unlimited_no_renames_diff_for_judged_paths(
    git_repo: GitRepo,
    case: str,
    source_roots: tuple[str, ...],
    expected: dict[str, frozenset[int]],
):
    """Expected result per fixture:

    * ``inside-edit`` -- one added source line at ``src/a.py``;
    * ``outside-edit`` -- no judged added lines;
    * ``rename-into`` -- the whole file is added at ``src/new.py`` (lines 1
      and 2), because a path-limited ``--no-renames`` diff cannot pair the
      out-of-root source;
    * ``rename-out`` -- no added lines; the deletion is not an addition;
    * ``rename-within`` -- the whole file is added at ``src/new.py`` under
      ``--no-renames``;
    * ``symlink-root`` -- the tracked target ``real/src/a.py`` changes at
      line 2 and is reached through the declared lexical root ``alias``;
    * ``file-root`` -- only the declared exact file ``src/only.py`` changes;
      its same-directory sibling is outside the lane's judged paths.
    """
    if case == "inside-edit":
        git_repo.write("src/a.py", "one\n")
        base = git_repo.commit_all("seed inside file")
        git_repo.write("src/a.py", "one\ntwo\n")
        git_repo.commit_all("edit inside file")
    elif case == "outside-edit":
        git_repo.write("src/a.py", "one\n")
        git_repo.write("docs/a.py", "one\n")
        base = git_repo.commit_all("seed inside and outside files")
        git_repo.write("docs/a.py", "one\ntwo\n")
        git_repo.commit_all("edit outside file")
    elif case == "rename-into":
        git_repo.write("src/existing.py", "stable\n")
        git_repo.write("docs/old.py", "one\ntwo\n")
        base = git_repo.commit_all("seed outside file")
        git_repo.git("mv", "docs/old.py", "src/new.py")
        git_repo.commit_all("rename outside file into the root")
    elif case == "rename-out":
        git_repo.write("docs/existing.py", "stable\n")
        git_repo.write("src/old.py", "one\ntwo\n")
        base = git_repo.commit_all("seed inside file")
        git_repo.git("mv", "src/old.py", "docs/old.py")
        git_repo.commit_all("rename inside file out of the root")
    elif case == "rename-within":
        git_repo.write("src/old.py", "one\ntwo\n")
        base = git_repo.commit_all("seed inside file")
        git_repo.git("mv", "src/old.py", "src/new.py")
        git_repo.commit_all("rename inside the root")
    elif case == "symlink-root":
        git_repo.write("real/src/a.py", "one\n")
        (git_repo.path / "alias").symlink_to("real/src", target_is_directory=True)
        base = git_repo.commit_all("seed through a symlinked root")
        git_repo.write("real/src/a.py", "one\ntwo\n")
        git_repo.commit_all("edit through a symlinked root")
    elif case == "file-root":
        git_repo.write("src/only.py", "one\n")
        git_repo.write("src/sibling.py", "one\n")
        base = git_repo.commit_all("seed exact file and sibling")
        git_repo.write("src/only.py", "one\ntwo\n")
        git_repo.write("src/sibling.py", "one\ntwo\n")
        git_repo.commit_all("edit exact file and sibling")
    else:  # pragma: no cover - the parametrization is closed
        raise AssertionError(case)

    judge = make_r1_judge(
        source_root_paths=tuple(
            (git_repo.path / root).resolve() for root in source_roots
        )
    )
    judge = replace(judge, source_roots=source_roots)
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    head = git_repo.head()

    limited = _limited_added(git_repo, lane, base, head)
    unlimited = _unlimited_added(git_repo, base, head)

    assert limited == expected
    assert {
        path: lines for path, lines in unlimited.items() if path in expected
    } == expected
    if case == "symlink-root":
        assert runner._request_base_diff_pathspecs(
            lane, repo_top=git_repo.path, project_root=git_repo.path
        ) == ("alias", "real/src")


def test_outside_root_symlink_leaf_is_an_accepted_diff_narrowing(
    git_repo: GitRepo,
):
    """A tracked symlink leaf outside ``src`` whose target is inside ``src``
    is present in the unlimited diff but absent from the limited diff. The
    real in-root change remains identical in both."""
    git_repo.write("src/a.py", "one\n")
    base = git_repo.commit_all("seed the real in-root source")
    git_repo.write("src/a.py", "one\ntwo\n")
    (git_repo.path / "alias.py").symlink_to("src/a.py")
    head = git_repo.commit_all("edit the source and add an outside-root symlink")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "src",))
    judge = replace(judge, source_roots=("src",))
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    limited_text = git.run(
        git_repo.path,
        *runner._request_base_diff_args(
            lane,
            repo_top=git_repo.path,
            project_root=git_repo.path,
            base_rev=base,
            head_rev=head,
        ),
    )
    unlimited_text = git.run(
        git_repo.path,
        "diff",
        "--unified=0",
        "--no-renames",
        base,
        head,
    )

    assert "alias.py" in unlimited_text
    assert "alias.py" not in limited_text
    limited = dict(parse_added_lines(limited_text).by_file)
    unlimited = dict(parse_added_lines(unlimited_text).by_file)
    assert limited == {"src/a.py": frozenset({2})}
    assert limited["src/a.py"] == unlimited["src/a.py"]
    # Accepted narrowing: Git's symlink diff text is the link target
    # (``src/a.py``), not source lines, so alias.py cannot produce a real
    # mutation candidate of its own. The target is still diffed under src/a.py.


def test_no_usable_pathspec_keeps_the_full_diff_without_an_empty_separator(
    git_repo: GitRepo, tmp_path: Path
):
    """An empty root list and an outside-repository root both return the
    historical full diff argv, with no trailing ``--``."""
    outside = tmp_path / "outside-root"
    outside.mkdir()

    empty = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(source_root_paths=()),
    )
    empty = replace(
        empty,
        judge=replace(empty.judge, source_roots=(), source_root_paths=()),
    )
    foreign = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(source_root_paths=(outside,)),
    )
    foreign = replace(
        foreign,
        judge=replace(
            foreign.judge,
            source_roots=(str(outside),),
            source_root_paths=(outside,),
        ),
    )

    for lane in (empty, foreign):
        assert (
            runner._request_base_diff_pathspecs(
                lane, repo_top=git_repo.path, project_root=git_repo.path
            )
            == ()
        )
        assert runner._request_base_diff_args(
            lane,
            repo_top=git_repo.path,
            project_root=git_repo.path,
            base_rev="base",
            head_rev="head",
        ) == ("diff", "--unified=0", "base", "head")


def test_pathspec_helper_handles_odd_declarations_without_crashing(
    git_repo: GitRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Synthetic lanes can express a repository-top root, mismatched resolved
    and declared lists, an exact-file root with no resolved roots, a lexical
    root outside the repository, and a file whose resolve fails. Each shape
    follows containment rather than a string prefix or an assumption."""
    git_repo.write("src/only.py", "one\n")
    git_repo.commit_all("seed odd-shape file")

    top = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(source_root_paths=(git_repo.path,)),
    )
    top = replace(
        top,
        judge=replace(top.judge, source_roots=(".",)),
    )
    assert runner._request_base_diff_pathspecs(
        top, repo_top=git_repo.path, project_root=git_repo.path
    ) == (".",)

    mismatched = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(source_root_paths=(git_repo.path / "src",)),
    )
    mismatched = replace(
        mismatched,
        judge=replace(mismatched.judge, source_roots=()),
    )
    assert runner._request_base_diff_pathspecs(
        mismatched, repo_top=git_repo.path, project_root=git_repo.path
    ) == ("src",)

    unresolved_file = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(source_root_paths=()),
    )
    unresolved_file = replace(
        unresolved_file,
        judge=replace(
            unresolved_file.judge,
            source_roots=("src/only.py",),
            source_root_paths=(),
        ),
    )
    assert (
        runner._request_base_diff_pathspecs(
            unresolved_file,
            repo_top=git_repo.path,
            project_root=git_repo.path,
        )
        == ()
    )

    foreign_lexical = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(source_root_paths=(git_repo.path / "src",)),
    )
    foreign_lexical = replace(
        foreign_lexical,
        judge=replace(foreign_lexical.judge, source_roots=("src",)),
    )
    assert runner._request_base_diff_pathspecs(
        foreign_lexical,
        repo_top=git_repo.path,
        project_root=tmp_path / "outside-project",
    ) == ("src",)

    exact_file = make_lane(
        rigor=("R0", "R1"),
        judge=make_r1_judge(source_root_paths=(git_repo.path / "src" / "only.py",)),
    )
    exact_file = replace(
        exact_file,
        judge=replace(exact_file.judge, source_roots=("src/only.py",)),
    )
    target = git_repo.path / "src" / "only.py"
    real_resolve = Path.resolve

    def fail_for_target(path, *args, **kwargs):
        if path == target:
            raise OSError("simulated resolve failure")
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fail_for_target)
    assert runner._request_base_diff_pathspecs(
        exact_file, repo_top=git_repo.path, project_root=git_repo.path
    ) == ("src/only.py",)
