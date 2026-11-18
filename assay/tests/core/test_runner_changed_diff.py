"""B245 U5b (D7): the changed-lines diff handed to cargo-mutants through the
``{assay.changed_diff}`` argv placeholder, end to end through ``run_lane``.

The fake process runner records the argv it receives and reads the handoff file
while the "command" runs. Every verdict a test produces is round-tripped through
``verify_document`` (``_run`` in the shared harness): a runner state that yields
a verdict ``verify`` rejects is a bug.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import GitRepo
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

from assay import runner
from assay.errors import AssayError, Outcome, ReasonCode
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
    git_repo: GitRepo, tmp_path: Path, monkeypatch
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
    # An absent report is, until U5c's `report_absent` / NO_MUTANTS terminal,
    # the generic unreadable-artifact ERROR with no `judgment.r2` -- and that
    # document verifies (`_run` round-trips it).
    r2 = _claim(verdict, "R2")
    assert r2.status is Outcome.ERROR
    assert r2.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert verdict.judgment is None or verdict.judgment.r2 is None


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
