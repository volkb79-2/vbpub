"""Target resolution: turn what a human types into cgroup paths to sample.

Accepted spec forms (``--target`` may be repeated):

    cgroup:/dev.slice/dev-background.slice   an absolute cgroup v2 path
    slice:dev-background.slice               a systemd slice, name expanded
    container:<name|id|prefix>               a Docker container
    label:<key>=<value>                      every container matching a label
    pid:<n>                                  the cgroup owning that PID
    self                                     the cgroup this process is in

A bare argument with no ``scheme:`` prefix is treated as ``container:`` when it
matches a running container and as ``cgroup:``/``slice:`` otherwise, so the
common cases need no prefix.

Two rules shape this module:

*Names are resolved where the resolver lives.*  Only the outer process has the
Docker socket, so container names become container **ids** before anything is
handed to the helper; ids are then located by scanning the cgroup tree, which
needs no Docker at all.  That keeps the sampling side dependency-free and lets
it keep working if the Docker daemon is restarted mid-run.

*Membership is re-evaluated, never frozen.*  A gate spawns containers after the
profiler starts; those must be picked up.  A target marked ``follow_children``
rescans its subtree on every discovery tick, and appearances/disappearances are
reported so they can be logged as events.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import util
from .access import CGROUP_ROOT, docker_bin

_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
# Both cgroup drivers docker can be configured with: systemd names the leaf
# "docker-<id>.scope", cgroupfs names it plain "<id>" under a "docker" parent.
_SCOPE_RES = (
    re.compile(r"^(?:docker|crio|containerd|libpod)-(?P<id>[0-9a-f]{64})\.scope$"),
    re.compile(r"^(?P<id>[0-9a-f]{64})$"),
)


class TargetError(ValueError):
    """A target spec that cannot be resolved to anything on this host."""


@dataclass
class Target:
    """One thing being watched, plus how it was asked for."""

    key: str                      # stable id used in the data files
    cgroup: str                   # absolute cgroup v2 path ("/dev.slice/...")
    label: str                    # human display name
    kind: str                     # cgroup | slice | container | pid | self
    spec: str                     # the original spec text
    follow_children: bool = False
    pid: Optional[int] = None     # set for pid: targets — enables /proc sampling
    container_id: Optional[str] = None
    metrics: Optional[Set[str]] = None  # None == every metric group
    role: str = "subject"         # subject | observer

    def abs_path(self, root: str = CGROUP_ROOT) -> str:
        return os.path.join(root, self.cgroup.lstrip("/"))

    def exists(self, root: str = CGROUP_ROOT) -> bool:
        return os.path.isdir(self.abs_path(root))


# ── systemd slice naming ────────────────────────────────────────────────────

def slice_to_path(name: str) -> str:
    """``dev-background.slice`` → ``/dev.slice/dev-background.slice``.

    systemd encodes the hierarchy in the unit name with ``-`` as the separator,
    so every prefix is an ancestor slice. ``-.slice`` is the root.
    """
    name = name.strip()
    if not name.endswith(".slice"):
        name += ".slice"
    if name == "-.slice":
        return "/"
    stem = name[: -len(".slice")]
    if not stem:
        raise TargetError(f"not a usable slice name: {name!r}")
    parts = stem.split("-")
    path = ""
    for index in range(1, len(parts) + 1):
        path += "/" + "-".join(parts[:index]) + ".slice"
    return path


def path_to_slice(cgroup: str) -> Optional[str]:
    """Inverse of :func:`slice_to_path` for paths that are slices."""
    leaf = os.path.basename(cgroup.rstrip("/"))
    return leaf if leaf.endswith(".slice") else None


# ── docker lookups (outer process only) ─────────────────────────────────────

def _docker_json(*args: str) -> Optional[list]:
    binary = docker_bin()
    if not binary:
        return None
    result = subprocess.run(
        [binary, *args], capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def resolve_container(ref: str) -> Tuple[str, str]:
    """``(full_id, display_name)`` for a container name, id, or id prefix."""
    data = _docker_json("inspect", ref)
    if not data:
        raise TargetError(
            f"no container matches {ref!r} (docker inspect found nothing). "
            "Check `docker ps` — a stopped container has no cgroup to sample."
        )
    info = data[0]
    return info["Id"], str(info.get("Name", ref)).lstrip("/")


def resolve_label(selector: str) -> List[Tuple[str, str]]:
    """Every running container carrying ``key=value``."""
    binary = docker_bin()
    if not binary:
        raise TargetError("docker CLI not found — label: targets need it")
    result = subprocess.run(
        [binary, "ps", "--filter", f"label={selector}", "--format", "{{.ID}}\t{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    out: List[Tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        cid, _, name = line.partition("\t")
        full, display = resolve_container(cid.strip())
        out.append((full, display or name.strip()))
    if not out:
        raise TargetError(f"no running container carries label {selector!r}")
    return out


# ── cgroup tree lookups (no docker needed) ──────────────────────────────────

def cgroup_of_pid(pid: int, root: str = CGROUP_ROOT) -> Optional[str]:
    """The cgroup v2 path of ``pid`` as seen from *this* namespace.

    Under a private cgroup namespace this reads back as ``/`` for our own
    processes — correct for that namespace, useless for locating anything on
    the host, which is exactly why the profiler re-execs into a host-view
    helper rather than trying to translate it.
    """
    text = util.read_text(f"/proc/{pid}/cgroup")
    if not text:
        return None
    for line in text.split("\n"):
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            path = parts[2] or "/"
            return path
    return None


def find_container_cgroup(container_id: str, root: str = CGROUP_ROOT) -> Optional[str]:
    """Locate a container's leaf cgroup by scanning the tree for its id.

    Scans rather than composing ``CgroupParent + scope`` because the two
    cgroup drivers name the leaf differently and because a container may have
    been placed under a slice the caller does not know about — the tree is the
    ground truth, the inspect output is a claim about it.
    """
    for dirpath, dirnames, _ in os.walk(root):
        for name in dirnames:
            for pattern in _SCOPE_RES:
                match = pattern.match(name)
                if match and match.group("id") == container_id:
                    full = os.path.join(dirpath, name)
                    return "/" + os.path.relpath(full, root)
        # Container scopes are never more than a few levels deep; walking the
        # whole tree costs little, but prune the leaf scopes' own children
        # (each holds no further containers) to keep it cheap on a busy host.
        dirnames[:] = [d for d in dirnames if not d.endswith(".scope")] or dirnames
    return None


def list_children(cgroup: str, root: str = CGROUP_ROOT) -> List[str]:
    """Immediate child cgroups of ``cgroup`` (absolute cgroup paths)."""
    base = os.path.join(root, cgroup.lstrip("/"))
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return []
    out = []
    for entry in entries:
        if os.path.isdir(os.path.join(base, entry)):
            out.append(os.path.join("/" + cgroup.strip("/"), entry) if cgroup != "/" else "/" + entry)
    return out


def walk_subtree(cgroup: str, root: str = CGROUP_ROOT, max_depth: int = 4) -> List[str]:
    """Every descendant cgroup of ``cgroup``, bounded in depth.

    The depth bound is a guard, not a preference: a runaway hierarchy (systemd
    user sessions, nested containers) can be thousands of cgroups deep, and a
    profiler that tries to sample all of them at 4 Hz becomes the load it was
    sent to measure.
    """
    out: List[str] = []
    base_depth = len([p for p in cgroup.strip("/").split("/") if p])
    stack = [cgroup]
    while stack:
        current = stack.pop()
        for child in list_children(current, root):
            depth = len([p for p in child.strip("/").split("/") if p]) - base_depth
            out.append(child)
            if depth < max_depth:
                stack.append(child)
    return sorted(out)


# ── spec parsing ────────────────────────────────────────────────────────────

def _split_spec(spec: str) -> Tuple[str, str]:
    scheme, sep, rest = spec.partition(":")
    if not sep:
        return ("", spec)
    if scheme in ("cgroup", "slice", "container", "containerid", "label", "pid", "self"):
        return (scheme, rest)
    return ("", spec)


def _parse_options(rest: str) -> Tuple[str, Dict[str, str]]:
    """Split ``value@opt=a,opt=b`` into value and options."""
    value, sep, opts = rest.partition("@")
    options: Dict[str, str] = {}
    if sep:
        for item in opts.split(","):
            if not item:
                continue
            key, _, val = item.partition("=")
            options[key.strip()] = val.strip() or "1"
    return value.strip(), options


def parse_target(
    spec: str,
    root: str = CGROUP_ROOT,
    default_follow: bool = False,
    role: str = "subject",
) -> List[Target]:
    """Resolve one ``--target`` spec into one or more concrete targets."""
    scheme, rest = _split_spec(spec)
    value, options = _parse_options(rest)

    follow = default_follow
    if "follow" in options:
        follow = options["follow"] not in ("0", "no", "false")
    if "nofollow" in options:
        follow = False
    metrics = None
    if "metrics" in options:
        metrics = {m.strip() for m in options["metrics"].split("+") if m.strip()}
    label_override = options.get("as")
    role = options.get("role", role)

    def make(cgroup: str, label: str, kind: str, **extra) -> Target:
        return Target(
            key=_key_for(cgroup, label),
            cgroup=cgroup,
            label=label_override or label,
            kind=kind,
            spec=spec,
            follow_children=follow,
            metrics=metrics,
            role=role,
            **extra,
        )

    if scheme == "self" or (not scheme and value == "self"):
        own = cgroup_of_pid(os.getpid(), root)
        if not own or own == "/":
            raise TargetError(
                "self: resolved to the namespace root — this process cannot see "
                "its own host cgroup. Name the container explicitly instead."
            )
        return [make(own, util.short_label(own), "self")]

    if scheme == "pid":
        try:
            pid = int(value)
        except ValueError as exc:
            raise TargetError(f"pid: needs a number, got {value!r}") from exc
        cgroup = cgroup_of_pid(pid, root)
        if not cgroup:
            raise TargetError(f"pid {pid} has no readable cgroup (already exited?)")
        comm = util.read_text(f"/proc/{pid}/comm") or str(pid)
        return [make(cgroup, f"{comm}[{pid}]", "pid", pid=pid)]

    if scheme == "label":
        out = []
        for cid, name in resolve_label(value):
            cgroup = find_container_cgroup(cid, root)
            if not cgroup:
                continue
            out.append(make(cgroup, name, "container", container_id=cid))
        if not out:
            raise TargetError(f"label {value!r} matched containers but none has a cgroup here")
        return out

    if scheme == "containerid":
        # Already-resolved form, produced by the outer process and handed to the
        # helper. The helper has the host cgroup tree but no Docker socket, so
        # it must be able to locate a container without asking the daemon.
        if not _CONTAINER_ID_RE.match(value):
            raise TargetError(f"containerid: needs a hex container id, got {value!r}")
        cgroup = find_container_cgroup(value, root)
        if not cgroup:
            raise TargetError(f"container {value[:12]} has no cgroup under {root}")
        return [make(cgroup, label_override or value[:12], "container", container_id=value)]

    if scheme == "container":
        cid, name = resolve_container(value)
        cgroup = find_container_cgroup(cid, root)
        if not cgroup:
            raise TargetError(
                f"container {name} ({cid[:12]}) has no cgroup under {root} — "
                "it is probably not running, or this process sees only its own "
                "cgroup namespace (run via the helper)."
            )
        return [make(cgroup, name, "container", container_id=cid)]

    if scheme == "slice":
        cgroup = slice_to_path(value)
        return [make(cgroup, value, "slice")]

    if scheme == "cgroup":
        cgroup = "/" + value.strip("/")
        return [make(cgroup, util.short_label(cgroup), "cgroup")]

    # No scheme: infer. A running container name is the friendliest reading,
    # then a slice unit name, then a raw path.
    if not value.startswith("/"):
        if docker_bin():
            try:
                return parse_target(f"container:{rest}", root, default_follow, role)
            except TargetError:
                pass
        if value.endswith(".slice") or _looks_like_slice(value):
            return parse_target(f"slice:{rest}", root, default_follow, role)
    return parse_target(f"cgroup:{rest}", root, default_follow, role)


def _looks_like_slice(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_.\\-]+$", value)) and "/" not in value


def _key_for(cgroup: str, label: str) -> str:
    """A short stable key for a target, unique within a run."""
    stem = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-").lower() or "target"
    return stem[:48]


def resolve_all(
    specs: Sequence[str],
    root: str = CGROUP_ROOT,
    default_follow: bool = False,
    role: str = "subject",
) -> List[Target]:
    """Resolve every spec, de-duplicating by cgroup path."""
    seen: Dict[str, Target] = {}
    errors: List[str] = []
    for spec in specs:
        try:
            for target in parse_target(spec, root, default_follow, role):
                existing = seen.get(target.cgroup)
                if existing is None:
                    key = target.key
                    suffix = 2
                    used = {t.key for t in seen.values()}
                    while target.key in used:
                        target.key = f"{key}-{suffix}"
                        suffix += 1
                    seen[target.cgroup] = target
                else:
                    # Same cgroup named twice: keep the stronger request.
                    existing.follow_children = existing.follow_children or target.follow_children
                    if target.metrics is None:
                        existing.metrics = None
                    elif existing.metrics is not None:
                        existing.metrics |= target.metrics
        except TargetError as exc:
            errors.append(f"{spec}: {exc}")
    if errors and not seen:
        raise TargetError("; ".join(errors))
    for message in errors:
        # Partial failure is survivable — say so loudly and keep the rest.
        print(f"[cgprofile] target skipped — {message}", flush=True)
    return list(seen.values())


@dataclass
class Membership:
    """The concrete cgroup set derived from targets, refreshed as things move."""

    targets: List[Target]
    root: str = CGROUP_ROOT
    max_depth: int = 4
    _paths: Dict[str, Target] = field(default_factory=dict)

    def refresh(self) -> Tuple[List[str], List[str]]:
        """Recompute the sampled set. Returns ``(appeared, disappeared)``."""
        current: Dict[str, Target] = {}
        for target in self.targets:
            if target.exists(self.root):
                current[target.cgroup] = target
            if target.follow_children:
                for child in walk_subtree(target.cgroup, self.root, self.max_depth):
                    current.setdefault(child, target)
        appeared = sorted(set(current) - set(self._paths))
        disappeared = sorted(set(self._paths) - set(current))
        self._paths = current
        return appeared, disappeared

    def paths(self) -> List[str]:
        return sorted(self._paths)

    def owner(self, cgroup: str) -> Optional[Target]:
        return self._paths.get(cgroup)

    def label_for(self, cgroup: str) -> str:
        owner = self._paths.get(cgroup)
        if owner and owner.cgroup == cgroup:
            return owner.label
        if owner:
            return f"{owner.label}/{util.short_label(cgroup, keep=1)}"
        return util.short_label(cgroup)
