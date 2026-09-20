from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from hypothesis import given, strategies as st


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


@st.composite
def _version_numbers(draw) -> set[tuple[int, int, int]]:
    return draw(
        st.sets(
            st.tuples(
                st.integers(min_value=0, max_value=99),
                st.integers(min_value=0, max_value=99),
                st.integers(min_value=0, max_value=99),
            ),
            min_size=1,
            max_size=20,
        )
    )


@given(_version_numbers())
def test_common_version_selection_is_the_highest_stable_intersection(
    versions: set[tuple[int, int, int]],
) -> None:
    """A newly published version is usable only when every upstream agrees."""
    common = {"%d.%d.%d" % version for version in versions}
    npm = common | {"999.0.0-beta", "garbage"}
    pypi = common | {"998.0.0rc1"}
    mcr = common | {"1.2.3-next"}
    expected = max(common, key=lambda value: tuple(map(int, value.split("."))))
    assert resolver.resolve_latest_common_version(npm, pypi, mcr) == expected
