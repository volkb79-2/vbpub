"""Crash-safety of _grow_root_to_plan (gap branch: sfdisk -N + partx -u + resize2fs), hermetic.

A fake command runner models three independent sizes (partition table, kernel view, ext4 filesystem) and can
die before or after any command. After ANY interruption a plain rerun must converge to table == kernel ==
filesystem == plan, and success must never be recorded while the filesystem is smaller than the partition.
"""

from __future__ import annotations

import pytest

from debian_install_v2.disk_layout import GIB
from debian_install_v2.installer import Installer, InstallerError
from debian_install_v2 import installer as installer_module

from .test_disk_policy import make_planner

DISK = 512 * GIB // 512
START = 2500608
OLD = 3 * GIB // 512
TARGET = 160 * GIB // 512
SWAP_START = 496 * GIB // 512


class Crash(Exception):
    pass


class FakeHost:
    def __init__(self) -> None:
        self.table = OLD
        self.kernel = OLD
        self.fs = OLD * 512
        self.steps: dict[str, dict] = {}
        self.calls: list[list[str]] = []
        self.counter = 0
        self.crash_at: int | None = None
        self.crash_when = "before"
        self.partx_works = True
        self.success_with_small_fs = False

    def run(self, argv, description="", dangerous=False, input=None, timeout=None):
        self.counter += 1
        index = self.counter
        if self.crash_at == index and self.crash_when == "before":
            raise Crash(index)
        self.calls.append(list(argv))
        out = self._effect(argv, input)
        if self.crash_at == index and self.crash_when == "after":
            raise Crash(index)
        return out

    def _effect(self, argv, input):
        tool = argv[0].rsplit("/", 1)[-1]
        if tool == "blockdev":
            return f"{self.kernel}\n"
        if tool == "sfdisk":
            assert "-N" in argv and input is not None
            self.table = int(input.strip().lstrip(",").split()[0])
            return ""
        if tool == "partx":
            if self.partx_works:
                self.kernel = self.table
            return ""
        if tool == "dumpe2fs":
            return f"Block size:               4096\nBlock count:              {self.fs // 4096}\n"
        if tool == "resize2fs":
            self.fs = (self.kernel * 512 // 4096) * 4096
            return ""
        if tool == "growpart":
            raise AssertionError("gap branch must not call growpart")
        raise AssertionError(f"unexpected command {argv}")


def build(tmp_path, monkeypatch, host: FakeHost) -> Installer:
    installer = make_planner(tmp_path, disk_gib=512, root_gib=3, root_start=START, swap_policy="on",
                             swap_disk_total_gb=16)
    installer.actions.dry_run = False
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "state.json").write_text("{}")
    installer._disk_facts = lambda: (DISK, START, host.table)
    installer._live_partition_entries = lambda *_a: {
        3: {"start": str(START), "size": str(host.table), "type": "linux"},
        4: {"start": str(SWAP_START), "size": str(4 * GIB // 512), "type": "swap"},
    }
    installer._resolve_swap_plan = lambda **_k: ([], TARGET, False)
    installer._root_filesystem_facts = lambda: (4096, 100_000)
    installer._packages = lambda *_a: None
    monkeypatch.setattr(installer_module.shutil, "which", lambda _name: "/usr/bin/growpart")

    class State:
        def load(self):
            return {"steps": host.steps}

        def mark_step(self, name, status, detail=""):
            if name == "root_resize" and status == "success" and host.fs < host.kernel * 512 - 2 * 1048576:
                host.success_with_small_fs = True
            host.steps[name] = {"status": status, "detail": detail}

    installer.state = State()
    installer.actions.run = host.run
    return installer


def test_clean_run_orders_sfdisk_partx_resize_and_converges(tmp_path, monkeypatch):
    host = FakeHost()
    installer = build(tmp_path, monkeypatch, host)
    installer._grow_root_to_plan()
    tools = [c[0].rsplit("/", 1)[-1] for c in host.calls]
    assert tools.index("sfdisk") < tools.index("partx") < tools.index("resize2fs")
    assert host.table == host.kernel == TARGET
    assert abs(host.fs - TARGET * 512) <= 4096
    assert host.steps["root_resize"]["status"] == "success"
    sfdisk = next(c for c in host.calls if c[0].endswith("sfdisk"))
    assert "--no-reread" in sfdisk and "--no-tell-kernel" in sfdisk
    assert sfdisk[sfdisk.index("-N") + 1] == "3"


def _clean_call_count(tmp_path, monkeypatch) -> int:
    host = FakeHost()
    installer = build(tmp_path, monkeypatch, host)
    installer._grow_root_to_plan()
    return host.counter


@pytest.mark.parametrize("when", ["before", "after"])
def test_rerun_converges_after_interruption_at_every_command(tmp_path, monkeypatch, when):
    total = _clean_call_count(tmp_path / "clean", monkeypatch)
    assert total == 5  # sfdisk, partx, blockdev --getsz, resize2fs, dumpe2fs
    for crash_at in range(1, total + 1):
        root = tmp_path / f"{when}-{crash_at}"
        root.mkdir()
        host = FakeHost()
        host.crash_at, host.crash_when = crash_at, when
        installer = build(root, monkeypatch, host)
        with pytest.raises(Crash):
            installer._grow_root_to_plan()
        host.crash_at = None
        # a fresh Installer object models the rerun after a process death
        again = build(root, monkeypatch, host)
        again._grow_root_to_plan()
        assert host.table == host.kernel == TARGET, (when, crash_at)
        assert abs(host.fs - TARGET * 512) <= 4096, (when, crash_at)
        assert host.steps["root_resize"]["status"] == "success", (when, crash_at)
        assert not host.success_with_small_fs, (when, crash_at)


def test_table_ahead_of_kernel_reruns_partx_before_resize(tmp_path, monkeypatch):
    """The reviewed hazard: sfdisk wrote the table, partx never ran, state is 'started'."""
    host = FakeHost()
    host.table = TARGET
    host.steps["root_resize"] = {"status": "started", "detail": ""}
    installer = build(tmp_path, monkeypatch, host)
    installer._grow_root_to_plan()
    tools = [c[0].rsplit("/", 1)[-1] for c in host.calls]
    assert "sfdisk" not in tools
    assert tools.index("partx") < tools.index("resize2fs")
    assert abs(host.fs - TARGET * 512) <= 4096


def test_success_state_with_stale_kernel_is_not_trusted(tmp_path, monkeypatch):
    host = FakeHost()
    host.table = TARGET
    host.steps["root_resize"] = {"status": "success", "detail": ""}
    installer = build(tmp_path, monkeypatch, host)
    installer._grow_root_to_plan()
    assert host.kernel == TARGET
    assert abs(host.fs - TARGET * 512) <= 4096


def test_fully_done_host_is_a_noop(tmp_path, monkeypatch):
    host = FakeHost()
    host.table = host.kernel = TARGET
    host.fs = TARGET * 512
    host.steps["root_resize"] = {"status": "success", "detail": ""}
    installer = build(tmp_path, monkeypatch, host)
    installer._grow_root_to_plan()
    tools = [c[0].rsplit("/", 1)[-1] for c in host.calls]
    assert tools == ["blockdev"]


def test_kernel_refusing_the_new_size_blocks_resize_and_success(tmp_path, monkeypatch):
    host = FakeHost()
    host.partx_works = False
    installer = build(tmp_path, monkeypatch, host)
    with pytest.raises(InstallerError, match="kernel reports root partition"):
        installer._grow_root_to_plan()
    tools = [c[0].rsplit("/", 1)[-1] for c in host.calls]
    assert "resize2fs" not in tools
    assert host.steps["root_resize"]["status"] == "started"


def test_resize2fs_that_does_nothing_is_not_recorded_as_success(tmp_path, monkeypatch):
    host = FakeHost()
    original = host._effect

    def lazy(argv, input):
        if argv[0].endswith("resize2fs"):
            return ""
        return original(argv, input)

    host._effect = lazy
    installer = build(tmp_path, monkeypatch, host)
    with pytest.raises(InstallerError, match="refusing to record root_resize success"):
        installer._grow_root_to_plan()
    assert host.steps["root_resize"]["status"] == "started"
