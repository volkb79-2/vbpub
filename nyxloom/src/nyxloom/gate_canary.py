"""Canary-commit builder for `nyxloom gate verify` (PACKAGE GA1,
docs/plan-gate-adoption.md checklist item 6: "Proven to REJECT").

`cli.cmd_gate_verify` needs to prove a project's declared gate actually
REJECTS broken code, not just passes good code (the `argv=["true"]` trap
named in `reference/STANDARD.md` §"What nyxloom requires of a project").
This module's ONE job is to produce that broken code as a real git commit --
`build_canary_commit` -- so the caller can hand it straight to
`gate_runner.run_gate_at_commit` and reuse that module's ONE isolation
mechanism (canonical `reference/LESSONS.md` L1) instead of a second
hand-rolled scratch-worktree runner here.

Two mutation mechanisms, tried in order:

  1. MUTATION (behavioral) -- reuse `mutation_gate.generate_mutants` against
     the first source file (deterministic path order) that yields at least
     one AST-mutable comparison/boolop/bool-constant node, and apply its
     first mutant (deterministic mutant order). This is a real behavioral
     change: a comparison operator flips (`<` -> `<=`), a boolean operator
     flips (`and` -> `or`), or a bool constant flips -- exactly the mutation
     catalogue `mutation_gate` already uses for D-CORRECT-4.
  2. FALLBACK (structural) -- when no file has an AST-mutable site (e.g. a
     tiny/data-only project with no comparisons to flip at all), inject
     `raise AssertionError("nyxloom-gate-canary")` as the first line of the
     first source file (deterministic path order). This breaks import for
     any test that imports the module, which is the weakest canary that
     still requires the gate to actually *run* the code under test.

Neither mechanism needs a coverage report: `gate verify` runs against
whatever gate a project has declared *today*, and a project's coverage.json
is often gate-internal (produced only as part of that same gate run) rather
than a standing artifact this verb could read beforehand.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import mutation_gate
from .config import ProjectConfig

MECHANISM_MUTATION = "mutation"
MECHANISM_FALLBACK = "fallback-assert"

# Directories never eligible as a canary target: VCS internals, nyxloom's own
# scratch-worktree parking lot, and test suites themselves (mutating a TEST
# would prove nothing about the SOURCE the gate exists to protect).
_EXCLUDED_DIR_NAMES = {
    ".git", ".worktrees", "tests", "test", "__pycache__", ".venv", "venv",
    "node_modules", ".tox", "build", "dist",
}

_CANARY_COMMIT_MESSAGE = "nyxloom gate-verify canary (disposable, never merged)"


class CanaryError(Exception):
    """No eligible canary target exists (neither a mutable comparison/boolop/
    bool-const anywhere, nor any `.py` source file at all), or the scratch
    worktree needed to build one could not be created/committed."""


@dataclass
class CanaryOutcome:
    commit: str          # the new commit carrying exactly one known-bad change
    mechanism: str        # MECHANISM_MUTATION | MECHANISM_FALLBACK
    target_path: str      # repo-relative path of the mutated file
    description: str      # human-readable: what changed, for the verb's output


def _repo_root(cfg: ProjectConfig) -> str:
    """The git top-level for cfg.root, or cfg.root itself if the call fails
    (mirrors gate_runner._repo_root -- nyxloom self-hosts as a subdirectory
    of the vbpub repo, so cfg.root is not always the repo root)."""
    res = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else str(cfg.root)


def _iter_source_files(worktree: Path) -> list[Path]:
    """Deterministic (sorted) `.py` files under `worktree`, excluding VCS /
    scratch / test / venv directories anywhere in their path."""
    out = []
    for path in sorted(worktree.rglob("*.py")):
        rel_parts = path.relative_to(worktree).parts[:-1]
        if any(part in _EXCLUDED_DIR_NAMES for part in rel_parts):
            continue
        out.append(path)
    return out


def _find_mutation(worktree: Path) -> tuple[Path, mutation_gate.Mutant] | None:
    """First (path, mutant) pair in deterministic file order. Every line of
    each candidate file is offered as a target line -- this layer has no
    coverage report to narrow against (see module docstring)."""
    for path in _iter_source_files(worktree):
        source = path.read_text(encoding="utf-8")
        n_lines = source.count("\n") + 1
        mutants = mutation_gate.generate_mutants(source, set(range(1, n_lines + 1)))
        if mutants:
            return path, mutants[0]
    return None


def inject_canary(worktree: Path) -> tuple[str, str, str]:
    """Mutate exactly one file IN PLACE under `worktree`.

    Returns (mechanism, target_relpath, description). Raises CanaryError when
    `worktree` has no `.py` source file at all (mutation AND fallback both
    have nothing to work with).
    """
    found = _find_mutation(worktree)
    if found is not None:
        path, mutant = found
        path.write_text(mutant.mutated_source, encoding="utf-8")
        return (
            MECHANISM_MUTATION,
            str(path.relative_to(worktree)),
            f"line {mutant.lineno}: {mutant.description}",
        )

    files = _iter_source_files(worktree)
    if not files:
        raise CanaryError(
            "no .py source file found under the project worktree -- cannot "
            "build a canary (neither a mutable comparison/boolop/bool-const "
            "site nor any source file to inject a fallback assertion into)"
        )
    fallback_path = files[0]
    original = fallback_path.read_text(encoding="utf-8")
    fallback_path.write_text(
        'raise AssertionError("nyxloom-gate-canary")\n' + original, encoding="utf-8",
    )
    return (
        MECHANISM_FALLBACK,
        str(fallback_path.relative_to(worktree)),
        "injected module-top-level raise AssertionError('nyxloom-gate-canary')",
    )


def build_canary_commit(cfg: ProjectConfig, commit: str) -> CanaryOutcome:
    """Build a new commit = `commit` + one known-bad mutation.

    Uses a disposable scratch worktree solely to author the commit; it is
    ALWAYS removed before returning (`finally`), same discipline as
    `gate_runner.run_gate_at_commit`. The returned commit SHA lives on in the
    repo's object graph after the worktree is gone (a git commit is
    content-addressed/immutable once made), so `gate_runner.
    run_gate_at_commit` can check it out fresh exactly like it would any
    other commit -- no second isolation mechanism needed here.
    """
    repo_root = _repo_root(cfg)
    scratch = Path(repo_root) / ".worktrees" / f"canary-{commit[:12]}"
    if scratch.exists():
        subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True)
    add = subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "--detach", str(scratch), commit],
        capture_output=True, text=True,
    )
    if add.returncode != 0:
        raise CanaryError(
            f"could not create a scratch worktree at {commit[:12]} to build the "
            f"canary: {add.stderr.strip()}"
        )
    try:
        mechanism, target_path, description = inject_canary(scratch)
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
        subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", str(scratch)],
                       capture_output=True, text=True)
    return CanaryOutcome(commit=canary_commit, mechanism=mechanism,
                         target_path=target_path, description=description)
