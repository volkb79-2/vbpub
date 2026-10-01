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


def test_the_inside_the_tree_check_does_not_fail_open_across_an_intermediate_symlink(
    git_repo: GitRepo, tmp_path
):
    """(Round-1 SF-1, the reviewer's PROBE D.) The containment check compared
    two DIFFERENT path namespaces.

    `output.resolve_state_directory` is lexical by design (`normpath`, never
    `realpath`, because resolving against the filesystem would follow
    symlinks that the descriptor walk exists to refuse), while
    `project_root.resolve()` does follow them. When the two disagreed
    `relative_to` raised and the path was silently classified "outside the
    judged tree, nothing to check" -- so a `--state-dir` reached through a
    symlink hop landed records INSIDE the real work tree and left it dirty,
    which is the exact `NO_MEASUREMENT`/`DIRTY_TREE` this refusal exists to
    prevent.
    """
    _seed(git_repo)
    hop_parent = tmp_path / "outside"
    hop_parent.mkdir()
    (hop_parent / "hop").symlink_to(git_repo.path)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(hop_parent / "hop" / "sneaky"),
            "--resume",
        ],
        stderr=err,
    )

    assert code != 0, "a symlink hop must not launder a path into the work tree"
    assert "DIRTY_TREE" in err.getvalue(), err.getvalue()
    assert not (git_repo.path / "sneaky").exists()
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_the_inside_the_tree_check_holds_when_the_root_itself_is_reached_by_symlink(
    git_repo: GitRepo, tmp_path
):
    """(Round-1 SF-1, the reviewer's PROBE C.) The other direction: the
    project root itself reached through a symlink, with a `--state-dir`
    lexically under that link."""
    _seed(git_repo)
    link = tmp_path / "link"
    link.symlink_to(git_repo.path)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(link / "assay.toml"),
            "--state-dir",
            str(link / "inside-via-link"),
            "--resume",
        ],
        stderr=err,
    )

    assert code != 0
    assert "DIRTY_TREE" in err.getvalue(), err.getvalue()
    assert not (git_repo.path / "inside-via-link").exists()
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_genuinely_outside_state_dir_is_still_accepted_after_the_symlink_fix(
    git_repo: GitRepo, tmp_path
):
    """The fix must fail CLOSED on the two probes above without failing
    closed on the ordinary case the flag exists for."""
    _seed(git_repo)
    outside = tmp_path / "genuinely-outside"

    assert (
        main(
            [
                "run",
                "unit",
                "--file",
                str(git_repo.path / "assay.toml"),
                "--state-dir",
                str(outside),
                "--resume",
            ]
        )
        == _COMPLETED
    )
    assert sorted(outside.glob("*.json"))
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_state_dir_spelled_as_git_pathspec_magic_refuses_by_name(
    git_repo: GitRepo, tmp_path
):
    """(Round-1 N2.) `path_is_ignored` runs without `--literal-pathspecs`
    (`check-ignore` refuses that flag outright), so git parses a leading `:`
    as pathspec magic and answered with a raw `GIT_FAILED` passthrough --
    the exact shape B068 was fixed to stop emitting. Named instead."""
    _seed(git_repo)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(git_repo.path / ":(icase)store"),
            "--resume",
        ],
        stderr=err,
    )

    assert code != 0
    message = err.getvalue()
    assert "pathspec magic" in message, message
    assert "GIT_FAILED" not in message, message


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


# --- B077: a destination reached THROUGH a symlink inside the judged tree ----


def _seed_with_a_committed_symlink_to_a_gitignored_store(repo: GitRepo) -> None:
    """The reviewer's exact repro: a symlink INSIDE the tree pointing at a
    gitignored location, both halves committed -- i.e. a consumer who
    configured this exactly right.

    The link is COMMITTED on purpose. An untracked link would itself make
    the tree dirty, which would confuse the thing under test with the
    `DIRTY_TREE` failure the sibling refusals above already cover.
    """
    _seed(repo)
    (repo.path / "durable-store").mkdir()
    repo.write(".gitignore", "durable-store/\n")
    (repo.path / "store-link").symlink_to("durable-store")
    repo.commit_all("a committed symlink to a gitignored store")


def test_a_state_dir_reached_through_a_symlink_names_the_link_not_gits_stderr(
    git_repo: GitRepo, tmp_path
):
    """(B077.) Git refuses to resolve a pathspec through a symlink at all --
    `fatal: pathspec '<path>' is beyond a symbolic link`, exit 128 -- which
    reached the operator verbatim as `ERROR`/`GIT_FAILED`: a repository
    failure shape for a destination-configuration mistake, and one a
    consumer who gitignored the real location correctly could hit while
    doing everything right. Named before git is asked, exactly as round-1's
    N2 pathspec-magic guard is.
    """
    _seed_with_a_committed_symlink_to_a_gitignored_store(git_repo)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--state-dir",
            str(git_repo.path / "store-link" / "records"),
            "--resume",
        ],
        stderr=err,
    )

    assert code != 0
    message = err.getvalue()
    # the raw passthrough is GONE...
    assert "GIT_FAILED" not in message, message
    # git's own stderr always interpolates the offending path immediately
    # after `pathspec `; the refusal quotes the phrasing (so an operator who
    # already met the raw error recognises it) but is never that passthrough.
    assert "fatal: pathspec '" not in message, message
    # ...replaced by a message naming the symlink AND the traversal
    assert "store-link" in message, message
    assert "symlink" in message, message
    assert "BAD_LANE_CONFIG" in message, message
    # and pointing at the destination that CAN be checked
    assert "durable-store" in message, message
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_progress_destination_reached_through_a_symlink_refuses_the_same_way(
    git_repo: GitRepo, tmp_path
):
    """The other half of the reviewer's pairing. `--progress` probes the
    destination FILE rather than a representative record, so it reaches the
    same guard by a different probe -- which is exactly why the check lives
    in the shared helper both flags call."""
    _seed_with_a_committed_symlink_to_a_gitignored_store(git_repo)
    err = io.StringIO()

    code = main(
        [
            "run",
            "unit",
            "--file",
            str(git_repo.path / "assay.toml"),
            "--progress",
            str(git_repo.path / "store-link" / "progress.jsonl"),
        ],
        stderr=err,
    )

    assert code != 0
    message = err.getvalue()
    assert "GIT_FAILED" not in message, message
    # git's own stderr always interpolates the offending path immediately
    # after `pathspec `; the refusal quotes the phrasing (so an operator who
    # already met the raw error recognises it) but is never that passthrough.
    assert "fatal: pathspec '" not in message, message
    assert "store-link" in message, message
    assert "--progress" in message, message
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_the_real_destination_behind_the_link_is_still_accepted(
    git_repo: GitRepo, tmp_path
):
    """The remedy the refusal names must actually work: passing the link's
    own destination -- a gitignored directory inside the tree -- is the
    already-correct case B066 shipped, and B077 must not have narrowed it."""
    _seed_with_a_committed_symlink_to_a_gitignored_store(git_repo)

    assert (
        main(
            [
                "run",
                "unit",
                "--file",
                str(git_repo.path / "assay.toml"),
                "--state-dir",
                str(git_repo.path / "durable-store"),
                "--resume",
            ]
        )
        == _COMPLETED
    )
    assert sorted((git_repo.path / "durable-store").glob("*.json"))
    assert git_repo.git("status", "--porcelain").strip() == ""


def test_a_destination_that_IS_a_symlink_is_still_refused_by_its_own_older_guard(
    git_repo: GitRepo, tmp_path
):
    """A symlink in the FINAL position is a different mistake with an older,
    earlier refusal, and B077 must not have moved it.

    Both flags lstat their destination before any repository work --
    `output.resolve_state_directory` requires a directory, `output.
    validate_progress_destination` an ordinary regular file -- and a symlink
    is neither. Those fire first and say so in their own words; B077's guard
    never sees these paths.
    """
    _seed(git_repo)
    (git_repo.path / "real-target").mkdir()
    (git_repo.path / "dir-link").symlink_to("real-target")
    (git_repo.path / "real-progress-target").write_text("", encoding="utf-8")
    (git_repo.path / "progress-link.jsonl").symlink_to("real-progress-target")
    git_repo.commit_all("committed symlinks in the final position")

    err = io.StringIO()
    assert main(
        [
            "run", "unit", "--file", str(git_repo.path / "assay.toml"),
            "--state-dir", str(git_repo.path / "dir-link"), "--resume",
        ],
        stderr=err,
    ) != 0
    assert "is not a directory" in err.getvalue(), err.getvalue()

    err = io.StringIO()
    assert main(
        [
            "run", "unit", "--file", str(git_repo.path / "assay.toml"),
            "--progress", str(git_repo.path / "progress-link.jsonl"),
        ],
        stderr=err,
    ) != 0
    assert "not an ordinary regular file" in err.getvalue(), err.getvalue()


def test_the_guard_probes_directory_components_only(git_repo: GitRepo):
    """`git check-ignore` answers normally about a path whose LAST component
    is a symlink -- there is nothing "beyond" it -- so a guard that probed
    every component would refuse a question git can and does answer.

    Asserted against the shared helper directly, because both CLI flags
    refuse a final-position symlink earlier for their own separate reasons
    (above), so this scoping is not observable end-to-end through either.
    """
    from assay.cli import _refuse_a_visible_store_inside_the_tree

    _seed(git_repo)
    (git_repo.path / "durable-store").mkdir()
    (git_repo.path / "durable-store" / "real.jsonl").write_text("", encoding="utf-8")
    git_repo.write(".gitignore", "durable-store/\nlink.jsonl\n")
    (git_repo.path / "link.jsonl").symlink_to("durable-store/real.jsonl")
    git_repo.commit_all("a gitignored symlink in the final position")

    # No exception: the probe's only symlink is its last component, so the
    # guard stands aside and `path_is_ignored` answers "ignored".
    _refuse_a_visible_store_inside_the_tree(
        "link.jsonl",
        flag="--progress",
        what="the progress file",
        root=git_repo.path,
        probe=Path("link.jsonl"),
    )


def test_a_destination_genuinely_outside_the_repository_is_unaffected(
    git_repo: GitRepo, tmp_path
):
    """B077's second acceptance line, first half: the guard runs only for a
    destination `_containments` already placed inside the tree, so an
    outside path never reaches it -- even one whose own parents are
    symlinks."""
    _seed(git_repo)
    real = tmp_path / "real-outside"
    real.mkdir()
    (tmp_path / "outside-link").symlink_to(real)

    assert (
        main(
            [
                "run",
                "unit",
                "--file",
                str(git_repo.path / "assay.toml"),
                "--state-dir",
                str(tmp_path / "outside-link" / "records"),
                "--resume",
            ]
        )
        == _COMPLETED
    )
    assert sorted((real / "records").glob("*.json"))
    assert git_repo.git("status", "--porcelain").strip() == ""
