"""B245 U5b (D7): the changed-lines diff handed to cargo-mutants through the
``{assay.changed_diff}`` argv placeholder, end to end through ``run_lane``.

The fake process runner records the argv it receives and reads the handoff file
while the "command" runs. Every verdict a test produces is round-tripped through
``verify_document`` (``_run`` in the shared harness): a runner state that yields
a verdict ``verify`` rejects is a bug.
"""

from __future__ import annotations

import errno
import hashlib
import stat
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import GitRepo, why_invalid
from test_runner_cargo_mutants_r2 import (
    Fake,
    OUT,
    _case,
    _claim,
    _exit_code,
    _lane,
    _run,
    _seed,
    _stage,
)

from assay import mutation, runner
from assay.diff import AddedLines
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verdict import PRODUCER_VERSION_UNOBSERVED
from assay.verify import verify_document
from assay.vocabulary import ARGV_PLACEHOLDER_CHANGED_DIFF as PLACEHOLDER

ARGV = ("cargo", "mutants", "--output", OUT, "--in-diff", PLACEHOLDER)


def _with_placeholder(lane):
    return replace(lane, argv=ARGV)


class Recording(Fake):
    """A :class:`Fake` that records the argv and the handoff file's state."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.argv: tuple[str, ...] | None = None
        self.path: Path | None = None
        self.content: bytes | None = None
        self.mode: int | None = None
        self.dir_mode: int | None = None
        self.cwd: Path | None = None

    def __call__(self, argv, *, env, cwd, timeout):
        self.argv = tuple(argv)
        self.cwd = Path(cwd)
        self.path = Path(argv[argv.index("--in-diff") + 1])
        self.content = self.path.read_bytes()
        self.mode = stat.S_IMODE(self.path.stat().st_mode)
        self.dir_mode = stat.S_IMODE(self.path.parent.stat().st_mode)
        return super().__call__(argv, env=env, cwd=cwd, timeout=timeout)


def _recording_case(git_repo: GitRepo, tmp_path: Path, case: str, **stage):
    base = _seed(git_repo, case)
    lane = _with_placeholder(_lane(git_repo, base))
    return lane, Recording(_stage(tmp_path, case, **stage), _exit_code(case))


def _changed_diff(verdict):
    assert verdict.judgment is not None and verdict.judgment.r2 is not None
    return verdict.judgment.r2.changed_diff


def test_substituted_path_exists_outside_snapshot_mode_0600(
    git_repo: GitRepo, tmp_path: Path
):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")

    _run(git_repo, lane, fake)

    assert fake.argv is not None and fake.path is not None
    assert PLACEHOLDER not in fake.argv
    assert fake.path.name == "in-diff.patch"
    assert fake.mode == 0o600
    assert fake.cwd is not None and fake.cwd not in fake.path.parents
    assert fake.dir_mode == 0o700
    assert fake.content


def test_diff_has_ab_prefixes_despite_repo_noprefix_config(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    git_repo.git("config", "diff.noprefix", "true")
    git_repo.git("config", "diff.mnemonicPrefix", "true")
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text("[diff]\n\tnoprefix = true\n\trenames = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")

    _run(git_repo, lane, fake)

    assert fake.content is not None
    text = fake.content.decode("utf-8")
    assert "diff --git a/src/lib.rs b/src/lib.rs" in text
    assert "--- /dev/null" in text and "+++ b/src/lib.rs" in text


def test_diff_relative_to_workspace_root_in_subproject(git_repo: GitRepo, tmp_path: Path):
    prefix = "sub/crate1"
    base = _seed(git_repo, "subdir", prefix=prefix)
    git_repo.write("sub/README.md", "outside the workspace root\n")
    git_repo.commit_all("a file outside the workspace root")
    lane = _with_placeholder(_lane(git_repo, base, prefix=prefix))
    fake = Recording(_stage(tmp_path, "subdir"), _exit_code("subdir"))

    verdict = _run(git_repo, lane, fake)

    assert fake.content is not None
    text = fake.content.decode("utf-8")
    assert "+++ b/src/lib.rs" in text
    assert "sub/crate1" not in text and "README" not in text
    changed = _changed_diff(verdict)
    assert changed is not None
    assert changed.relative_to == prefix


def test_diff_at_the_git_top_is_relative_to_dot(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")

    verdict = _run(git_repo, lane, fake)

    changed = _changed_diff(verdict)
    assert changed is not None and changed.relative_to == "."


def test_verdict_argv_keeps_placeholder(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")

    verdict = _run(git_repo, lane, fake)

    assert verdict.argv_declared == ARGV
    assert verdict.argv_effective == ARGV
    assert str(fake.path) not in "".join(verdict.argv_effective)
    assert fake.argv is not None and fake.argv != ARGV


def test_changed_diff_field_matches_written_bytes_sha256(
    git_repo: GitRepo, tmp_path: Path
):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")

    verdict = _run(git_repo, lane, fake)

    changed = _changed_diff(verdict)
    assert changed is not None and fake.content is not None
    assert changed.sha256 == hashlib.sha256(fake.content).hexdigest()
    assert changed.bytes == len(fake.content)
    assert changed.head_rev == git_repo.head()
    assert changed.base_rev == lane.judge.base
    assert verdict.judgment.resolved.base == changed.base_rev


def test_diff_deleted_after_lane(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")

    _run(git_repo, lane, fake)

    assert fake.path is not None
    assert not fake.path.exists()
    assert not fake.path.parent.exists()


def test_a_deleted_file_is_in_the_diff(git_repo: GitRepo, tmp_path: Path):
    base = _seed(git_repo, "indiff")
    git_repo.write("src/gone.rs", "pub fn gone() {}\n")
    base = git_repo.commit_all("add a file to delete")
    (git_repo.path / "src" / "gone.rs").unlink()
    git_repo.commit_all("delete it")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "indiff"), _exit_code("indiff"))

    _run(git_repo, lane, fake)

    assert fake.content is not None
    text = fake.content.decode("utf-8")
    assert "deleted file mode" in text
    assert "--- a/src/gone.rs" in text and "+++ /dev/null" in text


def test_a_docs_only_change_writes_an_empty_diff(
    git_repo: GitRepo, tmp_path: Path, monkeypatch, validator
):
    _seed(git_repo, "zero-mutants")
    base = git_repo.head()
    git_repo.write("README.md", "docs only\n")
    git_repo.commit_all("docs")
    lane = _with_placeholder(_lane(git_repo, base))
    # With an empty diff cargo-mutants writes no `mutants.out` at all (D3).
    fake = Recording(None, _exit_code("zero-mutants"))
    produced = []
    real = runner._produce_changed_diff

    def spy(*args, **kwargs):
        handoff = real(*args, **kwargs)
        produced.append(handoff)
        return handoff

    monkeypatch.setattr(runner, "_produce_changed_diff", spy)

    verdict = _run(git_repo, lane, fake)

    assert fake.content == b""
    (handoff,) = produced
    assert handoff.size == 0
    assert handoff.sha256 == hashlib.sha256(b"").hexdigest()
    # (U5c) The empty diff selects nothing: the tool wrote no report, which is
    # the zero-mutant shape, never a PASS.
    _assert_zero_mutant(verdict, validator, lines=())


def _assert_zero_mutant(verdict, validator, *, lines):
    """R0 PASS by exit code, R2 INCONCLUSIVE/NO_MUTANTS with `report_absent`,
    the `unobserved` producer, and a document the schema and `verify` accept."""
    assert _claim(verdict, "R0").status is Outcome.PASS
    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.INCONCLUSIVE
    assert r2.reason_code is ReasonCode.NO_MUTANTS
    assert r2.mutation is not None
    assert (r2.mutation.total, r2.mutation.candidate_count) == (0, 0)
    assert verdict.outcome is Outcome.INCONCLUSIVE
    assert verdict.reason_code is ReasonCode.NO_MUTANTS
    assert verdict.exit_code == 5
    policy = verdict.judgment.r2
    assert policy.report_absent is True
    assert policy.producer == "ingested"
    assert (
        policy.producer_tool.name,
        policy.producer_tool.version,
        policy.producer_tool.report_schema_version,
    ) == ("cargo-mutants", PRODUCER_VERSION_UNOBSERVED, "outcomes.json")
    assert policy.survived_uncovered == () and policy.discarded == ()
    assert policy.fail_under == 100.0
    assert policy.changed_diff is not None
    assert [(p.path, p.lineno) for p in policy.lines_without_candidates] == list(lines)
    document = verdict.to_dict()
    assert why_invalid(validator, document) == []
    assert verify_document(document) == []


def test_base_equal_to_head_is_the_existing_refusal(git_repo: GitRepo, tmp_path: Path):
    _seed(git_repo, "indiff")
    lane = _with_placeholder(_lane(git_repo, git_repo.head()))
    fake = Recording(_stage(tmp_path, "indiff"), _exit_code("indiff"))

    verdict = _run(git_repo, lane, fake)

    assert fake.argv is None
    assert verdict.outcome is Outcome.NO_MEASUREMENT
    assert verdict.judgment is None or verdict.judgment.r2 is None


def test_cli_appended_placeholder_refused(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")
    plan = runner.resolve_command_plan(
        lane, argv_append=(PLACEHOLDER,)
    )
    with pytest.raises(AssayError) as raised:
        runner.materialize_argv(plan)
    assert raised.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert raised.value.outcome is Outcome.ERROR


def test_plan_with_placeholder_and_no_handoff_errors(git_repo: GitRepo, tmp_path: Path):
    lane, _ = _recording_case(git_repo, tmp_path, "indiff")
    plan = runner.resolve_command_plan(lane)

    with pytest.raises(AssayError) as raised:
        runner.materialize_argv(plan)

    assert raised.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_a_plan_without_the_placeholder_is_unchanged(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _case(git_repo, tmp_path, "indiff")
    plan = runner.resolve_command_plan(lane)

    assert runner.materialize_argv(plan) == plan.argv_effective


def test_a_repeated_placeholder_is_refused(git_repo: GitRepo, tmp_path: Path):
    lane, _ = _recording_case(git_repo, tmp_path, "indiff")
    plan = runner.resolve_command_plan(replace(lane, argv=ARGV + (PLACEHOLDER,)))
    handoff = runner.ChangedDiffHandoff(
        path=tmp_path / "x",
        sha256="0" * 64,
        size=0,
        base_rev="1" * 40,
        head_rev="2" * 40,
        relative_to=".",
    )

    with pytest.raises(AssayError) as raised:
        runner.materialize_argv(replace(plan, changed_diff=handoff))

    assert raised.value.reason_code is ReasonCode.BAD_LANE_CONFIG


def test_the_handoff_path_replaces_exactly_the_declared_token(tmp_path: Path):
    plan = runner.CommandPlan(
        argv_declared=ARGV,
        argv_appended=("--extra",),
        argv_effective=ARGV + ("--extra",),
        env_declared={},
        env_effective={},
        env_passthrough=(),
        allow_argv_append=True,
        budget_seconds=1.0,
        project_prefix=None,
        changed_diff=runner.ChangedDiffHandoff(
            path=tmp_path / "in-diff.patch",
            sha256="0" * 64,
            size=0,
            base_rev="1" * 40,
            head_rev="2" * 40,
            relative_to=".",
        ),
    )

    assert runner.materialize_argv(plan) == (
        "cargo",
        "mutants",
        "--output",
        OUT,
        "--in-diff",
        str(tmp_path / "in-diff.patch"),
        "--extra",
    )


def test_a_renamed_file_that_exists_at_base_is_a_delete_plus_add(
    git_repo: GitRepo, tmp_path: Path
):
    # ``--no-renames`` is pinned: without it git emits ``rename from/to``
    # headers and NO hunks for an unchanged-content rename, which would hand
    # cargo-mutants a diff with no added lines.
    _seed(git_repo, "indiff")
    git_repo.write("src/old_name.rs", "pub fn moved() -> u8 {\n    1\n}\n")
    base = git_repo.commit_all("add a file that will be renamed")
    git_repo.git("mv", "src/old_name.rs", "src/new_name.rs")
    git_repo.commit_all("rename it")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "indiff"), _exit_code("indiff"))

    _run(git_repo, lane, fake)

    assert fake.content is not None
    text = fake.content.decode("utf-8")
    assert "rename from" not in text and "similarity index" not in text
    assert "--- a/src/old_name.rs" in text and "+++ /dev/null" in text
    assert "--- /dev/null" in text and "+++ b/src/new_name.rs" in text
    assert "+pub fn moved() -> u8 {" in text


class Tampering(Recording):
    """A :class:`Recording` command that changes the handoff file by name."""

    def __init__(self, *args, tamper, **kwargs):
        super().__init__(*args, **kwargs)
        self.tamper = tamper

    def __call__(self, argv, *, env, cwd, timeout):
        completed = super().__call__(argv, env=env, cwd=cwd, timeout=timeout)
        assert self.path is not None
        self.tamper(self.path)
        return completed


def _rewrite_same_size(path: Path) -> None:
    original = path.read_bytes()
    path.write_bytes(bytes(byte ^ 1 for byte in original))


def _truncate(path: Path) -> None:
    path.write_bytes(path.read_bytes()[:-1])


def _append(path: Path) -> None:
    with path.open("ab") as stream:
        stream.write(b"+extra line\n")


def _delete(path: Path) -> None:
    path.unlink()


def _replace_with_symlink(path: Path) -> None:
    target = path.parent / "elsewhere"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)


def _replace_directory_with_symlink(path: Path) -> None:
    moved = path.parent.with_name("moved-diff")
    path.parent.rename(moved)
    path.parent.symlink_to(moved)


@pytest.mark.parametrize(
    "tamper",
    [
        _rewrite_same_size,
        _truncate,
        _append,
        _delete,
        _replace_with_symlink,
        _replace_directory_with_symlink,
    ],
    ids=lambda func: func.__name__,
)
def test_a_command_that_rewrites_the_diff_makes_r2_an_error(
    git_repo: GitRepo, tmp_path: Path, tamper
):
    base = _seed(git_repo, "indiff")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Tampering(
        _stage(tmp_path, "indiff"), _exit_code("indiff"), tamper=tamper
    )

    # `_run` round-trips the document through `verify_document`.
    verdict = _run(git_repo, lane, fake)

    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.ERROR
    assert r2.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert r2.detail is not None and "changed-lines diff" in r2.detail
    # No judgment.r2 (hence no sha256 that attests bytes the tool did not read).
    assert verdict.judgment is None or verdict.judgment.r2 is None


def test_an_untouched_diff_still_judges_r2(git_repo: GitRepo, tmp_path: Path):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")

    verdict = _run(git_repo, lane, fake)

    assert _changed_diff(verdict) is not None
    assert _claim(verdict, "R2").reason_code is not ReasonCode.UNREADABLE_ARTIFACT


def test_a_diff_that_cannot_be_written_is_a_lane_refusal(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    lane, fake = _recording_case(git_repo, tmp_path, "indiff")
    real_open = runner.os.open

    def failing_open(path, flags, *args, **kwargs):
        if Path(path).name == "in-diff.patch":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(runner.os, "open", failing_open)

    verdict = _run(git_repo, lane, fake)

    assert fake.argv is None
    assert verdict.outcome is Outcome.ERROR
    assert verdict.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.judgment is None or verdict.judgment.r2 is None


# --- U5c: zero-mutant synthesis --------------------------------------------------


def _zero_lane(git_repo: GitRepo):
    """The zero-mutants subject committed, base = that commit; the caller then
    commits the change under test. Returns the base revision."""
    _seed(git_repo, "zero-mutants")
    return git_repo.head()


def test_a_comment_only_change_is_zero_mutants_with_its_lines_listed(
    git_repo: GitRepo, tmp_path: Path, validator
):
    base = _zero_lane(git_repo)
    path = git_repo.path / "src" / "lib.rs"
    text = path.read_text(encoding="utf-8").splitlines(keepends=True)
    text[0] = "//! Synthetic cargo-mutants subject (comment edited again).\n"
    path.write_text("".join(text), encoding="utf-8")
    git_repo.commit_all("comment only")
    git_repo.write("README.md", "docs too\n")
    git_repo.commit_all("docs outside the source roots")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(None, _exit_code("zero-mutants"))

    verdict = _run(git_repo, lane, fake)

    # The diff is non-empty, but the tool still found nothing to mutate; only
    # the added line under the source root is listed (Q3), never README.md.
    assert fake.content
    _assert_zero_mutant(verdict, validator, lines=[("src/lib.rs", 1)])


def test_the_zero_mutant_lines_are_those_of_a_workspace_below_the_git_top(
    git_repo: GitRepo, tmp_path: Path, validator
):
    prefix = "sub/crate1"
    _seed(git_repo, "zero-mutants", prefix=prefix)
    base = git_repo.head()
    path = git_repo.path / prefix / "src" / "lib.rs"
    path.write_text("// one\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    git_repo.commit_all("add a comment line")
    lane = _with_placeholder(_lane(git_repo, base, prefix=prefix))

    verdict = _run(git_repo, lane, Recording(None, _exit_code("zero-mutants")))

    _assert_zero_mutant(verdict, validator, lines=[(f"{prefix}/src/lib.rs", 1)])


def test_an_absent_report_without_the_handoff_stays_fail_closed(
    git_repo: GitRepo, tmp_path: Path
):
    # A whole_target lane never receives the placeholder diff (config refuses
    # it), so its absent report is still the unreadable-artifact refusal.
    base = _zero_lane(git_repo)
    lane = _lane(git_repo, base)
    judge = replace(lane.judge, mode="whole_target", targets=("src/lib.rs",))
    lane = replace(lane, judge=judge)

    verdict = _run(git_repo, lane, Fake(None, 0))

    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.ERROR
    assert r2.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.judgment is None or verdict.judgment.r2 is None


def test_a_failed_exit_with_an_absent_report_is_decided_by_the_exit_code(
    git_repo: GitRepo, tmp_path: Path
):
    base = _zero_lane(git_repo)
    git_repo.write("README.md", "docs only\n")
    git_repo.commit_all("docs")
    lane = _with_placeholder(_lane(git_repo, base))

    verdict = _run(git_repo, lane, Recording(None, 2))

    r0 = _claim(verdict, "R0")
    assert r0.status is Outcome.FAIL
    assert r0.reason_code is ReasonCode.COMMAND_FAILED
    assert verdict.outcome is Outcome.FAIL
    # R2 is the not-reached claim, never a synthesised zero-mutant one.
    assert _claim(verdict, "R2").reason_code is not ReasonCode.NO_MUTANTS
    assert verdict.judgment is None or verdict.judgment.r2 is None


def test_a_tampered_handoff_is_refused_before_any_synthesis(
    git_repo: GitRepo, tmp_path: Path
):
    base = _zero_lane(git_repo)
    git_repo.write("README.md", "docs only\n")
    git_repo.commit_all("docs")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Tampering(None, _exit_code("zero-mutants"), tamper=_append)

    verdict = _run(git_repo, lane, fake)

    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.ERROR
    assert r2.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert r2.detail is not None and "changed-lines diff" in r2.detail
    assert verdict.judgment is None or verdict.judgment.r2 is None


def test_the_synthesised_result_keeps_only_added_lines_under_a_source_root(
    tmp_path: Path,
):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "only.rs").write_text("x\n", encoding="utf-8")
    added = AddedLines(
        by_file={
            "src/b.rs": frozenset({7, 2}),
            "src/a.rs": frozenset({3}),
            "src/only.rs": frozenset({1}),
            "tests/t.rs": frozenset({1}),
            "srcother/x.rs": frozenset({1}),
        }
    )

    result = mutation.synthesize_zero_mutant_result(
        added,
        repo_top=tmp_path,
        source_root_paths=(tmp_path / "src",),
    )
    only_file_root = mutation.synthesize_zero_mutant_result(
        added, repo_top=tmp_path, source_root_paths=(tmp_path / "src" / "only.rs",)
    )

    assert [(p.path, p.lineno) for p in result.lines_without_candidates] == [
        ("src/a.rs", 3),
        ("src/b.rs", 2),
        ("src/b.rs", 7),
        ("src/only.rs", 1),
    ]
    assert [(p.path, p.lineno) for p in only_file_root.lines_without_candidates] == [
        ("src/only.rs", 1)
    ]
    assert result.report_absent is True
    assert result.mutation.total == 0 and result.mutation.candidate_count == 0


# --- U5c review finding 1: only an ABSENT report is ever synthesised --------------
#
# Every state below runs under a PLACEHOLDER lane, so the changed-lines diff
# handoff exists and exit 0 holds. The `_DirectoryAbsent` clause of the
# zero-mutant decision is then the only thing that keeps a present, refused or
# unusable report from being replaced by the synthesised "ran zero mutants"
# result: removing it would turn a real FAIL/BUDGET_EXCEEDED into an
# INCONCLUSIVE/NO_MUTANTS that hides real mutants.


def _not_zero_mutant(verdict) -> None:
    r2 = _claim(verdict, "R2")
    assert r2.reason_code is not ReasonCode.NO_MUTANTS
    assert verdict.reason_code is not ReasonCode.NO_MUTANTS
    assert verdict.judgment is None or verdict.judgment.r2 is None or (
        verdict.judgment.r2.report_absent is False
    )


def _unreadable(verdict) -> None:
    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.ERROR
    assert r2.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    _not_zero_mutant(verdict)
    assert verdict.judgment is None or verdict.judgment.r2 is None


def test_a_usable_report_with_the_handoff_is_its_own_terminal_not_zero_mutants(
    git_repo: GitRepo, tmp_path: Path
):
    base = _seed(git_repo, "full")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "full"), 0)

    verdict = _run(git_repo, lane, fake)

    assert fake.calls == 1 and fake.content
    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.BUDGET_EXCEEDED
    assert r2.reason_code is ReasonCode.LANE_TIMEOUT
    assert verdict.outcome is Outcome.BUDGET_EXCEEDED
    _not_zero_mutant(verdict)
    assert _changed_diff(verdict) is not None
    assert verdict.judgment.r2.report_absent is False


def test_a_usable_report_with_survivors_and_the_handoff_fails_the_floor(
    git_repo: GitRepo, tmp_path: Path
):
    base = _seed(git_repo, "indiff")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "indiff"), 0)

    verdict = _run(git_repo, lane, fake)

    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.FAIL
    assert r2.reason_code is ReasonCode.MUTANTS_SURVIVED
    assert verdict.outcome is Outcome.FAIL
    _not_zero_mutant(verdict)
    assert verdict.judgment.r2.report_absent is False


def test_a_refused_reservation_with_the_handoff_is_not_zero_mutants(
    git_repo: GitRepo, tmp_path: Path, monkeypatch
):
    real = runner.safeio.reserve_directory_output

    def littered(root, artifact, **kwargs):
        (Path(root) / artifact).mkdir(parents=True, exist_ok=True)
        (Path(root) / artifact / "junk.txt").write_text("x", encoding="utf-8")
        return real(root, artifact, **kwargs)

    monkeypatch.setattr(runner.safeio, "reserve_directory_output", littered)
    base = _seed(git_repo, "full")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "full"), 0)

    verdict = _run(git_repo, lane, fake)

    assert fake.argv is None
    _unreadable(verdict)


def test_a_tracked_out_with_the_handoff_is_not_zero_mutants(
    git_repo: GitRepo, tmp_path: Path
):
    git_repo.write(".gitignore", "")
    git_repo.write(f"{OUT}/keep.txt", "tracked\n")
    git_repo.commit_all("track the output directory")
    base = git_repo.head()
    git_repo.write("src/lib.rs", "pub fn f() {}\n")
    git_repo.write("Cargo.toml", '[package]\nname = "x"\nversion = "0.1.0"\n')
    git_repo.commit_all("subject")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "full"), 0)

    verdict = _run(git_repo, lane, fake)

    assert fake.argv is None
    _unreadable(verdict)


def test_a_report_outside_the_window_with_the_handoff_is_not_zero_mutants(
    git_repo: GitRepo, tmp_path: Path
):
    base = _seed(git_repo, "indiff")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "indiff", window=(-100, -50)), 0)

    verdict = _run(git_repo, lane, fake)

    assert fake.content
    _unreadable(verdict)


def test_a_tampered_member_diff_with_the_handoff_is_not_zero_mutants(
    git_repo: GitRepo, tmp_path: Path
):
    staged = _stage(tmp_path, "indiff")
    victim = next((staged / "diff").iterdir())
    victim.write_text(
        victim.read_text(encoding="utf-8").replace("n % 2", "n % 3"), encoding="utf-8"
    )
    base = _seed(git_repo, "indiff")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(staged, 0)

    verdict = _run(git_repo, lane, fake)

    assert fake.content
    _unreadable(verdict)


def test_a_stale_rotated_directory_with_the_handoff_is_not_zero_mutants(
    git_repo: GitRepo, tmp_path: Path
):
    # A refusal found while CONSUMING the directory (after the command ran).
    def rotated(cwd: Path) -> None:
        (cwd / OUT / "mutants.out.old").mkdir()

    base = _seed(git_repo, "indiff")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "indiff"), 0, before=rotated)

    verdict = _run(git_repo, lane, fake)

    assert fake.content
    _unreadable(verdict)


def test_an_out_renamed_away_with_the_handoff_is_not_zero_mutants(
    git_repo: GitRepo, tmp_path: Path
):
    def renamed(cwd: Path) -> None:
        (cwd / OUT).rename(cwd / "moved")
        (cwd / OUT).mkdir()

    base = _seed(git_repo, "indiff")
    lane = _with_placeholder(_lane(git_repo, base))
    fake = Recording(_stage(tmp_path, "indiff"), 0, before=renamed)

    verdict = _run(git_repo, lane, fake)

    assert fake.content
    _unreadable(verdict)
