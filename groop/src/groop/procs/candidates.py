"""D-019 bounded candidate-union selection — pure, deterministic, unit-testable.

The retained set for one tick is the union of CPU-hot, I/O-hot, selected/
pinned, and recently-hot processes (Required contract 2), deterministically
capped and prioritized (oracle O4), with pinned/selected processes surviving
eviction pressure unconditionally (oracle O5). This module takes already-
computed per-process rates and returns a selection; it never reads ``/proc``
itself, so every oracle can be exercised with plain in-memory fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from groop.config import ProcessConfig
from groop.procs.identity import ProcessKey


@dataclass(frozen=True)
class CandidateReasons:
    pinned: bool = False
    cpu_hot: bool = False
    io_hot: bool = False
    recently_hot: bool = False

    def as_tuple(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.pinned:
            out.append("pinned")
        if self.cpu_hot:
            out.append("cpu_hot")
        if self.io_hot:
            out.append("io_hot")
        if self.recently_hot:
            out.append("recently_hot")
        return tuple(out)


@dataclass
class SelectionResult:
    retained: list[ProcessKey]
    reasons: dict[ProcessKey, CandidateReasons]
    omitted: list[ProcessKey] = field(default_factory=list)
    omitted_reason: dict[ProcessKey, str] = field(default_factory=dict)
    eligible_count: int = 0
    candidate_count: int = 0
    hot_since: dict[ProcessKey, float] = field(default_factory=dict)


def _rank(items: dict[ProcessKey, float], top_n: int) -> list[ProcessKey]:
    ordered = sorted(items.items(), key=lambda kv: (-kv[1], kv[0].pid, kv[0].start_ticks))
    return [key for key, _ in ordered[: max(0, top_n)]]


def _apply_hard_cap(
    ordered_non_pinned: list[ProcessKey], remaining_slots: int
) -> tuple[list[ProcessKey], list[ProcessKey]]:
    """Split priority-ordered non-pinned candidates into (kept, evicted).

    A separate, monkeypatchable seam so the hard-cap oracle (O8) can prove a
    mutation that skips truncation is actually caught by the test suite.
    """
    return ordered_non_pinned[:remaining_slots], ordered_non_pinned[remaining_slots:]


def select_candidates(
    *,
    keys: list[ProcessKey],
    cpu_rate: dict[ProcessKey, float],
    io_rate: dict[ProcessKey, float],
    pinned_pids: frozenset[int],
    hot_since: dict[ProcessKey, float],
    now: float,
    config: ProcessConfig,
    eligible_count: int,
) -> SelectionResult:
    """Select and cap this tick's retained process union.

    ``hot_since`` is the previous tick's recently-hot bookkeeping; the
    returned :class:`SelectionResult` carries the updated map (this function
    has no side effects on the caller's dict).
    """
    key_by_pid: dict[int, ProcessKey] = {}
    for key in keys:
        # A pid may briefly appear twice only if the caller passed stale data;
        # keep the highest start_ticks (the newest incarnation) deterministically.
        current = key_by_pid.get(key.pid)
        if current is None or key.start_ticks > current.start_ticks:
            key_by_pid[key.pid] = key

    pinned_all = sorted(
        (key_by_pid[pid] for pid in pinned_pids if pid in key_by_pid),
        key=lambda k: k.pid,
    )
    pinned_retained = pinned_all[: config.pinned_cap]
    pinned_overflow = pinned_all[config.pinned_cap :]
    pinned_retained_set = set(pinned_retained)

    cpu_hot_list = _rank(cpu_rate, config.top_cpu)
    io_hot_list = _rank(io_rate, config.top_io)
    cpu_rank_index = {key: i for i, key in enumerate(cpu_hot_list)}
    io_rank_index = {key: i for i, key in enumerate(io_hot_list)}

    updated_hot_since = dict(hot_since)
    for key in cpu_hot_list:
        updated_hot_since[key] = now
    for key in io_hot_list:
        updated_hot_since[key] = now
    grace = config.recently_hot_grace_seconds
    updated_hot_since = {
        key: ts for key, ts in updated_hot_since.items() if now - ts <= grace
    }
    recently_hot_set = set(updated_hot_since)

    raw_union = pinned_retained_set | set(cpu_hot_list) | set(io_hot_list) | recently_hot_set
    candidate_count = len(raw_union)

    non_pinned = raw_union - pinned_retained_set

    def _sort_key(key: ProcessKey) -> tuple[int, float, int]:
        cpu_r = cpu_rank_index.get(key)
        io_r = io_rank_index.get(key)
        if cpu_r is not None or io_r is not None:
            best = min(v for v in (cpu_r, io_r) if v is not None)
            return (0, float(best), key.pid)
        # Recently-hot only: more recently hot sorts first.
        return (1, -updated_hot_since.get(key, 0.0), key.pid)

    ordered_non_pinned = sorted(non_pinned, key=_sort_key)
    remaining_slots = max(0, config.hard_cap - len(pinned_retained))
    kept_non_pinned, evicted_non_pinned = _apply_hard_cap(ordered_non_pinned, remaining_slots)

    retained = list(pinned_retained) + kept_non_pinned

    reasons: dict[ProcessKey, CandidateReasons] = {}
    for key in retained:
        reasons[key] = CandidateReasons(
            pinned=key in pinned_retained_set,
            cpu_hot=key in cpu_rank_index,
            io_hot=key in io_rank_index,
            recently_hot=key in recently_hot_set and key not in cpu_rank_index and key not in io_rank_index,
        )

    omitted: list[ProcessKey] = list(pinned_overflow) + evicted_non_pinned
    omitted_reason: dict[ProcessKey, str] = {key: "pinned_cap_exceeded" for key in pinned_overflow}
    for key in evicted_non_pinned:
        omitted_reason[key] = "hard_cap"

    return SelectionResult(
        retained=retained,
        reasons=reasons,
        omitted=omitted,
        omitted_reason=omitted_reason,
        eligible_count=eligible_count,
        candidate_count=candidate_count,
        hot_since=updated_hot_since,
    )
