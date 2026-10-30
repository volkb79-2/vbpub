"""Live filesystem checks; skipped unless COWTREE_LIVE_ROOT is provisioned."""
from __future__ import annotations

import multiprocessing
import os
import unittest
from pathlib import Path

from cowtree import SpaceEstimate, open_run

_LIVE_ROOT = os.environ.get("COWTREE_LIVE_ROOT")
_CHUNK = 64 * 1024 * 1024


def _try_lock(lock_path: str, queue) -> None:
    import fcntl

    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            queue.put("held")
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            queue.put("free")
    finally:
        os.close(descriptor)


@unittest.skipUnless(_LIVE_ROOT, "set COWTREE_LIVE_ROOT to a provisioned CoW area")
class CowtreeLiveTests(unittest.TestCase):
    def setUp(self):
        self.run = open_run(
            "cowtree-live",
            estimate=SpaceEstimate(_CHUNK, _CHUNK, 1),
            environ={"MDT_COW_ROOT": _LIVE_ROOT or ""},
        )
        if not self.run.caps.cheap_clone:
            self.run.close()
            self.skipTest("COWTREE_LIVE_ROOT did not detect a CoW backend")

    def tearDown(self):
        if getattr(self, "run", None) is not None:
            self.run.close()

    def test_snapshot_or_reflink_write_isolation_and_extent_sharing(self):
        tree = self.run.make_tree("source")
        payload = tree / "payload"
        with payload.open("wb") as handle:
            handle.write(os.urandom(_CHUNK))
            handle.flush()
            os.fsync(handle.fileno())
        before = os.statvfs(_LIVE_ROOT).f_bavail * os.statvfs(_LIVE_ROOT).f_frsize
        clone = self.run.clone_tree(tree, "clone")
        after = os.statvfs(_LIVE_ROOT).f_bavail * os.statvfs(_LIVE_ROOT).f_frsize
        self.assertLess(before - after, 1024 * 1024)
        with (clone / "payload").open("r+b") as handle:
            handle.write(b"changed")
        self.assertNotEqual(payload.read_bytes()[:7], b"changed")

    def test_delete_reclaims_space_and_cross_process_flock_is_held(self):
        tree = self.run.make_tree("source")
        payload = tree / "payload"
        payload.write_bytes(os.urandom(_CHUNK))
        clone = self.run.clone_tree(tree, "clone")
        clone_payload = clone / "payload"
        with clone_payload.open("r+b") as handle:
            handle.write(os.urandom(_CHUNK))
            handle.flush()
            os.fsync(handle.fileno())
        before = os.statvfs(_LIVE_ROOT).f_bavail * os.statvfs(_LIVE_ROOT).f_frsize
        self.run.delete_tree(clone)
        os.sync()
        after = os.statvfs(_LIVE_ROOT).f_bavail * os.statvfs(_LIVE_ROOT).f_frsize
        self.assertGreaterEqual(after - before, _CHUNK // 2)

        context = multiprocessing.get_context("spawn")
        queue = context.Queue()
        process = context.Process(target=_try_lock, args=(str(Path(self.run.path) / "owner.lock"), queue))
        process.start()
        self.assertEqual(queue.get(timeout=60), "held")
        process.join(60)
        self.assertEqual(process.exitcode, 0)


if __name__ == "__main__":
    unittest.main()
