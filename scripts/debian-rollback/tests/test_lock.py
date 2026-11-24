"""Durable file replacement and advisory-lock behaviour."""

from __future__ import annotations

import os
import stat
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401
from dr_state.errors import LockTimeout
from dr_state.lock import FileLock, atomic_write


class AtomicWriteTest(_support.TemporaryRootTest):
    def test_atomic_write_is_private_complete_and_replaces_symlinks(self) -> None:
        target = self.root / "metadata" / "one.meta"
        target.parent.mkdir()
        atomic_write(target, b"first\n")
        atomic_write(target, "second\n")
        self.assertEqual(target.read_bytes(), b"second\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(
            sorted(path.name for path in target.parent.iterdir()),
            ["one.meta"],
        )
        elsewhere = self.root / "elsewhere"
        elsewhere.write_text("foreign\n", encoding="utf-8")
        link = self.root / "link"
        link.symlink_to(elsewhere)
        atomic_write(link, "ours\n")
        self.assertFalse(link.is_symlink())
        self.assertEqual(link.read_text(encoding="utf-8"), "ours\n")
        self.assertEqual(elsewhere.read_text(encoding="utf-8"), "foreign\n")
        with self.assertRaises(FileNotFoundError):
            atomic_write(self.root / "missing-parent" / "target", "data")

    def test_scratch_has_the_single_dr_state_shape_and_is_gone_afterwards(self) -> None:
        target = self.root / "last-sequence"
        seen: list[list[str]] = []
        real_replace = os.replace

        def replace(source: object, destination: object) -> None:
            seen.append(sorted(path.name for path in self.root.iterdir()))
            real_replace(source, destination)

        with mock.patch("dr_state.lock.os.replace", replace):
            atomic_write(target, "000001\n")
        (during,) = seen
        scratch = [name for name in during if name != "last-sequence"]
        self.assertEqual(len(scratch), 1)
        self.assertTrue(scratch[0].startswith(".dr-state.tmp."), scratch)
        self.assertEqual([path.name for path in self.root.iterdir()], ["last-sequence"])


class FileLockTest(_support.TemporaryRootTest):
    def setUp(self) -> None:
        super().setUp()
        self.isolate_lock_path()

    def test_exclusive_and_shared_exclusion(self) -> None:
        first = FileLock(self.paths.lock_file, timeout=0)
        second = FileLock(self.paths.lock_file, timeout=0)
        with first:
            with self.assertRaises(LockTimeout):
                second.acquire()
        with second:
            self.assertTrue(second.held)
        shared_one = FileLock(self.paths.lock_file, shared=True, timeout=0)
        shared_two = FileLock(self.paths.lock_file, shared=True, timeout=0)
        writer = FileLock(self.paths.lock_file, timeout=0)
        with shared_one:
            with self.assertRaises(RuntimeError):
                shared_one.require_exclusive()
            shared_two.acquire()
            try:
                with self.assertRaises(LockTimeout):
                    writer.acquire()
            finally:
                shared_two.release()
        with writer:
            self.assertTrue(writer.held)
        shared_one.acquire()
        shared_one.release()
        shared_one.release()
        self.assertFalse(shared_one.held)
        self.assertEqual(stat.S_IMODE(os.stat(self.paths.lock_file).st_mode), 0o600)
        with self.assertRaisesRegex(ValueError, "negative"):
            FileLock(self.paths.lock_file, timeout=-1)

    def test_bounded_wait_acquires_once_the_holder_releases(self) -> None:
        order: list[str] = []
        holding = threading.Event()

        def hold() -> None:
            with FileLock(self.paths.lock_file, timeout=0):
                order.append("held")
                holding.set()
                time.sleep(0.2)
                order.append("released")

        holder = threading.Thread(target=hold)
        holder.start()
        try:
            self.assertTrue(holding.wait(5))
            waiter = FileLock(self.paths.lock_file, timeout=5)
            waiter.acquire()
            order.append("acquired")
            self.assertTrue(waiter.held)
            waiter.release()
        finally:
            holder.join()
        self.assertEqual(order, ["held", "released", "acquired"])

    def test_wait_gives_up_after_its_timeout_with_the_holder_still_in(self) -> None:
        with FileLock(self.paths.lock_file, timeout=0):
            started = time.monotonic()
            with self.assertRaises(LockTimeout):
                FileLock(self.paths.lock_file, timeout=0.2).acquire()
            self.assertGreaterEqual(time.monotonic() - started, 0.2)


if __name__ == "__main__":
    unittest.main()
