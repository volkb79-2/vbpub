"""APT protocol parsing and snapshot-window policy tests."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime

import _support  # noqa: F401
from dr_state import apt, ids


PLAN_V2 = """\
VERSION 2
APT::Get::CommandLine::AsString=apt%20install%20hello

hello - < 2.10-3 /var/cache/apt/archives/hello.deb
libc6:amd64 2.36-9 < 2.37-1 /var/cache/apt/archives/libc6.deb
oldpkg 1.0 > - **REMOVE**
manualpkg 1.0 = 1.0 **CONFIGURE**
"""


class PlanParserTest(unittest.TestCase):
    def test_recognized_plan_shapes(self) -> None:
        v3 = (
            "VERSION 3\r\n\r\n"
            "foo - - none < 1.0 amd64 same /var/cache/apt/archives/foo.deb\r\n"
        )
        removal = "VERSION 2\nAPT::Get::CommandLine::AsString=apt%20remove\n\noldpkg 1.0 > - **REMOVE**\n"
        other = """\
VERSION 2

new - < 2.0 /tmp/new.deb
old 3.0 > 2.0 /tmp/old.deb
same 1.0 = 1.0 /tmp/same.deb
"""
        cases = (
            (
                PLAN_V2,
                "install hello - -> 2.10-3\n"
                "upgrade libc6:amd64 2.36-9 -> 2.37-1\n"
                "remove oldpkg 1.0 -> -\n"
                "configure manualpkg 1.0 -> 1.0",
            ),
            (v3, "install foo - -> 1.0"),
            (removal, "remove oldpkg 1.0 -> -"),
            (
                other,
                "install new - -> 2.0\n"
                "downgrade old 3.0 -> 2.0\n"
                "reinstall-or-unchanged same 1.0 -> 1.0",
            ),
        )
        for text, wanted in cases:
            with self.subTest(summary=wanted):
                self.assertEqual(apt.summarize_plan(text), wanted)
        plan = apt.parse_plan(PLAN_V2)
        assert plan is not None
        self.assertEqual(plan.version, 2)
        self.assertEqual(
            plan.summary(),
            "install hello - -> 2.10-3\n"
            "upgrade libc6:amd64 2.36-9 -> 2.37-1\n"
            "remove oldpkg 1.0 -> -\n"
            "configure manualpkg 1.0 -> 1.0",
        )

    def test_empty_action_section_is_explicit(self) -> None:
        plan = apt.parse_plan("VERSION 2\n\n")
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.actions, ())
        self.assertEqual(plan.summary(), "no package actions reported")

    def test_unavailable_or_malformed_plans_return_none(self) -> None:
        rejected = (
            "",
            "VERSION 4\n\n",
            "VERSION 2\n",
            "VERSION 2\nnot-an-action\n",
            "VERSION 2\n\nnot-an-action\n",
            "VERSION 2\n\ntoo-few-fields\n",
            "VERSION 2\n\na b c d unexpected\n",
            "VERSION 3\n\nshort v3 line\n",
            "version 2\n\n",
        )
        for text in rejected:
            with self.subTest(text=text):
                self.assertIsNone(apt.parse_plan(text))
                self.assertIsNone(apt.summarize_plan(text))


class WindowTest(unittest.TestCase):
    NAME = "@os-000605-20261118-131158Z-apt"

    @classmethod
    def setUpClass(cls) -> None:
        cls.state = ids.parse(cls.NAME)
        assert cls.state is not None
        cls.created = cls.state.created

    def at(self, seconds: int) -> datetime:
        return datetime.fromtimestamp(
            int(self.created.timestamp()) + seconds,
            tz=UTC,
        )

    def test_d30_window_cases(self) -> None:
        cases = (
            ("young", self.state, 900, 100, True),
            ("just-inside", self.state, 900, 899, True),
            ("at-interval", self.state, 900, 900, False),
            ("old", self.state, 900, 5_000, False),
            ("same-second", self.state, 900, 0, True),
            ("future-clock", self.state, 900, -60, False),
            ("zero-interval", self.state, 0, 1, False),
            ("legacy", ids.parse("@os-pre-20261118131158-605-apt"), 900, 100, False),
            ("bad-stamp", ids.parse("@os-pre-notadate-605-apt"), 900, 10, False),
            ("short-stamp", ids.parse("@os-pre-2026111813-605-apt"), 900, 10, False),
            ("no-prefix", ids.parse("garbage"), 900, 10, False),
            ("impossible-date", ids.parse("@os-000001-20261340-250000Z-apt"), 900, 10, False),
        )
        for label, state, interval, seconds, wanted in cases:
            with self.subTest(label=label):
                self.assertEqual(
                    apt.window_blocks(state, interval, self.at(seconds)),
                    wanted,
                )

    def test_id_epoch_and_non_aware_time(self) -> None:
        self.assertEqual(
            int(self.state.created.timestamp()),
            int(datetime(2026, 11, 18, 13, 11, 58, tzinfo=UTC).timestamp()),
        )
        with self.assertRaisesRegex(ValueError, "aware"):
            apt.window_blocks(self.state, 900, datetime(2026, 11, 18, 13, 11, 59))


if __name__ == "__main__":
    unittest.main()
