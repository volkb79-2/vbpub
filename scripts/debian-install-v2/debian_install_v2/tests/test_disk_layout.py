from __future__ import annotations

import pytest

from debian_install_v2.disk_layout import GIB, automatic_swap_size_gb


@pytest.mark.parametrize(
    ("disk_gb", "ram_gb", "expected"),
    [
        (20, 4, 4), (32, 8, 6), (64, 16, 12),
        (128, 32, 24), (256, 64, 48), (512, 128, 95),
        (512, 4, 8), (20, 128, 4),
    ],
)
def test_automatic_swap_uses_twice_ram_and_rounded_twenty_percent_disk_cap(disk_gb, ram_gb, expected):
    # Provider-labelled GB are decimal; sizing uses the actual byte capacity.
    actual = automatic_swap_size_gb(
        disk_bytes=disk_gb * 1_000_000_000,
        memory_bytes=ram_gb * 1_000_000_000,
    )
    assert actual == expected


@pytest.mark.parametrize("disk_bytes,memory_bytes", [(0, GIB), (GIB, 0), (-1, GIB)])
def test_automatic_swap_refuses_missing_or_invalid_host_facts(disk_bytes, memory_bytes):
    with pytest.raises(ValueError):
        automatic_swap_size_gb(disk_bytes=disk_bytes, memory_bytes=memory_bytes)
