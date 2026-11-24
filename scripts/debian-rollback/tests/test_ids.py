"""State grammar, generator, ownership, mapping, and version ordering tests."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone

import _support  # noqa: F401
from dr_state import ids


class GrammarTest(unittest.TestCase):
    def test_canonical_state_is_accepted(self) -> None:
        state = ids.parse("@os-000123-20261118-120000Z-manual-rollback")
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state.name, "@os-000123-20261118-120000Z-manual-rollback")
        self.assertEqual(state.bare, "os-000123-20261118-120000Z-manual-rollback")
        self.assertEqual(state.sequence, 123)
        self.assertEqual(state.slug, "manual-rollback")
        self.assertEqual(state.created, datetime(2026, 11, 18, 12, 0, tzinfo=UTC))

    def test_slug_bounds_are_exactly_one_through_sixty_four(self) -> None:
        self.assertIsNotNone(ids.parse("@os-000001-20261118-120000Z-a"))
        self.assertIsNotNone(ids.parse(f"@os-000001-20261118-120000Z-{'a' * 64}"))
        self.assertIsNone(ids.parse(f"@os-000001-20261118-120000Z-{'a' * 65}"))

    def test_rejected_shapes(self) -> None:
        rejected = (
            "@os-000000-20261118-120000Z-apt",
            "@os-00001-20261118-120000Z-apt",
            "@os-000001-20261118-120000Z-",
            "@os-000001-20261118-120000Z-Apt",
            "@os-000001-20261118-120000Z-apt.conf",
            "@os-000001-20261340-250000Z-apt",
            "@os-000001-20260229-120000Z-apt",
            "@os-000001-20261118-240000Z-apt",
            "@os-000001-20261118-120060Z-apt",
            "@os-pre-20261118-120000Z-apt",
            "os-000001-20261118-120000Z-apt",
            "@os-000001-20261118-120000Z-apt\n",
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertIsNone(ids.parse(value))
        self.assertIsNotNone(ids.parse("@os-000001-20261118-120000Z-apt-"))
        self.assertIsNotNone(ids.parse("@os-000001-20261118-120000Z-apt_"))
        self.assertIsNone(ids.parse(None))
        self.assertIsNone(ids.parse(42))

    def test_make_round_trips_and_refuses_non_utc(self) -> None:
        created = datetime(2026, 11, 18, 12, 0, 1, tzinfo=UTC)
        state = ids.make(123, created, "manual-rollback")
        self.assertIsNotNone(state)
        self.assertEqual(
            state.name,
            "@os-000123-20261118-120001Z-manual-rollback",
        )
        self.assertIsNone(ids.make(0, created, "apt"))
        self.assertIsNone(
            ids.make(1, created.astimezone(UTC).replace(tzinfo=None), "apt")
        )
        self.assertIsNone(ids.make(1, created.replace(tzinfo=timezone(timedelta(hours=1))), "apt"))


class SlugAndClassificationTest(unittest.TestCase):
    def test_slug_sources_and_manual_sanitization(self) -> None:
        self.assertEqual(ids.slug("apt", "anything"), "apt")
        self.assertEqual(ids.slug("install", "anything"), "install")
        self.assertIsNone(ids.slug("unknown", "anything"))
        for reason, wanted in (
            ("Rollback . Conf", "manual-rollback-conf"),
            ("bad\tname", "manual-bad-name"),
            ("v1.2", "manual-v1-2"),
            (" MANUAL_SAFE ", "manual-manual_safe"),
            ("manual", "manual"),
            (" Manual! ", "manual"),
            ("manual-rollback", "manual-rollback"),
            ("Manual Rollback", "manual-rollback"),
        ):
            with self.subTest(reason=reason):
                self.assertEqual(ids.slug("manual", reason), wanted)

    def test_manual_reason_with_no_usable_characters_is_refused(self) -> None:
        for reason in ("", "...", "  ", "-_-"):
            with self.subTest(reason=reason):
                self.assertIsNone(ids.slug("manual", reason))

    def test_long_manual_slug_is_cut_to_the_bound_without_a_trailing_separator(self) -> None:
        result = ids.slug("manual", "x" * 400)
        assert result is not None
        self.assertEqual(len(result), 64)
        self.assertTrue(result.startswith("manual-"))
        self.assertFalse(result.endswith(("-", "_")))
        # The cut at 64 lands on the separator that follows 56 letters.
        cut = ids.slug("manual", "a" * 56 + " b")
        assert cut is not None
        self.assertLessEqual(len(cut), 63)
        self.assertFalse(cut.endswith(("-", "_")))
        self.assertIsNotNone(ids.make(1, datetime(2026, 11, 18, tzinfo=UTC), cut))

    def test_foreign_reserved_names_are_unmanaged(self) -> None:
        for value in ("@os-0001", "@os-pre-20261118", "@os-", "@os-000000-x"):
            with self.subTest(value=value):
                self.assertTrue(ids.unmanaged(value))
        self.assertFalse(ids.unmanaged("@os-000001-20261118-120000Z-apt"))
        self.assertFalse(ids.unmanaged("other"))
        self.assertFalse(ids.unmanaged(None))


class EntryMappingTest(unittest.TestCase):
    STATE = "@os-000123-20261118-120000Z-manual-rollback"

    def test_state_maps_to_entry_file_id(self) -> None:
        state = ids.parse(self.STATE)
        assert state is not None
        self.assertEqual(ids.entry_id(state), "os-000123-20261118-120000Z-manual-rollback.conf")

    def test_selector_mapping_strips_conf_and_boot_counting(self) -> None:
        accepted = (
            "os-000123-20261118-120000Z-manual-rollback",
            "os-000123-20261118-120000Z-manual-rollback.conf",
            "os-000123-20261118-120000Z-manual-rollback.CONF",
            "os-000123-20261118-120000Z-manual-rollback+3",
            "os-000123-20261118-120000Z-manual-rollback+3.conf",
            "os-000123-20261118-120000Z-manual-rollback+3-1",
            "os-000123-20261118-120000Z-manual-rollback+3-1.CoNf",
        )
        for value in accepted:
            with self.subTest(value=value):
                state = ids.parse_entry_id(value)
                self.assertIsNotNone(state)
                self.assertEqual(state.name, self.STATE)

    def test_selector_mapping_rejects_foreign_or_ambiguous_values(self) -> None:
        rejected = (
            "@os-000123-20261118-120000Z-manual-rollback",
            "os-000123-20261118-120000Z-manual-rollback.conf.conf",
            "os-000123-20261118-120000Z-manual-rollback+",
            "os-000123-20261118-120000Z-manual-rollback+3-",
            "os-000123-20261118-120000Z-manual-rollback+0-1-2",
            "os-pre-20261118",
            "os-000123-20261118-120000Z-manual-rollback/evil",
            None,
        )
        for value in rejected:
            with self.subTest(value=value):
                self.assertIsNone(ids.parse_entry_id(value))


class VersionOrderTest(unittest.TestCase):
    def test_kernel_versions_sort_newest_first(self) -> None:
        self.assertEqual(
            ids.newest_first(
                (
                    "6.12.9",
                    "6.12.117-rt-amd64",
                    "7.3.4",
                    "6.12.117",
                    "6.12.117~rc1-amd64",
                    "6.12.117+deb13-amd64",
                    "6.12.118",
                )
            ),
            (
                "7.3.4",
                "6.12.118",
                "6.12.117-rt-amd64",
                "6.12.117+deb13-amd64",
                "6.12.117",
                "6.12.117~rc1-amd64",
                "6.12.9",
            ),
        )

    # Captured from GNU coreutils 9.7 `sort -V`, ascending, on Debian-style kernel names.
    SORT_V_ASCENDING = (
        "6.1.0-9-amd64",
        "6.12.1a-amd64",
        "6.12.1+b-amd64",
        "6.12.1-amd64",
        "6.12.9-cloud-amd64",
        "6.12.73~bpo13+1-amd64",
        "6.12.73~rc1-amd64",
        "6.12.73+deb13-amd64",
        "6.12.73+deb13-cloud-amd64",
        "6.12.73-amd64",
        "6.12.73-cloud-amd64",
        "6.12.100-amd64",
        "6.13.2-amd64",
    )

    def test_ordering_matches_real_sort_v_output(self) -> None:
        shuffled = (
            self.SORT_V_ASCENDING[7::2] + self.SORT_V_ASCENDING[:7] + self.SORT_V_ASCENDING[8::2]
        )
        self.assertCountEqual(shuffled, self.SORT_V_ASCENDING)
        self.assertEqual(ids.newest_first(shuffled), tuple(reversed(self.SORT_V_ASCENDING)))

    def test_letter_then_plus_then_dash_and_tilde_before_end(self) -> None:
        self.assertEqual(
            ids.newest_first(("6.12.1-amd64", "6.12.1a-amd64", "6.12.1+b-amd64")),
            ("6.12.1-amd64", "6.12.1+b-amd64", "6.12.1a-amd64"),
        )
        self.assertLess(ids.compare_versions("6.12~rc1", "6.12"), 0)
        self.assertEqual(ids.compare_versions("6.012", "6.12"), 0)


if __name__ == "__main__":
    unittest.main()
