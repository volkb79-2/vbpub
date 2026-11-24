"""Sidecar schemas, global formats, and counter-floor repair tests."""

from __future__ import annotations

import base64
import stat
import unittest
from unittest import mock

import _support
from dr_state import FORMAT, ids, metadata
from dr_state.errors import FormatError, Refused
from dr_state.lock import FileLock


def encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def state(sequence: int = 1) -> ids.StateId:
    parsed = ids.parse(f"@os-{sequence:06d}-20261118-120000Z-manual-test")
    assert parsed is not None
    return parsed


def raw_sidecar(schema: int, fields: tuple[tuple[str, str], ...]) -> bytes:
    body = "".join(f"{key}={encoded(value)}\n" for key, value in fields)
    return f"schema={schema}\n{body}".encode("utf-8")


class SidecarTest(_support.TemporaryRootTest):
    def setUp(self) -> None:
        super().setUp()
        self.make_control_dir()

    def test_schema_one_defaults_and_absence_or_invalid_input(self) -> None:
        for source, wanted_pin in (("install", True), ("apt", False)):
            with self.subTest(source=source):
                value = metadata.parse_sidecar(
                    raw_sidecar(
                        1,
                        (
                            ("state", state().name),
                            ("sequence", "000001"),
                            ("source", source),
                        ),
                    )
                )
                self.assertEqual(value.kind, "checkpoint")
                self.assertEqual(value.pinned, wanted_pin)
        self.assertIsNone(metadata.load_sidecar(self.paths.metadata_dir / "none.meta"))
        bad = self.paths.metadata_dir / "bad.meta"
        bad.write_text("state=x\n", encoding="utf-8")
        with self.assertRaisesRegex(FormatError, "schema=N"):
            metadata.load_sidecar(bad)
        real = self.paths.metadata_dir / "real.meta"
        real.write_bytes(raw_sidecar(1, (("sequence", "000001"),)))
        link = self.paths.metadata_dir / "link.meta"
        link.symlink_to(real)
        with self.assertRaisesRegex(FormatError, "symlink"):
            metadata.load_sidecar(link)

    def test_schema_two_round_trip_and_required_added_fields(self) -> None:
        current = state(12)
        wanted = metadata.Metadata(
            state=current.name,
            sequence=12,
            origin_slug="manual-test",
            created_utc="2026-11-18T12:00:00Z",
            parent_state="@os-000011-20261118-110000Z-apt",
            source="manual",
            reason="test\nsecond line",
            note="why",
            os_pretty_name='Debian "Trixie"',
            os_id="debian",
            os_version_id="13",
            kernel="6.12.117+deb13-amd64",
            packages_status="recorded",
            packages="base-files\t13\nlibc6\t2",
            apt_actions="upgrade libc6 2 -> 3",
            kind="live",
            pinned=True,
        )
        target = self.paths.metadata_dir / "one.meta"
        target.write_bytes(metadata.serialize_sidecar(wanted))
        self.assertEqual(metadata.load_sidecar(target), wanted)
        self.assertTrue(target.read_bytes().startswith(b"schema=2\nstate="))
        with self.assertRaisesRegex(FormatError, "lacks kind or pinned"):
            metadata.parse_sidecar(raw_sidecar(2, (("sequence", "000001"),)))

    def test_schema_one_upgrades_and_newer_schema_refuses_writes(self) -> None:
        current = state(1)
        target = metadata.sidecar_path(self.paths, current)
        target.write_bytes(
            raw_sidecar(1, (("state", current.name), ("sequence", "000001")))
        )
        metadata.write_sidecar(
            self.paths,
            current,
            metadata.Metadata(state=current.name, sequence=1, pinned=True),
        )
        upgraded = metadata.load_sidecar(target)
        assert upgraded is not None
        self.assertEqual((upgraded.schema, upgraded.pinned), (2, True))
        target.write_bytes(
            raw_sidecar(
                3,
                (
                    ("state", current.name),
                    ("sequence", "000001"),
                    ("kind", "checkpoint"),
                    ("pinned", "0"),
                ),
            )
        )
        with self.assertRaisesRegex(FormatError, "schema 3"):
            metadata.write_sidecar(
                self.paths, current, metadata.Metadata(state=current.name, sequence=1)
            )
        self.assertIn(b"schema=3", target.read_bytes())

    def test_write_refuses_symlinked_control_and_metadata_directories(self) -> None:
        for directory, is_control in (
            (self.paths.control_dir, True),
            (self.paths.metadata_dir, False),
        ):
            with self.subTest(directory=directory.name, is_control=is_control):
                actual = self.root / f"actual-{directory.name}"
                actual.mkdir()
                if is_control:
                    self.paths.metadata_dir.rmdir()
                    self.paths.control_dir.rmdir()
                else:
                    self.paths.metadata_dir.rmdir()
                directory.symlink_to(actual, target_is_directory=True)
                with self.assertRaisesRegex(FormatError, "symlink"):
                    metadata.write_sidecar(
                        self.paths,
                        state(),
                        metadata.Metadata(state=state().name, sequence=1),
                    )
                self.assertEqual(list(actual.iterdir()), [])
                directory.unlink()
                if is_control:
                    self.paths.control_dir.mkdir()
                self.paths.metadata_dir.mkdir()

    def test_symlinked_sidecar_is_replaced_by_rename(self) -> None:
        current = state(2)
        foreign = self.root / "foreign.meta"
        foreign.write_text("foreign\n", encoding="utf-8")
        target = metadata.sidecar_path(self.paths, current)
        target.symlink_to(foreign)
        metadata.write_sidecar(
            self.paths, current, metadata.Metadata(state=current.name, sequence=2)
        )
        self.assertFalse(target.is_symlink())
        self.assertEqual(foreign.read_text(encoding="utf-8"), "foreign\n")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_scan_skips_corrupt_sidecars_with_a_warning(self) -> None:
        good = self.paths.metadata_dir / "good.meta"
        good.write_bytes(raw_sidecar(1, (("sequence", "000007"),)))
        bad = self.paths.metadata_dir / "bad.meta"
        bad.write_text("schema=wat\n", encoding="utf-8")
        warnings: list[str] = []
        self.assertEqual(metadata.scan_sidecar_sequences(self.paths, warnings.append), (7,))
        self.assertEqual(len(warnings), 1)
        self.assertIn("bad.meta", warnings[0])

    def test_unusable_sidecar_still_contributes_the_sequence_in_its_name(self) -> None:
        named = state(9)
        (self.paths.metadata_dir / f"{named.name}.meta").write_text(
            "schema=wat\n", encoding="utf-8"
        )
        foreign = self.root / "foreign.meta"
        foreign.write_bytes(raw_sidecar(1, (("sequence", "000031"),)))
        linked = state(12)
        (self.paths.metadata_dir / f"{linked.name}.meta").symlink_to(foreign)
        (self.paths.metadata_dir / "not-a-state.meta").write_text("x\n", encoding="utf-8")
        warnings: list[str] = []
        self.assertEqual(
            sorted(metadata.scan_sidecar_sequences(self.paths, warnings.append)), [9, 12]
        )
        self.assertEqual(len(warnings), 3)

    def test_unlistable_metadata_directory_warns_instead_of_failing(self) -> None:
        warnings: list[str] = []
        with mock.patch.object(
            type(self.paths.metadata_dir), "iterdir", side_effect=PermissionError("denied")
        ):
            self.assertEqual(metadata.scan_sidecar_sequences(self.paths, warnings.append), ())
        self.assertEqual(len(warnings), 1)
        self.assertIn("denied", warnings[0])
        self.assertEqual(metadata.scan_sidecar_sequences(self.paths), ())


class FormatTest(_support.TemporaryRootTest):
    def setUp(self) -> None:
        super().setUp()
        self.make_control_dir()

    def test_absent_format_is_one_and_control_and_esp_writes_record_it(self) -> None:
        info = metadata.read_format(self.paths.control_format_file)
        self.assertEqual((info.version, info.exists, info.issue), (1, False, None))
        metadata.write_control_format(self.paths)
        self.assertEqual(self.paths.control_format_file.read_text(encoding="ascii"), "1\n")
        self.paths.esp_dir.mkdir(parents=True)
        metadata.write_esp_format(self.paths)
        self.assertEqual(self.paths.esp_format_file.read_text(encoding="ascii"), "1\n")

    def test_malformed_and_newer_formats_block_writes(self) -> None:
        for text, version, reason in (("not-a-number\n", FORMAT, "malformed"), ("2\n", 2, "newer")):
            with self.subTest(text=text):
                target = self.paths.control_format_file
                target.write_text(text, encoding="ascii")
                self.assertEqual(metadata.read_format(target).version, version)
                with self.assertRaisesRegex(FormatError, reason):
                    metadata.write_control_format(self.paths)
                self.assertEqual(target.read_text(encoding="ascii"), text)

    def test_format_symlinks_and_control_directory_symlinks_are_refused(self) -> None:
        foreign = self.root / "foreign-format"
        foreign.write_text("1\n", encoding="ascii")
        self.paths.control_format_file.symlink_to(foreign)
        with self.assertRaisesRegex(FormatError, "symlink"):
            metadata.write_control_format(self.paths)
        self.assertEqual(foreign.read_text(encoding="ascii"), "1\n")
        self.paths.control_format_file.unlink()
        actual = self.root / "actual-control"
        actual.mkdir()
        self.paths.metadata_dir.rmdir()
        self.paths.control_dir.rmdir()
        self.paths.control_dir.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(FormatError, "symlink"):
            metadata.write_control_format(self.paths)
        self.assertEqual(list(actual.iterdir()), [])


class MissingControlDirectoryTest(_support.TemporaryRootTest):
    """A fresh install has `/.top` but no `.dr-state` yet; the first writer creates it."""

    def setUp(self) -> None:
        super().setUp()
        self.paths.top.mkdir()
        self.isolate_lock_path()

    def test_first_sequence_allocation_creates_the_private_control_directory(self) -> None:
        self.assertFalse(self.paths.control_dir.exists())
        lock = FileLock(self.paths.lock_file, timeout=0)
        with lock:
            result = metadata.allocate_sequence(self.paths, (), lock=lock)
        self.assertEqual((result.sequence, result.floor), (1, 0))
        self.assertEqual(stat.S_IMODE(self.paths.control_dir.stat().st_mode), 0o700)
        self.assertEqual(self.paths.counter_file.read_text(encoding="ascii"), "000001\n")

    def test_first_sidecar_write_creates_both_private_directories(self) -> None:
        current = state(1)
        metadata.write_sidecar(
            self.paths, current, metadata.Metadata(state=current.name, sequence=1)
        )
        for directory in (self.paths.control_dir, self.paths.metadata_dir):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(
            metadata.load_sidecar(metadata.sidecar_path(self.paths, current)).sequence, 1
        )

    def test_a_regular_file_in_place_of_the_directory_is_still_refused(self) -> None:
        self.paths.control_dir.write_text("not a directory\n", encoding="utf-8")
        lock = FileLock(self.paths.lock_file, timeout=0)
        with lock, self.assertRaisesRegex(FormatError, "not a directory"):
            metadata.allocate_sequence(self.paths, (), lock=lock)


class CounterFloorTest(_support.TemporaryRootTest):
    def setUp(self) -> None:
        super().setUp()
        self.make_control_dir()
        self.isolate_lock_path()
        self.lock = FileLock(self.paths.lock_file, timeout=0)

    def reserve(
        self,
        states: tuple[ids.StateId, ...] = (),
        warnings: list[str] | None = None,
    ) -> metadata.Allocation:
        output = [] if warnings is None else warnings
        with self.lock:
            return metadata.allocate_sequence(
                self.paths, states, lock=self.lock, warn=output.append
            )

    def put_sidecar_sequence(self, sequence: int) -> None:
        (self.paths.metadata_dir / "sidecar.meta").write_bytes(
            raw_sidecar(1, (("sequence", f"{sequence:06d}"),))
        )

    def test_floor_comes_from_each_source_and_uses_the_maximum(self) -> None:
        cases = (
            ("counter", "000012\n", (), None, 12),
            ("names", "000002\n", (state(9),), None, 9),
            ("sidecars", "000003\n", (state(4),), 11, 11),
        )
        for label, counter, states, sidecar, wanted in cases:
            with self.subTest(label=label):
                self.paths.counter_file.write_text(counter, encoding="ascii")
                if sidecar is not None:
                    self.put_sidecar_sequence(sidecar)
                self.assertEqual(self.reserve(states).floor, wanted)
                self.paths.counter_file.unlink()
                (self.paths.metadata_dir / "sidecar.meta").unlink(missing_ok=True)
        self.paths.counter_file.write_text("000012\n", encoding="ascii")
        self.put_sidecar_sequence(8)
        self.assertEqual(metadata.counter_floor(12, (state(5),), (8,)), 12)
        result = self.reserve((state(5),))
        self.assertEqual((result.floor, result.sequence, result.repaired), (12, 13, False))
        self.assertIsNone(result.warning)

    def test_missing_malformed_and_low_counters_repair_and_proceed(self) -> None:
        cases = (("missing", None, "missing"), ("malformed", "broken\n", "malformed"), ("low", "000002\n", "below floor 5"))
        for label, contents, reason in cases:
            with self.subTest(label=label):
                self.paths.counter_file.unlink(missing_ok=True)
                if contents is not None:
                    self.paths.counter_file.write_text(contents, encoding="ascii")
                result = self.reserve((state(5),))
                self.assertEqual((result.floor, result.sequence, result.repaired), (5, 6, True))
                self.assertIn(reason, result.warning or "")
                self.assertEqual(self.paths.counter_file.read_text(encoding="ascii"), "000006\n")

    def test_symlinked_counter_is_replaced_without_touching_its_target(self) -> None:
        foreign = self.root / "foreign-counter"
        foreign.write_text("000099\n", encoding="ascii")
        self.paths.counter_file.symlink_to(foreign)
        result = self.reserve((state(7),))
        self.assertEqual(result.sequence, 8)
        self.assertFalse(self.paths.counter_file.is_symlink())
        self.assertEqual(foreign.read_text(encoding="ascii"), "000099\n")
        self.assertIn("symlink", result.warning or "")

    def test_corrupt_sidecar_does_not_block_the_name_floor(self) -> None:
        (self.paths.metadata_dir / "bad.meta").write_text(
            "schema=1\nsequence=not-base64!\n", encoding="utf-8"
        )
        warnings: list[str] = []
        result = self.reserve((state(4),), warnings)
        self.assertEqual(result.sequence, 5)
        self.assertTrue(any("unusable sidecar" in line for line in warnings))

    def test_exhaustion_refuses_at_the_floor(self) -> None:
        self.paths.counter_file.write_text("999998\n", encoding="ascii")
        self.assertEqual(self.reserve().sequence, 999_999)
        self.paths.counter_file.write_text("999998\n", encoding="ascii")
        with self.assertRaisesRegex(Refused, "999999"):
            self.reserve((state(999_999),))
        self.assertEqual(self.paths.counter_file.read_text(encoding="ascii"), "999998\n")

    def test_allocation_requires_the_exact_exclusive_lock(self) -> None:
        with self.assertRaises(RuntimeError):
            metadata.allocate_sequence(self.paths, (), lock=self.lock)
        shared = FileLock(self.paths.lock_file, shared=True, timeout=0)
        with shared:
            with self.assertRaises(RuntimeError):
                metadata.allocate_sequence(self.paths, (), lock=shared)

    def test_symlinked_control_dir_blocks_counter_repair(self) -> None:
        actual = self.root / "actual-control"
        actual.mkdir()
        self.paths.metadata_dir.rmdir()
        self.paths.control_dir.rmdir()
        self.paths.control_dir.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(FormatError, "control directory"):
            self.reserve((state(1),))
        self.assertEqual(list(actual.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
