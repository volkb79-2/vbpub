"""Token subtree resolution — RG55-INTERFACE-CONTRACT.md §2.2 / §7.

Scope ``container-shared`` samples a long-lived container whose cgroup is
shared by many unrelated processes (the run-gate devcontainer's exec-mode
lane, for one). The daemon cannot attribute memory/CPU to *the lane* from
the cgroup alone — it has to know which pids in that cgroup belong to the
lane run-gate just started. The contract's answer is an environment-variable
token: run-gate exports ``RUN_GATE_PROFILE_SESSION=<token>`` into the lane
process before it execs, and the daemon finds every pid that carries it,
plus everything that pid has spawned since.

**Two things this module refuses to do**, because the whole daemon leans on
the refusal: raise on a pid that vanished between listing it and reading its
``/proc`` files (containers exit constantly; a resolver that dies on ESRCH-
shaped races would take the whole session down with it), and lose the running
"seen" history across calls — :meth:`SubtreeResolver.refresh` is called once
per discovery interval by the session server's sampling loop, and a pid that
appeared for one tick and exited before the next must still count in
``targets_seen`` (contract §3: "distinct pids ever attributed"), the same
running-union discipline :class:`lib.summary.SummaryAccumulator` applies to
the pids it is fed.

**Descendant discovery** has a fast path and a fallback, tried in that order
every call (never cached across calls: the kernel's ``/proc`` capability is
a `runtime` machine property, but re-checking costs one ``os.path.isdir`` and
buys mid-session portability for free if the fast path's directory ever
becomes readable/unreadable):

* fast path — ``/proc/<pid>/task/<pid>/children`` (``CONFIG_PROC_CHILDREN``,
  present on every kernel this daemon targets but not guaranteed): a
  whitespace-separated list of the thread's direct children, walked
  breadth-first from every token-owning pid.
* fallback — a full ``ppid`` map built once per call from every
  ``/proc/<pid>/stat`` under ``proc_root`` (parsed past the last ``)``, the
  same convention :func:`lib.metrics._proc_cpu_usec` uses for ``proc(5)``'s
  attacker-and-kernel-controlled ``comm`` field), then walked the same way.

Both paths only ever *add* pids to the frontier; a pid whose files vanish
mid-walk (``OSError``) simply contributes no children, it is not an error.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Set

from . import util
from .summary import read_cgroup_pids

_ENVIRON_SEP = "\x00"


def _environ_has(pid: int, proc_root: str, needle: str) -> bool:
    """Whether ``/proc/<pid>/environ`` carries the exact entry ``needle``.

    Read with ``errors="replace"`` per contract §2.2 — an environment value
    holding invalid UTF-8 (a stray binary blob some tool exported) must not
    turn "does this pid carry our token" into a crash. A zombie, a pid that
    exited between being listed and being read, or one this process lacks
    permission for (rare — the daemon runs ``pid: host`` precisely so this
    does not happen for lane processes) all read the same way: "no", never
    an exception.
    """
    path = os.path.join(proc_root, str(pid), "environ")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return False
    return needle in content.split(_ENVIRON_SEP)


def _parse_ppid(stat_path: str) -> Optional[int]:
    """``ppid`` (proc(5) field 4) from a ``/proc/<pid>/stat`` line.

    Field 2 (``comm``) may itself contain spaces or ``)`` characters, so the
    only safe parse is splitting on the *last* ``)`` and counting fields from
    there — the identical convention :func:`lib.metrics._proc_cpu_usec` uses
    for the same file, deliberately not reinvented here.
    """
    text = util.read_text(stat_path)
    if not text:
        return None
    close = text.rfind(")")
    if close == -1:
        return None
    fields = text[close + 1 :].split()
    if len(fields) < 2:
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def _direct_children_via_task(pid: int, proc_root: str) -> Optional[List[int]]:
    """Children of ``pid`` via ``/proc/<pid>/task/*/children``.

    Returns ``None`` (not ``[]``) when the interface itself is unavailable —
    the process is alive (its ``task`` directory exists) but no thread's
    ``children`` file does — so the caller can tell "unsupported kernel, use
    the ppid-map fallback for this entire call" apart from "supported kernel,
    this process just has no children right now".
    """
    task_dir = os.path.join(proc_root, str(pid), "task")
    try:
        tids = os.listdir(task_dir)
    except OSError:
        return None  # process gone: no children, not a fallback trigger
    saw_any_children_file = False
    out: List[int] = []
    for tid in tids:
        children_path = os.path.join(task_dir, tid, "children")
        if os.path.isfile(children_path):
            saw_any_children_file = True
        text = util.read_text(children_path)
        if not text:
            continue
        for token in text.split():
            try:
                out.append(int(token))
            except ValueError:
                continue
    return out if saw_any_children_file else None


def _build_ppid_map(proc_root: str) -> Dict[int, List[int]]:
    """``{ppid: [child, ...]}`` from every numeric ``/proc/<pid>/stat``."""
    out: Dict[int, List[int]] = {}
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        ppid = _parse_ppid(os.path.join(proc_root, name, "stat"))
        if ppid is None:
            continue
        out.setdefault(ppid, []).append(pid)
    return out


class SubtreeResolver:
    """Resolves the live pid set for one token, across repeated discovery.

    One instance per ``container-shared`` session, held alongside its
    :class:`lib.summary.SummaryAccumulator`. The session server calls
    :meth:`refresh` once per discovery interval (contract §2.2: 2 s) and
    feeds :attr:`current_pids` straight into
    ``SummaryAccumulator.add_sample(pids=...)`` — this class never talks to
    cgroup controller files itself beyond ``cgroup.procs`` (the accumulator
    owns everything else about the sample).
    """

    def __init__(self, *, cgroup_abs_path: str, token: str, proc_root: str = "/proc") -> None:
        self.cgroup_abs_path = cgroup_abs_path
        self.token = token
        self.proc_root = proc_root
        self._current: Set[int] = set()
        self._seen: Set[int] = set()

    def refresh(self) -> Set[int]:
        """Re-resolve the subtree now. Returns (and records into
        :attr:`current_pids` / the running :attr:`targets_seen` union) the
        full set: token-owning pids plus every descendant discovered from
        them, regardless of whether a descendant is still in the same
        cgroup (a lane's grandchild that re-execs into a different cgroup is
        still the lane's work)."""
        owners = self._token_owners()
        current = set(owners) | self._descendants(owners)
        self._current = current
        self._seen |= current
        return set(current)

    @property
    def current_pids(self) -> Set[int]:
        """The pid set from the most recent :meth:`refresh` (empty before
        the first call)."""
        return set(self._current)

    @property
    def targets_seen(self) -> int:
        """Distinct pids ever attributed, across every :meth:`refresh` call
        so far (contract §3 ``target.targets_seen``)."""
        return len(self._seen)

    @property
    def seen_pids(self) -> Set[int]:
        return set(self._seen)

    # ── internals ────────────────────────────────────────────────────────

    def _token_owners(self) -> Set[int]:
        needle = f"RUN_GATE_PROFILE_SESSION={self.token}"
        pids = read_cgroup_pids(self.cgroup_abs_path)
        return {pid for pid in pids if _environ_has(pid, self.proc_root, needle)}

    def _descendants(self, owners: Set[int]) -> Set[int]:
        if not owners:
            return set()
        task_support: Optional[bool] = None
        for pid in owners:
            children = _direct_children_via_task(pid, self.proc_root)
            if children is not None:
                task_support = True
                break
            # `_direct_children_via_task` returning None for *this* pid only
            # tells us the pid vanished OR the interface is unsupported; keep
            # probing the remaining owners before concluding "unsupported".
        if task_support:
            return self._walk_via_task(owners)
        # Either every owner vanished (an empty/near-empty result either way)
        # or the fast path is unavailable on this kernel — the ppid-map walk
        # below is correct and safe in both cases.
        return self._walk_via_ppid_map(owners)

    def _walk_via_task(self, owners: Set[int]) -> Set[int]:
        visited = set(owners)
        found: Set[int] = set()
        frontier = list(owners)
        while frontier:
            pid = frontier.pop()
            children = _direct_children_via_task(pid, self.proc_root) or []
            for child in children:
                if child not in visited:
                    visited.add(child)
                    found.add(child)
                    frontier.append(child)
        return found

    def _walk_via_ppid_map(self, owners: Set[int]) -> Set[int]:
        children_map = _build_ppid_map(self.proc_root)
        visited = set(owners)
        found: Set[int] = set()
        frontier = list(owners)
        while frontier:
            pid = frontier.pop()
            for child in children_map.get(pid, ()):
                if child not in visited:
                    visited.add(child)
                    found.add(child)
                    frontier.append(child)
        return found
