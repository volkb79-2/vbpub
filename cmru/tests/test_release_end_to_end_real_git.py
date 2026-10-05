"""REL-15: end-to-end ``cmru release`` runs against a real local bare origin.

The real launcher and the real in-worktree child run as subprocesses (through a
tiny ``CMRU_BIN`` wrapper). Only the three project steps are faked: a gate whose
behaviour a test scripts through ``GATE_HOOK``, a build that can be told to fail
(``FAIL_BUILD``) and a publisher that appends to ``MARK`` and can be told to fail
(``FAIL_PUSH``). Everything about git (tags, candidate branch, promotion, local
main sync, worktree lifecycle) is real.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import cli_extended
import cmru
import worktree
from cmru import transaction

_TAG = "demo-v0.1.0"

_CMRU_TOML = """\
schema_version = 1

[runtime]
kind = "none"

[github]
owner = "o"
repo = "r"
owner_type = "user"

[targets]
host = "github"
registry = ["ghcr.io"]

[project]
id = "demo"
description = "demo"
template_revision = 4
prefix = "demo-v"
artifacts = ["wheel"]
scm_dist = "demo"

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"
changelog = "CHANGES.md"
commit_generated = ["CHANGES.md"]
artifact_dirs = ["dist"]

[steps.run-tests]
quiet = true
commands = [ { label = "gate", argv = ["sh", "-c", '[ -z "$GATE_HOOK" ] || sh "$GATE_HOOK"'], cwd = "." } ]

[steps.build]
quiet = true
commands = [ { label = "build", argv = ["sh", "-c", '[ ! -e "$FAIL_BUILD" ] || exit 1; mkdir -p dist; echo w > dist/a.whl'], cwd = "." } ]

[steps.push]
quiet = true
commands = [ { label = "push", argv = ["sh", "-c", '[ ! -e "$FAIL_PUSH" ] || exit 1; echo pub >> "$MARK"'], cwd = "." } ]
"""


def _run(*args, cwd, env=None, check=True):
    result = subprocess.run(
        list(args), cwd=cwd, env=env, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{args} failed:\n{result.stdout}\n{result.stderr}")
    return result


class _Env:
    """A seeded bare origin, a caller clone and the fake-publisher knobs."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.origin = root / "origin.git"
        self.repo = root / "repo"
        self.project = self.repo / "demo"
        self.mark = root / "mark"
        self.fail_build = root / "fail-build"
        self.fail_push = root / "fail-push"
        self.hooks = root / "hooks"
        self.hooks.mkdir()
        sources = [
            str(Path(module.__file__).resolve().parents[1])
            for module in (cmru, cli_extended, worktree)
        ]
        wrapper = root / "cmru-bin"
        wrapper.write_text(
            "#!/bin/sh\n"
            f'PYTHONPATH="{os.pathsep.join(sources)}" exec "{sys.executable}" '
            '-c "import sys; from cmru.cli import main; sys.exit(main())" "$@"\n'
        )
        wrapper.chmod(0o755)
        self.env = {
            **os.environ,
            "CMRU_BIN": str(wrapper),
            "GITHUB_PUSH_PAT": "x",
            "GATE_HOOK": "",
            "MARK": str(self.mark),
            "FAIL_BUILD": str(self.fail_build),
            "FAIL_PUSH": str(self.fail_push),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        self.env.pop("PYTHONPATH", None)
        seed = root / "seed"
        seed.mkdir()
        self.git("init", "-q", "-b", "main", cwd=seed)
        (seed / "demo").mkdir()
        (seed / "demo" / "cmru.toml").write_text(_CMRU_TOML)
        (seed / "demo" / "CHANGES.md").write_text(
            "# Changes\n\n## [Unreleased]\n\n<!-- cmru: release history -->\n"
        )
        (seed / "demo" / "a.txt").write_text("hi\n")
        (seed / "README.md").write_text("root readme\n")
        (seed / ".gitignore").write_text(
            "cmru.release.log\ndist/\n.worktrees/\nlogs/\nevidence/\nartifacts/\n"
        )
        self.git("add", "-A", cwd=seed)
        self.git("commit", "-q", "-m", "feat: init", cwd=seed)
        self.git("clone", "-q", "--bare", str(seed), str(self.origin), cwd=root)
        self.git("clone", "-q", str(self.origin), str(self.repo), cwd=root)

    def git(self, *args, cwd=None, check=True):
        return _run("git", *args, cwd=cwd or self.repo, env=self.env, check=check)

    def out(self, *args, cwd=None) -> str:
        return self.git(*args, cwd=cwd).stdout.strip()

    def hook(self, name: str, body: str) -> None:
        path = self.hooks / f"{name}.sh"
        path.write_text(body)
        self.env["GATE_HOOK"] = str(path)

    def advance_main_hook(self, relative_path: str) -> None:
        """A gate that pushes a commit to origin/main from another clone, once."""
        self.hook("advance", f"""\
set -e
[ ! -e "{self.root}/advanced" ] || exit 0
touch "{self.root}/advanced"
rm -rf "{self.root}/other"
git clone -q "{self.origin}" "{self.root}/other"
cd "{self.root}/other"
mkdir -p "$(dirname "{relative_path}")"
echo concurrent >> "{relative_path}"
git add -A
git commit -q -m "concurrent change"
git push -q origin HEAD:refs/heads/main
""")

    def release(self, *args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [self.env["CMRU_BIN"], "release", *args],
            cwd=self.project, env=self.env, capture_output=True, text=True,
            timeout=300,
        )
        result.log = result.stdout + result.stderr
        return result

    def origin_tags(self) -> dict[str, str]:
        lines = self.out("ls-remote", "--tags", "origin").splitlines()
        return {line.split()[1]: line.split()[0] for line in lines}

    def origin_heads(self) -> list[str]:
        return [
            line.split()[1] for line in self.out("ls-remote", "--heads", "origin").splitlines()
        ]

    def local_tags(self) -> list[str]:
        return self.out("tag", "--list").split()

    def origin_main(self) -> str:
        return self.out("--git-dir", str(self.origin), "rev-parse", "main", cwd=self.root)

    def worktrees(self) -> list[str]:
        return [
            line.split(maxsplit=1)[1]
            for line in self.out("worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")
        ]

    def retained_workspace(self) -> Path:
        extra = [w for w in self.worktrees() if Path(w).resolve() != self.repo.resolve()]
        assert len(extra) == 1, extra
        return Path(extra[0])

    def scope_files(self, suffix: str) -> list[Path]:
        return sorted((self.repo / ".git").rglob(f"*{suffix}"))

    def published(self) -> list[str]:
        return self.mark.read_text().split() if self.mark.exists() else []


@pytest.fixture
def e2e(tmp_path):
    return _Env(tmp_path)


def test_rel15_fresh_release_end_to_end(e2e):
    result = e2e.release()

    assert result.returncode == 0, result.log
    assert f"refs/tags/{_TAG}" in e2e.origin_tags()
    assert e2e.published() == ["pub"]
    # The tagged candidate is what landed on origin/main.
    e2e.git("fetch", "-q", "origin", "main", "--tags")
    assert e2e.git(
        "merge-base", "--is-ancestor", _TAG, "origin/main", check=False,
    ).returncode == 0
    # The isolated worktree and the durable candidate branch are gone, and the
    # caller's own main was synced to the release.
    assert len(e2e.worktrees()) == 1
    assert e2e.origin_heads() == ["refs/heads/main"]
    assert e2e.out("rev-parse", "main") == e2e.origin_main()
    assert "[Unreleased]" in (e2e.project / "CHANGES.md").read_text()
    assert "## [0.1.0]" in (e2e.project / "CHANGES.md").read_text()


def test_rel08_release_inputs_commit_carries_the_candidate_trailer(e2e):
    result = e2e.release()

    assert result.returncode == 0, result.log
    body = e2e.out("log", "-1", "--format=%B", "--grep=prepare release inputs", "main")
    assert body.splitlines()[0] == "chore(demo): prepare release inputs"
    assert f"Cmru-Release-Candidate: {_TAG}" in body.splitlines()
    trailers = e2e.out(
        "log", "-1", "--format=%(trailers:key=Cmru-Release-Candidate,valueonly)",
        "--grep=prepare release inputs", "main",
    )
    assert trailers == _TAG


def test_rel04_main_advancing_during_the_gate_is_merged_not_lost(e2e):
    e2e.advance_main_hook("other.txt")

    result = e2e.release()

    assert result.returncode == 0, result.log
    assert e2e.published() == ["pub"]
    assert f"refs/tags/{_TAG}" in e2e.origin_tags()
    main = e2e.origin_main()
    files = e2e.out("--git-dir", str(e2e.origin), "ls-tree", "-r", "--name-only", main, cwd=e2e.root)
    assert "other.txt" in files  # the concurrent commit survived
    subjects = e2e.out(
        "--git-dir", str(e2e.origin), "log", "--format=%s", main, cwd=e2e.root,
    ).splitlines()
    assert f"Merge origin/main into release candidate {_TAG}" in subjects
    assert "concurrent change" in subjects
    # The published tag is an ancestor of main: nothing was rebased or forced.
    e2e.git("fetch", "-q", "origin", "--tags")
    assert e2e.git(
        "merge-base", "--is-ancestor", _TAG, "origin/main", check=False,
    ).returncode == 0


def test_rel04_main_advance_touching_the_project_path_stops_with_recovery(e2e):
    e2e.advance_main_hook("demo/a.txt")

    result = e2e.release()

    assert result.returncode != 0
    assert "changed the released project's own paths" in result.log
    assert "demo/a.txt" in result.log
    # Publication already happened; the tag is kept, never rolled back.
    assert e2e.published() == ["pub"]
    assert f"refs/tags/{_TAG}" in e2e.origin_tags()
    # origin/main still holds only the concurrent commit: no force, no merge.
    subjects = e2e.out(
        "--git-dir", str(e2e.origin), "log", "--format=%s", "main", cwd=e2e.root,
    ).splitlines()
    assert subjects[0] == "concurrent change"
    assert not any(s.startswith("Merge origin/main") for s in subjects)
    # The candidate was retained for the manual merge.
    assert e2e.retained_workspace().is_dir()


def _real_candidate(e2e, branch: str):
    """A committed candidate clone whose only change is outside the project path."""
    path = e2e.root / "candidate"
    e2e.git("clone", "-q", str(e2e.origin), str(path), cwd=e2e.root)
    e2e.git("checkout", "-q", "-b", branch, cwd=path)
    base = e2e.out("rev-parse", "HEAD", cwd=path)
    (path / "README.md").write_text("candidate change\n")
    e2e.git("commit", "-q", "-am", "chore: candidate edits the root readme", cwd=path)
    return transaction.ReleaseWorkspace(e2e.repo, path, branch, base)


def test_rel04_merge_conflict_is_aborted_and_the_candidate_is_left_clean(e2e):
    workspace = _real_candidate(e2e, "cmru/release/conflict")
    e2e.advance_main_hook("README.md")
    subprocess.run(["sh", e2e.env["GATE_HOOK"]], check=True, env=e2e.env)

    with pytest.raises(RuntimeError, match="conflicted"):
        transaction.promote_workspace(
            workspace, project_paths=("demo",), release_label="demo-v9",
        )

    status = e2e.out("status", "--porcelain=v1", cwd=workspace.path)
    assert status == ""
    assert not (workspace.path / ".git" / "MERGE_HEAD").exists()
    subjects = e2e.out(
        "--git-dir", str(e2e.origin), "log", "--format=%s", "main", cwd=e2e.root,
    ).splitlines()
    assert subjects[0] == "concurrent change"


def test_rel04_promotion_gives_up_after_bounded_attempts(e2e, monkeypatch):
    workspace = _real_candidate(e2e, "cmru/release/bounded")
    other = e2e.root / "racer"
    e2e.git("clone", "-q", str(e2e.origin), str(other), cwd=e2e.root)
    counter = {"n": 0}
    real_merge = transaction._merge_origin_main_into_candidate

    def racing_merge(*args, **kwargs):
        # After every merge another commit lands, so the next push loses again.
        counter["n"] += 1
        merged = real_merge(*args, **kwargs)
        e2e.git("pull", "-q", "--rebase", "origin", "main", cwd=other)
        (other / f"race{counter['n']}.txt").write_text("x\n")
        e2e.git("add", "-A", cwd=other)
        e2e.git("commit", "-q", "-m", f"race {counter['n']}", cwd=other)
        e2e.git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)
        return merged

    monkeypatch.setattr(transaction, "_merge_origin_main_into_candidate", racing_merge)
    # Make the very first push lose too.
    (other / "race0.txt").write_text("x\n")
    e2e.git("add", "-A", cwd=other)
    e2e.git("commit", "-q", "-m", "race 0", cwd=other)
    e2e.git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=other)

    with pytest.raises(RuntimeError, match="Gave up after 3 merge attempt"):
        transaction.promote_workspace(
            workspace, project_paths=("demo",), release_label="demo-v9",
        )

    assert counter["n"] == 3


def test_rel05_failed_build_rolls_the_tag_back_and_resume_proceeds(e2e):
    e2e.fail_build.write_text("")

    failed = e2e.release()

    assert failed.returncode != 0
    assert "rolling the release tag back" in failed.log
    assert e2e.origin_tags() == {}
    assert e2e.local_tags() == []
    assert e2e.published() == []
    assert len(e2e.scope_files(".tag-absent.json")) == 1
    workspace = e2e.retained_workspace()
    assert f"cmru release --resume {workspace}" in failed.log

    e2e.fail_build.unlink()
    resumed = e2e.release("--resume", str(workspace))

    assert resumed.returncode == 0, resumed.log
    assert f"refs/tags/{_TAG}" in e2e.origin_tags()
    assert e2e.published() == ["pub"]
    assert len(e2e.worktrees()) == 1


def test_rel05_failed_publish_keeps_the_tag_and_prints_recovery(e2e):
    e2e.fail_push.write_text("")

    failed = e2e.release()

    assert failed.returncode != 0
    tags = e2e.origin_tags()
    assert f"refs/tags/{_TAG}" in tags
    assert _TAG in e2e.local_tags()
    assert "rolling the release tag back" not in failed.log
    assert f"Publishing of {_TAG} had started" in failed.log
    assert tags[f"refs/tags/{_TAG}"] in failed.log  # the exact object id
    assert f"git tag -d {_TAG}" in failed.log
    workspace = e2e.retained_workspace()

    e2e.fail_push.unlink()
    resumed = e2e.release("--resume", str(workspace))
    assert resumed.returncode != 0  # the surviving tag makes resume refuse
    assert e2e.published() == []


def test_rel06_sync_fetch_failure_still_reports_the_retained_candidate(e2e):
    # The gate fails AND leaves the caller's origin unreachable, so the
    # best-effort local-main sync after the failure cannot fetch.
    e2e.hook("break", f"""\
git -C "{e2e.repo}" remote set-url origin "{e2e.root}/does-not-exist.git"
exit 1
""")

    failed = e2e.release()

    assert failed.returncode == 1
    # (The child prints its own gate-failure traceback; only the launcher's
    # tail, after the retained line, is the REL-06 subject.)
    assert "Release transaction failed; retained " in failed.log
    launcher_tail = failed.log.split("Release transaction failed; retained ", 1)[1]
    assert "Traceback" not in launcher_tail
    assert "Could not sync local main" in failed.log
    assert Path(launcher_tail.split(" ", 1)[0]).is_dir()


def test_cli01_status_does_not_touch_the_release_log_or_redirect_output(e2e):
    log = e2e.project / "cmru.release.log"
    log.write_text("SENTINEL from the previous release\n")

    def run(*args):
        return subprocess.run(
            [e2e.env["CMRU_BIN"], *args], cwd=e2e.project, env=e2e.env,
            capture_output=True, text=True, timeout=120,
        )

    status = run("status")

    assert status.returncode == 0, status.stdout + status.stderr
    assert log.read_text() == "SENTINEL from the previous release\n"
    # Not redirected through the release tee: the preview is on this process's own
    # stdout, and the (release-only) logging flags are not accepted by status.
    assert "demo" in status.stdout + status.stderr
    for flag in ("--log-append", "--show-run-details"):
        rejected = run("status", flag)
        assert rejected.returncode == 2, flag
    assert log.read_text() == "SENTINEL from the previous release\n"


def test_m24_launcher_snapshots_origin_tags_before_the_attempt(e2e):
    e2e.git("tag", "keep-me", "HEAD")
    e2e.git("push", "-q", "origin", "keep-me")
    e2e.hook("fail", "exit 1\n")

    failed = e2e.release()

    assert failed.returncode != 0
    snapshots = e2e.scope_files(".tags.json")
    assert len(snapshots) == 1, failed.log
    recorded = json.loads(snapshots[0].read_text())
    assert recorded == {"refs/tags/keep-me": e2e.origin_tags()["refs/tags/keep-me"]}
