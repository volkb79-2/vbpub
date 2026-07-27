from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve-playwright-version.py"
SPEC = importlib.util.spec_from_file_location("pwmcp_resolve_playwright_version", MODULE_PATH)
assert SPEC and SPEC.loader
resolver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = resolver
SPEC.loader.exec_module(resolver)


def test_resolver_uses_newest_version_all_upstreams_can_supply() -> None:
    # npm can publish first, MCR can publish second, and PyPI can lag both.
    # The release must select the last version common to all three instead of
    # emitting a Dockerfile base-image tag that does not exist yet.
    assert resolver.resolve_latest_common_version(
        {"1.61.0", "1.61.1", "1.62.0"},
        {"1.61.0"},
        {"1.61.0", "1.61.1"},
    ) == "1.61.0"


def test_mcr_versions_only_accept_exact_stable_distro_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolver,
        "_fetch_json",
        lambda _url, _label: {
            "tags": [
                "v1.61.0-noble",
                "v1.62.0-next-canary-20260709180306-noble",
                "v1.61.1-jammy",
                "v1.61-noble",
            ]
        },
    )

    assert resolver.fetch_mcr_versions("noble") == {"1.61.0"}


def test_resolver_fails_when_no_version_is_jointly_available() -> None:
    with pytest.raises(SystemExit):
        resolver.resolve_latest_common_version({"1.62.0"}, {"1.61.0"}, {"1.61.1"})
