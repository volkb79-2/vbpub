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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config_model
from . import procutil
from .config_constants import GLOBAL_CONFIG_INSTANCE_OVERRIDES
from .paths import to_physical_path
# CIU-85: the identity half of `_CIU_IDENTITY_ENV_KEYS` (below) is DERIVED
# from this, the same canonical fact->env-name table `workspace_env.py`'s
# own `LEGACY_IDENTITY_ENV_KEYS` derives from, rather than hand-maintained
# as a second, independently-drifting list. `workspace_env.py` never
# imports `worktree.py` (verified — no cycle), so this is a safe top-level
# import, unlike the rest of this module's `workspace_env` uses, which stay
# local/deferred by established convention.
from .workspace_env import GENERATED_FACT_ENV_KEYS

DEFAULT_WORKTREE_DIR = ".worktrees"
WORKTREE_INSTANCE_RECORD = "ciu.worktree-instance.json"
# S16.9 (CIU-25 substrate): v2 == "this record carries a `lease` field".
# v1 records (no lease concept at all) stay readable forever and are NEVER
# rewritten by a read; only an explicit lease mutation writes v2.
WORKTREE_INSTANCE_SCHEMA_VERSION = 2
WORKTREE_INSTANCE_BASE_SCHEMA_VERSION = 1
WORKTREE_INSTANCE_SCHEMA_VERSIONS = frozenset({1, 2})
WORKTREE_LEASE_MODES = frozenset({"held", "perpetual"})
WORKTREE_LEASE_KEYS = frozenset(
    {"holder", "acquired_at_utc", "renewed_at_utc", "expires_at_utc", "mode"}
)
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
class WorktreeLease:
    """S16.9 — one EXPLICIT ownership lease on a managed worktree instance.

    CIU-25's whole point: staleness is never inferred from age, basename or a
    missing process. It is DECLARED, by this record, or it is not known. The
    two modes are the closed vocabulary :data:`WORKTREE_LEASE_MODES`:

    ``held``
        a bounded claim — ``expires_at_utc`` is REQUIRED and is the only fact
        a future reap may treat as "the operator's claim has lapsed".
    ``perpetual``
        an explicit, unbounded claim — ``expires_at_utc`` is FORBIDDEN (must
        be ``null``). A perpetual lease can never lapse; it is the operator
        saying "this instance is long-lived on purpose", which the backlog
        entry names as the exact case an age heuristic gets wrong.

    Every timestamp is ISO-8601 with an EXPLICIT UTC offset, written in
    :func:`_utc_stamp`'s ``...Z`` form (the same form ``created_at_utc``
    already uses). A naive, offset-less timestamp is a refusal, never a
    lenient local-time parse — a lease whose expiry is ambiguous by up to a
    day is worse than no lease at all when a destructive verb reads it.
    """

    holder: str
    acquired_at_utc: str
    renewed_at_utc: str
    expires_at_utc: str | None
    mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "holder": self.holder,
            "acquired_at_utc": self.acquired_at_utc,
            "renewed_at_utc": self.renewed_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class WorktreeInstanceRecord:
    """Durable identity for one CIU-managed linked worktree (schema v1/v2).

    The record deliberately contains no current Git revision (derived during
    inspection) and no secret-bearing values.  It is written at the target
    CIU root, which can be below the Git worktree root in a monorepo.

    ``schema_version`` is 1 until an explicit lease operation touches this
    record; from then on it is 2 and the serialized form carries a ``lease``
    key (``null`` after a release). A v1 record read from disk is a v1 record
    in memory with ``lease=None`` — reading never upgrades it (S16.9).
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
    lease: WorktreeLease | None = None
    schema_version: int = WORKTREE_INSTANCE_BASE_SCHEMA_VERSION

    @property
    def ciu_root(self) -> Path:
        return self.git_worktree_path / self.ciu_root_offset

    @property
    def record_path(self) -> Path:
        return self.ciu_root / WORKTREE_INSTANCE_RECORD

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
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
        # A v1 record serializes EXACTLY as it always did — no `lease` key at
        # all. That is what makes "a read never rewrites the record" a
        # structural property rather than a promise: there is no v2 shape to
        # accidentally emit until a lease operation has set schema_version.
        if self.schema_version >= 2:
            doc["lease"] = self.lease.to_dict() if self.lease is not None else None
        return doc


def _validate_name(value: str, *, label: str) -> str:
    if not _NAME_RE.fullmatch(value):
        raise WorktreeError(
            f"[S16] invalid {label} {value!r}: expected one non-hidden Git-safe "
            "component containing only letters, digits, '.', '_' or '-'"
        )
    return value


def _parse_utc_timestamp(value: Any, *, label: str, path: Path) -> datetime:
    """One ISO-8601 instant that MUST carry an explicit UTC offset (S16.9).

    ``datetime.fromisoformat`` happily returns a NAIVE datetime for
    ``"2026-08-25T12:00:00"``; comparing that against an aware ``now`` raises,
    and "fixing" it by assuming local time would make a lease expire at a
    time nobody wrote down. So offset-less input is refused here, at the
    parse boundary, rather than anywhere a comparison happens.
    """
    if not isinstance(value, str) or not value:
        raise WorktreeError(f"[S16.9] malformed lease {label} in {path}: {value!r}")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorktreeError(
            f"[S16.9] lease {label} in {path} is not an ISO-8601 instant: "
            f"{value!r} ({exc})"
        ) from exc
    if parsed.tzinfo is None:
        raise WorktreeError(
            f"[S16.9] lease {label} in {path} has no UTC offset: {value!r} — "
            "a naive timestamp is refused, never parsed as local time"
        )
    return parsed


def _lease_from_dict(raw: Any, path: Path) -> WorktreeLease:
    if not isinstance(raw, dict) or set(raw) != set(WORKTREE_LEASE_KEYS):
        raise WorktreeError(
            f"[S16.9] malformed lease in {path}: expected an object with keys "
            f"{sorted(WORKTREE_LEASE_KEYS)}"
        )
    holder = raw["holder"]
    if not isinstance(holder, str) or not holder:
        raise WorktreeError(f"[S16.9] malformed lease holder in {path}: {holder!r}")
    mode = raw["mode"]
    if mode not in WORKTREE_LEASE_MODES:
        raise WorktreeError(
            f"[S16.9] unknown lease mode {mode!r} in {path}; closed vocabulary: "
            f"{', '.join(sorted(WORKTREE_LEASE_MODES))}"
        )
    _parse_utc_timestamp(raw["acquired_at_utc"], label="acquired_at_utc", path=path)
    _parse_utc_timestamp(raw["renewed_at_utc"], label="renewed_at_utc", path=path)
    expires = raw["expires_at_utc"]
    if mode == "held":
        if expires is None:
            raise WorktreeError(
                f"[S16.9] lease mode 'held' in {path} requires expires_at_utc; "
                "an unbounded claim must say so explicitly (mode 'perpetual')"
            )
        _parse_utc_timestamp(expires, label="expires_at_utc", path=path)
    elif expires is not None:
        raise WorktreeError(
            f"[S16.9] lease mode 'perpetual' in {path} forbids expires_at_utc, "
            f"got {expires!r} — a perpetual lease can never lapse"
        )
    return WorktreeLease(
        holder=holder, acquired_at_utc=raw["acquired_at_utc"],
        renewed_at_utc=raw["renewed_at_utc"], expires_at_utc=expires, mode=mode,
    )


def _record_from_dict(raw: Any, path: Path) -> WorktreeInstanceRecord:
    if not isinstance(raw, dict):
        raise WorktreeError(f"[S16] {path} must contain one JSON object")
    required = {
        "schema_version", "logical_name", "display_name", "branch",
        "git_worktree_path", "ciu_root_offset", "created_at_utc", "base_ref",
        "state", "runtime", "recovery_status",
    }
    # S16.9: the key set is SCHEMA-DEPENDENT — a v2 record must carry `lease`,
    # a v1 record must NOT. Anything else is checked against the v1 set and
    # then rejected by the version check below.
    declared_version = raw.get("schema_version")
    if declared_version == 2:
        required = required | {"lease"}
    if set(raw) != required:
        missing = sorted(required - set(raw))
        unknown = sorted(set(raw) - required)
        raise WorktreeError(
            f"[S16] malformed {path} (schema_version {declared_version!r}): "
            f"missing={missing}, unknown={unknown}"
        )
    if declared_version not in WORKTREE_INSTANCE_SCHEMA_VERSIONS:
        raise WorktreeError(
            f"[S16] unsupported worktree record schema_version "
            f"{raw['schema_version']!r} in {path}"
        )
    lease = None
    if declared_version == 2 and raw["lease"] is not None:
        lease = _lease_from_dict(raw["lease"], path)
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
        recovery_status=recovery, lease=lease,
        schema_version=declared_version,
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
        except OSError:
            pass
        raise WorktreeError(f"[S16] could not atomically write {path}: {exc}") from exc


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_stamp(instant: datetime) -> str:
    """The record's ONE timestamp spelling — exactly what ``created_at_utc``
    has always been written as (``...Z``), so a lease timestamp and an
    allocation timestamp in the same file are the same format."""
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# S16.9 — explicit ownership lease (CIU-25 substrate, NOT the reap verb)
# ---------------------------------------------------------------------------


def _host_identity() -> str:
    """This machine's name for a lease HOLDER string.

    Deliberately not a new identity mechanism: this calls
    ``workspace_env.detect_devcontainer_name()`` (CIU-59 — factored from
    four independently-duplicated call sites, this one included), the
    devcontainer name when there is one, the container/host ``HOSTNAME``
    otherwise, and records into ``ciu.env`` as ``DEVCONTAINER_NAME``. Unlike
    the INSTANCE_ID half of the holder string, this one is deliberately
    AMBIENT: it names the machine holding the lease right now, not the
    workspace being leased. The trailing ``or "unknown-host"`` fallback is
    this call site's OWN addition, not shared by the other three — a lease
    holder string must never be empty.
    """
    from .workspace_env import detect_devcontainer_name

    return detect_devcontainer_name() or "unknown-host"


def lease_holder(instance_id: str) -> str:
    """``ciu@<hostname>:<INSTANCE_ID>`` — who holds a lease (S16.9)."""
    return f"ciu@{_host_identity()}:{instance_id}"


def acquire_lease(
    record: WorktreeInstanceRecord,
    *,
    ttl_hours: float,
    holder: str,
    now: datetime | None = None,
) -> WorktreeInstanceRecord:
    """Return *record* with a ``held`` lease acquired or RENEWED (S16.9).

    A renewal preserves ``acquired_at_utc`` (when the claim first started)
    and moves ``renewed_at_utc``/``expires_at_utc`` — losing the original
    acquisition instant would erase the only evidence of how long an instance
    has actually been owned.
    """
    if not ttl_hours > 0:
        raise WorktreeError(
            f"[S16.9] lease ttl must be a positive number of hours, got {ttl_hours!r}"
        )
    instant = now or _utc_now()
    stamp = _utc_stamp(instant)
    expires = _utc_stamp(instant + timedelta(hours=ttl_hours))
    acquired = record.lease.acquired_at_utc if record.lease is not None else stamp
    return replace(
        record,
        lease=WorktreeLease(
            holder=holder, acquired_at_utc=acquired, renewed_at_utc=stamp,
            expires_at_utc=expires, mode="held",
        ),
        schema_version=WORKTREE_INSTANCE_SCHEMA_VERSION,
    )


def make_lease_perpetual(
    record: WorktreeInstanceRecord,
    *,
    holder: str,
    now: datetime | None = None,
) -> WorktreeInstanceRecord:
    """Return *record* with an explicit unbounded (``perpetual``) lease."""
    instant = now or _utc_now()
    stamp = _utc_stamp(instant)
    acquired = record.lease.acquired_at_utc if record.lease is not None else stamp
    return replace(
        record,
        lease=WorktreeLease(
            holder=holder, acquired_at_utc=acquired, renewed_at_utc=stamp,
            expires_at_utc=None, mode="perpetual",
        ),
        schema_version=WORKTREE_INSTANCE_SCHEMA_VERSION,
    )


def release_lease(record: WorktreeInstanceRecord) -> WorktreeInstanceRecord:
    """Return *record* with no lease at all (``lease: null``).

    The record STAYS at schema v2: "this instance participates in leasing and
    currently claims nothing" is a different, more informative fact than "this
    record predates leasing entirely", and a reader must be able to tell them
    apart.
    """
    return replace(
        record, lease=None, schema_version=WORKTREE_INSTANCE_SCHEMA_VERSION
    )


def instance_record_path(ciu_root: Path) -> Path:
    """The record of the checkout AT *ciu_root* — by exact path, never a
    search that could climb to the PRIMARY checkout's record (S16)."""
    return Path(ciu_root) / WORKTREE_INSTANCE_RECORD


def read_own_instance_record(ciu_root: Path) -> WorktreeInstanceRecord | None:
    """This checkout's OWN record, or ``None`` when it has none.

    ``None`` is the PRIMARY / unmanaged-checkout answer and is what gates
    every lease and ownership-label behavior in this package: a checkout with
    no lifecycle record is not a managed worktree instance and is left
    completely alone.
    """
    path = instance_record_path(ciu_root)
    if not path.is_file():
        return None
    return read_instance_record(path)


def acquire_own_lease(
    ciu_root: Path, *, ttl_hours: float, now: datetime | None = None
) -> WorktreeInstanceRecord | None:
    """Acquire/renew the ``held`` lease on the checkout at *ciu_root*.

    Returns ``None`` — writing nothing — when that checkout carries no
    instance record (PRIMARY or unmanaged): `ciu up` there behaves exactly as
    it did before this package existed.
    """
    record = read_own_instance_record(ciu_root)
    if record is None:
        return None
    instance_id = record.instance_id or _runtime_identity(Path(ciu_root))[0]
    updated = acquire_lease(
        record, ttl_hours=ttl_hours, holder=lease_holder(instance_id), now=now
    )
    _write_instance_record(updated)
    return updated


def release_own_lease(ciu_root: Path) -> WorktreeInstanceRecord | None:
    """Clear the lease on the checkout at *ciu_root* (``None`` when unmanaged).

    Callers invoke this ONLY after a teardown they verified succeeded — a
    failed clean must leave the lease exactly as it was, because the lease is
    the evidence that something still owns those Docker resources.
    """
    record = read_own_instance_record(ciu_root)
    if record is None:
        return None
    # Nothing claimed => nothing to clear, and in particular a v1 record is
    # NOT dragged up to v2 by a teardown that had no lease to release.
    if record.lease is None:
        return record
    updated = release_lease(record)
    _write_instance_record(updated)
    return updated


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
class SharedInfraRefService:
    """S16.1/CIU-52 — one CIU-resolved address for a service that belongs to
    the REFERENCE instance, recorded under this (joining) instance's own local
    alias.

    Deliberately a THIRD axis, independent of both
    :attr:`SharedInfraIntent.services` (which names THIS instance's own
    diverging-tier containers to connect) and
    :attr:`SharedInfraIntent.ref_projects` (which names the REFERENCE's compose
    projects, used only for AND-combined liveness). Those two are NOT paired
    with each other and neither can supply a reference-side service name, so
    an alias is never inferred from either: pointing this instance's OWN copy
    of a service at the reference's copy of it would be actively wrong.

    *container* is always CIU-derived (``deploy.container_name`` applied to the
    REFERENCE's own rendered global config) and authenticated against live
    Docker state before it is written — never hand-typed.
    """

    alias: str
    service: str
    container: str
    port: int | None = None


@dataclass(frozen=True)
class SharedInfraIntent:
    """S16.1/CIU-22 — a worktree's recorded intent to join a reference
    instance's shared-infra network, resolved and validated once at
    ``worktree add --shared-infra`` time and persisted verbatim into this
    worktree's own global instance overlay (see
    :func:`parse_shared_infra_config`,
    :func:`connect_shared_infra_after_up`).

    *ref_services* (S16.1/CIU-52) is OPTIONAL and defaults to ``()``: an
    instance that declares none behaves exactly as it did before that field
    existed — byte-identical overlay text, and not one extra Docker call at
    either `add` or join time.
    """

    ref_path: Path
    network: str
    services: tuple[str, ...]
    ref_projects: tuple[str, ...]
    ref_services: tuple[SharedInfraRefService, ...] = ()


# S16.1/CIU-52 grammars. Every recorded value passes through the worktree
# overlay, which is Jinja-rendered and `$VAR`-expanded (S3.2) and secret-
# scanned (S3.1a) on every later read — so `$` (and `{`) must be structurally
# impossible in an alias, a reference service key, or a derived container
# name, rather than merely unlikely.
_REF_SERVICE_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_REF_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_REF_SERVICE_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


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


def _parse_ref_services_arg(raw: str, *, label: str) -> tuple[tuple[str, str], ...]:
    """S16.1/CIU-52 CLI grammar — parse ``alias[,alias=ref_service,...]`` into
    ``(alias, reference_service)`` pairs, sorted by alias.

    A bare item means "the alias equals the reference's own service key" (the
    common case); ``alias=ref_service`` is the rename escape hatch. Splitting
    reuses :func:`_split_unique_list`, so a blank item, an empty value and a
    verbatim duplicate are refused with the exact wording the sibling
    shared-infra flags already use. Alias uniqueness is then enforced on the
    ALIAS specifically (``vault,vault=vault`` is two distinct items but one
    alias); two different aliases MAY legitimately name the same reference
    service.
    """
    pairs: dict[str, str] = {}
    for item in _split_unique_list(raw, label=label):
        alias, sep, service = item.partition("=")
        if not sep:
            service = alias
        if not _REF_SERVICE_ALIAS_RE.fullmatch(alias):
            raise WorktreeError(
                f"[S16.1] {label} alias {alias!r} must match "
                f"{_REF_SERVICE_ALIAS_RE.pattern!r}; it becomes a "
                "[topology.services.<alias>] table key in this instance's own "
                "configuration."
            )
        if not _REF_SERVICE_NAME_RE.fullmatch(service):
            raise WorktreeError(
                f"[S16.1] {label} reference service {service!r} (alias "
                f"{alias!r}) must match {_REF_SERVICE_NAME_RE.pattern!r}."
            )
        if alias in pairs:
            raise WorktreeError(
                f"[S16.1] {label} contains a duplicate alias: {alias!r}"
            )
        pairs[alias] = service
    return tuple(sorted(pairs.items()))


def _config_ref_services(
    value: Any, *, label: str
) -> tuple[SharedInfraRefService, ...]:
    """S16.1/CIU-52 stored-TOML grammar — a table of tables keyed by alias,
    each ``{service, container, port?}``.

    The alias is the value's identity (it becomes ``topology.services.<alias>``
    in this instance's own config) and must be unique, which a TOML table key
    enforces structurally — hence a table rather than a flat list. Returns a
    deterministic tuple sorted by alias, matching the order
    :func:`_worktree_overlay_text` writes, so the overlay round-trips exactly.
    """
    if not isinstance(value, dict) or not value:
        raise WorktreeError(f"[S16.1] {label} must be a non-empty table of tables")
    entries: list[SharedInfraRefService] = []
    for alias in sorted(value):
        if not _REF_SERVICE_ALIAS_RE.fullmatch(alias):
            raise WorktreeError(
                f"[S16.1] {label} alias {alias!r} must match "
                f"{_REF_SERVICE_ALIAS_RE.pattern!r}"
            )
        raw = value[alias]
        if not isinstance(raw, dict):
            raise WorktreeError(f"[S16.1] {label}.{alias} must be a table")
        required = {"service", "container"}
        optional = {"port"}
        missing, unknown = required - set(raw), set(raw) - (required | optional)
        if missing or unknown:
            raise WorktreeError(
                f"[S16.1] malformed {label}.{alias}: "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        service = raw["service"]
        if not isinstance(service, str) or not _REF_SERVICE_NAME_RE.fullmatch(service):
            raise WorktreeError(
                f"[S16.1] {label}.{alias}.service must be a string matching "
                f"{_REF_SERVICE_NAME_RE.pattern!r}; got {service!r}"
            )
        container = raw["container"]
        if not isinstance(container, str) or not _REF_SERVICE_CONTAINER_RE.fullmatch(
            container
        ):
            raise WorktreeError(
                f"[S16.1] {label}.{alias}.container must be a string matching "
                f"{_REF_SERVICE_CONTAINER_RE.pattern!r}; got {container!r}"
            )
        port = raw.get("port")
        if port is not None and (not isinstance(port, int) or isinstance(port, bool)):
            raise WorktreeError(
                f"[S16.1] {label}.{alias}.port must be an integer; got {port!r}"
            )
        entries.append(
            SharedInfraRefService(
                alias=alias, service=service, container=container, port=port
            )
        )
    return tuple(entries)


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
    # The shape stays CLOSED; CIU-52 widens it by exactly one OPTIONAL key.
    # Both halves of the original message survive verbatim, so an unknown key
    # is still named and a missing required key is still named.
    required = {"ref_path", "network", "services", "ref_projects"}
    optional = {"ref_services"}
    missing, unknown = required - set(raw), set(raw) - (required | optional)
    if missing or unknown:
        raise WorktreeError(
            "[S16.1] malformed [ciu.instance.shared_infra]: "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if not isinstance(raw["ref_path"], str) or not raw["ref_path"]:
        raise WorktreeError("[S16.1] shared_infra.ref_path must be a non-empty string")
    if not isinstance(raw["network"], str) or not raw["network"]:
        raise WorktreeError("[S16.1] shared_infra.network must be a non-empty string")
    ref_services: tuple[SharedInfraRefService, ...] = ()
    if "ref_services" in raw:
        ref_services = _config_ref_services(
            raw["ref_services"], label="shared_infra.ref_services"
        )
    return SharedInfraIntent(
        ref_path=Path(raw["ref_path"]), network=raw["network"],
        services=_config_string_list(raw["services"], label="shared_infra.services"),
        ref_projects=_config_string_list(
            raw["ref_projects"], label="shared_infra.ref_projects"
        ),
        ref_services=ref_services,
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
        # S16.1/CIU-52. Sub-tables of the parent intent FIRST, then the
        # top-level [topology.*] blocks they feed — the order a human reads
        # top-to-bottom. Emitted only when ref_services is non-empty, so an
        # instance that declares none gets byte-identical text to before.
        for entry in shared_infra.ref_services:
            lines.extend([
                "",
                f"[ciu.instance.shared_infra.ref_services.{entry.alias}]",
                f"service = {json.dumps(entry.service)}",
                f"container = {json.dumps(entry.container)}",
            ])
            if entry.port is not None:
                lines.append(f"port = {entry.port}")
        if shared_infra.ref_services:
            lines.extend([
                "",
                "# S16.1/CIU-52 — CIU-resolved addressing for the reference instance's shared",
                "# services. Do not hand-edit; re-run `ciu worktree add --shared-infra ...`.",
            ])
            for entry in shared_infra.ref_services:
                lines.extend([
                    "",
                    f"[topology.services.{entry.alias}]",
                    f"internal_host = {json.dumps(entry.container)}",
                ])
                if entry.port is not None:
                    lines.append(f"internal_port = {entry.port}")
    return "\n".join(lines) + "\n"


def _write_worktree_overlay(
    ciu_root: Path, profile: str | None, shared_infra: SharedInfraIntent | None
) -> None:
    payload = _worktree_overlay_text(profile, shared_infra)
    if payload is None:
        return
    path = ciu_root / GLOBAL_CONFIG_INSTANCE_OVERRIDES
    if path.exists():
        raise WorktreeError(
            f"[S16] refusing to overwrite existing instance override {path}"
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
        except OSError:
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


def _live_ref_service_names(network: str, service: str) -> list[str]:
    """S16.1/CIU-52 — the NAMES of containers currently RUNNING on *network*
    that carry ``com.docker.compose.service=<service>``.

    Shared verbatim by add-time authentication and join-time re-verification:
    same query, same failure shape, so the two can never drift into disagreeing
    about what "live" means.

    A query that cannot be ANSWERED is a loud failure, never an empty result.
    An unreachable daemon, a missing binary and a non-zero ``docker ps`` all
    raise — collapsing any of them into ``[]`` would turn "CIU could not
    determine this" into "CIU determined the container is absent", which for
    the add-time caller is a silent refusal-for-the-wrong-reason and for the
    join-time caller would be indistinguishable from real staleness.
    """
    try:
        res = procutil.docker(
            [
                "ps", "--no-trunc",
                "--filter", f"network={network}",
                "--filter", f"label=com.docker.compose.service={service}",
                "--format", "{{.Names}}",
            ],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise WorktreeError(
            f"[S16.1] could not query reference service {service!r} on "
            f"network {network!r}: {exc}"
        ) from exc
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.1] could not query reference service {service!r} on "
            f"network {network!r}: "
            f"{(res.stderr or res.stdout or '').strip()}"
        )
    names = {
        name.strip()
        for line in (res.stdout or "").splitlines()
        for name in line.split(",")
        if name.strip()
    }
    return sorted(names)


def _authenticate_ref_services(
    network: str, entries: tuple[SharedInfraRefService, ...], *, recorded: bool
) -> None:
    """S16.1/CIU-52 — prove every entry's container name is a container that
    is ACTUALLY running, right now, on the reference's network under that
    service label.

    This is what makes the derivation trustworthy rather than merely plausible:
    a stale recording, a reference that was re-created under a new identity, or
    a wrong derivation all fail here instead of being written into (or acted
    on from) this instance's own addressing. The container name itself carries
    the reference's ``project_name``/``environment_tag``, which IS the
    authenticating fact — so the query is deliberately NOT additionally scoped
    by ``com.docker.compose.project``: a reference may legitimately run a
    shared service under a project the operator never needed to declare via
    ``--shared-infra-ref-projects`` for liveness, and scoping to those would
    refuse a correct configuration.

    *recorded* selects add-time ("resolved", nothing written yet) from
    join-time ("recorded", already persisted) phrasing and remedy.
    """
    for entry in entries:
        live = _live_ref_service_names(network, entry.service)
        if entry.container in live:
            continue
        message = (
            f"[S16.1] {'recorded' if recorded else 'resolved'} reference "
            f"container {entry.container!r} for service {entry.service!r} "
            f"(alias {entry.alias!r}) is not live on network {network!r} "
            f"(found: {live}). The reference instance may be stopped, or its "
            "identity may have changed."
        )
        if recorded:
            message += (
                " Restore it, re-run `ciu worktree add --shared-infra` to "
                "update the recorded reference, or `ciu down` this instance."
            )
        raise WorktreeError(message)


def _resolve_ref_services(
    requested: tuple[tuple[str, str], ...],
    *,
    ref_ciu_root: Path,
    ref_env: Mapping[str, str],
    network: str,
) -> tuple[SharedInfraRefService, ...]:
    """S16.1/CIU-52 — derive each requested reference service's QUALIFIED
    container name from the REFERENCE's OWN rendered global config, then
    authenticate it against live Docker state before it is trusted.

    The derivation source is the reference's own configuration and nothing
    else — never string surgery on ``ref_projects`` (which names compose
    projects, not services, and is not paired with anything) and never this
    instance's own config (which would produce this instance's copy of the
    service, the exact wrong answer).

    ``write_rendered=False`` and ``environ=ref_env`` are both mandatory and
    both load-bearing, mirroring :func:`resolve_worktree_cap` and
    :func:`_resolve_budget_candidates`, which read another checkout's policy
    the same way: the first keeps CIU from writing ``ciu.global.toml`` into a
    checkout it does not own, the second keeps THIS process's ambient
    environment (its own ``INSTANCE_ID``, ``REPO_ROOT``, ...) from leaking into
    the reference's templates and silently producing a name that belongs to
    neither instance.
    """
    if not requested:
        return ()

    # Lazy import: deploy.py imports engine, which imports worktree, so a
    # module-level import here would cycle. Same reason as the existing lazy
    # `from . import engine` in _candidate_project.
    from . import deploy as deploy_mod

    try:
        ref_global = config_model.render_global_chain(
            ref_ciu_root, ref_ciu_root, write_rendered=False, environ=ref_env,
        )
    except ValueError as exc:
        raise WorktreeError(
            f"[S16.1] could not render the shared-infra reference's own global "
            f"configuration at {ref_ciu_root}: {exc}"
        ) from exc

    resolved: list[SharedInfraRefService] = []
    for alias, service in requested:
        try:
            container = deploy_mod.container_name(ref_global, service)
        except ValueError as exc:
            raise WorktreeError(
                f"[S16.1] could not resolve reference service {service!r} "
                f"(alias {alias!r}) from {ref_ciu_root}: {exc}"
            ) from exc
        if not _REF_SERVICE_CONTAINER_RE.fullmatch(container):
            raise WorktreeError(
                f"[S16.1] reference service {service!r} (alias {alias!r}) "
                f"derives container name {container!r}, which is not a legal "
                f"container name ({_REF_SERVICE_CONTAINER_RE.pattern!r}); the "
                "reference's deploy.project_name/environment_tag look wrong."
            )
        resolved.append(
            SharedInfraRefService(
                alias=alias, service=service, container=container,
                port=_ref_service_port(ref_global, service),
            )
        )

    _authenticate_ref_services(network, tuple(resolved), recorded=False)
    return tuple(resolved)


def _ref_service_port(ref_global: Mapping[str, Any], service: str) -> int | None:
    """The reference's own declared ``topology.services.<service>.internal_port``,
    or ``None`` when it declares none.

    Never invented: an absent (or non-integer) value stays ``None`` so the
    overlay writes no ``internal_port`` key at all, leaving any committed
    default in the joining instance's own chain to survive the merge.
    """
    topology = ref_global.get("topology")
    services = topology.get("services") if isinstance(topology, dict) else None
    entry = services.get(service) if isinstance(services, dict) else None
    port = entry.get("internal_port") if isinstance(entry, dict) else None
    return port if isinstance(port, int) and not isinstance(port, bool) else None


def _preflight_shared_infra_for_add(
    repo_root: Path,
    *,
    shared_infra: str,
    shared_infra_services: str,
    shared_infra_ref_projects: str,
    shared_infra_ref_services: str | None = None,
) -> SharedInfraIntent:
    """S16.1 — resolve and validate `worktree add --shared-infra` input
    BEFORE any side effect (no git worktree, no checkout). Read-only Docker
    checks only; see the module's O1 contract."""
    from .workspace_env import (
        GENERATED_FACTS_HEADER,
        WorkspaceEnvError,
        generated_facts_path,
        identity_env_from_facts,
        read_generated_facts,
    )

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

    # CIU-75: the reference's network is a FACT read, so it comes from that
    # checkout's own `[ciu.instance.generated]` facts file — the sole
    # instance-fact source since 7.7.0 — not from its legacy `ciu.env` export.
    ref_ciu_root = ref.path / _ciu_root_offset(repo_root)
    try:
        ref_facts = read_generated_facts(ref_ciu_root)
    # CIU-62's three-exception lesson survives the cutover in one narrower
    # form: read failure, non-UTF-8 byte and malformed table all arrive as
    # WorkspaceEnvError from the reader, which normalizes them at the seam.
    except WorkspaceEnvError as exc:
        raise WorktreeError(f"[S16.1] could not read {ref_ciu_root}: {exc}") from exc

    network = ref_facts.get("network", "")
    if not network:
        raise WorktreeError(
            f"[S16.1] {ref_ciu_root} declares no generated instance network "
            f"(no {GENERATED_FACTS_HEADER}.network in "
            f"{generated_facts_path(ref_ciu_root).name}), so it is not a usable CIU "
            "instance. Run `ciu env generate` there first."
        )

    # The environment the REFERENCE's own config chain renders against
    # (`_resolve_ref_services` below): ambient MINUS every CIU identity key,
    # PLUS the reference's own facts — the same rule `_sanitized_target_env`
    # and `_resolve_budget_candidates` use since CIU-75. This process's own
    # INSTANCE_ID/REPO_ROOT must never reach the reference's templates.
    ref_env = {
        k: v for k, v in os.environ.items() if k not in _CIU_IDENTITY_ENV_KEYS
    }
    ref_env.update(identity_env_from_facts(ref_facts))

    services = _split_unique_list(shared_infra_services, label="--shared-infra-services")
    ref_projects = _split_unique_list(
        shared_infra_ref_projects, label="--shared-infra-ref-projects"
    )
    # Grammar first, alongside its two siblings: a malformed flag is refused
    # before CIU asks Docker anything at all.
    requested_ref_services: tuple[tuple[str, str], ...] = ()
    if shared_infra_ref_services is not None:
        requested_ref_services = _parse_ref_services_arg(
            shared_infra_ref_services, label="--shared-infra-ref-services"
        )

    _check_reference_network_and_projects(network, ref_projects)

    ref_services = _resolve_ref_services(
        requested_ref_services,
        ref_ciu_root=ref.path / _ciu_root_offset(repo_root),
        ref_env=ref_env,
        network=network,
    )

    return SharedInfraIntent(
        ref_path=ref.path, network=network, services=services,
        ref_projects=ref_projects, ref_services=ref_services,
    )


def _generate_env_in(worktree: Path, *, identity_only: bool = False) -> int:
    """Run ``ciu env generate`` INSIDE *worktree*, with the primary checkout's
    repo-path variables STRIPPED from the child environment.

    CIU-10 reconciles a pre-set ``PHYSICAL_REPO_ROOT`` against mountinfo, so an
    inherited value is usually caught — but the honest input here is *no* value:
    the whole point of generating is to DERIVE this checkout's identity, and an
    inherited one is another repo's answer to the same question.

    CIU-85: strips the same ``_CIU_IDENTITY_ENV_KEYS`` its siblings do, via
    that shared tuple, rather than its own separately hand-maintained
    literal — the pre-fix copy here predated `PUBLIC_FQDN` joining the
    identity tuple (CIU-47) and had silently fallen one key behind; an
    ambient ``PUBLIC_FQDN`` leaking through here would have been silently
    *adopted* as this checkout's own by `_detect_public_fqdn`'s "no
    independently-derived value: the pre-set value stands" rule — the exact
    cross-checkout leak CIU-47 fixed elsewhere.
    """
    import os
    import sys

    env = {
        k: v for k, v in os.environ.items() if k not in _CIU_IDENTITY_ENV_KEYS
    }
    argv = [sys.executable, "-m", "ciu.cli", "env", "generate"]
    if identity_only:
        argv.append("--identity-only")
    try:
        return subprocess.run(argv, cwd=str(worktree), env=env, check=False).returncode
    except OSError as exc:  # pragma: no cover - environmental
        raise WorktreeError(f"[S16] could not run `ciu env generate`: {exc}") from exc


def _clean_in(worktree: Path, *, yes: bool) -> int:
    """Run ``ciu clean`` INSIDE *worktree*, under that worktree's own identity.

    A subprocess, not an in-process call, and deliberately so. S1.1 requires
    ``--define-root`` to agree with ``REPO_ROOT``; this process's REPO_ROOT
    normally points at the PRIMARY checkout, so an in-process clean of a
    worktree would either abort on that guard or — worse, if the guard were
    bypassed — clean the wrong instance. Handing the child the worktree's own
    environment makes the two agree honestly instead of arguing.
    """
    import os
    import sys

    # CIU-75: read the target checkout's identity from its OWN
    # `[ciu.instance.generated]` facts file by exact path — never through
    # `find_workspace_env`-style searching, which prefers `$REPO_ROOT` over
    # the directory it was given and would therefore answer with the PRIMARY's
    # identity, cleaning the wrong instance under a convincingly-correct-
    # looking env. The path is a fact, not something to search for.
    #
    # Only the IDENTITY keys are overlaid onto the ambient environment, where
    # the pre-cutover code overlaid every `ciu.env` key. That is the whole
    # delta and it is deliberate: identity is per-checkout (which is exactly
    # why this function exists), while the rest of `ciu.env` — USER_UID,
    # DOCKER_GID, PYTHON_EXECUTABLE, HOST_MDT_TMP — are facts about the
    # MACHINE, identical in both checkouts and now taken live from this
    # process rather than from a file that may predate a rebuild.
    from .workspace_env import (
        WorkspaceEnvError,
        generated_facts_path,
        read_instance_identity_env,
    )

    try:
        identity = read_instance_identity_env(worktree)
    except WorkspaceEnvError as exc:
        raise WorktreeError(f"[S16] could not read {worktree}: {exc}") from exc
    if not identity:
        raise WorktreeError(
            f"[S16] {generated_facts_path(worktree)} carries no "
            "generated instance identity, so CIU cannot tell which instance to "
            "clean. Run `ciu env generate` in that worktree first — cleaning "
            "under the PRIMARY checkout's environment would target the wrong "
            "stack."
        )
    # CIU-85: strip THIS process's own identity keys before overlaying the
    # target's, matching the two siblings that already do
    # (`_sanitized_target_env`, `_resolve_budget_candidates`). Harmless in
    # practice today — `identity` always carries all six overlay-fact keys
    # once `not identity` above has refused an empty table, so every key in
    # `_CIU_IDENTITY_ENV_KEYS` bar one is overwritten regardless — but
    # `CIU_SERVICES_PROFILE` is NOT an overlay fact and was therefore never
    # in `identity`, so without the strip the CALLER's service-profile
    # selection leaked into the child `ciu clean`, unlike its two siblings.
    env = {k: v for k, v in os.environ.items() if k not in _CIU_IDENTITY_ENV_KEYS}
    env.update(identity)

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
    if len(matches) > 1:
        raise WorktreeError(f"[S16] ambiguous logical identity {logical_name!r}")
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# S16.4 / S16.5 — structured JSON documents and capability discovery (D-009)
# ---------------------------------------------------------------------------

WORKTREE_JSON_SCHEMA_VERSION = 1
CAPABILITIES_SCHEMA_VERSION = 1

# Closed operation vocabulary for every structured document. The status
# vocabulary is WORKTREE_LIFECYCLE_STATES plus the terminal "removed"; a
# recovery-required instance additionally carries a closed recovery_status
# (WORKTREE_RECOVERY_STATUSES).
WORKTREE_JSON_OPERATIONS = frozenset({
    "add", "adopt", "create", "ensure", "inspect", "lease", "list", "remove",
})
WORKTREE_JSON_STATUSES = WORKTREE_LIFECYCLE_STATES | {"removed"}

# The closed, sorted allowlist of shipped machine contracts. Consumers
# allowlist these identifiers instead of inferring features from SemVer
# (D-009). An identifier is added only when its code path ships in the SAME
# release; `worktree.up.v1`/`worktree.exec-local.v1` ship in P05;
# `worktree.exec-target.v1` ships in P06.
WORKTREE_CAPABILITIES = (
    "worktree.branches.v1",
    # S16.9/S16.10, ciu-P26 + ciu-P27: the ownership lease (record schema v2 +
    # `ciu worktree lease`) and the Docker-resource reap survey/transaction
    # that reads it. Advertised together because a consumer that can reap must
    # be able to declare a lease first — reaping is only ever as safe as the
    # ownership signal it consults.
    "worktree.lease.v1",
    "worktree.reap.v1",
    "worktree.identity.v1",
    "worktree.inspect.v1",
    "worktree.lifecycle-json.v1",
    "worktree.up.v1",
    "worktree.exec-local.v1",
    "worktree.exec-target.v1",
)


def build_instance_document(
    operation: str,
    record: WorktreeInstanceRecord,
    git_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One versioned instance document: the persisted record plus, when
    *git_facts* is supplied, freshly derived Git facts (S16.4). *operation*
    must be a member of the closed :data:`WORKTREE_JSON_OPERATIONS`
    vocabulary; *status* is the record's own lifecycle state."""
    if operation not in WORKTREE_JSON_OPERATIONS:
        raise WorktreeError(
            f"[S16] unknown document operation {operation!r}; closed vocabulary: "
            f"{', '.join(sorted(WORKTREE_JSON_OPERATIONS))}"
        )
    doc: dict[str, Any] = {
        "schema_version": WORKTREE_JSON_SCHEMA_VERSION,
        "operation": operation,
        "status": record.state,
        "instance": record.to_dict(),
    }
    if git_facts is not None:
        doc["git"] = dict(git_facts)
    return doc


def _current_git_facts(repo_root: Path, record: WorktreeInstanceRecord) -> dict[str, Any]:
    """Freshly read Git facts for one managed instance (S16.4).

    The registered path, branch/detached state and HEAD come from the CURRENT
    ``git worktree list``; dirty comes from ``git status --porcelain``. A
    record whose checkout is no longer a registered worktree, or whose status
    cannot be read, is a refusal — never a repaired or guessed value.
    """
    wt = find_worktree(repo_root, str(record.git_worktree_path))
    if wt is None:
        raise WorktreeError(
            f"[S16] instance record claims {record.git_worktree_path}, but Git "
            f"no longer registers that checkout as a worktree under {repo_root}"
        )
    status = _git(["status", "--porcelain"], wt.path)
    if status.returncode != 0:
        raise WorktreeError(
            f"[S16] could not read git status for {wt.path}: "
            f"{(status.stderr or status.stdout).strip()}"
        )
    return {
        "registered": True,
        "path": str(wt.path),
        "branch": wt.branch,
        "detached": wt.branch == "(detached)",
        "primary": wt.is_primary,
        "head": wt.head,
        "dirty": bool(status.stdout.strip()),
    }


def inspect_instance(repo_root: Path, logical_name: str) -> dict[str, Any]:
    """Build the ``ciu worktree inspect LOGICAL --json`` document (S16.4).

    No logical record is a refusal; a record whose Git facts cannot be read
    truthfully is a refusal. Never a stale-record-only result.
    """
    repo_root = Path(repo_root).resolve()
    record = find_instance_record(repo_root, logical_name)
    if record is None:
        raise WorktreeError(
            f"[S16] no managed worktree instance named {logical_name!r} under "
            f"{repo_root}; `ciu worktree list` shows what exists."
        )
    return build_instance_document(
        "inspect", record, _current_git_facts(repo_root, record)
    )


def list_instances(repo_root: Path) -> dict[str, Any]:
    """Build the ``ciu worktree list --json`` document: every managed
    instance with its freshly derived Git facts, in git's own order."""
    repo_root = Path(repo_root).resolve()
    instances = [
        build_instance_document(
            "inspect", record, _current_git_facts(repo_root, record)
        )
        for record in list_instance_records(repo_root)
    ]
    return {
        "schema_version": WORKTREE_JSON_SCHEMA_VERSION,
        "operation": "list",
        "status": "ready",
        "instances": instances,
    }


def remove_document(
    repo_root: Path,
    name: str,
    *,
    yes: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Run the existing clean-then-remove and return the versioned removal
    document (S16.4). The validated pre-state (the instance record, when the
    worktree is managed) is captured before removal; success is emitted only
    after BOTH clean and ``git worktree remove`` complete. A failure is the
    existing :class:`WorktreeError` (identifying retained resources) — no
    success document is ever produced."""
    repo_root = Path(repo_root).resolve()
    record = find_instance_record(repo_root, name)
    removed_path = remove(repo_root, name, yes=yes, force=force)
    doc: dict[str, Any] = {
        "schema_version": WORKTREE_JSON_SCHEMA_VERSION,
        "operation": "remove",
        "status": "removed",
        "removed_path": str(removed_path),
    }
    if record is not None:
        doc["instance"] = record.to_dict()
    return doc


def _lease_duration_hours(text: str) -> float:
    """Parse one ``--extend`` duration into hours (S16.9).

    Reuses the codebase's ONE duration grammar (``deploy.parse_duration_seconds``,
    the strict form of what ``deploy._seconds`` has always accepted: ``24h``,
    ``90m``, ``3600``) rather than inventing a second spelling. The import is
    lazy because ``deploy`` imports this module.
    """
    from . import deploy as deploy_mod

    try:
        seconds = deploy_mod.parse_duration_seconds(text)
    except ValueError as exc:
        raise WorktreeError(f"[S16.9] invalid lease duration {text!r}: {exc}") from exc
    if seconds <= 0:
        raise WorktreeError(
            f"[S16.9] lease duration must be positive, got {text!r}"
        )
    return seconds / 3600.0


def apply_lease(
    repo_root: Path,
    logical_name: str,
    *,
    extend: str | None = None,
    perpetual: bool = False,
    release: bool = False,
    now: datetime | None = None,
) -> WorktreeInstanceRecord:
    """`ciu worktree lease LOGICAL (--extend D | --perpetual | --release)`.

    The explicit operator verb behind S16.9. It touches the RECORD only: it
    runs no Docker query and does not care whether the instance is currently
    up. Extending or releasing a claim on a stopped instance is exactly as
    meaningful as on a running one — the lease describes ownership of the
    instance's resources, not their current run state.

    *now* mirrors `acquire_lease`/`make_lease_perpetual`'s own optional
    override (CIU-76): absent, both fall back to real wall-clock time as
    always; a caller (a test freezing a fixture clock) can pin the instant
    the acquire/renew/perpetual math runs against, rather than letting it
    silently race the real clock. `--release` ignores it — releasing a lease
    is not time-based.
    """
    repo_root = Path(repo_root).resolve()
    if sum((extend is not None, perpetual, release)) != 1:
        raise WorktreeError(
            "[S16.9] `ciu worktree lease` needs exactly one of --extend "
            "DURATION, --perpetual or --release"
        )
    record = find_instance_record(repo_root, logical_name)
    if record is None:
        raise WorktreeError(
            f"[S16.9] no managed worktree instance named {logical_name!r} under "
            f"{repo_root}; `ciu worktree list` shows what exists."
        )
    if release:
        # Unconditional, unlike the teardown-driven clear: the operator asked
        # for "claims nothing", so the record says so even if it said so
        # already (and a v1 record is normalized to v2 by that statement).
        updated = release_lease(record)
    else:
        instance_id = record.instance_id or _runtime_identity(record.ciu_root)[0]
        holder = lease_holder(instance_id)
        updated = (
            make_lease_perpetual(record, holder=holder, now=now)
            if perpetual
            else acquire_lease(
                record, ttl_hours=_lease_duration_hours(str(extend)),
                holder=holder, now=now,
            )
        )
    _write_instance_record(updated)
    return updated


def capabilities_document() -> dict[str, Any]:
    """The versioned, closed capability allowlist (S16.5 / D-009). Only
    machine contracts shipped in THIS release are advertised."""
    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "capabilities": sorted(WORKTREE_CAPABILITIES),
    }


# ---------------------------------------------------------------------------
# S16.8 — worktree BRANCH hygiene (CIU-25, git half): survey + prune
# ---------------------------------------------------------------------------

# 2 as of ciu-P28: the closed category vocabulary WIDENED (new
# `managed-instance`). Strict consumers refuse unknown members fail-closed, so
# a widened vocabulary is a schema bump — the same rule S17.3's provenance
# document followed when its verdicts widened.
BRANCHES_SCHEMA_VERSION = 2

# Closed category vocabulary. Every local branch collapses into exactly one:
#   base          — the branch the survey is measured against (never touched)
#   mainline      — the repository's DEFAULT branch (origin/HEAD's target;
#                   policy fallback: literally "main"/"master" when origin/HEAD
#                   is unresolvable) — never pruned even when the survey runs
#                   against another base: "clean up merged branches" can never
#                   mean deleting a mainline
#   current       — somebody's working context, even when merged: the PRIMARY
#                   checkout's branch OR the branch of the checkout this
#                   command was INVOKED FROM (ciu-P28: pruning the invoking
#                   checkout removed the operator's own cwd out from under the
#                   loop, so the next git call raised and the whole prune
#                   aborted with no document — see prune_branches)
#   managed-instance — its checkout carries a CIU-managed instance record, at
#                   ANY lifecycle state. NEVER removed here: the git-half prune
#                   does a bare `git worktree remove`, and removing a managed
#                   checkout without `ciu clean` FIRST destroys the rendered
#                   config that tells CIU what to clean, orphaning
#                   containers/volumes/networks and stranding root-owned vol-*
#                   directories no unprivileged operator can delete
#                   (ciu-P28 / see remove()). Dispose with `ciu worktree rm`.
#   prunable      — fully merged into base AND (no checkout, or a clean,
#                   non-primary, non-invoking, unmanaged checkout) → safe to
#                   remove
#   merged-dirty  — merged but its checkout carries uncommitted changes;
#                   decide by hand what to do with the dirt first
#   unmerged      — has commits not in base → keep; attributes inform the decision
BRANCH_CATEGORIES = (
    "base", "mainline", "current", "managed-instance",
    "prunable", "merged-dirty", "unmerged",
)

_MAINLINE_FALLBACKS = ("main", "master")


def _default_branch(repo_root: Path) -> str | None:
    """The repo's default branch: origin/HEAD's target, else None."""
    res = _git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], repo_root)
    if res.returncode == 0:
        return res.stdout.strip().removeprefix("origin/")
    return None


def _resolve_base_branch(repo_root: Path, base: str) -> str:
    """Verify *base* names a LOCAL BRANCH; refuse loudly otherwise.

    A local branch is required — not a SHA, not a remote-tracking ref —
    because classification and the destructive prune reason about branch
    NAMES against this anchor. Accepting `--base <SHA>` let the branch whose
    tip WAS that SHA classify as prunable and get deleted: the survey's own
    anchor removed by its own prune (review finding).
    """
    res = _git(
        ["rev-parse", "--verify", "--quiet", f"refs/heads/{base}"], repo_root
    )
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.8] --base {base!r} does not name a LOCAL BRANCH in {repo_root} "
            "(SHAs and remote-tracking refs are refused — the prune reasons "
            "about branch names). Pass e.g. `ciu worktree branches --base main`."
        )
    return base


def _is_ancestor(repo_root: Path, ancestor_ref: str, descendant_ref: str) -> bool:
    res = _git(
        ["merge-base", "--is-ancestor", ancestor_ref, descendant_ref], repo_root
    )
    return res.returncode == 0


def _prune_base_sanity(repo_root: Path, base: str) -> None:
    """Refuse `-y` when the base is not contained in any checkout's HEAD.

    Git's `branch -d` judges mergedness against HEAD/upstream, NOT against an
    arbitrary --base. Pruning a base no checkout contains could remove linked
    checkouts while `branch -d` then refuses every branch — half-pruned state
    reported as success (the reproduced review BLOCKER). The survey answers
    any base; the DESTRUCTIVE pass demands one git itself agrees with.
    """
    base_sha = _git(["rev-parse", "--verify", f"refs/heads/{base}"], repo_root)
    assert base_sha.returncode == 0  # _resolve_base_branch already ran
    base_tip = base_sha.stdout.strip()

    safeties: list[str] = []
    for wt in list_worktrees(repo_root):
        if wt.is_primary and wt.branch != "(detached)":
            safeties.append(wt.branch)
    default = _default_branch(repo_root)
    if default:
        safeties.append(default)

    for head in dict.fromkeys(safeties):
        head_sha = _git(["rev-parse", "--verify", f"refs/heads/{head}"], repo_root)
        if head_sha.returncode == 0 and base_tip == head_sha.stdout.strip():
            return  # the base IS a working HEAD
        if _is_ancestor(repo_root, base_tip, head):
            return
    raise WorktreeError(
        f"[S16.8] --base {base!r} is not contained in any checkout's HEAD or "
        "origin/HEAD. Pruning against it could remove checkouts while Git then "
        "refuses the branch deletions (its mergedness rule uses HEAD/upstream, "
        "not --base) — leaving half-removed state. Survey only, or pass the "
        "branch your work actually merges target (e.g. the mainline)."
    )


def _branch_facts(repo_root: Path, base: str) -> list[dict[str, Any]]:
    """Per-local-branch facts, batched through for-each-ref/rev-list."""
    fmt = "%(refname:short)%00%(objectname:short)%00%(committerdate:iso-strict)%00%(contents:subject)"
    res = _git(["for-each-ref", "refs/heads", "--format", fmt], repo_root)
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.8] git for-each-ref failed: {(res.stderr or res.stdout).strip()}"
        )
    facts: list[dict[str, Any]] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        name, sha, date, subject = (line.split("\x00", 3) + ["", "", "", ""])[:4]
        # ahead/behind in ONE call: left = base-only commits (behind),
        # right = branch-only commits (ahead).
        lr = _git(["rev-list", "--count", "--left-right", f"{base}...{name}"], repo_root)
        ahead = behind = -1
        if lr.returncode == 0 and "\t" in lr.stdout:
            behind_s, ahead_s = lr.stdout.split()
            behind, ahead = int(behind_s), int(ahead_s)
        diff = _git(["diff", "--name-only", f"{base}...{name}"], repo_root)
        changed_files = (
            len([ln for ln in diff.stdout.splitlines() if ln.strip()])
            if diff.returncode == 0 else -1
        )
        facts.append({
            "name": name,
            "head": sha,
            "last_commit_at": date,
            "last_commit_subject": subject,
            "ahead": ahead,
            "behind": behind,
            "changed_files": changed_files,
            # merged = zero branch-only commits
            "merged": ahead == 0,
        })
    return facts


def branch_hygiene(repo_root: Path, *, base: str = "main") -> dict[str, Any]:
    """Build the ``ciu worktree branches`` document (S16.8 / CIU-25).

    A GROUNDED staleness survey — never age-based, never process-based: a
    branch is prunable only when Git itself proves nothing would be lost
    (zero commits not in *base*) AND any checkout of it is clean. Attributes
    (#changed files vs the merge-base, ahead/behind, last-commit age, ciu
    instance linkage) are surfaced so a human can rule on the rest.
    """
    repo_root = Path(repo_root).resolve()
    _resolve_base_branch(repo_root, base)
    facts = _branch_facts(repo_root, base)
    worktrees = list_worktrees(repo_root)
    checkout_map: dict[str, list[Path]] = {}
    # Checkouts that are somebody's working context RIGHT NOW and are
    # therefore never a removal candidate this run: the primary, and the
    # checkout this command was invoked from. The invoking one is load-bearing
    # (ciu-P28): its branch used to classify `prunable`, so `-y` removed the
    # operator's own cwd mid-loop and every following `git` call raised.
    guarded_paths: set[Path] = {git_toplevel(repo_root).resolve()}
    for wt in worktrees:
        checkout_map.setdefault(wt.branch, []).append(wt.path)
        if wt.is_primary:
            guarded_paths.add(wt.path.resolve())
    records = {
        r.git_worktree_path.resolve(): r for r in list_instance_records(repo_root)
    }
    default_branch = _default_branch(repo_root)

    branches: list[dict[str, Any]] = []
    for fact in facts:
        name = fact["name"]
        checkouts = sorted(str(p) for p in checkout_map.get(name, []))
        checkout: str | None = checkouts[0] if checkouts else None
        dirty = False
        if checkout:
            st = _git(["status", "--porcelain"], Path(checkout))
            dirty = st.returncode != 0 or bool(st.stdout.strip())
        record = (
            records.get(Path(checkout).resolve()) if checkout else None
        )
        ciu_instance = (
            {"logical_name": record.logical_name, "state": record.state}
            if record is not None else None
        )

        if name == base:
            # exact name only: "feature/main" is a real branch with its own
            # work, never the anchor (review finding — endswith misfiled it
            # into base, hiding it from every actionable category)
            category = "base"
        elif name == default_branch or (
            default_branch is None and name in _MAINLINE_FALLBACKS
        ):
            # Never prune a mainline, even when the survey runs against
            # another base and the mainline happens to be fully merged.
            category = "mainline"
        elif checkout and Path(checkout).resolve() in guarded_paths:
            # The primary checkout's branch — and the INVOKING checkout's —
            # are somebody's working context: even when merged, removing one
            # would yank a live workspace (the invoking one, the very cwd the
            # rest of the prune loop still needs).
            category = "current"
        elif ciu_instance is not None:
            # A CIU-managed checkout is disposed of by `ciu worktree rm`
            # (clean-then-remove), never by this git-half prune's bare
            # `git worktree remove` — see the vocabulary comment above.
            category = "managed-instance"
        elif fact["merged"]:
            if checkout and dirty:
                category = "merged-dirty"
            else:
                category = "prunable"
        else:
            category = "unmerged"

        branches.append({
            **fact,
            "category": category,
            "checkout": checkout,
            "dirty": dirty,
            "ciu_instance": ciu_instance,
        })

    branches.sort(key=lambda b: (b["category"], b["name"]))
    counts = {c: sum(1 for b in branches if b["category"] == c) for c in BRANCH_CATEGORIES}
    prunable_n = counts["prunable"]
    hint = (
        f"{prunable_n} branch(es) are fully merged and safe to remove; "
        "re-run with -y/--yes to prune them."
    ) if prunable_n else "nothing prunable."
    managed_n = counts["managed-instance"]
    if managed_n:
        # Name the escape hatch: -y deliberately refuses these, so the
        # operator must be told which command DOES dispose of them safely.
        hint += (
            f" {managed_n} branch(es) carry a CIU-managed instance and are "
            "never pruned here — dispose of each with `ciu worktree rm NAME`, "
            "which runs `ciu clean` BEFORE removing the checkout."
        )
    return {
        "schema_version": BRANCHES_SCHEMA_VERSION,
        "operation": "branches",
        "status": "survey",
        "base": base,
        "counts": counts,
        "hint": hint,
        "branches": branches,
    }


def _prune_candidate_refusal(git_root: Path, name: str) -> str:
    """Why ``git branch -d NAME`` would refuse, read-only, or ``""``.

    Both arms exist because ``git worktree remove`` runs FIRST: a refusal
    discovered by ``branch -d`` is discovered when the checkout is already
    gone — the half-pruned state the reviews reproduced. Git's own deletion
    rule is "contained in the upstream, else contained in HEAD", and *git_root*
    is the PRIMARY worktree, so ``HEAD`` here is exactly the HEAD the real
    ``branch -d`` will judge against (ciu-P28: it used to be the INVOKING
    checkout's HEAD, which falsely reported merged branches "not fully
    merged" — after destroying their checkouts).
    """
    tip = _git(["rev-parse", "--verify", f"refs/heads/{name}"], git_root)
    up = _git(["rev-parse", "--verify", "--quiet", f"{name}@{{upstream}}"], git_root)
    if up.returncode == 0:
        if tip.returncode == 0 and not _is_ancestor(
            git_root, tip.stdout.strip(), up.stdout.strip()
        ):
            return (
                "tracks an upstream that does not contain it — git branch -d "
                "would refuse after the checkout was gone; reconcile the "
                "upstream first"
            )
        return ""  # upstream contains it: git deletes regardless of HEAD
    if tip.returncode == 0 and not _is_ancestor(git_root, tip.stdout.strip(), "HEAD"):
        return (
            "not contained in the PRIMARY checkout's HEAD, which is the HEAD "
            "`git branch -d` judges against — it would refuse after the "
            "checkout was gone; merge it there (or into an upstream) first"
        )
    return ""


def prune_branches(
    repo_root: Path, *, base: str = "main", yes: bool = False
) -> dict[str, Any]:
    """Remove exactly the ``prunable`` category (S16.8 / CIU-25).

    Every destructive git command runs from the PRIMARY worktree, never from
    the invoking checkout: ``git branch -d`` judges mergedness against the
    HEAD of the worktree it runs in, so invoking from a linked checkout that
    was behind used to report fully-merged branches as "not fully merged"
    while destroying their checkouts anyway (ciu-P28). It also means the
    prune's own cwd is a checkout no candidate can remove.

    Removal order per branch: the read-only refusal pre-checks
    (:func:`_prune_candidate_refusal`), then ``git worktree remove`` (Git
    re-verifies cleanliness and refuses dirt — belt to our braces), then
    ``git branch -d`` (Git re-verifies mergedness). A refusal ANYWHERE — a
    non-zero exit or an unexpected raise — moves that branch to *failed* WITH
    the reason and the prune continues to the remaining candidates; nothing
    escapes the loop, so a document is always returned. The overall status is
    ``pruned`` only when every prunable branch was removed. Without *yes* no
    side effect happens: the caller gets the survey with an explicit hint.
    """
    survey = branch_hygiene(repo_root, base=base)
    prunables = [b for b in survey["branches"] if b["category"] == "prunable"]
    if not yes:
        # branch_hygiene's hint already says what -y would do; a survey is
        # side-effect-free by construction.
        return survey

    # BLOCKER fix (review): the destructive pass demands a base git itself
    # agrees with, BEFORE anything is touched.
    _prune_base_sanity(repo_root, base)

    repo_root = Path(repo_root).resolve()
    git_root = primary_worktree_root(repo_root)
    removed: list[str] = []
    failed: list[dict[str, str]] = []
    for branch in prunables:
        name = branch["name"]
        try:
            failure = _prune_candidate_refusal(git_root, name)
            if not failure and branch["checkout"]:
                res = _git(["worktree", "remove", branch["checkout"]], git_root)
                if res.returncode != 0:
                    failure = (res.stderr or res.stdout).strip()
            if not failure:
                res = _git(["branch", "-d", name], git_root)
                if res.returncode != 0:
                    failure = (res.stderr or res.stdout).strip()
        except WorktreeError as exc:
            # One candidate's failure is never the whole operation's: the
            # review reproduced an unhandled mid-loop raise that returned NO
            # document at all and silently left every later candidate
            # unprocessed. Name it, keep going.
            failure = (
                f"unexpected git failure, remaining branches still processed: {exc}"
            )
        if failure:
            failed.append({"branch": name, "reason": failure})
        else:
            removed.append(name)

    # Re-survey AFTER the removals so the returned document reports the
    # post-prune truth (counts/branches), not the stale pre-prune snapshot.
    fresh = branch_hygiene(repo_root, base=base)
    survey["branches"] = fresh["branches"]
    survey["counts"] = fresh["counts"]
    survey["hint"] = fresh["hint"]
    survey["operation"] = "branches-prune"
    survey["status"] = "pruned" if not failed else "partial"
    survey["removed"] = removed
    survey["failed"] = failed
    return survey


# ---------------------------------------------------------------------------
# S16.10 — Docker-resource REAP (CIU-25, docker half): survey + destroy
# ---------------------------------------------------------------------------
#
# The structural twin of S16.8's branch hygiene, one layer down: a closed
# survey that classifies EVERY Docker resource group into exactly one
# category, and a SEPARATE `-y` pass that acts on exactly the categories a
# fact — never a heuristic — proves safe.
#
# The one rule that governs every line below: **CIU destroys only what it can
# PROVE it owns and PROVE nothing still claims.** Age, directory-basename
# similarity and "no process is running" appear nowhere in this file, by
# construction — the backlog entry (CIU-25) names all three as the wrong
# answer, because a long-lived worktree is a legitimate thing and a stopped
# instance is not an abandoned one. Ownership is DECLARED, by ciu-P26's lease
# and `ciu.instance` label, or it is not known; and what is not known is not
# destroyed.

REAP_SCHEMA_VERSION = 1

# Closed category vocabulary. Every surveyed resource group collapses into
# exactly one member — never zero, never two (the precedence rule that makes
# that true is :func:`_classify_reap_group`, read top to bottom):
#   owned            — attributable to a checkout that still exists and still
#                      claims it: a record whose lease is held/perpetual/
#                      unconfigured, OR (no record) a REGISTERED worktree whose
#                      own generated facts declare that instance_id. Never destroyed
#   lease-expired    — a readable record whose `held` lease's expires_at_utc is
#                      in the past at survey time (S16.9). The ONLY grounded
#                      "the operator's claim has lapsed" signal there is
#   checkout-missing — unclaimed (as `orphaned`) AND the group's own
#                      `ciu.repo-root` label names a directory that no longer
#                      exists. NOTE: this is a REFINEMENT of `orphaned`, not a
#                      separate licence — a group only reaches either test once
#                      no record and no checkout claims its id, which is what
#                      licenses removal; the label only decides which message
#                      the operator reads. The instance record lives INSIDE the
#                      checkout, so a vanished checkout takes its record with
#                      it: `ciu.repo-root` is the only durable, checkout-
#                      EXTERNAL evidence of where an instance used to live
#   orphaned         — resources labelled `ciu.instance=<id>` for an id that
#                      matches NO instance record and NO registered checkout,
#                      and whose recorded repo root does still exist (or was
#                      never labelled)
#   partial-cleanup  — the attributed record DECLARES state
#                      `recovery-required`: CIU itself wrote down that this
#                      instance's lifecycle did not complete. Deliberately
#                      NARROWER than the original carve, which also counted "a
#                      group with some (not all) of its resources present" —
#                      undecidable (nothing records what "all" would be) and
#                      catastrophic (`ciu down` preserves volumes on purpose,
#                      so a legitimately-stopped owned instance would have
#                      qualified). See the ciu-P27 handoff amendment
#   unattributable   — no `ciu.instance` label AND no identity-form compose
#                      project name: CIU cannot prove whose these are. NEVER
#                      destroyed, under any flag combination
#   ambiguous        — the attribution does not resolve to exactly one
#                      TRUSTWORTHY record: more than one identity claims the
#                      group, more than one record claims the identity, or the
#                      one record that does is contradicted by Git. NEVER
#                      destroyed, under any flag combination
REAP_CATEGORIES = (
    "owned", "lease-expired", "checkout-missing", "orphaned",
    "partial-cleanup", "unattributable", "ambiguous",
)

# The categories `-y` may act on, and the DEFAULT set when `--category` is
# absent (they are the same tuple on purpose: there is no category that is
# destructible-but-off-by-default, because an operator reading `--category`'s
# help must not have to discover a hidden extra). `owned`, `unattributable`
# and `ambiguous` are absent STRUCTURALLY, not by policy: `--category` refuses
# every name outside this tuple, so no flag combination can reach them.
REAP_DESTRUCTIBLE_CATEGORIES = (
    "checkout-missing", "lease-expired", "orphaned", "partial-cleanup",
)

# Closed document-status vocabulary: a side-effect-free survey, a `-y
# --dry-run` plan, a fully successful pass, and a pass where at least one
# targeted group was not disposed of (the CLI exits 1 on that last one).
REAP_STATUSES = frozenset({"survey", "dry-run", "reaped", "partial"})

_IDENTITY_NETWORK_SUFFIX = "-network"


def _ownership_label_keys() -> tuple[str, str]:
    """The ``(ciu.instance, ciu.repo-root)`` label keys ciu-P26 stamps on every
    managed instance's resources. ``engine`` owns that closed vocabulary
    (S16.9), and the import is lazy because ``engine`` imports THIS module."""
    from . import engine as engine_mod

    return (
        engine_mod.OWNERSHIP_LABEL_INSTANCE,
        engine_mod.OWNERSHIP_LABEL_REPO_ROOT,
    )


def _reap_docker_rows(args: list[str], *, what: str, fields: int) -> list[list[str]]:
    """One read-only, tab-separated Docker enumeration.

    Two failure modes, deliberately answered differently:

    * **docker is ABSENT** — a CIU workspace can legitimately be local-only.
      No Docker means no Docker resources, an empty enumeration is the honest
      answer, and an empty survey destroys nothing.
    * **docker is PRESENT and the query FAILED** — a refusal. A survey that
      silently under-reports is the input to a destructive pass, and the
      group it failed to see is exactly the one whose absence would let a
      shared network look unused.

    Every field is pulled with an explicit ``{{.Label "k"}}`` lookup rather
    than parsed out of the comma-joined ``{{.Labels}}`` blob: a label VALUE
    containing a comma would split that blob wrong, and a mis-parsed
    ``ciu.instance`` is a mis-attribution — the one error class this whole
    module exists to prevent.
    """
    try:
        res = procutil.docker(args, capture=True, check=False)
    except (FileNotFoundError, OSError):
        return []
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.10] could not enumerate {what}: "
            f"{(res.stderr or res.stdout or '').strip()} — refusing rather than "
            "surveying from an incomplete picture, because a group this query "
            "failed to see is a group a destructive pass would misjudge."
        )
    rows: list[list[str]] = []
    for line in (res.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != fields:
            raise WorktreeError(
                f"[S16.10] unparseable {what} row from docker (expected {fields} "
                f"tab-separated fields): {line!r}"
            )
        rows.append(parts)
    return rows


@dataclass(frozen=True)
class ReapIdentities:
    """Everything the survey knows about WHO could own Docker resources.

    ``records`` are the parsed instance records; ``findings`` are the
    inconsistencies met on the way (never raised — see
    :func:`survey_instance_records`); ``distrusted`` are the INSTANCE_IDs
    whose record Git contradicts; ``checkouts``/``networks`` map every
    registered checkout's INSTANCE_ID to its path and its identity network;
    ``unresolved`` names checkouts that carry a record FILE whose identity
    could not be established at all.
    """

    records: tuple[WorktreeInstanceRecord, ...]
    findings: tuple[dict[str, str], ...]
    distrusted: frozenset[str]
    checkouts: dict[str, str]
    networks: dict[str, str]
    unresolved: tuple[str, ...]


def _reap_record_inconsistencies(
    record: WorktreeInstanceRecord,
    wt: WorktreeInfo,
    offset: Path,
    seen_logical: dict[str, str],
) -> list[str]:
    """The same four cross-checks :func:`list_instance_records` RAISES on,
    rendered as strings so a survey can report them and continue."""
    out: list[str] = []
    if record.git_worktree_path.resolve() != wt.path.resolve():
        out.append(
            f"record claims Git path {record.git_worktree_path}, but Git "
            f"registers {wt.path}"
        )
    if record.ciu_root_offset != offset:
        out.append(
            f"record claims CIU-root offset {record.ciu_root_offset}, but this "
            f"family derives {offset}"
        )
    if record.branch != wt.branch:
        out.append(
            f"record claims branch {record.branch!r}, but Git registers "
            f"{wt.branch!r}"
        )
    if record.logical_name in seen_logical:
        out.append(
            f"duplicate logical worktree identity {record.logical_name!r} within "
            f"one Git family (also claimed by {seen_logical[record.logical_name]})"
        )
    return out


def survey_instance_records(repo_root: Path) -> ReapIdentities:
    """:func:`list_instance_records` for a DESTRUCTIVE reader — never raises.

    ``list_instance_records`` refuses the whole family on the first
    inconsistency it meets (branch mismatch, offset mismatch, duplicate
    logical identity, an unreadable file). That is exactly right for
    ``add``/``rm``/``inspect``, which act on ONE named instance and must not
    proceed over a contradiction. It is exactly wrong for a reap survey,
    which is most needed precisely when something is already broken: one bad
    record would blind the operator to every other instance on the host, and
    "the survey crashed" is the worst possible answer to "what is safe to
    delete?".

    So this sibling collects each inconsistency as a FINDING and keeps going.
    The findings are then load-bearing in two places, both of them in the
    SAFE direction:

    1. an inconsistent record can never license destruction — every group it
       would have attributed classifies ``ambiguous`` instead
       (:func:`_classify_reap_group`);
    2. a checkout carrying a record FILE whose identity cannot be established
       at all disarms the ``orphaned`` category outright (:func:`reap_groups`)
       — a corrupted record on a LIVE instance must never make that
       instance's labelled resources look unclaimed.

    Identity is also derived per checkout INDEPENDENTLY of the record, from
    that checkout's own ``[ciu.instance.generated]`` facts file by exact
    path (:func:`_runtime_identity`,
    never the ambient environment — the CIU-41 contamination species), so a
    live checkout whose record was deleted is still recognised as an owner.
    """
    repo_root = Path(repo_root).resolve()
    offset = _ciu_root_offset(repo_root)
    records: list[WorktreeInstanceRecord] = []
    findings: list[dict[str, str]] = []
    distrusted: set[str] = set()
    checkouts: dict[str, str] = {}
    networks: dict[str, str] = {}
    unresolved: list[str] = []
    seen_logical: dict[str, str] = {}

    for wt in list_worktrees(repo_root):
        ciu_root = wt.path / offset
        record_path = ciu_root / WORKTREE_INSTANCE_RECORD
        record: WorktreeInstanceRecord | None = None
        has_record_file = record_path.is_file()
        if has_record_file:
            try:
                record = read_instance_record(record_path)
            except WorktreeError as exc:
                findings.append({
                    "kind": "unreadable-record",
                    "path": str(record_path),
                    "detail": str(exc),
                })
            else:
                for detail in _reap_record_inconsistencies(
                    record, wt, offset, seen_logical
                ):
                    findings.append({
                        "kind": "inconsistent-record",
                        "path": str(record_path),
                        "detail": detail,
                    })
                    if record.instance_id:
                        distrusted.add(record.instance_id)
                seen_logical.setdefault(record.logical_name, str(record_path))
                records.append(record)

        identity: tuple[str, str] | None = None
        if record is not None and record.instance_id and record.network:
            identity = (record.instance_id, record.network)
        else:
            # No record, or an `allocating` one with no runtime identity yet:
            # the checkout's OWN generated `[ciu.instance.generated]` overlay
            # table is the authority (CIU-75), read by exact path. Absent/unreadable is not an error here — a checkout
            # that never generated an environment never stamped a label.
            try:
                identity = _runtime_identity(ciu_root)
            except WorktreeError:
                identity = None
        if identity is not None:
            checkouts[identity[0]] = str(wt.path)
            networks[identity[0]] = identity[1]
        elif has_record_file:
            # This checkout WAS a managed instance, so it may well have
            # stamped `ciu.instance` labels — and we cannot say which id.
            unresolved.append(str(wt.path))

    findings.sort(key=lambda f: (f["kind"], f["path"], f["detail"]))
    return ReapIdentities(
        records=tuple(records),
        findings=tuple(findings),
        distrusted=frozenset(distrusted),
        checkouts=checkouts,
        networks=networks,
        unresolved=tuple(unresolved),
    )


def _lease_is_expired(record: WorktreeInstanceRecord, instant: datetime) -> bool:
    """True only for a ``held`` lease whose expiry has PASSED (S16.9).

    Every other shape answers False, and every one of them is the safe
    direction: no lease at all (a v1 record, or a released v2 one) means the
    instance never participated in leasing or explicitly claims nothing —
    neither is evidence of abandonment; ``perpetual`` can never lapse by
    definition. An unparseable stored expiry is also False: the read path
    already refuses such a record (:func:`_parse_utc_timestamp`), and if one
    somehow reaches here, "I cannot read the claim" must never be rounded
    down to "there is no claim".
    """
    lease = record.lease
    if lease is None or lease.mode != "held" or lease.expires_at_utc is None:
        return False
    try:
        expires = _parse_utc_timestamp(
            lease.expires_at_utc, label="expires_at_utc", path=record.record_path
        )
    except WorktreeError:
        return False
    return expires <= instant


def _classify_reap_group(
    ids: set[str],
    repo_roots: set[str],
    *,
    records_by_id: dict[str, WorktreeInstanceRecord],
    duplicated_ids: set[str],
    identities: ReapIdentities,
    instant: datetime,
) -> tuple[str, str]:
    """The closed partition, as ONE ordered chain of first-match-wins rules.

    Written as a single function with early returns precisely so the
    precedence is readable top-to-bottom and a group cannot land in two
    buckets: the first rule that fires is the answer. The order encodes two
    policies — every un-provable attribution resolves to a NEVER-destroyed
    category before any destructible one is considered, and among the
    destructible ones the most operationally specific fact wins (a checkout
    that is GONE is a more actionable truth than a lease that lapsed, and
    `ciu clean` cannot run there either way).
    """
    if len(ids) > 1:
        return "ambiguous", (
            "more than one CIU identity claims this group ("
            + ", ".join(sorted(ids))
            + "); CIU will not guess which one owns it"
        )
    if not ids:
        return "unattributable", (
            "no `ciu.instance` label and no identity-form compose project name — "
            "CIU cannot prove whose these resources are, so it will never remove "
            "them; dispose of them by hand if you know better"
        )
    instance_id = next(iter(ids))
    if instance_id in duplicated_ids:
        return "ambiguous", (
            f"more than one instance record claims INSTANCE_ID {instance_id!r}"
        )
    if instance_id in identities.distrusted:
        return "ambiguous", (
            f"the record claiming INSTANCE_ID {instance_id!r} is contradicted by "
            "Git (see this document's `findings`); a record CIU cannot trust can "
            "never license destruction"
        )
    record = records_by_id.get(instance_id)
    if record is None:
        checkout = identities.checkouts.get(instance_id)
        if checkout is not None:
            return "owned", (
                f"no instance record claims {instance_id!r}, but the registered "
                f"checkout {checkout} declares that instance_id in its own "
                "generated identity facts — a live checkout still owns what it "
                "created"
            )
        if repo_roots and not any(Path(root).is_dir() for root in repo_roots):
            return "checkout-missing", (
                f"labelled ciu.instance={instance_id}, claimed by no record and "
                "no registered checkout, and its own ciu.repo-root label ("
                + ", ".join(sorted(repo_roots))
                + ") names a directory that is not present (deleted, or on a "
                "currently-unavailable mount — filesystem absence cannot "
                "distinguish the two) — `ciu clean` cannot run there right "
                "now. The Docker resources are this verb's half; a stale Git "
                "registration is `ciu worktree branches`' (or `git worktree "
                "prune`'s)"
            )
        return "orphaned", (
            f"labelled ciu.instance={instance_id}, which matches no instance "
            "record and no registered checkout in this Git family"
        )
    if record.state == "recovery-required":
        return "partial-cleanup", (
            f"instance {record.logical_name!r} DECLARES state "
            f"'recovery-required' ({record.recovery_status}) — CIU itself "
            "recorded that this instance's lifecycle did not complete"
        )
    if _lease_is_expired(record, instant):
        return "lease-expired", (
            f"instance {record.logical_name!r}'s held lease expired at "
            f"{record.lease.expires_at_utc if record.lease else '?'} "
            f"(holder {record.lease.holder if record.lease else '?'})"
        )
    return "owned", (
        f"instance {record.logical_name!r} is registered, its checkout exists, "
        "and nothing says its claim has lapsed"
    )


def _reap_group_documents(
    repo_root: Path, instant: datetime
) -> tuple[list[dict[str, Any]], ReapIdentities]:
    """Enumerate + classify every Docker resource group (the survey's core)."""
    identities = survey_instance_records(repo_root)
    instance_label, root_label = _ownership_label_keys()
    ownership = f'{{{{.Label "{instance_label}"}}}}\t{{{{.Label "{root_label}"}}}}'

    containers = _reap_docker_rows(
        [
            "ps", "-a", "--no-trunc", "--format",
            '{{.ID}}\t{{.Names}}\t{{.Label "com.docker.compose.project"}}\t'
            + ownership,
        ],
        what="containers", fields=5,
    )
    volumes = _reap_docker_rows(
        [
            "volume", "ls", "--format",
            '{{.Name}}\t{{.Label "com.docker.compose.project"}}\t' + ownership,
        ],
        what="volumes", fields=4,
    )
    networks = _reap_docker_rows(
        [
            "network", "ls", "--format",
            '{{.Name}}\t{{.Label "com.docker.compose.project"}}\t' + ownership,
        ],
        what="networks", fields=4,
    )

    raw: dict[str, dict[str, Any]] = {}

    def bucket(key: str, project: str | None) -> dict[str, Any]:
        return raw.setdefault(key, {
            "key": key, "compose_project": project,
            "containers": [], "volumes": [], "networks": [],
            "label_ids": set(), "repo_roots": set(),
        })

    # A resource with no compose project is not a GROUP member: `ciu up`
    # always deploys through compose, so anything compose never created is
    # not this verb's business and is left entirely alone (never surveyed,
    # therefore never destroyed).
    def claim(group: dict[str, Any], inst: str, root: str) -> None:
        if inst:
            group["label_ids"].add(inst)
        if root:
            group["repo_roots"].add(root)

    for cid, cname, project, inst, root in containers:
        if not project:
            continue
        group = bucket(project, project)
        group["containers"].append({"id": cid, "name": cname})
        claim(group, inst, root)
    for vname, project, inst, root in volumes:
        if not project:
            continue
        group = bucket(project, project)
        group["volumes"].append(vname)
        claim(group, inst, root)
    for nname, project, inst, root in networks:
        if not project:
            continue
        group = bucket(project, project)
        group["networks"].append(nname)
        claim(group, inst, root)

    # The IDENTITY network (S2.6 `{repo}-{id}-network`) is the one resource
    # compose never creates: `ciu env generate` makes it and every stack
    # declares it `external: true`, so it carries no compose project label and
    # ciu-P26 deliberately never labels it either. It is therefore attached by
    # NAME, and only ever to an identity CIU already knows — an unrecognised
    # loose network on the host is not evidence of anything and is ignored.
    network_owner = {name: iid for iid, name in identities.networks.items()}
    for nname, project, inst, root in networks:
        if project:
            continue
        owner = inst or network_owner.get(nname)
        if not owner:
            continue
        attached = [
            group for group in raw.values() if owner in group["label_ids"]
        ]
        if not attached:
            attached = [bucket(nname, None)]
            claim(attached[0], owner, root)
        for group in attached:
            group["networks"].append(nname)

    # Identity-form project-name attribution: `{repo}-{id}-{stack}`
    # (engine.identity_compose_project_name) for a config-less deployment,
    # whose containers a pre-P26 `ciu up` never labelled. Derived from the
    # identity network name, which already carries the same `{repo}-{id}`
    # prefix, so there is no second spelling of the convention to drift.
    prefixes = {
        iid: net[: -len(_IDENTITY_NETWORK_SUFFIX)]
        for iid, net in identities.networks.items()
        if net.endswith(_IDENTITY_NETWORK_SUFFIX)
    }
    records_by_id: dict[str, WorktreeInstanceRecord] = {}
    duplicated_ids: set[str] = set()
    for record in identities.records:
        if not record.instance_id:
            continue
        if record.instance_id in records_by_id:
            duplicated_ids.add(record.instance_id)
        records_by_id[record.instance_id] = record

    documents: list[dict[str, Any]] = []
    for group in raw.values():
        project = group["compose_project"]
        name_ids = {
            iid for iid, prefix in prefixes.items()
            if project and project.startswith(prefix + "-")
        }
        ids = set(group["label_ids"]) | name_ids
        category, reason = _classify_reap_group(
            ids, set(group["repo_roots"]), records_by_id=records_by_id,
            duplicated_ids=duplicated_ids, identities=identities,
            instant=instant,
        )
        instance_id = next(iter(ids)) if len(ids) == 1 else None
        record = records_by_id.get(instance_id) if instance_id else None
        documents.append({
            "key": group["key"],
            "compose_project": project,
            "category": category,
            "reason": reason,
            "instance_id": instance_id,
            "repo_roots": sorted(group["repo_roots"]),
            "logical_name": record.logical_name if record is not None else None,
            "state": record.state if record is not None else None,
            "ciu_root": str(record.ciu_root) if record is not None else None,
            "checkout_exists": (
                record.ciu_root.is_dir() if record is not None else False
            ),
            "lease": (
                record.lease.to_dict()
                if record is not None and record.lease is not None else None
            ),
            "containers": sorted(group["containers"], key=lambda c: c["id"]),
            "volumes": sorted(set(group["volumes"])),
            "networks": sorted(set(group["networks"])),
        })
    documents.sort(key=lambda g: (g["category"], g["key"]))
    return documents, identities


def _reap_hint(counts: Mapping[str, int], identity_complete: bool) -> str:
    destructible = sum(counts[c] for c in REAP_DESTRUCTIBLE_CATEGORIES)
    if destructible:
        hint = (
            f"{destructible} resource group(s) are provably disposable "
            f"({', '.join(f'{c}={counts[c]}' for c in REAP_DESTRUCTIBLE_CATEGORIES if counts[c])}); "
            "re-run with -y/--yes to reap them (add --dry-run first to see the "
            "exact commands)."
        )
    else:
        hint = "nothing provably disposable."
    if counts["unattributable"]:
        hint += (
            f" {counts['unattributable']} group(s) are unattributable and are "
            "NEVER reaped — CIU cannot prove whose they are. Inspect one with "
            "`docker ps -a --filter label=com.docker.compose.project=<project>` "
            "and remove it by hand with "
            "`docker compose -p <project> down -v` if you know it is yours."
        )
    if counts["ambiguous"]:
        hint += (
            f" {counts['ambiguous']} group(s) are ambiguous and are NEVER "
            "reaped — see each group's `reason` (and this document's "
            "`findings`) for the competing claims, and reconcile them first."
        )
    if not identity_complete:
        hint += (
            " At least one registered checkout carries an instance record CIU "
            "could not read an identity from, so the `orphaned` category is "
            "DISARMED for this run: an id that looks unclaimed might simply be "
            "the one that could not be read."
        )
    return hint


def survey_reap_groups(
    repo_root: Path, *, now: datetime | None = None
) -> dict[str, Any]:
    """Build the ``ciu worktree reap`` survey document (S16.10 / CIU-25).

    Side-effect-free by construction: every Docker call below is a read-only
    enumeration, and nothing on disk is written — a v1 instance record is not
    even re-serialized, let alone upgraded (S16.9 makes that structural).

    *now* is injectable and must be timezone-aware; lease expiry is evaluated
    against it and nothing else. Tests never call the real clock, and the
    document carries no timestamp of its own, so two consecutive surveys of an
    unchanged host are byte-identical.
    """
    repo_root = Path(repo_root).resolve()
    instant = now or _utc_now()
    if instant.tzinfo is None:
        raise WorktreeError(
            "[S16.10] the reap survey clock must be timezone-aware; a naive "
            "instant would compare against stored UTC expiries by up to a day "
            "of luck"
        )
    groups, identities = _reap_group_documents(repo_root, instant)
    counts = {c: sum(1 for g in groups if g["category"] == c) for c in REAP_CATEGORIES}
    identity_complete = not identities.unresolved
    return {
        "schema_version": REAP_SCHEMA_VERSION,
        "operation": "reap",
        "status": "survey",
        "identity_complete": identity_complete,
        "unresolved_checkouts": list(identities.unresolved),
        "counts": counts,
        "findings": [dict(f) for f in identities.findings],
        "hint": _reap_hint(counts, identity_complete),
        "groups": groups,
    }


def resolve_reap_categories(raw: str | None) -> tuple[str, ...]:
    """Parse ``--category C[,C...]`` into the closed destructible set.

    Every name outside :data:`REAP_DESTRUCTIBLE_CATEGORIES` is a REFUSAL,
    including the three real-but-protected categories. That is the whole
    safety property of this verb stated as code: ``--category unattributable``
    and ``--category ambiguous`` do not select a protected category, they fail
    the command, so there is no flag combination anywhere that reaches one.
    """
    if raw is None:
        return REAP_DESTRUCTIBLE_CATEGORIES
    names = tuple(dict.fromkeys(p.strip() for p in raw.split(",") if p.strip()))
    if not names:
        raise WorktreeError(
            "[S16.10] --category needs at least one category name; closed "
            f"selectable vocabulary: {', '.join(sorted(REAP_DESTRUCTIBLE_CATEGORIES))}"
        )
    for name in names:
        if name in REAP_DESTRUCTIBLE_CATEGORIES:
            continue
        if name in REAP_CATEGORIES:
            raise WorktreeError(
                f"[S16.10] refusing --category {name!r}: that category is never "
                "acted on by this verb, and there is deliberately no flag that "
                "forces it. CIU removes a resource group only when a record, a "
                "lease or an ownership label PROVES what it is; "
                f"{name!r} means exactly that no such proof exists. Selectable: "
                f"{', '.join(sorted(REAP_DESTRUCTIBLE_CATEGORIES))}."
            )
        raise WorktreeError(
            f"[S16.10] unknown --category {name!r}; selectable vocabulary: "
            f"{', '.join(sorted(REAP_DESTRUCTIBLE_CATEGORIES))}"
        )
    return names


def _reap_plan(group: Mapping[str, Any]) -> list[str]:
    """The exact remediation this group would receive, as operator commands."""
    if _reap_uses_clean(group):
        return [f"(cd {group['ciu_root']} && ciu clean -y)"]
    plan: list[str] = []
    if group["containers"]:
        plan.append(
            "docker rm -f " + " ".join(c["id"] for c in group["containers"])
        )
    if group["volumes"]:
        plan.append("docker volume rm " + " ".join(group["volumes"]))
    for network in group["networks"]:
        plan.append(
            f"docker network rm {network}  # only if no container is still joined"
        )
    return plan or ["# nothing to remove"]


def _reap_uses_clean(group: Mapping[str, Any]) -> bool:
    """True when this group's checkout can still clean itself.

    `ciu clean` is authoritative: it knows the rendered config, the `vol-*`
    hostdirs and the root-helper path a bare `docker rm` knows nothing about.
    The ciu-P28 hotfix lesson binds this — a reap that touches a MANAGED
    instance goes through clean-then-remove, never a bare resource deletion —
    so the direct path below is reached ONLY when there is no checkout left to
    run it in.

    CIU-75: the readiness signal is the checkout's ``[ciu.instance.generated]``
    facts file, NOT its ``ciu.env``. `ciu clean` derives the identity
    network and the identity compose project from that table now, so a
    checkout carrying only a legacy `ciu.env` can no longer clean itself and
    answering True for one would send this reap into a `_clean_in` that must
    refuse. Presence, not readability: a present-but-corrupt table still
    answers True here so `_clean_in` refuses loudly, rather than this
    predicate quietly demoting indeterminacy to a bare docker removal.

    **What False actually costs, stated because it is not a refusal.** The
    caller falls through to `docker rm -f` + volume/network removal. That
    disposes of the docker resources and NOTHING else: no hostdir removal, no
    root-helper path, so `vol-*` data stays on disk. `_reap_one_group` says so
    in its notes when the checkout still exists — a reap that quietly leaves
    data behind is worse than one that refuses, and it must not be silent.
    """
    from .workspace_env import has_generated_facts

    ciu_root = group.get("ciu_root")
    if not ciu_root:
        return False
    return has_generated_facts(Path(ciu_root))


def _docker_reap(args: list[str], *, what: str) -> str:
    """Run one DESTRUCTIVE docker command; return "" or the real error text."""
    try:
        res = procutil.docker(args, capture=True, check=False)
    except (FileNotFoundError, OSError) as exc:
        return f"{what}: {exc}"
    if res.returncode != 0:
        return f"{what}: {(res.stderr or res.stdout or '').strip()}"
    return ""


def _reap_network(
    network: str, removed_ids: set[str], blocked_by: list[str]
) -> tuple[str, str]:
    """Remove one network, or say why it was left standing. -> (note, failure).

    Two independent guards, either of which alone means "leave it":

    1. any container STILL joined that this pass did not just remove — the
       shared-infra case (S16.1), where another instance's containers hold a
       second membership on this network. Tearing it down would break a live
       instance that never asked to be reaped;
    2. any OTHER surveyed group of the same identity not yet disposed of —
       one workspace's several stacks share one identity network, and the last
       one out turns off the light.
    """
    if blocked_by:
        return (
            f"network {network} left standing: still needed by "
            + ", ".join(sorted(blocked_by)),
            "",
        )
    if not _docker_network_exists(network):
        return f"network {network} was already gone", ""
    joined = _network_container_ids(network) - removed_ids
    if joined:
        return (
            f"network {network} left standing: {len(joined)} container(s) from "
            "another instance are still joined to it "
            f"({', '.join(sorted(joined))})",
            "",
        )
    failure = _docker_reap(["network", "rm", network], what=f"network rm {network}")
    if failure:
        return "", failure
    return f"removed network {network}", ""


def _reap_one_group(
    group: Mapping[str, Any], blocked_networks_by: Mapping[str, list[str]]
) -> tuple[list[str], str]:
    """Dispose of exactly one group. -> (notes, failure-reason-or-"").

    Strict order — containers, then volumes, then networks — and the first
    failure ABORTS this group: a volume still attached to a container we
    failed to remove cannot be removed either, and reporting the second,
    derived error would bury the real one.
    """
    notes: list[str] = []
    if _reap_uses_clean(group):
        try:
            rc = _clean_in(Path(str(group["ciu_root"])), yes=True)
        except WorktreeError as exc:
            return notes, f"`ciu clean` could not run in {group['ciu_root']}: {exc}"
        if rc != 0:
            return notes, (
                f"`ciu clean` failed (exit {rc}) in {group['ciu_root']}; the "
                "instance's own teardown is authoritative and CIU will not "
                "second-guess it with a bare docker removal"
            )
        return [f"cleaned in {group['ciu_root']}"], ""

    # CIU-75, review round 1: say so when a checkout that STILL EXISTS is being
    # disposed of the blunt way. `_reap_uses_clean` answering False sends this
    # group down the bare-docker path — it does not refuse — so hostdir removal
    # and the root-helper never run and `vol-*` data stays on disk. Before the
    # cutover that happened to a checkout with no `ciu.env`; now it happens to
    # one with no generated table, which is a state an upgrade can produce.
    # Silence here is the estate's own "data left on disk, nobody told" defect.
    _reap_root = group.get("ciu_root")
    if _reap_root and Path(str(_reap_root)).is_dir():
        from .workspace_env import GENERATED_FACTS_HEADER

        notes.append(
            f"{_reap_root} has no {GENERATED_FACTS_HEADER} table, so `ciu clean` "
            "could not run there: docker resources were removed directly and "
            "hostdir/root-helper cleanup did NOT run. Run `ciu env generate` in "
            "that checkout and `ciu clean` if vol-* data remains."
        )

    removed_ids = {c["id"] for c in group["containers"]}
    if group["containers"]:
        failure = _docker_reap(
            ["rm", "-f", *sorted(removed_ids)], what="container rm",
        )
        if failure:
            return notes, failure
        notes.append(f"removed {len(removed_ids)} container(s)")
    if group["volumes"]:
        failure = _docker_reap(
            ["volume", "rm", *group["volumes"]], what="volume rm",
        )
        if failure:
            return notes, failure
        notes.append(f"removed {len(group['volumes'])} volume(s)")
    for network in group["networks"]:
        note, failure = _reap_network(
            network, removed_ids, blocked_networks_by.get(network, [])
        )
        if failure:
            return notes, failure
        notes.append(note)
    return notes, ""


def reap_groups(
    repo_root: Path,
    *,
    yes: bool = False,
    categories: str | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Survey, and with *yes* reap exactly the provably-disposable groups.

    Without *yes* this is :func:`survey_reap_groups` verbatim — no side
    effect of any kind, the same contract ``worktree branches`` has.

    With *yes*, one group's failure is never the whole sweep's: it lands in
    ``failed`` with the real error text and every other targeted group is
    still processed, so a document is always returned. The returned document
    is then a RE-SURVEY of the post-state, not the pre-pass plan — what an
    operator needs after a destructive verb is what is true NOW.
    """
    # Validated BEFORE anything is enumerated: `--category ambiguous` must
    # fail as the refusal it is, not after a Docker error incidentally
    # produced some other exit code.
    active = resolve_reap_categories(categories)
    survey = survey_reap_groups(repo_root, now=now)
    if not yes:
        return survey

    targets = [g for g in survey["groups"] if g["category"] in active]
    if dry_run:
        survey["status"] = "dry-run"
        survey["categories"] = list(active)
        survey["plan"] = [
            {"group": g["key"], "category": g["category"], "commands": _reap_plan(g)}
            for g in targets
        ]
        return survey

    # A network is shared by every group of one identity; a group is a blocker
    # for it until that group has been successfully disposed of.
    # An UNTARGETED group (an `owned` sibling stack, say) never leaves this
    # map, so it blocks its identity network for the whole pass.
    pending: dict[str, set[str]] = {}
    for group in survey["groups"]:
        for network in group["networks"]:
            pending.setdefault(network, set()).add(group["key"])

    reaped: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for group in targets:
        blocked = {
            network: sorted(pending.get(network, set()) - {group["key"]})
            for network in group["networks"]
        }
        if group["category"] == "orphaned" and not survey["identity_complete"]:
            failed.append({"group": group["key"], "reason": (
                "refusing to reap an `orphaned` group while "
                + ", ".join(survey["unresolved_checkouts"])
                + " carries an instance record CIU could not read an identity "
                "from: an id that looks unclaimed may simply be the one that "
                "could not be read. Repair or remove that record first."
            )})
            continue
        try:
            notes, failure = _reap_one_group(group, blocked)
        except WorktreeError as exc:
            # Nothing escapes this loop: an unexpected refusal mid-sweep must
            # not leave every later group silently unprocessed (the ciu-P28
            # defect shape, one layer down).
            notes, failure = [], (
                f"unexpected failure, remaining groups still processed: {exc}"
            )
        if failure:
            failed.append({"group": group["key"], "reason": failure})
            continue
        for network in group["networks"]:
            pending.get(network, set()).discard(group["key"])
        reaped.append({"group": group["key"], "notes": notes})

    fresh = survey_reap_groups(repo_root, now=now)
    survey["groups"] = fresh["groups"]
    survey["counts"] = fresh["counts"]
    survey["findings"] = fresh["findings"]
    survey["hint"] = fresh["hint"]
    survey["identity_complete"] = fresh["identity_complete"]
    survey["unresolved_checkouts"] = fresh["unresolved_checkouts"]
    survey["status"] = "reaped" if not failed else "partial"
    survey["categories"] = list(active)
    survey["reaped"] = reaped
    survey["failed"] = failed
    return survey


# ---------------------------------------------------------------------------
# S16.6 — exact selected-worktree control (`worktree up` / `worktree exec`)
# ---------------------------------------------------------------------------

# Ambient environment keys that describe SOME CIU instance (root, identity,
# network, profile). They are stripped from the child environment so a
# sibling checkout's values can never contaminate the selected instance.
#
# CIU-85: the identity half is DERIVED from `GENERATED_FACT_ENV_KEYS` (the
# canonical fact->env-name table, `workspace_env.py`) instead of a second,
# hand-maintained literal — the same "two lists that must agree" shape
# CIU-75 already removed elsewhere by deriving `LEGACY_IDENTITY_ENV_KEYS`
# from the identical table. This is also how `PUBLIC_FQDN` — one of the six
# identity facts since CIU-47, but absent from the OLD hand-written five-plus-
# one list here — joins BY CONSTRUCTION rather than needing to be remembered.
# `CIU_SERVICES_PROFILE` is not a `[ciu.instance.generated]` fact (it is a CLI
# selection, not a workspace identity value) and stays the one hand-added
# member on top.
_CIU_IDENTITY_ENV_KEYS = tuple(GENERATED_FACT_ENV_KEYS.values()) + (
    "CIU_SERVICES_PROFILE",
)

# CIU-75: the exact `[ciu.instance.generated]` facts required to identify the
# selected record/root. `public_fqdn` is deliberately NOT here — CIU derives it
# only when the workspace declares one, so an empty value is legitimate and
# requiring it would refuse every FQDN-less instance.
_REQUIRED_TARGET_FACTS = (
    "repo_root",
    "physical_repo_root",
    "instance_id",
    "network",
    "repo_name",
)


def _require_ready_record(
    repo_root: Path, logical_name: str
) -> WorktreeInstanceRecord:
    """One exact ready managed record, or a refusal (missing or not ready)."""
    record = find_instance_record(repo_root, logical_name)
    if record is None:
        raise WorktreeError(
            f"[S16] no managed worktree instance named {logical_name!r} under "
            f"{repo_root}; `ciu worktree list` shows what exists."
        )
    if record.state != "ready":
        raise WorktreeError(
            f"[S16] instance {logical_name!r} is {record.state}, not ready; "
            f"resume it with `ciu worktree ensure {logical_name}`"
        )
    return record


def _sanitized_target_env(
    repo_root: Path, record: WorktreeInstanceRecord
) -> dict[str, str]:
    """The child environment for the selected instance (S16.6).

    Ambient process environment MINUS every CIU root/identity/network/profile
    key, then overlaid with the target's OWN ``[ciu.instance.generated]``
    facts read by exact path (CIU-75 — the overlay is the sole instance-fact
    source; the target's legacy ``ciu.env`` is never consulted) — never
    sourced through a shell, never inherited from a sibling. Those facts must
    identify the selected record/root: a missing fact, or a repo_root /
    instance_id / network that disagrees with the record, is a refusal, never
    an invented fallback.
    """
    env = {k: v for k, v in os.environ.items() if k not in _CIU_IDENTITY_ENV_KEYS}
    from .workspace_env import (
        WorkspaceEnvError,
        generated_facts_path,
        identity_env_from_facts,
        read_generated_facts,
    )

    facts_path = generated_facts_path(record.ciu_root)
    try:
        facts = read_generated_facts(record.ciu_root)
    except WorkspaceEnvError as exc:
        raise WorktreeError(f"[S16] could not read {facts_path}: {exc}") from exc
    missing = [k for k in _REQUIRED_TARGET_FACTS if not facts.get(k)]
    if missing:
        raise WorktreeError(
            f"[S16] {facts_path} lacks required identity fact(s): "
            f"{', '.join(missing)}"
        )
    if Path(facts["repo_root"]).resolve() != record.ciu_root.resolve():
        raise WorktreeError(
            f"[S16] {facts_path} repo_root {facts['repo_root']!r} does not "
            f"match the selected instance's CIU root {record.ciu_root}"
        )
    if facts["instance_id"] != record.instance_id:
        raise WorktreeError(
            f"[S16] {facts_path} instance_id {facts['instance_id']!r} does "
            f"not match the selected record's {record.instance_id!r}"
        )
    if facts["network"] != record.network:
        raise WorktreeError(
            f"[S16] {facts_path} network {facts['network']!r} does not "
            f"match the selected record's {record.network!r}"
        )
    env.update(identity_env_from_facts(facts))
    return env


def _run_child(
    argv: list[str], cwd: Path, env: Mapping[str, str]
) -> subprocess.CompletedProcess:
    """Run *argv* (no shell) in *cwd* under *env*; return the raw result.

    The single subprocess seam for `worktree up`/`worktree exec`, so tests
    replace exactly this call — never the whole subprocess module, which would
    also swallow the git plumbing.
    """
    return subprocess.run(list(argv), cwd=str(cwd), env=dict(env), check=False)


def up_instance(repo_root: Path, logical_name: str) -> int:
    """``ciu worktree up LOGICAL`` — start the selected ready instance exactly.

    Reads that instance's OWN ``[ciu.instance.generated]`` facts by
    exact path, builds the
    sanitized child environment (:func:`_sanitized_target_env`), and invokes
    CIU's existing up entry point as a subprocess in the instance's CIU root —
    never in-process, so the target's own ``REPO_ROOT``/identity rule honestly.
    Returns the child's exact exit code; a missing/not-ready record, an
    invalid/mismatched target env, or a child-start failure refuses loudly.
    """
    repo_root = Path(repo_root).resolve()
    record = _require_ready_record(repo_root, logical_name)
    env = _sanitized_target_env(repo_root, record)
    import sys

    argv = [sys.executable, "-m", "ciu.cli", "up"]
    try:
        res = _run_child(argv, record.ciu_root, env)
    except OSError as exc:
        raise WorktreeError(
            f"[S16] could not run `ciu up` in {record.ciu_root}: {exc}"
        ) from exc
    return res.returncode


def exec_instance(repo_root: Path, logical_name: str, argv: list[str]) -> int:
    """``ciu worktree exec LOGICAL -- ARGV...`` — run exact argv WITHOUT a
    shell in the selected ready instance's CIU root, under the sanitized
    target environment. Never starts/cleans/renders anything implicitly.
    Returns the child's exact exit code (never a wrapper-masked value).
    """
    repo_root = Path(repo_root).resolve()
    record = _require_ready_record(repo_root, logical_name)
    if not argv or argv[0] != "--":
        raise WorktreeError(
            "[S16] `ciu worktree exec LOGICAL -- ARGV...` requires a `--` "
            "separator and at least one argv element"
        )
    child_argv = argv[1:]
    if not child_argv:
        raise WorktreeError(
            "[S16] exec requires at least one argv element after `--`"
        )
    env = _sanitized_target_env(repo_root, record)
    try:
        res = _run_child(child_argv, record.ciu_root, env)
    except OSError as exc:
        raise WorktreeError(
            f"[S16] could not run exec argv in {record.ciu_root}: {exc}"
        ) from exc
    return res.returncode


# ---------------------------------------------------------------------------
# S16.7 — declared worktree container targets (`exec --target`)
# ---------------------------------------------------------------------------

_EXEC_TARGET_KEYS = frozenset({"stack", "service", "workdir", "requires_worktree_mount"})


@dataclass(frozen=True)
class ExecTarget:
    """One declared ``[ciu.worktree.exec_targets.<alias>]`` entry (S16.7).

    The alias is a Git-safe single component; ``stack``/``service``/``workdir``
    are required non-empty strings; ``requires_worktree_mount`` is a boolean
    defaulting to true (false is the only opt-out). Exactly these four keys
    exist — there is no arbitrary service-selection escape hatch.
    """

    alias: str
    stack: str
    service: str
    workdir: str
    requires_worktree_mount: bool


def parse_exec_targets(raw: Mapping[str, Any]) -> dict[str, ExecTarget]:
    """Validate ``[ciu.worktree.exec_targets]`` and return ``alias ->
    ExecTarget``. Unknown keys, unknown aliases, empty strings, or malformed
    booleans refuse loudly.
    """
    targets: dict[str, ExecTarget] = {}
    for alias, entry in raw.items():
        _validate_name(alias, label="exec target alias")
        if not isinstance(entry, Mapping):
            raise WorktreeError(f"[S16.7] exec target {alias!r} must be a table")
        unknown = set(entry) - _EXEC_TARGET_KEYS
        if unknown:
            raise WorktreeError(
                f"[S16.7] exec target {alias!r} has unknown key(s): "
                f"{', '.join(sorted(unknown))}"
            )
        for key in ("stack", "service", "workdir"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise WorktreeError(
                    f"[S16.7] exec target {alias!r} requires a non-empty "
                    f"string '{key}'"
                )
        requires = entry.get("requires_worktree_mount", True)
        if not isinstance(requires, bool):
            raise WorktreeError(
                f"[S16.7] exec target {alias!r} 'requires_worktree_mount' must "
                f"be a boolean, got {requires!r}"
            )
        targets[alias] = ExecTarget(
            alias=alias,
            stack=entry["stack"],
            service=entry["service"],
            workdir=entry["workdir"],
            requires_worktree_mount=requires,
        )
    return targets


def resolve_exec_targets_config(
    global_config: Mapping[str, Any],
) -> dict[str, ExecTarget]:
    """Extract and validate ``[ciu.worktree.exec_targets]`` from a rendered
    global config; empty mapping when none is declared."""
    ciu = global_config.get("ciu", {})
    if not isinstance(ciu, dict):
        raise WorktreeError("[S16.7] [ciu] must be a table")
    worktree_cfg = ciu.get("worktree")
    if worktree_cfg is None:
        return {}
    if not isinstance(worktree_cfg, dict):
        raise WorktreeError("[S16.7] [ciu.worktree] must be a table")
    raw_targets = worktree_cfg.get("exec_targets")
    if raw_targets is None:
        return {}
    if not isinstance(raw_targets, dict):
        raise WorktreeError("[S16.7] [ciu.worktree.exec_targets] must be a table")
    return parse_exec_targets(raw_targets)


def _resolve_target_container(project: str, service: str, network: str) -> str:
    """Exactly one RUNNING container for the selected compose project/service
    on the selected instance's own network — never a substring match, and
    never an implicit `up`. Zero or multiple matches refuse.
    """
    try:
        res = procutil.docker(
            [
                "ps",
                "--filter", f"label=com.docker.compose.project={project}",
                "--filter", f"label=com.docker.compose.service={service}",
                "--filter", f"network={network}",
                "--format", "{{.ID}}",
            ],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise WorktreeError(
            f"[S16.7] could not query target container for project {project!r} "
            f"service {service!r} on network {network!r}: {exc}"
        ) from exc
    ids = [line for line in (res.stdout or "").splitlines() if line.strip()]
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.7] `docker ps` failed for target project {project!r} "
            f"service {service!r}: {(res.stderr or res.stdout or '').strip()}"
        )
    if len(ids) != 1:
        raise WorktreeError(
            f"[S16.7] expected exactly one running container for project "
            f"{project!r} service {service!r} on network {network!r}, found "
            f"{len(ids)}; no `up` is ever started implicitly"
        )
    return ids[0]


def _workdir_within(workdir: str, destination: str) -> bool:
    """True when the container *workdir* equals *destination* or is a strict
    subdirectory of it (path-component comparison, no filesystem access)."""
    work = workdir.rstrip("/")
    dest = destination.rstrip("/")
    return work == dest or work.startswith(dest + "/")


def _verify_worktree_mount(
    container_id: str,
    record: WorktreeInstanceRecord,
    workdir: str,
    env: Mapping[str, str],
) -> None:
    """Require a bind mount whose HOST source is the selected Git worktree's
    physical path and whose CONTAINER destination contains the declared
    workdir (S16.7).

    Docker's own ``inspect`` output is the ONLY namespace authority: the
    host-side ``Source`` is compared against the physical (host-namespace)
    translation of the record's Git path (derived with the target's own
    REPO_ROOT/PHYSICAL_REPO_ROOT), and the container-side ``Destination``
    against the declared workdir. No local filesystem predicate is ever run on
    a path belonging to the other namespace.
    """
    try:
        res = procutil.docker(
            ["inspect", "--format", "{{json .Mounts}}", container_id],
            capture=True, check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        raise WorktreeError(
            f"[S16.7] could not inspect target container {container_id} "
            f"mounts: {exc}"
        ) from exc
    if res.returncode != 0:
        raise WorktreeError(
            f"[S16.7] `docker inspect` failed for {container_id}: "
            f"{(res.stderr or res.stdout or '').strip()}"
        )
    try:
        mounts = json.loads(res.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise WorktreeError(
            f"[S16.7] `docker inspect` returned unparseable mounts for "
            f"{container_id}"
        ) from exc
    physical = os.path.normpath(str(to_physical_path(
        record.git_worktree_path,
        repo_root=Path(env["REPO_ROOT"]),
        physical_root=Path(env["PHYSICAL_REPO_ROOT"]),
    )))
    for mount in mounts:
        if not isinstance(mount, dict):
            continue
        source = mount.get("Source")
        destination = mount.get("Destination")
        if not isinstance(source, str) or not isinstance(destination, str):
            continue
        if os.path.normpath(source) != physical:
            continue
        if _workdir_within(workdir, destination):
            return
    raise WorktreeError(
        f"[S16.7] target container {container_id} does not mount the selected "
        f"worktree {physical} at a path containing workdir {workdir!r}"
    )


def exec_target_instance(
    repo_root: Path, logical_name: str, alias: str, argv: list[str]
) -> int:
    """``ciu worktree exec LOGICAL --target ALIAS -- ARGV...`` — run exact
    argv (no shell) inside the ONE already-running container for the declared
    target of the selected instance.

    All config/selection/mount validation happens BEFORE any Docker
    execution; `up` is never started implicitly. The selected target's own
    global chain is rendered (without writing) under the instance's exact
    environment; the exact compose project/service/network identity is
    derived with the existing naming rule; exactly one running container must
    match; the worktree-mount proof is mandatory by default. Returns the exact
    ``docker exec`` exit code.
    """
    repo_root = Path(repo_root).resolve()
    record = _require_ready_record(repo_root, logical_name)
    env = _sanitized_target_env(repo_root, record)
    if not argv or argv[0] != "--":
        raise WorktreeError(
            "[S16.7] `ciu worktree exec LOGICAL --target ALIAS -- ARGV...` "
            "requires a `--` separator and at least one argv element"
        )
    child_argv = argv[1:]
    if not child_argv:
        raise WorktreeError(
            "[S16.7] exec --target requires at least one argv element after `--`"
        )

    global_config = config_model.render_global_chain(
        record.ciu_root, record.ciu_root, write_rendered=False, environ=env
    )
    targets = resolve_exec_targets_config(global_config)
    target = targets.get(alias)
    if target is None:
        declared = ", ".join(sorted(targets)) or "(none)"
        raise WorktreeError(
            f"[S16.7] no exec target alias {alias!r} declared in "
            f"{record.ciu_root}; declared: {declared}"
        )

    from . import engine

    stack = record.ciu_root / target.stack
    try:
        project = engine.compose_project_name(global_config, stack)
    except ValueError as exc:
        raise WorktreeError(
            f"[S16.7] could not derive the compose project for target "
            f"{alias!r}: {exc}"
        ) from exc
    network = env["DOCKER_NETWORK_INTERNAL"]

    container_id = _resolve_target_container(project, target.service, network)
    if target.requires_worktree_mount:
        _verify_worktree_mount(container_id, record, target.workdir, env)

    # NO `--` in the docker argv: `docker exec` stops option-parsing at the
    # CONTAINER positional, so a `--` after it would be executed AS the
    # command inside the container (measured live at review: exit 127,
    # `exec: "--": executable file not found`). The CLI-level `--` separator
    # was already consumed by the parser; child_argv is verbatim.
    docker_argv = ["exec", "-w", target.workdir, container_id, *child_argv]
    try:
        res = procutil.docker(docker_argv, capture=False, check=False)
    except (FileNotFoundError, OSError) as exc:
        raise WorktreeError(
            f"[S16.7] could not run `docker exec` in {container_id}: {exc}"
        ) from exc
    return res.returncode


def _branch_exists(repo_root: Path, branch: str) -> bool:
    return _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], repo_root).returncode == 0


def _runtime_identity(ciu_root: Path) -> tuple[str, str]:
    """The (instance_id, network) *ciu env generate* just derived for *ciu_root*.

    CIU-75: read from that checkout's own ``[ciu.instance.generated]`` facts
    file, the sole instance-fact source since 7.7.0. The legacy ``ciu.env``
    export is written by the same generate and carries the same two values,
    but is no longer read back by anything inside CIU.
    """
    from .workspace_env import (
        WorkspaceEnvError,
        generated_facts_path,
        read_generated_facts,
    )

    facts_path = generated_facts_path(ciu_root)
    try:
        facts = read_generated_facts(ciu_root)
    except WorkspaceEnvError as exc:
        raise WorktreeError(
            f"[S16] could not read generated runtime identity from "
            f"{facts_path}: {exc}"
        ) from exc
    instance_id = facts.get("instance_id", "")
    network = facts.get("network", "")
    if not instance_id or not network:
        raise WorktreeError(
            f"[S16] {facts_path} lacks instance_id or network"
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
    shared_infra_ref_services: str | None = None,
) -> WorktreeInstanceRecord:
    """Create a new managed worktree after family-wide collision admission.

    Creates ``<primary-git-root>/<worktree_dir>/<display>`` off
    *base*, then generates that checkout's own identity facts — which is what gives
    it a distinct ``INSTANCE_ID``, network and container prefix (S2).
    Worktree-local configuration is persisted separately in
    ``ciu.global.instance.toml.j2`` and survives env regeneration and clean.

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
    overlay-declared network, every declared reference project actually running on
    it) BEFORE any side effect; `create` records the resolved intent into the
    new worktree's own local config overlay but never joins anything itself — the actual
    ``docker network connect`` calls happen later, at ``ciu up`` time, in the
    new worktree's own process (see :func:`connect_shared_infra_after_up`).
    This instance keeps its OWN ``DOCKER_NETWORK_INTERNAL`` throughout; only
    the declared diverging services later gain a SECOND membership.

    *shared_infra_ref_services* (S16.1/CIU-52) is OPTIONAL and, unlike the
    three above, describes the REFERENCE's services rather than this
    instance's own: ``alias[,alias=ref_service]``. For each, CIU derives the
    reference's qualified container name from the reference's OWN rendered
    config, authenticates it against live Docker state, and records it as this
    instance's ``topology.services.<alias>.internal_host``. Supplying it still
    requires the rest of the group; omitting it changes nothing.
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
    if (
        shared_infra is not None or shared_infra_services is not None
        or shared_infra_ref_projects is not None
        or shared_infra_ref_services is not None
    ):
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
            shared_infra_ref_services=shared_infra_ref_services,
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
    shared_infra_ref_services: str | None = None,
) -> WorktreeInstanceRecord:
    """Explicitly take ownership of one registered, unmanaged linked checkout."""
    repo_root = Path(repo_root).resolve()
    _validate_name(logical_name, label="logical name")
    shared_infra_intent: SharedInfraIntent | None = None
    if (
        shared_infra is not None or shared_infra_services is not None
        or shared_infra_ref_projects is not None
        or shared_infra_ref_services is not None
    ):
        if not (shared_infra and shared_infra_services and shared_infra_ref_projects and profile):
            raise WorktreeError(
                "[S16.1] adopt shared-infra options and --profile are all-or-nothing"
            )
        shared_infra_intent = _preflight_shared_infra_for_add(
            repo_root, shared_infra=shared_infra,
            shared_infra_services=shared_infra_services,
            shared_infra_ref_projects=shared_infra_ref_projects,
            shared_infra_ref_services=shared_infra_ref_services,
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
        if (record.ciu_root / GLOBAL_CONFIG_INSTANCE_OVERRIDES).exists() and (
            profile or shared_infra_intent is not None
        ):
            _mark_recovery(record, "env-generation-failed")
            raise WorktreeError(
                f"[S16] {record.ciu_root} already has an instance override; "
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

    # S16.9 — ON SUCCESS ONLY. The lease is the evidence that something still
    # owns this instance's Docker resources, so it is cleared exactly when the
    # clean that removed them SUCCEEDED (rc == 0). A failed clean reaching here
    # means --force was passed: the checkout is about to be destroyed with
    # resources possibly still standing, which is precisely when the ownership
    # record must NOT be erased.
    if rc == 0:
        release_own_lease(ciu_root)

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
       explicit ``[ciu.instance.generated]`` facts; require their
       ``network`` still
       equal *intent.network* (catches a stale recording); refuse any
       declared reference project equal to *compose_project* (a reference
       must belong to the OTHER instance); then re-run the same AND-combined
       reference-liveness check `add` used, so a reference stopped between
       verbs is caught here too.
    2. (S16.1/CIU-52) For every recorded *intent.ref_services* entry, re-run
       the SAME live-name query `add` authenticated the derivation with and
       require the recorded container to still be present under that service
       label on the reference network — an `add`-time record is write-once, so
       a reference re-created under a new identity is caught here rather than
       silently addressed. A no-op when none is declared.
    3. For every declared service, require at least one RUNNING container in
       *compose_project* carrying that service's label — a previously joined
       child masquerading as live infra never satisfies this because it
       carries its OWN project's label, not the reference's.
    4. Only once every precondition above holds does this snapshot the
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
    from .workspace_env import WorkspaceEnvError, read_generated_facts

    ref = find_worktree(repo_root, str(intent.ref_path))
    if ref is None:
        raise WorktreeError(
            f"[S16.1] shared-infra reference {intent.ref_path} is no longer a "
            f"registered worktree under {repo_root}; it may have been removed. "
            "Restore it, re-run `ciu worktree add --shared-infra` to update the "
            "recorded reference, or `ciu down` this instance."
        )

    # CIU-75: the reference's CURRENT network is a fact read, so it comes from
    # that checkout's own `[ciu.instance.generated]` facts file.
    ref_ciu_root = ref.path / _ciu_root_offset(repo_root)
    try:
        ref_facts = read_generated_facts(ref_ciu_root)
    # CIU-62's three distinct failures — the read (`OSError`), a non-UTF-8 byte
    # (`UnicodeDecodeError`) and a malformed table — still all refuse; the
    # reader normalizes them to `WorkspaceEnvError` at the seam so no call site
    # has to re-derive that the last two are sibling `ValueError` subclasses.
    except WorkspaceEnvError as exc:
        raise WorktreeError(f"[S16.1] could not read {ref_ciu_root}: {exc}") from exc

    current_network = ref_facts.get("network", "")
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

    # S16.1/CIU-52: every recorded reference-service address is re-proven
    # against live Docker state HERE, still inside the every-precondition-
    # before-any-side-effect region — a container name resolved at `add` time
    # is a write-once record, and the reference may have been re-created under
    # a new identity since. A no-op (and zero Docker calls) when none is
    # declared. Nothing has been connected yet, so a refusal here has nothing
    # to roll back.
    _authenticate_ref_services(intent.network, intent.ref_services, recorded=True)

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

# The CLOSED key vocabulary of `[ciu.worktree]`. An unknown key here has
# always been a hard refusal rather than a silent ignore, and stays one:
# a misspelled capacity or lease policy that quietly does nothing is exactly
# the "defaults are hazards" shape this estate refuses. `exec_targets`
# (S16.7's per-alias grammar, validated separately by
# `resolve_exec_targets_config`/`parse_exec_targets`) is a member of this
# same table too (CIU-69) — its own contents are NOT re-validated here, only
# its presence as a top-level key is accepted rather than refused.
WORKTREE_TABLE_KEYS = frozenset(
    {"max_concurrent_instances", "lease_ttl_hours", "exec_targets"}
)


def _validate_worktree_table(raw: Any) -> None:
    """Shape + closed-key check for one `[ciu.worktree]` table (S16.3/S16.9)."""
    if not isinstance(raw, Mapping):
        raise WorktreeError(
            f"[S16.3] [ciu.worktree] must be a table, got "
            f"{type(raw).__name__}: {raw!r}"
        )
    unknown = set(raw) - set(WORKTREE_TABLE_KEYS)
    if unknown:
        raise WorktreeError(
            f"[S16.3] unknown key(s) in [ciu.worktree]: "
            f"{', '.join(sorted(unknown))}"
        )


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
        _validate_worktree_table(raw)
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
    return resolve_max_concurrent_instances(_primary_worktree_table(repo_root))


def _primary_worktree_table(repo_root: Path) -> Any:
    """The PRIMARY CIU root's raw ``[ciu.worktree]`` table, or ``None``.

    One reader for the whole table, shared by the capacity cap (S16.3) and
    the lease TTL (S16.9), so the two policies can never disagree about WHICH
    checkout's configuration is authoritative.
    """
    repo_root = Path(repo_root).resolve()
    if _git(["rev-parse", "--show-toplevel"], repo_root).returncode != 0:
        return None
    root = primary_ciu_root(repo_root)
    try:
        root_global = config_model.render_global_chain(
            root, root, write_rendered=False
        )
    except ValueError as exc:
        if not str(exc).startswith("[ERROR] No global configuration found."):
            raise
        root_global = {}
    return root_global.get("ciu", {}).get("worktree")


def resolve_lease_ttl_hours(raw: Mapping[str, Any] | None) -> float | None:
    """S16.9 — the declared ``[ciu.worktree].lease_ttl_hours``, or ``None``.

    ``None`` (the key absent) means NO lease is ever acquired by ``ciu up``
    for this project. That absence is the whole additive-safety story: a
    consumer who configures nothing keeps exactly today's behavior and takes
    on zero new expiry risk for anything already running. There is
    deliberately no non-zero default — a default TTL would start silently
    expiring leases on instances whose operators never opted in.
    """
    if raw is None:
        return None
    _validate_worktree_table(raw)
    if "lease_ttl_hours" not in raw:
        return None
    value = raw["lease_ttl_hours"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise WorktreeError(
            "[S16.9] [ciu.worktree] lease_ttl_hours must be a positive "
            f"number of hours, got {value!r}"
        )
    return float(value)


def resolve_worktree_lease_ttl(repo_root: Path) -> float | None:
    """S16.9 — this family's lease TTL, from the PRIMARY CIU root's own
    ``[ciu.worktree]`` table (same authority as the capacity cap). There is
    no ambient override: a lease's lifetime is a repository policy, not
    something a single shell can quietly shorten for one `ciu up`."""
    return resolve_lease_ttl_hours(_primary_worktree_table(repo_root))


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
    rendered against that candidate's OWN explicit
    ``[ciu.instance.generated]`` facts — never the caller's ambient
    IDENTITY. A candidate whose stack is genuinely absent on its checked-out
    branch is skipped (logged, not an error); everything else that cannot be
    resolved truthfully is a loud ``[S16.3]`` failure, never evidence of an
    inactive instance.

    CIU-75 changed the render environment's SHAPE along with its source, and
    the change is deliberate. The pre-cutover code passed the candidate's
    parsed ``ciu.env`` and NOTHING else, so a candidate template referencing
    any non-identity variable (``$USER_UID``, ``$DOCKER_GID``) got it from
    that file. The overlay carries identity facts only, so the environment is
    now the ambient process environment MINUS every CIU identity key, PLUS the
    candidate's own facts — the same rule :func:`_sanitized_target_env` uses.
    Identity still comes exclusively from the candidate (which is the property
    this function exists to hold); machine facts, which are identical in every
    checkout on this host, come live from this process instead of from a file
    that may predate a rebuild.
    """
    from .workspace_env import (
        WorkspaceEnvError,
        generated_facts_path,
        identity_env_from_facts,
        read_generated_facts,
    )

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

        facts_path = generated_facts_path(entry.path)
        try:
            candidate_facts = read_generated_facts(entry.path)
        # CIU-62: the read (`OSError`), a non-UTF-8 byte (`UnicodeDecodeError`,
        # a ValueError SIBLING of WorkspaceEnvError rather than a subclass) and
        # a malformed table are three distinct failures; the reader normalizes
        # all three to WorkspaceEnvError so covering them here is one name.
        except WorkspaceEnvError as exc:
            raise WorktreeError(
                f"[S16.3] could not read/parse {facts_path}: {exc}"
            ) from exc
        if not candidate_facts:
            # A raw git worktree, never registered as a CIU instance.
            continue
        network = candidate_facts.get("network", "")
        if not network:
            raise WorktreeError(
                f"[S16.3] {facts_path} declares no instance network; "
                f"{entry.path} looks like a registered CIU instance whose "
                "deployment state cannot be truthfully counted."
            )
        candidate_env = {
            k: v for k, v in os.environ.items() if k not in _CIU_IDENTITY_ENV_KEYS
        }
        candidate_env.update(identity_env_from_facts(candidate_facts))
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
