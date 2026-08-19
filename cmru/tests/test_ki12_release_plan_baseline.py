"""KI-12: the release plan must be a true function of the pushed repository
(S12.2a), and a tag AHEAD of the snapshot commit must abort rather than look
like a genuinely unchanged project (S12.2b) -- but a tag EQUAL to the
snapshot commit is the ordinary, benign state right after a completed
release and must never abort.

Adversarial review found the original S12.2a fix verified only a tag's NAME
against origin, not its OBJECT -- a hand-made local tag sharing a name with a
genuinely-published one at a DIFFERENT commit passed unnoticed, and a newer
origin-only tag this local clone never fetched was invisible to the plan
entirely. Both are now checked directly against the resolved commit SHAs.

Uses real temporary git repos with a real ``origin`` remote (a bare clone) —
these defects are specifically about local-vs-pushed state, which cannot be
faked with mocks without also faking the very distinction under test.
"""
from __future__ import annotations

import subprocess
import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import version
from cmru.version import (
    ReleasePlanRefused,
    _latest_tag_for_prefix,
    _tag_covers_head,
    _tag_head_relationship,
    _tag_pushed_to_origin,
    detect_changed_projects,
)


# ---------------------------------------------------------------------------
# Real-repo harness: a clone with a real `origin` bare remote.
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(f"git {args} failed:\n{result.stderr}")
    return result.stdout.strip()


class _Repo:
    """A real git repo with a real ``origin`` remote (a bare clone), so
    "pushed" vs. "local-only" is a genuine distinction, not a mock."""

    def __enter__(self) -> "_Repo":
        self.tmp = Path(tempfile.mkdtemp(prefix="cmru_ki12_test_"))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "test@example.invalid")
        _git(self.root, "config", "user.name", "test")
        (self.root / "README.md").write_text("init\n")
        (self.root / "demo").mkdir()
        (self.root / "demo" / "x.py").write_text("x = 1\n")
        (self.root / "other").mkdir()
        (self.root / "other" / "y.py").write_text("y = 1\n")
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

    def tag(self, name: str, *, ref: str = "HEAD", push: bool = False, annotated: bool = True) -> None:
        if annotated:
            _git(self.root, "tag", "-a", name, "-m", f"release {name}", ref)
        else:
            _git(self.root, "tag", name, ref)
        if push:
            _git(self.root, "push", "-q", "origin", name)

    def retag_locally(self, name: str, ref: str, *, annotated: bool = True) -> None:
        """Delete the local tag (if any) and recreate it at ``ref``, WITHOUT
        pushing -- diverges the local tag from whatever origin already has
        under this exact name (a hand-made tag created over an existing
        published one, sharing its name but not its commit)."""
        subprocess.run(["git", "tag", "-d", name], cwd=self.root, capture_output=True, text=True)
        self.tag(name, ref=ref, push=False, annotated=annotated)

    def push_and_forget_tag(self, name: str, *, ref: str = "HEAD", annotated: bool = True) -> None:
        """Create+push a tag, then delete the LOCAL ref -- origin keeps it,
        but ``git tag --list`` here no longer shows it. Simulates a clone
        that never fetched a tag another operator already published."""
        self.tag(name, ref=ref, push=True, annotated=annotated)
        _git(self.root, "tag", "-d", name)

    def tag_ahead(self, name: str, path: str = "demo/x.py") -> str:
        """Simulate a previous release that tagged and pushed, but never
        promoted `origin/main` to that commit: commit once more, tag and push
        THAT commit, then reset local HEAD back to where it was -- exactly
        the "ahead" state in S12.2b. Returns the commit HEAD was reset to
        (the "snapshot" commit the plan will actually evaluate against)."""
        before = self.head()
        self.commit(path, f"feat: prepares {name}")
        self.tag(name, push=True)
        _git(self.root, "reset", "-q", "--hard", before)
        return before

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD")

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
# _tag_pushed_to_origin -- name AND object must both match (the blocking fix)
# ---------------------------------------------------------------------------

def test_tag_pushed_to_origin_true_when_annotated_object_matches():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        assert _tag_pushed_to_origin(repo.root, "demo-v1.0.0") is True


def test_tag_pushed_to_origin_true_when_lightweight_object_matches():
    """Must-succeed control, lightweight variant: a lightweight tag has no
    peeled ^{} line at all -- the plain ls-remote line already IS the commit."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True, annotated=False)
        assert _tag_pushed_to_origin(repo.root, "demo-v1.0.0") is True


def test_tag_pushed_to_origin_false_for_a_genuinely_absent_tag():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=False)  # local-only, never pushed under this name at all
        assert _tag_pushed_to_origin(repo.root, "demo-v1.0.0") is False


def test_tag_pushed_to_origin_raises_when_annotated_name_matches_but_object_differs():
    """THE blocking fix: a local tag can share a NAME with a genuinely
    published one while pointing at a DIFFERENT commit -- checking the name
    alone would pass this. Must refuse, naming both SHAs."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        remote_commit = repo.head()
        repo.commit("demo/x.py", "feat: more")
        repo.retag_locally("demo-v1.0.0", "HEAD")  # hand-made, same name, newer commit
        local_commit = _git(repo.root, "rev-parse", "demo-v1.0.0^{commit}")
        assert local_commit != remote_commit
        with pytest.raises(ReleasePlanRefused, match="disagrees with origin") as excinfo:
            _tag_pushed_to_origin(repo.root, "demo-v1.0.0")
        message = str(excinfo.value)
        assert local_commit in message
        assert remote_commit in message


def test_tag_pushed_to_origin_raises_when_lightweight_name_matches_but_object_differs():
    """The same blocking-fix probe, lightweight variant."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True, annotated=False)
        remote_commit = repo.head()
        repo.commit("demo/x.py", "feat: more")
        repo.retag_locally("demo-v1.0.0", "HEAD", annotated=False)
        local_commit = _git(repo.root, "rev-parse", "demo-v1.0.0^{commit}")
        assert local_commit != remote_commit
        with pytest.raises(ReleasePlanRefused, match="disagrees with origin") as excinfo:
            _tag_pushed_to_origin(repo.root, "demo-v1.0.0")
        message = str(excinfo.value)
        assert local_commit in message
        assert remote_commit in message


def test_tag_pushed_to_origin_raises_when_origin_is_unreachable():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=False)
        _git(repo.root, "remote", "remove", "origin")
        with pytest.raises(ReleasePlanRefused, match="cannot verify"):
            _tag_pushed_to_origin(repo.root, "demo-v1.0.0")


def test_tag_pushed_to_origin_raises_when_ls_remote_output_is_unparseable(monkeypatch):
    """Defensive guard: the exact two-refspec query this function sends
    should, with real git, only ever come back with lines matching one of
    the two exact patterns it parses -- not reachable through real git, so
    exercised directly against a forced, malformed response. Refuses
    explicitly rather than silently comparing against None."""
    class _FakeResult:
        returncode = 0
        stdout = "deadbeef\trefs/tags/some-other-unrelated-tag\n"
        stderr = ""

    monkeypatch.setattr(version.subprocess, "run", lambda *a, **k: _FakeResult())
    with pytest.raises(ReleasePlanRefused, match="could not be parsed"):
        _tag_pushed_to_origin(Path("/nonexistent"), "demo-v1.0.0")


def test_highest_remote_tag_for_prefix_skips_ls_remote_lines_outside_refs_tags(monkeypatch):
    """Defensive guard: `--tags` should mean every returned line is a
    refs/tags/* ref -- skip anything else rather than mis-parse it. Not
    reachable through real git; exercised directly against a forced
    response that mixes in an unrelated ref."""
    class _FakeResult:
        returncode = 0
        stdout = "deadbeef\trefs/heads/main\ncafebabe\trefs/tags/demo-v1.0.0\n"
        stderr = ""

    monkeypatch.setattr(version.subprocess, "run", lambda *a, **k: _FakeResult())
    result = version._highest_remote_tag_for_prefix(Path("/nonexistent"), "demo-v", lambda t: t)
    assert result == "demo-v1.0.0"


# ---------------------------------------------------------------------------
# _latest_tag_for_prefix(require_pushed=...)
# ---------------------------------------------------------------------------

def test_latest_tag_for_prefix_default_ignores_publication_state():
    """Must-succeed control: today's plain local read (require_pushed=False,
    the default) is untouched by KI-12a -- an unpushed tag is still returned,
    for callers (status/changelog) that don't opt into the stricter guarantee."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=False)
        assert _latest_tag_for_prefix(repo.root, "demo-v") == "demo-v1.0.0"


def test_latest_tag_for_prefix_require_pushed_refuses_a_local_only_tag():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=False)
        with pytest.raises(ReleasePlanRefused, match="not present on origin") as excinfo:
            _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True)
        message = str(excinfo.value)
        assert "demo-v1.0.0" in message
        assert "S12.2a" in message
        assert "git push origin demo-v1.0.0" in message
        assert "git tag -d demo-v1.0.0" in message


def test_latest_tag_for_prefix_require_pushed_succeeds_for_a_pushed_tag():
    """Must-succeed control for the probe above: identical setup, but the tag
    was actually pushed -- require_pushed=True returns it normally."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        assert _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True) == "demo-v1.0.0"


def test_latest_tag_for_prefix_require_pushed_distinguishes_unreachable_from_absent():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=False)
        _git(repo.root, "remote", "remove", "origin")
        with pytest.raises(ReleasePlanRefused, match="cannot"):
            _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True)


def test_latest_tag_for_prefix_require_pushed_refuses_an_unreachable_origin_even_for_first_release():
    """An unreachable origin is never silently treated as "nothing there
    yet" -- even the first-release case must be able to verify that."""
    with _Repo() as repo:
        _git(repo.root, "remote", "remove", "origin")
        with pytest.raises(ReleasePlanRefused, match="cannot"):
            _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True)


def test_latest_tag_for_prefix_require_pushed_first_release_with_a_reachable_empty_origin_succeeds():
    """Must-succeed control for the probe above: a genuinely first release --
    local has nothing, and a REACHABLE origin has nothing for this prefix
    either -- returns None cleanly, no refusal."""
    with _Repo() as repo:
        assert _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True) is None


# --- the second blocking-fix scenario: a newer origin-only tag -------------

def test_latest_tag_for_prefix_require_pushed_refuses_when_origin_has_a_newer_tag():
    """THE second blocking-fix probe: origin carries a higher matching tag
    than this local clone has ever fetched. A stale local read would derive
    a version that already exists and fail mid-release after promoting
    main -- exactly the half-completed state S12.2b now aborts on."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        repo.commit("demo/x.py", "feat: more")
        repo.push_and_forget_tag("demo-v2.0.0")  # origin has it; this clone "never fetched" it
        with pytest.raises(ReleasePlanRefused, match="newer tag") as excinfo:
            _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True)
        message = str(excinfo.value)
        assert "demo-v2.0.0" in message
        assert "demo-v1.0.0" in message
        assert "fetch tags" in message


def test_latest_tag_for_prefix_require_pushed_succeeds_when_local_already_has_the_newest():
    """Must-succeed control: same two tags, but the local clone actually has
    demo-v2.0.0 too (no staleness) -- proceeds normally, no refusal."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        repo.commit("demo/x.py", "feat: more")
        repo.tag("demo-v2.0.0", push=True)  # kept locally, unlike push_and_forget_tag
        assert _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True) == "demo-v2.0.0"


def test_latest_tag_for_prefix_require_pushed_refuses_first_release_when_origin_secretly_has_a_tag():
    """The most extreme form of staleness: local believes this is a first
    release (no matching tags at all), but origin already has one."""
    with _Repo() as repo:
        repo.push_and_forget_tag("demo-v1.0.0")
        with pytest.raises(ReleasePlanRefused, match="newer tag") as excinfo:
            _latest_tag_for_prefix(repo.root, "demo-v", require_pushed=True)
        message = str(excinfo.value)
        assert "demo-v1.0.0" in message
        assert "none locally" in message


# ---------------------------------------------------------------------------
# _tag_covers_head
# ---------------------------------------------------------------------------

def test_tag_covers_head_false_when_tag_is_behind_head():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        repo.commit("demo/x.py", "feat: more")
        assert _tag_covers_head(repo.root, "demo-v1.0.0") is False


def test_tag_covers_head_true_when_tag_is_exactly_at_head():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        assert _tag_covers_head(repo.root, "demo-v1.0.0") is True


def test_tag_covers_head_raises_on_an_unresolvable_ref():
    with _Repo() as repo:
        with pytest.raises(ReleasePlanRefused, match="cannot compare HEAD"):
            _tag_covers_head(repo.root, "does-not-exist-v9")


# ---------------------------------------------------------------------------
# _tag_head_relationship -- the three-state classification (S12.2b)
# ---------------------------------------------------------------------------

def test_tag_head_relationship_equal_when_tag_is_exactly_at_head():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        assert _tag_head_relationship(repo.root, "demo-v1.0.0") == "equal"


def test_tag_head_relationship_ahead_when_tag_is_a_strict_descendant_of_head():
    with _Repo() as repo:
        repo.tag_ahead("demo-v1.0.0")
        assert _tag_head_relationship(repo.root, "demo-v1.0.0") == "ahead"


def test_tag_head_relationship_behind_when_tag_is_a_strict_ancestor_of_head():
    with _Repo() as repo:
        repo.tag("demo-v1.0.0")
        repo.commit("demo/x.py", "feat: more")
        assert _tag_head_relationship(repo.root, "demo-v1.0.0") == "behind"


# ---------------------------------------------------------------------------
# detect_changed_projects: check_tag_at_head / allow_tag_ahead_of_head /
# require_pushed_baseline
# ---------------------------------------------------------------------------

def test_detect_changed_projects_default_keeps_folding_tag_at_head_into_skip():
    """Must-succeed control: all new knobs default to False, so today's
    plain behaviour is exactly preserved unless a caller opts in."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)  # at HEAD, published -- still just "unchanged" by default
        changed = detect_changed_projects(repo.root, {"demo": _proj("demo")})
        assert changed == []


def test_pushed_tag_exactly_at_head_is_a_benign_informative_skip_not_an_abort(capsys):
    """THE regression this correction exists to fix: a pushed tag exactly at
    the snapshot commit is the ORDINARY state right after a completed
    release (e.g. running the plan again with nothing new landed anywhere).
    It must never abort, never be blamed on a hand-made tag, and never
    advise `git tag -d` a legitimate release tag -- it's named and skipped."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )
    assert changed == []
    output = capsys.readouterr().out
    assert "demo" in output and "demo-v1.0.0" in output
    assert "already released" in output
    assert "snapshot commit" in output
    assert "hand" not in output.lower()
    assert "tag -d" not in output


def test_equal_state_message_prints_even_when_ahead_is_overridden(capsys):
    """`allow_tag_ahead_of_head` must never suppress the "equal" informative
    message -- that state was never an abort to begin with, so there is
    nothing for the override to downgrade."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
            allow_tag_ahead_of_head=True,
        )
    assert changed == []
    assert "already released" in capsys.readouterr().out


def test_check_tag_at_head_false_suppresses_the_informative_message_too():
    """Must-succeed control for the two probes above: with check_tag_at_head
    itself False (status/changelog's default), none of this analysis runs at
    all -- no print, no raise, today's plain silent skip."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")}, require_pushed_baseline=True,
        )
        assert changed == []


def test_detect_changed_projects_one_commit_back_releases_normally():
    """Must-succeed control for the "ahead" probe below: the SAME shape, but
    with one more commit under the project's own path landed after the tag
    -- the plan must proceed exactly as it would without either new check."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        repo.commit("demo/x.py", "feat: one more commit")
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )
        assert [c[0] for c in changed] == ["demo"]
        assert changed[0][2] == "demo-v1.0.0"


def test_detect_changed_projects_aborts_when_tag_is_ahead_of_head():
    """The genuine anomaly (S12.2b's "ahead" state): a tag is pushed to a
    commit not yet in the snapshot's history at all -- almost always a
    previous release that tagged+pushed but never promoted origin/main."""
    with _Repo() as repo:
        before = repo.tag_ahead("demo-v1.0.0")
        with pytest.raises(ReleasePlanRefused) as excinfo:
            detect_changed_projects(
                repo.root, {"demo": _proj("demo")},
                require_pushed_baseline=True, check_tag_at_head=True,
            )
        message = str(excinfo.value)
        assert "demo" in message
        assert "demo-v1.0.0" in message
        assert "AHEAD" in message
        assert before[:8] in message
        assert "half-completed" in message
        assert "--allow-tag-ahead-of-head" in message
        assert "hand" not in message.lower()  # this is not the hand-made-tag case


def test_tag_actually_promoted_to_head_releases_as_an_ordinary_benign_skip():
    """Paired must-succeed control for the probe above: the exact same
    tag-creation shape, except this time promotion actually completed (HEAD
    was never reset back) -- so the tag's commit IS the snapshot commit
    ("equal", not "ahead"), and the plan must not abort."""
    with _Repo() as repo:
        repo.commit("demo/x.py", "feat: prepares demo-v1.0.0")
        repo.tag("demo-v1.0.0", push=True)  # not reset back -- this IS HEAD
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )
        assert changed == []


def test_allow_tag_ahead_of_head_downgrades_exactly_that_refusal_and_nothing_else():
    """allow_tag_ahead_of_head=True must downgrade ONLY the "ahead" refusal
    for the project it applies to -- it must never mask an unrelated
    project's require_pushed_baseline refusal in the very same call."""
    with _Repo() as repo:
        # "demo" is genuinely ahead -- downgraded to a silent skip.
        repo.tag_ahead("demo-v1.0.0")
        # "other" has a hand-made, unpushed tag -- a completely different defect
        # that allow_tag_ahead_of_head has no business silencing.
        repo.tag("other-v1.0.0", push=False)

        with pytest.raises(ReleasePlanRefused, match="not present on origin") as excinfo:
            detect_changed_projects(
                repo.root, {"demo": _proj("demo"), "other": _proj("other")},
                require_pushed_baseline=True, check_tag_at_head=True,
                allow_tag_ahead_of_head=True,
            )
        assert "other-v1.0.0" in str(excinfo.value)
        assert "demo-v1.0.0" not in str(excinfo.value)


def test_allow_tag_ahead_of_head_alone_skips_silently_with_no_other_defect_present():
    """The same downgrade in isolation (no second project, no unrelated
    defect): a plain, ordinary skip -- not an error, not "changed"."""
    with _Repo() as repo:
        repo.tag_ahead("demo-v1.0.0")
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
            allow_tag_ahead_of_head=True,
        )
        assert changed == []


def test_genuinely_unchanged_project_still_skips_silently_under_both_checks():
    """The ordinary happy path must not regress: a project's tag sits BEHIND
    HEAD (some unrelated project's commit moved the repo forward), and
    nothing under this project's own paths changed since -- still a plain,
    silent skip even with both new checks turned on."""
    with _Repo() as repo:
        repo.tag("demo-v1.0.0", push=True)
        repo.commit("other/y.py", "feat: unrelated project moved on")
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )
        assert changed == []


def test_first_release_project_with_a_reachable_origin_needs_no_tag_check():
    with _Repo() as repo:
        changed = detect_changed_projects(
            repo.root, {"demo": _proj("demo")},
            require_pushed_baseline=True, check_tag_at_head=True,
        )
        assert [c[0] for c in changed] == ["demo"]
        assert changed[0][2] is None
