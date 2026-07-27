"""Behavioural boundaries for cgroup-v2 text parsers.

Kernel control files are not guaranteed to contain only well-formed rows;
malformed optional fields must be ignored rather than making a collection pass
fail or fabricating a finite cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.collect.cgroup import (
    build_entity_predicate,
    parse_int_text,
    read_cpu_max,
    read_flat_kv,
    read_io_max_capped,
    read_io_max_caps,
    read_io_stat,
    read_io_weight,
    read_pressure,
)


def _write(tmp_path: Path, name: str, contents: str) -> Path:
    path = tmp_path / name
    path.write_text(contents, encoding="utf-8")
    return path


def test_parse_int_text_distinguishes_unlimited_and_malformed_values() -> None:
    assert parse_int_text(" max ") is None
    assert parse_int_text(" 42 ") == 42
    assert parse_int_text("forty-two") is None


def test_flat_and_io_stat_ignore_malformed_rows_and_values(tmp_path: Path) -> None:
    flat, flat_source = read_flat_kv(_write(tmp_path, "flat", "good 7\nbad nope\nlonely\n"))
    io, io_source = read_io_stat(
        _write(tmp_path, "io.stat", "8:0 rbytes=7 wbytes=bad\n\n8:1 rios=3\n")
    )

    assert (flat, flat_source) == ({"good": 7}, "exact")
    assert (io, io_source) == ({"8:0": {"rbytes": 7}, "8:1": {"rios": 3}}, "exact")


def test_pressure_ignores_blank_and_malformed_values(tmp_path: Path) -> None:
    pressure, source = read_pressure(
        _write(tmp_path, "pressure", "\nsome avg10=0.25 total=bad\nfull avg10=1.0\n")
    )

    assert source == "exact"
    assert pressure == {"some": {"avg10": 0.25}, "full": {"avg10": 1.0}}


@pytest.mark.parametrize(
    "contents, expected",
    [
        ("default 100\n", (100, "exact")),
        ("default nope\ndefault=200\n", (200, "exact")),
        ("default=bad\n", (None, "unavail_kernel")),
    ],
)
def test_io_weight_accepts_both_kernel_formats_and_rejects_bad_values(
    tmp_path: Path, contents: str, expected: tuple[int | None, str]
) -> None:
    assert read_io_weight(_write(tmp_path, "io.weight", contents)) == expected


def test_io_max_reports_finite_caps_without_treating_max_or_bad_values_as_caps(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "io.max",
        "8:0 rbps=max wbps=100 riops=bad\n8:1 wbps=50 wiops=9 unrelated=4\n",
    )

    assert read_io_max_capped(path) == (1, "exact")
    assert read_io_max_caps(path) == (
        {"rbps": None, "wbps": 150, "riops": None, "wiops": 9},
        "exact",
    )


@pytest.mark.parametrize(
    "contents, expected",
    [
        ("max 100000\n", (None, 100000, "unlimited")),
        ("25000 100000\n", (25000, 100000, "exact")),
        ("25000\n", (None, None, "unavail_kernel")),
        ("25000 nope\n", (None, None, "unavail_kernel")),
    ],
)
def test_cpu_max_requires_a_valid_quota_period_pair(
    tmp_path: Path, contents: str, expected: tuple[int | None, int | None, str]
) -> None:
    assert read_cpu_max(_write(tmp_path, "cpu.max", contents)) == expected


def test_entity_predicate_matches_globs_and_slice_subtree_but_rejects_traversal() -> None:
    predicate = build_entity_predicate(("*.service",), ("system.slice",))
    assert predicate is not None
    assert predicate("api.service")
    assert predicate("system.slice")
    assert predicate("system.slice/worker.scope")
    assert not predicate("user.slice/session.scope")

    with pytest.raises(ValueError, match="parent traversal"):
        build_entity_predicate(None, ("../escape",))
