"""Bounded reads: no reader in this package may load a stream-sized file to answer a question.

The estate's record sets sit next to the streams they describe (`<run>.jsonl` can be gigabytes)
and the gate directories hold logs; every read here is capped at a head or a tail so a reader's
memory use cannot depend on the size of a file it did not need.
"""

from __future__ import annotations

import os
from pathlib import Path

# A record file (`.done`, `.route`, `.thread`, `.wt`, `.live`, `meta.json`, `result.json`,
# `STATE.md` head) is small; a bigger one is truncated rather than held whole.
SMALL_LIMIT = 4096
# `last.txt`, `lane.log`: only the tail can carry the verdict line.
TAIL_LIMIT = 8192


def walk(directory: Path) -> tuple[list[Path], str]:
    """The entries of `directory`, or none plus the reason it could not be listed.

    An unreadable directory (permissions, an ACL, a stale mount) is ONE source's problem, not
    the command's: the caller reports that source as unreadable and keeps the others.
    """

    try:
        return sorted(directory.iterdir()), ""
    except OSError as error:
        return [], f"cannot read {directory}: {error}"


def head(path: Path, limit: int = SMALL_LIMIT) -> str | None:
    """The first `limit` bytes of `path` as text; ``None`` when it cannot be read."""

    try:
        with path.open("rb") as handle:
            return handle.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def tail(path: Path, limit: int = TAIL_LIMIT) -> str | None:
    """The last `limit` bytes of `path`; a partially read first line is dropped.

    Dropping it matters: a truncated line can produce a false match at its start, and a
    consumer reading a verdict from half a line is exactly the "absence for emptiness" class
    of wrong answer.
    """

    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - limit)
            handle.seek(start)
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        return None
    if start > 0:
        text = text.partition("\n")[2]
    return text


def whole(path: Path, limit: int) -> str | None:
    """The whole file when it is smaller than `limit`, else ``None`` (a source we will not read).

    A caller that cannot read a source in full must say `unknown` rather than read a prefix of
    it and report the prefix as the file's content.
    """

    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > limit:
        return None
    return head(path, limit)


def last_line_with_prefix(path: Path, prefix: str, *, chunk: int = 65536) -> str | None:
    """The LAST line of `path` that starts with `prefix`, read in bounded chunks.

    Streaming rather than seeking to the tail is deliberate: the line has to be the last one
    that STARTS with the prefix, and a tail window can hold only a suffix of a long log — a
    verdict written before a wall of test output would silently disappear from a tail read.
    Memory stays at one chunk plus one line.
    """

    return last_line_matching(path, (prefix,), chunk=chunk)


def last_line_matching(path: Path, prefixes: tuple[str, ...], *, chunk: int = 65536) -> str | None:
    """The LAST line of `path` that starts with ANY of `prefixes`, in one streaming pass.

    This is the primitive a consumer uses when the question is "where did this file leave off",
    not "what is near its end": a `last.txt` can have kilobytes of prose after its last
    `READY`/`YIELD` line, and a tail window would then report no position at all — the
    "absence for emptiness" answer. One pass, one chunk plus one line of memory.
    """

    found: str | None = None
    pending = ""
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(chunk)
                if not block:
                    break
                pending += block.decode("utf-8", "replace")
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines:
                    if line.startswith(prefixes):
                        found = line
            if pending.startswith(prefixes):
                found = pending
    except OSError:
        return None
    return found
