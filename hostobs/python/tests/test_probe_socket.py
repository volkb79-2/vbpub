#!/usr/bin/env python3
"""The real activated `hostobs-probe` process over one `AF_UNIX` listener.

This test sends `SIGTERM`, so run it only through `tools/dev` or the registered `conformance`
lane, whose containers have a private PID namespace. `HOSTOBS_PROBE_BIN` is required and a
missing or non-executable value is a failure, never a skip.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hostobs_client.conn import Connection  # noqa: E402
from hostobs_client.wire import ConnectionLost  # noqa: E402

HOSTOBS_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_CGROUP = HOSTOBS_ROOT / "crates/kernel/tests/fixtures/cgroupfs"
SELECTION = {"entities": [{"all": True}], "metrics": ["cpu.usage_usec"]}
CHILD_WAIT_S = 30


class RealProbeSocket(unittest.TestCase):
    def setUp(self) -> None:
        binary = os.environ.get("HOSTOBS_PROBE_BIN")
        if not binary or not os.path.isfile(binary) or not os.access(binary, os.X_OK):
            self.fail(
                "HOSTOBS_PROBE_BIN must name an executable hostobs-probe; "
                "the conformance lane builds it and exports that variable"
            )
        self.binary = os.path.abspath(binary)
        self.temp = tempfile.TemporaryDirectory(prefix="hostobs-p09-")
        self.addCleanup(self.temp.cleanup)
        self.tmpfs_dir = Path("/dev/shm") / (
            f"hostobs-conformance-{os.getpid()}-{time.monotonic_ns()}"
        )
        self.addCleanup(shutil.rmtree, self.tmpfs_dir, True)
        self.assert_dev_shm_tmpfs()
        self.store_dir = Path(self.temp.name) / "store"
        self.listener, self.address = self.listener_address()
        self.addCleanup(self.listener.close)
        self.config = Path(self.temp.name) / "hostobsd.toml"
        self.write_config(self.tmpfs_dir, self.store_dir)

    def assert_dev_shm_tmpfs(self) -> None:
        try:
            lines = Path("/proc/self/mounts").read_text(encoding="utf-8").splitlines()
        except OSError as error:
            self.fail(
                "/dev/shm must be verified as tmpfs for store.tmpfs_dir in the conformance lane; "
                f"cannot read /proc/self/mounts: {error}"
            )
        wanted = Path("/dev/shm")
        covering: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines):
            fields = line.split()
            if len(fields) < 3:
                continue
            mountpoint = fields[1].replace("\\040", " ").replace("\\134", "\\")
            point = Path(mountpoint)
            if wanted == point or point in wanted.parents:
                covering.append((len(point.parts), index, fields[2]))
        if not covering or max(covering)[2] != "tmpfs":
            self.fail(
                "/dev/shm must be tmpfs for store.tmpfs_dir in the conformance lane; "
                "the real-probe test cannot use /tmp"
            )

    def write_config(self, tmpfs_dir: Path, store_dir: Path) -> None:
        self.config.write_text(
            "[probe]\n"
            'levels = ["observe"]\n'
            "tick_ms = 100\n"
            f'cgroup_root = "{FIXTURE_CGROUP}"\n'
            "\n"
            "[limits]\n"
            "max_rows = 1024\n"
            "\n"
            "[store]\n"
            f'dir = "{store_dir}"\n'
            f'tmpfs_dir = "{tmpfs_dir}"\n'
            "events_cap_bytes = 67108864\n"
            "events_ring_bytes = 4194304\n",
            encoding="utf-8",
        )

    def listener_address(self) -> tuple[socket.socket, str]:
        """A working abstract address, or a temporary filesystem socket path."""
        name = f"\0hostobs-p09-{os.getpid()}-{time.monotonic_ns()}"
        candidate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            candidate.bind(name)
            candidate.listen(8)
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(1)
                probe.connect(name)
            finally:
                probe.close()
            return candidate, name
        except OSError:
            candidate.close()

        path = os.path.join(self.temp.name, "observe.sock")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen(8)
        return listener, path

    def spawn_probe(self, *, fdnames: str = "observe", with_config: bool = True):
        """Spawn the shell shape from PROBE-PROCESS §(h), with the listener at fd 3."""
        command = (
            'exec 3<&"$2" && LISTEN_PID=$$ exec "$0" --config "$1"'
            if with_config
            else 'exec 3<&"$2" && LISTEN_PID=$$ exec "$0"'
        )
        env = os.environ.copy()
        env.pop("LISTEN_PID", None)
        env["LISTEN_FDS"] = "1"
        env["LISTEN_FDNAMES"] = fdnames
        return subprocess.Popen(
            ["sh", "-c", command, self.binary, str(self.config), str(self.listener.fileno())],
            pass_fds=(self.listener.fileno(),),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def reap(self, child: subprocess.Popen[str]) -> tuple[str, str]:
        """Reap every child, killing only a test failure's still-running process."""
        try:
            return child.communicate(timeout=CHILD_WAIT_S)
        except subprocess.TimeoutExpired:
            child.kill()
            return child.communicate(timeout=CHILD_WAIT_S)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()

    def assert_one_stderr_line(self, stderr: str) -> None:
        lines = stderr.rstrip("\n").splitlines()
        self.assertEqual(len(lines), 1, stderr)
        self.assertTrue(lines[0])

    def wait_for_tick(self, connection: Connection) -> None:
        deadline = time.monotonic() + CHILD_WAIT_S
        while True:
            status = connection.request("status", {})
            if status["tick"]["seq"] > 0:
                return
            if time.monotonic() >= deadline:
                self.fail("the activated probe published no sampler tick within 30 s")
            time.sleep(0.01)

    def subscription_interval_ms(self, connection: Connection) -> int:
        tick_ms = connection.limits["tick_ms"]
        minimum = max(connection.limits["min_interval_ms"], tick_ms)
        if tick_ms <= 0:
            self.fail(f"block-level probe announced tick_ms={tick_ms}")
        interval_ms = ((minimum + tick_ms - 1) // tick_ms) * tick_ms
        self.assertLessEqual(interval_ms, 60_000)
        return interval_ms

    def test_hello_current_subscribe_then_default_sigterm(self) -> None:
        child = self.spawn_probe()
        connection = None
        subscription = None
        try:
            connection = Connection.open(
                self.address, "observe", client="p09-real-socket", timeout_s=CHILD_WAIT_S
            )
            self.assertEqual(connection.level, "observe")
            self.assertEqual(
                connection.verbs,
                ["hello", "status", "current", "subscribe", "unsubscribe"],
            )
            self.assertEqual(connection.limits["tick_ms"], 100)
            self.assertEqual(connection.limits["min_interval_ms"], 1_000)
            self.assertEqual(connection.limits["max_rows"], 1024)
            self.wait_for_tick(connection)

            stream = connection.blocks("current", {"selection": SELECTION})
            blocks = list(stream)
            self.assertEqual(len(blocks), 1)
            self.assertGreater(blocks[0].header.seq, 0)
            self.assertGreater(blocks[0].header.n_rows, 0)
            self.assertEqual(stream.end["reason"], "complete")

            interval_ms = self.subscription_interval_ms(connection)
            subscription = connection.subscribe(SELECTION, interval_ms)
            first = next(subscription)
            second = next(subscription)
            self.assertGreater(first.header.seq, 0)
            self.assertGreater(second.header.seq, first.header.seq)

            child.send_signal(signal.SIGTERM)
            _, stderr = self.reap(child)
            self.assertEqual(child.returncode, -signal.SIGTERM)
            self.assertEqual(stderr, "")
            deadline = time.monotonic() + CHILD_WAIT_S
            while True:
                try:
                    next(subscription)
                except ConnectionLost:
                    break
                if time.monotonic() >= deadline:
                    self.fail("the client did not observe connection loss after SIGTERM")
        finally:
            if subscription is not None:
                subscription.close()
            if connection is not None:
                connection.close()
            self.reap(child)

    def test_control_name_is_refused_before_any_client_byte(self) -> None:
        child = self.spawn_probe(fdnames="control")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(CHILD_WAIT_S)
            client.connect(self.address)
            # The parent's listening descriptor keeps the queued connection from seeing EOF
            # after the probe exits. Drop it before waiting; the child has its own inherited fd.
            self.listener.close()
            _, stderr = self.reap(child)
            self.assertEqual(child.returncode, 2)
            self.assert_one_stderr_line(stderr)
            self.assertIn("control", stderr)
            try:
                got = client.recv(1)
            except (BrokenPipeError, ConnectionResetError):
                got = b""
            self.assertEqual(got, b"")
        finally:
            client.close()
            self.reap(child)

    def test_disk_backed_store_tmpfs_dir_refuses_before_creating_tiers(self) -> None:
        disk_tmpfs_dir = Path(self.temp.name) / "disk-tiers"
        durable_dir = Path(self.temp.name) / "durable-store"
        self.write_config(disk_tmpfs_dir, durable_dir)
        child = self.spawn_probe()
        _, stderr = self.reap(child)
        self.assertEqual(child.returncode, 2)
        self.assertEqual(
            stderr,
            "hostobs-probe: store.tmpfs_dir must be on tmpfs; "
            "store.dir is durable storage\n",
        )
        self.assertFalse(disk_tmpfs_dir.exists())
        self.assertFalse(durable_dir.exists())

    def test_missing_config_is_refused(self) -> None:
        child = self.spawn_probe(with_config=False)
        _, stderr = self.reap(child)
        self.assertEqual(child.returncode, 2)
        self.assert_one_stderr_line(stderr)
        self.assertIn("--config", stderr)


if __name__ == "__main__":
    unittest.main()
