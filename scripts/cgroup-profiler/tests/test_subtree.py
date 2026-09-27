"""Tests for lib/subtree.py — the token subtree resolver (contract §2.2/§7).

Conventions match ``tests/conftest.py``'s ``proc_root``/``cgroup_root``
fixtures: a fake tree built under ``tmp_path``, no real ``/proc`` or
``/sys/fs/cgroup`` ever touched, no sleeping (there is nothing to sleep for —
``refresh()`` takes no clock; the session server decides cadence).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pytest

from lib import subtree


# ── fake /proc + cgroup.procs builders ──────────────────────────────────────

def mkproc(
    root: Path,
    pid: int,
    *,
    ppid: int = 1,
    environ: Optional[List[str]] = None,
    task_children: Optional[Dict[int, List[int]]] = None,
    bare_task_dir: bool = False,
    cgroup: Optional[str] = None,
) -> Path:
    """One ``/proc/<pid>`` directory.

    ``environ`` (a list of ``KEY=VALUE`` strings) writes the NUL-joined
    environ file when given, and is omitted entirely when ``None`` (the
    "unreadable — pid never had one we could see" case, not an empty one).
    ``task_children`` writes ``task/<tid>/children`` for each given
    tid→children mapping (the fast-path interface). ``bare_task_dir=True``
    creates ``task/<pid>/`` with no ``children`` file inside it — an alive
    process on a kernel without ``CONFIG_PROC_CHILDREN``.
    """
    pdir = root / str(pid)
    pdir.mkdir(parents=True, exist_ok=True)
    # proc(5): fields after the last ')' are state, ppid, pgrp, session, ...
    # Padding to a handful of zero fields is enough — _parse_ppid only reads
    # index 1 (ppid).
    (pdir / "stat").write_text(f"{pid} (proc{pid}) S {ppid} 0 0 0 0 0 0\n")
    if cgroup is not None:
        (pdir / "cgroup").write_text(cgroup)
    if environ is not None:
        (pdir / "environ").write_bytes(("\x00".join(environ) + "\x00").encode())
    if task_children is not None:
        for tid, kids in task_children.items():
            tdir = pdir / "task" / str(tid)
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "children").write_text(" ".join(str(k) for k in kids))
    if bare_task_dir:
        (pdir / "task" / str(pid)).mkdir(parents=True, exist_ok=True)
    return pdir


def mkcgroup(root: Path, rel: str, pids: Iterable[int]) -> str:
    """A fake cgroup directory holding ``cgroup.procs``; returns its abs path."""
    cdir = root / rel.strip("/")
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "cgroup.procs").write_text("\n".join(str(p) for p in pids) + "\n")
    return str(cdir)


TOKEN = "abc123def456"
NEEDLE = f"RUN_GATE_PROFILE_SESSION={TOKEN}"


def resolver(cgroup_abs: str, proc_root: Path, token: str = TOKEN) -> subtree.SubtreeResolver:
    target_path = Path(cgroup_abs)
    cgroup_root = target_path.parents[1]
    cgroup = "/" + target_path.relative_to(cgroup_root).as_posix()
    return subtree.SubtreeResolver(
        cgroup=cgroup, cgroup_root=str(cgroup_root), token=token,
        proc_root=str(proc_root),
    )


# ── token ownership ─────────────────────────────────────────────────────────

class TestTokenOwnership:
    def test_private_pid_namespace_filters_token_roots_to_target_cgroup(
        self, tmp_path: Path, monkeypatch,
    ):
        proc = tmp_path / "hostproc"
        mkproc(proc, 3000, cgroup="0::/\n")
        mkproc(
            proc, 3100, environ=[NEEDLE], task_children={3100: [3101]},
            cgroup="0::/lane.scope\n",
        )
        mkproc(
            proc, 3101, ppid=3100, task_children={3101: []},
            cgroup="0::/outside.scope\n",
        )
        mkproc(proc, 3200, environ=[NEEDLE], cgroup="0::/outside.scope\n")
        cgroup = mkcgroup(tmp_path, "cgroup/helper.scope/lane.scope", [0])
        mkcgroup(tmp_path, "cgroup/helper.scope", [3000])
        mkcgroup(tmp_path, "cgroup/helper.scope/outside.scope", [0])
        monkeypatch.setattr(subtree.targets_mod.access, "have_host_proc_view", lambda root: True)
        monkeypatch.setattr(subtree.targets_mod.os, "getpid", lambda: 3000)
        real_read_text = subtree.targets_mod.util.read_text

        def local_and_remote_cgroups(path):
            if path == "/proc/self/cgroup":
                return "0::/\n"
            return real_read_text(path)

        monkeypatch.setattr(subtree.targets_mod.util, "read_text", local_and_remote_cgroups)

        r = resolver(cgroup, proc)

        assert r.refresh() == {3100, 3101}
        assert r.targets_seen == 2

    def test_private_pid_namespace_without_host_proc_listing_has_no_owners(
        self, tmp_path: Path, monkeypatch,
    ):
        proc = tmp_path / "hostproc"
        cgroup = mkcgroup(tmp_path, "cgroup/helper.scope/lane.scope", [0])
        mkcgroup(tmp_path, "cgroup/helper.scope", [3000])
        monkeypatch.setattr(subtree.targets_mod.access, "have_host_proc_view", lambda root: True)
        monkeypatch.setattr(subtree.targets_mod.os, "getpid", lambda: 3000)
        real_read_text = subtree.targets_mod.util.read_text
        monkeypatch.setattr(
            subtree.targets_mod.util, "read_text",
            lambda path: "0::/\n" if path == "/proc/self/cgroup" else real_read_text(path),
        )
        real_listdir = subtree.targets_mod.os.listdir

        def fail_proc_listing(path):
            if str(path) == str(proc):
                raise PermissionError(path)
            return real_listdir(path)

        monkeypatch.setattr(subtree.targets_mod.os, "listdir", fail_proc_listing)

        assert resolver(cgroup, proc).refresh() == set()

    def test_owner_found_among_several_cgroup_pids(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 10, environ=[NEEDLE, "PATH=/usr/bin"])
        mkproc(proc, 11, environ=["PATH=/usr/bin", "OTHER=1"])
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [10, 11])

        r = resolver(cgroup, proc)
        current = r.refresh()

        assert current == {10}
        assert r.current_pids == {10}
        assert r.targets_seen == 1

    def test_exact_match_not_prefix_or_substring(self, tmp_path: Path):
        proc = tmp_path / "proc"
        # A DIFFERENT session's token that happens to be a prefix of ours.
        mkproc(proc, 20, environ=[f"RUN_GATE_PROFILE_SESSION={TOKEN[:-1]}"])
        # Ours appears only as part of a longer, unrelated value — never a
        # standalone environ entry — and must not match via substring.
        mkproc(proc, 21, environ=[f"NOISE=xx{NEEDLE}xx"])
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [20, 21])

        r = resolver(cgroup, proc)
        assert r.refresh() == set()
        assert r.targets_seen == 0

    def test_unreadable_environ_is_skipped_not_fatal(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 30, environ=None)  # no environ file at all
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [30])

        r = resolver(cgroup, proc)
        assert r.refresh() == set()  # no exception raised

    def test_vanished_cgroup_returns_empty_set(self, tmp_path: Path):
        r = resolver(str(tmp_path / "cgroup" / "gone"), tmp_path / "proc")
        assert r.refresh() == set()
        assert r.current_pids == set()
        assert r.targets_seen == 0


# ── descendant discovery: fast path (task/*/children) ───────────────────────

class TestTaskChildrenFastPath:
    def test_two_level_descendants_via_task_children(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 100, environ=[NEEDLE], task_children={100: [101]})
        mkproc(proc, 101, task_children={101: [102]})
        mkproc(proc, 102, task_children={102: []})
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [100])

        r = resolver(cgroup, proc)
        assert r.refresh() == {100, 101, 102}
        assert r.targets_seen == 3

    def test_diamond_shared_descendant_counted_once(self, tmp_path: Path):
        proc = tmp_path / "proc"
        # Two independent token-bearing owners whose children both reparent
        # onto the same grandchild (e.g. after an intermediate process exits
        # and init/subreaper re-parents it) — must appear once, not twice.
        mkproc(proc, 200, environ=[NEEDLE], task_children={200: [202]})
        mkproc(proc, 201, environ=[NEEDLE], task_children={201: [202]})
        mkproc(proc, 202, task_children={202: []})
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [200, 201])

        r = resolver(cgroup, proc)
        current = r.refresh()
        assert current == {200, 201, 202}

    def test_child_listed_but_already_gone_is_still_attributed(self, tmp_path: Path):
        """A child pid named in the parent's ``children`` file at read time,
        but whose own ``/proc/<pid>`` has already vanished by the time we
        probe it further — it was real when discovered, so it counts, even
        though the walk cannot find anything beneath it."""
        proc = tmp_path / "proc"
        mkproc(proc, 300, environ=[NEEDLE], task_children={300: [301]})
        # 301 deliberately not created at all.
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [300])

        r = resolver(cgroup, proc)
        assert r.refresh() == {300, 301}

    def test_owner_vanished_before_probe_does_not_block_detecting_support(
        self, tmp_path: Path
    ):
        """One owner's process is already gone (no /proc/<pid> at all) by the
        time descendants are probed; a second owner still has a live
        ``task/*/children`` interface — support must still be detected from
        the second owner rather than incorrectly falling back."""
        proc = tmp_path / "proc"
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [400, 401])
        # 400: token owner, but its /proc dir will simply not exist — model
        # this by writing cgroup.procs/environ through a wrapper cgroup dir
        # while still creating pid 400's environ (ownership is judged off
        # cgroup.procs + environ, independent of whether pid 400 itself
        # supports the task/children probe).
        mkproc(proc, 400, environ=[NEEDLE])  # no task dir at all -> None
        mkproc(proc, 401, environ=[NEEDLE], task_children={401: [402]})
        mkproc(proc, 402, task_children={402: []})

        r = resolver(cgroup, proc)
        current = r.refresh()
        assert current == {400, 401, 402}


# ── descendant discovery: ppid-map fallback ──────────────────────────────────

class TestPpidMapFallback:
    def test_falls_back_when_children_file_absent(self, tmp_path: Path):
        proc = tmp_path / "proc"
        # bare_task_dir=True: task/<pid>/ exists (process alive) but no
        # "children" file inside it -> the fast path is unsupported.
        mkproc(proc, 500, ppid=1, environ=[NEEDLE], bare_task_dir=True)
        mkproc(proc, 501, ppid=500, bare_task_dir=True)
        mkproc(proc, 502, ppid=501, bare_task_dir=True)
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [500])

        r = resolver(cgroup, proc)
        assert r.refresh() == {500, 501, 502}

    def test_falls_back_when_no_task_directory_at_all(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 600, ppid=1, environ=[NEEDLE])
        mkproc(proc, 601, ppid=600)
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [600])

        r = resolver(cgroup, proc)
        assert r.refresh() == {600, 601}

    def test_ppid_map_ignores_non_numeric_and_unreadable_entries(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 700, ppid=1, environ=[NEEDLE])
        mkproc(proc, 701, ppid=700)
        # A non-pid entry alongside the numeric ones (real /proc always has
        # these: "self", "meminfo", ...) must be skipped, not crash the scan.
        (proc / "meminfo").write_text("MemTotal: 1 kB\n")
        # A numeric-looking directory with no readable stat (e.g. a race
        # between listdir() and the process exiting) contributes nothing.
        (proc / "9999").mkdir()
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [700])

        r = resolver(cgroup, proc)
        assert r.refresh() == {700, 701}

    def test_leaf_owner_has_no_descendants(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 800, ppid=1, environ=[NEEDLE])
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [800])

        r = resolver(cgroup, proc)
        assert r.refresh() == {800}

    def test_owner_that_is_also_a_descendant_of_another_owner_not_double_walked(
        self, tmp_path: Path
    ):
        """Both a parent and its own child re-exec with the same token (a
        wrapper script exporting the env to itself and to what it execs into)
        — both are already in ``owners``, so the ppid-map walk must find the
        child already ``visited`` and skip it, then still discover the
        child's own further descendant."""
        proc = tmp_path / "proc"
        mkproc(proc, 550, ppid=1, environ=[NEEDLE])
        mkproc(proc, 551, ppid=550, environ=[NEEDLE])
        mkproc(proc, 552, ppid=551)
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [550, 551])

        r = resolver(cgroup, proc)
        assert r.refresh() == {550, 551, 552}


# ── running union across repeated refresh() calls ───────────────────────────

class TestRunningUnion:
    def test_targets_seen_survives_a_pid_disappearing(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 900, environ=[NEEDLE], task_children={900: [901]})
        mkproc(proc, 901, task_children={901: []})
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [900])

        r = resolver(cgroup, proc)
        first = r.refresh()
        assert first == {900, 901}
        assert r.targets_seen == 2

        # 901 exits; the owner's children list no longer names it.
        mkproc(proc, 900, environ=[NEEDLE], task_children={900: []})
        second = r.refresh()
        assert second == {900}
        assert r.current_pids == {900}
        # ...but it was real once, and contract §3 wants that history.
        assert r.targets_seen == 2
        assert r.seen_pids == {900, 901}

    def test_no_owners_ever_seen_leaves_targets_seen_at_zero(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 1000, environ=["UNRELATED=1"])
        cgroup = mkcgroup(tmp_path, "cgroup/lane", [1000])

        r = resolver(cgroup, proc)
        r.refresh()
        r.refresh()
        assert r.targets_seen == 0


# ── direct unit coverage of small internals (same convention test_targets.py
# uses for _split_spec/_parse_options/_looks_like_slice: a `_`-prefixed helper
# with an edge case awkward to reach end-to-end gets one direct test) ────────

class TestInternals:
    def test_parse_ppid_missing_file(self, tmp_path: Path):
        assert subtree._parse_ppid(str(tmp_path / "nope" / "stat")) is None

    def test_parse_ppid_no_closing_paren(self, tmp_path: Path):
        stat = tmp_path / "stat"
        stat.write_text("123 no-paren-here S 1 0 0\n")
        assert subtree._parse_ppid(str(stat)) is None

    def test_parse_ppid_too_few_fields_after_paren(self, tmp_path: Path):
        stat = tmp_path / "stat"
        stat.write_text("123 (comm) S\n")
        assert subtree._parse_ppid(str(stat)) is None

    def test_parse_ppid_exactly_two_fields_after_paren_still_parses(self, tmp_path: Path):
        # `len(fields) < 2` must be a STRICT less-than -- exactly 2 fields
        # (state + ppid, nothing more) is the MINIMUM valid case, not a
        # rejection. The sibling "too few fields" test above only covers
        # len==1, which cannot distinguish `<` from `<=`.
        stat = tmp_path / "stat"
        stat.write_text("123 (comm) S 42\n")
        assert subtree._parse_ppid(str(stat)) == 42

    def test_parse_ppid_non_numeric_ppid_field(self, tmp_path: Path):
        stat = tmp_path / "stat"
        stat.write_text("123 (comm) S notanumber 0\n")
        assert subtree._parse_ppid(str(stat)) is None

    def test_parse_ppid_comm_with_parens_and_spaces(self, tmp_path: Path):
        stat = tmp_path / "stat"
        stat.write_text("123 (weird (comm) name) S 42 0 0\n")
        assert subtree._parse_ppid(str(stat)) == 42

    def test_direct_children_via_task_process_gone(self, tmp_path: Path):
        assert subtree._direct_children_via_task(12345, str(tmp_path)) is None

    def test_direct_children_via_task_ignores_non_numeric_children_tokens(
        self, tmp_path: Path
    ):
        proc = tmp_path / "proc"
        tdir = proc / "1" / "task" / "1"
        tdir.mkdir(parents=True)
        (tdir / "children").write_text("2 notanumber 3\n")
        assert subtree._direct_children_via_task(1, str(proc)) == [2, 3]

    def test_build_ppid_map_unreadable_proc_root(self, tmp_path: Path):
        assert subtree._build_ppid_map(str(tmp_path / "nope")) == {}

    def test_environ_has_exact_membership_not_containment(self, tmp_path: Path):
        proc = tmp_path / "proc"
        mkproc(proc, 1, environ=["A=1", NEEDLE, "B=2"])
        assert subtree._environ_has(1, str(proc), NEEDLE) is True
        assert subtree._environ_has(1, str(proc), NEEDLE + "x") is False
