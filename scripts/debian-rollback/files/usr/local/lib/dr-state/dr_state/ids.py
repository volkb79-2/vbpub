"""One parser and generator for state IDs and loader entry IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cmp_to_key
from typing import Iterable

_STATE_RE = re.compile(
    r"^@os-(?P<sequence>[0-9]{6})-(?P<date>[0-9]{8})-"
    r"(?P<time>[0-9]{6})Z-(?P<slug>[a-z0-9_-]{1,64})$"
)
_COUNTING_SUFFIX_RE = re.compile(r"\+[0-9]+(-[0-9]+)?$")
_MANUAL_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_DASH_RUN_RE = re.compile(r"-{2,}")
_MANUAL_SLUG_SHAPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]*[a-z0-9]")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


@dataclass(frozen=True, slots=True)
class StateId:
    """A managed state name and its derived identity fields."""

    name: str
    sequence: int
    created: datetime
    slug: str
    bare: str


def parse(value: object) -> StateId | None:
    """Parse the only supported managed state-name grammar."""

    if not isinstance(value, str):
        return None
    match = _STATE_RE.fullmatch(value)
    if match is None:
        return None
    sequence = int(match["sequence"])
    if sequence == 0:
        return None
    stamp = match["date"] + match["time"]
    try:
        created = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    if created.strftime("%Y%m%d%H%M%S") != stamp:
        return None
    bare = value[1:]
    return StateId(
        name=value,
        sequence=sequence,
        created=created,
        slug=match["slug"],
        bare=bare,
    )


def make(sequence: int, created: datetime, slug: str) -> StateId | None:
    """Build a canonical ID, accepting only a real UTC second."""

    if not isinstance(sequence, int) or isinstance(sequence, bool):
        return None
    if not 1 <= sequence <= 999_999:
        return None
    if created.tzinfo is None or created.utcoffset() != timedelta(0):
        return None
    name = (
        f"@os-{sequence:06d}-{created:%Y%m%d-%H%M%SZ}-{slug}"
    )
    return parse(name)


def slug(source: str, reason: str) -> str | None:
    """Return the fixed or sanitized slug for an ID; None when there is none.

    A manual reason that normalizes to ``manual`` or already starts with
    ``manual-`` is kept as is, and one that normalizes to nothing is refused.
    """

    if source == "apt":
        return "apt"
    if source == "install":
        return "install"
    if source != "manual":
        return None
    # The bash pipeline, step by step: ASCII-only lowering (``LC_ALL=C tr``),
    # every other character run to one ``-`` with all ``-`` runs squeezed
    # (``tr -cs``), and only ``-`` stripped at both ends (``_`` stays).
    lowered = reason.translate(_ASCII_LOWER)
    normalized = _DASH_RUN_RE.sub("-", _MANUAL_SLUG_RE.sub("-", lowered)).strip("-")
    if not normalized:
        return None
    if normalized != "manual" and not normalized.startswith("manual-"):
        normalized = "manual-" + normalized
    cut = normalized[:64]
    if cut.endswith("-"):
        cut = cut[:-1]
    # Like bash, a slug that does not start and end with a letter or digit
    # (for example a trailing ``_``) is refused rather than repaired.
    return cut if _MANUAL_SLUG_SHAPE_RE.fullmatch(cut) else None


def entry_id(state: StateId) -> str:
    """Map a managed state to systemd-boot's entry ID."""

    return f"{state.bare}.conf"


def parse_entry_id(value: object) -> StateId | None:
    """Map an entry or selector value back through the one grammar."""

    if not isinstance(value, str) or value.startswith("@"):
        return None
    without_extension = re.sub(r"\.conf$", "", value, flags=re.IGNORECASE)
    without_count = _COUNTING_SUFFIX_RE.sub("", without_extension)
    return parse("@" + without_count)


def unmanaged(value: object) -> bool:
    """Whether a storage object has the reserved prefix but no managed ID."""

    return isinstance(value, str) and value.startswith("@os-") and parse(value) is None


def _digit(char: str) -> bool:
    return "0" <= char <= "9"


def _order(char: str) -> int:
    if _digit(char):
        return 0
    if "a" <= char.lower() <= "z":
        return ord(char)
    if char == "~":
        return -1
    return ord(char) + 256


_FILE_VERSION_SUFFIX_RE = re.compile(r"(?:\.[A-Za-z~][A-Za-z0-9~]*)*$")


def _verrevcmp(left: str, right: str) -> int:
    """Compare version prefixes with the dpkg ``verrevcmp`` rule."""

    i = j = 0
    while i < len(left) or j < len(right):
        while (i < len(left) and not _digit(left[i])) or (
            j < len(right) and not _digit(right[j])
        ):
            a = _order(left[i]) if i < len(left) else 0
            b = _order(right[j]) if j < len(right) else 0
            if a != b:
                return a - b
            i += 1
            j += 1
        while i < len(left) and left[i] == "0":
            i += 1
        while j < len(right) and right[j] == "0":
            j += 1
        first_difference = 0
        while i < len(left) and _digit(left[i]) and j < len(right) and _digit(right[j]):
            first_difference = first_difference or ord(left[i]) - ord(right[j])
            i += 1
            j += 1
        if i < len(left) and _digit(left[i]):
            return 1
        if j < len(right) and _digit(right[j]):
            return -1
        if first_difference:
            return first_difference
    return 0


def compare_versions(left: str, right: str) -> int:
    """Compare names with GNU ``sort -V``'s ``filevercmp`` rule."""

    left_prefix = _FILE_VERSION_SUFFIX_RE.sub("", left)
    right_prefix = _FILE_VERSION_SUFFIX_RE.sub("", right)
    result = _verrevcmp(left_prefix, right_prefix)
    if result:
        return result
    # filevercmp compares the complete names byte-for-byte after an equal
    # prefix key. Kernel versions are ASCII, so UTF-8 bytes equal strcmp.
    left_bytes = left.encode("utf-8")
    right_bytes = right.encode("utf-8")
    return (left_bytes > right_bytes) - (left_bytes < right_bytes)


version_key = cmp_to_key(compare_versions)


def newest_first(versions: Iterable[str]) -> tuple[str, ...]:
    """Sort kernel versions newest first using ``version_key``."""

    return tuple(sorted(versions, key=version_key, reverse=True))
