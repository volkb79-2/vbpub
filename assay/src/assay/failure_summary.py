"""Name the first failing test in a captured R0 command output (B146).

Pure text reading, advisory and display-only. It never changes an outcome, a
reason code or a verdict field: ``assay run``'s one-line headline stays the
verdict's own pair. Its only job is to let the concise summary say that the R0
command measurably failed, and which test failed first, even when a later
refusal (for example ``NO_MEASUREMENT``/``DIRTY_TREE`` after a failing suite
dirtied the tree) is what the headline reports.

Recognised producers, each of which a lane's declared R0 command may be:

* pytest: the ``FAILED``/``ERROR`` lines of the short test summary, falling
  back to the ``____ name ____`` section header of the failures block.
* ``go test`` text: ``--- FAIL: Name``; ``go test -json``: the first event
  whose ``Action`` is ``fail`` and that names a ``Test``.
* vitest text: `` FAIL  file > suite > test``.
* jest text: ``● suite › test``.

Output no recogniser matches yields ``None``: the summary then states nothing
rather than guessing. The scan sees only the bounded tail the verdict retains,
so the name is the first failure visible in that tail.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Iterable

__all__ = ["first_failing_test"]

#: Display bound for one name; a longer one is cut, never rejected.
MAX_NAME_CHARS = 200

_PYTEST_SUMMARY = re.compile(r"^(?:FAILED|ERROR) (\S*(?:::|\.py)\S*)", re.MULTILINE)
_PYTEST_HEADER = re.compile(r"^_{2,} (\S.*?) _{2,}$", re.MULTILINE)
_GO_TEXT = re.compile(r"^\s*--- FAIL: (\S+)", re.MULTILINE)
_VITEST = re.compile(r"^\s*FAIL +(\S.* > \S.*)$", re.MULTILINE)
_JEST = re.compile(r"^\s*● (\S.* › \S.*)$", re.MULTILINE)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _regex(pattern: re.Pattern[str]) -> Callable[[str], tuple[int, str] | None]:
    def find(text: str) -> tuple[int, str] | None:
        match = pattern.search(text)
        return None if match is None else (match.start(), match.group(1))

    return find


def _go_json(text: str) -> tuple[int, str] | None:
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                event = json.loads(stripped)
            except (ValueError, RecursionError):
                event = None
            if (
                isinstance(event, dict)
                and event.get("Action") == "fail"
                and isinstance(event.get("Test"), str)
                and event["Test"]
            ):
                return offset, event["Test"]
        offset += len(line)
    return None


#: Earliest position wins across these. The pytest header is a fallback only:
#: it carries a bare function name where the short summary carries the node id.
_RECOGNISERS: tuple[Callable[[str], tuple[int, str] | None], ...] = (
    _regex(_PYTEST_SUMMARY),
    _regex(_GO_TEXT),
    _go_json,
    _regex(_VITEST),
    _regex(_JEST),
)
_FALLBACK = _regex(_PYTEST_HEADER)


def _clean(name: str) -> str:
    name = _CONTROL.sub(" ", name).strip()
    if len(name) > MAX_NAME_CHARS:
        name = name[:MAX_NAME_CHARS]
    return name


def first_failing_test(*texts: str | None) -> str | None:
    """The first failing test named in the first of *texts* that names one.

    Texts are tried in order (stdout before stderr); ``None`` is skipped.
    """
    for text in texts:
        if not text:
            continue
        found: Iterable[tuple[int, str]] = (
            hit for hit in (read(text) for read in _RECOGNISERS) if hit is not None
        )
        best = min(found, default=None)
        if best is None:
            best = _FALLBACK(text)
        if best is not None:
            name = _clean(best[1])
            if name:
                return name
    return None
