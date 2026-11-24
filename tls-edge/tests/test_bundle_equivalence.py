"""Manifest-selection tests for the release equivalence evidence."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "bundle_equivalence.py"
SPEC = importlib.util.spec_from_file_location("tls_edge_bundle_equivalence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
equivalence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(equivalence)


def _manifest(files: dict[str, str]) -> dict:
    return {"files": {path: {"sha256": digest} for path, digest in files.items()}}


def test_compare_accepts_only_the_named_maintainer_removals_and_equal_wheels() -> None:
    old = _manifest({
        "README.md": "old-readme",
        "scripts/verify.sh": "old-verify",
        "scripts/build-artifact.sh": "old-build",
        "wheels/ciu-1.2.3-py3-none-any.whl": "ciu",
        "wheels/cli_extended-0.8.0-py3-none-any.whl": "cli",
    })
    new = _manifest({
        "README.md": "new-readme",
        "scripts/verify.sh": "new-verify",
        "wheels/ciu-1.2.3-py3-none-any.whl": "ciu",
        "wheels/cli_extended-0.8.0-py3-none-any.whl": "cli",
    })
    for path in equivalence.EXPECTED_OLD_ONLY - {"scripts/build-artifact.sh"}:
        old["files"][path] = {"sha256": path}

    dropped, changed, wheels = equivalence.compare_manifests(old, new)

    assert dropped == sorted(equivalence.EXPECTED_OLD_ONLY)
    assert changed == ["README.md", "scripts/verify.sh"]
    assert wheels == ["ciu", "cli-extended"]


def test_compare_refuses_an_unexpected_new_operator_file() -> None:
    old = _manifest({"README.md": "old"})
    new = _manifest({"README.md": "new", "tests/test_bundle.py": "test"})
    for path in equivalence.EXPECTED_OLD_ONLY:
        old["files"][path] = {"sha256": path}

    with pytest.raises(SystemExit, match="new bundle adds operator files"):
        equivalence.compare_manifests(old, new)
