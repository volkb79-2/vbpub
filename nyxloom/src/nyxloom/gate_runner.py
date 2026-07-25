"""Shared gate-execution primitive (factory-hardening F).

Running a project's verification gate against a specific commit -- in a clean
detached scratch worktree checked out at that commit, never the operator's live
checkout -- is needed in more than one place: the daemon's post-merge validation
(`daemon._run_post_merge_gate`) and the manual merge-record path
(`cli.cmd_merge`, which F newly gates). This module is the ONE implementation of
"select the project's verification gate" and "run a gate at a commit and return
a GateResult", so those callers share it instead of each carrying a divergent
copy (canonical `reference/LESSONS.md` L1: one source of truth, not N
hand-maintained copies -- the same principle factory-hardening A applied to the
JSON schemas).

Deliberately NOT (yet) folded into `daemon._execute_auto_merge`'s pre-merge /
mutation runs: those gate an in-progress merge tree in a scratch worktree the
method has ALREADY built and has not yet published as a ref -- a genuinely
different shape (gate BEFORE the commit is reachable as `<default_branch>`), not
a commit that already exists in the object graph. Converging them is a possible
follow-up, flagged here rather than hidden.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import GateDef, ProjectConfig
from .types import GateResult, utc_now


def select_verification_gate(cfg: ProjectConfig) -> GateDef | None:
    """The gate used to (re-)verify a merged or merge-candidate commit.

    Prefers a project's declared ``phase == "post-merge"`` gate; failing that,
    falls back to its ``"implementation"`` gate -- the same gate the code was
    already required to pass, re-run as the verification check (the documented
    default; no project registered today declares a dedicated post-merge gate).
    Returns None only when the project declares no gate at all. Lowest
    ``gate_id`` wins among ties, so selection is deterministic.
    """
    post_merge = [g for g in cfg.gates.values() if g.phase == "post-merge"]
    if post_merge:
        return sorted(post_merge, key=lambda g: g.gate_id)[0]
    impl = [g for g in cfg.gates.values() if g.phase == "implementation"]
    if impl:
        return sorted(impl, key=lambda g: g.gate_id)[0]
    return None


def _repo_root(cfg: ProjectConfig) -> str:
    """The git top-level for cfg.root, or cfg.root itself if the call fails.
    nyxloom self-hosts as a SUBDIRECTORY of the vbpub repo, so cfg.root is not
    always the repo root -- resolving the top-level is what makes worktree ops
    and ref updates land in the right repository (mirrors the daemon's own
    resolution)."""
    res = subprocess.run(
        ["git", "-C", str(cfg.root), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else str(cfg.root)


def run_gate_at_commit(cfg: ProjectConfig, gate: GateDef, commit: str,
                       phase: str = "post-merge") -> GateResult:
    """Run ``gate.argv`` against ``commit`` and return a GateResult
    (``exit_code == 0`` is a pass).

    The gate runs in a fresh detached scratch worktree checked out at ``commit``,
    with ``{worktree}`` in the argv substituted for that scratch path -- so the
    gate validates exactly ``commit`` and never the operator's live checkout
    (which may sit on another branch or carry uncommitted edits). If the scratch
    worktree cannot be created (e.g. ``commit`` is not a real object), it falls
    back to running against the live checkout root -- mirroring
    ``daemon._run_post_merge_gate``'s own fallback, so a placeholder/unknown
    commit degrades to a live-tree run rather than a hard error. The scratch
    worktree is always removed.
    """
    repo_root = _repo_root(cfg)
    scratch: Path | None = None
    if commit and repo_root:
        scratch = Path(repo_root) / ".worktrees" / f"gaterun-{commit[:12]}"
        if scratch.exists():
            subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", str(scratch)],
                           capture_output=True, text=True)
        add = subprocess.run(
            ["git", "-C", repo_root, "worktree", "add", "--detach", str(scratch), commit],
            capture_output=True, text=True,
        )
        if add.returncode != 0:
            scratch = None  # fall back to the live root below
    gate_cwd = str(scratch) if scratch is not None else str(cfg.root)
    worktree_value = str(scratch) if scratch is not None else repo_root
    argv = [tok.replace("{worktree}", worktree_value) for tok in gate.argv]
    started = utc_now()
    try:
        proc = subprocess.run(argv, cwd=gate_cwd, capture_output=True,
                              text=True, timeout=gate.timeout_seconds)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124  # conventional shell timeout exit code
    except OSError:
        exit_code = 127  # command-not-found / exec failure
    finally:
        if scratch is not None:
            subprocess.run(["git", "-C", repo_root, "worktree", "remove", "--force", str(scratch)],
                           capture_output=True, text=True)
    return GateResult(
        gate_id=gate.gate_id, phase=phase, commit=commit,
        exit_code=exit_code, started=started, ended=utc_now(),
        environment=gate.environment,
    )
