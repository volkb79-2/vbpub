"""Shared fixtures for pwmcp's root-level tests."""

from __future__ import annotations

import pytest

from _testdeps import missing_message


@pytest.fixture
def require_pillow() -> None:
    """Fail (never skip) when Pillow is missing in an environment that requires it.

    ``test_ocr_service.needs_pil`` attaches this fixture when ``PWMCP_REQUIRE_TEST_DEPS=1``, which the
    registered gate lanes (assay.toml) set: the sidecar's image handling is judged code, so a gate
    environment without Pillow must be RED, not green with ~40 tests silently skipped.
    """

    try:
        import PIL  # noqa: F401
    except ImportError:
        pytest.fail(missing_message("PIL (Pillow)", "the sidecar tests decode real images"), pytrace=False)
