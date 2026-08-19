"""KI-13: the "unchanged" path must name the exact baseline tag AND the
specific reason it was skipped -- never a bare project-name list.

Before this fix, ``cmru release`` printed the baseline tag only on the
CHANGED path (``assay: assay-v2.0.0 -> assay-v2.1.0 (minor)``) and withheld
it on the UNCHANGED path (a bare ``Unchanged, skipping: ciu, cmru, assay,
...``) -- exactly backwards, since the changed case never needed
disambiguation and the unchanged case is precisely where an operator who
just committed under a project's own path cannot tell a wrong ``paths``
glob, a misplaced/unpushed tag, or a genuinely unchanged project apart.

Package A (KI-12) already shipped one informative message of this shape for
the "equal" state (a pushed tag exactly at the snapshot commit --
``already released as ... at the snapshot commit; nothing new since``).
This module covers the OTHER states that must now share that one shape
(S12.2e): "behind" (the ordinary, by-far-most-common case -- no message at
all, previously), "ahead" downgraded via ``--allow-tag-ahead-of-head``
(silent, previously), and the invariant that a first release (no prior tag)
is eligible, never printed here at all.

Real temporary git repos with a real ``origin`` remote (a bare clone) --
S12.2a/S12.2b's checks are specifically about local-vs-pushed state and
tag-vs-HEAD commit relationships, which cannot be faked with mocks without
also faking the very distinction under test.
"""
from __future__ import annotations

import subprocess
import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace

from cmru.version import detect_changed_projects


# ---------------------------------------------------------------------------
# Real-repo harness -- trimmed to what this module needs (a real `origin`
# remote, commits, and tags at chosen refs).
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(f"git {args} failed:\n{result.stderr}")
    return result.stdout.strip()


class _Repo:
    def __enter__(self) -> "_Repo":
        self.tmp = Path(tempfile.mkdtemp(prefix="cmru_ki13_test_"))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "test@example.invalid")
        _git(self.root, "config", "user.name", "test")
        (self.root / "README.md").write_text("init\n")
        for name in ("demo", "shared", "other"):
            (self.root / name).mkdir()
            (self.root / name / "x.py").write_text("x = 1\n")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-q", "-m", "chore: initial")
        self.origin = self.tmp / "origin.git"
        _git(self.tmp, "clone", "-q", "--bare", str(self.root), str(self.origin))
        _git(self.root, "remote", "add", "origin", str(self.origin))
        _git(self.root, "push", "-q", "origin", "main")
        return self

    def commit(self, path: str, msg: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text() if target.exists() else ""
        target.write_text(existing + f"# {msg}\n")
        _git(self.root, "add", path)
        _git(self.root, "commit", "-q", "-m", msg)

    def tag(self, name: str, *, ref: str = "HEAD", push: bool = True) -> None:
        _git(self.root, "tag", "-a", name, "-m", f"release {name}", ref)
        if push:
            _git(self.root, "push", "-q", "origin", name)

    def tag_ahead(self, name: str, path: str = "demo/x.py") -> str:
        """A tag pushed to a commit not (yet) in this snapshot's history at
        all -- S12.2b's "ahead" state."""
        before = self.head()
        self.commit(path, f"feat: prepares {name}")
        self.tag(name)
        _git(self.root, "reset", "-q", "--hard", before)
        return before

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD")

    def commit_of(self, ref: str) -> str:
        return _git(self.root, "rev-parse", f"{ref}^{{commit}}")

    def __exit__(self, *_exc) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _proj(name: str, *, prefix: str | None = None, paths: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        prefix=prefix or f"{name}-v",
        cwd=name,
        paths=paths or [name],
        version=SimpleNamespace(bump="conventional"),
    )


# ---------------------------------------------------------------------------
# "behind" -- the ordinary, by-far-most-common unchanged reason: no message
# at all before this fix.
# ---------------------------------------------------------------------------

def test_behind_state_names_baseline_tag_commit_and_paths(capsys):
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        expected_sha = repo.commit_of("demo-v1.0.0")[:8]
        repo.commit("other/x.py", "feat: unrelated project moves on")  # moves HEAD, not demo/

        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )

    assert changed == []
    out = capsys.readouterr().out
    assert out.strip() == (
        f"[INFO] Unchanged, skipping: demo (no commits under demo/ "
        f"since demo-v1.0.0 @ {expected_sha})"
    )


def test_behind_state_message_lists_every_watched_path(capsys):
    """A project watching more than one path (S12.3's ``project.version.paths``)
    must name ALL of them, not just the first -- the whole point is telling an
    operator which globs were actually checked."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        repo.commit("other/x.py", "feat: unrelated project moves on")

        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo", paths=["demo", "shared"])},
            require_pushed_baseline=True, check_tag_at_head=True,
        )

    assert changed == []
    out = capsys.readouterr().out
    assert "no commits under demo/, shared/ since demo-v1.0.0" in out


def test_behind_state_is_a_must_succeed_control_when_project_actually_changed():
    """Paired must-succeed control: the identical setup, but the commit lands
    UNDER the project's own path -- it must be reported CHANGED, not skipped,
    and print none of the unchanged reasoning."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        repo.commit("demo/x.py", "feat: actually touches demo")

        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )

    assert [c[0] for c in changed] == ["demo"]


# ---------------------------------------------------------------------------
# "equal" -- Package A's existing message (KI-12). Not re-litigated in depth
# here (see test_ki12_release_plan_baseline.py); this only pins that it keeps
# the same one shape KI-13 unifies everything else around.
# ---------------------------------------------------------------------------

def test_equal_state_already_released_message_is_the_shape_everything_else_matches(capsys):
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")  # not reset back -- this IS HEAD ("equal")

        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )

    assert changed == []
    out = capsys.readouterr().out
    assert out.strip() == (
        "[INFO] Unchanged, skipping: demo (already released as "
        "demo-v1.0.0 at the snapshot commit; nothing new since)"
    )


# ---------------------------------------------------------------------------
# "ahead" downgraded via --allow-tag-ahead-of-head -- silent before this fix.
# ---------------------------------------------------------------------------

def test_ahead_state_downgraded_by_the_flag_names_tag_and_the_flag_itself(capsys):
    with _Repo() as repo:
        repo.tag_ahead("demo-v1.0.0")

        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
            allow_tag_ahead_of_head=True,
        )

    assert changed == []
    out = capsys.readouterr().out
    assert out.strip() == (
        "[INFO] Unchanged, skipping: demo (tag demo-v1.0.0 is ahead of the "
        "snapshot commit; skipped via --allow-tag-ahead-of-head)"
    )


# ---------------------------------------------------------------------------
# No prior tag -- always eligible (first release), never printed as skipped.
# ---------------------------------------------------------------------------

def test_first_release_project_is_eligible_and_prints_no_unchanged_reason(capsys):
    with _Repo() as repo:
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )

    assert [c[0] for c in changed] == ["demo"]
    assert changed[0][2] is None  # no last_tag
    out = capsys.readouterr().out
    assert "Unchanged" not in out
    assert out == ""


# ---------------------------------------------------------------------------
# cmru status / changelog (check_tag_at_head=False): still today's plain,
# silent skip -- unaffected by KI-13. Paired must-succeed control for the
# "behind" probe above.
# ---------------------------------------------------------------------------

def test_check_tag_at_head_false_keeps_the_behind_state_silent(capsys):
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        repo.commit("other/x.py", "feat: unrelated project moves on")

        changed = detect_changed_projects(repo.root, {"demo": _proj("demo")})

    assert changed == []
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# Readability: one line per unchanged project, not a wall of duplicated
# prose, even at cmru's real scale (seven products).
# ---------------------------------------------------------------------------

def test_seven_unchanged_projects_each_get_exactly_one_line(capsys):
    with _Repo() as repo:
        names = ["ciu", "cmru", "assay", "topos", "pwmcp", "mdt", "tls-edge"]
        for name in names:
            (repo.root / name).mkdir()
            (repo.root / name / "x.py").write_text("x = 1\n")
        _git(repo.root, "add", ".")
        _git(repo.root, "commit", "-q", "-m", "chore: add remaining projects")
        _git(repo.root, "push", "-q", "origin", "main")
        for name in names:
            repo.tag(f"{name}-v1.0.0")  # every one "equal": tagged exactly at HEAD

        projects = {name: _proj(name) for name in names}
        changed = detect_changed_projects(
            repo.root, projects,
            require_pushed_baseline=True, check_tag_at_head=True,
        )

    assert changed == []
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == len(names)  # one line per project, not a merged wall of prose
    for name in names:
        matching = [line for line in lines if f"skipping: {name} " in line]
        assert len(matching) == 1, f"expected exactly one line for {name!r}, got {matching}"
