"""Parse raw per-vdev write counters from ``zpool iostat -v -p`` output."""

from __future__ import annotations


def parse_vdev_write_counters(output: str, devices: list[str]) -> dict[str, tuple[int, int]]:
    """Return total write operations/bytes observed for each exact vdev path."""
    if not devices or len(set(devices)) != len(devices):
        raise ValueError("expected a non-empty list of unique vdev paths")

    operations_index: int | None = None
    bytes_index: int | None = None
    for line in output.splitlines():
        columns = line.split()
        if columns and columns[0] == "pool" and columns.count("write") >= 2:
            first_write = columns.index("write")
            operations_index = first_write
            bytes_index = columns.index("write", first_write + 1)
            break
    if operations_index is None or bytes_index is None:
        raise ValueError("zpool iostat output is missing its pool/vdev write headers")

    totals = {device: [0, 0] for device in devices}
    found: set[str] = set()
    for line in output.splitlines():
        columns = line.split()
        if not columns or columns[0] not in totals:
            continue
        if len(columns) <= bytes_index:
            raise ValueError(f"short zpool iostat row for {columns[0]!r}: {line!r}")
        try:
            operations = int(columns[operations_index])
            byte_count = int(columns[bytes_index])
        except ValueError as exc:
            raise ValueError(f"non-integer zpool iostat counters for {columns[0]!r}: {line!r}") from exc
        if operations < 0 or byte_count < 0:
            raise ValueError(f"negative zpool iostat counter for {columns[0]!r}: {line!r}")
        totals[columns[0]][0] += operations
        totals[columns[0]][1] += byte_count
        found.add(columns[0])

    missing = sorted(set(devices) - found)
    if missing:
        raise ValueError(f"zpool iostat output omitted vdev path(s): {missing!r}")
    return {device: (values[0], values[1]) for device, values in totals.items()}
