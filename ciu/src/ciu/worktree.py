"""S16 — per-worktree stack instances (`ciu worktree add|rm|list`).

WHY THIS IS CIU'S JOB
--------------------
A git worktree of a CIU repo is already a distinct CIU instance: ``INSTANCE_ID``
is a hash of the PHYSICAL repo path (S2), so a second checkout gets its own
network, container prefix and volumes automatically. Everything needed to stand
one up is therefore already CIU's data — instance identity, network naming,
profile narrowing (S7.5), the render-input layer. What was missing was a verb
that COMPOSES them, so consumers wrote it out as prose instead: a five-step
recipe (``git worktree add`` → ``ciu env generate`` → hand-append
``CIU_SERVICES_PROFILE`` → render → up) that each operator retyped and could get
subtly wrong.

It passes the placement test in ``docs/DESIGN-NOTES.md`` D7 — *would a project
using only CIU still get value from this?* — because isolated instances are what
any CIU consumer wants from a second checkout, with no other tool involved.

THE ORDER IN `rm` IS THE POINT
------------------------------
``ciu clean`` FIRST, ``git worktree remove`` SECOND.

``ciu down`` stops containers but PRESERVES volumes, so it leaves ``vol-*`` bind
dirs behind — and Postgres, Redis and friends chown those to their own uid, so a
subsequent ``rm -rf`` of the worktree fails with ``Permission denied`` and
strands them on disk. ``ciu clean`` routes removal through a privileged helper
(``engine.privileged_rmtree``) for exactly this reason.

Doing it in the other order is worse than slow, it is unrecoverable-by-CIU:
once the checkout is gone, so is the rendered config that told CIU what to
remove, and the leftovers can only be cleared by hand with a root helper
container. This module refuses to remove a checkout it has not cleaned.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKTREE_DIR = ".worktrees"


class WorktreeError(RuntimeError):
    """A worktree operation failed (configuration/environment; exit 2)."""


@dataclass(frozen=True)
class WorktreeInfo:
    """One entry from ``git worktree list --porcelain``."""

    path: Path
    branch: str
    head: str

    @property
    def is_primary(self) -> bool:
        """True for the main checkout (never a candidate for `rm`)."""
        return not (self.path / ".git").is_file()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:  # pragma: no cover - git absent is environmental
        raise WorktreeError(f"[S16] could not run git: {exc}") from exc


def list_worktrees(repo_root: Path) -> list[WorktreeInfo]:
    """Every registered worktree, primary first (git's own order)."""
    res = _git(["worktree", "list", "--porcelain"], repo_root)
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16] `git worktree list` failed in {repo_root}: "
            f"{(res.stderr or res.stdout).strip()}"
        )
    out: list[WorktreeInfo] = []
    path = head = branch = ""
    for line in res.stdout.splitlines() + [""]:
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("HEAD "):
            head = line[len("HEAD "):][:8]
        elif line.startswith("branch "):
            branch = line[len("branch "):].removeprefix("refs/heads/")
        elif line.startswith("detached"):
            branch = "(detached)"
        elif not line and path:
            out.append(WorktreeInfo(Path(path), branch or "(unknown)", head))
            path = head = branch = ""
    return out


def find_worktree(repo_root: Path, name: str) -> WorktreeInfo | None:
    """Look a worktree up by NAME (its directory basename) or by path."""
    target = Path(name)
    for wt in list_worktrees(repo_root):
        if wt.path.name == name or (target.is_absolute() and wt.path == target):
            return wt
    return None


def _generate_env_in(worktree: Path) -> int:
    """Run ``ciu env generate`` INSIDE *worktree*, with the primary checkout's
    repo-path variables STRIPPED from the child environment.

    CIU-10 reconciles a pre-set ``PHYSICAL_REPO_ROOT`` against mountinfo, so an
    inherited value is usually caught — but the honest input here is *no* value:
    the whole point of generating is to DERIVE this checkout's identity, and an
    inherited one is another repo's answer to the same question.
    """
    import os
    import sys

    env = {
        k: v for k, v in os.environ.items()
        if k not in ("REPO_ROOT", "PHYSICAL_REPO_ROOT", "DOCKER_NETWORK_INTERNAL",
                     "INSTANCE_ID", "REPO_NAME", "CIU_SERVICES_PROFILE")
    }
    argv = [sys.executable, "-m", "ciu.cli", "env", "generate"]
    try:
        return subprocess.run(argv, cwd=str(worktree), env=env, check=False).returncode
    except OSError as exc:  # pragma: no cover - environmental
        raise WorktreeError(f"[S16] could not run `ciu env generate`: {exc}") from exc


def _clean_in(worktree: Path, *, yes: bool) -> int:
    """Run ``ciu clean`` INSIDE *worktree*, under that worktree's own ciu.env.

    A subprocess, not an in-process call, and deliberately so. S1.1 requires
    ``--define-root`` to agree with ``REPO_ROOT``; this process's REPO_ROOT
    normally points at the PRIMARY checkout, so an in-process clean of a
    worktree would either abort on that guard or — worse, if the guard were
    bypassed — clean the wrong instance. Handing the child the worktree's own
    environment makes the two agree honestly instead of arguing.
    """
    import os
    import sys

    # parse_workspace_env, NOT load_workspace_env: the latter mutates THIS
    # process's os.environ, and it locates the file via find_workspace_env,
    # which prefers `$REPO_ROOT` over the directory it was given — so with the
    # primary's REPO_ROOT set it would read the PRIMARY's ciu.env and we would
    # clean the wrong instance under a convincingly-correct-looking env. Name
    # the file explicitly; it is a fact, not something to search for.
    from .workspace_env import parse_workspace_env

    env_file = worktree / "ciu.env"
    if not env_file.is_file():
        raise WorktreeError(
            f"[S16] {env_file} does not exist, so CIU cannot tell which instance "
            "to clean. Run `ciu env generate` in that worktree first — cleaning "
            "under the PRIMARY checkout's environment would target the wrong "
            "stack."
        )
    env = dict(os.environ)
    try:
        env.update(parse_workspace_env(env_file))
    except OSError as exc:
        raise WorktreeError(f"[S16] could not read {env_file}: {exc}") from exc

    argv = [sys.executable, "-m", "ciu.cli", "clean"] + (["-y"] if yes else [])
    try:
        return subprocess.run(argv, cwd=str(worktree), env=env, check=False).returncode
    except OSError as exc:  # pragma: no cover - environmental
        raise WorktreeError(f"[S16] could not run `ciu clean`: {exc}") from exc


def add(
    repo_root: Path,
    name: str,
    *,
    base: str = "main",
    profile: str | None = None,
    worktree_dir: str = DEFAULT_WORKTREE_DIR,
) -> Path:
    """Create worktree *name* and make it a ready-to-deploy CIU instance.

    Creates ``<repo>/<worktree_dir>/<name>`` on a new branch named *name* off
    *base*, then generates that checkout's own ``ciu.env`` — which is what gives
    it a distinct ``INSTANCE_ID``, network and container prefix (S2). With
    *profile*, narrows the instance to those service profiles (S7.5) by writing
    ``CIU_SERVICES_PROFILE`` into the new ``ciu.env``.

    The worktree lives UNDER the repo root deliberately. A consumer whose gating
    test container bind-mounts the repo can then see it for free; a worktree in
    ``/tmp`` is invisible to that container and its tests cannot be gated there.

    Deploy is deliberately NOT performed: `add` prepares an instance, it does
    not decide that you want it running.
    """
    if not name or "/" in name or name.startswith("."):
        raise WorktreeError(
            f"[S16] invalid worktree name {name!r}: must be a single path "
            "component, not starting with '.'"
        )

    target = repo_root / worktree_dir / name
    if target.exists():
        raise WorktreeError(
            f"[S16] {target} already exists. Use `ciu worktree rm {name}` first, "
            "or pick another name."
        )

    res = _git(["worktree", "add", "-b", name, str(target), base], repo_root)
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16] `git worktree add` failed: "
            f"{(res.stderr or res.stdout).strip()}"
        )

    # The new checkout's OWN ciu.env. Without this it would inherit whatever the
    # ambient environment says and collide with the primary instance's network
    # and container names — the failure this verb exists to prevent.
    #
    # Subprocess, for the same reason as _clean_in: this process's REPO_ROOT /
    # PHYSICAL_REPO_ROOT normally describe the PRIMARY checkout, and generation
    # reads them (CIU-10's reconciliation). Generating in-process would derive
    # the new instance's identity from the old instance's environment.
    rc = _generate_env_in(target)
    if rc != 0:
        raise WorktreeError(
            f"[S16] worktree created at {target}, but `ciu env generate` failed "
            f"there (exit {rc}). The checkout is NOT a usable instance yet; fix "
            f"the cause and re-run `ciu env generate` in {target}."
        )

    if profile:
        env_file = target / "ciu.env"
        with env_file.open("a", encoding="utf-8") as fh:
            fh.write(
                "\n# Instance service narrowing (Seam 4 / S7.5), set by "
                "`ciu worktree add --profile`\n"
                f'export CIU_SERVICES_PROFILE="{profile}"\n'
            )
    return target


def remove(
    repo_root: Path,
    name: str,
    *,
    yes: bool = False,
    force: bool = False,
) -> Path:
    """Dispose of worktree *name*: ``ciu clean`` first, then remove the checkout.

    Never reorders those two steps — see the module docstring. A clean that fails
    ABORTS the removal (unless *force*), because removing the checkout after a
    failed clean destroys the only config that could complete it and leaves
    root-owned volume dirs no unprivileged operator can delete.
    """
    wt = find_worktree(repo_root, name)
    if wt is None:
        raise WorktreeError(
            f"[S16] no worktree named {name!r} under {repo_root}. "
            "`ciu worktree list` shows what exists."
        )
    if wt.is_primary or wt.path.resolve() == repo_root.resolve():
        raise WorktreeError(
            f"[S16] refusing to remove {wt.path}: that is the PRIMARY checkout, "
            "not a worktree instance."
        )

    rc = _clean_in(wt.path, yes=yes)
    if rc != 0 and not force:
        raise WorktreeError(
            f"[S16] `ciu clean` failed (exit {rc}) in {wt.path}; NOT removing the "
            "checkout. Removing it now would destroy the rendered config that "
            "tells CIU what to clean, stranding root-owned vol-* directories "
            "that an unprivileged `rm -rf` cannot delete. Fix the cause and "
            "retry, or pass --force to remove anyway (leftovers become your "
            "problem)."
        )

    res = _git(["worktree", "remove", str(wt.path)] + (["--force"] if force else []),
               repo_root)
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16] volumes were cleaned, but `git worktree remove` failed: "
            f"{(res.stderr or res.stdout).strip()}"
        )
    return wt.path
