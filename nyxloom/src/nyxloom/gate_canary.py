"""Canary builder + multi-attempt verifier for `nyxloom gate verify`
(PACKAGE GA1, docs/plan-gate-adoption.md checklist item 6: "Proven to
REJECT"; redesigned after adversarial review of the first cut -- see that
review for the full rationale of each design choice below).

`cli.cmd_gate_verify` needs to prove a project's declared gate actually
REJECTS broken code, not just passes good code (the `argv=["true"]` trap
named in `reference/STANDARD.md` §"What nyxloom requires of a project").
This module's job: `verify_gate_rejects_canary` tries up to
`_MAX_CANARY_ATTEMPTS` distinct known-bad commits and reports whether ANY of
them made the gate fail.

Design choices (each closes a review finding on the first cut):

* **Subtree-scoped, never repo-wide.** nyxloom self-hosts as a subdirectory
  of the vbpub repo (`cfg.root` = `.../vbpub/nyxloom`, git top-level =
  `.../vbpub`); topos/naf are similar. A canary must never wander into a
  SIBLING project's source the gate never runs against -- that made the
  first cut deterministically print LAUNDERS for nyxloom's own (strong)
  gate. `_project_subtree_root` confines every candidate search to
  `cfg.root`'s own subtree of the checkout.
* **Import-break is the ONLY mechanism**, not a mutation/AST-rewrite. The
  first cut used `mutation_gate.generate_mutants` + `ast.unparse`, which (a)
  needed COVERED-line knowledge this layer doesn't have (an arbitrary
  mutable line may sit in dead code) and (b) `ast.unparse` reformats the
  WHOLE file, so any lint/format-checking gate would flag the reformat
  itself and "detect" the canary for the wrong reason (false TRUSTWORTHY).
  `inject_import_break` is a single-line insertion
  (`raise AssertionError("nyxloom-gate-canary")` as the first real
  statement, after any module docstring / `from __future__ import`), so any
  test that imports the module fails on import -- no coverage knowledge
  needed, no reformat produced.
* **Multi-attempt.** One arbitrary file being untested by the suite doesn't
  mean the gate launders -- it means that ONE file was a bad pick.
  `verify_gate_rejects_canary` tries up to `_MAX_CANARY_ATTEMPTS` distinct
  files (largest / `src`-package files first, most likely to be imported by
  the suite), stopping at the first genuine kill. LAUNDERS only when EVERY
  attempt survives; if none survive cleanly but none is a genuine kill
  either (every non-zero exit was itself a timeout/exec-failure), the
  verdict is INCONCLUSIVE, not LAUNDERS.

Each attempt is its own real git commit, built in a disposable scratch
worktree (mirroring `gate_runner.run_gate_at_commit`'s own discipline,
always removed via `finally`) and handed to
`gate_runner.run_gate_at_commit` -- the ONE isolation primitive used to
actually RUN a gate against a commit (canonical `reference/LESSONS.md` L1).
This module still hand-rolls its OWN `git worktree add`/`remove` for the
BUILD step (there is no existing primitive for "create commit C+1 with one
file mutated" to reuse -- `gate_runner` only runs a gate at an
already-existing commit), so it is not a fully second isolation mechanism,
just the one extra step gate_runner has no equivalent for.
"""
from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import gate_runner
from .config import GateDef, ProjectConfig

MECHANISM_IMPORT_BREAK = "import-break"

# Up to this many distinct source files are tried before giving up (largest/
# src-package files first -- see _candidate_source_files).
_MAX_CANARY_ATTEMPTS = 4

_ASSERT_STMT = 'raise AssertionError("nyxloom-gate-canary")\n'
_CANARY_COMMIT_MESSAGE = "nyxloom gate-verify canary (disposable, never merged)"

# gate_runner.run_gate_at_commit's own conventional sentinels for "the gate
# subprocess itself never rendered a verdict" (timeout / exec failure) --
# neither counts as a genuine rejection of the canary's content.
_INCONCLUSIVE_EXIT_CODES = frozenset({124, 127})

# Directories never eligible as a canary target: VCS internals, nyxloom's own
# scratch-worktree parking lot, and test suites themselves (mutating a TEST
# would prove nothing about the SOURCE the gate exists to protect).
_EXCLUDED_DIR_NAMES = {
    ".git", ".worktrees", "tests", "test", "__pycache__", ".venv", "venv",
    "node_modules", ".tox", "build", "dist",
}


class CanaryError(Exception):
    """No eligible canary target exists in the project's subtree (no `.py`
    source file at all), or a scratch worktree needed to discover/build one
    could not be created/committed."""


@dataclass
class CanaryOutcome:
    commit: str          # the new commit carrying exactly one known-bad change
    mechanism: str        # always MECHANISM_IMPORT_BREAK today
    target_path: str      # repo-top-level-relative path of the mutated file
    description: str      # human-readable: what changed


@dataclass
class CanaryAttempt:
    """One (build canary commit, run the gate against it) round."""
    target_path: str
    commit: str
    exit_code: int
    description: str

    @property
    def killed(self) -> bool:
        """A genuine rejection: non-zero, and not a timeout/exec-failure
        sentinel the gate subprocess itself produced rather than the gate's
        actual verdict logic."""
        return self.exit_code != 0 and self.exit_code not in _INCONCLUSIVE_EXIT_CODES

    @property
    def inconclusive(self) -> bool:
        return self.exit_code in _INCONCLUSIVE_EXIT_CODES


@dataclass
class CanaryVerifyResult:
    killed: bool                                    # >=1 attempt genuinely killed
    inconclusive: bool                               # no kill, but >=1 attempt inconclusive
    attempts: list[CanaryAttempt] = field(default_factory=list)


def _project_subtree_root(cfg: ProjectConfig, scratch: Path) -> Path:
    """The directory WITHIN `scratch` (a checkout of cfg.root's repo) that
    corresponds to cfg.root itself -- `scratch` unchanged when cfg.root IS
    the git top-level (the common case: most projects put nyxloom.toml at
    the repo root), or a real subdirectory of `scratch` for a project that
    self-hosts inside a larger repo (nyxloom itself; see module docstring).
    """
    repo_root = Path(gate_runner._repo_root(cfg)).resolve()
    project_root = Path(cfg.root).resolve()
    try:
        rel = project_root.relative_to(repo_root)
    except ValueError:
        rel = Path(".")
    return (scratch / rel).resolve()


def _candidate_source_files(subtree: Path, limit: int) -> list[Path]:
    """Up to `limit` distinct `.py` files under `subtree` (never outside
    it), ranked deterministically: files under a `src/` package directory
    first (most likely to be the actual importable package the suite
    exercises, vs. tooling/scripts at the repo root), then largest file
    first (a bigger module is more likely to be imported by SOMETHING),
    then alphabetical path as the final tiebreak for full determinism."""
    def _rank(path: Path) -> tuple:
        rel = path.relative_to(subtree)
        not_in_src = "src" not in rel.parts[:-1]
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return (not_in_src, -size, str(rel))

    files = []
    for path in subtree.rglob("*.py"):
        rel_parts = path.relative_to(subtree).parts[:-1]
        if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts):
            continue
        files.append(path)
    files.sort(key=_rank)
    return files[:limit]


def _insertion_index_after_future(source: str) -> int:
    """1-based line count to skip before inserting the canary statement --
    past any leading module docstring and/or `from __future__ import ...`
    line(s), so the injected line never trips `SyntaxError: from __future__
    imports must occur at the beginning of the file` (this repo's own
    modules commonly start with `from __future__ import annotations`).
    Falls back to 0 (prepend) on anything that isn't a clean docstring/
    future-import prefix, and on an unparseable source (the insertion still
    happens; a syntax-broken source is not this function's problem)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    idx = 0
    for i, node in enumerate(tree.body):
        if (i == 0 and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            idx = node.end_lineno
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            idx = node.end_lineno
            continue
        break
    return idx


def inject_import_break(path: Path) -> str:
    """Insert `raise AssertionError("nyxloom-gate-canary")` as the first
    real statement of `path`'s module body (MINIMAL one-line insertion --
    never a whole-file reformat). Any test that imports this module fails
    on import. Returns a human-readable description of what changed."""
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    idx = _insertion_index_after_future(original)
    lines.insert(idx, _ASSERT_STMT)
    path.write_text("".join(lines), encoding="utf-8")
    return f"inserted `{_ASSERT_STMT.strip()}` at line {idx + 1}"


def discover_canary_candidates(
        cfg: ProjectConfig, commit: str, limit: int = _MAX_CANARY_ATTEMPTS) -> list[str]:
    """Up to `limit` candidate target files (repo-top-level-relative, POSIX
    separators), discovered from a disposable READ-ONLY scratch worktree
    checked out at `commit` (never the live cfg.root, which may be dirty or
    on a different commit) -- always removed (`finally`). Raises
    CanaryError when the project's OWN SUBTREE has no `.py` source file at
    all, or the scratch worktree itself could not be created.
    """
    repo_root = Path(gate_runner._repo_root(cfg))
    scratch = repo_root / ".worktrees" / f"canary-discover-{commit[:12]}"
    if scratch.exists():
        subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True)
    add = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", "--detach", str(scratch), commit],
        capture_output=True, text=True,
    )
    if add.returncode != 0:
        raise CanaryError(
            f"could not create a scratch worktree at {commit[:12]} to discover canary "
            f"candidates: {add.stderr.strip()}"
        )
    try:
        subtree = _project_subtree_root(cfg, scratch)
        candidates = _candidate_source_files(subtree, limit)
        if not candidates:
            raise CanaryError(
                "no .py source file found under the project's own subtree -- cannot "
                "build a canary (a canary is never drawn from outside the project, "
                "even when it self-hosts inside a larger repo)"
            )
        return [str(p.relative_to(scratch)) for p in candidates]
    finally:
        subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True)


def build_canary_commit(cfg: ProjectConfig, commit: str, target_relpath: str) -> CanaryOutcome:
    """Build ONE canary commit = `commit` + an import-break injected into
    `target_relpath` (repo-top-level-relative, as returned by
    `discover_canary_candidates`). Uses a disposable scratch worktree solely
    to author the commit; it is ALWAYS removed before returning (`finally`)
    -- the returned commit SHA lives on in the repo's object graph after the
    worktree is gone (a git commit is content-addressed/immutable once
    made), so `gate_runner.run_gate_at_commit` can check it out fresh
    exactly like it would any other commit.
    """
    repo_root = Path(gate_runner._repo_root(cfg))
    scratch = repo_root / ".worktrees" / f"canary-{commit[:12]}"
    if scratch.exists():
        subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True)
    add = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "add", "--detach", str(scratch), commit],
        capture_output=True, text=True,
    )
    if add.returncode != 0:
        raise CanaryError(
            f"could not create a scratch worktree at {commit[:12]} to build the "
            f"canary: {add.stderr.strip()}"
        )
    try:
        target = scratch / target_relpath
        description = inject_import_break(target)
        subprocess.run(["git", "-C", str(scratch), "add", "-A"],
                       capture_output=True, text=True)
        commit_res = subprocess.run(
            ["git", "-C", str(scratch),
             "-c", "user.email=nyxloom-gate-canary@local",
             "-c", "user.name=nyxloom-gate-canary",
             "commit", "-qm", _CANARY_COMMIT_MESSAGE],
            capture_output=True, text=True,
        )
        if commit_res.returncode != 0:
            raise CanaryError(f"canary commit failed: {commit_res.stderr.strip()}")
        rev = subprocess.run(
            ["git", "-C", str(scratch), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        canary_commit = rev.stdout.strip()
    finally:
        subprocess.run(["git", "-C", str(repo_root), "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True)
    return CanaryOutcome(commit=canary_commit, mechanism=MECHANISM_IMPORT_BREAK,
                         target_path=target_relpath, description=description)


def verify_gate_rejects_canary(
        cfg: ProjectConfig, gate: GateDef, commit: str,
        max_attempts: int = _MAX_CANARY_ATTEMPTS) -> CanaryVerifyResult:
    """Try up to `max_attempts` distinct canaries against `gate`, stopping
    at the first genuine kill (`CanaryAttempt.killed`) -- TRUSTWORTHY only
    needs ONE proof the gate can reject broken code. If every attempt
    SURVIVES (exit 0), `killed=False, inconclusive=False` (LAUNDERS). If no
    attempt survives cleanly AND no attempt is a genuine kill either (every
    non-zero exit was itself a timeout/exec-failure sentinel --
    `_INCONCLUSIVE_EXIT_CODES`), `killed=False, inconclusive=True`: the gate
    never actually rendered a verdict on the broken code, so nothing here
    proves either TRUSTWORTHY or LAUNDERS.
    """
    targets = discover_canary_candidates(cfg, commit, max_attempts)
    attempts: list[CanaryAttempt] = []
    for target in targets:
        outcome = build_canary_commit(cfg, commit, target)
        result = gate_runner.run_gate_at_commit(cfg, gate, outcome.commit, phase="verify-canary")
        attempt = CanaryAttempt(target_path=outcome.target_path, commit=outcome.commit,
                                exit_code=result.exit_code, description=outcome.description)
        attempts.append(attempt)
        if attempt.killed:
            return CanaryVerifyResult(killed=True, inconclusive=False, attempts=attempts)
    any_inconclusive = any(a.inconclusive for a in attempts)
    return CanaryVerifyResult(killed=False, inconclusive=any_inconclusive, attempts=attempts)
