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

import json
import os
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
    # 2 as of ciu-P28: the closed category vocabulary widened (managed-instance)
    assert doc["schema_version"] == worktree.BRANCHES_SCHEMA_VERSION == 2
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
    assert doc["schema_version"] == 2
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


def test_yes_base_equal_to_linked_head_is_refused(repo):
    """A LINKED checkout's HEAD is NOT a safety anchor: git's branch -d judges
    against the repo HEAD (primary) and upstreams, so pruning against a
    linked-only head could still half-prune. The guard refuses; surveying
    stays allowed."""
    _branch(repo, "integration")
    assert _git(["checkout", "main"], repo).returncode == 0  # free the branch
    wt_path = repo.parent / "wt-int"
    assert _git(["worktree", "add", str(wt_path), "integration"], repo).returncode == 0
    _branch(repo, "fix/x", file="x.txt")  # distinct file: no add/add conflict
    assert _git(["checkout", "main"], repo).returncode == 0
    # merge fix/x into integration FROM the linked worktree (it owns that
    # branch), so fix/x is prunable against base=integration whose tip IS
    # the linked worktree's HEAD — exercising the sanity guard's equality path.
    assert _git(["merge", "--no-ff", "-m", "m", "fix/x"], wt_path).returncode == 0

    with pytest.raises(worktree.WorktreeError, match="not contained in any"):
        worktree.prune_branches(repo, base="integration", yes=True)
    assert _git(["rev-parse", "--verify", "fix/x"], repo).returncode == 0


def test_upstream_containing_tip_prunes_normally(repo, tmp_path):
    """The upstream PRE-CHECK happy path: upstream contains the tip → no
    failed entry from the pre-check; removal proceeds."""
    bare = tmp_path / "origin.git"
    assert _git(["init", "--bare", "-b", "main", str(bare)], repo).returncode == 0
    assert _git(["remote", "add", "origin", str(bare)], repo).returncode == 0
    _branch(repo, "fix/synced")
    assert _git(["push", "-u", "origin", "fix/synced"], repo).returncode == 0
    assert _git(["checkout", "main"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m", "fix/synced"], repo).returncode == 0

    doc = worktree.prune_branches(repo, yes=True)
    assert doc["status"] == "pruned"
    assert "fix/synced" in doc["removed"]
    assert doc["failed"] == []


def test_yes_base_ancestor_of_primary_head_is_accepted(repo):
    """The sanity guard's ANCESTOR path: an older base fully contained in the
    primary HEAD is a legal prune anchor."""
    _branch(repo, "develop", file="d.txt")
    assert _git(["checkout", "main"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m", "develop"], repo).returncode == 0
    # The candidate must be merged into THE BASE (develop), not merely into
    # main, to be prunable against this anchor.
    _branch(repo, "fix/old-base")
    assert _git(["checkout", "develop"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m2", "fix/old-base"], repo).returncode == 0
    # keep main AHEAD of develop: the ancestor arm of the sanity guard needs
    # base ⊆ primary HEAD.
    assert _git(["checkout", "main"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m3", "develop"], repo).returncode == 0

    doc = worktree.prune_branches(repo, base="develop", yes=True)
    assert doc["status"] == "pruned"
    assert "fix/old-base" in doc["removed"]


def test_yes_safety_includes_origin_head_target(repo, tmp_path):
    """origin/HEAD's target joins the safety set: pruning against it passes
    even though no local HEAD equals it."""
    bare = tmp_path / "origin.git"
    assert _git(["init", "--bare", "-b", "main", str(bare)], repo).returncode == 0
    assert _git(["remote", "add", "origin", str(bare)], repo).returncode == 0
    _branch(repo, "hub-main", file="h.txt")
    assert _git(["push", "-u", "origin", "hub-main"], repo).returncode == 0
    assert _git(
        ["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/hub-main"],
        repo,
    ).returncode == 0
    # hub-main contains nothing new... give it its own commit so it differs:
    _branch(repo, "fix/on-hub")
    assert _git(["checkout", "hub-main"], repo).returncode == 0
    (repo / "h2.txt").write_text("hub\n", encoding="utf-8")
    assert _git(["add", "h2.txt"], repo).returncode == 0
    assert _git(["commit", "-m", "hub work"], repo).returncode == 0
    assert _git(["push", "origin", "hub-main"], repo).returncode == 0
    assert _git(["checkout", "main"], repo).returncode == 0

    # survey against hub-main must NOT refuse: hub-main IS origin/HEAD's target
    worktree.prune_branches(repo, base="hub-main", yes=True)


# ---------------------------------------------------------------------------
# ciu-P28 HOTFIX regressions — each reproduces, end-to-end on a real scratch
# repo with real worktrees, one of the four defects two independent
# retrospective adversarial reviews found in already-released code.
# ---------------------------------------------------------------------------

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _write_instance_record(
    repo: Path, wt_path: Path, logical: str, branch: str, *, state: str = "ready"
) -> None:
    """Fabricate the managed-instance record `worktree create` would write,
    AND locally exclude it exactly as `worktree create` does — otherwise the
    record file itself dirties the checkout and the branch never reaches the
    `prunable` classification whose destruction is the defect under test."""
    worktree._ensure_record_is_excluded(repo, Path("."))
    (wt_path / worktree.WORKTREE_INSTANCE_RECORD).write_text(
        json.dumps({
            "schema_version": 1,
            "logical_name": logical,
            "display_name": logical,
            "branch": branch,
            "git_worktree_path": str(wt_path),
            "ciu_root_offset": ".",
            "created_at_utc": "2026-08-25T00:00:00+00:00",
            "base_ref": "main",
            "state": state,
            "runtime": {"instance_id": "abc123", "network": "repo-abc123-network"},
            "recovery_status": None,
        }),
        encoding="utf-8",
    )


def _run_ciu(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """`python -m ciu ...` as a REAL subprocess, so the assertion is on the
    process exit code itself, not on an in-process return value."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    for leaked in ("REPO_ROOT", "PHYSICAL_REPO_ROOT", "REPO_NAME", "INSTANCE_ID",
                   "DOCKER_NETWORK_INTERNAL", "PUBLIC_FQDN", "CIU_EXIT_ON"):
        env.pop(leaked, None)
    return subprocess.run(
        [sys.executable, "-m", "ciu", *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, check=False,
    )


# --- O1: a managed CIU instance is never bare-removed ----------------------


def test_managed_instance_branch_is_never_pruned_without_ciu_clean(repo):
    """REVIEW BLOCKING-1 (found twice, independently): `branches -y` ran a
    BARE `git worktree remove` on a fully-merged branch whose checkout carried
    a live managed CIU instance record — destroying the rendered config that
    tells CIU what to clean, orphaning containers/volumes/networks and
    stranding root-owned vol-* dirs. The checkout must survive."""
    _branch(repo, "feat/managed")
    assert _git(["checkout", "main"], repo).returncode == 0
    wt_path = repo.parent / "wt-managed"
    assert _git(["worktree", "add", str(wt_path), "feat/managed"], repo).returncode == 0
    _write_instance_record(repo, wt_path, "managed", "feat/managed")
    _merge_into_main(repo, "feat/managed")  # fully merged AND clean → old: prunable

    doc = worktree.branch_hygiene(repo)
    entry = next(b for b in doc["branches"] if b["name"] == "feat/managed")
    assert entry["merged"] is True and entry["dirty"] is False
    assert entry["category"] == "managed-instance"
    assert entry["ciu_instance"] == {"logical_name": "managed", "state": "ready"}
    assert "ciu worktree rm" in doc["hint"]

    pruned = worktree.prune_branches(repo, yes=True)

    assert pruned["removed"] == []
    assert pruned["failed"] == []
    assert pruned["status"] == "pruned"
    # the instance and everything that could clean it are still there
    assert wt_path.is_dir()
    assert (wt_path / worktree.WORKTREE_INSTANCE_RECORD).is_file()
    assert _git(["rev-parse", "--verify", "feat/managed"], repo).returncode == 0
    assert worktree.find_instance_record(repo, "managed") is not None


def test_managed_instance_outranks_prunable_in_every_lifecycle_state(repo):
    """The record having been LOADED is the point — not its lifecycle state.
    An `allocating`/`recovery-required` instance is if anything MORE fragile."""
    _branch(repo, "feat/half-built")
    assert _git(["checkout", "main"], repo).returncode == 0
    wt_path = repo.parent / "wt-half"
    assert _git(["worktree", "add", str(wt_path), "feat/half-built"], repo).returncode == 0
    _write_instance_record(repo, wt_path, "half", "feat/half-built", state="allocating")
    _merge_into_main(repo, "feat/half-built")

    doc = worktree.prune_branches(repo, yes=True)
    entry = next(b for b in doc["branches"] if b["name"] == "feat/half-built")
    assert entry["category"] == "managed-instance"
    assert doc["removed"] == []
    assert wt_path.is_dir()


def test_managed_instance_category_is_reported_by_the_cli(repo, monkeypatch, capsys):
    from ciu import cli

    _branch(repo, "feat/managed-cli")
    assert _git(["checkout", "main"], repo).returncode == 0
    wt_path = repo.parent / "wt-managed-cli"
    assert _git(
        ["worktree", "add", str(wt_path), "feat/managed-cli"], repo
    ).returncode == 0
    _write_instance_record(repo, wt_path, "mcli", "feat/managed-cli")
    _merge_into_main(repo, "feat/managed-cli")

    monkeypatch.chdir(repo)
    assert cli._worktree(["branches", "--define-root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "1 managed-instance" in out
    assert "managed-instance:" in out
    assert "ciu worktree rm" in out


# --- O2: mergedness judged against the PRIMARY HEAD ------------------------


def test_prune_from_linked_worktree_judges_mergedness_against_primary_head(repo):
    """REVIEW BLOCKING-2, reproduced end-to-end: invoked from a LINKED
    worktree whose own HEAD is behind main, `git branch -d` judged mergedness
    against THAT checkout's HEAD. Fully-merged branches were reported "not
    fully merged" (`removed: []`) while their checkouts were destroyed first
    anyway. The invoking checkout's HEAD must not corrupt the judgement."""
    # feat/behind forks BEFORE the merges, so its HEAD contains neither.
    _branch(repo, "feat/behind")
    assert _git(["checkout", "main"], repo).returncode == 0
    _branch(repo, "fix/merged-a")
    _merge_into_main(repo, "fix/merged-a")
    _branch(repo, "fix/merged-b")
    _merge_into_main(repo, "fix/merged-b")  # primary back on main
    wt_a = repo.parent / "wt-a"
    assert _git(["worktree", "add", str(wt_a), "fix/merged-a"], repo).returncode == 0
    wt_from = repo.parent / "wt-from"
    assert _git(["worktree", "add", str(wt_from), "feat/behind"], repo).returncode == 0
    # The premise of the defect: the invoking HEAD really does NOT contain them.
    assert _git(
        ["merge-base", "--is-ancestor", "fix/merged-a", "feat/behind"], repo
    ).returncode != 0

    doc = worktree.prune_branches(wt_from, yes=True)

    assert doc["failed"] == []
    assert doc["status"] == "pruned"
    assert sorted(doc["removed"]) == ["fix/merged-a", "fix/merged-b"]
    assert _git(["rev-parse", "--verify", "fix/merged-a"], repo).returncode != 0
    assert _git(["rev-parse", "--verify", "fix/merged-b"], repo).returncode != 0
    assert not wt_a.exists()
    # the invoking checkout and its unmerged branch are untouched
    assert wt_from.is_dir()
    assert _git(["rev-parse", "--verify", "feat/behind"], repo).returncode == 0


def test_candidate_not_contained_in_primary_head_fails_before_destruction(repo, tmp_path):
    """The other half of the same wrong-HEAD family: base sanity can pass via
    origin/HEAD alone, while `git branch -d` (which judges against the PRIMARY
    HEAD) would still refuse — AFTER the checkout was gone. The per-candidate
    HEAD pre-check reports it while everything is still intact."""
    bare = tmp_path / "origin.git"
    assert _git(["init", "--bare", "-b", "main", str(bare)], repo).returncode == 0
    assert _git(["remote", "add", "origin", str(bare)], repo).returncode == 0
    # hub-main is origin/HEAD's target and carries work main lacks.
    assert _git(["checkout", "-b", "hub-main", "main"], repo).returncode == 0
    (repo / "h.txt").write_text("hub\n", encoding="utf-8")
    assert _git(["add", "h.txt"], repo).returncode == 0
    assert _git(["commit", "-m", "hub work"], repo).returncode == 0
    assert _git(["push", "-u", "origin", "hub-main"], repo).returncode == 0
    assert _git(
        ["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/hub-main"],
        repo,
    ).returncode == 0
    # fix/on-hub is merged into hub-main only; the PRIMARY stays on main.
    _branch(repo, "fix/on-hub", file="oh.txt")
    assert _git(["checkout", "hub-main"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m", "fix/on-hub"], repo).returncode == 0
    assert _git(["push", "origin", "hub-main"], repo).returncode == 0
    assert _git(["checkout", "main"], repo).returncode == 0
    wt_hub = repo.parent / "wt-on-hub"
    assert _git(["worktree", "add", str(wt_hub), "fix/on-hub"], repo).returncode == 0

    doc = worktree.prune_branches(repo, base="hub-main", yes=True)

    assert doc["status"] == "partial"
    assert [f["branch"] for f in doc["failed"]] == ["fix/on-hub"]
    assert "PRIMARY checkout's HEAD" in doc["failed"][0]["reason"]
    assert doc["removed"] == []
    assert wt_hub.is_dir()  # nothing was destroyed ahead of the refusal
    assert _git(["rev-parse", "--verify", "fix/on-hub"], repo).returncode == 0


# --- O3: no self-destruct / no unhandled exception mid-prune ---------------


def test_prune_from_a_checkout_whose_own_branch_is_prunable_does_not_self_destruct(repo):
    """REVIEW BLOCKING-3: invoked from a checkout whose own branch was itself
    prunable, `git worktree remove` deleted the invoking cwd; the very next
    `git` call raised (cwd gone), the exception escaped mid-loop, NO document
    was returned and every later prunable branch was silently never processed.
    `fix/aaa-self` sorts BEFORE `fix/zzz-other`, so the abort happened first."""
    _branch(repo, "fix/aaa-self")
    _merge_into_main(repo, "fix/aaa-self")
    _branch(repo, "fix/zzz-other")
    _merge_into_main(repo, "fix/zzz-other")  # primary back on main
    wt_self = repo.parent / "wt-self"
    assert _git(["worktree", "add", str(wt_self), "fix/aaa-self"], repo).returncode == 0

    survey = worktree.branch_hygiene(wt_self)
    cats = {b["name"]: b["category"] for b in survey["branches"]}
    assert cats["fix/aaa-self"] == "current"   # never a candidate THIS run
    assert cats["fix/zzz-other"] == "prunable"

    doc = worktree.prune_branches(wt_self, yes=True)

    # a document IS returned, and the later branch WAS processed
    assert doc["removed"] == ["fix/zzz-other"]
    assert doc["failed"] == []
    assert doc["status"] == "pruned"
    assert _git(["rev-parse", "--verify", "fix/zzz-other"], repo).returncode != 0
    # the operator's own working directory and branch survive
    assert wt_self.is_dir()
    assert _git(["rev-parse", "--verify", "fix/aaa-self"], repo).returncode == 0
    # ...and from the PRIMARY it is an ordinary prunable candidate again
    assert {
        b["name"]: b["category"] for b in worktree.branch_hygiene(repo)["branches"]
    }["fix/aaa-self"] == "prunable"


def test_unexpected_git_failure_on_one_branch_never_aborts_the_prune(repo, monkeypatch):
    """O3's negative, generalised: NO exception escapes the per-branch loop.
    One branch raising must become a NAMED `failed` entry while the remaining
    candidates are still processed."""
    _branch(repo, "fix/aaa-boom")
    _merge_into_main(repo, "fix/aaa-boom")
    _branch(repo, "fix/zzz-fine")
    _merge_into_main(repo, "fix/zzz-fine")
    real_git = worktree._git

    def exploding(args, cwd):
        if args[:2] == ["branch", "-d"] and "fix/aaa-boom" in args:
            raise worktree.WorktreeError("[S16] could not run git: cwd vanished")
        return real_git(args, cwd)

    monkeypatch.setattr(worktree, "_git", exploding)
    doc = worktree.prune_branches(repo, yes=True)

    assert doc["status"] == "partial"
    assert [f["branch"] for f in doc["failed"]] == ["fix/aaa-boom"]
    assert "remaining branches still processed" in doc["failed"][0]["reason"]
    assert doc["removed"] == ["fix/zzz-fine"]


# --- O4: --json exit code matches the human path ---------------------------


def test_cli_json_prune_exits_nonzero_on_partial_real_subprocess(repo, tmp_path):
    """REVIEW MEDIUM (json-exit): the `status == "partial" -> exit 1` decision
    lived INSIDE the human/else branch, so `--json` reported success on a
    partial prune. Asserted on the real process exit code."""
    bare = tmp_path / "origin.git"
    assert _git(["init", "--bare", "-b", "main", str(bare)], repo).returncode == 0
    assert _git(["remote", "add", "origin", str(bare)], repo).returncode == 0
    _branch(repo, "fix/tracked")
    assert _git(["push", "-u", "origin", "fix/tracked"], repo).returncode == 0
    (repo / "f.txt").write_text("second\n", encoding="utf-8")
    assert _git(["commit", "-am", "second"], repo).returncode == 0
    assert _git(["checkout", "main"], repo).returncode == 0
    assert _git(["merge", "--no-ff", "-m", "m", "fix/tracked"], repo).returncode == 0

    res = _run_ciu(
        ["worktree", "branches", "-y", "--json", "--define-root", str(repo)], repo
    )

    assert res.returncode == 1, res.stderr
    doc = json.loads(res.stdout)
    assert doc["operation"] == "branches-prune"
    assert doc["status"] == "partial"
    assert [f["branch"] for f in doc["failed"]] == ["fix/tracked"]


def test_cli_json_prune_and_survey_exit_zero_when_not_partial_real_subprocess(repo):
    """The same shared decision point must still return 0 for a clean prune
    and for a pure survey, in JSON mode."""
    _branch(repo, "fix/merged-json")
    _merge_into_main(repo, "fix/merged-json")

    survey = _run_ciu(
        ["worktree", "branches", "--json", "--define-root", str(repo)], repo
    )
    assert survey.returncode == 0, survey.stderr
    assert json.loads(survey.stdout)["status"] == "survey"

    pruned = _run_ciu(
        ["worktree", "branches", "-y", "--json", "--define-root", str(repo)], repo
    )
    assert pruned.returncode == 0, pruned.stderr
    doc = json.loads(pruned.stdout)
    assert doc["status"] == "pruned"
    assert doc["removed"] == ["fix/merged-json"]
