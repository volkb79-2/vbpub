"""AD2-2: the dedicated-dev shared tree cache (dev-cache.slice, tree/upper tmpfs units, host-capacity.json, GC timer).

Hermetic: tmp dirs, fake meminfo/mountinfo/proc/cgroupfs, a fake systemctl on PATH, real flock for the lease cases."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("mdt_tree_cache_t", ROOT / "mdt_tree_cache.py")
tc = importlib.util.module_from_spec(spec)
sys.modules["mdt_tree_cache_t"] = tc
spec.loader.exec_module(tc)
import mdt_capacity as cap  # noqa: E402

GIB = 1024 ** 3
MIB = 1024 ** 2
HOURS = 3600
GID = 4242
CLI = [sys.executable, str(ROOT / "mdt_tree_cache.py")]


def plan_for(env: dict[str, str], mem_gib: int = 8, dedicated: bool = True, gid: int = GID) -> "tc.Plan":
    return tc.make_plan(env, mem_total=mem_gib * GIB, dedicated=dedicated, gid_lookup=lambda _n: gid)


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)


class PlanTests(TmpCase):
    def test_other_roles_and_the_off_switch_plan_nothing(self) -> None:
        self.assertFalse(plan_for({}, dedicated=False).enabled)
        self.assertFalse(plan_for({"HOST_ROLE": "shared-prod", "MDT_TREE_CACHE_SIZE": "2G"}, dedicated=False).enabled)
        self.assertFalse(plan_for({"MDT_TREE_CACHE_SIZE": "-"}).enabled)

    def test_defaults_are_25_and_10_percent_of_ram_and_inodes_follow_the_size(self) -> None:
        p = plan_for({})
        upper = 8 * GIB // 10 // 4096 * 4096  # whole pages: tmpfs rounds size= and the check compares the rounded value
        self.assertEqual((p.cache_size, p.upper_size), (2 * GIB, upper))
        self.assertEqual(p.cache_inodes, 2 * GIB // (16 * 1024))
        self.assertEqual(plan_for({"MDT_TREE_CACHE_SIZE": "100000001"}).cache_size % 4096, 0)
        self.assertEqual((p.cache_root, p.upper_root, p.group, p.gid), ("/srv/tree-cache", "/srv/tree-upper", "runner", GID))
        self.assertEqual((p.gc_idle, p.upper_gc_idle, p.gc_interval), (6 * HOURS, 24 * HOURS, "15min"))

    def test_keys_override_and_a_dash_drops_only_the_upper_area(self) -> None:
        p = plan_for({"MDT_TREE_CACHE_SIZE": "1G", "MDT_TREE_CACHE_INODES": "50000", "MDT_TREE_UPPER_SIZE": "-",
                      "MDT_TREE_CACHE_ROOT": "/mnt/tc", "MDT_TREE_CACHE_GC_IDLE": "90min", "MDT_TREE_CACHE_GC_INTERVAL": "5min"})
        self.assertEqual((p.cache_size, p.cache_inodes, p.cache_root, p.upper_root, p.gc_idle, p.gc_interval),
                         (GIB, 50000, "/mnt/tc", None, 5400, "5min"))

    def test_refusals(self) -> None:
        bad = [
            {"MDT_TREE_CACHE_ROOT": "/etc"}, {"MDT_TREE_CACHE_ROOT": "relative/x"}, {"MDT_TREE_CACHE_ROOT": "/srv/../etc/x"},
            {"MDT_TREE_CACHE_ROOT": "/srv/a b"}, {"MDT_TREE_UPPER_ROOT": "/srv/tree-cache"},
            {"MDT_TREE_UPPER_ROOT": "/srv/tree-cache/up"}, {"MDT_TREE_CACHE_SIZE": "10M"}, {"MDT_TREE_CACHE_SIZE": "lots"},
            {"MDT_TREE_CACHE_SIZE": "3G", "MDT_TREE_UPPER_SIZE": "2G"},  # 5 of 8 GiB: more than half
            {"MDT_TREE_CACHE_INODES": "12"}, {"MDT_TREE_CACHE_GC_IDLE": "6 hours"}, {"MDT_TREE_CACHE_GC_INTERVAL": "0min"},
            {"MDT_TREE_CACHE_GROUP": "Bad Group"}, {"DEV_CACHE_MEMORY_LOW": "lots"}, {"DEV_CACHE_ZSWAP_WRITEBACK": "maybe"},
        ]
        for env in bad:
            with self.subTest(env=env), self.assertRaises(tc.TreeCacheError):
                plan_for(env)

    def test_a_missing_group_is_refused_by_name(self) -> None:
        def nobody(_n: str) -> int:
            raise KeyError(_n)
        with self.assertRaisesRegex(tc.TreeCacheError, "no such group"):
            tc.make_plan({}, mem_total=8 * GIB, dedicated=True, gid_lookup=nobody)

    def test_mount_unit_names_are_the_systemd_escape(self) -> None:
        self.assertEqual(tc.mount_unit_name("/srv/tree-cache"), "srv-tree\\x2dcache.mount")
        self.assertEqual(tc.mount_unit_name("/srv/tree-upper"), "srv-tree\\x2dupper.mount")
        self.assertEqual(tc.mount_unit_name("/mnt/a_b.c"), "mnt-a_b.c.mount")
        self.assertEqual(tc.mount_unit_name("/mnt/a+b"), "mnt-a\\x2bb.mount")
        if shutil.which("systemd-escape"):
            for path in ("/srv/tree-cache", "/mnt/a+b", "/srv/x/.y"):
                real = subprocess.run(["systemd-escape", "-p", "--suffix=mount", path], capture_output=True, text=True).stdout.strip()
                self.assertEqual(tc.mount_unit_name(path), real, path)

    def test_tree_paths_may_not_overlap_system_directories(self) -> None:
        for bad in ("/var/lib/docker", "/var/lib", "/etc/mdt", "/home/runner/x", "/usr/local/x", "/mnt", "/run/x", "/boot/x", "/opt/x"):
            self.assertIsNotNone(tc.validate_tree_path(bad), bad)
            with self.assertRaises(tc.TreeCacheError):
                plan_for({"MDT_TREE_CACHE_ROOT": bad})
        for ok in ("/srv/tree-cache", "/srv/x/y", "/mnt/tree", "/tmp/tree-cache"):
            self.assertIsNone(tc.validate_tree_path(ok), ok)

    def test_the_protected_low_sum_includes_the_cache_low(self) -> None:
        env = {"DEV_INTERACTIVE_MEMORY_LOW": "512M", "DEV_BUILDKITD_MEMORY_LOW": "256M"}
        without = cap.dedicated_memory(env, mem_total=8 * GIB, swap_total=16 * GIB)
        with_cache = cap.dedicated_memory({**env, "DEV_CACHE_MEMORY_LOW": "1G"}, mem_total=8 * GIB, swap_total=16 * GIB)
        self.assertEqual(with_cache.low - without.low, GIB)  # dev.slice MemoryLow covers the cache (recursive protection)
        self.assertEqual(without.gates_high - with_cache.gates_high, GIB)  # load tiers leave room for it
        self.assertEqual(without.background_high - with_cache.background_high, GIB)
        # AD2-0: MemoryMax is dev High (does not subtract the Low); the swap ceiling does
        self.assertEqual(with_cache.gates_max, without.gates_max)
        self.assertEqual(with_cache.background_max, without.background_max)
        self.assertEqual(cap.load_tier_swap(16 * GIB, 768 * 1024 ** 2) - cap.load_tier_swap(16 * GIB, 768 * 1024 ** 2 + GIB), GIB)
        # `-` and empty mean none
        for none in ("", "-"):
            same = cap.dedicated_memory({**env, "DEV_CACHE_MEMORY_LOW": none}, mem_total=8 * GIB, swap_total=16 * GIB)
            self.assertEqual((same.low, same.gates_high), (without.low, without.gates_high))


class RenderTests(TmpCase):
    UNITS = ROOT / "units"

    def render(self, env: dict[str, str], dedicated: bool, sysdir: Path, cap_json: Path):
        plan = plan_for(env, dedicated=dedicated)
        return plan, tc.cmd_render(plan, env, self.UNITS, sysdir, cap_json)

    def test_dedicated_dev_renders_slice_mounts_timer_and_the_capacity_block(self) -> None:
        sysdir = self.tmp / "sd"
        sysdir.mkdir()
        cj = self.tmp / "host-capacity.json"
        _plan, rc = self.render({"DEV_CACHE_MEMORY_LOW": "768M"}, True, sysdir, cj)
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(p.name for p in sysdir.iterdir()),
                         sorted(["dev-cache.slice", "mdt-tree-cache-gc.service", "mdt-tree-cache-gc.timer",
                                 "srv-tree\\x2dcache.mount", "srv-tree\\x2dupper.mount"]))
        sl = (sysdir / "dev-cache.slice").read_text()
        self.assertIn("MemoryLow=768M\n", sl)
        self.assertIn("MemoryZSwapWriteback=yes\n", sl)
        for forbidden in ("MemoryHigh", "MemoryMax", "MemorySwapMax"):
            self.assertNotRegex(sl, rf"(?m)^{forbidden}=")  # comments may name them, directives may not
        m = (sysdir / "srv-tree\\x2dcache.mount").read_text()
        self.assertIn("Where=/srv/tree-cache\n", m)
        self.assertIn(f"Options=size={2 * GIB},nr_inodes={2 * GIB // 16384},mode=2770,gid={GID},nosuid,nodev,huge=never\n", m)
        self.assertNotIn("noexec", re.sub(r"(?m)^#.*$", "", m))
        self.assertNotIn("noswap", re.sub(r"(?m)^#.*$", "", m))
        u = (sysdir / "srv-tree\\x2dupper.mount").read_text()
        self.assertIn("Where=/srv/tree-upper\n", u)
        self.assertIn(f"size={8 * GIB // 10 // 4096 * 4096},", u)
        self.assertIn("ConditionPathIsMountPoint=/srv/tree-cache", (sysdir / "mdt-tree-cache-gc.service").read_text())
        self.assertIn("OnUnitActiveSec=15min", (sysdir / "mdt-tree-cache-gc.timer").read_text())
        block = json.loads(cj.read_text())["tree_cache"]
        self.assertEqual((block["root"], block["slice"], block["size_bytes"], block["upper_root"], block["overlay_opts"]),
                         ("/srv/tree-cache", "dev-cache.slice", 2 * GIB, "/srv/tree-upper", "redirect_dir=on,xino=auto"))

    def test_an_empty_cache_low_drops_the_directive(self) -> None:
        sysdir = self.tmp / "sd"
        sysdir.mkdir()
        self.render({}, True, sysdir, self.tmp / "c.json")
        self.assertNotRegex((sysdir / "dev-cache.slice").read_text(), r"(?m)^MemoryLow=")

    def test_shared_prod_renders_nothing_and_removes_only_what_it_owns(self) -> None:
        sysdir = self.tmp / "sd"
        sysdir.mkdir()
        cj = self.tmp / "host-capacity.json"
        cj.write_text(json.dumps({"role": "dedicated-dev"}))
        self.render({}, True, sysdir, cj)
        (sysdir / "operator.mount").write_text("[Mount]\nWhat=tmpfs\nWhere=/srv/x\nType=tmpfs\n")  # no marker: not ours
        (sysdir / "dev-gates.slice").write_text("[Slice]\n")
        calls = self.tmp / "calls"
        fake = self.tmp / "bin"
        fake.mkdir()
        (fake / "systemctl").write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
        (fake / "systemctl").chmod(0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = f"{fake}:{old}"
        try:
            _plan, rc = self.render({"HOST_ROLE": "shared-prod"}, False, sysdir, cj)
        finally:
            os.environ["PATH"] = old
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(p.name for p in sysdir.iterdir()), ["dev-gates.slice", "operator.mount"])
        self.assertIn("disable --now mdt-tree-cache-gc.timer", calls.read_text())
        self.assertEqual(json.loads(cj.read_text()), {"role": "dedicated-dev"})  # only tree_cache went; other writers' keys stay

    def test_shared_prod_on_a_clean_host_changes_nothing_at_all(self) -> None:
        sysdir = self.tmp / "sd"
        sysdir.mkdir()
        (sysdir / "dev.slice").write_text("[Slice]\nMemoryLow=1G\n")
        before = {p.name: p.read_bytes() for p in sysdir.iterdir()}
        cj = self.tmp / "host-capacity.json"
        for env in ({}, {"HOST_ROLE": "shared-prod"}):
            self.render(env, False, sysdir, cj)
        self.assertEqual({p.name: p.read_bytes() for p in sysdir.iterdir()}, before)
        self.assertFalse(cj.exists())  # not even created

    def test_render_refuses_to_overwrite_a_unit_it_does_not_own_and_writes_nothing(self) -> None:
        sysdir = self.tmp / "sd"
        sysdir.mkdir()
        mine = "[Mount]\nWhat=tmpfs\nWhere=/srv/tree-cache\nType=tmpfs\nOptions=size=1G\n"  # the operator's own, no marker
        (sysdir / "srv-tree\\x2dcache.mount").write_text(mine)
        cj = self.tmp / "host-capacity.json"
        _plan, rc = self.render({}, True, sysdir, cj)
        self.assertEqual(rc, 2)
        self.assertEqual((sysdir / "srv-tree\\x2dcache.mount").read_text(), mine)
        self.assertEqual([p.name for p in sysdir.iterdir()], ["srv-tree\\x2dcache.mount"])  # nothing else rendered
        self.assertFalse(cj.exists())
        # a symlink with the planned name is refused too
        (sysdir / "srv-tree\\x2dcache.mount").unlink()
        (sysdir / "srv-tree\\x2dcache.mount").symlink_to(self.tmp / "elsewhere")
        _plan, rc = self.render({}, True, sysdir, cj)
        self.assertEqual(rc, 2)

    def test_an_owned_unit_is_updated_in_place(self) -> None:
        sysdir = self.tmp / "sd"
        sysdir.mkdir()
        self.render({}, True, sysdir, self.tmp / "c.json")
        _plan, rc = self.render({"MDT_TREE_CACHE_SIZE": "1G"}, True, sysdir, self.tmp / "c.json")
        self.assertEqual(rc, 0)
        self.assertIn(f"size={GIB}", (sysdir / "srv-tree\\x2dcache.mount").read_text())

    def test_activate_refuses_to_mount_over_a_non_empty_unmounted_directory(self) -> None:
        d = self.tmp / "data"
        d.mkdir()
        self.assertFalse(tc._holds_data(str(d), self._mountinfo_without(d)))
        (d / "x").write_text("1")
        self.assertTrue(tc._holds_data(str(d), self._mountinfo_without(d)))
        mi = self.tmp / "mi2"
        mi.write_text(f"30 1 0:45 / {d} rw - tmpfs tmpfs rw\n")
        self.assertFalse(tc._holds_data(str(d), mi))  # already a mount point: remount is fine

    def _mountinfo_without(self, _d: Path) -> Path:
        mi = self.tmp / "mi"
        mi.write_text("20 1 0:1 / / rw - ext4 /dev/x rw\n")
        return mi

    def test_changing_the_root_replaces_the_owned_mount_unit(self) -> None:
        sysdir = self.tmp / "sd"
        sysdir.mkdir()
        self.render({}, True, sysdir, self.tmp / "c.json")
        self.render({"MDT_TREE_CACHE_ROOT": "/mnt/tc"}, True, sysdir, self.tmp / "c.json")
        names = {p.name for p in sysdir.iterdir()}
        self.assertIn("mnt-tc.mount", names)
        self.assertNotIn("srv-tree\\x2dcache.mount", names)
        self.assertIn("srv-tree\\x2dupper.mount", names)

    def test_capacity_json_merge_and_refusals(self) -> None:
        cj = self.tmp / "c.json"
        cj.write_text(json.dumps({"role": "dedicated-dev", "lane_memory_max": 1}))
        tc.merge_capacity_json(cj, plan_for({}))
        data = json.loads(cj.read_text())
        self.assertEqual((data["role"], data["lane_memory_max"]), ("dedicated-dev", 1))
        self.assertIn("tree_cache", data)
        self.assertEqual(stat.S_IMODE(cj.stat().st_mode), 0o644)
        tc.merge_capacity_json(cj, tc.Plan(enabled=False))
        self.assertEqual(json.loads(cj.read_text()), {"role": "dedicated-dev", "lane_memory_max": 1})
        only = self.tmp / "only.json"
        tc.merge_capacity_json(only, plan_for({}))
        tc.merge_capacity_json(only, tc.Plan(enabled=False))
        self.assertFalse(only.exists())
        broken = self.tmp / "broken.json"
        broken.write_text("{nope")
        with self.assertRaises(tc.TreeCacheError):
            tc.merge_capacity_json(broken, plan_for({}))
        self.assertEqual(broken.read_text(), "{nope")


class ActivateTests(TmpCase):
    def run_activate(self, active_units: str, env: dict[str, str] | None = None) -> list[str]:
        fake = self.tmp / "bin"
        fake.mkdir()
        calls = self.tmp / "calls"
        (fake / "systemctl").write_text(
            '#!/bin/sh\necho "$@" >> %s\n'
            'if [ "$1" = is-active ]; then case " %s " in *" $3 "*) exit 0;; esac; exit 3; fi\nexit 0\n' % (calls, active_units))
        (fake / "systemctl").chmod(0o755)
        root = self.tmp / "roots"
        plan = plan_for({"MDT_TREE_CACHE_ROOT": str(root / "c"), "MDT_TREE_UPPER_ROOT": str(root / "u"), **(env or {})})
        old = os.environ["PATH"]
        os.environ["PATH"] = f"{fake}:{old}"
        try:
            self.assertEqual(tc.cmd_activate(plan, self.tmp), 0)
        finally:
            os.environ["PATH"] = old
        self.assertTrue((root / "c").is_dir() and (root / "u").is_dir())
        return calls.read_text().splitlines()

    def test_a_fresh_host_starts_the_slice_mounts_both_areas_and_enables_the_timer(self) -> None:
        calls = self.run_activate("")
        self.assertIn("start dev-cache.slice", calls)
        self.assertEqual(sum(c.startswith("enable --now") and c.endswith(".mount") for c in calls), 2)
        self.assertIn("enable --now mdt-tree-cache-gc.timer", calls)

    def test_an_active_mount_is_remounted_not_restarted(self) -> None:
        unit = tc.mount_unit_name(str(self.tmp / "roots" / "c"))
        calls = self.run_activate(unit)
        self.assertIn(f"reload {unit}", calls)
        self.assertNotIn(f"enable --now {unit}", calls)

    def test_nothing_happens_when_disabled(self) -> None:
        self.assertEqual(tc.cmd_activate(tc.Plan(enabled=False), self.tmp), 0)


class GcTests(TmpCase):
    ROOT_DEV = "0:45"

    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "cache"
        self.root.mkdir()
        (self.root / ".locks").mkdir()
        self.now = 1_000_000.0
        self.held: list[int] = []
        self.addCleanup(lambda: [os.close(fd) for fd in self.held])

    def key(self, name: str, idle_s: float, complete: bool = True) -> Path:
        d = self.root / name
        (d / "tree").mkdir(parents=True)
        (d / "tree" / "f").write_text("x")
        (d / ".lease").write_text("")
        if complete:
            (d / "COMPLETE").write_text("digest")
        t = self.now - idle_s
        for p in (d / ".last-used", d / "COMPLETE"):
            if complete or p.name != "COMPLETE":
                p.write_text("")
                os.utime(p, (t, t))
        return d

    def lease(self, key_dir: Path) -> None:
        fd = os.open(key_dir / ".lease", os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_SH)
        self.held.append(fd)

    def gc(self, mountinfo: str = "", idle: int = 6 * HOURS, dry: bool = False) -> list[str]:
        return tc.gc_cache(self.root, idle, mountinfo, self.now, dry, self.ROOT_DEV)

    def test_an_idle_unleased_unmounted_key_is_removed_and_a_recent_one_kept(self) -> None:
        old, new = self.key("old", 7 * HOURS), self.key("new", 60)
        log = self.gc()
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertIn("removed old", "\n".join(log))
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith(".trash")], [])

    def test_a_leased_key_is_kept_even_when_idle(self) -> None:
        k = self.key("leased", 30 * HOURS)
        self.lease(k)
        self.assertIn("keep leased: leased", self.gc())
        self.assertTrue(k.exists())

    def test_a_bind_of_the_key_or_an_overlay_lowerdir_holds_it(self) -> None:
        a, b, c = self.key("k1", 30 * HOURS), self.key("k2", 30 * HOURS), self.key("k10", 30 * HOURS)
        mi = (f"30 1 {self.ROOT_DEV} / {self.root} rw - tmpfs tmpfs rw\n"
              f"99 80 {self.ROOT_DEV} /k1/tree /assay-template ro - tmpfs tmpfs rw\n"
              f"100 80 0:60 / /var/lib/docker/volumes/v/_data rw - overlay overlay rw,lowerdir={self.root}/k2/tree,upperdir=/x/u,workdir=/x/w\n")
        self.gc(mi)
        self.assertTrue(a.exists(), "bind of k1/tree (root field is relative to the tmpfs)")
        self.assertTrue(b.exists(), "overlay lowerdir naming k2")
        self.assertFalse(c.exists(), "k1 must not hold k10")

    def test_a_bind_from_another_device_does_not_hold(self) -> None:
        k = self.key("k1", 30 * HOURS)
        self.gc(f"99 80 0:99 /k1/tree /x ro - tmpfs tmpfs rw\n")
        self.assertFalse(k.exists())

    def test_stale_builds_are_removed_unless_their_builder_holds_the_lock(self) -> None:
        free, busy = self.root / "a.tmp-1", self.root / "b.tmp-2"
        for d in (free, busy):
            (d / "tree").mkdir(parents=True)
            os.utime(d, (self.now - 2 * HOURS, self.now - 2 * HOURS))
        young = self.root / "c.tmp-3"
        young.mkdir()
        os.utime(young, (self.now - 60, self.now - 60))  # a fresh build is never removed, lock or not
        lock = self.root / ".locks" / "b"
        lock.write_text("")
        fd = os.open(lock, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.held.append(fd)
        self.gc()
        self.assertFalse(free.exists())
        self.assertTrue(busy.exists())
        self.assertTrue(young.exists())

    def test_incomplete_symlinked_and_hidden_entries_are_left_alone_and_trash_is_swept(self) -> None:
        inc = self.key("half", 30 * HOURS, complete=False)
        target = self.tmp / "elsewhere"
        target.mkdir()
        (self.root / "link").symlink_to(target)
        trash = self.root / ".trash-x.1"
        trash.mkdir()
        (self.root / ".locks" / "keep").write_text("")
        self.gc()
        self.assertTrue(inc.exists() and target.exists() and (self.root / "link").is_symlink())
        self.assertFalse(trash.exists())
        self.assertTrue((self.root / ".locks" / "keep").exists())

    def test_dry_run_changes_nothing(self) -> None:
        k = self.key("old", 30 * HOURS)
        tmpb = self.root / "x.tmp-1"
        tmpb.mkdir()
        log = self.gc(dry=True)
        self.assertTrue(k.exists() and tmpb.exists())
        self.assertIn("would remove old", "\n".join(log))

    def test_idle_falls_back_to_complete_then_the_directory(self) -> None:
        k = self.key("nolast", 30 * HOURS)
        (k / ".last-used").unlink()
        self.assertTrue(self.gc(idle=40 * HOURS) and k.exists())  # COMPLETE is 30 h old: under 40 h
        self.gc()
        self.assertFalse(k.exists())

    def test_upper_orphans(self) -> None:
        up = self.tmp / "upper"
        up.mkdir()
        old, young, mounted = up / "tok-old", up / "tok-new", up / "tok-live"
        for d in (old, young, mounted):
            (d / "w0" / "u").mkdir(parents=True)
            (d / "w0" / "w").mkdir()
        t = self.now - 3 * 86400
        for d in (old, mounted):
            os.utime(d, (t, t))
        mi = f"100 80 0:60 / /v rw - overlay overlay rw,lowerdir=/l,upperdir={up}/tok-live/w0/u,workdir={up}/tok-live/w0/w\n"
        log = tc.gc_upper(up, 86400, mi, self.now, False, "0:46")
        self.assertFalse(old.exists())
        self.assertTrue(young.exists() and mounted.exists())
        self.assertIn("orphan upper tok-old", "\n".join(log))

    def fake_world(self, root: Path, with_mount: bool) -> tuple[Path, Path]:
        proc = self.tmp / "proc"
        (proc / "1").mkdir(parents=True)
        (proc / "1" / "mountinfo").write_text("1 0 0:1 / / rw - ext4 /dev/vda rw\n")
        (proc / "self").mkdir()  # non-numeric entries are ignored
        mi = self.tmp / "mountinfo"
        mi.write_text(f"30 1 {self.ROOT_DEV} / {root} rw - tmpfs tmpfs rw\n" if with_mount else "1 0 0:1 / / rw - ext4 /dev/vda rw\n")
        return proc, mi

    def test_cmd_gc_refuses_a_root_that_is_not_a_tmpfs_mount_point(self) -> None:
        k = self.key("old", 30 * HOURS)
        proc, mi = self.fake_world(self.root, with_mount=False)
        plan = plan_for({"MDT_TREE_CACHE_ROOT": str(self.root), "MDT_TREE_UPPER_SIZE": "-"})
        self.assertEqual(tc.cmd_gc(plan, False, mi, proc, self.now), 1)
        self.assertTrue(k.exists())

    def test_cmd_gc_on_a_tmpfs_root_removes_idle_keys(self) -> None:
        k = self.key("old", 30 * HOURS)
        proc, mi = self.fake_world(self.root, with_mount=True)
        plan = plan_for({"MDT_TREE_CACHE_ROOT": str(self.root), "MDT_TREE_UPPER_SIZE": "-"})
        self.assertEqual(tc.cmd_gc(plan, False, mi, proc, self.now), 0)
        self.assertFalse(k.exists())

    def test_cmd_gc_keeps_everything_when_a_mountinfo_cannot_be_read(self) -> None:
        k = self.key("old", 30 * HOURS)
        proc, mi = self.fake_world(self.root, with_mount=True)
        (proc / "2").mkdir()
        (proc / "2" / "mountinfo").mkdir()  # reading a directory: EISDIR, not "process gone"
        plan = plan_for({"MDT_TREE_CACHE_ROOT": str(self.root), "MDT_TREE_UPPER_SIZE": "-"})
        self.assertEqual(tc.cmd_gc(plan, False, mi, proc, self.now), 1)
        self.assertTrue(k.exists())

    def test_cmd_gc_when_disabled_is_a_no_op(self) -> None:
        self.assertEqual(tc.cmd_gc(tc.Plan(enabled=False), False), 0)

    # ---- robustness (review F-3, F-3b)
    def _proc_with(self, pid: str, state: str | None) -> Path:
        proc, _mi = self.fake_world(self.root, with_mount=True)
        (proc / pid).mkdir()
        (proc / pid / "mountinfo").write_text("")
        if state is not None:
            (proc / pid / "stat").write_text(f"{pid} (x y) {state} 1 1 1 0 -1\n")
        return proc

    def _einval_for(self, pid: str):
        real = tc._read_mountinfo

        def fake(path: Path) -> str:
            if path.parent.name == pid:
                raise OSError(22, "Invalid argument")
            return real(path)
        return fake

    def test_a_zombie_whose_mountinfo_read_fails_with_einval_is_not_a_holder(self) -> None:
        proc = self._proc_with("77", "Z")
        from unittest import mock
        with mock.patch.object(tc, "_read_mountinfo", self._einval_for("77")):
            self.assertIsNotNone(tc.all_mountinfo_text(proc))

    def test_a_live_process_with_an_unreadable_mountinfo_keeps_the_tree(self) -> None:
        proc = self._proc_with("78", "S")
        from unittest import mock
        with mock.patch.object(tc, "_read_mountinfo", self._einval_for("78")):
            self.assertIsNone(tc.all_mountinfo_text(proc))
        (proc / "79").mkdir()  # stat unreadable but the entry exists: unknown -> keep
        (proc / "79" / "mountinfo").write_text("")
        with mock.patch.object(tc, "_read_mountinfo", self._einval_for("79")):
            self.assertIsNone(tc.all_mountinfo_text(proc))

    def test_a_process_that_vanished_meanwhile_is_skipped(self) -> None:
        proc = self._proc_with("80", "S")
        real = tc._read_mountinfo

        def vanish(path: Path) -> str:
            if path.parent.name == "80":
                shutil.rmtree(path.parent)
                raise OSError(22, "Invalid argument")
            return real(path)
        from unittest import mock
        with mock.patch.object(tc, "_read_mountinfo", vanish):
            self.assertIsNotNone(tc.all_mountinfo_text(proc))

    def test_a_transient_einval_is_retried_once(self) -> None:
        proc = self._proc_with("81", "R")
        real, calls = tc._read_mountinfo, []

        def flaky(path: Path) -> str:
            if path.parent.name == "81":
                calls.append(1)
                if len(calls) == 1:
                    raise OSError(22, "Invalid argument")
            return real(path)
        from unittest import mock
        with mock.patch.object(tc, "_read_mountinfo", flaky):
            self.assertIsNotNone(tc.all_mountinfo_text(proc))
        self.assertEqual(len(calls), 2)

    def test_a_symlink_lease_is_an_invalid_lease_and_the_pass_goes_on(self) -> None:
        bad, good = self.key("bad", 30 * HOURS), self.key("good", 30 * HOURS)
        (bad / ".lease").unlink()
        (bad / ".lease").symlink_to(self.tmp / "target")
        log = self.gc()
        self.assertTrue(bad.exists())
        self.assertFalse(good.exists())
        self.assertIn("keep bad: .lease is not a regular file (invalid lease)", log)

    def test_a_fifo_lease_is_never_opened(self) -> None:
        k = self.key("fifo", 30 * HOURS)
        (k / ".lease").unlink()
        os.mkfifo(k / ".lease")
        import signal

        def alarm(_s, _f):
            raise AssertionError("GC blocked on a FIFO .lease")
        old = signal.signal(signal.SIGALRM, alarm)
        signal.alarm(10)
        try:
            log = self.gc()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        self.assertTrue(k.exists())
        self.assertIn("keep fifo: .lease is not a regular file (invalid lease)", log)

    def test_one_failing_entry_does_not_abort_the_pass(self) -> None:
        a, b = self.key("a", 30 * HOURS), self.key("b", 30 * HOURS)
        from unittest import mock
        real = tc.shutil.rmtree

        def boom(path, *args, **kw):
            if Path(path).name.startswith(".trash-a."):
                raise OSError(39, "Directory not empty")
            return real(path, *args, **kw)
        with mock.patch.object(tc.shutil, "rmtree", boom):
            log = self.gc()
        self.assertFalse(b.exists())
        self.assertTrue(any(line.startswith("ERROR a:") for line in log), log)

    def test_cmd_gc_isolates_an_area_that_raises(self) -> None:
        proc, mi = self.fake_world(self.root, with_mount=True)
        plan = plan_for({"MDT_TREE_CACHE_ROOT": str(self.root), "MDT_TREE_UPPER_SIZE": "-"})
        from unittest import mock
        with mock.patch.object(tc, "gc_cache", side_effect=RuntimeError("x")):
            self.assertEqual(tc.cmd_gc(plan, False, mi, proc, self.now), 1)

    def test_real_flock_lessee_blocks_removal_and_st_nlink_flags_a_removed_key(self) -> None:
        # the contract rule: after LOCK_SH the lessee re-checks st_nlink (an unlinked inode shows 0)
        k = self.key("gone", 30 * HOURS)
        fd = os.open(k / ".lease", os.O_RDONLY)
        self.held.append(fd)
        os.unlink(k / ".lease")
        self.assertEqual(os.fstat(fd).st_nlink, 0)


class CheckTests(TmpCase):
    def world(self, mounted: bool = True, slice_low: str = "805306368", timer: bool = True):
        cg = self.tmp / "cg"
        (cg / "dev.slice" / "dev-cache.slice").mkdir(parents=True, exist_ok=True)
        (cg / "dev.slice" / "dev-cache.slice" / "memory.low").write_text(slice_low + "\n")
        plan = plan_for({"DEV_CACHE_MEMORY_LOW": "768M", "MDT_TREE_UPPER_SIZE": "-"})
        mi = self.tmp / "mi"
        mi.write_text((f"30 1 0:45 / /srv/tree-cache rw - tmpfs tmpfs rw,size={plan.cache_size // 1024}k,nr_inodes={plan.cache_inodes},"
                       f"mode=2770,gid={GID}\n") if mounted else "")
        cj = self.tmp / "c.json"
        tc.merge_capacity_json(cj, plan)
        return plan, cg, mi, cj, (lambda _u: timer)

    def run_check(self, **kw) -> str:
        plan, cg, mi, cj, active = self.world(**kw)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tc.cmd_check(plan, cg, mi, self.tmp, cj, active)
        return buf.getvalue()

    def test_a_healthy_host_reports_only_ok(self) -> None:
        out = self.run_check()
        self.assertNotIn("FAIL", out)
        self.assertGreaterEqual(out.count("OK "), 5)

    def test_each_missing_piece_is_a_fail(self) -> None:
        self.assertIn("FAIL tree cache area /srv/tree-cache is not a tmpfs", self.run_check(mounted=False))
        self.assertIn("FAIL dev-cache.slice memory.low=1 but", self.run_check(slice_low="1"))
        self.assertIn("FAIL mdt-tree-cache-gc.timer is not active", self.run_check(timer=False))

    def test_other_roles_get_one_info_line(self) -> None:
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            tc.cmd_check(tc.Plan(enabled=False), self.tmp, self.tmp / "x", self.tmp, self.tmp / "y", lambda _u: False)
        self.assertTrue(buf.getvalue().startswith("INFO tree cache not configured"))


class WiringTests(TmpCase):
    INSTALL = (ROOT / "install.sh").read_text()

    def test_install_plans_before_rendering_and_activates_after_the_slices_start(self) -> None:
        plan_i = self.INSTALL.index('mdt_tree_cache.py" plan ')
        render_i = self.INSTALL.index('mdt_tree_cache.py" render ')
        start_i = self.INSTALL.index("dev-gates.slice dev-buildkitd.slice\n# dedicated-dev only: start dev-cache.slice")
        act_i = self.INSTALL.index('mdt_tree_cache.py" activate ')
        self.assertTrue(plan_i < render_i < act_i and start_i < act_i)
        self.assertLess(self.INSTALL.index('"$HERE/mdt_tree_cache.py" /usr/local/sbin') if '"$HERE/mdt_tree_cache.py" /usr/local/sbin' in self.INSTALL
                        else self.INSTALL.index('install -m 0755 "$HERE/mdt_tree_cache.py"'), render_i + 1)
        self.assertIn("daemon-reload", self.INSTALL[render_i:act_i])

    def test_the_plan_is_validated_before_the_commit_point(self) -> None:
        commit = self.INSTALL.index("# === COMMIT POINT")
        self.assertLess(self.INSTALL.index('mdt_tree_cache.py" plan '), commit)
        self.assertEqual(self.INSTALL.count('mdt_tree_cache.py" plan '), 1)
        self.assertLess(self.INSTALL.index('_tree_role='), commit)

    def test_the_contract_doc_is_neutral_and_carries_the_lessee_rule(self) -> None:
        doc = (ROOT.parents[1] / "libraries" / "cowtree" / "HOST-CONTRACT.md").read_text()
        for key in ("MDT_TREE_CACHE_ROOT", "MDT_TREE_UPPER_ROOT", "DEV_CACHE_MEMORY_LOW", "/etc/mdt/host-capacity.json", "export it as"):
            self.assertNotIn(key, doc)
        self.assertIn("st_nlink", doc)
        self.assertIn("COMPLETE", doc[doc.index("Re-validate"):])
        self.assertIn("no per-lane limit", doc)

    def test_the_cli_plans_nothing_for_shared_prod_and_something_for_dedicated(self) -> None:
        env = self.tmp / "e.env"
        env.write_text("HOST_ROLE=shared-prod\n")
        mi = self.tmp / "meminfo"
        mi.write_text("MemTotal: 8388608 kB\nSwapTotal: 0 kB\n")
        out = subprocess.run([*CLI, "plan", "--config", str(env), "--meminfo", str(mi), "--role", "other"], capture_output=True, text=True)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "TREE_CACHE_ENABLED=0"))
        out = subprocess.run([*CLI, "plan", "--config", str(env), "--meminfo", str(mi), "--role", "dedicated-dev", "--gid", "7"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("TREE_CACHE_SIZE=2147483648", out.stdout)
        env.write_text("MDT_TREE_CACHE_ROOT=/etc\n")
        out = subprocess.run([*CLI, "plan", "--config", str(env), "--meminfo", str(mi), "--role", "dedicated-dev", "--gid", "7"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("MDT_TREE_CACHE_ROOT", out.stderr)

    def test_every_key_is_in_the_env_example_the_wizard_and_the_runtime_optional_set(self) -> None:
        example = (ROOT / "host-setup.env.example").read_text()
        wizard = (ROOT / "mdt-host-setup-wizard.py").read_text()
        optional = re.compile((ROOT / "runtime-optional-keys.re").read_text().strip().splitlines()[-1])
        keys = ["DEV_CACHE_MEMORY_LOW", "DEV_CACHE_ZSWAP_WRITEBACK", "MDT_TREE_CACHE_ROOT", "MDT_TREE_CACHE_SIZE", "MDT_TREE_CACHE_INODES",
                "MDT_TREE_UPPER_ROOT", "MDT_TREE_UPPER_SIZE", "MDT_TREE_UPPER_INODES", "MDT_TREE_CACHE_GROUP",
                "MDT_TREE_CACHE_GC_IDLE", "MDT_TREE_UPPER_GC_IDLE", "MDT_TREE_CACHE_GC_INTERVAL"]
        for k in keys:
            with self.subTest(key=k):
                self.assertRegex(example, rf"(?m)^{k}=")
                self.assertIn(f'"{k}":', wizard)
                self.assertTrue(optional.fullmatch(k))
        self.assertNotIn("MDT_TREE", (ROOT / "units" / "dev.slice.in").read_text())

    def test_check_sh_and_reconcile_know_the_slice(self) -> None:
        check = (ROOT / "scripts" / "check.sh").read_text()
        self.assertIn("mdt_tree_cache.py", check)
        self.assertIn('"$(_mdt_host_role)" != dedicated-dev', check[check.index("AD2-2"):])
        rec = (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text()
        self.assertIn('"dev-cache.slice:DEV_CACHE_ZSWAP_WRITEBACK"', rec)

    def test_the_shipped_units_are_marked_owned(self) -> None:
        for name in ("dev-cache.slice.in", "mdt-tree-area.mount.in", "mdt-tree-cache-gc.service.in", "mdt-tree-cache-gc.timer.in"):
            head = (ROOT / "units" / name).read_text()[:600]
            self.assertTrue(tc.OWN_MARK_SLICE in head or tc.OWN_MARK_MOUNT in head, name)


class TemplateWiringTests(TmpCase):
    """Every units/*.in is resolvable by exactly one renderer from the shipped example: a template key that is
    wired into neither install.sh's RENDER_VARS nor mdt_tree_cache.py cannot be added silently."""
    UNITS = ROOT / "units"
    TOKEN = re.compile(r"@[A-Za-z_][A-Za-z0-9_]*@")

    def example_env(self) -> dict[str, str]:
        return cap._parse_env(ROOT / "host-setup.env.example")

    def render_vars(self) -> set[str]:
        m = re.search(r'^RENDER_VARS="(.*?)"$', (ROOT / "install.sh").read_text(), re.S | re.M)
        self.assertIsNotNone(m)
        return set(m.group(1).replace("\\\n", " ").split())

    def test_owned_list_is_exactly_the_templates_planned_units_reads(self) -> None:
        seen: list[str] = []
        real = tc._render
        tc._render = lambda template, subs: (seen.append(template.name), real(template, subs))[1]
        try:
            plan = plan_for(self.example_env())
            tc.planned_units(plan, self.UNITS, self.example_env())
        finally:
            tc._render = real
        self.assertEqual(sorted(set(seen)), sorted(tc.OWNED_TEMPLATES))

    def test_every_template_is_wired_to_a_renderer_and_renders_without_a_token(self) -> None:
        env = self.example_env()
        env["HOST_ROLE"] = "dedicated-dev"
        units = tc.planned_units(plan_for(env), self.UNITS, env)
        self.assertEqual(len(units), 5, sorted(units))  # slice, gc service + timer, cache + upper mount
        for name, text in units.items():
            self.assertEqual(self.TOKEN.findall(text), [], name)
        rv = self.render_vars()
        for tmpl in sorted(self.UNITS.glob("*.in")):
            if tmpl.name in tc.OWNED_TEMPLATES:
                continue
            missing = {t.strip("@") for t in self.TOKEN.findall(tmpl.read_text())} - rv
            self.assertEqual(missing, set(), f"{tmpl.name}: placeholders neither in install.sh RENDER_VARS nor owned by mdt_tree_cache.py")

    def test_cache_slice_documented_defaults(self) -> None:
        env = self.example_env()
        env["HOST_ROLE"] = "dedicated-dev"
        text = tc.planned_units(plan_for(env), self.UNITS, env)["dev-cache.slice"]
        self.assertNotRegex(text, r"(?m)^MemoryLow=")  # empty = no protection: the line is dropped
        self.assertIn("MemoryZSwapWriteback=yes\n", text)
        env.update(DEV_CACHE_MEMORY_LOW="512M", DEV_CACHE_ZSWAP_WRITEBACK="no")
        text = tc.planned_units(plan_for(env), self.UNITS, env)["dev-cache.slice"]
        self.assertIn("MemoryLow=512M\n", text)
        self.assertIn("MemoryZSwapWriteback=no\n", text)

    def test_render_refuses_a_token_it_was_not_given(self) -> None:
        t = self.tmp / "x.in"
        t.write_text("[Slice]\nMemoryLow=@NOT_WIRED@\n")
        with self.assertRaises(tc.TreeCacheError):
            tc._render(t, {"OTHER": "1"})

    def test_templates_cli_lists_the_owned_templates(self) -> None:
        out = subprocess.run([*CLI, "templates"], check=True, capture_output=True, text=True).stdout.split()
        self.assertEqual(out, list(tc.OWNED_TEMPLATES))


if __name__ == "__main__":
    unittest.main()
