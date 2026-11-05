"""(B230) The per-worker tree provisioning ladder and the shared template.

Assay owns this algorithm; no other tool is required for it.  Given the lane's
``isolation.materialize`` mode, the optional environment a caller provides and
what the workspace can do, a mutation worker's tree comes from the highest rung
that is available, and every step down is explicit (an advisory on stderr and a
line in the ladder record), never silent:

``A`` overlay
    A per-worker overlay *slot* the caller prepared (``ASSAY_WORKER_SLOTS``)
    over a shared read-only template (``ASSAY_TEMPLATE_DIR``).  Reads and
    mappings hit the template's inodes, so the page cache is shared and the
    template's primed index stays valid; the slot is not primed again.
``B`` tmpfs copy
    One private copy of the template under ``isolation.tmpfs_root`` per worker.
``C`` reflink
    A copy-on-write clone from a ``cheap_clone`` (btrfs) workspace.
``D`` copy
    A plain build of the tree for every candidate.

The shared template is keyed by tree identity and built once per key by
:func:`ensure_template`.  The generic keyed-tree lifecycle (lock, atomic
publish, lease, GC) comes from the ``cowtree`` library when it is importable
and from a small built-in implementation of the same on-disk layout when it
is not; either result can be consumed by the other.  Nothing here imports
``cowtree`` at module load, so assay stays stdlib-only.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib
import json
import os
import re
import secrets
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import _tmpfs_worker
from .errors import LaneConfigError

ENV_TEMPLATE_DIR = "ASSAY_TEMPLATE_DIR"
ENV_WORKER_SLOTS = "ASSAY_WORKER_SLOTS"

#: Advanced whenever what a template contains (or how its key is derived)
#: changes, so an old template can never be mistaken for a current one.
TEMPLATE_FORMAT = "assay-template/1"

RUNG_NAMES: Mapping[str, str] = {
    "A": "overlay",
    "B": "tmpfs-copy",
    "C": "reflink",
    "D": "copy",
}

#: A plain copy per candidate (rung D) is accepted for ``jobs > 1`` only while
#: the tree is this small; a larger tree makes every worker pay the full copy
#: and the run is refused with the reasons instead.
PLAIN_COPY_PARALLEL_MAX_BYTES = 64 * 1024 * 1024

_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{7,127}\Z")


class TemplateError(LaneConfigError):
    """The shared template cannot be created or trusted."""


@dataclass(frozen=True)
class TemplateEnvironment:
    """What the caller offered through the environment (nothing is assumed)."""

    template_dir: Path | None
    slots: tuple[Path, ...]
    problems: tuple[str, ...]


def _is_clean_absolute(value: str) -> bool:
    return value.startswith("/") and ".." not in value.split("/")


def read_environment(env: Mapping[str, str]) -> TemplateEnvironment:
    """Parse ``ASSAY_TEMPLATE_DIR`` / ``ASSAY_WORKER_SLOTS``; empty is absent."""
    problems: list[str] = []
    template_dir: Path | None = None
    raw_template = env.get(ENV_TEMPLATE_DIR, "")
    if raw_template:
        if _is_clean_absolute(raw_template):
            template_dir = Path(raw_template)
        else:
            problems.append(
                f"{ENV_TEMPLATE_DIR}={raw_template!r} is not an absolute path "
                f"without '..'; ignored"
            )
    slots: list[Path] = []
    raw_slots = env.get(ENV_WORKER_SLOTS, "")
    if raw_slots:
        for part in raw_slots.split(":"):
            if _is_clean_absolute(part):
                slots.append(Path(part))
            else:
                problems.append(
                    f"{ENV_WORKER_SLOTS} entry {part!r} is not an absolute "
                    f"path without '..'; ignored"
                )
    return TemplateEnvironment(template_dir, tuple(slots), tuple(problems))


@dataclass(frozen=True)
class LadderPlan:
    """The highest rung this run attempts, and why every higher one is skipped."""

    rung: str
    reason: str
    skipped: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return RUNG_NAMES[self.rung]

    def below(self, reason: str, *, cheap_clone: bool) -> "LadderPlan":
        """The plan one step down after *reason* ruled the current rung out."""
        lower = "C" if cheap_clone else "D"
        return LadderPlan(
            lower,
            "copy-on-write clones of the sealed template"
            if cheap_clone
            else "a plain build per candidate",
            (*self.skipped, f"rung {self.rung} ({self.name}) unavailable: {reason}"),
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "rung": self.rung,
            "name": self.name,
            "reason": self.reason,
            "skipped": list(self.skipped),
        }


def plan_ladder(
    mode: str, environment: TemplateEnvironment, *, cheap_clone: bool
) -> LadderPlan:
    """The top rung for *mode*; rungs not reachable by *mode* are listed as skipped."""
    skipped = list(environment.problems)
    offered = environment.template_dir is not None or bool(environment.slots)
    if mode == "template":
        if environment.template_dir is not None and environment.slots:
            return LadderPlan(
                "A",
                f"{len(environment.slots)} overlay slot(s) from "
                f"{ENV_WORKER_SLOTS} over {ENV_TEMPLATE_DIR}",
                tuple(skipped),
            )
        if environment.slots:
            skipped.append(
                f"rung A (overlay) unavailable: {ENV_WORKER_SLOTS} is set "
                f"but {ENV_TEMPLATE_DIR} is not"
            )
        elif environment.template_dir is not None:
            skipped.append(
                f"rung A (overlay) unavailable: {ENV_TEMPLATE_DIR} is set "
                f"but {ENV_WORKER_SLOTS} is not (the template is the copy "
                f"and repair source)"
            )
        else:
            skipped.append(
                f"rung A (overlay) unavailable: neither {ENV_TEMPLATE_DIR} "
                f"nor {ENV_WORKER_SLOTS} is set"
            )
    elif mode == "tmpfs-worker":
        # Declared on purpose: rung A is not part of this mode, so it is
        # neither "skipped" nor a step down.
        if offered:
            skipped.append(
                f"{ENV_TEMPLATE_DIR}/{ENV_WORKER_SLOTS} ignored: "
                f"isolation.materialize = 'tmpfs-worker' uses no overlay slots"
            )
    else:
        if offered:
            skipped.append(
                f"{ENV_TEMPLATE_DIR}/{ENV_WORKER_SLOTS} ignored: "
                f"isolation.materialize = 'cow' uses no shared template"
            )
        return LadderPlan(
            "C" if cheap_clone else "D",
            "copy-on-write clones of the sealed template"
            if cheap_clone
            else "a plain build per candidate",
            tuple(skipped),
        )
    return LadderPlan(
        "B", "one tmpfs copy of the template per worker", tuple(skipped)
    )


def template_key(
    *,
    commit: str,
    tree_sha256: str,
    project_prefix: str,
    link_paths: Sequence[str],
    snapshot_history: str,
    resolved_base: str | None,
) -> str:
    """The identity of the template a run needs: a hex digest.

    ``commit`` fixes the git tree and ``HEAD`` of the template, ``tree_sha256``
    the frozen manifest (so the snapshot selection), and the remaining fields
    are the isolation configuration that shapes the repository metadata.
    """
    document = json.dumps(
        {
            "format": TEMPLATE_FORMAT,
            "commit": commit,
            "tree_sha256": tree_sha256,
            "project_prefix": project_prefix,
            "link_paths": sorted(link_paths),
            "snapshot_history": snapshot_history,
            "resolved_base": resolved_base,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EnsuredTemplate:
    key: str
    tree: Path
    built: bool
    backend: str


def _load_cowtree() -> Any:
    """The ``cowtree.cache`` module, or ``None`` when it is not installed."""
    try:
        return importlib.import_module("cowtree.cache")
    except ImportError:
        return None


def ensure_template(
    root: str | os.PathLike[str],
    key: str,
    build: Callable[[str], None],
    *,
    require_tmpfs: bool,
    use_cowtree: bool = True,
) -> EnsuredTemplate:
    """Build the template for *key* under *root* once; later calls reuse it.

    ``build(tree_directory)`` fills an empty directory.  There is no
    permission seal: an overlay lower layer must stay writable-safe (a
    ``chmod`` would force whole-file copy-ups), the mount provides the
    read-only property.
    """
    if _KEY.fullmatch(key) is None:
        raise TemplateError(f"template key {key!r} is not a valid cache key")
    cowtree = _load_cowtree() if use_cowtree else None
    if cowtree is not None:
        try:
            cache = cowtree.Cache(os.fspath(root), require_tmpfs=require_tmpfs)
            published = cache.ensure(key, build, identity=key)
        except Exception as exc:  # cowtree has its own error hierarchy
            raise TemplateError(f"cowtree could not ensure {key}: {exc}") from exc
        return EnsuredTemplate(key, Path(published.tree), published.built, "cowtree")
    return _ensure_builtin(Path(root), key, build, require_tmpfs=require_tmpfs)


def _ensure_builtin(
    root: Path, key: str, build: Callable[[str], None], *, require_tmpfs: bool
) -> EnsuredTemplate:
    """The same layout as ``cowtree.cache``: ``<key>/{tree,COMPLETE,.lease}``."""
    if not root.is_dir():
        raise TemplateError(f"template root {root} is not a directory")
    if require_tmpfs:
        kind = _tmpfs_worker._mount_type(
            os.path.realpath(root), "/proc/self/mounts"
        )
        if kind not in ("tmpfs", "ramfs"):
            raise TemplateError(
                f"template root {root} is on {kind or 'unknown'}, not a tmpfs"
            )
    locks = root / ".locks"
    locks.mkdir(exist_ok=True)
    descriptor = os.open(locks / key, os.O_CREAT | os.O_RDWR, 0o660)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        final = root / key
        complete = final / "COMPLETE"
        if complete.is_file():
            recorded = json.loads(complete.read_text(encoding="utf-8"))
            if recorded.get("identity") != key:
                raise TemplateError(
                    f"{final} is published with identity "
                    f"{recorded.get('identity')!r}, not {key!r}"
                )
            return EnsuredTemplate(key, final / "tree", False, "builtin")
        if final.exists():
            shutil.rmtree(final)
        staging = root / f"{key}.tmp-{secrets.token_hex(6)}"
        tree = staging / "tree"
        tree.mkdir(parents=True)
        try:
            build(os.fspath(tree))
            (staging / "COMPLETE").write_text(
                json.dumps(
                    {"format": TEMPLATE_FORMAT, "key": key, "identity": key},
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            (staging / ".lease").touch()
            (staging / ".last-used").touch()
            os.rename(staging, final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return EnsuredTemplate(key, final / "tree", True, "builtin")
    finally:
        os.close(descriptor)


def parallel_refusal(
    plan: LadderPlan,
    *,
    jobs: int,
    tree_bytes: int,
    tmpfs_fits_all: bool,
    cheap_clone: bool,
) -> str | None:
    """Why ``jobs > 1`` is refused for *plan*, or ``None`` when it is allowed.

    Rungs A and C are cheap per-worker sources.  Rung B is one while the tmpfs
    can hold ``jobs`` trees; otherwise the run falls to C (cheap) or D.  Rung D
    is accepted only for a small tree.
    """
    if jobs <= 1 or plan.rung in ("A", "C"):
        return None
    if plan.rung == "B" and tmpfs_fits_all:
        return None
    if plan.rung == "B" and cheap_clone:
        return None
    if tree_bytes <= PLAIN_COPY_PARALLEL_MAX_BYTES:
        return None
    detail = "; ".join(plan.skipped) or plan.reason
    return (
        f"native R2 --jobs {jobs} requires a cheap_clone snapshot workspace "
        f"or another cheap per-worker tree source (overlay slots, tmpfs "
        f"copies); only a plain copy of {tree_bytes} bytes per candidate is "
        f"available ({detail}); configure one of them or pass --jobs 1"
    )


@dataclass
class LadderLog:
    """What actually happened, for the run's ladder record."""

    plan: LadderPlan
    degradations: list[str] = field(default_factory=list)
    candidates: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def count(self, rung: str) -> None:
        """Count one candidate (or one worker) served from *rung*."""
        with self._lock:
            self.candidates[rung] = self.candidates.get(rung, 0) + 1

    def as_record(self) -> dict[str, Any]:
        return {
            **self.plan.as_record(),
            "degradations": list(self.degradations),
            "candidates": dict(sorted(self.candidates.items())),
        }
