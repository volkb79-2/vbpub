"""One parser and generator for state IDs and loader entry IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

_STATE_RE = re.compile(
    r"^@os-(?P<sequence>[0-9]{6})-(?P<date>[0-9]{8})-"
    r"(?P<time>[0-9]{6})Z-(?P<slug>[a-z0-9_-]{1,64})$"
)
_COUNTING_SUFFIX_RE = re.compile(r"\+[0-9]+(-[0-9]+)?$")
_MANUAL_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


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
    """Return the fixed or sanitized origin/reason component for an ID."""

    if source == "apt":
        return "apt"
    if source == "install":
        return "install"
    if source != "manual":
        return None
    normalized = _MANUAL_SLUG_RE.sub("-", reason.lower()).strip("-_")
    result = ("manual-" + normalized)[:64].rstrip("-_")
    if not result:
        result = "manual"
    return result


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


def version_key(version: str) -> tuple[tuple[int, int, str], ...]:
    """A deterministic approximation of GNU ``sort -V`` for kernel names."""

    tokens: list[tuple[int, int, str]] = []
    for match in re.finditer(r"[0-9]+|[A-Za-z]+|~|.", version):
        token = match.group(0)
        if token.isdigit():
            tokens.append((3, int(token), token))
        elif token == "~":
            tokens.append((0, 0, ""))
        else:
            tokens.append((2, 0, token))
    tokens.append((1, 0, ""))
    return tuple(tokens)


def newest_first(versions: Iterable[str]) -> tuple[str, ...]:
    """Sort kernel versions newest first using ``version_key``."""

    return tuple(sorted(versions, key=version_key, reverse=True))
