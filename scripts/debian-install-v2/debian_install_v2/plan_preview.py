"""Offline, host-independent resolution of an explicit disk layout."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from . import layout_policy
from .config import Config
from .disk_layout import GIB, automatic_swap_size_gb

_DISK_SIZE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(GiB|G|TiB|T)", re.IGNORECASE)


class PlanPreviewError(ValueError):
    """An offline plan cannot be resolved from the supplied disk facts."""


def _plan_rows(
    spec: layout_policy.DiskSpec,
    plan: layout_policy.PoolPlan,
    *,
    root_start_sector: int,
    swap_extents: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def row(entry: str, number: int | None, start: int, size: int, partlabel: str = "") -> None:
        rows.append({
            "entry": entry,
            "number": number,
            "partlabel": partlabel,
            "start": start,
            "start_gib": round(start * 512 / GIB, 2),
            "sectors": size,
            "gib": round(size * 512 / GIB, 2),
        })

    row("root", 1, root_start_sector, plan.root_size)
    number = 2
    for (start, size), entry in zip(plan.extents, spec.entries()):
        label = entry.partlabel
        row("iobench raw" if label == layout_policy.IOBENCH_PARTLABEL else "data",
            number, start, size, label)
        number += 1
    if plan.reserve_sectors:
        row("reserve (unallocated)", None, plan.reserve_start, plan.reserve_sectors)
    for start, size in swap_extents:
        row("swap", number, start, size, f"vbpub-swap{number}")
        number += 1
    rows.sort(key=lambda item: item["start"])
    return rows


def disk_size_bytes(value: str) -> int:
    match = _DISK_SIZE_RE.fullmatch(value.strip())
    if not match:
        raise PlanPreviewError("disk size must look like 20GiB, 512GiB, or 2TiB")
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise PlanPreviewError("disk size is not a valid number") from exc
    unit = match.group(2).lower()
    multiplier = GIB if unit in {"g", "gib"} else 1024 * GIB
    total = amount * multiplier
    if total <= 0 or total != total.to_integral_value() or int(total) % layout_policy.SECTOR:
        raise PlanPreviewError("disk size must be positive and an exact multiple of 512 bytes")
    return int(total)


def resolve_plan(
    config: Config,
    *,
    disk_size: str,
    ram_gib: int | None = None,
    ram_bytes: int | None = None,
    root_start_sector: int = layout_policy.ALIGN,
    root_current_size_gib: int | None = None,
    root_current_size_sectors: int | None = None,
) -> dict[str, Any]:
    """Resolve entries and display assumptions without inspecting or opening a device."""
    disk_bytes = disk_size_bytes(disk_size)
    disk_sectors = disk_bytes // layout_policy.SECTOR
    if root_start_sector < 1 or root_start_sector >= disk_sectors:
        raise PlanPreviewError("root start sector must be positive and before the end of the disk")
    safe_end = disk_sectors
    pool_end = disk_sectors
    swap_total = 0
    try:
        spec = layout_policy.disk_spec(config.partitioning)
        safe_end = layout_policy.gpt_safe_end(disk_sectors)
        swap_extents: list[tuple[int, int]] = []
        if config.swap_policy == "on":
            if config.swap_disk_total_gb is None:
                memory_bytes = ram_bytes if ram_bytes is not None else (ram_gib * GIB if ram_gib else None)
                if memory_bytes is None or memory_bytes < 1:
                    raise PlanPreviewError(
                        "automatic swap sizing needs --ram-gib for an offline plan, or set swap_disk_total_gb"
                    )
                swap_total = automatic_swap_size_gb(disk_bytes=disk_bytes, memory_bytes=memory_bytes)
            else:
                swap_total = config.swap_disk_total_gb
            target = swap_total * layout_policy.SECTORS_PER_GIB
            count = min(config.swap_partition_count, target // layout_policy.ALIGN)
            if count <= 0:
                raise PlanPreviewError("the configured swap target is smaller than one aligned partition")
            per_partition = target // count
            per_partition -= per_partition % layout_policy.ALIGN
            if per_partition <= 0:
                raise PlanPreviewError("the configured swap target cannot be divided into aligned partitions")
            actual = per_partition * count
            first = safe_end - actual
            swap_extents = [(first + index * per_partition, per_partition) for index in range(count)]
        pool_end = layout_policy.pool_end(disk_sectors, swap_extents)
        if spec.root.kind == "keep":
            if root_current_size_sectors is not None and root_current_size_sectors > 0:
                root_current = root_current_size_sectors
            elif root_current_size_gib is not None and root_current_size_gib >= 1:
                root_current = root_current_size_gib * layout_policy.SECTORS_PER_GIB
            else:
                raise PlanPreviewError(
                    'partitioning.root="keep" needs --root-current-size-gib for an offline plan'
                )
        else:
            root_current = config.preserve_root_size_gb * layout_policy.SECTORS_PER_GIB
        plan = layout_policy.plan_pool(
            spec,
            root_start=root_start_sector,
            pool_end=pool_end,
            root_current_size=root_current,
            min_root_sectors=config.preserve_root_size_gb * layout_policy.SECTORS_PER_GIB,
        )
    except layout_policy.LayoutPolicyError as exc:
        diagnostic = None
        if "spec" in locals() and "root_current" in locals():
            try:
                diagnostic = layout_policy.plan_pool(
                    spec,
                    root_start=root_start_sector,
                    pool_end=pool_end,
                    root_current_size=root_current,
                    min_root_sectors=0,
                )
            except layout_policy.LayoutPolicyError:
                pass
        if diagnostic is not None:
            table = format_table(_plan_rows(
                spec, diagnostic, root_start_sector=root_start_sector, swap_extents=swap_extents
            ))
            raise PlanPreviewError(f"{exc}\nResolved plan table (GiB):\n{table}") from exc
        raise PlanPreviewError(
            f"{exc}\nResolved capacity table (GiB):\n"
            f"  disk: start 0.00, size {disk_bytes / GIB:.2f}\n"
            f"  root-start: sector {root_start_sector} ({root_start_sector * 512 / GIB:.2f} GiB)\n"
            f"  usable-plan-end: sector {pool_end} ({pool_end * 512 / GIB:.2f} GiB)\n"
            f"  requested raw iobench: {spec.iobench_raw_gb if 'spec' in locals() else 'unresolved'} GiB\n"
            f"  requested swap: {swap_total if 'swap_total' in locals() else 'unresolved'} GiB"
        ) from exc

    rows = _plan_rows(spec, plan, root_start_sector=root_start_sector, swap_extents=swap_extents)
    number = max((row["number"] or 0 for row in rows), default=0) + 1
    if number - 1 > layout_policy.GPT_MAX_PARTITIONS:
        raise PlanPreviewError(
            f"resolved plan needs partition number {number - 1}, above the GPT limit "
            f"of {layout_policy.GPT_MAX_PARTITIONS}"
        )
    return {
        "disk_size_gib": round(disk_bytes / GIB, 2),
        "disk_sectors": disk_sectors,
        "root_start_sector": root_start_sector,
        "root_start_gib": round(root_start_sector * 512 / GIB, 2),
        "swap_policy": config.swap_policy,
        "swap_total_gib": swap_total,
        "partitioning": config.partitioning,
        "plan_table": rows,
        "assumptions": [
            "no pre-root partitions are included; root defaults to partition 1 at the supplied root-start sector",
            "the raw iobench partition is never formatted or mounted",
        ],
    }


def format_table(rows: list[dict[str, Any]]) -> str:
    """Render resolved rows for CLI, wizard, and refusal output."""
    heading = "No.  Entry                 PARTLABEL       Start GiB  Size GiB"
    lines = [heading]
    for item in rows:
        number = "-" if item["number"] is None else str(item["number"])
        lines.append(
            f"{number:>3}  {item['entry']:<20}  {item['partlabel']:<14} "
            f"{item['start_gib']:>8.2f}  {item['gib']:>8.2f}"
        )
    return "\n".join(lines)
