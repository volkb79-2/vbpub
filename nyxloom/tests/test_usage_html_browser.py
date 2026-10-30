"""Collected opt-in acceptance test for the managed offline HTML browser probe."""

from __future__ import annotations

from _testdeps import optional_module, require_browser_acceptance


def test_usage_html_browser_acceptance():
    require_browser_acceptance()
    optional_module(
        "pwmcp_client.session",
        "the managed PWMCP client is required for real-browser interaction checks",
    )
    from usage_html_browser_check import run_browser_check

    run_browser_check()
