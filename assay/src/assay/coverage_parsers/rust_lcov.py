"""Producer-specific normalization for stable cargo-llvm-cov LCOV output.

The generic LCOV parser treats BRF/BRH as real branch measurements. Stable
Rust currently writes zero-only totals without BRDA records, which is the
producer's way of saying branch measurement is unavailable. Keep this
normalization outside the generic parser and call it only at the Rust adapter
boundary.
"""

from __future__ import annotations

import re

_BRANCH_MARKER = re.compile(r"^(BRF|BRH):(\d+)$")


def normalize_stable_rust_branch_markers(text: str) -> str:
    """Remove only complete zero BRF/BRH pairs from marker-only Rust LCOV.

    Any BRDA record, malformed or nonzero marker, unpaired total, duplicate
    marker, or marker outside an SF record leaves the artifact byte-for-byte
    unchanged. The normal LCOV parser then retains responsibility for
    refusing malformed branch totals.
    """
    lines = text.splitlines(keepends=True)
    if not lines or any(line.strip().startswith("BRDA:") for line in lines):
        return text

    marker_indexes: list[int] = []
    in_record = False
    record_markers: list[tuple[str, int]] = []
    saw_markers = False

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if line.startswith("SF:"):
            if in_record:
                return text
            in_record = True
            record_markers = []
            continue
        if line == "end_of_record":
            if not in_record:
                return text
            if record_markers:
                counts = {kind: 0 for kind in ("BRF", "BRH")}
                for kind, value in record_markers:
                    counts[kind] += 1
                    if value != 0:
                        return text
                if counts != {"BRF": 1, "BRH": 1}:
                    return text
            in_record = False
            record_markers = []
            continue

        if line.startswith(("BRF:", "BRH:")):
            match = _BRANCH_MARKER.fullmatch(line)
            if not in_record or match is None:
                return text
            saw_markers = True
            marker_indexes.append(index)
            record_markers.append((match.group(1), int(match.group(2))))

    if in_record or not saw_markers:
        return text
    marker_set = set(marker_indexes)
    return "".join(line for index, line in enumerate(lines) if index not in marker_set)
