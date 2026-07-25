"""Unit + integration tests for gate_canary (PACKAGE GA1) -- the canary
builder/verifier for `nyxloom gate verify`. CLI tests (test_cli.py)
monkeypatch gate_runner.run_gate_at_commit to force a verdict cheaply; this
module's REAL body -- subtree scoping, candidate ranking, import-break
injection, the scratch-worktree build/cleanup cycle, and the multi-attempt
verdict rollup -- is exercised here directly against real git (mirroring
test_gate_runner.py's own split), PLUS one full end-to-end integration test
with a REAL gate (real pytest subprocess, no mocking of
gate_runner.run_gate_at_commit at all) that is the actual regression guard
for the subtree-scoping bug the first cut shipped with.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from nyxloom import gate_canary
from nyxloom.config import GateDef


def _run(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _init_repo(root: Path, files: dict[str, str]) -> str:
    """Create a git repo at `root` with `files` {relpath: content}, commit
    everything, and return the HEAD sha."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    assert _run(root, "init", "-q", "-b", "main").returncode == 0
    assert _run(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A").returncode == 0
    assert _run(root, "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "init").returncode == 0
    return _run(root, "rev-parse", "HEAD").stdout.strip()


class _FakeCfg:
    """Minimal stand-in for ProjectConfig -- gate_canary only reads `.root`."""
    def __init__(self, root: Path):
        self.root = root


def _gate(argv, timeout=30):
    return GateDef(gate_id="g", argv=argv, phase="implementation",
                   timeout_seconds=timeout, environment="local")


# --------------------------------------------------------------------------- #
# inject_import_break / _insertion_index_after_future -- minimal one-line
# insertion, no whole-file reformat (MAJOR #3).
# --------------------------------------------------------------------------- #

def test_inject_import_break_prepends_when_no_future_or_docstring(tmp_path):
    target = tmp_path / "thing.py"
    original = "def add(a, b):\n    return a + b\n"
    target.write_text(original)

    description = gate_canary.inject_import_break(target)

    mutated = target.read_text()
    assert mutated == 'raise AssertionError("nyxloom-gate-canary")\n' + original
    assert "line 1" in description


def test_inject_import_break_lands_after_future_import(tmp_path):
    target = tmp_path / "thing.py"
    original = "from __future__ import annotations\n\ndef add(a, b):\n    return a + b\n"
    target.write_text(original)

    gate_canary.inject_import_break(target)

    lines = target.read_text().splitlines()
    assert lines[0] == "from __future__ import annotations"
    assert lines[1] == 'raise AssertionError("nyxloom-gate-canary")'
    # everything else preserved verbatim (no reformat)
    assert target.read_text() == (
        "from __future__ import annotations\n"
        'raise AssertionError("nyxloom-gate-canary")\n'
        "\ndef add(a, b):\n    return a + b\n"
    )


def test_inject_import_break_lands_after_docstring_and_future_import(tmp_path):
    target = tmp_path / "thing.py"
    original = (
        '"""Module docstring."""\n'
        "from __future__ import annotations\n"
        "\n"
        "X = 1\n"
    )
    target.write_text(original)

    gate_canary.inject_import_break(target)

    lines = target.read_text().splitlines()
    assert lines[0] == '"""Module docstring."""'
    assert lines[1] == "from __future__ import annotations"
    assert lines[2] == 'raise AssertionError("nyxloom-gate-canary")'


def test_inject_import_break_is_minimal_one_line_diff(tmp_path):
    """MAJOR #3 regression: the injection must be a single-line insertion,
    not a whole-file reformat (ast.unparse's failure mode)."""
    root = tmp_path / "repo"
    original = "x = (1 +\n     2)\n\ny = [1,2,3]\n"
    head = _init_repo(root, {"thing.py": original})

    gate_canary.inject_import_break(root / "thing.py")

    diff = _run(root, "diff", "--unified=0", "--", "thing.py").stdout
    # exactly one added line, zero removed lines, in the unified diff body
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert added == ['+raise AssertionError("nyxloom-gate-canary")']
    assert removed == []
    # the rest of the file's formatting (e.g. the multi-line parens) survives untouched
    assert "x = (1 +\n     2)" in (root / "thing.py").read_text()


def test_inject_import_break_prepends_on_syntax_error(tmp_path):
    """An already-broken (unparseable) source file falls back to a plain
    prepend rather than crashing -- ast.parse failing is not this
    function's problem to solve, only to not choke on."""
    target = tmp_path / "broken.py"
    target.write_text("def f(:\n")

    description = gate_canary.inject_import_break(target)

    mutated = target.read_text()
    assert mutated.startswith('raise AssertionError("nyxloom-gate-canary")\n')
    assert "line 1" in description


# --------------------------------------------------------------------------- #
# _candidate_source_files -- deterministic ranking + exclusions
# --------------------------------------------------------------------------- #

def test_candidate_ranking_prefers_src_then_larger_then_alphabetical(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "small.py").write_text("x = 1\n")
    (tmp_path / "src" / "pkg" / "big.py").write_text("x = 1\n" * 50)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tool.py").write_text("x = 1\n" * 500)  # huge, but NOT under src/

    candidates = gate_canary._candidate_source_files(tmp_path, limit=10)
    rels = [str(p.relative_to(tmp_path)) for p in candidates]

    # both src/ files rank ahead of the much-larger non-src file
    assert rels.index("src/pkg/big.py") < rels.index("scripts/tool.py")
    assert rels.index("src/pkg/small.py") < rels.index("scripts/tool.py")
    # within src/, the larger file wins
    assert rels.index("src/pkg/big.py") < rels.index("src/pkg/small.py")


def test_candidate_ranking_respects_limit(tmp_path):
    for i in range(6):
        (tmp_path / f"m{i}.py").write_text("x = 1\n")

    candidates = gate_canary._candidate_source_files(tmp_path, limit=3)

    assert len(candidates) == 3


def test_candidate_ranking_excludes_test_and_vcs_dirs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("assert 1 == 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hooks.py").write_text("x = 1\n")
    (tmp_path / "real.py").write_text("x = 1\n")

    candidates = gate_canary._candidate_source_files(tmp_path, limit=10)

    assert [str(p.relative_to(tmp_path)) for p in candidates] == ["real.py"]


def test_candidate_ranking_handles_unstattable_file(tmp_path):
    """A broken symlink (or any matched path that fails to stat) must not
    crash ranking -- treated as size 0 rather than propagating the OSError."""
    (tmp_path / "broken_link.py").symlink_to(tmp_path / "does-not-exist.py")

    candidates = gate_canary._candidate_source_files(tmp_path, limit=10)

    assert [str(p.relative_to(tmp_path)) for p in candidates] == ["broken_link.py"]


# --------------------------------------------------------------------------- #
# _project_subtree_root -- BLOCKER #1's core fix
# --------------------------------------------------------------------------- #

def test_project_subtree_root_is_scratch_itself_when_root_is_toplevel(tmp_path):
    root = tmp_path / "repo"
    _init_repo(root, {"a.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    scratch = tmp_path / "scratch-checkout"
    scratch.mkdir()

    subtree = gate_canary._project_subtree_root(cfg, scratch)

    assert subtree == scratch.resolve()


def test_project_subtree_root_is_a_subdir_when_project_self_hosts(tmp_path):
    """nyxloom's own layout: cfg.root is a SUBDIRECTORY of the git
    top-level."""
    repo = tmp_path / "repo"
    (repo / "myproj").mkdir(parents=True)
    (repo / "myproj" / "a.py").write_text("x = 1\n")
    (repo / "other" / "b.py").parent.mkdir(parents=True)
    (repo / "other" / "b.py").write_text("x = 1\n")
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _run(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")

    cfg = _FakeCfg(repo / "myproj")
    scratch = tmp_path / "scratch-checkout"
    (scratch / "myproj").mkdir(parents=True)
    (scratch / "other").mkdir()

    subtree = gate_canary._project_subtree_root(cfg, scratch)

    assert subtree == (scratch / "myproj").resolve()
    assert subtree != scratch.resolve()


def test_project_subtree_root_falls_back_when_repo_root_is_unrelated(tmp_path, monkeypatch):
    """Pathological case: gate_runner._repo_root's answer is NOT actually an
    ancestor of cfg.root (relative_to raises ValueError) -- falls back to
    treating the whole scratch as the subtree rather than crashing."""
    (tmp_path / "myproj").mkdir()
    cfg = _FakeCfg(tmp_path / "myproj")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    monkeypatch.setattr(
        gate_canary.gate_runner, "_repo_root",
        lambda cfg_: str(tmp_path / "unrelated-repo-root"),
    )

    subtree = gate_canary._project_subtree_root(cfg, scratch)

    assert subtree == scratch.resolve()


# --------------------------------------------------------------------------- #
# discover_canary_candidates -- real git, subtree-scoped discovery
# --------------------------------------------------------------------------- #

def test_discover_canary_candidates_stays_inside_subtree(tmp_path):
    """BLOCKER #1 regression at the discovery layer: an alphabetically-
    earlier sibling file OUTSIDE the project subtree must never be picked."""
    root = tmp_path / "repo"
    head = _init_repo(root, {
        "aaa-sibling.py": "x = 1\n",          # alphabetically first, OUTSIDE the subtree
        "myproj/src/myproj/core.py": "def add(a, b):\n    return a + b\n",
    })
    cfg = _FakeCfg(root / "myproj")

    candidates = gate_canary.discover_canary_candidates(cfg, head, limit=10)

    assert candidates == ["myproj/src/myproj/core.py"]
    assert not any("aaa-sibling" in c for c in candidates)


def test_discover_canary_candidates_raises_when_subtree_has_no_source(tmp_path):
    """Even though a SIBLING directory has .py source, the project's own
    subtree has none -- CanaryError, not a silent wander outside the
    project."""
    root = tmp_path / "repo"
    head = _init_repo(root, {
        "aaa-sibling.py": "x = 1\n",
        "myproj/README.md": "docs only\n",
    })
    cfg = _FakeCfg(root / "myproj")

    with pytest.raises(gate_canary.CanaryError, match="own subtree"):
        gate_canary.discover_canary_candidates(cfg, head)


def test_discover_canary_candidates_cleans_up_scratch(tmp_path):
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n"})
    cfg = _FakeCfg(root)

    gate_canary.discover_canary_candidates(cfg, head)

    scratch = root / ".worktrees" / f"canary-discover-{head[:12]}"
    assert not scratch.exists()
    assert str(scratch) not in _run(root, "worktree", "list").stdout


def test_discover_canary_candidates_worktree_add_failure_raises(tmp_path):
    root = tmp_path / "repo"
    _init_repo(root, {"a.py": "x = 1\n"})
    cfg = _FakeCfg(root)

    with pytest.raises(gate_canary.CanaryError, match="could not create a scratch worktree"):
        gate_canary.discover_canary_candidates(cfg, "deadbeef" * 5)


def test_discover_canary_candidates_removes_stale_scratch_before_discovering(tmp_path):
    """A leftover discovery scratch worktree from an interrupted prior run
    is removed before this run re-adds it (idempotent, mirrors
    build_canary_commit's own stale-scratch handling)."""
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n"})
    cfg = _FakeCfg(root)

    stale = root / ".worktrees" / f"canary-discover-{head[:12]}"
    assert _run(root, "worktree", "add", "--detach", str(stale), head).returncode == 0
    assert stale.exists()

    candidates = gate_canary.discover_canary_candidates(cfg, head)

    assert candidates == ["a.py"]
    assert not stale.exists()


# --------------------------------------------------------------------------- #
# build_canary_commit -- the real git scratch-worktree build/cleanup cycle
# --------------------------------------------------------------------------- #

def test_build_canary_commit_produces_a_real_commit_and_cleans_up(tmp_path):
    root = tmp_path / "repo"
    head = _init_repo(root, {"thing.py": "def add(a, b):\n    return a + b\n"})
    cfg = _FakeCfg(root)

    outcome = gate_canary.build_canary_commit(cfg, head, "thing.py")

    assert outcome.commit != head
    assert _run(root, "cat-file", "-e", outcome.commit).returncode == 0
    assert outcome.mechanism == gate_canary.MECHANISM_IMPORT_BREAK
    assert outcome.target_path == "thing.py"

    diff = _run(root, "diff", "--name-only", head, outcome.commit).stdout.split()
    assert diff == ["thing.py"]

    scratch = root / ".worktrees" / f"canary-{head[:12]}"
    assert not scratch.exists()
    assert str(scratch) not in _run(root, "worktree", "list").stdout

    # the ORIGINAL checkout is untouched
    assert (root / "thing.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_build_canary_commit_worktree_add_failure_raises(tmp_path):
    root = tmp_path / "repo"
    _init_repo(root, {"thing.py": "x = 1\n"})
    cfg = _FakeCfg(root)

    with pytest.raises(gate_canary.CanaryError, match="could not create a scratch worktree"):
        gate_canary.build_canary_commit(cfg, "deadbeef" * 5, "thing.py")


def test_build_canary_commit_commit_step_failure_raises_and_cleans_up(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    head = _init_repo(root, {"thing.py": "x = 1\n"})
    cfg = _FakeCfg(root)

    real_run = subprocess.run

    def _fake_run(argv, **kwargs):
        if gate_canary._CANARY_COMMIT_MESSAGE in argv:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="simulated commit failure")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(gate_canary.subprocess, "run", _fake_run)

    with pytest.raises(gate_canary.CanaryError, match="canary commit failed"):
        gate_canary.build_canary_commit(cfg, head, "thing.py")

    scratch = root / ".worktrees" / f"canary-{head[:12]}"
    assert not scratch.exists()


def test_build_canary_commit_removes_stale_scratch_before_building(tmp_path):
    root = tmp_path / "repo"
    head = _init_repo(root, {"thing.py": "x = 1\n"})
    cfg = _FakeCfg(root)

    stale = root / ".worktrees" / f"canary-{head[:12]}"
    assert _run(root, "worktree", "add", "--detach", str(stale), head).returncode == 0
    assert stale.exists()

    outcome = gate_canary.build_canary_commit(cfg, head, "thing.py")

    assert outcome.commit != head
    assert not stale.exists()


# --------------------------------------------------------------------------- #
# verify_gate_rejects_canary -- multi-attempt rollup (MAJOR #2 fix), with
# gate_runner.run_gate_at_commit monkeypatched for deterministic per-attempt
# exit codes (the real subprocess path is covered by the integration test
# below and by build_canary_commit's own real-git tests above).
# --------------------------------------------------------------------------- #

def test_verify_gate_rejects_canary_stops_at_first_kill(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n" * 10, "b.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    gate = _gate(["true"])

    calls = []

    def fake_run(cfg_, gate_, commit, phase="post-merge"):
        calls.append(commit)
        from nyxloom.types import GateResult, utc_now
        return GateResult(gate_id=gate_.gate_id, phase=phase, commit=commit, exit_code=1,
                          started=utc_now(), ended=utc_now(), environment=gate_.environment)

    monkeypatch.setattr(gate_canary.gate_runner, "run_gate_at_commit", fake_run)

    result = gate_canary.verify_gate_rejects_canary(cfg, gate, head, max_attempts=4)

    assert result.killed is True
    assert result.inconclusive is False
    assert len(result.attempts) == 1          # stopped after the first kill
    assert len(calls) == 1


def test_verify_gate_rejects_canary_launders_when_all_survive(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n" * 10, "b.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    gate = _gate(["true"])

    def fake_run(cfg_, gate_, commit, phase="post-merge"):
        from nyxloom.types import GateResult, utc_now
        return GateResult(gate_id=gate_.gate_id, phase=phase, commit=commit, exit_code=0,
                          started=utc_now(), ended=utc_now(), environment=gate_.environment)

    monkeypatch.setattr(gate_canary.gate_runner, "run_gate_at_commit", fake_run)

    result = gate_canary.verify_gate_rejects_canary(cfg, gate, head, max_attempts=4)

    assert result.killed is False
    assert result.inconclusive is False
    assert len(result.attempts) == 2           # both candidates tried, both survived
    assert all(a.exit_code == 0 for a in result.attempts)


def test_verify_gate_rejects_canary_inconclusive_on_timeout(tmp_path, monkeypatch):
    """MINOR #4: a canary run that times out (124) is neither a kill nor a
    clean survival -- with no genuine kill anywhere, the verdict rolls up to
    inconclusive, not LAUNDERS."""
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n" * 10, "b.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    gate = _gate(["true"])

    exit_codes = iter([124, 0])  # first attempt times out, second survives cleanly

    def fake_run(cfg_, gate_, commit, phase="post-merge"):
        from nyxloom.types import GateResult, utc_now
        return GateResult(gate_id=gate_.gate_id, phase=phase, commit=commit,
                          exit_code=next(exit_codes),
                          started=utc_now(), ended=utc_now(), environment=gate_.environment)

    monkeypatch.setattr(gate_canary.gate_runner, "run_gate_at_commit", fake_run)

    result = gate_canary.verify_gate_rejects_canary(cfg, gate, head, max_attempts=4)

    assert result.killed is False
    assert result.inconclusive is True
    assert len(result.attempts) == 2
    assert result.attempts[0].inconclusive is True
    assert result.attempts[1].inconclusive is False


def test_verify_gate_rejects_canary_execfail_127_is_inconclusive_not_launders(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    gate = _gate(["true"])

    def fake_run(cfg_, gate_, commit, phase="post-merge"):
        from nyxloom.types import GateResult, utc_now
        return GateResult(gate_id=gate_.gate_id, phase=phase, commit=commit, exit_code=127,
                          started=utc_now(), ended=utc_now(), environment=gate_.environment)

    monkeypatch.setattr(gate_canary.gate_runner, "run_gate_at_commit", fake_run)

    result = gate_canary.verify_gate_rejects_canary(cfg, gate, head, max_attempts=1)

    assert result.killed is False
    assert result.inconclusive is True


# --------------------------------------------------------------------------- #
# REAL end-to-end integration test -- no mocking of run_gate_at_commit at
# all. This is the actual regression guard for BLOCKER #1 (canary scoped to
# a sibling directory outside the project subtree): cfg.root is a
# SUBDIRECTORY of the git top-level (nyxloom's own layout), a sibling
# directory alphabetically BEFORE it has an importable module the project's
# gate never touches, and a REAL pytest gate covers only the project
# subtree's own tests.
# --------------------------------------------------------------------------- #

def _build_subtree_fixture(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    head = _init_repo(repo, {
        # Alphabetically FIRST, and OUTSIDE the project subtree -- the
        # project's own gate never imports this. Under the pre-fix
        # (repo-wide) scan, THIS is the file that would get mutated.
        "aaa-sibling/unrelated.py": "def helper(x):\n    return x + 1\n",
        "myproj/src/myproj/__init__.py": "",
        "myproj/src/myproj/core.py": "def add(a, b):\n    return a + b\n",
        "myproj/tests/test_core.py": textwrap.dedent("""\
            import os
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
            from myproj.core import add

            def test_add():
                assert add(1, 2) == 3
            """),
    })
    return repo, head


def test_verify_gate_rejects_canary_real_subtree_integration(tmp_path):
    """(a) the canary lands INSIDE the project subtree, never in the
    sibling directory; (b) the REAL gate (an actual pytest subprocess over
    the subtree's own tests) fails on it -> a genuine kill on the first
    attempt (TRUSTWORTHY-equivalent at this layer)."""
    repo, head = _build_subtree_fixture(tmp_path)
    cfg = _FakeCfg(repo / "myproj")
    gate = _gate([sys.executable, "-m", "pytest", "-q", "{worktree}/myproj/tests"], timeout=60)

    from nyxloom import gate_runner

    # Sanity: the real gate PASSES on known-good HEAD.
    good = gate_runner.run_gate_at_commit(cfg, gate, head, phase="verify-good")
    assert good.exit_code == 0

    result = gate_canary.verify_gate_rejects_canary(cfg, gate, head, max_attempts=4)

    assert len(result.attempts) == 1
    target = result.attempts[0].target_path
    assert target.startswith("myproj/")
    assert "aaa-sibling" not in target
    assert result.killed is True
    assert result.inconclusive is False


# --------------------------------------------------------------------------- #
# GA2b: inject_uncovered_line -- a valid, test-neutral, never-called function
# whose body lines no test covers (the coverage-canary mechanism).
# --------------------------------------------------------------------------- #

def test_inject_uncovered_line_appends_valid_never_called_function(tmp_path):
    import ast

    target = tmp_path / "core.py"
    original = "from __future__ import annotations\n\ndef add(a, b):\n    return a + b\n"
    target.write_text(original)

    description = gate_canary.inject_uncovered_line(target)

    mutated = target.read_text()
    # the original content is preserved verbatim as a prefix (pure append)
    assert mutated.startswith(original)
    # the result is valid Python declaring a NEW module-level function
    tree = ast.parse(mutated)
    funcs = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    assert funcs == ["add", gate_canary._UNCOVERED_CANARY_FUNC]
    # the canary function is never CALLED anywhere (only defined)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == gate_canary._UNCOVERED_CANARY_FUNC]
    assert calls == []
    assert "2 uncovered lines" in description


def test_inject_uncovered_line_adds_trailing_newline_when_missing(tmp_path):
    """A source file with NO trailing newline still yields valid Python
    (the append doesn't glue onto the last line)."""
    import ast

    target = tmp_path / "notrail.py"
    target.write_text("x = 1")   # deliberately no trailing newline

    gate_canary.inject_uncovered_line(target)

    mutated = target.read_text()
    ast.parse(mutated)   # raises if the append glued onto `x = 1`
    assert mutated.startswith("x = 1\n")
    assert gate_canary._UNCOVERED_CANARY_FUNC in mutated


def test_inject_uncovered_line_is_additive_only(tmp_path):
    """The injection is a pure append -- zero removed lines in the diff, the
    original body untouched (mirrors the import-break minimal-diff guard)."""
    root = tmp_path / "repo"
    original = "def add(a, b):\n    return a + b\n"
    head = _init_repo(root, {"core.py": original})

    gate_canary.inject_uncovered_line(root / "core.py")

    diff = _run(root, "diff", "--unified=0", "--", "core.py").stdout
    removed = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    assert removed == []
    added = [ln for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    # the appended block adds the def + its two uncovered body lines
    assert any("def " + gate_canary._UNCOVERED_CANARY_FUNC in ln for ln in added)
    assert any("return doubled" in ln for ln in added)
    assert "def add(a, b):" in (root / "core.py").read_text()


def test_build_canary_commit_uncovered_line_mechanism(tmp_path):
    """build_canary_commit routes the injector + mechanism through: an
    uncovered-line canary tags MECHANISM_UNCOVERED_LINE and actually appends
    the never-called function to the committed file."""
    root = tmp_path / "repo"
    head = _init_repo(root, {"thing.py": "def add(a, b):\n    return a + b\n"})
    cfg = _FakeCfg(root)

    outcome = gate_canary.build_canary_commit(
        cfg, head, "thing.py",
        inject=gate_canary.inject_uncovered_line,
        mechanism=gate_canary.MECHANISM_UNCOVERED_LINE)

    assert outcome.mechanism == gate_canary.MECHANISM_UNCOVERED_LINE
    assert outcome.commit != head
    # the committed blob carries the canary function; the live checkout does not
    committed = _run(root, "show", f"{outcome.commit}:thing.py").stdout
    assert gate_canary._UNCOVERED_CANARY_FUNC in committed
    assert gate_canary._UNCOVERED_CANARY_FUNC not in (root / "thing.py").read_text()


# --------------------------------------------------------------------------- #
# verify_gate_enforces_coverage -- the same multi-attempt rollup as GA1's
# verifier, with run_gate_at_commit monkeypatched for deterministic exit
# codes (the REAL coverage-floor behaviour is the integration test below).
# --------------------------------------------------------------------------- #

def _fake_run_returning(exit_code):
    def fake_run(cfg_, gate_, commit, phase="post-merge"):
        from nyxloom.types import GateResult, utc_now
        return GateResult(gate_id=gate_.gate_id, phase=phase, commit=commit,
                          exit_code=exit_code, started=utc_now(), ended=utc_now(),
                          environment=gate_.environment)
    return fake_run


def test_verify_gate_enforces_coverage_killed_when_floor_rejects(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n" * 10, "b.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    gate = _gate(["true"])

    phases = []

    def fake_run(cfg_, gate_, commit, phase="post-merge"):
        phases.append(phase)
        from nyxloom.types import GateResult, utc_now
        return GateResult(gate_id=gate_.gate_id, phase=phase, commit=commit, exit_code=1,
                          started=utc_now(), ended=utc_now(), environment=gate_.environment)

    monkeypatch.setattr(gate_canary.gate_runner, "run_gate_at_commit", fake_run)

    result = gate_canary.verify_gate_enforces_coverage(cfg, gate, head, max_attempts=4)

    assert result.killed is True
    assert result.inconclusive is False
    assert len(result.attempts) == 1                       # stops at first kill
    assert phases == ["verify-coverage-canary"]            # distinct phase from GA1


def test_verify_gate_enforces_coverage_launders_when_all_survive(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n" * 10, "b.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    gate = _gate(["true"])

    monkeypatch.setattr(gate_canary.gate_runner, "run_gate_at_commit", _fake_run_returning(0))

    result = gate_canary.verify_gate_enforces_coverage(cfg, gate, head, max_attempts=4)

    assert result.killed is False
    assert result.inconclusive is False
    assert len(result.attempts) == 2                       # both candidates tried, both survived


def test_verify_gate_enforces_coverage_inconclusive_on_timeout(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    head = _init_repo(root, {"a.py": "x = 1\n"})
    cfg = _FakeCfg(root)
    gate = _gate(["true"])

    monkeypatch.setattr(gate_canary.gate_runner, "run_gate_at_commit", _fake_run_returning(124))

    result = gate_canary.verify_gate_enforces_coverage(cfg, gate, head, max_attempts=1)

    assert result.killed is False
    assert result.inconclusive is True


# --------------------------------------------------------------------------- #
# REAL end-to-end integration -- the DISCRIMINATING proof (no mocking of
# run_gate_at_commit): the SAME uncovered-line canary is KILLED by a gate
# that enforces a changed-line-coverage floor, but LAUNDERED (survives) by a
# tests-only gate. This is what makes the coverage-canary distinct from the
# import-break canary: it isolates the coverage axis specifically.
# --------------------------------------------------------------------------- #

def test_verify_gate_enforces_coverage_real_discriminates_floor_from_tests_only(tmp_path):
    repo, head = _build_subtree_fixture(tmp_path)
    cfg = _FakeCfg(repo / "myproj")

    from nyxloom import gate_runner

    # A gate that enforces a 100% coverage floor over the project's package.
    # The --cov path MUST use {worktree} (not the original repo checkout):
    # run_gate_at_commit runs in a scratch worktree, and the test imports the
    # package from there, so coverage must measure THAT copy.
    cov_gate = _gate(
        [sys.executable, "-m", "pytest", "-q",
         "--cov={worktree}/myproj/src/myproj",
         "--cov-report=", "--cov-fail-under=100",
         "{worktree}/myproj/tests"],
        timeout=120)
    # A tests-only gate (no coverage floor at all) over the same tests.
    tests_only_gate = _gate(
        [sys.executable, "-m", "pytest", "-q", "{worktree}/myproj/tests"], timeout=120)

    # Sanity: BOTH gates pass known-good HEAD (100% covered, tests green).
    assert gate_runner.run_gate_at_commit(cfg, cov_gate, head, phase="verify-good").exit_code == 0
    assert gate_runner.run_gate_at_commit(cfg, tests_only_gate, head, phase="verify-good").exit_code == 0

    # The coverage-enforcing gate REJECTS the uncovered-line canary...
    killed = gate_canary.verify_gate_enforces_coverage(cfg, cov_gate, head, max_attempts=4)
    assert killed.killed is True
    assert killed.inconclusive is False
    assert killed.attempts[0].target_path.startswith("myproj/")

    # ...while the tests-only gate LAUNDERS the very same canary (it breaks no
    # test) -- proving the canary isolates the coverage axis, not test-pass.
    laundered = gate_canary.verify_gate_enforces_coverage(cfg, tests_only_gate, head, max_attempts=4)
    assert laundered.killed is False
    assert laundered.inconclusive is False
