"""Captured real-toolchain failure lines remain recognized as R0 witnesses."""

from __future__ import annotations

from conftest import TESTS_ROOT

from assay.failure_summary import first_failing_test


def test_captured_nextest_failure_fixture_names_the_failing_test():
    text = (TESTS_ROOT / "fixtures/rust/nextest-failure.txt").read_text(encoding="utf-8")

    assert first_failing_test(text) == "assay-rust-fixture integration::forced_failure"


def test_captured_libtest_failure_fixture_names_the_failing_test():
    text = (TESTS_ROOT / "fixtures/rust/libtest-failure.txt").read_text(encoding="utf-8")

    assert first_failing_test(text) == "integration::forced_failure"
