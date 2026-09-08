"""B066 -- ``--state-dir PATH``: resume state that outlives its worktree.

`mutation_state_record_path` fixed the records under
`<project_root>/.assay/mutation-state/` and `--resume` read and wrote there
and nowhere else. A persistent worktree resumed across retries; a FRESH
worktree per run -- cmru's release transaction, a dstdns Mode-B instance --
carried its own empty store away with it, so `--resume` was inert exactly
where budget-capped retries happen most.

The relocation is safe by construction rather than by policy: a candidate id
folds the source file's exact bytes, its span, its replacement and its
operator, so a record from another worktree either MATCHES its identity or is
ignored, and a record that contradicts the identity it is filed under still
fails the lane `UNREADABLE_ARTIFACT`. This file measures both halves of that
claim on two REAL worktrees rather than asserting it.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from conftest import GitRepo

from assay.cli import main


_LANE = """\
schema_version = 2

[lanes.unit]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.unit.isolation]
snapshot_selection = "repository"

[lanes.unit.judge]
language = "python"
source_roots = ["pkg"]
base = "base"

[lanes.unit.judge.mutation]
jobs = 1
max_mutants = 10
operators = ["python:bool-const-flip"]
"""


def _seed(repo: GitRepo, *, flags: str = "a = True\n") -> None:
    repo.write("assay.toml", _LANE)
    repo.write("pkg/flags.py", flags)
    repo.commit_all("lane")
    repo.git("checkout", "-q", "-b", "base")
    repo.write("pkg/flags.py", "a = False\n")
    repo.commit_all("base flag")
    repo.git("checkout", "-q", "-b", "feature")
    repo.write("pkg/flags.py", flags)
    repo.commit_all("restore flag")


def _events(destination: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in destination.read_text(encoding="utf-8").splitlines()
    ]


def _linked_worktree(repo: GitRepo, where: Path) -> Path:
    """A SECOND real worktree of the same repository, on the same commit.

    `--detach`, because `feature` is already checked out in the primary
    worktree and git refuses to check one branch out twice. Detached is also
    the honest model of the consumers this item exists for: cmru's release
    transaction and a Mode-B instance check out a commit, not a branch.
    """
    subprocess.run(
        [
            "git",
            "-C",
            str(repo.path),
            "worktree",
            "add",
            "-q",
            "--detach",
            str(where),
            "feature",
        ],
        check=True,
        capture_output=True,
    )
    return where


#: The lane's command is `exit 0`, so every mutant survives and the verdict
#: is FAIL/MUTANTS_SURVIVED. That is deliberate and irrelevant here: this
#: file measures WHERE resume state lives, not what the lane judges. What
#: matters is that the run COMPLETED -- 1 is a verdict, not a crash.
_COMPLETED = 1


def _run(lane_file: Path, state_dir: Path, progress: Path) -> int:
    return main(
        [
            "run",
            "unit",
            "--file",
            str(lane_file),
            "--state-dir",
            str(state_dir),
            "--progress",
            str(progress),
            "--resume",
        ]
    )


def test_two_worktrees_of_one_commit_share_one_state_dir_and_the_second_resumes(
    git_repo: GitRepo, tmp_path
):
    """B066's headline acceptance, measured rather than asserted.

    Two DIFFERENT worktrees, one commit, one shared `--state-dir`: the
    second run genuinely resumes, and says so with `event: resume` and
    `resumed_total > 0`. Under the old project-root-derived store the second
    worktree's `.assay/mutation-state/` was empty and every candidate ran
    again.
    """
    _seed(git_repo)
    state_dir = tmp_path / "durable-state"

    first_progress = tmp_path / "first.jsonl"
    assert _run(git_repo.path / "assay.toml", state_dir, first_progress) == _COMPLETED
    records = sorted(state_dir.glob("*.json"))
    assert records, "the first run wrote no resume records at all"
    assert "resume" not in {
        event["event"] for event in _events(first_progress)
    }, "there was nothing to resume from on the first run"

    second = _linked_worktree(git_repo, tmp_path / "second-worktree")
    second_progress = tmp_path / "second.jsonl"
    assert _run(second / "assay.toml", state_dir, second_progress) == _COMPLETED

    resumed = [
        event for event in _events(second_progress) if event["event"] == "resume"
    ]
    assert resumed, _events(second_progress)
    assert resumed[0]["resumed_total"] == len(records)
    # Nothing was left to execute, which is what "genuinely resumed" means.
    sizes = next(
        event for event in _events(second_progress) if event["event"] == "candidates"
    )
    assert sizes["pending_total"] == 0
    assert sizes["candidate_total"] == len(records)


def test_a_source_edit_between_the_two_runs_reexecutes_the_touched_files_candidates(
    git_repo: GitRepo, tmp_path
):
    """The other half of the shared-store claim, and the reason it is safe.

    A candidate id folds the source file's exact bytes, so editing the file
    changes every one of its candidates' identities: the shared store simply
    holds no record under the NEW ids and the work is redone. That is by
    construction -- but B066's acceptance asks for it MEASURED, and a
    construction argument that nobody ever ran is how a store silently
    resumes stale work.
    """
    _seed(git_repo)
    state_dir = tmp_path / "durable-state"

    first_progress = tmp_path / "first.jsonl"
    assert _run(git_repo.path / "assay.toml", state_dir, first_progress) == _COMPLETED
    first_ids = {path.name for path in state_dir.glob("*.json")}
    assert first_ids

    second = _linked_worktree(git_repo, tmp_path / "second-worktree")
    # A real source edit, committed, so the tree is clean for the lane.
    (second / "pkg" / "flags.py").write_text("a = True\nb = True\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(second), "commit", "-qam", "edit the source"],
        check=True,
        capture_output=True,
    )

    second_progress = tmp_path / "second.jsonl"
    assert _run(second / "assay.toml", state_dir, second_progress) == _COMPLETED

    sizes = next(
        event for event in _events(second_progress) if event["event"] == "candidates"
    )
    assert sizes["pending_total"] > 0, (
        "the edited file's candidates must be re-executed, not resumed"
    )
    after = {path.name for path in state_dir.glob("*.json")}
    assert after > first_ids, "the new identities must be written beside the old"


def test_the_default_location_is_unchanged_when_no_state_dir_is_given(
    git_repo: GitRepo, tmp_path
):
    """The relocation is opt-in. An existing consumer's records stay exactly
    where they already are."""
    _seed(git_repo)
    git_repo.write(".gitignore", ".assay/\n")
    git_repo.commit_all("ignore the state store")

    assert (
        main(
            [
                "run",
                "unit",
                "--file",
                str(git_repo.path / "assay.toml"),
                "--resume",
            ]
        )
        == _COMPLETED
    )

    assert sorted(
        (git_repo.path / ".assay" / "mutation-state").glob("*.json")
    ), "the default store moved"


def test_a_state_dir_inside_the_judged_tree_refuses_before_any_work(
    git_repo: GitRepo, tmp_path
):
    """B066's second acceptance box, and the trap it closes.

    An untracked directory inside the work tree is exactly what
    `git.dirty_paths` reports, so the NEXT run of this lane would refuse
    `NO_MEASUREMENT`/`DIRTY_TREE` -- the identical failure B031 measured for
    the progress artifact. Refused before the lane's command runs, naming
    both the cause and the fix.
    """
    _seed(git_repo)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(git_repo.path / "inside-the-tree"),
            "--resume",
        ],
        stderr=err,
    )

    assert code != 0
    message = err.getvalue()
    assert "DIRTY_TREE" in message, message
    assert "inside-the-tree" in message, message
    assert not (git_repo.path / "inside-the-tree").exists(), (
        "refused BEFORE anything was created"
    )


def test_a_gitignored_state_dir_inside_the_tree_is_accepted(
    git_repo: GitRepo, tmp_path
):
    """Git cannot see it, so nothing goes dirty -- the same rule
    `--progress`'s own guidance already states."""
    _seed(git_repo)
    git_repo.write(".gitignore", "resume-store/\n")
    git_repo.commit_all("ignore the resume store")

    assert (
        main(
            [
                "run",
                "unit",
                "--file",
                str(git_repo.path / "assay.toml"),
                "--state-dir",
                str(git_repo.path / "resume-store"),
                "--resume",
            ]
        )
        == _COMPLETED
    )
    assert sorted((git_repo.path / "resume-store").glob("*.json"))
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_state_dir_that_is_an_existing_file_refuses(git_repo: GitRepo, tmp_path):
    _seed(git_repo)
    occupied = tmp_path / "not-a-directory"
    occupied.write_text("", encoding="utf-8")
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(occupied),
            "--resume",
        ],
        stderr=err,
    )

    assert code != 0
    assert "is not a directory" in err.getvalue(), err.getvalue()


def test_verify_is_unaffected_by_where_resume_state_lives(git_repo: GitRepo, tmp_path):
    """Resume state is diagnostic, never evidence: the verdict does not name
    the store and `assay verify` never reads it."""
    _seed(git_repo)
    state_dir = tmp_path / "durable-state"
    verdict_path = tmp_path / "verdict.json"

    main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(state_dir),
            "--resume",
            "--verdict-json",
            str(verdict_path),
        ]
    )

    document = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert str(state_dir) not in json.dumps(document)
    assert main(["verify", str(verdict_path)]) == 0
