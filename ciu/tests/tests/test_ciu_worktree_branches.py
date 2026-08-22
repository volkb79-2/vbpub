"""S16.8 / CIU-25 (git half) — worktree branch hygiene: grounded survey + prune.

Oracles:
- A fully merged branch with no checkout (or a clean non-primary one) is
  ``prunable``; ``-y`` removes exactly that category and nothing else.
- A merged branch whose checkout is DIRTY is ``merged-dirty`` — never pruned.
- An unmerged branch stays ``unmerged`` with its attributes (ahead, changed
  files) surfaced for a human decision — never pruned regardless of age.
- The base branch and the PRIMARY checkout's branch are never candidates.
- Without ``-y`` there are NO side effects — survey + hint only.
- Controlled wrong implementation: an age-based rule would prune the unmerged
  branch; these tests fail it because the unmerged branch must survive any
  prune.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    assert _git(["add", "README.md"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0
    return repo


def _branch(repo: Path, name: str, *, commit: bool = True, file: str = "f.txt") -> None:
    # ALWAYS branch from main — chaining from the current HEAD would make one
    # test branch contain another's commits and blur the classifications.
    assert _git(["checkout", "-b", name, "main"], repo).returncode == 0
    if commit:
        (repo / file).write_text(f"{name}\n", encoding="utf-8")
        assert _git(["add", file], repo).returncode == 0
        assert _git(["commit", "-m", f"work on {name}"], repo).returncode == 0


def _merge_into_main(repo: Path, name: str) -> None:
    assert _git(["checkout", "main"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", f"merge {name}", name], repo).returncode == 0


def test_survey_classifies_merged_branch_without_checkout_as_prunable(repo):
    _branch(repo, "feature/wip")           # created first: primary leaves it
    _branch(repo, "fix/merged-no-checkout")
    _merge_into_main(repo, "fix/merged-no-checkout")  # primary back on main

    doc = worktree.branch_hygiene(repo)
    cats = {b["name"]: b["category"] for b in doc["branches"]}
    assert cats["main"] == "base"
    assert cats["fix/merged-no-checkout"] == "prunable"
    assert cats["feature/wip"] == "unmerged"
    wip = next(b for b in doc["branches"] if b["name"] == "feature/wip")
    assert wip["ahead"] == 1 and wip["behind"] >= 1 and wip["changed_files"] >= 1
    assert wip["merged"] is False


def test_prune_removes_only_prunable_and_keeps_unmerged(repo):
    """Controlled wrong implementation check: an age-based or sloppy rule
    would take feature/wip too; after the prune it MUST still exist."""
    _branch(repo, "feature/wip")
    _branch(repo, "fix/merged-a")
    _merge_into_main(repo, "fix/merged-a")

    doc = worktree.prune_branches(repo, yes=True)

    assert doc["status"] == "pruned"
    assert doc["removed"] == ["fix/merged-a"]
    assert doc["failed"] == []
    # unmerged survives; base survives; merged branch gone
    surviving = _git(["for-each-ref", "refs/heads", "--format=%(refname:short)"], repo)
    assert "feature/wip" in surviving.stdout
    assert "main" in surviving.stdout
    assert "fix/merged-a" not in surviving.stdout


def test_merged_branch_with_dirty_linked_checkout_is_merged_dirty_never_pruned(repo):
    _branch(repo, "fix/merged-dirty")
    # Park the primary back on main so the branch is free for a linked checkout.
    assert _git(["checkout", "main"], repo).returncode == 0
    wt_path = repo.parent / "wt-dirty"
    assert _git(["worktree", "add", str(wt_path), "fix/merged-dirty"], repo).returncode == 0
    # Dirt in the LINKED checkout.
    (wt_path / "scratch.txt").write_text("uncommitted\n", encoding="utf-8")
    _merge_into_main(repo, "fix/merged-dirty")

    doc = worktree.branch_hygiene(repo)
    entry = next(b for b in doc["branches"] if b["name"] == "fix/merged-dirty")
    assert entry["category"] == "merged-dirty"

    pruned = worktree.prune_branches(repo, yes=True)
    assert pruned["removed"] == []
    assert pruned["status"] == "pruned"  # nothing failed — simply not a candidate
    assert _git(["rev-parse", "--verify", "fix/merged-dirty"], repo).returncode == 0
    assert wt_path.exists()


def test_primary_branch_is_current_and_mainline_never_prunable(repo):
    """Measuring against ANOTHER base must never make the mainline a removal
    candidate — 'clean up merged branches' can never mean deleting main."""
    _branch(repo, "develop")
    # primary now sits on develop; main is fully merged INTO develop.
    doc = worktree.branch_hygiene(repo, base="develop")
    cats = {b["name"]: b["category"] for b in doc["branches"]}
    assert cats["develop"] == "base"
    assert cats["main"] in ("mainline", "current")
    assert worktree._default_branch(repo) is None  # no origin → policy fallback

    # Here base==primary HEAD (develop), so the destructive pass is legal;
    # the base-vs-HEAD refusal itself is covered by
    # test_yes_refuses_when_base_not_contained_in_any_head. Nothing prunable
    # exists (main is mainline-protected, develop is the base).
    pruned = worktree.prune_branches(repo, base="develop", yes=True)
    assert "main" not in pruned["removed"]
    assert _git(["rev-parse", "--verify", "main"], repo).returncode == 0


def test_mainline_guard_uses_origin_head_when_available(repo):
    """With an origin/HEAD symbolic ref, ITS target is the protected mainline
    (precedence over the fallback names) — and mainline wins even when the
    branch is also unmerged."""
    _branch(repo, "trunk")
    assert _git(["update-ref", "refs/remotes/origin/trunk", "main"], repo).returncode == 0
    assert _git(
        ["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"], repo
    ).returncode == 0
    assert worktree._default_branch(repo) == "trunk"
    # trunk IS the declared default → mainline, whatever its mergedness.
    doc = worktree.branch_hygiene(repo, base="main")
    entry = next(b for b in doc["branches"] if b["name"] == "trunk")
    assert entry["category"] == "mainline"


def test_linked_worktree_on_clean_merged_branch_is_prunable_with_checkout(repo):
    _branch(repo, "fix/linked")
    _merge_into_main(repo, "fix/linked")
    wt_path = repo.parent / "wt-linked"
    assert _git(["worktree", "add", str(wt_path), "fix/linked"], repo).returncode == 0

    doc = worktree.branch_hygiene(repo)
    entry = next(b for b in doc["branches"] if b["name"] == "fix/linked")
    assert entry["category"] == "prunable"
    assert entry["checkout"] == str(wt_path)

    pruned = worktree.prune_branches(repo, yes=True)
    assert pruned["removed"] == ["fix/linked"]
    assert not wt_path.exists()
    assert _git(["rev-parse", "--verify", "fix/linked"], repo).returncode != 0


def test_unknown_base_ref_refuses_loudly(repo):
    with pytest.raises(worktree.WorktreeError, match="LOCAL BRANCH"):
        worktree.branch_hygiene(repo, base="trunk")


def test_survey_without_yes_has_no_side_effects_and_hints(repo):
    _branch(repo, "fix/merged-x")
    _merge_into_main(repo, "fix/merged-x")

    doc = worktree.branch_hygiene(repo)
    assert doc["status"] == "survey"
    assert "re-run with -y/--yes" in doc["hint"]
    assert _git(["rev-parse", "--verify", "fix/merged-x"], repo).returncode == 0


def test_branches_document_is_versioned_envelope(repo):
    _branch(repo, "feature/z")
    doc = worktree.branch_hygiene(repo)
    assert doc["schema_version"] == worktree.BRANCHES_SCHEMA_VERSION == 1
    assert doc["operation"] == "branches"
    assert set(doc["counts"]) == set(worktree.BRANCH_CATEGORIES)
    assert {b["category"] for b in doc["branches"]} <= set(worktree.BRANCH_CATEGORIES)


def test_ciu_instance_linkage_surfaces_in_survey(repo, tmp_path, monkeypatch):
    """A managed instance's record links the branch to its lifecycle state."""
    _branch(repo, "feat/managed")
    assert _git(["checkout", "main"], repo).returncode == 0
    wt_path = repo.parent / "wt-managed"
    assert _git(["worktree", "add", str(wt_path), "feat/managed"], repo).returncode == 0
    # Fabricate a minimal valid instance record at the linked checkout's root,
    # as `worktree create` would have written.
    record = {
        "schema_version": 1,
        "logical_name": "managed",
        "display_name": "managed",
        "branch": "feat/managed",
        "git_worktree_path": str(wt_path),
        "ciu_root_offset": ".",
        "created_at_utc": "2026-08-22T00:00:00+00:00",
        "base_ref": "main",
        "state": "ready",
        "runtime": {"instance_id": "abc123", "network": "repo-abc123-network"},
        "recovery_status": None,
    }
    import json

    (wt_path / worktree.WORKTREE_INSTANCE_RECORD).write_text(
        json.dumps(record), encoding="utf-8"
    )

    doc = worktree.branch_hygiene(repo)
    entry = next(b for b in doc["branches"] if b["name"] == "feat/managed")
    assert entry["ciu_instance"] == {"logical_name": "managed", "state": "ready"}


# ---------------------------------------------------------------------------
# Fail-closed git paths and prune failure reporting (coverage of S16.8 edges)
# ---------------------------------------------------------------------------


def test_for_each_ref_failure_refuses(repo, monkeypatch):
    real_git = worktree._git

    def boom(args, cwd):
        if args[0] == "for-each-ref":
            return subprocess.CompletedProcess(args, 1, "", "fatal: bad object")
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", boom)
    with pytest.raises(worktree.WorktreeError, match="for-each-ref failed"):
        worktree.branch_hygiene(repo)


def test_unreadable_rev_list_or_diff_degrades_to_sentinel_minus_one(repo, monkeypatch):
    _branch(repo, "feature/x")
    real_git = worktree._git

    def selective(args, cwd):
        if args[0] in ("rev-list", "diff"):
            return subprocess.CompletedProcess(args, 1, "", "error")
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", selective)
    doc = worktree.branch_hygiene(repo)
    entry = next(b for b in doc["branches"] if b["name"] == "feature/x")
    assert entry["ahead"] == -1 and entry["behind"] == -1
    assert entry["changed_files"] == -1
    assert entry["merged"] is False  # -1 is never read as "merged"


def test_for_each_ref_blank_lines_and_short_rows_are_tolerated(repo, monkeypatch):
    real_git = worktree._git

    def with_noise(args, cwd):
        res = real_git(args, cwd)
        if args[0] == "for-each-ref":
            # a blank line and a truncated (3-field) row must not crash
            res.stdout = "\n" + res.stdout + "\ntruncated\x00row\x00here\n"
        return res

    monkeypatch.setattr(worktree, "_git", with_noise)
    doc = worktree.branch_hygiene(repo)
    assert any(b["name"] == "main" for b in doc["branches"])


def test_prune_reports_per_branch_failure_and_continues(repo, monkeypatch):
    """A refusal on one prunable branch moves THAT branch to `failed` with the
    reason and the prune continues — status becomes `partial`, never `pruned`
    while a survivor remains."""
    _branch(repo, "fix/merged-1")
    _merge_into_main(repo, "fix/merged-1")
    _branch(repo, "fix/merged-2")
    _merge_into_main(repo, "fix/merged-2")  # leaves primary on main
    real_git = worktree._git

    def failing_branch_d(args, cwd):
        if args[:2] == ["branch", "-d"] and "fix/merged-1" in args:
            return subprocess.CompletedProcess(args, 1, "", "error: branch not fully merged")
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", failing_branch_d)
    doc = worktree.prune_branches(repo, yes=True)

    assert doc["status"] == "partial"
    assert doc["failed"] == [
        {"branch": "fix/merged-1", "reason": "error: branch not fully merged"}
    ]
    assert "fix/merged-2" in doc["removed"]
    assert _git(["rev-parse", "--verify", "fix/merged-1"], repo).returncode == 0


def test_prune_failed_worktree_removal_skips_branch_delete(repo, monkeypatch):
    """When `git worktree remove` refuses, the branch delete is not attempted —
    the failure names the worktree step's reason."""
    _branch(repo, "fix/linked")
    assert _git(["checkout", "main"], repo).returncode == 0  # free the branch
    wt_path = repo.parent / "wt-linked"
    assert _git(["worktree", "add", str(wt_path), "fix/linked"], repo).returncode == 0
    (wt_path / "committed.txt").write_text("clean\n", encoding="utf-8")
    _git(["add", "."], cwd=wt_path)
    _git(["commit", "-m", "clean work"], cwd=wt_path)
    _merge_into_main(repo, "fix/linked")  # primary back on main; linked checkout stays clean
    real_git = worktree._git
    seen: list[list[str]] = []

    def failing_remove(args, cwd):
        seen.append(args)
        if args[:2] == ["worktree", "remove"]:
            return subprocess.CompletedProcess(args, 1, "", "fatal: contains modified files")
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", failing_remove)
    doc = worktree.prune_branches(repo, yes=True)

    assert doc["status"] == "partial"
    assert doc["failed"] == [
        {"branch": "fix/linked", "reason": "fatal: contains modified files"}
    ]
    assert not any(args[:2] == ["branch", "-d"] for args in seen)


# ---------------------------------------------------------------------------
# CLI dispatch (`ciu worktree branches`) — human, JSON, and -y paths
# ---------------------------------------------------------------------------


def test_cli_branches_survey_human_output(repo, monkeypatch, capsys):
    monkeypatch.delenv("REPO_ROOT", raising=False)  # ambient shell state must not redirect
    from ciu import cli

    monkeypatch.chdir(repo)
    _branch(repo, "fix/merged-cli")
    _merge_into_main(repo, "fix/merged-cli")

    assert cli._worktree(["branches", "--define-root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "branch hygiene vs 'main'" in out
    assert "1 prunable" in out
    assert "prunable:" in out
    assert "fix/merged-cli" in out
    assert "re-run with -y/--yes" in out


def test_cli_branches_json_dispatch(repo, monkeypatch, capsys):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    import json as _json

    from ciu import cli

    monkeypatch.chdir(repo)
    _branch(repo, "fix/merged-cli")
    _merge_into_main(repo, "fix/merged-cli")

    assert cli._worktree(["branches", "--json", "--define-root", str(repo)]) == 0
    doc = _json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    assert doc["operation"] == "branches"
    assert doc["status"] == "survey"
    assert {b["name"] for b in doc["branches"]} >= {"main", "fix/merged-cli"}


def test_cli_branches_yes_prunes_and_reports(repo, monkeypatch, capsys):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    from ciu import cli

    monkeypatch.chdir(repo)
    _branch(repo, "fix/merged-cli")
    _merge_into_main(repo, "fix/merged-cli")

    assert cli._worktree(["branches", "-y", "--define-root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "0 prunable" in out  # already removed
    assert _git(["rev-parse", "--verify", "fix/merged-cli"], repo).returncode != 0


def test_cli_branches_unknown_base_refuses_exit_2(repo, monkeypatch, capsys):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    from ciu import cli

    monkeypatch.chdir(repo)
    assert cli._worktree(["branches", "--base", "trunk", "--define-root", str(repo)]) == 2
    assert "LOCAL BRANCH" in capsys.readouterr().err


def test_prune_without_yes_is_pure_survey(repo):
    _branch(repo, "fix/merged-y")
    _merge_into_main(repo, "fix/merged-y")

    doc = worktree.prune_branches(repo, yes=False)
    assert doc["status"] == "survey"
    assert doc["operation"] == "branches"
    assert any(b["name"] == "fix/merged-y" for b in doc["branches"])
    assert _git(["rev-parse", "--verify", "fix/merged-y"], repo).returncode == 0


# ---------------------------------------------------------------------------
# Review fixes — destructive pass safety (local-branch base, HEAD-agreement
# guard, upstream pre-check, honest failure surfacing)
# ---------------------------------------------------------------------------


def test_base_must_be_a_local_branch(repo):
    """A SHA or remote-tracking ref is refused: the prune reasons about branch
    NAMES, and a SHA anchor once let the anchor branch itself classify
    prunable (review finding 8)."""
    sha = _git(["rev-parse", "main"], repo).stdout.strip()
    with pytest.raises(worktree.WorktreeError, match="LOCAL BRANCH"):
        worktree.branch_hygiene(repo, base=sha)
    with pytest.raises(worktree.WorktreeError, match="LOCAL BRANCH"):
        worktree.prune_branches(repo, base=sha, yes=True)


def test_feature_main_is_never_the_base(repo):
    """An exact-name-only base match: 'feature/main' has its own work and must
    stay visible to the actionable categories (review finding 9)."""
    _branch(repo, "feature/main")
    assert _git(["checkout", "main"], repo).returncode == 0
    doc = worktree.branch_hygiene(repo)
    cats = {b["name"]: b["category"] for b in doc["branches"]}
    assert cats["feature/main"] == "unmerged"
    assert cats["main"] == "base"


def test_yes_refuses_when_base_not_contained_in_any_head(repo):
    """The reproduced review BLOCKER: base=develop while primary HEAD is main.
    A branch merged into develop would previously lose its checkout while git
    refused the deletion — half-pruned state, silent success. Now -y refuses
    UPFRONT and nothing is touched."""
    _branch(repo, "feature/wip")
    # develop must CONTAIN work main lacks, else it is trivially contained.
    assert _git(["checkout", "-b", "develop", "main"], repo).returncode == 0
    (repo / "dev.txt").write_text("dev\n", encoding="utf-8")
    assert _git(["add", "dev.txt"], repo).returncode == 0
    assert _git(["commit", "-m", "dev work"], repo).returncode == 0
    _branch(repo, "fix/on-develop", file="d.txt")
    # merge into develop (NOT main): primary HEAD stays main
    assert _git(["checkout", "develop"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m", "fix/on-develop"], repo).returncode == 0
    assert _git(["checkout", "main"], repo).returncode == 0
    wt_path = repo.parent / "wt-dev"
    assert _git(["worktree", "add", str(wt_path), "fix/on-develop"], repo).returncode == 0

    with pytest.raises(worktree.WorktreeError, match="not contained in any"):
        worktree.prune_branches(repo, base="develop", yes=True)

    # nothing was touched
    assert wt_path.exists()
    assert _git(["rev-parse", "--verify", "fix/on-develop"], repo).returncode == 0


def test_upstream_blocked_candidate_reported_without_destruction(repo, tmp_path):
    """A branch tracking an upstream that lacks its tip would be refused by
    `branch -d` AFTER its checkout was already gone — the pre-check reports it
    as failed while everything is still intact."""
    # bare "origin" + pushed branch
    bare = tmp_path / "origin.git"
    assert _git(["init", "--bare", "-b", "main", str(bare)], repo).returncode == 0
    assert _git(["remote", "add", "origin", str(bare)], repo).returncode == 0
    _branch(repo, "fix/tracked")
    assert _git(["push", "-u", "origin", "fix/tracked"], repo).returncode == 0
    # advance LOCAL past its upstream, then merge into main (primary HEAD)
    (repo / "f.txt").write_text("second\n", encoding="utf-8")
    assert _git(["commit", "-am", "second"], repo).returncode == 0
    assert _git(["checkout", "main"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m", "fix/tracked"], repo).returncode == 0

    doc = worktree.prune_branches(repo, yes=True)

    assert [f["branch"] for f in doc["failed"]] == ["fix/tracked"]
    assert "upstream" in doc["failed"][0]["reason"]
    assert doc["status"] == "partial"
    assert _git(["rev-parse", "--verify", "fix/tracked"], repo).returncode == 0


def test_cli_prune_surfaces_removed_failed_and_exits_nonzero_on_partial(
    repo, monkeypatch, capsys
):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    from ciu import cli

    _branch(repo, "fix/m-a")
    _merge_into_main(repo, "fix/m-a")
    _branch(repo, "fix/m-b")
    _merge_into_main(repo, "fix/m-b")  # primary back on main
    real_git = worktree._git

    def failing(args, cwd):
        if args[:2] == ["branch", "-d"] and "fix/m-a" in args:
            return subprocess.CompletedProcess(args, 1, "", "error: not fully merged")
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", failing)
    code = cli._worktree(["branches", "-y", "--define-root", str(repo)])
    out = capsys.readouterr().out

    assert code == 1  # partial prune is NOT a silent success
    assert "removed: fix/m-b" in out
    assert "FAILED: fix/m-a" in out
