"""The one bounded aggregation engine (P88).

``run_query`` consumes a :class:`~groop.query.source.FrameSource`, applies a
strict :class:`Query`, and returns a :class:`Result` whose ``meta`` reports the
full observability truth (requested window, observed start/end, sample count,
coverage, gaps/eviction, resets, source, freshness, truncation).  It is the
single engine behind CLI/TUI/HTTP/MCP: no consumer re-aggregates frames.

Order of operations, so bounds are enforced BEFORE the response is materialized
(Contract 6):
    1. pull + window-filter the canonical frames from the source;
    2. resolve the entity selector (a miss yields an empty result, not an error);
    3. rank/project the candidate rows;
    4. enforce row and point caps (error, or an explicit truncation policy);
    5. build the bounded row payload;
    6. enforce the encoded-byte cap (error, or truncation) — never an unbounded
       full-frame fallback.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from typing import Any

from groop.model import Frame
from groop.registry import REGISTRY
from groop.report import WindowRange, parse_window_spec

from .errors import (
    BoundExceededError,
    IncompatibleQueryError,
    InvalidQueryError,
    UnknownFieldError,
)
from .semantics import (
    ValueSemantic,
    collect_points,
    current_value,
    resolve_semantic,
    summarize,
)
from .source import FrameSource, SourceFrame, SourceProvenance

_SHAPES = ("current", "raw", "summary")
_PROJECTIONS = ("hierarchy", "flat")
_VISIBILITIES = ("all", "available")
_ORDERS = ("asc", "desc")

# A temporal gap is flagged when the inter-frame delta exceeds the nominal
# interval by this factor.  1.5 tolerates ordinary jitter but catches a dropped
# sample.
_GAP_FACTOR = 1.5

# Default caps.  4 MiB matches the daemon/MCP aggregate response ceiling.
DEFAULT_MAX_ROWS = 10000
DEFAULT_MAX_POINTS = 500000
DEFAULT_MAX_BYTES = 4 * 1024 * 1024

_NEG_INF = float("-inf")
_POS_INF = float("inf")

# Default summary stat used to rank each value semantic.
_DEFAULT_SORT_STAT: dict[ValueSemantic, str] = {
    ValueSemantic.GAUGE: "p95",
    ValueSemantic.RATE: "p95",
    ValueSemantic.COUNTER_DELTA: "total",
    ValueSemantic.EVENT_COUNT: "events",
    ValueSemantic.INTEGRAL: "integral",
}
# Stat keys that exist (and are numeric-sortable) per semantic.
_SORTABLE_STATS: dict[ValueSemantic, frozenset[str]] = {
    ValueSemantic.GAUGE: frozenset({"min", "mean", "p50", "p95", "max"}),
    ValueSemantic.RATE: frozenset({"min", "mean", "p50", "p95", "max", "resets"}),
    ValueSemantic.COUNTER_DELTA: frozenset({"total", "intervals", "resets"}),
    ValueSemantic.EVENT_COUNT: frozenset({"events", "intervals", "resets"}),
    ValueSemantic.INTEGRAL: frozenset({"integral", "span_s", "count", "resets"}),
    ValueSemantic.STATE_DURATION: frozenset(),
}


# ---------------------------------------------------------------------------
# Query object (Contract 2) — strict; unknown fields / bad combos are typed.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricRef:
    name: str
    semantic: str | None = None  # None -> canonical


@dataclass(frozen=True)
class Selector:
    keys: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    slice: str | None = None

    @property
    def is_all(self) -> bool:
        return not self.keys and not self.globs and self.slice is None


@dataclass(frozen=True)
class SortSpec:
    metric: str
    stat: str | None = None
    order: str = "desc"


@dataclass(frozen=True)
class Caps:
    max_rows: int = DEFAULT_MAX_ROWS
    max_points: int = DEFAULT_MAX_POINTS
    max_bytes: int = DEFAULT_MAX_BYTES
    on_exceed: str = "error"  # "error" | "truncate"


@dataclass(frozen=True)
class Query:
    shape: str
    metrics: tuple[MetricRef, ...]
    window_spec: str = "all"
    selector: Selector = field(default_factory=Selector)
    projection: str = "flat"
    visibility: str = "all"
    sort: SortSpec | None = None
    caps: Caps = field(default_factory=Caps)

    _FIELDS = frozenset(
        {"shape", "metrics", "window_spec", "selector", "projection", "visibility", "sort", "caps"}
    )

    @classmethod
    def from_dict(cls, data: Any) -> Query:
        """Build a Query from a plain dict, rejecting unknown fields (Contract 2)."""
        if not isinstance(data, dict):
            raise InvalidQueryError("query must be a mapping")
        unknown = set(data) - cls._FIELDS
        if unknown:
            raise UnknownFieldError(f"unknown query field(s): {', '.join(sorted(unknown))}")
        if "shape" not in data:
            raise InvalidQueryError("query requires a 'shape'")
        metrics: list[MetricRef] = []
        for m in data.get("metrics", ()):
            if isinstance(m, str):
                metrics.append(_parse_metric_token(m))
            elif isinstance(m, dict):
                extra = set(m) - {"name", "semantic"}
                if extra:
                    raise UnknownFieldError(f"unknown metric field(s): {', '.join(sorted(extra))}")
                if "name" not in m:
                    raise InvalidQueryError("metric spec requires a 'name'")
                metrics.append(MetricRef(name=m["name"], semantic=m.get("semantic")))
            else:
                raise InvalidQueryError(f"invalid metric spec: {m!r}")
        sel_data = data.get("selector", {})
        if not isinstance(sel_data, dict):
            raise InvalidQueryError("selector must be a mapping")
        sel_extra = set(sel_data) - {"keys", "globs", "slice"}
        if sel_extra:
            raise UnknownFieldError(f"unknown selector field(s): {', '.join(sorted(sel_extra))}")
        selector = Selector(
            keys=tuple(sel_data.get("keys", ())),
            globs=tuple(sel_data.get("globs", ())),
            slice=sel_data.get("slice"),
        )
        sort = None
        sort_data = data.get("sort")
        if sort_data is not None:
            if isinstance(sort_data, str):
                sort = _parse_sort_token(sort_data)
            elif isinstance(sort_data, dict):
                s_extra = set(sort_data) - {"metric", "stat", "order"}
                if s_extra:
                    raise UnknownFieldError(f"unknown sort field(s): {', '.join(sorted(s_extra))}")
                if "metric" not in sort_data:
                    raise InvalidQueryError("sort spec requires a 'metric'")
                sort = SortSpec(
                    metric=sort_data["metric"],
                    stat=sort_data.get("stat"),
                    order=sort_data.get("order", "desc"),
                )
            else:
                raise InvalidQueryError(f"invalid sort spec: {sort_data!r}")
        caps_data = data.get("caps", {})
        if not isinstance(caps_data, dict):
            raise InvalidQueryError("caps must be a mapping")
        c_extra = set(caps_data) - {"max_rows", "max_points", "max_bytes", "on_exceed"}
        if c_extra:
            raise UnknownFieldError(f"unknown caps field(s): {', '.join(sorted(c_extra))}")
        caps = Caps(
            max_rows=_as_int(caps_data.get("max_rows", DEFAULT_MAX_ROWS), "caps.max_rows"),
            max_points=_as_int(caps_data.get("max_points", DEFAULT_MAX_POINTS), "caps.max_points"),
            max_bytes=_as_int(caps_data.get("max_bytes", DEFAULT_MAX_BYTES), "caps.max_bytes"),
            on_exceed=caps_data.get("on_exceed", "error"),
        )
        return cls(
            shape=data["shape"],
            metrics=tuple(metrics),
            window_spec=data.get("window_spec", "all"),
            selector=selector,
            projection=data.get("projection", "flat"),
            visibility=data.get("visibility", "all"),
            sort=sort,
            caps=caps,
        )


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidQueryError(f"{name} must be an integer")
    return value


def _parse_metric_token(token: str) -> MetricRef:
    """Parse ``name`` or ``name:semantic``."""
    if ":" in token:
        name, _, sem = token.partition(":")
        return MetricRef(name=name, semantic=sem)
    return MetricRef(name=token)


def _parse_sort_token(token: str) -> SortSpec:
    """Parse ``metric[:stat][:order]``."""
    parts = token.split(":")
    metric = parts[0]
    stat: str | None = None
    order = "desc"
    for part in parts[1:]:
        if part in _ORDERS:
            order = part
        else:
            stat = part
    return SortSpec(metric=metric, stat=stat, order=order)


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ResolvedMetric:
    name: str
    semantic: ValueSemantic


def _validate(query: Query) -> tuple[list[_ResolvedMetric], SortSpec | None]:
    if query.shape not in _SHAPES:
        raise InvalidQueryError(f"unknown shape {query.shape!r}; expected one of: {', '.join(_SHAPES)}")
    if query.projection not in _PROJECTIONS:
        raise InvalidQueryError(
            f"unknown projection {query.projection!r}; expected one of: {', '.join(_PROJECTIONS)}"
        )
    if query.visibility not in _VISIBILITIES:
        raise InvalidQueryError(
            f"unknown visibility {query.visibility!r}; expected one of: {', '.join(_VISIBILITIES)}"
        )
    if query.caps.on_exceed not in ("error", "truncate"):
        raise InvalidQueryError(
            f"unknown caps.on_exceed {query.caps.on_exceed!r}; expected 'error' or 'truncate'"
        )
    for bound_name, value in (
        ("max_rows", query.caps.max_rows),
        ("max_points", query.caps.max_points),
        ("max_bytes", query.caps.max_bytes),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidQueryError(f"caps.{bound_name} must be a non-negative integer")
    if not query.metrics:
        raise InvalidQueryError("a query must select at least one metric")

    resolved: list[_ResolvedMetric] = []
    seen: set[str] = set()
    for ref in query.metrics:
        if ref.name in seen:
            raise IncompatibleQueryError(f"metric {ref.name!r} selected more than once")
        seen.add(ref.name)
        resolved.append(_ResolvedMetric(name=ref.name, semantic=resolve_semantic(ref.name, ref.semantic)))

    if query.shape == "raw" and query.projection == "hierarchy":
        raise IncompatibleQueryError(
            "raw shape has no hierarchy; request projection 'flat' for a series dump"
        )

    sort = query.sort
    if query.shape == "raw":
        if sort is not None:
            raise IncompatibleQueryError(
                "raw shape does not rank; series are emitted in canonical (entity, metric) order"
            )
        return resolved, None

    by_name = {rm.name: rm for rm in resolved}
    if sort is None:
        if query.shape == "current":
            # Every metric ranks by its current value.
            return resolved, SortSpec(metric=resolved[0].name, stat="value", order="desc")
        # summary: rank by the first rankable metric; if none is rankable
        # (e.g. a lone state_duration), fall back to canonical key order (None).
        for rm in resolved:
            stat = _DEFAULT_SORT_STAT.get(rm.semantic)
            if stat is not None:
                return resolved, SortSpec(metric=rm.name, stat=stat, order="desc")
        return resolved, None

    if sort.order not in _ORDERS:
        raise InvalidQueryError(f"unknown sort order {sort.order!r}; expected 'asc' or 'desc'")
    if sort.metric not in by_name:
        raise IncompatibleQueryError(f"sort metric {sort.metric!r} is not among the selected metrics")
    rm = by_name[sort.metric]
    if query.shape == "current":
        if sort.stat not in (None, "value"):
            raise IncompatibleQueryError("current shape ranks by 'value'; a summary stat is not available")
        return resolved, SortSpec(metric=sort.metric, stat="value", order=sort.order)
    stat = sort.stat if sort.stat is not None else _DEFAULT_SORT_STAT.get(rm.semantic)
    sortable = _SORTABLE_STATS[rm.semantic]
    if stat is None or stat not in sortable:
        allowed = ", ".join(sorted(sortable)) or "(none)"
        raise IncompatibleQueryError(
            f"stat {stat!r} is not sortable for {sort.metric!r} ({rm.semantic.value}); sortable: {allowed}"
        )
    return resolved, SortSpec(metric=sort.metric, stat=stat, order=sort.order)


# ---------------------------------------------------------------------------
# Source pull + window filter.
# ---------------------------------------------------------------------------

@dataclass
class _Windowed:
    frames: list[Frame]
    source_frames: list[SourceFrame]
    requested: WindowRange | None
    evicted: bool
    provenance: SourceProvenance


def _pull(source: FrameSource, window_spec: str) -> _Windowed:
    source_frames = list(source.iter_source_frames())
    all_frames = [sf.frame for sf in source_frames]
    if window_spec == "all":
        requested: WindowRange | None = None
    else:
        last_ts = all_frames[-1].ts if all_frames else 0.0
        requested = parse_window_spec(window_spec, last_ts)
    if requested is None:
        kept = source_frames
    else:
        kept = [sf for sf in source_frames if requested.start_ts <= sf.frame.ts <= requested.end_ts]
    return _Windowed(
        frames=[sf.frame for sf in kept],
        source_frames=kept,
        requested=requested,
        evicted=source.evicted,
        provenance=source.provenance,
    )


# ---------------------------------------------------------------------------
# Entity selection (a miss is empty, never an error).
# ---------------------------------------------------------------------------

def _full_parent_map(frames: list[Frame]) -> dict[str, str | None]:
    parents: dict[str, str | None] = {}
    for frame in frames:
        for key, ef in frame.entities.items():
            parents.setdefault(key, ef.entity.parent)
    return parents


def _select_keys(frames: list[Frame], selector: Selector, parents: dict[str, str | None]) -> list[str]:
    all_keys = list(parents)
    if selector.is_all:
        return sorted(all_keys)
    chosen: set[str] = set()
    for key in all_keys:
        if key in selector.keys:
            chosen.add(key)
        elif any(fnmatch.fnmatchcase(key, pat) for pat in selector.globs):
            chosen.add(key)
        elif selector.slice is not None and _in_slice(key, selector.slice, parents):
            chosen.add(key)
    return sorted(chosen)


def _in_slice(key: str, slice_key: str, parents: dict[str, str | None]) -> bool:
    if key == slice_key:
        return True
    seen: set[str] = set()
    current: str | None = key
    while current is not None and current not in seen:
        seen.add(current)
        parent = parents.get(current)
        if parent == slice_key:
            return True
        current = parent
    return False


# ---------------------------------------------------------------------------
# Registry-driven subtree aggregation (Contract 5 — never assumed additive).
# ---------------------------------------------------------------------------

def subtree_aggregate(
    key: str,
    metric: str,
    own_values: dict[str, float | None],
    children: dict[str, list[str]],
) -> float | None:
    """Aggregate *metric* over the subtree rooted at *key*, per branch_policy.

    * ``kernel_subtree``: the node's own value already includes descendants —
      return it, never the sum of children (which would double-count).
    * ``child_sum``: additive — the node's own value plus each child's subtree.
    * ``local_only``: no subtree meaning — the node's own value alone.
    """
    policy = REGISTRY[metric].branch_policy
    own = own_values.get(key)
    if policy in ("kernel_subtree", "local_only"):
        return own
    total = 0.0
    seen = False
    if own is not None:
        total += own
        seen = True
    for child in children.get(key, ()):
        sub = subtree_aggregate(child, metric, own_values, children)
        if sub is not None:
            total += sub
            seen = True
    return total if seen else None


def _build_tree(
    keys: list[str], parents: dict[str, str | None]
) -> tuple[dict[str, list[str]], list[str]]:
    keyset = set(keys)
    children: dict[str, list[str]] = {k: [] for k in keys}
    roots: list[str] = []
    for key in keys:
        parent = parents.get(key)
        if parent is not None and parent in keyset:
            children[parent].append(key)
        else:
            roots.append(key)
    return children, roots


def _ancestor_path(key: str, parents: dict[str, str | None]) -> list[str]:
    path: list[str] = []
    seen: set[str] = set()
    current = parents.get(key)
    while current is not None and current not in seen:
        seen.add(current)
        path.append(current)
        current = parents.get(current)
    path.reverse()
    return path


# ---------------------------------------------------------------------------
# Result + deterministic serialization.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Result:
    meta: dict[str, Any]
    rows: list[dict[str, Any]]

    def to_jsonable(self) -> dict[str, Any]:
        return {"meta": self.meta, "rows": self.rows}


def format_result(result: Result, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(result.to_jsonable(), sort_keys=True, indent=2)
    return json.dumps(result.to_jsonable(), sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Meta (Contract 4).
# ---------------------------------------------------------------------------

def _compute_meta(query: Query, windowed: _Windowed, *, reset_count: int, truncation: dict[str, Any]) -> dict[str, Any]:
    sframes = windowed.source_frames
    frames = windowed.frames
    observed_start = frames[0].ts if frames else None
    observed_end = frames[-1].ts if frames else None

    gaps: list[dict[str, Any]] = []
    for i in range(1, len(sframes)):
        prev = sframes[i - 1].frame
        cur = sframes[i].frame
        seq_gap = bool(sframes[i].gap_before)
        interval = cur.interval_s if cur.interval_s and cur.interval_s > 0 else prev.interval_s
        temporal_gap = bool(interval and interval > 0 and (cur.ts - prev.ts) > interval * _GAP_FACTOR)
        if seq_gap or temporal_gap:
            gaps.append(
                {
                    "position": i,
                    "from_ts": prev.ts,
                    "to_ts": cur.ts,
                    "sequence_gap": seq_gap,
                    "temporal_gap": temporal_gap,
                }
            )

    evicted = bool(windowed.evicted or (sframes and sframes[0].gap_before))
    complete = (not evicted) and (not gaps)
    requested = None
    if windowed.requested is not None:
        requested = {"start_ts": windowed.requested.start_ts, "end_ts": windowed.requested.end_ts}

    return {
        "shape": query.shape,
        "projection": query.projection,
        "visibility": query.visibility,
        "requested_window": requested,
        "observed_start_ts": observed_start,
        "observed_end_ts": observed_end,
        "sample_count": len(frames),
        "coverage": {
            "frames": len(frames),
            "span_s": (observed_end - observed_start) if frames else 0.0,
            "gap_count": len(gaps),
            "complete": complete,
        },
        "gaps": gaps,
        "eviction": {"occurred": evicted},
        "resets": {"count": reset_count},
        "freshness": {"newest_ts": observed_end, "oldest_ts": observed_start},
        "source": windowed.provenance.to_jsonable(),
        "truncation": truncation,
    }


# ---------------------------------------------------------------------------
# Visibility.
# ---------------------------------------------------------------------------

def _hidden_by_visibility(src: str, visibility: str) -> bool:
    """Under 'available', hide values the collector could not read (unavail_*)."""
    return visibility == "available" and isinstance(src, str) and src.startswith("unavail")


# ---------------------------------------------------------------------------
# Cell builders per shape.
# ---------------------------------------------------------------------------

def _current_cells(
    frames: list[Frame], keys: list[str], metrics: list[_ResolvedMetric], visibility: str
) -> dict[str, dict[str, dict[str, Any]]]:
    last = frames[-1]
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for key in keys:
        ef = last.entities.get(key)
        row: dict[str, dict[str, Any]] = {}
        for rm in metrics:
            mv = ef.metrics.get(rm.name) if ef is not None else None
            if mv is None:
                row[rm.name] = {"value": None, "src": "absent"}
            elif _hidden_by_visibility(mv.src, visibility):
                row[rm.name] = {"value": None, "src": mv.src, "hidden": True}
            else:
                row[rm.name] = {"value": current_value(mv.v), "src": mv.src}
        out[key] = row
    return out


def _summary_cells(
    frames: list[Frame], keys: list[str], metrics: list[_ResolvedMetric], visibility: str
) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    total_resets = 0
    for key in keys:
        row: dict[str, dict[str, Any]] = {}
        for rm in metrics:
            points = collect_points(frames, key, rm.name)
            if visibility == "available":
                points = [p for p in points if not _hidden_by_visibility(p.src, "available")]
            summary = summarize(points, rm.semantic)
            total_resets += summary.resets
            entry: dict[str, Any] = {"semantic": summary.semantic.value, "sample_count": summary.sample_count}
            entry.update(summary.stats)
            row[rm.name] = entry
        out[key] = row
    return out, total_resets


# ---------------------------------------------------------------------------
# Ranking.
# ---------------------------------------------------------------------------

def _cell_stat(cell: dict[str, Any] | None, stat: str) -> float | None:
    if cell is None:
        return None
    val = cell.get(stat)
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return float(val)


def _ordering_key(value: float | None, key: str, reverse: bool) -> tuple[float, str]:
    if value is None:
        value = _NEG_INF if reverse else _POS_INF  # missing sorts last
    primary = -value if reverse else value
    return (primary, key)


def _rank_flat(
    keys: list[str], cells: dict[str, dict[str, dict[str, Any]]], sort: SortSpec
) -> list[str]:
    stat = sort.stat or "value"
    reverse = sort.order == "desc"
    return sorted(
        keys,
        key=lambda k: _ordering_key(_cell_stat(cells.get(k, {}).get(sort.metric), stat), k, reverse),
    )


def _order_children(
    children: dict[str, list[str]],
    roots: list[str],
    own_values: dict[str, float | None],
    metric: str,
    order: str,
) -> None:
    reverse = order == "desc"

    def sort_key(k: str) -> tuple[float, str]:
        return _ordering_key(subtree_aggregate(k, metric, own_values, children), k, reverse)

    roots.sort(key=sort_key)
    for kids in children.values():
        kids.sort(key=sort_key)


# ---------------------------------------------------------------------------
# Projection.
# ---------------------------------------------------------------------------

def _project(
    query: Query,
    keys: list[str],
    parents: dict[str, str | None],
    cells: dict[str, dict[str, dict[str, Any]]],
    sort: SortSpec | None,
    own_values: dict[str, float | None],
) -> list[dict[str, Any]]:
    if query.projection == "flat":
        ordered = sorted(keys) if sort is None else _rank_flat(keys, cells, sort)
        return [
            {"key": key, "path": _ancestor_path(key, parents), "metrics": cells[key]}
            for key in ordered
        ]
    # hierarchy
    children, roots = _build_tree(keys, parents)
    if sort is None:
        roots.sort()
        for kids in children.values():
            kids.sort()
    else:
        _order_children(children, roots, own_values, sort.metric, sort.order)
    rows: list[dict[str, Any]] = []
    stack: list[tuple[str, int]] = [(r, 0) for r in reversed(roots)]
    while stack:
        key, depth = stack.pop()
        row: dict[str, Any] = {
            "key": key,
            "depth": depth,
            "path": _ancestor_path(key, parents),
            "metrics": cells[key],
        }
        if sort is not None:
            policy = REGISTRY[sort.metric].branch_policy
            subtree = subtree_aggregate(key, sort.metric, own_values, children)
            row["subtree"] = {
                "metric": sort.metric,
                "policy": policy,
                "additive": policy == "child_sum",
                "value": round(subtree, 6) if isinstance(subtree, float) else subtree,
            }
        rows.append(row)
        for child in reversed(children.get(key, [])):
            stack.append((child, depth + 1))
    return rows


# ---------------------------------------------------------------------------
# Bounds enforcement.
# ---------------------------------------------------------------------------

def _no_truncation(policy: str) -> dict[str, Any]:
    return {"truncated": False, "policy": policy}


def _apply_row_cap(rows: list[dict[str, Any]], caps: Caps) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    n = len(rows)
    if n <= caps.max_rows:
        return rows, _no_truncation(caps.on_exceed)
    if caps.on_exceed == "error":
        raise BoundExceededError(
            f"result has {n} rows, exceeding max_rows={caps.max_rows}",
            bound="max_rows",
            limit=caps.max_rows,
            observed=n,
        )
    return rows[: caps.max_rows], {
        "truncated": True,
        "policy": "truncate",
        "reason": "max_rows",
        "dropped_rows": n - caps.max_rows,
    }


def _enforce_byte_cap(
    meta: dict[str, Any], rows: list[dict[str, Any]], caps: Caps, truncation: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def encoded_len(kept: list[dict[str, Any]], trunc: dict[str, Any]) -> int:
        meta_copy = dict(meta)
        meta_copy["truncation"] = trunc
        payload = {"meta": meta_copy, "rows": kept}
        return len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    if encoded_len(rows, truncation) <= caps.max_bytes:
        return rows, truncation

    floor_trunc = {"truncated": True, "policy": caps.on_exceed, "reason": "max_bytes", "dropped_rows": len(rows)}
    if encoded_len([], floor_trunc) > caps.max_bytes:
        raise BoundExceededError(
            f"encoded meta alone exceeds max_bytes={caps.max_bytes}",
            bound="max_bytes",
            limit=caps.max_bytes,
            observed=encoded_len([], floor_trunc),
        )
    if caps.on_exceed == "error":
        raise BoundExceededError(
            f"encoded result exceeds max_bytes={caps.max_bytes}",
            bound="max_bytes",
            limit=caps.max_bytes,
            observed=encoded_len(rows, truncation),
        )
    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trial = {"truncated": True, "policy": "truncate", "reason": "max_bytes", "dropped_rows": len(rows) - mid}
        if encoded_len(rows[:mid], trial) <= caps.max_bytes:
            lo = mid
        else:
            hi = mid - 1
    final = {"truncated": True, "policy": "truncate", "reason": "max_bytes", "dropped_rows": len(rows) - lo}
    if truncation.get("truncated"):
        final["also"] = truncation.get("reason")
    return rows[:lo], final


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------

def run_query(source: FrameSource, query: Query) -> Result:
    resolved, sort = _validate(query)
    windowed = _pull(source, query.window_spec)
    parents = _full_parent_map(windowed.frames)
    keys = _select_keys(windowed.frames, query.selector, parents)

    if query.shape == "raw":
        return _run_raw(query, resolved, windowed, keys)
    if query.shape == "current":
        return _run_current(query, resolved, windowed, keys, parents, sort)
    return _run_summary(query, resolved, windowed, keys, parents, sort)


def _run_current(
    query: Query,
    resolved: list[_ResolvedMetric],
    windowed: _Windowed,
    keys: list[str],
    parents: dict[str, str | None],
    sort: SortSpec,
) -> Result:
    if not windowed.frames:
        return _finish(query, windowed, [], reset_count=0)
    cells = _current_cells(windowed.frames, keys, resolved, query.visibility)
    own_values = {k: _cell_stat(cells.get(k, {}).get(sort.metric), "value") for k in keys}
    rows = _project(query, keys, parents, cells, sort, own_values)
    return _finish(query, windowed, rows, reset_count=0)


def _run_summary(
    query: Query,
    resolved: list[_ResolvedMetric],
    windowed: _Windowed,
    keys: list[str],
    parents: dict[str, str | None],
    sort: SortSpec | None,
) -> Result:
    if not windowed.frames:
        return _finish(query, windowed, [], reset_count=0)
    cells, reset_count = _summary_cells(windowed.frames, keys, resolved, query.visibility)
    if sort is None:
        own_values: dict[str, float | None] = {}
    else:
        stat = sort.stat or "value"
        own_values = {k: _cell_stat(cells.get(k, {}).get(sort.metric), stat) for k in keys}
    rows = _project(query, keys, parents, cells, sort, own_values)
    return _finish(query, windowed, rows, reset_count=reset_count)


def _run_raw(
    query: Query,
    resolved: list[_ResolvedMetric],
    windowed: _Windowed,
    keys: list[str],
) -> Result:
    frames = windowed.frames
    caps = query.caps
    series_specs = [(k, rm) for k in keys for rm in resolved]
    upper = len(frames) * len(series_specs)
    if upper > caps.max_points and caps.on_exceed == "error":
        raise BoundExceededError(
            f"raw series would hold up to {upper} points, exceeding max_points={caps.max_points}",
            bound="max_points",
            limit=caps.max_points,
            observed=upper,
        )

    rows: list[dict[str, Any]] = []
    points_used = 0
    emitted_series = 0
    truncated = False
    for key, rm in series_specs:
        if caps.on_exceed == "truncate" and points_used >= caps.max_points:
            truncated = True
            break
        pts: list[dict[str, Any]] = []
        for frame in frames:
            ef = frame.entities.get(key)
            if ef is None:
                continue
            mv = ef.metrics.get(rm.name)
            if mv is None:
                continue
            if _hidden_by_visibility(mv.src, query.visibility):
                continue
            if caps.on_exceed == "truncate" and points_used >= caps.max_points:
                truncated = True
                break
            point: dict[str, Any] = {"ts": frame.ts, "value": current_value(mv.v), "src": mv.src}
            if mv.raw is not None:
                point["raw"] = mv.raw
            pts.append(point)
            points_used += 1
        if pts:
            rows.append({"key": key, "metric": rm.name, "semantic": rm.semantic.value, "points": pts})
            emitted_series += 1

    if truncated:
        truncation = {
            "truncated": True,
            "policy": "truncate",
            "reason": "max_points",
            "emitted_series": emitted_series,
            "total_series": len(series_specs),
        }
    else:
        truncation = _no_truncation(caps.on_exceed)
    return _finish(query, windowed, rows, reset_count=0, precomputed_truncation=truncation)


def _finish(
    query: Query,
    windowed: _Windowed,
    rows: list[dict[str, Any]],
    *,
    reset_count: int,
    precomputed_truncation: dict[str, Any] | None = None,
) -> Result:
    caps = query.caps
    if precomputed_truncation is None:
        rows, truncation = _apply_row_cap(rows, caps)
    else:
        truncation = precomputed_truncation
    meta = _compute_meta(query, windowed, reset_count=reset_count, truncation=truncation)
    rows, truncation = _enforce_byte_cap(meta, rows, caps, truncation)
    meta["truncation"] = truncation
    return Result(meta=meta, rows=rows)
