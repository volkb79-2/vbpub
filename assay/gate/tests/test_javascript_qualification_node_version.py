"""The opt-in JavaScript qualification must match its pinned Node engines."""

import pytest

from gate.tests.node_toolchain import (
    node_version_supports_pinned_qualification_engines,
)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("v20.18.1", False),
        ("v20.19.0", True),
        ("v20.19.1", True),
        ("v21.9.0", False),
        ("v22.12.0", False),
        ("v22.13.0", True),
        ("v23.0.0", False),
        ("v24.0.0", True),
        ("v25.1.2", True),
        ("22.13", False),
        ("not-a-version", False),
    ],
)
def test_node_qualification_version_matches_pinned_eslint_engines(
    version: str, expected: bool
):
    assert node_version_supports_pinned_qualification_engines(version) is expected
