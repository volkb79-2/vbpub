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
import json
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config_model
from . import procutil
from .config_constants import GLOBAL_CONFIG_WORKTREE_OVERRIDES

DEFAULT_WORKTREE_DIR = ".worktrees"
WORKTREE_INSTANCE_RECORD = "ciu.worktree-instance.json"
WORKTREE_INSTANCE_SCHEMA_VERSION = 1
WORKTREE_LIFECYCLE_STATES = frozenset(
    {"allocating", "ready", "recovery-required"}
)
WORKTREE_RECOVERY_STATUSES = frozenset(
    {"checkout-incomplete", "env-generation-failed", "runtime-collision"}
)
_ALLOCATION_LOCK_NAME = "ciu-worktree-allocation.lock"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

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


@dataclass(frozen=True)
class WorktreeInstanceRecord:
    """Schema-v1 durable identity for one CIU-managed linked worktree.

    The record deliberately contains no current Git revision (derived during
    inspection) and no secret-bearing values.  It is written at the target
    CIU root, which can be below the Git worktree root in a monorepo.
    """

    logical_name: str
    display_name: str
    branch: str
    git_worktree_path: Path
    ciu_root_offset: Path
    created_at_utc: str
    base_ref: str
    state: str
    instance_id: str | None = None
    network: str | None = None
    recovery_status: str | None = None
    schema_version: int = WORKTREE_INSTANCE_SCHEMA_VERSION

    @property
    def ciu_root(self) -> Path:
        return self.git_worktree_path / self.ciu_root_offset

    @property
    def record_path(self) -> Path:
        return self.ciu_root / WORKTREE_INSTANCE_RECORD

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "logical_name": self.logical_name,
            "display_name": self.display_name,
            "branch": self.branch,
            "git_worktree_path": str(self.git_worktree_path),
            "ciu_root_offset": str(self.ciu_root_offset),
            "created_at_utc": self.created_at_utc,
            "base_ref": self.base_ref,
            "state": self.state,
            "runtime": {
                "instance_id": self.instance_id,
                "network": self.network,
            },
            "recovery_status": self.recovery_status,
        }


def _validate_name(value: str, *, label: str) -> str:
    if not _NAME_RE.fullmatch(value):
        raise WorktreeError(
            f"[S16] invalid {label} {value!r}: expected one non-hidden Git-safe "
            "component containing only letters, digits, '.', '_' or '-'"
        )
    return value


def _record_from_dict(raw: Any, path: Path) -> WorktreeInstanceRecord:
    if not isinstance(raw, dict):
        raise WorktreeError(f"[S16] {path} must contain one JSON object")
    required = {
        "schema_version", "logical_name", "display_name", "branch",
        "git_worktree_path", "ciu_root_offset", "created_at_utc", "base_ref",
        "state", "runtime", "recovery_status",
    }
    if set(raw) != required:
        missing = sorted(required - set(raw))
        unknown = sorted(set(raw) - required)
        raise WorktreeError(
            f"[S16] malformed {path}: missing={missing}, unknown={unknown}"
        )
    if raw["schema_version"] != WORKTREE_INSTANCE_SCHEMA_VERSION:
        raise WorktreeError(
            f"[S16] unsupported worktree record schema_version "
            f"{raw['schema_version']!r} in {path}"
        )
    runtime = raw["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {"instance_id", "network"}:
        raise WorktreeError(f"[S16] malformed runtime identity in {path}")
    scalar_strings = (
        "logical_name", "display_name", "branch", "git_worktree_path",
        "ciu_root_offset", "created_at_utc", "base_ref", "state",
    )
    if any(not isinstance(raw[key], str) or not raw[key] for key in scalar_strings):
        raise WorktreeError(f"[S16] malformed required string field in {path}")
    _validate_name(raw["logical_name"], label="logical name")
    _validate_name(raw["display_name"], label="display name")
    state = raw["state"]
    if state not in WORKTREE_LIFECYCLE_STATES:
        raise WorktreeError(f"[S16] unknown lifecycle state {state!r} in {path}")
    recovery = raw["recovery_status"]
    if recovery is not None and recovery not in WORKTREE_RECOVERY_STATUSES:
        raise WorktreeError(f"[S16] unknown recovery status {recovery!r} in {path}")
    for key, value in runtime.items():
        if value is not None and (not isinstance(value, str) or not value):
            raise WorktreeError(f"[S16] malformed runtime.{key} in {path}")
    if state == "ready" and (
        not runtime["instance_id"] or not runtime["network"] or recovery is not None
    ):
        raise WorktreeError(f"[S16] ready record lacks a closed runtime identity in {path}")
    if state != "recovery-required" and recovery is not None:
        raise WorktreeError(f"[S16] {state!r} record carries recovery_status in {path}")
    offset = Path(raw["ciu_root_offset"])
    if offset.is_absolute() or ".." in offset.parts:
        raise WorktreeError(f"[S16] unsafe ciu_root_offset in {path}: {offset}")
    git_path = Path(raw["git_worktree_path"])
    if not git_path.is_absolute():
        raise WorktreeError(f"[S16] git_worktree_path is not absolute in {path}")
    return WorktreeInstanceRecord(
        logical_name=raw["logical_name"], display_name=raw["display_name"],
        branch=raw["branch"], git_worktree_path=git_path,
        ciu_root_offset=offset, created_at_utc=raw["created_at_utc"],
        base_ref=raw["base_ref"], state=state,
        instance_id=runtime["instance_id"], network=runtime["network"],
        recovery_status=recovery,
    )


def read_instance_record(path: Path) -> WorktreeInstanceRecord:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorktreeError(f"[S16] could not read valid instance record {path}: {exc}") from exc
    return _record_from_dict(raw, path)


def _write_instance_record(record: WorktreeInstanceRecord) -> None:
    """Atomically replace one record; a crash yields old-or-new, never partial."""
    path = record.record_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n"
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:  # pragma: no cover - best-effort cleanup after write failure
            pass
        raise WorktreeError(f"[S16] could not atomically write {path}: {exc}") from exc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generated_worktree_name(prefix: str, feature: str, *, now: datetime | None = None) -> str:
    """Return the unsuffixed human-sortable generated name (D-005)."""
    _validate_name(prefix, label="generated prefix")
    _validate_name(feature, label="feature description")
    instant = now or _utc_now()
    if instant.tzinfo is None:
        raise WorktreeError("[S16] generated-name clock must be timezone-aware")
    stamp = instant.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}-{stamp}-{feature}"


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
    worktree's own global worktree overlay (see
    :func:`parse_shared_infra_config`,
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


def _config_string_list(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise WorktreeError(f"[S16.1] {label} must be a non-empty string array")
    items = tuple(item.strip() for item in value)
    if len(set(items)) != len(items):
        raise WorktreeError(f"[S16.1] {label} contains a duplicate item")
    return items


def parse_shared_infra_config(global_config: Mapping[str, Any]) -> SharedInfraIntent | None:
    """Read the closed S16.1 intent from the rendered worktree config layer."""
    ciu = global_config.get("ciu", {})
    if not isinstance(ciu, dict):
        raise WorktreeError("[S16.1] [ciu] must be a table")
    instance = ciu.get("instance", {})
    if not isinstance(instance, dict):
        raise WorktreeError("[S16.1] [ciu.instance] must be a table")
    raw = instance.get("shared_infra")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorktreeError("[S16.1] [ciu.instance.shared_infra] must be a table")
    required = {"ref_path", "network", "services", "ref_projects"}
    if set(raw) != required:
        raise WorktreeError(
            "[S16.1] malformed [ciu.instance.shared_infra]: "
            f"missing={sorted(required - set(raw))}, "
            f"unknown={sorted(set(raw) - required)}"
        )
    if not isinstance(raw["ref_path"], str) or not raw["ref_path"]:
        raise WorktreeError("[S16.1] shared_infra.ref_path must be a non-empty string")
    if not isinstance(raw["network"], str) or not raw["network"]:
        raise WorktreeError("[S16.1] shared_infra.network must be a non-empty string")
    return SharedInfraIntent(
        ref_path=Path(raw["ref_path"]), network=raw["network"],
        services=_config_string_list(raw["services"], label="shared_infra.services"),
        ref_projects=_config_string_list(
            raw["ref_projects"], label="shared_infra.ref_projects"
        ),
    )


def _worktree_overlay_text(
    profile: str | None, shared_infra: SharedInfraIntent | None
) -> str | None:
    """Render CIU-owned initial local settings as a sparse TOML template."""
    profiles = _split_unique_list(profile, label="--profile") if profile else ()
    if not profiles and shared_infra is None:
        return None
    lines = [
        "# Worktree-local sparse global override (S3.1b / S16).",
        "# Durable configuration: preserved by `ciu clean` and `ciu env generate`.",
        "[ciu.instance]",
    ]
    if profiles:
        lines.append(f"service_profiles = {json.dumps(list(profiles))}")
    if shared_infra is not None:
        lines.extend([
            "",
            "[ciu.instance.shared_infra]",
            f"ref_path = {json.dumps(str(shared_infra.ref_path))}",
            f"network = {json.dumps(shared_infra.network)}",
            f"services = {json.dumps(list(shared_infra.services))}",
            f"ref_projects = {json.dumps(list(shared_infra.ref_projects))}",
        ])
    return "\n".join(lines) + "\n"


def _write_worktree_overlay(
    ciu_root: Path, profile: str | None, shared_infra: SharedInfraIntent | None
) -> None:
    payload = _worktree_overlay_text(profile, shared_infra)
    if payload is None:
        return
    path = ciu_root / GLOBAL_CONFIG_WORKTREE_OVERRIDES
    if path.exists():
        raise WorktreeError(
            f"[S16] refusing to overwrite existing worktree override {path}"
        )
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:  # pragma: no cover - best-effort cleanup after write failure
            pass
        raise WorktreeError(f"[S16] could not write {path}: {exc}") from exc


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

    managed_ref = find_instance_record(repo_root, shared_infra)
    ref = (
        find_worktree(repo_root, str(managed_ref.git_worktree_path))
        if managed_ref is not None else find_worktree(repo_root, shared_infra)
    )
    if ref is None:
        raise WorktreeError(
            f"[S16.1] --shared-infra {shared_infra!r} does not resolve to a "
            f"registered worktree under {repo_root}. `ciu worktree list` shows "
            "what exists."
        )

    ref_env_file = ref.path / _ciu_root_offset(repo_root) / "ciu.env"
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


def _generate_env_in(worktree: Path, *, identity_only: bool = False) -> int:
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
    if identity_only:
        argv.append("--identity-only")
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


@contextmanager
def _allocation_lock(repo_root: Path) -> Iterator[None]:
    lock_path = _git_common_dir(repo_root) / _ALLOCATION_LOCK_NAME
    try:
        lock_fh = open(lock_path, "a+")
    except OSError as exc:
        raise WorktreeError(f"[S16] could not open allocation lock {lock_path}: {exc}") from exc
    try:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()


def _record_ignore_pattern(offset: Path) -> str:
    parts = [] if offset == Path(".") else list(offset.parts)
    return "/" + "/".join([*parts, WORKTREE_INSTANCE_RECORD])


def _ensure_record_is_excluded(repo_root: Path, offset: Path) -> None:
    """Locally exclude CIU's identity record for every sibling checkout."""
    exclude = _git_common_dir(repo_root) / "info" / "exclude"
    pattern = _record_ignore_pattern(offset)
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8").splitlines() if exclude.exists() else []
        if pattern not in existing:
            with exclude.open("a", encoding="utf-8") as fh:
                if existing and exclude.stat().st_size:
                    fh.write("\n" if not exclude.read_text(encoding="utf-8").endswith("\n") else "")
                fh.write(pattern + "\n")
    except OSError as exc:
        raise WorktreeError(
            f"[S16] could not exclude machine identity record via {exclude}: {exc}"
        ) from exc


def list_instance_records(repo_root: Path) -> list[WorktreeInstanceRecord]:
    """Read and cross-check every managed record in one Git family."""
    offset = _ciu_root_offset(repo_root)
    records: list[WorktreeInstanceRecord] = []
    logical_names: set[str] = set()
    for wt in list_worktrees(repo_root):
        path = wt.path / offset / WORKTREE_INSTANCE_RECORD
        if not path.is_file():
            continue
        record = read_instance_record(path)
        if record.git_worktree_path.resolve() != wt.path.resolve():
            raise WorktreeError(
                f"[S16] {path} claims Git path {record.git_worktree_path}, "
                f"but Git registers {wt.path}"
            )
        if record.ciu_root_offset != offset:
            raise WorktreeError(
                f"[S16] {path} claims CIU-root offset {record.ciu_root_offset}, "
                f"but this family derives {offset}"
            )
        if record.branch != wt.branch:
            raise WorktreeError(
                f"[S16] {path} claims branch {record.branch!r}, "
                f"but Git registers {wt.branch!r}"
            )
        if record.logical_name in logical_names:
            raise WorktreeError(
                f"[S16] duplicate logical worktree identity {record.logical_name!r} "
                "within one Git family"
            )
        logical_names.add(record.logical_name)
        records.append(record)
    return records


def find_instance_record(repo_root: Path, logical_name: str) -> WorktreeInstanceRecord | None:
    matches = [
        record for record in list_instance_records(repo_root)
        if record.logical_name == logical_name
    ]
    if len(matches) > 1:  # pragma: no cover - list_instance_records refuses first
        raise WorktreeError(f"[S16] ambiguous logical identity {logical_name!r}")
    return matches[0] if matches else None


def _branch_exists(repo_root: Path, branch: str) -> bool:
    return _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], repo_root).returncode == 0


def _runtime_identity(ciu_root: Path) -> tuple[str, str]:
    from .workspace_env import parse_workspace_env

    env_path = ciu_root / "ciu.env"
    try:
        values = parse_workspace_env(env_path)
    except (OSError, ValueError) as exc:
        raise WorktreeError(f"[S16] could not read generated runtime identity {env_path}: {exc}") from exc
    instance_id = values.get("INSTANCE_ID", "")
    network = values.get("DOCKER_NETWORK_INTERNAL", "")
    if not instance_id or not network:
        raise WorktreeError(
            f"[S16] {env_path} lacks INSTANCE_ID or DOCKER_NETWORK_INTERNAL"
        )
    return instance_id, network


def _check_runtime_collision(
    repo_root: Path, record: WorktreeInstanceRecord, instance_id: str, network: str
) -> None:
    for other in list_instance_records(repo_root):
        if other.logical_name == record.logical_name:
            continue
        if other.instance_id == instance_id:
            raise WorktreeError(
                f"[S16] runtime INSTANCE_ID {instance_id!r} already belongs to "
                f"logical instance {other.logical_name!r}"
            )
        if other.network == network:
            raise WorktreeError(
                f"[S16] runtime network {network!r} already belongs to "
                f"logical instance {other.logical_name!r}"
            )


def _docker_network_exists(network: str) -> bool:
    """Check one exact host network name; Docker absence means local-only CIU."""
    try:
        result = procutil.docker(
            ["network", "ls", "--filter", f"name=^{network}$", "--format", "{{.Name}}"],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    if result.returncode != 0:
        raise WorktreeError(
            f"[S16] could not verify host runtime-network uniqueness for "
            f"{network!r}: {(result.stderr or result.stdout or '').strip()}"
        )
    return network in {line.strip() for line in (result.stdout or "").splitlines()}


def _mark_recovery(record: WorktreeInstanceRecord, status: str) -> WorktreeInstanceRecord:
    failed = replace(record, state="recovery-required", recovery_status=status)
    _write_instance_record(failed)
    return failed


def _finish_allocation(
    repo_root: Path,
    record: WorktreeInstanceRecord,
    *,
    checkout_required: bool,
    allow_existing_network: bool = False,
) -> WorktreeInstanceRecord:
    if checkout_required:
        checkout = _git(["reset", "--hard", record.base_ref], record.git_worktree_path)
        if checkout.returncode != 0:
            _mark_recovery(record, "checkout-incomplete")
            raise WorktreeError(
                f"[S16] allocated {record.git_worktree_path}, but checkout of "
                f"{record.base_ref!r} failed: {(checkout.stderr or checkout.stdout).strip()}. "
                f"Resume with `ciu worktree ensure {record.logical_name}`."
            )

    rc = _generate_env_in(record.ciu_root, identity_only=True)
    if rc != 0:
        _mark_recovery(record, "env-generation-failed")
        raise WorktreeError(
            f"[S16] worktree exists at {record.git_worktree_path}, but "
            f"`ciu env generate` failed in {record.ciu_root} (exit {rc}). "
            f"Resume with `ciu worktree ensure {record.logical_name}`."
        )
    try:
        instance_id, network = _runtime_identity(record.ciu_root)
        _check_runtime_collision(repo_root, record, instance_id, network)
        if not allow_existing_network and _docker_network_exists(network):
            raise WorktreeError(
                f"[S16] host runtime network {network!r} already exists before "
                "this allocation; refusing a possible independent-clone collision"
            )
    except WorktreeError:
        _mark_recovery(record, "runtime-collision")
        raise
    allocating = replace(
        record, state="allocating", instance_id=instance_id, network=network,
        recovery_status=None,
    )
    _write_instance_record(allocating)

    rc = _generate_env_in(record.ciu_root)
    if rc != 0:
        _mark_recovery(allocating, "env-generation-failed")
        raise WorktreeError(
            f"[S16] worktree exists at {record.git_worktree_path}, but full "
            f"`ciu env generate` failed in {record.ciu_root} (exit {rc}). "
            f"Resume with `ciu worktree ensure {record.logical_name}`."
        )
    confirmed_id, confirmed_network = _runtime_identity(record.ciu_root)
    if (confirmed_id, confirmed_network) != (instance_id, network):
        _mark_recovery(allocating, "runtime-collision")
        raise WorktreeError(
            "[S16] runtime identity changed between identity-only and full env generation"
        )
    ready = replace(
        allocating, state="ready", instance_id=instance_id, network=network,
        recovery_status=None,
    )
    _write_instance_record(ready)
    return ready


def create(
    repo_root: Path,
    logical_name: str,
    *,
    base: str = "main",
    profile: str | None = None,
    worktree_dir: str = DEFAULT_WORKTREE_DIR,
    display_name: str | None = None,
    prefix: str | None = None,
    feature: str | None = None,
    branch: str | None = None,
    path: Path | None = None,
    shared_infra: str | None = None,
    shared_infra_services: str | None = None,
    shared_infra_ref_projects: str | None = None,
) -> WorktreeInstanceRecord:
    """Create a new managed worktree after family-wide collision admission.

    Creates ``<primary-git-root>/<worktree_dir>/<display>`` off
    *base*, then generates that checkout's own ``ciu.env`` — which is what gives
    it a distinct ``INSTANCE_ID``, network and container prefix (S2).
    Worktree-local configuration is persisted separately in
    ``ciu.global.worktree.toml.j2`` and survives env regeneration and clean.

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
    it) BEFORE any side effect; `create` records the resolved intent into the
    new worktree's own local config overlay but never joins anything itself — the actual
    ``docker network connect`` calls happen later, at ``ciu up`` time, in the
    new worktree's own process (see :func:`connect_shared_infra_after_up`).
    This instance keeps its OWN ``DOCKER_NETWORK_INTERNAL`` throughout; only
    the declared diverging services later gain a SECOND membership.
    """
    repo_root = Path(repo_root).resolve()
    _validate_name(logical_name, label="logical name")
    if bool(prefix) != bool(feature):
        raise WorktreeError("[S16] --prefix and --feature must be supplied together")
    if display_name is not None and prefix is not None:
        raise WorktreeError("[S16] explicit display name conflicts with generated naming")
    if display_name is not None:
        _validate_name(display_name, label="display name")
    if branch is not None and not branch:
        raise WorktreeError("[S16] --branch cannot be empty")

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

    offset = _ciu_root_offset(repo_root)
    primary = primary_worktree_root(repo_root).resolve()
    instant = _utc_now()
    generated_base = (
        generated_worktree_name(prefix, feature, now=instant)
        if prefix is not None and feature is not None else None
    )

    with _allocation_lock(repo_root):
        if find_instance_record(repo_root, logical_name) is not None:
            raise WorktreeError(
                f"[S16] logical worktree identity {logical_name!r} already exists; "
                "use `ciu worktree ensure` to resume it"
            )
        _ensure_record_is_excluded(repo_root, offset)

        suffix = 1
        while True:
            candidate_display = display_name or generated_base or logical_name
            if generated_base is not None and suffix > 1:
                candidate_display = f"{generated_base}-{suffix}"
            _validate_name(candidate_display, label="display name")
            candidate_branch = branch or candidate_display
            target = (
                (path if path.is_absolute() else primary / path)
                if path is not None else primary / worktree_dir / candidate_display
            ).resolve()
            occupied = (
                target.exists() or _branch_exists(repo_root, candidate_branch)
                or any(wt.path.resolve() == target for wt in list_worktrees(repo_root))
            )
            if generated_base is not None and branch is None and path is None and occupied:
                suffix += 1
                continue
            if occupied:
                raise WorktreeError(
                    f"[S16] requested branch/path is occupied: branch "
                    f"{candidate_branch!r}, path {target}"
                )
            break

        if generated_base is not None and (
            candidate_branch != candidate_display or target.name != candidate_display
        ):
            raise WorktreeError(
                "[S16] generated branch and worktree directory basename must be identical"
            )

        res = _git(
            ["worktree", "add", "--no-checkout", "-b", candidate_branch, str(target), base],
            repo_root,
        )
        if res.returncode != 0:
            raise WorktreeError(
                f"[S16] `git worktree add` failed: "
                f"{(res.stderr or res.stdout).strip()}"
            )

        record = WorktreeInstanceRecord(
            logical_name=logical_name,
            display_name=candidate_display,
            branch=candidate_branch,
            git_worktree_path=target,
            ciu_root_offset=offset,
            created_at_utc=instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            base_ref=base,
            state="allocating",
        )
        _write_instance_record(record)
        try:
            _write_worktree_overlay(record.ciu_root, profile, shared_infra_intent)
        except WorktreeError:
            _mark_recovery(record, "checkout-incomplete")
            raise
        return _finish_allocation(repo_root, record, checkout_required=True)


def add(
    repo_root: Path,
    name: str,
    **kwargs: Any,
) -> Path:
    """Backward-compatible human create spelling; returns the checkout path."""
    return create(repo_root, name, **kwargs).git_worktree_path


def ensure(
    repo_root: Path,
    logical_name: str,
    **create_kwargs: Any,
) -> WorktreeInstanceRecord:
    """Return an exact ready match or resume one CIU-owned partial allocation."""
    _validate_name(logical_name, label="logical name")
    with _allocation_lock(repo_root):
        record = find_instance_record(repo_root, logical_name)
        if record is not None:
            requested_prefix = create_kwargs.get("prefix")
            requested_feature = create_kwargs.get("feature")
            if bool(requested_prefix) != bool(requested_feature):
                raise WorktreeError("[S16] --prefix and --feature must be supplied together")
            if requested_prefix and requested_feature:
                created = datetime.fromisoformat(record.created_at_utc.replace("Z", "+00:00"))
                expected_base = generated_worktree_name(
                    requested_prefix, requested_feature, now=created
                )
                if not (
                    record.display_name == expected_base
                    or re.fullmatch(
                        re.escape(expected_base) + r"-(?:[2-9]|[1-9][0-9]+)",
                        record.display_name,
                    )
                ):
                    raise WorktreeError(
                        f"[S16] ensure generated-name mismatch: record has "
                        f"{record.display_name!r}, requested base is {expected_base!r}"
                    )
            constraints = {
                "display_name": create_kwargs.get("display_name"),
                "branch": create_kwargs.get("branch"),
                "git_worktree_path": create_kwargs.get("path"),
            }
            for field, expected in constraints.items():
                if expected is None:
                    continue
                actual = getattr(record, field)
                if field == "git_worktree_path":
                    requested_path = Path(expected)
                    expected = (
                        requested_path if requested_path.is_absolute()
                        else primary_worktree_root(repo_root) / requested_path
                    ).resolve()
                    actual = Path(actual).resolve()
                if actual != expected:
                    raise WorktreeError(
                        f"[S16] ensure mismatch for {field}: record has "
                        f"{actual!r}, caller requested {expected!r}"
                    )
            if record.state == "ready":
                return record
            checkout_required = record.recovery_status in (None, "checkout-incomplete")
            return _finish_allocation(
                repo_root, record, checkout_required=checkout_required,
                allow_existing_network=record.recovery_status == "env-generation-failed",
            )
    return create(repo_root, logical_name, **create_kwargs)


def adopt(
    repo_root: Path,
    logical_name: str,
    target: str,
    *,
    profile: str | None = None,
    shared_infra: str | None = None,
    shared_infra_services: str | None = None,
    shared_infra_ref_projects: str | None = None,
) -> WorktreeInstanceRecord:
    """Explicitly take ownership of one registered, unmanaged linked checkout."""
    repo_root = Path(repo_root).resolve()
    _validate_name(logical_name, label="logical name")
    shared_infra_intent: SharedInfraIntent | None = None
    if shared_infra is not None or shared_infra_services is not None or shared_infra_ref_projects is not None:
        if not (shared_infra and shared_infra_services and shared_infra_ref_projects and profile):
            raise WorktreeError(
                "[S16.1] adopt shared-infra options and --profile are all-or-nothing"
            )
        shared_infra_intent = _preflight_shared_infra_for_add(
            repo_root, shared_infra=shared_infra,
            shared_infra_services=shared_infra_services,
            shared_infra_ref_projects=shared_infra_ref_projects,
        )
    with _allocation_lock(repo_root):
        if find_instance_record(repo_root, logical_name) is not None:
            raise WorktreeError(f"[S16] logical identity {logical_name!r} is already managed")
        wt = find_worktree(repo_root, target)
        if wt is None:
            raise WorktreeError(f"[S16] {target!r} is not a registered worktree")
        if wt.is_primary or wt.branch in ("(detached)", "(unknown)"):
            raise WorktreeError("[S16] adopt requires one non-primary attached-branch worktree")
        offset = _ciu_root_offset(repo_root)
        record_path = wt.path / offset / WORKTREE_INSTANCE_RECORD
        if record_path.exists():
            raise WorktreeError(f"[S16] {wt.path} already has a managed instance record")
        _ensure_record_is_excluded(repo_root, offset)
        head = _git(["rev-parse", "HEAD"], wt.path)
        if head.returncode != 0:
            raise WorktreeError(f"[S16] cannot derive adopted checkout HEAD: {head.stderr.strip()}")
        record = WorktreeInstanceRecord(
            logical_name=logical_name, display_name=wt.path.name, branch=wt.branch,
            git_worktree_path=wt.path.resolve(), ciu_root_offset=offset,
            created_at_utc=_utc_now().isoformat().replace("+00:00", "Z"),
            base_ref=head.stdout.strip(), state="allocating",
        )
        _write_instance_record(record)
        if (record.ciu_root / GLOBAL_CONFIG_WORKTREE_OVERRIDES).exists() and (
            profile or shared_infra_intent is not None
        ):
            _mark_recovery(record, "env-generation-failed")
            raise WorktreeError(
                f"[S16] {record.ciu_root} already has a worktree override; "
                "refusing to replace it with adopt flags"
            )
        _write_worktree_overlay(record.ciu_root, profile, shared_infra_intent)
        return _finish_allocation(
            repo_root, record, checkout_required=False, allow_existing_network=True
        )


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
    managed = find_instance_record(repo_root, name)
    wt = (
        find_worktree(repo_root, str(managed.git_worktree_path))
        if managed is not None else find_worktree(repo_root, name)
    )
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

    ciu_root = managed.ciu_root if managed is not None else wt.path / _ciu_root_offset(repo_root)
    rc = _clean_in(ciu_root, yes=yes)
    if rc != 0 and not force:
        raise WorktreeError(
            f"[S16] `ciu clean` failed (exit {rc}) in {ciu_root}; NOT removing the "
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

    ref_env_file = ref.path / _ciu_root_offset(repo_root) / "ciu.env"
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
