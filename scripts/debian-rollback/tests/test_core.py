"""Core verb outcomes and safety ordering over fake storage and boot boundaries."""

from __future__ import annotations

import io
import shutil
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import _support
from dr_state import ids, metadata
from dr_state.boot import Inspection, Selector
from dr_state.boot.grub import GrubBoot
from dr_state.core import Completed, Context, Runner, execute, make_context
from dr_state.errors import (
    BootConfigError,
    FormatError,
    Refused,
    StorageError,
)
from dr_state.storage import KernelPair, Space, Stored


class FakeRunner:
    def __init__(self, packages: str = "apt\t3.0\n") -> None:
        self.packages = packages
        self.environment = ""
        self.fail_grubenv = False

    def run(self, argv: list[str], *, input: str | None = None) -> Completed:
        _ = input
        if argv[0] == "dpkg-query":
            return Completed(0, self.packages, "")
        if argv[0] == "grub-editenv":
            operation = argv[2]
            if operation == "create":
                Path(argv[1]).touch()
                self.environment = ""
                return Completed(0, "", "")
            if operation == "list":
                if self.fail_grubenv:
                    return Completed(1, "", "simulated unreadable grubenv")
                return Completed(0, self.environment, "")
            if operation == "set":
                if "=" not in argv[3]:
                    return Completed(64, "", "invalid grubenv assignment")
                key, _, value = argv[3].partition("=")
                current: dict[str, str] = {}
                for line in self.environment.splitlines():
                    name, separator, old_value = line.partition("=")
                    if separator:
                        current[name] = old_value
                current[key] = value
                self.environment = "".join(
                    f"{name}={current[name]}\n" for name in sorted(current)
                )
                return Completed(0, "", "")
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
    boot: FakeBoot | None = None

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
        if self.boot is not None and state.name in self.boot.published:
            raise AssertionError(
                f"{state.name} was still published when storage.delete was called"
            )
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
        self.storage.boot = self.boot

    def context(
        self,
        *,
        boot: FakeBoot | GrubBoot | None = None,
        runner: object | None = None,
        env: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> Context:
        return Context(
            paths=self.paths,
            storage=self.storage,
            runner=runner or FakeRunner(),
            clock=lambda: now or datetime(2026, 11, 18, 14, 0, tzinfo=UTC),
            env=env or {"GRUBEDITENV_STATE": "unused"},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            boot=boot or self.boot,
        )

    def grub_context(self, environment: str) -> Context:
        self.paths.esp_dir.mkdir(parents=True, exist_ok=True)
        self.paths.esp_marker.write_text("grub\n", encoding="utf-8")
        self.paths.esp_grubenv.touch()
        runner = FakeRunner()
        runner.environment = environment
        boot = GrubBoot(self.paths, self.storage, runner)
        return self.context(runner=runner, boot=boot)

    def production_grub_context(self, environment: str) -> tuple[Context, FakeRunner]:
        self.paths.esp_dir.mkdir(parents=True, exist_ok=True)
        self.paths.esp_marker.write_text("grub\n", encoding="utf-8")
        self.paths.esp_grubenv.touch()
        runner = FakeRunner()
        runner.environment = environment
        context = make_context(
            self.paths,
            storage=self.storage,
            runner=runner,
            clock=lambda: datetime(2026, 11, 18, 14, 0, tzinfo=UTC),
            env={"GRUBEDITENV_STATE": "unused"},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        return context, runner

    def assert_snapshot_survives_publication_failure(
        self,
        error: Exception,
    ) -> None:
        self.boot.publish_error = error
        context = self.context()
        self.assertEqual(
            execute(context, ("snapshot", "--reason", "before-upgrade")),
            0,
        )
        target = make_state(3, 14, "manual-before-upgrade")
        self.assertTrue((self.paths.top / target.name).is_dir())
        sidecar = metadata.load_sidecar(metadata.sidecar_path(self.paths, target))
        self.assertIsNotNone(sidecar)
        self.assertIn(
            f"warning: checkpoint {target.name} was written but "
            "the boot menu was not updated",
            context.stderr.getvalue(),
        )
        self.assertIn(
            f"dr-state: wrote {target.name} (copy of {self.install.name})",
            context.stdout.getvalue(),
        )

    def add_state(self, sequence: int, hour: int, slug: str = "apt") -> ids.StateId:
        value = make_state(sequence, hour, slug)
        boot = self.paths.top / value.name / "boot"
        boot.mkdir(parents=True)
        (boot / "vmlinuz-6.12.117+deb13-amd64").write_text("kernel\n")
        (boot / "initrd.img-6.12.117+deb13-amd64").write_text("initrd\n")
        self.storage.states += (value,)
        return value

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

    def test_snapshot_survives_oserror_publication_failure(self) -> None:
        self.assert_snapshot_survives_publication_failure(OSError("full ESP"))

    def test_snapshot_survives_format_publication_failure(self) -> None:
        self.assert_snapshot_survives_publication_failure(
            FormatError("format 2 is newer than supported format 1")
        )

    def test_runner_turns_an_exec_oserror_into_exit_127(self) -> None:
        with mock.patch(
            "dr_state.core.subprocess.run",
            side_effect=FileNotFoundError("missing program"),
        ):
            completed = Runner().run(["missing-program"])
        self.assertEqual(completed.returncode, 127)
        self.assertEqual(completed.stdout, "")
        self.assertIn("missing program", completed.stderr)

    def test_snapshot_records_unavailable_when_dpkg_query_exec_fails(self) -> None:
        self.boot.publish_error = OSError("full ESP")
        with mock.patch(
            "dr_state.core.subprocess.run",
            side_effect=FileNotFoundError("dpkg-query"),
        ):
            context = self.context(runner=Runner())
            result = execute(context, ("snapshot", "--reason", "before-upgrade"))
        self.assertEqual(result, 0)
        target = make_state(3, 14, "manual-before-upgrade")
        sidecar = metadata.load_sidecar(metadata.sidecar_path(self.paths, target))
        self.assertIsNotNone(sidecar)
        self.assertEqual(sidecar.packages_status, "unavailable")
        self.assertEqual(sidecar.packages, "")
        self.assertIn(
            f"warning: checkpoint {target.name} was written but "
            "the boot menu was not updated",
            context.stderr.getvalue(),
        )

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

    def test_dr_state_none_wins_over_a_detected_running_state(self) -> None:
        context = self.context(env={"DR_STATE": "none"})
        self.assertEqual(
            execute(context, ("snapshot", "--reason", "before-upgrade")),
            0,
        )
        self.assertEqual(
            context.stderr.getvalue(),
            "dr-state: DR_STATE=none, snapshot skipped\n",
        )
        self.assertNotIn("snapshot", self.events)
        self.assertFalse(self.paths.counter_file.exists())

        with self.assertRaisesRegex(
            Refused,
            "prune: refusing with DR_STATE=none",
        ):
            execute(self.context(env={"DR_STATE": "none"}), ("prune", "--keep", "0"))
        with self.assertRaisesRegex(
            Refused,
            "delete: refusing with DR_STATE=none",
        ):
            execute(
                self.context(env={"DR_STATE": "none"}),
                ("delete", self.apt.name, "--yes"),
            )
        self.assertEqual(self.events, [])

    def test_dr_state_none_only_escapes_snapshot_prune_and_delete(self) -> None:
        self.storage.running_name = self.apt.name
        context = self.grub_context("")
        context.env = {"DR_STATE": "none"}

        self.assertEqual(execute(context, ("list",)), 0)
        self.assertIn(f"{self.apt.name} (running) kernel=", context.stdout.getvalue())
        self.assertEqual(execute(context, ("gen-menu",)), 0)
        self.assertEqual(
            context.runner.environment,
            f"saved_entry={self.apt.name}\n",
        )

    def test_production_context_preserves_configured_default(self) -> None:
        self.storage.running_name = self.install.name
        context, runner = self.production_grub_context(
            f"saved_entry={self.apt.name}\n"
        )
        self.assertIsNone(context.boot)

        self.assertEqual(
            execute(context, ("snapshot", "--reason", "before-upgrade")),
            0,
        )
        self.assertEqual(
            runner.environment,
            f"saved_entry={self.apt.name}\n",
        )
        self.assertTrue(
            (self.paths.top / make_state(3, 14, "manual-before-upgrade").name).is_dir()
        )
        self.assertEqual(execute(context, ("gen-menu",)), 0)
        self.assertEqual(
            runner.environment,
            f"saved_entry={self.apt.name}\n",
        )
        self.assertEqual(execute(context, ("list",)), 0)
        self.assertIn(
            f"{self.apt.name} (default) kernel=",
            context.stdout.getvalue(),
        )
        self.assertEqual(execute(context, ("show", self.apt.name)), 0)
        self.assertIn("status: default", context.stdout.getvalue())

    def test_snapshot_note_can_be_the_json_literal(self) -> None:
        context = self.context()
        self.assertEqual(execute(context, ("snapshot", "--note", "--json")), 0)
        target = make_state(3, 14, "manual")
        sidecar = metadata.load_sidecar(metadata.sidecar_path(self.paths, target))
        self.assertIsNotNone(sidecar)
        self.assertEqual(sidecar.note, "--json")

    def test_delete_publishes_the_shrunken_menu_before_storage_delete(self) -> None:
        self.storage.running_name = self.apt.name
        self.boot.default_name = self.apt.name
        context = self.context()
        execute(context, ("delete", self.install.name, "--yes"))
        self.assertEqual(self.events, ["publish", "delete"])
        self.assertFalse((self.paths.top / self.install.name).exists())
        self.assertIn(f"deleted {self.install.name}", context.stdout.getvalue())

    def test_delete_proceeds_when_next_entry_is_empty(self) -> None:
        third = self.add_state(3, 14)
        context = self.grub_context(
            f"saved_entry={self.apt.name}\n"
            "next_entry=\n"
        )
        self.assertEqual(
            execute(context, ("delete", third.name, "--yes")),
            0,
        )
        self.assertFalse((self.paths.top / third.name).exists())
        published = context.boot.inspect().published
        self.assertNotIn(third.name, published)
        self.assertIn(self.apt.name, published)
        self.assertIn(f"deleted {third.name}", context.stdout.getvalue())

    def test_delete_refuses_the_configured_default_before_any_change(self) -> None:
        self.storage.running_name = self.apt.name
        self.boot.default_name = self.install.name
        context = self.context()
        with self.assertRaisesRegex(Refused, "configured grub default"):
            execute(context, ("delete", self.install.name, "--yes"))
        self.assertEqual(self.events, [])
        self.assertTrue((self.paths.top / self.install.name).is_dir())

    def test_delete_refuses_the_running_state_before_any_change(self) -> None:
        self.storage.running_name = self.apt.name
        self.boot.default_name = self.install.name
        context = self.context()
        with self.assertRaisesRegex(Refused, "running state"):
            execute(context, ("delete", self.apt.name, "--yes"))
        self.assertEqual(self.events, [])
        self.assertTrue((self.paths.top / self.apt.name).is_dir())

    def test_prune_never_deletes_a_pinned_state(self) -> None:
        third = self.add_state(3, 14)
        fourth = self.add_state(4, 15)
        self.make_control_dir()
        metadata.write_sidecar(
            self.paths,
            self.apt,
            metadata.Metadata(
                state=self.apt.name,
                sequence=self.apt.sequence,
                origin_slug=self.apt.slug,
                source="apt",
                reason="apt",
                kind="checkpoint",
                pinned=True,
            ),
        )
        context = self.context()
        execute(context, ("prune", "--keep", "0"))
        self.assertEqual(
            self.events,
            ["publish", "delete", "delete", "publish"],
        )
        self.assertTrue((self.paths.top / self.apt.name).is_dir())
        self.assertFalse((self.paths.top / third.name).exists())
        self.assertFalse((self.paths.top / fourth.name).exists())

    def test_prune_proceeds_when_next_entry_is_empty(self) -> None:
        third = self.add_state(3, 14)
        fourth = self.add_state(4, 15)
        context = self.grub_context(
            f"saved_entry={self.apt.name}\n"
            "next_entry=\n"
        )
        self.assertEqual(execute(context, ("prune", "--keep", "0")), 0)
        self.assertTrue((self.paths.top / self.install.name).is_dir())
        self.assertTrue((self.paths.top / self.apt.name).is_dir())
        self.assertFalse((self.paths.top / third.name).exists())
        self.assertFalse((self.paths.top / fourth.name).exists())

    def test_prune_protects_running_default_and_oneshot_separately(self) -> None:
        third = self.add_state(3, 14)
        fourth = self.add_state(4, 15)
        self.boot.default_name = self.apt.name
        self.boot.oneshot_name = third.name
        self.assertEqual(
            execute(self.context(), ("prune", "--keep", "0")),
            0,
        )
        self.assertTrue((self.paths.top / self.install.name).is_dir())
        self.assertTrue((self.paths.top / self.apt.name).is_dir())
        self.assertTrue((self.paths.top / third.name).is_dir())
        self.assertFalse((self.paths.top / fourth.name).exists())

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

    def test_install_grub_writes_the_marker_and_format(self) -> None:
        self.paths.esp.mkdir(parents=True)

        class InstallRunner:
            def run(
                self, argv: list[str], *, input: str | None = None
            ) -> Completed:
                _ = argv, input
                return Completed(0, "", "")

        context = self.context(runner=InstallRunner())
        execute(context, ("install-grub",))
        self.assertEqual(
            self.paths.esp_marker.read_text(encoding="utf-8"),
            "grub\n",
        )
        self.assertEqual(
            self.paths.esp_format_file.read_text(encoding="utf-8"),
            "1\n",
        )


if __name__ == "__main__":
    unittest.main()
