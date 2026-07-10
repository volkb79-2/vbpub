"""Regression tests for packaging metadata.

P43: Proves the published Textual dependency is >=8.2.8 without an artificial
upper ceiling. Reads project metadata from pyproject.toml; does not query the
network or import application code beyond what is needed to find the file.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import pytest


def _find_project_root() -> Path:
    """Return the groop project root (parent of tests/)."""
    return Path(__file__).resolve().parents[1]


def _read_pyproject_toml() -> str:
    root = _find_project_root()
    return (root / "pyproject.toml").read_text("utf-8")


def _extract_textual_dependency(text: str) -> str | None:
    """Extract the raw textual dependency specifier from pyproject.toml.

    Looks for ``textual>=...`` inside the ``dependencies = [...]`` list.
    Returns the full spec (e.g. ``textual>=8.2.8``) or None.
    """
    m = re.search(
        r'dependencies\s*=\s*\[.*?"(textual[^"]*)"',
        text,
        re.DOTALL,
    )
    if m:
        return m.group(1)
    return None


def test_textual_dependency_present() -> None:
    """The pyproject.toml must declare a textual dependency."""
    content = _read_pyproject_toml()
    spec = _extract_textual_dependency(content)
    assert spec is not None, (
        "Could not find textual dependency in pyproject.toml"
    )
    assert spec.startswith("textual"), f"Unexpected spec: {spec!r}"


def test_textual_lower_bound_at_least_8_2_8() -> None:
    """The lower bound must be >=8.2.8."""
    content = _read_pyproject_toml()
    spec = _extract_textual_dependency(content)
    assert spec is not None

    m = re.search(r">=\s*([\d.]+)", spec)
    assert m, f"No >= version found in {spec!r}"
    version_str = m.group(1)
    parts = [int(x) for x in version_str.split(".")]

    assert len(parts) >= 2, f"Version too short: {version_str}"
    major, minor = parts[0], parts[1]
    patch = parts[2] if len(parts) >= 3 else 0

    assert major > 8 or (major == 8 and (minor > 2 or (minor == 2 and patch >= 8))), (
        f"Lower bound {version_str} is below 8.2.8"
    )


def test_textual_has_no_upper_ceiling() -> None:
    """No '<' upper bound must be present on the textual dependency."""
    content = _read_pyproject_toml()
    spec = _extract_textual_dependency(content)
    assert spec is not None
    assert "<" not in spec, f"Upper ceiling detected in {spec!r}"


def test_no_other_upper_ceiling_on_textual() -> None:
    """Ensure no comma-separated upper bound like 'textual>=X,<Y' exists."""
    content = _read_pyproject_toml()
    specs = re.findall(r'"textual[^"]*"', content)
    for spec in specs:
        assert "," not in spec, f"Multi-clause textual spec found: {spec}"


def test_wheel_metadata_requires_dist() -> None:
    """If a built wheel exists, check its METADATA for Requires-Dist: textual>=8.2.8.

    This is a soft check: if no wheel is found, the test skips.
    """
    root = _find_project_root()
    dist_dir = root / "dist"
    if not dist_dir.is_dir():
        pytest.skip("No dist/ directory; run 'python3 -m build groop/' first")

    wheels = list(dist_dir.glob("*.whl"))
    if not wheels:
        pytest.skip("No .whl files in dist/; run 'python3 -m build groop/' first")

    wheel_path = wheels[0]
    with zipfile.ZipFile(wheel_path, "r") as zf:
        metadata_paths = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        if not metadata_paths:
            pytest.skip(f"No METADATA found in {wheel_path.name}")
        metadata = zf.read(metadata_paths[0]).decode("utf-8")

    rd_lines = re.findall(r"^Requires-Dist:\s+(textual.*)$", metadata, re.MULTILINE)
    assert rd_lines, (
        f"No Requires-Dist: textual found in wheel METADATA:\n{metadata[:500]}..."
    )

    rd = rd_lines[0]
    assert rd.startswith("textual"), f"Unexpected Requires-Dist: {rd!r}"

    m = re.search(r">=\s*([\d.]+)", rd)
    assert m, f"No >= version in Requires-Dist: {rd!r}"
    version_str = m.group(1)
    parts = [int(x) for x in version_str.split(".")]
    assert len(parts) >= 2, f"Version too short: {version_str}"
    major, minor = parts[0], parts[1]
    patch = parts[2] if len(parts) >= 3 else 0
    assert major > 8 or (major == 8 and (minor > 2 or (minor == 2 and patch >= 8))), (
        f"Wheel lower bound {version_str} is below 8.2.8"
    )

    assert "<" not in rd, f"Upper ceiling in wheel metadata: {rd!r}"
