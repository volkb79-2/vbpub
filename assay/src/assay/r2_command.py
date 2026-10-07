"""Pure helpers for the native R2 no-coverage command contract."""

from __future__ import annotations

import hashlib
from typing import Final, Sequence

R2_TRANSFORM_ID: Final = "assay-r2-pytest-nocov/1"
R2_APPENDED: Final[tuple[str, ...]] = ("-p", "no:pytest_cov")


class UnrecognizedCoverageOption(ValueError):
    """A coverage-like pytest option cannot be removed without guessing."""


def transform_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Remove the declared pytest-cov options without changing other tokens."""
    transformed: list[str] = []
    for token in argv:
        if not isinstance(token, str):
            raise TypeError(f"R2 argv entries must be strings, got {token!r}")
        if token == "--cov-branch":
            continue
        if token.startswith("--cov=") and len(token) > len("--cov="):
            continue
        if token.startswith("--cov-report=") and len(token) > len("--cov-report="):
            continue
        if (
            token == "--cov"
            or token == "--no-cov"
            or token.startswith("--cov")
            or token.startswith("--no-cov")
        ):
            raise UnrecognizedCoverageOption(token)
        transformed.append(token)
    return tuple(transformed)


def collection_digest(node_ids: Sequence[str]) -> str:
    """Hash ordered, duplicate-preserving UTF-8 node IDs using byte netstrings."""
    parts: list[bytes] = []
    for node_id in node_ids:
        if not isinstance(node_id, str):
            raise TypeError(f"collection node IDs must be strings, got {node_id!r}")
        encoded = node_id.encode("utf-8")
        parts.extend((str(len(encoded)).encode("ascii"), b":", encoded, b","))
    return hashlib.sha256(b"".join(parts)).hexdigest()
