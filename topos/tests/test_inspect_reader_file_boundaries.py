"""Filesystem-backed boundary coverage for inspect-files reader safety."""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.inspect_files.reader import (
    InspectFilesReadError,
    _bounded_read,
    _confine_and_open,
    build_inspect_read,
)


def test_confined_open_rejects_a_path_outside_the_allowlisted_tmp_root(
    tmp_path: Path,
) -> None:
    """Descriptor traversal must not turn an outside file into readable content."""
    allow_root = tmp_path / "allowed"
    allow_root.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("must not be disclosed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not under"):
        _confine_and_open(outside, allow_root)


def test_bounded_read_detects_more_source_after_an_exact_byte_cap(
    tmp_path: Path,
) -> None:
    """The returned content stays within the cap yet reports withheld bytes."""
    payload = tmp_path / "large.log"
    payload.write_bytes(b"a" * 65_537)

    with payload.open("rb") as buf:
        content, trunc_bytes, trunc_lines, total_bytes, total_lines = _bounded_read(
            buf, max_bytes=65_536, max_lines=10
        )

    assert content == "a" * 65_536
    assert trunc_bytes is True
    assert trunc_lines is False
    assert (total_bytes, total_lines) == (65_536, 0)


@pytest.mark.parametrize(
    ("target", "timeout", "message"),
    [
        ("unsafe unit;", 30.0, "unsafe characters"),
        ("safe.service", 61.0, "exceeds maximum"),
    ],
)
def test_journal_validation_returns_typed_errors_without_starting_a_process(
    tmp_path: Path, target: str, timeout: float, message: str
) -> None:
    """Invalid journal requests fail as typed results before any external read."""
    result = build_inspect_read(
        "systemd-journal",
        target,
        inspect_files=True,
        admin=True,
        fixture_root=tmp_path,
        is_root=lambda: True,
        journal_timeout=timeout,
    )

    assert isinstance(result, InspectFilesReadError)
    assert result.kind is not None
    assert result.mode == "error"
    assert message in result.error


def test_unknown_kind_is_a_typed_error_after_authorization(tmp_path: Path) -> None:
    """A caller gets an error result, never a path lookup, for an unknown kind."""
    result = build_inspect_read(
        "not-a-catalog-kind",
        "target",
        inspect_files=True,
        admin=True,
        fixture_root=tmp_path,
        is_root=lambda: True,
    )

    assert isinstance(result, InspectFilesReadError)
    assert result.kind is None
    assert result.to_jsonable()["kind"] == "none"
    assert "not-a-catalog-kind" in result.error
