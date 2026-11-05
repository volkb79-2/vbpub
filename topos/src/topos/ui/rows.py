"""Row model of the single view: tree, noise levels, filter, sort, process rows."""
from __future__ import annotations

from dataclasses import dataclass, field

from topos.grouping import group_entities
from topos.model import Entity, EntityFrame, Frame

from .columns import has_drift, max_psi, n0, num, sort_value
from .overlay import ProcRow

NOISE_LEVELS = ("all", "middle", "hot")
VIEW_MODES = ("tree", "container", "ciu-grouped")
PROC_CAP_COLLAPSED = 2
PROC_CAP_OPEN = 5


@dataclass(slots=True)
class Row:
    kind: str
    """``cg`` cgroup, ``proc`` process under its cgroup, ``flat`` ranked process, ``group`` header."""
    key: str
    """Unique row key."""
    owner: str | None = None
    """Entity key the side pane, drill-down and snapshot act on."""
    depth: int = 0
    has_kids: bool = False
    open: bool = False
    ef: EntityFrame | None = None
    proc: ProcRow | None = None
    label: str = ""


@dataclass(slots=True)
class RowSet:
    rows: list[Row] = field(default_factory=list)
    hidden: int = 0


def row_name(entity: Entity) -> str:
    if not entity.key:
        return "/ (host)"
    if entity.docker is not None:
        return entity.docker.name
    segment = entity.key.rsplit("/", 1)[-1]
    if segment.startswith("docker-") and len(segment) > 24:
        return f"{segment[:19]}….scope"
    return segment


def search_text(ef: EntityFrame) -> str:
    image = ef.entity.docker.image if ef.entity.docker is not None else ""
    return f"{ef.entity.key} {row_name(ef.entity)} {image}".lower()


# ── noise ────────────────────────────────────────────────────────────


def is_hot(ef: EntityFrame) -> bool:
    some = max_psi(ef, "some") or 0.0
    return (
        n0(ef, "cpu_pct") >= 5.0
        or n0(ef, "io_r_bps") + n0(ef, "io_w_bps") >= 1_048_576.0
        or some >= 5.0
        or n0(ef, "cpu_throttled_pct") > 0.5
        or n0(ef, "mem_events_oom_kill_per_s") > 0.0
    )


def has_limit(ef: EntityFrame) -> bool:
    def positive(key: str) -> bool:
        v = num(ef, key)
        return v is not None and v > 0

    def not_default(key: str) -> bool:
        v = num(ef, key)
        return v is not None and v != 100.0

    return (
        any(positive(k) for k in ("mem_min", "mem_low", "mem_high", "mem_max", "cpu_quota_us", "pids_max"))
        or not_default("cpu_weight")
        or not_default("io_weight")
    )


def is_middle(ef: EntityFrame) -> bool:
    return is_hot(ef) or n0(ef, "pids_current") > 0 or has_limit(ef) or has_drift(ef) or bool(ef.findings)


def _passes_noise(ef: EntityFrame, noise: str) -> bool:
    if noise == "all":
        return True
    return is_hot(ef) if noise == "hot" else is_middle(ef)


# ── tree ─────────────────────────────────────────────────────────────


def children_map(frame: Frame) -> dict[str | None, list[EntityFrame]]:
    """Children by parent key; entities whose parent is absent hang off ``None``."""
    out: dict[str | None, list[EntityFrame]] = {}
    for ef in frame.entities.values():
        parent = ef.entity.parent
        if parent is not None and parent not in frame.entities:
            parent = None
        out.setdefault(parent, []).append(ef)
    return out


def default_expanded(frame: Frame) -> set[str]:
    """Keys open on first sight: the root and its direct children."""
    kids = children_map(frame)
    open_keys: set[str] = set()
    for top in kids.get(None, []):
        open_keys.add(top.entity.key)
        open_keys.update(c.entity.key for c in kids.get(top.entity.key, []))
    return open_keys


def blocked_by_cgroup(
    frame: Frame, procs: dict[str, list[ProcRow]], own: dict[str, int] | None = None
) -> dict[str, int]:
    """D-state task counts per cgroup, including descendants.

    *own* (D-state tasks owned by each cgroup, from a source that counts them all)
    wins over counting the rows in *procs*, which a source may have capped.
    """
    kids = children_map(frame)
    out: dict[str, int] = {}

    def walk(ef: EntityFrame) -> int:
        key = ef.entity.key
        n = own.get(key, 0) if own is not None else sum(1 for p in procs.get(key, ()) if p.state == "D")
        for child in kids.get(ef.entity.key, []):
            n += walk(child)
        out[ef.entity.key] = n
        return n

    for top in kids.get(None, []):
        walk(top)
    return out


def _sorter(sort_id: str, desc: bool, blk: dict[str, int] | None):
    def key_of(ef: EntityFrame) -> float:
        value = sort_value(sort_id, ef, None if blk is None else blk.get(ef.entity.key, 0))
        return -1.0 if value is None else value

    def order(items: list[EntityFrame]) -> list[EntityFrame]:
        if sort_id == "name":
            return sorted(items, key=lambda e: (row_name(e.entity).lower(), e.entity.key), reverse=desc)
        return sorted(items, key=lambda e: (-key_of(e) if desc else key_of(e), row_name(e.entity).lower(), e.entity.key))

    return order


def build_rows(
    frame: Frame,
    *,
    mode: str = "tree",
    noise: str = "middle",
    filter_text: str = "",
    sort_id: str = "cpu",
    desc: bool = True,
    expanded: set[str] | None = None,
    procs: dict[str, list[ProcRow]] | None = None,
    procs_on: bool = True,
    flat_procs: bool = False,
    blk: dict[str, int] | None = None,
) -> RowSet:
    needle = filter_text.lower().strip()
    if mode != "tree" and noise == "middle":
        noise = "all"  # the container lists exist to show containers; only 'hot' narrows them
    expanded = expanded if expanded is not None else set()
    procs = procs or {}
    order = _sorter(sort_id, desc, blk)
    kids = children_map(frame)
    entities = frame.entities

    def own(ef: EntityFrame) -> bool:
        return _passes_noise(ef, noise) and (not needle or needle in search_text(ef))

    visible: dict[str, bool] = {}

    def mark(ef: EntityFrame) -> bool:
        v = own(ef)
        for child in kids.get(ef.entity.key, []):
            v = mark(child) or v
        visible[ef.entity.key] = v
        return v

    for top in kids.get(None, []):
        mark(top)
        if not top.entity.key and (not needle or any(visible.values())):
            visible[""] = True
    hidden = sum(1 for v in visible.values() if not v)

    rows: list[Row] = []
    if flat_procs:
        ranked = [
            (key, p)
            for key, plist in procs.items()
            if visible.get(key)
            for p in plist
        ]
        ranked.sort(key=lambda kp: -kp[1].cpu_rank)
        for key, p in ranked[:300]:
            rows.append(Row("flat", f"{key}#{p.pid}", owner=key, ef=entities.get(key), proc=p))
        return RowSet(rows, hidden)

    if mode == "container":
        flat = [ef for ef in entities.values() if ef.entity.docker is not None and own(ef)]
        hidden = sum(1 for ef in entities.values() if ef.entity.docker is not None and not own(ef))
        return RowSet([Row("cg", ef.entity.key, owner=ef.entity.key, ef=ef) for ef in order(flat)], hidden)

    if mode == "ciu-grouped":
        grouped = group_entities(frame)
        hidden = 0
        for group in grouped.groups:
            members = [ef for ef in group.entity_frames if own(ef)]
            hidden += len(group.entity_frames) - len(members)
            if not members:
                continue
            phase = str(group.phase) if group.phase is not None else (f"? ({group.phase_raw})" if group.phase_raw else "-")
            rows.append(
                Row("group", f"__group__{group.stack or ''}__{group.phase_raw or ''}",
                    label=f"{group.stack}  |  phase {phase}  ({group.source})")
            )
            rows.extend(Row("cg", ef.entity.key, owner=ef.entity.key, ef=ef, label=_inferred(ef)) for ef in order(members))
        loose = [ef for ef in grouped.ungrouped if own(ef)]
        hidden += len(grouped.ungrouped) - len(loose)
        if loose and not needle:
            rows.append(Row("group", "__ungrouped__", label="other containers (no CIU)"))
        rows.extend(Row("cg", ef.entity.key, owner=ef.entity.key, ef=ef) for ef in order(loose))
        return RowSet(rows, hidden)

    def emit(ef: EntityFrame, depth: int) -> None:
        key = ef.entity.key
        children = [c for c in kids.get(key, []) if visible.get(c.entity.key)]
        # Only hot or blocked processes float under their cgroup, blocked first, capped.
        plist = [p for p in procs.get(key, ()) if p.hot] if procs_on else []
        plist.sort(key=lambda p: (p.state != "D", -p.cpu_rank))
        is_open = bool(needle) or key in expanded
        rows.append(Row("cg", key, owner=key, depth=depth, has_kids=bool(children) or bool(plist), open=is_open, ef=ef))
        if is_open:
            for child in order(children):
                emit(child, depth + 1)
        for p in plist[: PROC_CAP_OPEN if is_open else PROC_CAP_COLLAPSED]:
            rows.append(Row("proc", f"{key}#{p.pid}", owner=key, depth=depth + 1, ef=ef, proc=p))

    for top in order([t for t in kids.get(None, []) if visible.get(t.entity.key)]):
        emit(top, 0)
    return RowSet(rows, hidden)


def _inferred(ef: EntityFrame) -> str:
    ciu = ef.entity.ciu
    return "" if ciu is None or ciu.source == "label" else f"({ciu.source})"
