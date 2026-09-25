"""Leaf helpers for the canonical native mutation-candidate identity."""

from __future__ import annotations

import hashlib


def candidate_id_from_fields(
    *,
    path: str,
    source_sha256: str,
    start_byte: int,
    end_byte: int,
    mutated_file_sha256: str,
    operator: str,
) -> str:
    """Return the stable candidate digest from its recorded identity inputs.

    Keep this module dependency-free: planning, execution, verdict construction
    and the independent verifier all use this one canonical byte sequence.
    """
    if not isinstance(path, str) or not path or "\0" in path:
        raise ValueError("candidate path must be a non-empty NUL-free string")
    if not isinstance(operator, str) or not operator or "\0" in operator:
        raise ValueError("candidate operator must be a non-empty NUL-free string")
    for name, digest in (
        ("source_sha256", source_sha256),
        ("mutated_file_sha256", mutated_file_sha256),
    ):
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    if (
        isinstance(start_byte, bool)
        or isinstance(end_byte, bool)
        or not isinstance(start_byte, int)
        or not isinstance(end_byte, int)
        or start_byte < 0
        or end_byte <= start_byte
    ):
        raise ValueError("candidate byte span must be a non-empty half-open range")
    identity = "\0".join(
        (
            path,
            source_sha256,
            str(start_byte),
            str(end_byte),
            mutated_file_sha256,
            operator,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
