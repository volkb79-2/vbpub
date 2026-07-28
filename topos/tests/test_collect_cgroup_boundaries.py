"""Cgroup collection boundaries backed by a deterministic fixture tree."""

from __future__ import annotations

from pathlib import Path

from topos.collect.cgroup import (
    collect_cgroup,
    read_io_max_capped,
    read_io_weight,
    read_pressure,
    walk_entities,
)
from topos.model import Entity


def _write(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    return path


def test_optional_kernel_rows_are_skipped_until_a_valid_value_is_found(tmp_path: Path) -> None:
    """Blank/malformed rows do not become a fabricated pressure, weight, or cap."""
    pressure, pressure_src = read_pressure(
        _write(tmp_path / "memory.pressure", "some avg10=0.25\n\nfull avg10=bad\n")
    )

    assert (pressure, pressure_src) == ({"some": {"avg10": 0.25}, "full": {}}, "exact")
    assert read_io_weight(_write(tmp_path / "io.weight", "other 1\n\ndefault=bad\n")) == (
        None,
        "unavail_kernel",
    )
    assert read_io_max_capped(_write(tmp_path / "io.max", "8:0 riops=not-a-number\n")) == (
        0,
        "exact",
    )


def test_collect_cgroup_exposes_only_finite_io_max_caps(tmp_path: Path) -> None:
    """The collected raw counters retain finite per-device totals, not max caps."""
    _write(
        tmp_path / "io.max",
        "8:0 rbps=10 wbps=max riops=3\n8:1 rbps=5 wiops=7 unrelated=9\n",
    )

    sample = collect_cgroup(tmp_path, "", Entity(key="", kind="root", parent=None))

    assert sample.raw_counters["io.max:_available"] == 1
    assert sample.raw_counters["io.max:rbps"] == 15
    assert sample.raw_counters["io.max:riops"] == 3
    assert sample.raw_counters["io.max:wiops"] == 7
    assert "io.max:wbps" not in sample.raw_counters


def test_walk_entities_preserves_systemd_and_unknown_entity_kinds(tmp_path: Path) -> None:
    """Filesystem discovery classifies terminal systemd units and ordinary directories."""
    (tmp_path / "batch.service").mkdir()
    (tmp_path / "jobs.slice").mkdir()
    (tmp_path / "session.scope").mkdir()
    (tmp_path / "plain-directory").mkdir()

    entities = walk_entities(tmp_path)

    assert entities["batch.service"].kind == "service"
    assert entities["jobs.slice"].kind == "slice"
    assert entities["session.scope"].kind == "scope"
    assert entities["plain-directory"].kind == "other"
