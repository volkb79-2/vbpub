"""Btrfs storage invariants at a fake subprocess boundary."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from collections.abc import Callable

import _support
from dr_state import ids
from dr_state.errors import Refused, StorageError
from dr_state.storage.btrfs import BtrfsStorage


@dataclass
class Completed:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class FakeRunner:
    def __init__(self, replies: dict[tuple[str, ...], Completed] | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: list[str]) -> Completed:
        self.calls.append(tuple(argv))
        key = tuple(argv)
        return self.replies.get(
            key,
            Completed(stderr=f"unexpected fake boundary operation: {argv}"),
        )


class CallbackRunner:
    def __init__(self, callback: Callable[[list[str]], Completed]) -> None:
        self.callback = callback

    def run(self, argv: list[str]) -> Completed:
        return self.callback(argv)


def state(name: str) -> ids.StateId:
    parsed = ids.parse(name)
    if parsed is None:
        raise AssertionError(f"invalid test state {name}")
    return parsed


class BtrfsTest(_support.TemporaryRootTest):
    def setUp(self) -> None:
        super().setUp()
        self.paths.top.mkdir()
        self.paths.fstab.parent.mkdir(parents=True)
        self.paths.proc_cmdline.parent.mkdir(parents=True)

    def test_list_reports_managed_and_unmanaged_objects_once(self) -> None:
        for name in ("@os-000001-20261118-120000Z-install", "@os-0001", "other"):
            (self.paths.top / name).mkdir()
        storage = BtrfsStorage(self.paths, FakeRunner())
        listed = storage.list()
        self.assertEqual(
            [item.name for item in listed],
            ["@os-000001-20261118-120000Z-install", "@os-0001"],
        )
        self.assertTrue(all(item.kind is None for item in listed))

    def test_running_reads_only_the_root_subvolume_option(self) -> None:
        storage = BtrfsStorage(self.paths, FakeRunner())
        self.paths.proc_cmdline.write_text(
            "quiet rootflags=subvol=/@os-main,ro root=/dev/sda2\n",
            encoding="utf-8",
        )
        self.assertEqual(storage.running(), "/@os-main")
        self.paths.proc_cmdline.write_text("quiet root=/dev/sda2\n", encoding="utf-8")
        self.assertIsNone(storage.running())

    def test_activate_is_an_idempotent_local_noop(self) -> None:
        storage = BtrfsStorage(self.paths, FakeRunner())
        state_id = state("@os-000001-20261118-120000Z-install")
        storage.activate(state_id)
        storage.activate(state_id)
        self.assertEqual(storage.runner.calls, [])

    def test_kernels_returns_only_complete_pairs_newest_first(self) -> None:
        name = "@os-000001-20261118-120000Z-install"
        directory = self.paths.top / name
        boot = directory / "boot"
        boot.mkdir(parents=True)
        for version, kernel_size, initrd_size in (
            ("6.12.117+deb13-amd64", 3, 2),
            ("6.12.107+deb13-amd64", 2, 3),
            ("6.20.1+deb13-amd64", 0, 4),
        ):
            (boot / f"vmlinuz-{version}").write_bytes(b"k" * kernel_size)
            (boot / f"initrd.img-{version}").write_bytes(b"i" * initrd_size)
        storage = BtrfsStorage(self.paths, FakeRunner())
        pairs = storage.kernels(name)
        self.assertEqual(
            [pair.version for pair in pairs],
            ["6.12.117+deb13-amd64", "6.12.107+deb13-amd64"],
        )
        self.assertEqual(pairs[0].fs_vmlinuz, f"/{name}/boot/vmlinuz-6.12.117+deb13-amd64")
        self.assertEqual(pairs[0].fs_initrd, f"/{name}/boot/initrd.img-6.12.117+deb13-amd64")

    def test_snapshot_failure_removes_a_partial_target(self) -> None:
        source = state("@os-000001-20261118-120000Z-install")
        target = state("@os-000002-20261118-120100Z-apt")
        self.paths.top.joinpath(source.name).mkdir()
        partial = self.paths.top / target.name

        class PartialRunner:
            def run(self, argv: list[str]) -> Completed:
                if argv[1:3] == ["subvolume", "snapshot"]:
                    partial.mkdir()
                    return Completed(1, stderr="simulated snapshot failure")
                if argv[1:3] == ["subvolume", "delete"]:
                    partial.rmdir()
                    return Completed()
                return Completed(64, stderr="unsupported fake operation")

        storage = BtrfsStorage(self.paths, PartialRunner())
        with self.assertRaises(StorageError):
            storage.snapshot(source, target)
        self.assertFalse(partial.exists())

    def test_delete_refuses_a_dependent_state_and_nested_child(self) -> None:
        target = state("@os-000001-20261118-120000Z-install")
        dependent = state("@os-000002-20261118-120100Z-apt")
        (self.paths.top / target.name).mkdir()
        (self.paths.top / dependent.name).mkdir()

        # Keep the UUID literals local so the fixture cannot accidentally pass by name.
        target_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

        def run_with_dependency(argv: list[str]) -> Completed:
            if argv[1:3] == ["subvolume", "show"]:
                if argv[-1].endswith(target.name):
                    return Completed(stdout=f"UUID: {target_uuid}\n")
                return Completed(stdout=f"UUID: other\nParent UUID: {target_uuid}\n")
            if argv[1:3] == ["subvolume", "delete"]:
                raise AssertionError("delete ran before dependency refusal")
            return Completed()

        storage = BtrfsStorage(self.paths, CallbackRunner(run_with_dependency))
        with self.assertRaisesRegex(Refused, dependent.name):
            storage.delete(target)

        (self.paths.top / dependent.name).rmdir()

        def child_runner(argv: list[str]) -> Completed:
            if argv[1:3] == ["subvolume", "show"]:
                return Completed(stdout=f"UUID: {target_uuid}\n")
            if argv[1:3] == ["subvolume", "list"]:
                return Completed(stdout="ID 256 gen 1 path child\n")
            if argv[1:3] == ["subvolume", "delete"]:
                raise AssertionError("delete ran before child refusal")
            return Completed()

        storage.runner = CallbackRunner(child_runner)
        with self.assertRaisesRegex(Refused, "child"):
            storage.delete(target)

    def test_parent_and_root_arguments_come_from_authoritative_sources(self) -> None:
        parent = state("@os-000001-20261118-120000Z-install")
        child = state("@os-000002-20261118-120100Z-apt")
        (self.paths.top / parent.name).mkdir()
        (self.paths.top / child.name).mkdir()
        parent_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        child_uuid = "11111111-2222-3333-4444-555555555555"
        self.paths.fstab.write_text(
            f"UUID={child_uuid} / btrfs defaults,noatime 0 0\n",
            encoding="utf-8",
        )

        def respond(argv: list[str]) -> Completed:
            if argv[1:3] != ["subvolume", "show"]:
                return Completed()
            if argv[-1].endswith(child.name):
                return Completed(stdout=f"UUID: {child_uuid}\nParent UUID: {parent_uuid}\n")
            return Completed(stdout=f"UUID: {parent_uuid}\n")

        storage = BtrfsStorage(self.paths, CallbackRunner(respond))
        self.assertEqual(storage.parent(child), parent)
        self.assertEqual(storage.locator(child), child_uuid)
        self.assertEqual(
            storage.root_args(child),
            f"root=UUID={child_uuid} rootflags=subvol={child.name}",
        )


if __name__ == "__main__":
    unittest.main()
