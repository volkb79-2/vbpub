"""The wait-text loop; clock and sleep are injected so tests need neither QEMU nor real time."""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from .errors import NoExactText, WaitTimeout


def wait_for(read: Callable[[], Any], pattern: str, timeout: float, interval: float = 0.5, *, literal: bool = False, accept_unverified: bool = False, accept_partial: bool = False,
             clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Poll ``read()`` (returns an object with ``.text`` and ``.source``) until ``pattern`` matches.

    A poll where no exact-text source applies (NoExactText) is not fatal: the screen may enter a text mode later.
    The pattern is searched (re.MULTILINE) in the whole text; ``literal`` escapes it.  One poll always runs, even
    with timeout 0.  Raises WaitTimeout (exit 3) carrying the last text seen."""
    rx = re.compile(re.escape(pattern) if literal else pattern, re.MULTILINE)
    start = clock()
    polls = 0
    last_text, last_note = "", ""
    while True:
        polls += 1
        try:
            result = read()
            last_text = result.text
            exact = getattr(result, "exact", True)
            if (exact == "unverified" and not accept_unverified) or (exact == "partial" and not accept_partial):
                last_note = f"{result.source} text {exact}: {getattr(result, 'exact_reason', '')}"  # never match on text we cannot vouch for
                m = None
            else:
                m = rx.search(result.text)
                last_note = f"last source {result.source}"
            if m:
                return {"matched": m.group(0), "span": list(m.span()), "source": result.source, "exact": exact, "polls": polls,
                        "elapsed_s": round(clock() - start, 3)}
        except NoExactText as exc:
            last_note = f"no exact text: {exc}"
        remaining = timeout - (clock() - start)
        if remaining <= 0:
            raise WaitTimeout(f"pattern {pattern!r} not seen within {timeout:g}s after {polls} polls ({last_note})", last_text)
        sleep(min(interval, remaining))
