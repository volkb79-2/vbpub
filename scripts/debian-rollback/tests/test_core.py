"""Core verb outcomes and safety ordering over fake storage and boot boundaries."""

from __future__ import annotations

import io
import shutil
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import _support
from dr_state import ids, metadata
from dr_state.boot import Inspection, Selector
from dr_state.core import Completed, Context, execute
from dr_state.errors import BootConfigError, Refused, StorageError
from dr_state.storage import KernelPair, Space, Stored


class FakeRunner:
    def __init__(self, packages: str = "apt\t3.0\n") -> None:
        self.packages = packages

    def run(self, argv: list[str], *, input: str | None = None) -> Completed:
        _ = input
        if argv[0] == "dpkg-query":
            return Completed(0, self.packages, "")
        return Completed(64, "", f"unsupported fake program: {argv}")


def make_state(sequence: int, hour: int, slug: str = "apt") -> ids.StateId:
    value = ids.make(sequence, datetime(2026, 11, 18, hour, tzinfo=UTC), slug)
    if value is None:
        raise AssertionError("invalid test state")
    return value


@dataclass
class FakeStorage:
    paths: object
    states: tuple[ids.StateId, ...]
    running_name: str | None
    events: list[str]

    name = "btrfs"

    @property
    def control_dir(self) -> Path:
        return self.paths.control_dir

    def list(self) -> list[Stored]:
        result = [Stored(state.name, None, None) for state in self.states]
        unmanaged = self.paths.top / "@os-0001"
        if unmanaged.exists():
            result.append(Stored(unmanaged.name, None, None))
        return sorted(result, key=lambda item: item.name)

    def running(self) -> str | None:
        return self.running_name

    def kernels(self, state: str) -> list[KernelPair]:
        boot = self.paths.top / state / "boot"
        kernel = boot / "vmlinuz-6.12.117+deb13-amd64"
        initrd = boot / "initrd.img-6.12.117+deb13-amd64"
        if not kernel.is_file() or not initrd.is_file():
            return []
        return [
            KernelPair(
                version="6.12.117+deb13-amd64",
                vmlinuz=kernel,
                initrd=initrd,
                fs_vmlinuz=f"/{state}/boot/vmlinuz-6.12.117+deb13-amd64",
                fs_initrd=f"/{state}/boot/initrd.img-6.12.117+deb13-amd64",
            )
        ]

    def snapshot(self, source: ids.StateId, target: ids.StateId) -> None:
        self.events.append("snapshot")
        (self.paths.top / target.name).mkdir()

    def activate(self, state: ids.StateId) -> None:
        self.events.append("activate")

    def delete(self, state: ids.StateId) -> None:
        self.events.append("delete")
        shutil.rmtree(self.paths.top / state.name)

    def parent(self, state: ids.StateId) -> ids.StateId | None:
        item = metadata.load_sidecar(metadata.sidecar_path(self.paths, state))
        return ids.parse(item.parent_state) if item is not None else None

    def locator(self, state: ids.StateId) -> str:
        _ = state
        return "11111111-2222-3333-4444-555555555555"

    def root_args(self, state: ids.StateId) -> str:
        return f"root=UUID=11111111-2222-3333-4444-555555555555 rootflags=subvol={state.name}"

    def space(self) -> Space:
        return Space(1 << 40, 1 << 39)


class FakeBoot:
    name = "grub"
    supports_oneshot = True

    def __init__(
        self,
        *,
        default: str | None = None,
        oneshot: str | None = None,
        events: list[str],
        default_error: Exception | None = None,
        publish_error: Exception | None = None,
    ) -> None:
        self.default_name = default
        self.oneshot_name = oneshot
        self.events = events
        self.default_error = default_error
        self.publish_error = publish_error
        self.published: tuple[str, ...] = ()

    def default(self, *, strict: bool) -> Selector | None:
        if strict and self.default_error is not None:
            raise self.default_error
        if self.default_name is None:
            return None
        return Selector(self.default_name, self.default_name, self.default_name, True)

    def oneshot(self, *, strict: bool) -> Selector | None:
        _ = strict
        if self.oneshot_name is None:
            return None
        return Selector(self.oneshot_name, self.oneshot_name, self.oneshot_name, True)

    def publish(self, targets: object, default: ids.StateId) -> None:
        self.events.append("publish")
        if self.publish_error is not None:
            raise self.publish_error
        self.published = tuple(target.state.name for target in targets)
        self.default_name = default.name

    def set_oneshot(self, state: ids.StateId) -> None:
        self.events.append("one-shot")
        self.oneshot_name = state.name

    def install(self) -> None:
        self.events.append("install")

    def capacity(self, keep: object) -> None:
        _ = keep
        return None

    def payload(self, target: object) -> list[tuple[str, int]]:
        _ = target
        return []

    def protected(self) -> frozenset[str]:
        return frozenset(
            name
            for name in (self.default_name, self.oneshot_name)
            if name is not None
        )

    def inspect(self) -> Inspection:
        return Inspection(frozenset(), (), ())


class CoreTest(_support.TemporaryRootTest):
    def setUp(self) -> None:
        super().setUp()
        self.paths.top.mkdir()
        self.paths.fstab.parent.mkdir(parents=True)
        self.paths.proc_osrelease.parent.mkdir(parents=True)
        self.paths.proc_osrelease.write_text("6.12.117+deb13-amd64\n", encoding="ascii")
        self.events: list[str] = []
        self.install = make_state(1, 12, "install")
        self.apt = make_state(2, 13)
        for value in (self.install, self.apt):
            boot = self.paths.top / value.name / "boot"
            boot.mkdir(parents=True)
            (boot / "vmlinuz-6.12.117+deb13-amd64").write_text("kernel\n")
            (boot / "initrd.img-6.12.117+deb13-amd64").write_text("initrd\n")
        self.storage = FakeStorage(self.paths, (self.install, self.apt), self.install.name, self.events)
        self.boot = FakeBoot(default=self.install.name, events=self.events)

    def context(
        self,
        *,
        boot: FakeBoot | None = None,
        env: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> Context:
        return Context(
            paths=self.paths,
            storage=self.storage,
            runner=FakeRunner(),
            clock=lambda: now or datetime(2026, 11, 18, 14, 0, tzinfo=UTC),
            env=env or {"GRUBEDITENV_STATE": "unused"},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            boot=boot or self.boot,
        )

    def test_snapshot_records_metadata_and_survives_publication_failure(self) -> None:
        self.boot.publish_error = BootConfigError("marker missing")
        context = self.context()
        result = execute(context, ("snapshot", "--reason", "before-upgrade"))
        self.assertEqual(result, 0)
        target = make_state(3, 14, "manual-before-upgrade")
        self.assertTrue((self.paths.top / target.name).is_dir())
        sidecar = metadata.load_sidecar(metadata.sidecar_path(self.paths, target))
        self.assertIsNotNone(sidecar)
        self.assertEqual(sidecar.parent_state, self.install.name)
        self.assertEqual(sidecar.source, "manual")
        self.assertIn("was written but the boot menu was not updated", context.stderr.getvalue())

    def test_snapshot_metadata_failure_removes_the_object(self) -> None:
        context = self.context()
        original = metadata.write_sidecar
        target = make_state(3, 14, "manual-checkpoint")

        def fail_write(*args: object, **kwargs: object) -> None:
            _ = args, kwargs
            raise StorageError("simulated metadata failure")

        metadata.write_sidecar = fail_write
        try:
            with self.assertRaisesRegex(StorageError, "cannot persist"):
                execute(context, ("snapshot", "--reason", "checkpoint"))
        finally:
            metadata.write_sidecar = original
        self.assertFalse((self.paths.top / target.name).exists())
        self.assertIn("delete", self.events)

    def test_unmanaged_running_state_refuses_snapshot_before_storage_changes(self) -> None:
        unmanaged = self.paths.top / "@os-0001"
        unmanaged.mkdir()
        (unmanaged / "boot").mkdir()
        self.storage.running_name = unmanaged.name
        context = self.context()
        with self.assertRaisesRegex(Refused, "unmanaged"):
            execute(context, ("snapshot", "--reason", "apt"))
        self.assertFalse(self.paths.counter_file.exists())
        self.assertNotIn("snapshot", self.events)

    def test_delete_publishes_the_shrunken_menu_before_storage_delete(self) -> None:
        self.storage.running_name = self.apt.name
        self.boot.default_name = self.apt.name
        context = self.context()
        execute(context, ("delete", self.install.name, "--yes"))
        self.assertEqual(self.events, ["publish", "delete"])
        self.assertFalse((self.paths.top / self.install.name).exists())
        self.assertIn(f"deleted {self.install.name}", context.stdout.getvalue())

    def test_prune_fails_closed_when_the_default_is_unreadable(self) -> None:
        self.boot.default_error = BootConfigError("simulated unreadable grubenv")
        context = self.context()
        with self.assertRaisesRegex(BootConfigError, "unreadable"):
            execute(context, ("prune", "--keep", "0"))
        self.assertEqual(self.events, [])
        self.assertTrue((self.paths.top / self.apt.name).is_dir())

    def test_list_marks_unmanaged_without_publishing_it(self) -> None:
        (self.paths.top / "@os-0001").mkdir()
        self.boot.default_name = self.apt.name
        context = self.context()
        self.assertEqual(execute(context, ("list",)), 0)
        output = context.stdout.getvalue()
        self.assertIn("@os-0001 (unmanaged) kernel=unknown", output)
        self.assertIn(f"{self.apt.name} (default) kernel=", output)


if __name__ == "__main__":
    unittest.main()
