"""Tests for S16.3/CIU-24 -- `src/ciu/worktree.py`'s worktree-instance
concurrency budget (`primary_worktree_root`, `git_toplevel`,
`primary_ciu_root`, `resolve_max_concurrent_instances`,
`resolve_worktree_cap`, `worktree_budget_slot`, and their private helpers).

Covers the handoff's O1/O2/O3 traceability table:

- O1: the sole file-configured policy source is the PRIMARY *Git* worktree's
  own CIU root's global `[ciu.worktree]` table -- NEVER the Git root, an
  intermediate global layer, or a linked worktree's own branch -- plus the
  `CIU_MAX_CONCURRENT_WORKTREES` ambient override, and every invalid-input
  refusal.
- O2: the pre-lock candidate translation (Git root -> CIU root offset), the
  registered-and-deployed classifier (exact per-instance compose-project
  label, never a bare label-presence check), and every required fixture
  named in the handoff (nested-root, missing-stack-sibling, P02 composition).
- O3 (worktree.py half): the locked count-and-start critical section itself
  -- all candidate identity resolved BEFORE the flock, held across the
  caller's own "executor" code, released on every return/raise. The engine
  wiring half of O3 (main_execution/run_shipped call sites) is covered by
  `test_ciu_engine_worktree_budget.py`.

The Docker seam is `monkeypatch.setattr(worktree.procutil, "docker", fake)`
(the seam named in the handoff, precedent: `test_ciu_deploy_actions.py:1348`)
-- `tester-unified:local` has no Docker socket. No real Docker/network/clock
dependency anywhere in this file; the one genuine-concurrency test uses real
threads synchronized with `threading.Event` (never a sleep or a deadline) so
its verdict cannot depend on how fast the host is.
"""
from __future__ import annotations

import fcntl
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import worktree  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, check=False)


def _git_ok(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    res = _git(args, cwd)
    assert res.returncode == 0, f"git {args} failed in {cwd}: {res.stderr}"
    return res


def _global_defaults(*, project: str = "demo", env_tag: str = "dev",
                      max_concurrent: int | None = None,
                      extra: str = "") -> str:
    body = f'[deploy]\nproject_name = "{project}"\nenvironment_tag = "{env_tag}"\n'
    if max_concurrent is not None:
        body += f"\n[ciu.worktree]\nmax_concurrent_instances = {max_concurrent}\n"
    return body + extra


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """A throwaway git repo, branch 'main', with its CIU marker nested one
    level below the git top level at ``project/`` -- the monorepo shape this
    package exists to get right (the CIU marker sits below the git
    top-level, exactly like this real repo's own `ciu/` under `vbpub/`).

    Returns the GIT ROOT (``tmp_path/repo``); the CIU root is
    ``tmp_path/repo/project``.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_ok(["init", "-b", "main"], repo)
    _git_ok(["config", "user.email", "t@example.com"], repo)
    _git_ok(["config", "user.name", "Test"], repo)
    (repo / ".gitignore").write_text(
        "ciu.env\nciu.global.worktree.toml.j2\n**/.ciu/\nciu.global.toml\n"
        "ciu.toml\nciu.compose.yml\n"
    )
    project = repo / "project"
    project.mkdir()
    (project / "ciu.global.defaults.toml.j2").write_text(
        _global_defaults(max_concurrent=3)
    )
    stack = project / "stack"
    stack.mkdir()
    (stack / "ciu.defaults.toml.j2").write_text('[demo]\nname = "x"\n')
    _git_ok(["add", "-A"], repo)
    _git_ok(["commit", "-m", "init"], repo)
    return repo


def _ciu_root(git_root: Path) -> Path:
    return git_root / "project"


def _stack_rel() -> Path:
    return Path("stack")


def _write_ciu_env(worktree_root: Path, *, network: str, instance_id: str = "") -> None:
    """CIU-75: the generated identity table lives in the GIT WORKTREE root's
    `ciu.global.worktree.toml.j2` (matching `worktree.add`'s own existing,
    already-shipped placement -- S16.1's `ref.path` precedent) -- NOT at the
    (possibly nested) CIU root. Named for the file it replaced, so the
    existing 36 call sites keep reading the same way."""
    from ciu.workspace_env import GENERATED_FACTS_KEYS, upsert_generated_facts

    facts = {key: "" for key in GENERATED_FACTS_KEYS}
    facts["network"] = network
    facts["instance_id"] = instance_id
    upsert_generated_facts(worktree_root, facts)


def _add_linked_worktree(git_root: Path, branch: str, *, base: str = "main") -> Path:
    target = git_root.parent / branch
    _git_ok(["worktree", "add", "-b", branch, str(target), base], git_root)
    return target


def _project_filter(args: list[str]) -> str | None:
    if args and args[0] == "ps" and "--filter" in args:
        val = args[args.index("--filter") + 1]
        if val.startswith("label=com.docker.compose.project="):
            return val.split("=", 2)[-1]
    return None


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _deployed_docker(deployed: dict[str, str]):
    """A strict fake for `worktree.procutil.docker` answering the S16.3
    `docker ps --filter label=...project=<P> --format {{.Networks}}` query:
    *deployed* maps project -> the comma-separated network list a running
    container of that project reports; a project absent from *deployed*
    answers empty (not deployed). Any other call raises AssertionError."""
    calls: list[list[str]] = []

    def fake(args, **kw):
        calls.append(list(args))
        proj = _project_filter(args)
        if proj is None:
            raise AssertionError(f"unscripted docker call: {args}")
        networks = deployed.get(proj, "")
        return _proc(stdout=(networks + "\n") if networks else "")

    fake.calls = calls
    return fake


# ---------------------------------------------------------------------------
# primary_worktree_root / git_toplevel (S16.3)
# ---------------------------------------------------------------------------


def test_primary_worktree_root_is_the_git_root(tmp_git_repo):
    assert worktree.primary_worktree_root(_ciu_root(tmp_git_repo)) == tmp_git_repo


def test_primary_worktree_root_from_linked_worktree_still_finds_primary(tmp_git_repo):
    linked = _add_linked_worktree(tmp_git_repo, "linked")
    assert worktree.primary_worktree_root(_ciu_root(linked)) == tmp_git_repo


def test_primary_worktree_root_zero_primaries_raises(tmp_git_repo, monkeypatch):
    monkeypatch.setattr(worktree, "list_worktrees", lambda repo_root: [])
    with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
        worktree.primary_worktree_root(_ciu_root(tmp_git_repo))


def test_primary_worktree_root_multiple_primaries_raises(tmp_git_repo, monkeypatch):
    fake_primary = worktree.WorktreeInfo(path=Path("/nowhere"), branch="main", head="abc")
    monkeypatch.setattr(
        worktree, "list_worktrees",
        lambda repo_root: [fake_primary, fake_primary],
    )
    monkeypatch.setattr(worktree.WorktreeInfo, "is_primary", property(lambda self: True))
    with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*found 2"):
        worktree.primary_worktree_root(_ciu_root(tmp_git_repo))


def test_git_toplevel_returns_git_root(tmp_git_repo):
    assert worktree.git_toplevel(_ciu_root(tmp_git_repo)) == tmp_git_repo.resolve()


def test_git_toplevel_not_a_git_repo_raises(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
        worktree.git_toplevel(bare)


def test_git_toplevel_malformed_zero_exit_output_raises(tmp_git_repo, monkeypatch):
    """A zero exit but non-absolute/empty stdout (never observed from real
    git, but defended against explicitly) is still a loud [S16.3] failure,
    not a silently-accepted bogus path."""
    monkeypatch.setattr(
        worktree, "_git",
        lambda args, cwd: subprocess.CompletedProcess(args, 0, stdout="not/absolute\n", stderr=""),
    )
    with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*absolute"):
        worktree.git_toplevel(_ciu_root(tmp_git_repo))


def test_git_common_dir_failure_raises(tmp_git_repo, monkeypatch):
    monkeypatch.setattr(
        worktree, "_git",
        lambda args, cwd: subprocess.CompletedProcess(args, 128, stdout="", stderr="not a git repo"),
    )
    with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*git-common-dir"):
        worktree._git_common_dir(_ciu_root(tmp_git_repo))


def test_git_common_dir_resolves_a_relative_result_against_repo_root(tmp_git_repo, monkeypatch):
    """`git rev-parse --git-common-dir` reports a RELATIVE path from the
    PRIMARY checkout (confirmed with real git: plain `.git`) -- must be
    resolved against repo_root, never returned as-is (which would be
    meaningless once the lock path is later joined onto it)."""
    monkeypatch.setattr(
        worktree, "_git",
        lambda args, cwd: subprocess.CompletedProcess(args, 0, stdout=".git\n", stderr=""),
    )
    result = worktree._git_common_dir(_ciu_root(tmp_git_repo))
    assert result == (_ciu_root(tmp_git_repo) / ".git").resolve()
    assert result.is_absolute()


def test_git_common_dir_absolute_result_used_as_is(tmp_git_repo, monkeypatch):
    """`git rev-parse --git-common-dir` reports an ABSOLUTE path from a
    LINKED worktree (confirmed with real git) -- used verbatim, never
    re-joined onto repo_root (which would produce a bogus nested path)."""
    absolute_common = str((tmp_git_repo / ".git").resolve())
    monkeypatch.setattr(
        worktree, "_git",
        lambda args, cwd: subprocess.CompletedProcess(args, 0, stdout=absolute_common + "\n", stderr=""),
    )
    result = worktree._git_common_dir(_ciu_root(tmp_git_repo))
    assert result == Path(absolute_common)


def test_lock_location_is_shared_across_the_whole_worktree_family(tmp_git_repo, monkeypatch):
    """Real git, UNMOCKED: `_git_common_dir` from the PRIMARY and from a
    LINKED worktree of the SAME family must resolve to the IDENTICAL path
    (a linked worktree's `--git-common-dir` always reports the primary's
    own `.git`, never its own) -- this identity is what makes the S16.3
    lock visible to every sibling worktree, per SPEC S16.3's own claim.

    Then prove `worktree_budget_slot` itself actually USES that shared
    location, not just that `_git_common_dir` alone reports it correctly:
    taking a slot from EITHER worktree must create exactly ONE lock file,
    at the shared common-dir path -- never a second, per-worktree-local
    one. A mutant that swapped the lock path for
    `repo_root / _BUDGET_LOCK_NAME` (a per-worktree-local lock) would still
    pass every OTHER test in this file (`test_cap_none_...` only checks
    non-existence through the same mutated function, and the two-thread
    contention test passes the SAME repo_root to both threads) but fails
    THIS one: it would produce two distinct lock files instead of one
    shared one.
    """
    _write_ciu_env(tmp_git_repo, network="net-primary")
    linked = _add_linked_worktree(tmp_git_repo, "linked")
    (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
        _global_defaults(env_tag="linked", max_concurrent=3)
    )
    _git_ok(["add", "-A"], linked)
    _git_ok(["commit", "-m", "linked distinct project"], linked)
    _write_ciu_env(linked, network="net-linked")

    common_from_primary = worktree._git_common_dir(_ciu_root(tmp_git_repo))
    common_from_linked = worktree._git_common_dir(_ciu_root(linked))
    assert common_from_primary == common_from_linked, (
        "a linked worktree's own --git-common-dir must equal the primary's"
    )

    monkeypatch.setattr(worktree.procutil, "docker", _deployed_docker({}))

    with worktree.worktree_budget_slot(
        _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
    ):
        pass
    with worktree.worktree_budget_slot(
        _ciu_root(linked), 1, "net-linked", _stack_rel()
    ):
        pass

    lock_files = set(tmp_git_repo.parent.rglob(worktree._BUDGET_LOCK_NAME))
    assert lock_files == {common_from_primary / worktree._BUDGET_LOCK_NAME}, (
        f"expected exactly one shared lock file, found {lock_files}"
    )


# ---------------------------------------------------------------------------
# primary_ciu_root -- the git-root-to-CIU-root offset (S16.3)
# ---------------------------------------------------------------------------


def test_primary_ciu_root_from_primary(tmp_git_repo):
    assert worktree.primary_ciu_root(_ciu_root(tmp_git_repo)) == _ciu_root(tmp_git_repo)


def test_primary_ciu_root_from_linked_worktree_resolves_to_PRIMARY_ciu_root(tmp_git_repo):
    """The critical offset-translation assertion: invoked from a LINKED
    worktree's own nested CIU root, primary_ciu_root must still resolve to
    the PRIMARY's nested CIU root -- never the linked worktree's own, and
    never the raw git root of either."""
    linked = _add_linked_worktree(tmp_git_repo, "linked")
    resolved = worktree.primary_ciu_root(_ciu_root(linked))
    assert resolved == _ciu_root(tmp_git_repo)
    assert resolved != _ciu_root(linked)
    assert resolved != tmp_git_repo
    assert resolved != linked


def test_primary_ciu_root_standalone_repo_offset_is_dot(tmp_path):
    """A standalone CIU git repository (CIU marker AT the git root) has
    offset Path('.') -- primary_ciu_root equals the git root exactly."""
    repo = tmp_path / "standalone"
    repo.mkdir()
    _git_ok(["init", "-b", "main"], repo)
    _git_ok(["config", "user.email", "t@example.com"], repo)
    _git_ok(["config", "user.name", "Test"], repo)
    (repo / "ciu.global.defaults.toml.j2").write_text(_global_defaults())
    _git_ok(["add", "-A"], repo)
    _git_ok(["commit", "-m", "init"], repo)
    assert worktree.primary_ciu_root(repo) == repo.resolve()


def test_primary_ciu_root_absent_derived_root_raises(tmp_git_repo):
    """The primary's OWN copy of the offset path does not exist on disk --
    a loud [S16.3] failure, never a silent fall-back to the git root."""
    linked = _add_linked_worktree(tmp_git_repo, "linked")
    import shutil
    shutil.rmtree(_ciu_root(tmp_git_repo))
    with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*does not exist"):
        worktree.primary_ciu_root(_ciu_root(linked))


def test_ciu_root_offset_repo_root_not_under_its_own_toplevel_raises(tmp_git_repo, monkeypatch):
    """Defensive branch: if the CIU root is somehow not under its own git
    top-level at all (never observed with real git -- `--show-toplevel` run
    from inside repo_root always returns an ancestor -- but defended against
    explicitly), the offset derivation fails loudly rather than silently
    using the git root or an unrelated path."""
    monkeypatch.setattr(worktree, "git_toplevel", lambda repo_root: Path("/completely/unrelated"))
    with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*offset"):
        worktree._ciu_root_offset(_ciu_root(tmp_git_repo))


# ---------------------------------------------------------------------------
# resolve_max_concurrent_instances (S16.3)
# ---------------------------------------------------------------------------


class TestResolveMaxConcurrentInstances:
    def test_both_absent_is_no_cap(self):
        assert worktree.resolve_max_concurrent_instances(None, environ={}) is None

    def test_present_empty_table_is_valid_no_cap(self):
        """`[ciu.worktree]` present but with none of its (optional) keys set
        is a valid, empty table -- not an error, and not a cap."""
        assert worktree.resolve_max_concurrent_instances({}, environ={}) is None

    def test_valid_file_value(self):
        assert worktree.resolve_max_concurrent_instances(
            {"max_concurrent_instances": 3}, environ={}
        ) == 3

    def test_ambient_overrides_valid_file_value(self):
        assert worktree.resolve_max_concurrent_instances(
            {"max_concurrent_instances": 3},
            environ={"CIU_MAX_CONCURRENT_WORKTREES": "7"},
        ) == 7

    def test_ambient_present_file_absent(self):
        assert worktree.resolve_max_concurrent_instances(
            None, environ={"CIU_MAX_CONCURRENT_WORKTREES": "2"}
        ) == 2

    def test_file_not_a_mapping_raises(self):
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*table"):
            worktree.resolve_max_concurrent_instances([1, 2], environ={})

    def test_unknown_key_raises(self):
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*unknown"):
            worktree.resolve_max_concurrent_instances(
                {"max_concurrent_instances": 1, "typo_key": True}, environ={}
            )

    @pytest.mark.parametrize("value", [0, -1, "3", 3.0, None])
    def test_invalid_file_value_types_raise(self, value):
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree.resolve_max_concurrent_instances(
                {"max_concurrent_instances": value}, environ={}
            )

    def test_boolean_file_value_raises(self):
        """bool is an int subclass in Python -- must be explicitly rejected,
        not silently accepted as 0/1."""
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree.resolve_max_concurrent_instances(
                {"max_concurrent_instances": True}, environ={}
            )

    def test_zero_is_not_unlimited(self):
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree.resolve_max_concurrent_instances(
                {"max_concurrent_instances": 0}, environ={}
            )

    @pytest.mark.parametrize("raw", ["", " ", "0", "-1", "+1", "1.5", "01", "abc", " 3", "3 "])
    def test_invalid_ambient_values_raise(self, raw):
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree.resolve_max_concurrent_instances(
                None, environ={"CIU_MAX_CONCURRENT_WORKTREES": raw}
            )

    def test_invalid_ambient_does_not_mask_invalid_file(self):
        """An ambient override never masks an invalid file table -- the file
        is validated even when the ambient value would otherwise win."""
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree.resolve_max_concurrent_instances(
                {"max_concurrent_instances": -5},
                environ={"CIU_MAX_CONCURRENT_WORKTREES": "3"},
            )

    def test_environ_none_defaults_to_os_environ(self, monkeypatch):
        monkeypatch.setenv("CIU_MAX_CONCURRENT_WORKTREES", "4")
        assert worktree.resolve_max_concurrent_instances(None) == 4
        monkeypatch.delenv("CIU_MAX_CONCURRENT_WORKTREES", raising=False)
        assert worktree.resolve_max_concurrent_instances(None) is None


# ---------------------------------------------------------------------------
# resolve_worktree_cap -- the primary-CIU-root-only file lookup (O1)
# ---------------------------------------------------------------------------


class TestResolveWorktreeCap:
    def test_reads_policy_from_primary_root(self, tmp_git_repo):
        assert worktree.resolve_worktree_cap(_ciu_root(tmp_git_repo)) == 3

    def test_reads_from_primary_when_invoked_from_linked_worktree(self, tmp_git_repo):
        """The nested-root fixture's core assertion: invoking from a LINKED
        worktree's own CIU root still reads the PRIMARY's policy."""
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        assert worktree.resolve_worktree_cap(_ciu_root(linked)) == 3

    def test_linked_branchs_own_conflicting_policy_is_ignored(self, tmp_git_repo):
        """A linked worktree's OWN branch carries a DIFFERENT cap -- proves
        the primary's value wins, never the invoking process's own branch."""
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
            _global_defaults(max_concurrent=99)
        )
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "linked overrides cap to 99"], linked)
        assert worktree.resolve_worktree_cap(_ciu_root(linked)) == 3

    def test_no_rendered_artifact_is_written(self, tmp_git_repo):
        worktree.resolve_worktree_cap(_ciu_root(tmp_git_repo))
        assert not (_ciu_root(tmp_git_repo) / "ciu.global.toml").exists()

    def test_no_global_template_at_all_is_no_cap(self, tmp_path):
        """A registered primary git worktree with NO ciu.global.defaults --
        render_global_chain's own no-configuration ValueError is the narrow,
        expected case: no file policy, no cap (never an [S16.3] error)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_ok(["init", "-b", "main"], repo)
        _git_ok(["config", "user.email", "t@example.com"], repo)
        _git_ok(["config", "user.name", "Test"], repo)
        (repo / "README.md").write_text("hi\n")
        _git_ok(["add", "-A"], repo)
        _git_ok(["commit", "-m", "init"], repo)
        assert worktree.resolve_worktree_cap(repo) is None

    def test_unrelated_render_error_still_raises(self, tmp_git_repo):
        """A genuinely broken template (bad TOML) must NOT be swallowed by
        the narrow no-global-configuration catch."""
        (_ciu_root(tmp_git_repo) / "ciu.global.defaults.toml.j2").write_text(
            "this is not [valid toml\n"
        )
        with pytest.raises(ValueError):
            worktree.resolve_worktree_cap(_ciu_root(tmp_git_repo))

    def test_not_inside_a_git_work_tree_is_no_cap(self, tmp_path):
        """A bare (non-git) CIU root has no worktree family and therefore no
        file-level policy -- mirrors the codebase's own S1.7 precedent for
        exactly this condition, and is what keeps the hundreds of existing
        non-git tmp_path engine fixtures elsewhere in this suite working."""
        bare = tmp_path / "bare"
        bare.mkdir()
        (bare / "ciu.global.defaults.toml.j2").write_text(_global_defaults(max_concurrent=9))
        assert worktree.resolve_worktree_cap(bare) is None

    def test_not_inside_git_work_tree_still_honours_ambient_override(self, tmp_path, monkeypatch):
        bare = tmp_path / "bare"
        bare.mkdir()
        monkeypatch.setenv("CIU_MAX_CONCURRENT_WORKTREES", "6")
        assert worktree.resolve_worktree_cap(bare) == 6

    def test_ambient_override_wins_over_primary_file_value(self, tmp_git_repo, monkeypatch):
        monkeypatch.setenv("CIU_MAX_CONCURRENT_WORKTREES", "11")
        assert worktree.resolve_worktree_cap(_ciu_root(tmp_git_repo)) == 11

    def test_invalid_file_value_at_primary_raises(self, tmp_git_repo):
        (_ciu_root(tmp_git_repo) / "ciu.global.defaults.toml.j2").write_text(
            _global_defaults(max_concurrent=0)
        )
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree.resolve_worktree_cap(_ciu_root(tmp_git_repo))


# ---------------------------------------------------------------------------
# _resolve_budget_candidates -- the instance classifier (O2)
# ---------------------------------------------------------------------------


class TestResolveBudgetCandidates:
    def test_primary_only(self, tmp_git_repo):
        _write_ciu_env(tmp_git_repo, network="net-primary")
        candidates = worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())
        assert len(candidates) == 1
        assert candidates[0].network == "net-primary"
        assert candidates[0].project == "demo-dev-stack"

    def test_raw_worktree_without_ciu_env_excluded(self, tmp_git_repo):
        """A plain git worktree that was never `ciu env generate`d is not a
        registered CIU instance -- excluded, not an error."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        _add_linked_worktree(tmp_git_repo, "raw")
        candidates = worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())
        assert [c.network for c in candidates] == ["net-primary"]

    def test_eligible_linked_worktree_included_with_own_env_and_project(self, tmp_git_repo):
        """The nested-root fixture: the child worktree renders against its
        OWN explicit ciu.env, deriving its OWN distinct project -- not the
        caller's ambient environment."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
            _global_defaults(project="demo", env_tag="linked", max_concurrent=3)
        )
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "linked stack"], linked)
        _write_ciu_env(linked, network="net-linked")

        candidates = worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())
        by_network = {c.network: c for c in candidates}
        assert set(by_network) == {"net-primary", "net-linked"}
        assert by_network["net-linked"].project == "demo-linked-stack"
        assert by_network["net-primary"].project == "demo-dev-stack"

    def test_missing_stack_sibling_skipped_with_info_note_and_no_docker_query(
        self, tmp_git_repo, capsys,
    ):
        """Required fixture: the stack directory is removed ONLY on the
        child's checked-out branch -- skipped as not-deployed, an [S16.3]
        INFO note is logged naming it, and no Docker query is ever made for
        it (proven separately in the classifier test below; here we prove
        the candidate list + the logged note)."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        _write_ciu_env(linked, network="net-linked")
        import shutil
        shutil.rmtree(_ciu_root(linked) / "stack")
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "drop stack on this branch"], linked)

        candidates = worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())
        assert [c.network for c in candidates] == ["net-primary"]
        out = capsys.readouterr().out
        assert "[INFO] [S16.3]" in out
        assert str(linked) in out

    def test_env_missing_docker_network_internal_raises(self, tmp_git_repo):
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        _write_ciu_env(linked, network="")
        with pytest.raises(
            worktree.WorktreeError, match=r"\[S16\.3\].*declares no instance network"
        ):
            worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())

    def test_env_unparseable_raises(self, tmp_git_repo):
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        (linked / "ciu.global.worktree.toml.j2").write_text(
            "[ciu.instance.generated]\nnetwork = not-a-toml-value\n"
        )
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())

    def test_env_non_utf8_raises(self, tmp_git_repo):
        """CIU-62 — the gap `(OSError, WorkspaceEnvError)` still left open at
        this site. A non-UTF-8 byte raises `UnicodeDecodeError`, a SIBLING of
        `WorkspaceEnvError` under `ValueError`, not a subclass of it; the
        S16.3 capacity count must refuse an instance it cannot read rather
        than crash mid-survey (and never silently undercount). CIU-75 moved
        the source to the overlay and normalized the three exception types at
        the reader; the refusal contract is unchanged."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        (linked / "ciu.global.worktree.toml.j2").write_bytes(
            b'[ciu.instance.generated]\nnetwork = "\xff\xfe"\n'
        )
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\] could not read/parse"):
            worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())

    def test_duplicate_network_across_candidates_raises(self, tmp_git_repo):
        _write_ciu_env(tmp_git_repo, network="same-net")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        _write_ciu_env(linked, network="same-net")
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*same-net"):
            worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())

    def test_present_candidate_with_unrenderable_config_raises(self, tmp_git_repo):
        """A present candidate stack whose global config cannot render is a
        loud [S16.3] failure -- never treated as an inactive instance."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        _write_ciu_env(linked, network="net-linked")
        (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text("not [ valid toml\n")
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "break linked config"], linked)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*could not render"):
            worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())

    def test_present_candidate_missing_deploy_identity_raises(self, tmp_git_repo):
        """A present candidate whose global config has no
        deploy.project_name/environment_tag cannot derive a compose project
        -- [S16.3], never evidence of an inactive instance."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        _write_ciu_env(linked, network="net-linked")
        (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text("[ciu]\nx = 1\n")
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "drop deploy identity on linked"], linked)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*could not derive"):
            worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())

    def test_candidate_env_isolation_ambient_identity_never_leaks(
        self, tmp_git_repo, monkeypatch
    ):
        """The CALLING process's own IDENTITY must never reach a candidate's
        render: each candidate renders against ITS OWN generated facts.

        CIU-75 narrowed what this test can honestly claim, deliberately. The
        pre-cutover environment was the candidate's whole parsed `ciu.env` and
        nothing else, so NO ambient variable could reach the render. The
        overlay carries identity facts only, so the environment is now ambient
        MINUS every CIU identity key PLUS the candidate's facts — machine
        facts (`$USER_UID`, `$PATH`) are shared by every checkout on this host
        anyway, and identity, the thing that actually differs, is still taken
        exclusively from the candidate. An ambient `INSTANCE_ID` set here to a
        third value proves exactly that."""
        monkeypatch.setenv("INSTANCE_ID", "ambient-leak")
        _write_ciu_env(tmp_git_repo, network="net-primary", instance_id="primary-id")
        (_ciu_root(tmp_git_repo) / "ciu.global.defaults.toml.j2").write_text(
            '[deploy]\nproject_name = "demo"\nenvironment_tag = "$INSTANCE_ID"\n'
            "\n[ciu.worktree]\nmax_concurrent_instances = 3\n"
        )
        _git_ok(["add", "-A"], tmp_git_repo)
        _git_ok(["commit", "-m", "tag from identity"], tmp_git_repo)
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        _write_ciu_env(linked, network="net-linked", instance_id="linked-id")

        candidates = worktree._resolve_budget_candidates(_ciu_root(tmp_git_repo), _stack_rel())
        by_network = {c.network: c for c in candidates}
        assert by_network["net-primary"].project == "demo-primary-id-stack"
        assert by_network["net-linked"].project == "demo-linked-id-stack"


# ---------------------------------------------------------------------------
# _candidate_deployed -- exact-project-label classifier (O2, P02 composition)
# ---------------------------------------------------------------------------


class TestCandidateDeployed:
    def _candidate(self, *, network="net-a", project="proj-a"):
        return worktree._BudgetCandidate(
            worktree_path=Path("/wt"), stack=Path("/wt/stack"),
            network=network, project=project,
        )

    def test_own_project_own_network_is_deployed(self, monkeypatch):
        fake = _deployed_docker({"proj-a": "net-a"})
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        assert worktree._candidate_deployed(self._candidate()) is True

    def test_no_matching_container_is_not_deployed(self, monkeypatch):
        fake = _deployed_docker({})
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        assert worktree._candidate_deployed(self._candidate()) is False

    def test_own_project_container_on_a_different_network_is_not_deployed(self, monkeypatch):
        """A container carrying the candidate's OWN exact project label, but
        reporting only some OTHER network (never its own) -- e.g. a stale
        reconnect elsewhere -- must not be counted deployed either."""
        fake = _deployed_docker({"proj-a": "some-other-network"})
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        assert worktree._candidate_deployed(self._candidate()) is False

    def test_p02_composition_fixture_child_network_on_other_project_not_deployed(self, monkeypatch):
        """Required P02 composition fixture: container A carries
        `com.docker.compose.project=A-project` and lists BOTH `A-network`
        and `B-network` (S16.1's shared-infra join gave it a second network
        membership) -- querying B's EXACT project label must return nothing,
        so B is NOT counted deployed even though B's network name literally
        appears in A's container output."""
        fake = _deployed_docker({"A-project": "A-network,B-network"})
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        candidate_a = self._candidate(network="A-network", project="A-project")
        candidate_b = self._candidate(network="B-network", project="B-project")
        assert worktree._candidate_deployed(candidate_a) is True
        assert worktree._candidate_deployed(candidate_b) is False

    def test_docker_nonzero_raises(self, monkeypatch):
        def fake(args, **kw):
            return _proc(returncode=1, stderr="daemon unavailable")
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree._candidate_deployed(self._candidate())

    def test_docker_oserror_raises(self, monkeypatch):
        def fake(args, **kw):
            raise FileNotFoundError("no docker binary")
        monkeypatch.setattr(worktree.procutil, "docker", fake)
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            worktree._candidate_deployed(self._candidate())


# ---------------------------------------------------------------------------
# worktree_budget_slot -- the locked count-and-start critical section (O2/O3)
# ---------------------------------------------------------------------------


class TestWorktreeBudgetSlotDecisionTable:
    def test_cap_none_never_touches_docker_or_lock(self, tmp_git_repo, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("must not query Docker when cap is None")
        monkeypatch.setattr(worktree.procutil, "docker", boom)
        ran = []
        with worktree.worktree_budget_slot(
            _ciu_root(tmp_git_repo), None, "net-primary", _stack_rel()
        ):
            ran.append(True)
        assert ran == [True]
        assert not (worktree._git_common_dir(_ciu_root(tmp_git_repo)) / worktree._BUDGET_LOCK_NAME).exists()

    def test_current_absent_total_under_cap_starts(self, tmp_git_repo, monkeypatch):
        _write_ciu_env(tmp_git_repo, network="net-primary")
        monkeypatch.setattr(worktree.procutil, "docker", _deployed_docker({}))
        ran = []
        with worktree.worktree_budget_slot(
            _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
        ):
            ran.append(True)
        assert ran == [True]

    def test_current_absent_total_at_cap_refuses_before_executor(self, tmp_git_repo, monkeypatch):
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
            _global_defaults(env_tag="linked", max_concurrent=3)
        )
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "linked distinct project"], linked)
        _write_ciu_env(linked, network="net-linked")
        monkeypatch.setattr(
            worktree.procutil, "docker", _deployed_docker({"demo-linked-stack": "net-linked"})
        )
        executor_called = []
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*cap 1"):
            with worktree.worktree_budget_slot(
                _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
            ):
                executor_called.append(True)  # pragma: no cover -- must not run
        assert executor_called == []

    def test_current_already_deployed_allows_rerun_even_over_cap(self, tmp_git_repo, monkeypatch):
        """The already-deployed-may-rerun rule: even with total >= cap, the
        CURRENT instance is allowed to re-run (e.g. idempotent re-`up`)."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
            _global_defaults(env_tag="linked", max_concurrent=3)
        )
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "linked distinct project"], linked)
        _write_ciu_env(linked, network="net-linked")
        monkeypatch.setattr(
            worktree.procutil, "docker",
            _deployed_docker({"demo-dev-stack": "net-primary", "demo-linked-stack": "net-linked"}),
        )
        ran = []
        with worktree.worktree_budget_slot(
            _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
        ):
            ran.append(True)
        assert ran == [True]

    def test_lowered_cap_after_current_deployed_still_allows_rerun_even_far_over(
        self, tmp_git_repo, monkeypatch,
    ):
        """The policy was lowered to 1 AFTER two instances (current +
        linked) were already deployed under a looser cap -- current's own
        rerun must still be allowed, even though the total (2) is now well
        over the (lowered) cap."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        linked = _add_linked_worktree(tmp_git_repo, "linked")
        (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
            _global_defaults(env_tag="linked", max_concurrent=3)
        )
        _git_ok(["add", "-A"], linked)
        _git_ok(["commit", "-m", "linked distinct project"], linked)
        _write_ciu_env(linked, network="net-linked")
        monkeypatch.setattr(
            worktree.procutil, "docker",
            _deployed_docker({"demo-dev-stack": "net-primary", "demo-linked-stack": "net-linked"}),
        )
        ran = []
        with worktree.worktree_budget_slot(
            _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
        ):
            ran.append(True)
        assert ran == [True]

    def test_current_network_not_registered_raises(self, tmp_git_repo, monkeypatch):
        _write_ciu_env(tmp_git_repo, network="net-primary")
        monkeypatch.setattr(worktree.procutil, "docker", _deployed_docker({}))
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\].*not among"):
            with worktree.worktree_budget_slot(
                _ciu_root(tmp_git_repo), 1, "net-UNREGISTERED", _stack_rel()
            ):
                pass  # pragma: no cover -- must not run

    def test_stack_rel_absolute_raises(self, tmp_git_repo):
        with pytest.raises(worktree.WorktreeError, match=r"\[S16\.3\]"):
            with worktree.worktree_budget_slot(
                _ciu_root(tmp_git_repo), 1, "net-primary", Path("/absolute")
            ):
                pass  # pragma: no cover -- must not run

    def test_compose_failure_releases_lock_no_lasting_reservation(self, tmp_git_repo, monkeypatch):
        """A Compose failure inside the `with` block must still release the
        lock -- proven by a SECOND acquisition succeeding immediately
        afterwards with no stale reservation."""
        _write_ciu_env(tmp_git_repo, network="net-primary")
        monkeypatch.setattr(worktree.procutil, "docker", _deployed_docker({}))

        class BoomError(Exception):
            pass

        with pytest.raises(BoomError):
            with worktree.worktree_budget_slot(
                _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
            ):
                raise BoomError("compose blew up")

        ran = []
        with worktree.worktree_budget_slot(
            _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
        ):
            ran.append(True)
        assert ran == [True]


# ---------------------------------------------------------------------------
# Lock discipline -- instrumented fcntl.flock (O2/O3)
# ---------------------------------------------------------------------------


class _FlockRecorder:
    """Wraps the REAL `fcntl.flock` and records every LOCK_EX/LOCK_UN
    transition, delegating to the real syscall so genuine mutual exclusion
    is still exercised."""

    def __init__(self, real_flock):
        self._real = real_flock
        self.events: list[str] = []

    def __call__(self, fd, operation):
        if operation == fcntl.LOCK_EX:
            result = self._real(fd, operation)
            self.events.append("LOCK_EX")
            return result
        if operation == fcntl.LOCK_UN:
            result = self._real(fd, operation)
            self.events.append("LOCK_UN")
            return result
        return self._real(fd, operation)  # pragma: no cover -- only EX/UN used


def test_lock_held_continuously_from_count_decision_through_executor(tmp_git_repo, monkeypatch):
    """Proves ONE continuous locked section: no LOCK_UN occurs between the
    count decision and the executor's own return. A deliberately broken
    acquire -> count -> release -> re-acquire -> run arrangement would
    insert a LOCK_UN before the executor runs and fail this assertion even
    though it is locked at both sampled endpoints (start and end)."""
    _write_ciu_env(tmp_git_repo, network="net-primary")
    monkeypatch.setattr(worktree.procutil, "docker", _deployed_docker({}))
    recorder = _FlockRecorder(fcntl.flock)
    monkeypatch.setattr(worktree.fcntl, "flock", recorder)

    def executor():
        assert recorder.events == ["LOCK_EX"], (
            f"executor ran with unexpected lock state: {recorder.events}"
        )

    assert recorder.events == []
    with worktree.worktree_budget_slot(
        _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
    ):
        executor()
    assert recorder.events == ["LOCK_EX", "LOCK_UN"]


def test_candidate_rendering_finishes_before_the_first_lock_ex(tmp_git_repo, monkeypatch):
    """Proves candidate resolution (every candidate's `render_global_chain`
    call, inside `_resolve_budget_candidates`) finishes ENTIRELY before the
    family flock is ever acquired -- required by the handoff's own
    proof-material table ("Assert all candidate env/config renders finish
    before the first `LOCK_EX`"). `test_lock_held_continuously_...` above
    only records `LOCK_EX`/`LOCK_UN` transitions, so it cannot distinguish
    this from a mutant that moved `_resolve_budget_candidates()` to run
    INSIDE the flock: the render calls would still happen somewhere between
    the same LOCK_EX/LOCK_UN pair either way, and that test would still
    pass. Recording renders and lock transitions into ONE shared ordered
    list is what makes the two distinguishable: a render event appearing
    at or after the `LOCK_EX` index fails THIS test specifically.
    """
    _write_ciu_env(tmp_git_repo, network="net-primary")
    linked = _add_linked_worktree(tmp_git_repo, "linked")
    (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
        _global_defaults(env_tag="linked", max_concurrent=3)
    )
    _git_ok(["add", "-A"], linked)
    _git_ok(["commit", "-m", "linked distinct project"], linked)
    _write_ciu_env(linked, network="net-linked")
    monkeypatch.setattr(worktree.procutil, "docker", _deployed_docker({}))

    events: list[str] = []
    real_flock = fcntl.flock

    def recording_flock(fd, operation):
        result = real_flock(fd, operation)
        if operation == fcntl.LOCK_EX:
            events.append("LOCK_EX")
        elif operation == fcntl.LOCK_UN:
            events.append("LOCK_UN")
        return result

    monkeypatch.setattr(worktree.fcntl, "flock", recording_flock)

    real_render = worktree.config_model.render_global_chain

    def recording_render(*args, **kwargs):
        events.append("render")
        return real_render(*args, **kwargs)

    monkeypatch.setattr(worktree.config_model, "render_global_chain", recording_render)

    with worktree.worktree_budget_slot(
        _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
    ):
        pass

    assert "LOCK_EX" in events, f"the lock was never acquired: {events}"
    lock_index = events.index("LOCK_EX")
    render_indices = [i for i, e in enumerate(events) if e == "render"]
    # Both candidates (primary + linked) must actually have been rendered --
    # an empty list here would make the ordering assertion below vacuous.
    assert len(render_indices) == 2, f"expected 2 candidate renders, got: {events}"
    assert all(i < lock_index for i in render_indices), (
        f"a candidate render occurred at/after LOCK_EX: {events}"
    )


def test_lock_released_on_refusal_before_ever_yielding(tmp_git_repo, monkeypatch):
    """A refusal (count >= cap) never acquires-then-forgets-to-release: the
    lock must still show a clean EX/UN pair even though `yield` is never
    reached."""
    _write_ciu_env(tmp_git_repo, network="net-primary")
    linked = _add_linked_worktree(tmp_git_repo, "linked")
    (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
        _global_defaults(env_tag="linked", max_concurrent=3)
    )
    subprocess.run(["git", "add", "-A"], cwd=str(linked), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=str(linked), check=True, capture_output=True)
    _write_ciu_env(linked, network="net-linked")
    monkeypatch.setattr(
        worktree.procutil, "docker", _deployed_docker({"demo-linked-stack": "net-linked"})
    )
    recorder = _FlockRecorder(fcntl.flock)
    monkeypatch.setattr(worktree.fcntl, "flock", recorder)

    with pytest.raises(worktree.WorktreeError):
        with worktree.worktree_budget_slot(
            _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
        ):
            pass  # pragma: no cover -- must not run
    assert recorder.events == ["LOCK_EX", "LOCK_UN"]


# ---------------------------------------------------------------------------
# Genuine two-thread contention -- "two simultaneous cold starts" (O3)
# ---------------------------------------------------------------------------


def test_two_concurrent_cold_starts_second_waiter_recounts_and_refuses(tmp_git_repo, monkeypatch):
    """Real threads, real `fcntl.flock` -- no sleeps, no deadlines. Thread A
    takes the slot for the PRIMARY instance and holds it (simulating a slow
    Compose) until explicitly released via a `threading.Event`. Thread B
    (the LINKED instance) is started only after a real synchronization point
    proves A already holds the lock, and B's own attempt-to-acquire is
    observed (another real sync point) before A releases -- so B's
    `fcntl.flock` call is PROVABLY blocked by the OS, not just "slower".
    Once A releases, B re-counts under the lock and sees A's now-deployed
    state, refusing because the (cap=1) budget is now exhausted.

    Every `Event.wait()`/`Thread.join()` below carries a generous (60s)
    timeout used ONLY as a hang failsafe (AUTHORING.md 3b-A) -- each is
    immediately asserted to have actually fired, so a broken implementation
    fails with a clear assertion instead of hanging the suite forever; the
    verdict itself never depends on how fast either thread runs.
    """
    HANG_FAILSAFE_S = 60
    _write_ciu_env(tmp_git_repo, network="net-primary")
    linked = _add_linked_worktree(tmp_git_repo, "linked")
    (_ciu_root(linked) / "ciu.global.defaults.toml.j2").write_text(
        _global_defaults(env_tag="linked", max_concurrent=3)
    )
    subprocess.run(["git", "add", "-A"], cwd=str(linked), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "x"], cwd=str(linked), check=True, capture_output=True)
    _write_ciu_env(linked, network="net-linked")

    # Docker state: PRIMARY becomes "deployed" only once thread A signals it
    # brought its own compose up -- BEFORE that, nothing is deployed.
    state_lock = threading.Lock()
    deployed = {}

    def fake_docker(args, **kw):
        with state_lock:
            snapshot = dict(deployed)
        proj = _project_filter(args)
        assert proj is not None
        networks = snapshot.get(proj, "")
        return _proc(stdout=(networks + "\n") if networks else "")

    monkeypatch.setattr(worktree.procutil, "docker", fake_docker)

    real_flock = fcntl.flock
    b_attempting = threading.Event()

    def instrumented_flock(fd, operation):
        if operation == fcntl.LOCK_EX and threading.current_thread().name == "B":
            b_attempting.set()
        return real_flock(fd, operation)

    monkeypatch.setattr(worktree.fcntl, "flock", instrumented_flock)

    a_in_cs = threading.Event()
    release_a = threading.Event()
    a_result: dict = {}
    b_result: dict = {}

    def run_a():
        try:
            with worktree.worktree_budget_slot(
                _ciu_root(tmp_git_repo), 1, "net-primary", _stack_rel()
            ):
                with state_lock:
                    deployed["demo-dev-stack"] = "net-primary"
                a_in_cs.set()
                assert release_a.wait(timeout=HANG_FAILSAFE_S), "main never released A"
            a_result["outcome"] = "started"
        except worktree.WorktreeError as exc:
            a_result["outcome"] = "refused"
            a_result["error"] = str(exc)

    def run_b():
        # real sync point: A is confirmed inside its section
        assert a_in_cs.wait(timeout=HANG_FAILSAFE_S), "A never entered its critical section"
        try:
            with worktree.worktree_budget_slot(
                _ciu_root(tmp_git_repo), 1, "net-linked", _stack_rel()
            ):
                b_result["outcome"] = "started"  # pragma: no cover -- expect refusal
        except worktree.WorktreeError as exc:
            b_result["outcome"] = "refused"
            b_result["error"] = str(exc)

    thread_a = threading.Thread(target=run_a, name="A")
    thread_b = threading.Thread(target=run_b, name="B")
    thread_a.start()
    assert a_in_cs.wait(timeout=HANG_FAILSAFE_S), "A never entered its critical section"
    thread_b.start()
    # real sync point: B is provably blocked on A's flock
    assert b_attempting.wait(timeout=HANG_FAILSAFE_S), "B never attempted to acquire the lock"

    # B cannot possibly have produced a result yet -- it is blocked in the
    # kernel on fcntl.flock(LOCK_EX), which A still holds.
    assert b_result == {}

    release_a.set()
    thread_a.join(timeout=HANG_FAILSAFE_S)
    thread_b.join(timeout=HANG_FAILSAFE_S)
    assert not thread_a.is_alive(), "thread A did not finish within the hang failsafe"
    assert not thread_b.is_alive(), "thread B did not finish within the hang failsafe"

    assert a_result["outcome"] == "started"
    assert b_result["outcome"] == "refused"
    assert "cap 1" in b_result["error"]
