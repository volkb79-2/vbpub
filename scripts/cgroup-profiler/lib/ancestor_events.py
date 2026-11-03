"""Local ``memory.events`` of a cgroup's ancestors, as deltas between two reads.

A lane's memory stall is a capacity signal when its own limit caused it and
contamination when reclaim was driven from above. An ancestor's *local*
``high`` or ``max`` event during the measured span is direct evidence of the
second case. This module is the one reader shared by the Summary
(``ancestors_memory_events_local``, whole session) and the observer's
measurement-window context (per window).

The list runs from the target's parent up to and including the root of the dev
tree (``dev.slice`` by default). The target itself is never listed: its own
events belong to the Summary's ``events`` block.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

EVENT_KEYS = ("high", "max", "oom", "oom_kill")
DEFAULT_ROOT_CGROUP = "/dev.slice"

Counters = Dict[str, int]


def ancestor_paths(cgroup: str, root: str = DEFAULT_ROOT_CGROUP) -> List[str]:
    """Parent-first ancestors of ``cgroup`` up to and including ``root``.

    When ``cgroup`` is not below ``root`` the walk ends at its top-level
    component instead, so the list is always finite and never includes ``/``.
    """
    path = "/" + cgroup.strip("/")
    root = "/" + root.strip("/")
    out: List[str] = []
    while True:
        parent = os.path.dirname(path) or "/"
        if parent == "/" or path == root:
            break
        out.append(parent)
        if parent == root:
            break
        path = parent
    return out


def read_local_events(directory: str) -> Optional[Counters]:
    """The four event counters of ``memory.events.local``, or None.

    Any missing file, malformed line, or missing counter makes the whole read
    unavailable: a partial map would let a delta pretend an event did not
    happen.
    """
    try:
        with open(os.path.join(directory, "memory.events.local"), encoding="ascii") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeError):
        return None
    values: Counters = {}
    for line in lines:
        fields = line.split()
        if len(fields) != 2 or not fields[1].isdecimal():
            return None
        values[fields[0]] = int(fields[1], 10)
    if not all(key in values for key in EVENT_KEYS):
        return None
    return {key: values[key] for key in EVENT_KEYS}


def read_ancestors(cgroup_root: str, paths: List[str]) -> Dict[str, Optional[Counters]]:
    """One read of every listed ancestor, keyed by cgroup path."""
    return {
        path: read_local_events(os.path.join(cgroup_root, path.lstrip("/")))
        for path in paths
    }


def deltas(
    start: Dict[str, Optional[Counters]], end: Dict[str, Optional[Counters]]
) -> List[Dict[str, Any]]:
    """Entries ``{path, high, max, oom, oom_kill}`` in ``start`` order.

    An ancestor unreadable at either end keeps its entry with null counters.
    """
    entries: List[Dict[str, Any]] = []
    for path, before in start.items():
        after = end.get(path)
        entry: Dict[str, Any] = {"path": path}
        for key in EVENT_KEYS:
            if before is None or after is None:
                entry[key] = None
            else:
                entry[key] = max(0, after[key] - before[key])
        entries.append(entry)
    return entries
