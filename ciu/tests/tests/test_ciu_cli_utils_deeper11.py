"""Public CLI-version fallback contracts."""
from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import ciu  # noqa: E402
from ciu import cli_utils  # noqa: E402


def _metadata_unavailable(_package: str) -> str:
    raise importlib.metadata.PackageNotFoundError("ciu")


@pytest.mark.parametrize(
    ("metadata_value", "package_value", "expected"),
    [
        ("2.4.1", "build-fallback", "2.4.1"),
        (None, "build-fallback", "build-fallback"),
        (None, None, "unknown"),
    ],
    ids=("installed-metadata", "built-package", "source-checkout"),
)
def test_cli_version_has_user_visible_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
    metadata_value: str | None,
    package_value: str | None,
    expected: str,
) -> None:
    """``ciu --version`` stays informative outside a fully installed wheel.

    Installed metadata is authoritative.  Editable/build contexts without that
    metadata still expose the package's build-time version; only a source
    checkout missing both sources reports the explicit ``unknown`` sentinel.
    """
    if metadata_value is None:
        monkeypatch.setattr(importlib.metadata, "version", _metadata_unavailable)
    else:
        monkeypatch.setattr(
            importlib.metadata, "version", lambda _package: metadata_value
        )
    if package_value is None:
        monkeypatch.delattr(ciu, "__version__")
    else:
        monkeypatch.setattr(ciu, "__version__", package_value)

    assert cli_utils.get_cli_version() == expected
