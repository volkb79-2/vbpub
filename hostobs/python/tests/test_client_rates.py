#!/usr/bin/env python3
"""Tests of `hostobs_client.rates` against `fixtures/rates/rates.json` (WIRE.md 7.5).

    python3 -I -m unittest discover -s python/tests -p 'test_client_rates.py' -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HOSTOBS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOSTOBS / "python"))

from hostobs_client.rates import Reading, rate  # noqa: E402

CASES = json.loads((HOSTOBS / "fixtures" / "rates" / "rates.json").read_text(encoding="utf-8"))["cases"]


class Rates(unittest.TestCase):
    def test_every_fixture_case(self):
        self.assertGreater(len(CASES), 10)
        for case in CASES:
            got = rate(case["previous"], case["current"], case["nominal_interval_ns"])
            if "expect" in case:
                self.assertTrue(got["ok"], case["case"])
                for key, value in case["expect"].items():
                    if key == "per_second":
                        self.assertAlmostEqual(got[key], value, delta=case.get("per_second_tolerance", 1e-9))
                    else:
                        self.assertEqual(got[key], value, f"{case['case']}: {key}")
            else:
                self.assertFalse(got["ok"], case["case"])
                for key, value in case["expect_refusal"].items():
                    self.assertEqual(got[key], value, f"{case['case']}: {key}")

    def test_the_delta_is_an_exact_integer_and_the_division_comes_last(self):
        big = 2**64 - 1
        got = rate(Reading(0, big - 1), Reading(10**9, big), 10**9)
        self.assertEqual((got["delta"], got["per_second"]), (1, 1.0))
        got = rate(Reading(0, 2**53 + 1), Reading(10**9, 2**53 + 3), 10**9)
        self.assertEqual(got["delta"], 2)
        self.assertIsInstance(got["delta"], int)
        got = rate(Reading(0, 0), Reading(1, big), 0)
        self.assertEqual(got["per_second"], big * 10**9 / 1)

    def test_integer_delta_first_differs_from_float_first_and_is_pinned(self):
        delta, elapsed = 12745503462193745463, 4051686261
        got = rate(Reading(0, 0), Reading(elapsed, delta), 10**9)
        self.assertEqual(got["per_second"], delta * 10**9 / elapsed)
        self.assertEqual(got["per_second"], 3.145728134203564e18)
        self.assertNotEqual(got["per_second"], float(delta) * 1e9 / elapsed)

    def test_mono_ns_is_validated_like_value(self):
        for bad in (1.5, -1, 2**64, True, None, "5"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rate(Reading(bad, 1), Reading(10**9, 2), 1)
            with self.assertRaises(ValueError, msg=repr(bad)):
                rate(Reading(0, 1), Reading(bad, 2), 1)
        with self.assertRaises(ValueError):
            rate(Reading(0.0, None, "absent"), Reading(10**9, 2), 1)
        self.assertTrue(rate(Reading(0, 1), Reading(2**64 - 1, 2), 1)["ok"])

    def test_missed_samples_saturate_and_a_zero_nominal_reports_none(self):
        self.assertEqual(rate(Reading(0, 0), Reading(10**9 * 2**33, 5), 10)["missed"], 2**32 - 1)
        self.assertEqual(rate(Reading(0, 0), Reading(10**9, 5), 0)["missed"], 0)

    def test_quality_codes_are_accepted_and_bad_input_is_a_value_error(self):
        self.assertFalse(rate(Reading(0, None, 1), Reading(5, 3), 1)["ok"])
        self.assertEqual(rate(Reading(0, None, 3), Reading(5, 3), 1)["quality"], "stale")
        with self.assertRaises(ValueError):
            rate(Reading(0, 1, 99), Reading(5, 3), 1)
        with self.assertRaises(ValueError):
            rate(Reading(0, 1, "bogus"), Reading(5, 3), 1)
        with self.assertRaises(ValueError):
            rate(Reading(0, -1), Reading(5, 3), 1)
        self.assertEqual(rate((0, 1), (10**9, 2), 10**9)["delta"], 1)


if __name__ == "__main__":
    unittest.main()
