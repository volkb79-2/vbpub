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

import fcntl
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config_model
from . import procutil

DEFAULT_WORKTREE_DIR = ".worktrees"

# S16.1 — shared-infra join intent keys (CIU-22). A worktree created with
# `--shared-infra` carries these four in its OWN ciu.env; see
# SharedInfraIntent / parse_shared_infra_intent below.
SHARED_INFRA_REF_PATH = "CIU_SHARED_INFRA_REF_PATH"
SHARED_INFRA_NETWORK = "CIU_SHARED_INFRA_NETWORK"
SHARED_INFRA_SERVICES = "CIU_SHARED_INFRA_SERVICES"
SHARED_INFRA_REF_PROJECTS = "CIU_SHARED_INFRA_REF_PROJECTS"


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


@dataclass(frozen=True)
class SharedInfraIntent:
    """S16.1/CIU-22 — a worktree's recorded intent to join a reference
    instance's shared-infra network, resolved and validated once at
    ``worktree add --shared-infra`` time and persisted verbatim into this
    worktree's own ``ciu.env`` (see :func:`parse_shared_infra_intent`,
    :func:`connect_shared_infra_after_up`)."""

    ref_path: Path
    network: str
    services: tuple[str, ...]
    ref_projects: tuple[str, ...]


def _split_unique_list(raw: str, *, label: str) -> tuple[str, ...]:
    """Split *raw* on commas into a non-empty, duplicate-free, order-preserving
    tuple. Every item is stripped; a blank item or a duplicate is a loud
    ``WorktreeError`` — this is shared by add-time CLI parsing and ciu.env
    intent parsing so both reject the same malformed input the same way."""
    items = [p.strip() for p in raw.split(",")]
    if not raw or any(not item for item in items):
        raise WorktreeError(
            f"[S16.1] {label} must be a non-empty comma-separated list with no "
            f"blank items: {raw!r}"
        )
    seen: set[str] = set()
    for item in items:
        if item in seen:
            raise WorktreeError(f"[S16.1] {label} contains a duplicate item: {item!r}")
        seen.add(item)
    return tuple(items)


def parse_shared_infra_intent(values: Mapping[str, str]) -> SharedInfraIntent | None:
    """The SOLE reader of the S16.1 shared-infra intent recorded in a
    worktree's own ``ciu.env`` (CIU-22). Called by ``engine.py`` against
    ``os.environ`` after bootstrap.

    All four fields absent returns ``None`` — an ordinary worktree, unaffected
    by this feature. Any PARTIAL, empty, malformed, or duplicate-containing
    group raises :class:`WorktreeError`: no consumer may silently proceed on a
    corrupt intent (a hand-edited or truncated ``ciu.env``).
    """
    keys = (
        SHARED_INFRA_REF_PATH, SHARED_INFRA_NETWORK,
        SHARED_INFRA_SERVICES, SHARED_INFRA_REF_PROJECTS,
    )
    present = [k for k in keys if values.get(k)]
    if not present:
        return None
    missing = [k for k in keys if not values.get(k)]
    if missing:
        raise WorktreeError(
            f"[S16.1] partial shared-infra intent in ciu.env: {', '.join(missing)} "
            f"missing while {', '.join(present)} set. This checkout's ciu.env is "
            "corrupt or was hand-edited; regenerate it via `ciu worktree add "
            "--shared-infra ...` or remove all four CIU_SHARED_INFRA_* fields."
        )
    return SharedInfraIntent(
        ref_path=Path(values[SHARED_INFRA_REF_PATH]),
        network=values[SHARED_INFRA_NETWORK],
        services=_split_unique_list(values[SHARED_INFRA_SERVICES], label=SHARED_INFRA_SERVICES),
        ref_projects=_split_unique_list(
            values[SHARED_INFRA_REF_PROJECTS], label=SHARED_INFRA_REF_PROJECTS
        ),
    )


def _check_reference_network_and_projects(network: str, ref_projects: tuple[str, ...]) -> None:
    """S16.1 — the two reference-live Docker checks, shared by add-time
    preflight and post-up revalidation: *network* must exist, and EVERY
    *ref_projects* entry (AND-combined, never OR) must have at least one
    RUNNING container on it carrying ``com.docker.compose.project=<R>``. This
    proves only that something carrying each operator-supplied label is
    running — R is never derived or independently authenticated by CIU.

    A network containing only an unrelated/undeclared-project container (the
    masquerader case) fails here because the per-project query is scoped to
    both the network AND the exact declared label — a bare labelled-container
    count on the network is not accepted as liveness.
    """
    try:
        inspect = procutil.docker(["network", "inspect", network], capture=True, check=False)
    except (FileNotFoundError, OSError) as exc:
        raise WorktreeError(f"[S16.1] could not inspect network {network!r}: {exc}") from exc
    if inspect.returncode != 0:
        raise WorktreeError(
            f"[S16.1] shared-infra network {network!r} does not exist or is not "
            f"inspectable: {(inspect.stderr or inspect.stdout or '').strip()}"
        )

    for project in ref_projects:
        try:
            ps = procutil.docker(
                [
                    "ps",
                    "--filter", f"network={network}",
                    "--filter", f"label=com.docker.compose.project={project}",
                    "--format", "{{.ID}}",
                ],
                capture=True, check=False,
            )
        except (FileNotFoundError, OSError) as exc:
            raise WorktreeError(
                f"[S16.1] could not query reference project {project!r} on "
                f"network {network!r}: {exc}"
            ) from exc
        ids = [line for line in (ps.stdout or "").splitlines() if line.strip()]
        if ps.returncode != 0 or not ids:
            raise WorktreeError(
                f"[S16.1] shared-infra reference project {project!r} has no "
                f"running container on network {network!r}; the reference "
                "instance does not look live. Bring it up first, or drop "
                "--shared-infra."
            )


def _preflight_shared_infra_for_add(
    repo_root: Path,
    *,
    shared_infra: str,
    shared_infra_services: str,
    shared_infra_ref_projects: str,
) -> SharedInfraIntent:
    """S16.1 — resolve and validate `worktree add --shared-infra` input
    BEFORE any side effect (no git worktree, no checkout). Read-only Docker
    checks only; see the module's O1 contract."""
    from .workspace_env import parse_workspace_env

    ref = find_worktree(repo_root, shared_infra)
    if ref is None:
        raise WorktreeError(
            f"[S16.1] --shared-infra {shared_infra!r} does not resolve to a "
            f"registered worktree under {repo_root}. `ciu worktree list` shows "
            "what exists."
        )

    ref_env_file = ref.path / "ciu.env"
    if not ref_env_file.is_file():
        raise WorktreeError(
            f"[S16.1] {ref_env_file} does not exist, so CIU cannot read the "
            "reference worktree's network. Run `ciu env generate` there first."
        )
    try:
        ref_env = parse_workspace_env(ref_env_file)
    except OSError as exc:
        raise WorktreeError(f"[S16.1] could not read {ref_env_file}: {exc}") from exc

    network = ref_env.get("DOCKER_NETWORK_INTERNAL", "")
    if not network:
        raise WorktreeError(
            f"[S16.1] {ref_env_file} has no DOCKER_NETWORK_INTERNAL; the "
            "reference worktree is not a usable CIU instance."
        )

    services = _split_unique_list(shared_infra_services, label="--shared-infra-services")
    ref_projects = _split_unique_list(
        shared_infra_ref_projects, label="--shared-infra-ref-projects"
    )

    _check_reference_network_and_projects(network, ref_projects)

    return SharedInfraIntent(
        ref_path=ref.path, network=network, services=services, ref_projects=ref_projects,
    )


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
    shared_infra: str | None = None,
    shared_infra_services: str | None = None,
    shared_infra_ref_projects: str | None = None,
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

    With *shared_infra* (S16.1/CIU-22), joins this instance's declared
    diverging-tier services onto an EXISTING reference worktree's shared-infra
    network instead of standing up a second copy of heavy, rarely-diverging
    infrastructure. *shared_infra* is the reference worktree (the same
    basename-or-absolute-path grammar as :func:`find_worktree`);
    *shared_infra_services* and *shared_infra_ref_projects* are raw
    comma-separated lists (this function owns validation, not argparse). All
    three, together with a non-empty *profile*, are an all-or-nothing group —
    partial input is a loud :class:`WorktreeError`, never silent inference
    from a compose file. The reference is fully validated (registered, its own
    ``ciu.env`` network, every declared reference project actually running on
    it) BEFORE any side effect; `add` records the resolved intent into the new
    worktree's own ``ciu.env`` but never joins anything itself — the actual
    ``docker network connect`` calls happen later, at ``ciu up`` time, in the
    new worktree's own process (see :func:`connect_shared_infra_after_up`).
    This instance keeps its OWN ``DOCKER_NETWORK_INTERNAL`` throughout; only
    the declared diverging services later gain a SECOND membership.
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

    shared_infra_intent: SharedInfraIntent | None = None
    if shared_infra is not None or shared_infra_services is not None or shared_infra_ref_projects is not None:
        if not (shared_infra and shared_infra_services and shared_infra_ref_projects and profile):
            raise WorktreeError(
                "[S16.1] --shared-infra requires --shared-infra-services, "
                "--shared-infra-ref-projects, and a non-empty --profile all "
                "together; got a partial group. No mode may infer a tier from "
                "a compose file."
            )
        shared_infra_intent = _preflight_shared_infra_for_add(
            repo_root,
            shared_infra=shared_infra,
            shared_infra_services=shared_infra_services,
            shared_infra_ref_projects=shared_infra_ref_projects,
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

    if shared_infra_intent is not None:
        env_file = target / "ciu.env"
        with env_file.open("a", encoding="utf-8") as fh:
            fh.write(
                "\n# Shared-infra join intent (S16.1/CIU-22), set by "
                "`ciu worktree add --shared-infra`. Resolved and validated at "
                "add-time; `ciu up` re-validates before joining -- this "
                "instance's OWN DOCKER_NETWORK_INTERNAL never changes.\n"
                f'export {SHARED_INFRA_REF_PATH}="{shared_infra_intent.ref_path}"\n'
                f'export {SHARED_INFRA_NETWORK}="{shared_infra_intent.network}"\n'
                f'export {SHARED_INFRA_SERVICES}="{",".join(shared_infra_intent.services)}"\n'
                f'export {SHARED_INFRA_REF_PROJECTS}='
                f'"{",".join(shared_infra_intent.ref_projects)}"\n'
            )
    return target


def remove(
    repo_root: Path,
    name: str,
    *,
    yes: bool = False,
    force: bool = False,
) -> Path:
    """Dispose of worktree *name*: ``ciu clean``, then remove the checkout.

    Never reorders those steps — see the module docstring. A clean that fails
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


def _network_container_ids(network: str) -> set[str]:
    """The FULL (untruncated) container IDs currently members of *network*.

    Matched against ``docker ps --no-trunc`` IDs in
    :func:`connect_shared_infra_after_up` — ``docker ps``'s default ``.ID``
    format is a truncated 12-char ID, while ``docker network inspect``'s
    ``.Containers`` map is keyed by the FULL 64-char ID; comparing a truncated
    ID against a full one would never match, silently treating every target as
    absent. ``--no-trunc`` on the ``ps`` side is what keeps this comparison
    valid.
    """
    try:
        res = procutil.docker(
            [
                "network", "inspect", network,
                "--format", "{{range $id, $c := .Containers}}{{$id}} {{end}}",
            ],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise WorktreeError(
            f"[S16.1] could not inspect shared-infra network {network!r} "
            f"membership: {exc}"
        ) from exc
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.1] could not inspect shared-infra network {network!r} "
            f"membership: {(res.stderr or res.stdout or '').strip()}"
        )
    return set((res.stdout or "").split())


def _disconnect_rollback(network: str, connected_ids: list[str]) -> list[str]:
    """Disconnect *connected_ids* from *network* in REVERSE order — only IDs
    THIS invocation itself connected, never a pre-existing member or a
    concurrent no-op. Returns human-readable failure strings for any
    disconnect that itself failed; the caller appends them to the original
    failure rather than swallowing them."""
    failures: list[str] = []
    for cid in reversed(connected_ids):
        try:
            res = procutil.docker(
                ["network", "disconnect", network, cid], capture=True, check=False
            )
        except (FileNotFoundError, OSError) as exc:
            failures.append(f"{cid}: {exc}")
            continue
        if res.returncode != 0:
            failures.append(f"{cid}: {(res.stderr or res.stdout or '').strip()}")
    return failures


def _connect_failure_message(
    cname: str, cid: str, network: str, detail: str, rollback_failures: list[str]
) -> str:
    message = (
        f"[S16.1] could not connect {cname!r} ({cid}) to shared-infra network "
        f"{network!r}: {detail}"
    )
    if rollback_failures:
        message += "; rollback also failed for: " + "; ".join(rollback_failures)
    return message


def connect_shared_infra_after_up(
    repo_root: Path,
    compose_project: str,
    intent: SharedInfraIntent,
) -> None:
    """S16.1/CIU-22 — after a SUCCESSFUL non-dry-run ``docker compose up`` of
    THIS instance's own stack, join its declared diverging-tier services onto
    *intent*'s reference network. Called from ``engine.main_execution`` and
    ``engine.run_shipped``, never before Compose has succeeded and never for a
    dry run.

    This instance's own ``DOCKER_NETWORK_INTERNAL`` and its base compose/
    overlay network declarations are NEVER touched — only the declared
    diverging-service containers gain a SECOND membership, on the reference
    network, via imperative ``docker network connect`` calls outside compose.

    Every precondition below is checked BEFORE any connect is attempted:

    1. Re-resolve *intent.ref_path* against the CURRENT ``git worktree list``
       (the reference may have been removed since `add`); re-read its
       explicit ``ciu.env``; require its ``DOCKER_NETWORK_INTERNAL`` still
       equal *intent.network* (catches a stale recording); refuse any
       declared reference project equal to *compose_project* (a reference
       must belong to the OTHER instance); then re-run the same AND-combined
       reference-liveness check `add` used, so a reference stopped between
       verbs is caught here too.
    2. For every declared service, require at least one RUNNING container in
       *compose_project* carrying that service's label — a previously joined
       child masquerading as live infra never satisfies this because it
       carries its OWN project's label, not the reference's.
    3. Only once every precondition above holds does this snapshot the
       reference network's membership and connect each declared-service
       container ABSENT from that snapshot. On every NON-ZERO connect result,
       re-inspect the network for that SAME container ID (Docker STATE, never
       Docker diagnostic TEXT): present means a successful CONCURRENT no-op
       (this invocation never connected it, so it is not recorded and never
       rolled back); absent means a genuine failure, which disconnects only
       this invocation's own zero-return connects, in reverse order, and
       re-raises.

    Never runs ``docker compose down`` on failure: this instance's own stack
    stays up, on its own network, observably not joined.
    """
    from .workspace_env import parse_workspace_env

    ref = find_worktree(repo_root, str(intent.ref_path))
    if ref is None:
        raise WorktreeError(
            f"[S16.1] shared-infra reference {intent.ref_path} is no longer a "
            f"registered worktree under {repo_root}; it may have been removed. "
            "Restore it, re-run `ciu worktree add --shared-infra` to update the "
            "recorded reference, or `ciu down` this instance."
        )

    ref_env_file = ref.path / "ciu.env"
    if not ref_env_file.is_file():
        raise WorktreeError(
            f"[S16.1] {ref_env_file} does not exist; the shared-infra reference "
            "is not a usable CIU instance any more."
        )
    try:
        ref_env = parse_workspace_env(ref_env_file)
    except OSError as exc:
        raise WorktreeError(f"[S16.1] could not read {ref_env_file}: {exc}") from exc

    current_network = ref_env.get("DOCKER_NETWORK_INTERNAL", "")
    if not current_network or current_network != intent.network:
        raise WorktreeError(
            f"[S16.1] shared-infra reference network changed (recorded "
            f"{intent.network!r}, now {current_network or '(absent)'!r}); "
            "refusing to join a network that may no longer be the reference's "
            "own."
        )

    for project in intent.ref_projects:
        if project == compose_project:
            raise WorktreeError(
                f"[S16.1] declared reference project {project!r} is this "
                "instance's OWN compose project; a reference project must "
                "belong to the reference instance, not the one joining it."
            )

    _check_reference_network_and_projects(intent.network, intent.ref_projects)

    targets: list[tuple[str, str]] = []
    for service in intent.services:
        try:
            res = procutil.docker(
                [
                    "ps", "--no-trunc",
                    "--filter", f"label=com.docker.compose.project={compose_project}",
                    "--filter", f"label=com.docker.compose.service={service}",
                    "--format", "{{.ID}}\t{{.Names}}",
                ],
                capture=True, check=False,
            )
        except (FileNotFoundError, OSError) as exc:
            raise WorktreeError(
                f"[S16.1] could not query shared-infra service {service!r} in "
                f"project {compose_project!r}: {exc}"
            ) from exc
        rows = [line for line in (res.stdout or "").splitlines() if line.strip()]
        if res.returncode != 0 or not rows:
            raise WorktreeError(
                f"[S16.1] declared shared-infra service {service!r} has no "
                f"running container in compose project {compose_project!r}; "
                "cannot join a service Compose did not start."
            )
        for row in rows:
            cid, _, cname = row.partition("\t")
            targets.append((cid, cname))

    # Deterministic connect order.
    targets.sort(key=lambda t: t[1])

    existing_members = _network_container_ids(intent.network)
    absent = [(cid, cname) for cid, cname in targets if cid not in existing_members]

    connected: list[str] = []
    for cid, cname in absent:
        try:
            result = procutil.docker(
                ["network", "connect", intent.network, cid], capture=True, check=False
            )
        except (FileNotFoundError, OSError) as exc:
            rollback_failures = _disconnect_rollback(intent.network, connected)
            raise WorktreeError(
                _connect_failure_message(
                    cname, cid, intent.network, str(exc), rollback_failures
                )
            ) from exc

        if result.returncode == 0:
            connected.append(cid)
            continue

        # Non-zero: Docker STATE, not Docker diagnostic TEXT, decides the
        # outcome. Re-inspect membership for this SAME target ID. A failure
        # HERE must still roll back this invocation's own earlier successful
        # connects (`connected`) rather than propagate straight past them.
        try:
            members_now = _network_container_ids(intent.network)
        except WorktreeError as exc:
            rollback_failures = _disconnect_rollback(intent.network, connected)
            message = str(exc)
            if rollback_failures:
                message += "; rollback also failed for: " + "; ".join(rollback_failures)
            raise WorktreeError(message) from exc
        if cid in members_now:
            # Another actor joined it between the snapshot and this connect
            # call: a successful concurrent no-op. This invocation never
            # connected it, so it is not recorded and never rolled back.
            continue

        rollback_failures = _disconnect_rollback(intent.network, connected)
        detail = (result.stderr or result.stdout or "").strip()
        raise WorktreeError(
            _connect_failure_message(cname, cid, intent.network, detail, rollback_failures)
        )


# ===========================================================================
# S16.3 — worktree instance concurrency budget (CIU-24)
# ===========================================================================
#
# A repository's git-worktree family shares one host, so nothing today caps
# how many `ciu worktree` instances can be deployed at once against what the
# host can actually sustain. This is deliberately NOT a `[governance]` /
# `[<root>.governance]` value and does NOT participate in CIU-13's global/
# stack governance merge (see governance.resolve_stack_governance): capacity
# is one policy for the whole git-worktree family, never a property of the
# single stack being launched — a stack that raised its own budget could
# starve every sibling instance on the host.
#
# The ONLY file-level configuration source is the PRIMARY *Git* worktree's
# own CIU root's global table:
#
#   [ciu.worktree]
#   max_concurrent_instances = 3
#
# "Primary Git worktree" and "this process's own CIU configuration root" are
# NOT the same path in a monorepo (dev.py:resolve_repo_root walks up to a CIU
# marker, which may sit below `git rev-parse --show-toplevel`) — the offset
# between them is derived once (`_ciu_root_offset`) and re-applied, verbatim,
# to every linked worktree so a candidate's CIU root is never assumed to
# equal its raw git worktree path.

_MAX_CONCURRENT_ENV = "CIU_MAX_CONCURRENT_WORKTREES"
_MAX_CONCURRENT_DECIMAL_RE = re.compile(r"^[1-9][0-9]*$")
_BUDGET_LOCK_NAME = "ciu-worktree-budget.lock"


def primary_worktree_root(repo_root: Path) -> Path:
    """The registered PRIMARY **Git** worktree of *repo_root*'s family (S16.3).

    This is the git-rooted checkout, NOT necessarily this process's own CIU
    configuration root — see :func:`primary_ciu_root` for the translation.
    Zero or multiple primary entries is a loud ``[S16.3]`` failure; the path
    is never chosen by iteration order.
    """
    primaries = [wt for wt in list_worktrees(repo_root) if wt.is_primary]
    if len(primaries) != 1:
        raise WorktreeError(
            f"[S16.3] expected exactly one primary git worktree under "
            f"{repo_root}, found {len(primaries)}"
        )
    return primaries[0].path


def git_toplevel(repo_root: Path) -> Path:
    """``git rev-parse --show-toplevel`` from *repo_root* (S16.3).

    This is the GIT root, which may sit ABOVE this process's own CIU root in
    a monorepo (see :func:`primary_ciu_root`) — never substituted for it.
    """
    res = _git(["rev-parse", "--show-toplevel"], repo_root)
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.3] `git rev-parse --show-toplevel` failed in {repo_root}: "
            f"{(res.stderr or res.stdout).strip()}"
        )
    out = (res.stdout or "").strip()
    top = Path(out)
    if not out or not top.is_absolute() or not top.is_dir():
        raise WorktreeError(
            f"[S16.3] `git rev-parse --show-toplevel` in {repo_root} did not "
            f"return one absolute, existing directory: {out!r}"
        )
    return top


def _ciu_root_offset(repo_root: Path) -> Path:
    """The relative offset from this process's own git toplevel to its CIU
    root (``Path(".")`` for a standalone CIU git repository). Computed once
    here and re-applied, verbatim, to every linked worktree by both
    :func:`primary_ciu_root` and the candidate translation in
    :func:`worktree_budget_slot` — the sole namespace translation this
    feature uses, never re-derived differently in two places.
    """
    repo_root = Path(repo_root).resolve()
    top = git_toplevel(repo_root)
    try:
        return repo_root.relative_to(top)
    except ValueError as exc:
        raise WorktreeError(
            f"[S16.3] CIU root {repo_root} is not under its own git "
            f"top-level {top}; cannot derive the git-root-to-CIU-root offset"
        ) from exc


def primary_ciu_root(repo_root: Path) -> Path:
    """This process's family's PRIMARY CIU configuration root (S16.3).

    Applies the EXACT relative offset between *repo_root* (this process's own
    CIU root) and its git toplevel to the registered primary GIT worktree —
    never the git root itself, and never a linked (non-primary) worktree's
    own branch, which may carry a conflicting policy. Failure to derive the
    offset, or an absent derived root, is a loud ``[S16.3]`` failure: silently
    falling back to the git root is exactly the bug this function exists to
    prevent in a monorepo where the CIU marker sits below the git top-level.
    """
    repo_root = Path(repo_root).resolve()
    offset = _ciu_root_offset(repo_root)
    primary = primary_worktree_root(repo_root)
    candidate = primary / offset
    if not candidate.is_dir():
        raise WorktreeError(
            f"[S16.3] derived primary CIU root {candidate} (primary "
            f"worktree {primary} + offset {offset}) does not exist"
        )
    return candidate


def resolve_max_concurrent_instances(
    raw: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int | None:
    """S16.3 — validate the file-declared ``[ciu.worktree]`` table and apply
    the ``CIU_MAX_CONCURRENT_WORKTREES`` ambient override.

    *raw* is validated even when an ambient override is also present — an
    ambient override never masks an invalid file table. There is no
    ``0 == unlimited`` sentinel: only absence at BOTH sources means no cap.
    """
    file_value: int | None = None
    if raw is not None:
        if not isinstance(raw, Mapping):
            raise WorktreeError(
                f"[S16.3] [ciu.worktree] must be a table, got "
                f"{type(raw).__name__}: {raw!r}"
            )
        unknown = set(raw) - {"max_concurrent_instances"}
        if unknown:
            raise WorktreeError(
                f"[S16.3] unknown key(s) in [ciu.worktree]: "
                f"{', '.join(sorted(unknown))}"
            )
        if "max_concurrent_instances" in raw:
            value = raw["max_concurrent_instances"]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise WorktreeError(
                    "[S16.3] [ciu.worktree] max_concurrent_instances must be "
                    f"a positive integer, got {value!r}"
                )
            file_value = value

    env = os.environ if environ is None else environ
    ambient = env.get(_MAX_CONCURRENT_ENV)
    if ambient is None:
        return file_value
    if not _MAX_CONCURRENT_DECIMAL_RE.match(ambient):
        raise WorktreeError(
            f"[S16.3] ${_MAX_CONCURRENT_ENV} must be a positive decimal "
            f"integer (no sign, no leading zero, no decimal point, no "
            f"surrounding whitespace), got {ambient!r}"
        )
    return int(ambient)


def resolve_worktree_cap(repo_root: Path) -> int | None:
    """S16.3/CIU-24 — the sole concurrency-budget cap for THIS process, read
    once from the PRIMARY CIU root's global ``[ciu.worktree]`` table (never
    the current stack's own render, and never governance.py's CIU-13 global/
    stack merge — see the module banner above).

    A *repo_root* that is not inside a git work tree at all has no worktree
    family and therefore no file-level policy to read. This mirrors the
    codebase's own existing precedent for exactly this condition
    (``engine._check_gitignore``, S1.7): skip the file lookup silently
    rather than raise. An explicit ``CIU_MAX_CONCURRENT_WORKTREES`` ambient
    override is still honoured even then — a caller CAN set that outside
    git — and :func:`worktree_budget_slot` itself will refuse loudly the
    moment it tries to enumerate worktrees for a non-``None`` cap it cannot
    actually honour.
    """
    repo_root = Path(repo_root).resolve()
    raw: Any = None
    if _git(["rev-parse", "--show-toplevel"], repo_root).returncode == 0:
        root = primary_ciu_root(repo_root)
        try:
            root_global = config_model.render_global_chain(
                root, root, write_rendered=False
            )
        except ValueError as exc:
            if not str(exc).startswith("[ERROR] No global configuration found."):
                raise
            root_global = {}
        raw = root_global.get("ciu", {}).get("worktree")
    return resolve_max_concurrent_instances(raw)


@dataclass(frozen=True)
class _BudgetCandidate:
    """One registered, renderable worktree instance, resolved BEFORE the
    S16.3 family lock is taken."""

    worktree_path: Path
    stack: Path
    network: str
    project: str


def _candidate_project(candidate_global: dict, candidate_stack: Path) -> str:
    # Lazy import: engine.py imports worktree.py for the budget slot, so a
    # module-level `worktree -> engine` import would cycle.
    from . import engine

    return engine.compose_project_name(candidate_global, candidate_stack)


def _resolve_budget_candidates(
    repo_root: Path, stack_rel: Path
) -> list[_BudgetCandidate]:
    """S16.3 — pre-lock candidate resolution: translate every git-registered
    worktree into its OWN CIU-root/stack/network/compose-project identity,
    rendered against that candidate's OWN explicit ``ciu.env`` — never the
    caller's ambient environment. A candidate whose stack is genuinely
    absent on its checked-out branch is skipped (logged, not an error);
    everything else that cannot be resolved truthfully is a loud ``[S16.3]``
    failure, never evidence of an inactive instance.
    """
    from .workspace_env import WorkspaceEnvError, parse_workspace_env

    offset = _ciu_root_offset(repo_root)

    candidates: list[_BudgetCandidate] = []
    network_owners: dict[str, Path] = {}
    for entry in list_worktrees(repo_root):
        candidate_ciu_root = entry.path / offset
        candidate_stack = candidate_ciu_root / stack_rel
        if not candidate_stack.is_dir():
            print(
                f"[INFO] [S16.3] {entry.path} has no stack at {stack_rel} on "
                "its checked-out branch; not deployed, excluded from the "
                "worktree capacity count.",
                flush=True,
            )
            continue

        env_file = entry.path / "ciu.env"
        if not env_file.is_file():
            # A raw git worktree, never registered as a CIU instance.
            continue
        try:
            candidate_env = parse_workspace_env(env_file)
        except (OSError, WorkspaceEnvError) as exc:
            raise WorktreeError(
                f"[S16.3] could not read/parse {env_file}: {exc}"
            ) from exc
        network = candidate_env.get("DOCKER_NETWORK_INTERNAL", "")
        if not network:
            raise WorktreeError(
                f"[S16.3] {env_file} has no DOCKER_NETWORK_INTERNAL; "
                f"{entry.path} looks like a registered CIU instance whose "
                "deployment state cannot be truthfully counted."
            )
        if network in network_owners:
            raise WorktreeError(
                f"[S16.3] DOCKER_NETWORK_INTERNAL {network!r} is registered "
                f"to both {network_owners[network]} and {entry.path}; "
                "refusing to count worktree capacity against an ambiguous "
                "network."
            )
        network_owners[network] = entry.path

        try:
            candidate_global = config_model.render_global_chain(
                candidate_stack, candidate_ciu_root,
                write_rendered=False, environ=candidate_env,
            )
        except ValueError as exc:
            raise WorktreeError(
                f"[S16.3] could not render the global configuration for "
                f"candidate {candidate_stack}: {exc}"
            ) from exc
        try:
            project = _candidate_project(candidate_global, candidate_stack)
        except ValueError as exc:
            raise WorktreeError(
                f"[S16.3] could not derive the compose project for "
                f"candidate {candidate_stack}: {exc}"
            ) from exc

        candidates.append(_BudgetCandidate(
            worktree_path=entry.path, stack=candidate_stack,
            network=network, project=project,
        ))

    return candidates


def _candidate_deployed(candidate: _BudgetCandidate) -> bool:
    """S16.3 — true only when a container carrying *candidate*'s OWN EXACT
    compose-project label also lists *candidate*'s OWN network.

    Value-qualified, never a bare label-presence check: a P02 child
    container may list the reference network too, but it carries the
    CHILD's project label, never the reference's, so it can never satisfy
    the reference candidate's own query here.
    """
    try:
        result = procutil.docker(
            [
                "ps",
                "--filter", f"label=com.docker.compose.project={candidate.project}",
                "--format", "{{.Networks}}",
            ],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise WorktreeError(
            f"[S16.3] could not query Docker for candidate project "
            f"{candidate.project!r}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise WorktreeError(
            f"[S16.3] `docker ps` failed for candidate project "
            f"{candidate.project!r}: "
            f"{(result.stderr or result.stdout or '').strip()}"
        )
    for line in (result.stdout or "").splitlines():
        networks = {n.strip() for n in line.split(",") if n.strip()}
        if candidate.network in networks:
            return True
    return False


def _git_common_dir(repo_root: Path) -> Path:
    """The ``.git`` directory shared by every linked worktree of *repo_root*'s
    family (S16.3) — the S16.3 lock lives here so it is visible to, and
    shared by, every sibling worktree regardless of which one takes it."""
    res = _git(["rev-parse", "--git-common-dir"], repo_root)
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.3] `git rev-parse --git-common-dir` failed in {repo_root}: "
            f"{(res.stderr or res.stdout).strip()}"
        )
    out = (res.stdout or "").strip()
    common = Path(out)
    if not common.is_absolute():
        common = (Path(repo_root) / common).resolve()
    return common


@contextmanager
def worktree_budget_slot(
    repo_root: Path,
    cap: int | None,
    current_network: str,
    stack_rel: Path,
) -> Iterator[None]:
    """S16.3/CIU-24 — the locked count-and-start critical section.

    ``cap is None`` (no configured budget) yields immediately: no candidate
    resolution, no Docker call, no lock. Otherwise every candidate's identity
    is resolved BEFORE the family-wide flock is acquired — only the Docker
    queries, the count decision, and the caller's own compose executor
    (invoked INSIDE this context, at the ``yield``) run while the lock is
    held. The lock is released in a ``finally`` on every return or raise from
    that caller code, so a Compose failure leaves no lasting reservation.

    If *current_network* is already deployed, this yields even when the
    observed count is at or over *cap* — an already-running instance may be
    reconciled (e.g. re-``up``) after the policy is later lowered. Otherwise
    a count ``>= cap`` refuses before the caller's executor ever runs.
    """
    if cap is None:
        yield
        return

    repo_root = Path(repo_root).resolve()
    if stack_rel.is_absolute():
        raise WorktreeError(
            f"[S16.3] stack_rel must be relative to repo_root, got {stack_rel!r}"
        )

    candidates = _resolve_budget_candidates(repo_root, stack_rel)
    networks = {c.network for c in candidates}
    if current_network not in networks:
        raise WorktreeError(
            f"[S16.3] current network {current_network!r} is not among the "
            f"registered worktree instances' own DOCKER_NETWORK_INTERNAL "
            f"values ({sorted(networks)}); cannot count capacity for an "
            "unregistered instance."
        )

    lock_path = _git_common_dir(repo_root) / _BUDGET_LOCK_NAME
    lock_fh = open(lock_path, "a+")
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            deployed_count = 0
            current_deployed = False
            for candidate in candidates:
                is_deployed = _candidate_deployed(candidate)
                if candidate.network == current_network:
                    current_deployed = is_deployed
                elif is_deployed:
                    deployed_count += 1
            if not current_deployed and deployed_count >= cap:
                raise WorktreeError(
                    f"[S16.3] worktree capacity refused: {deployed_count} "
                    f"other instance(s) already deployed >= cap {cap} "
                    f"(current network {current_network!r} not yet deployed)"
                )
            yield
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    finally:
        lock_fh.close()
