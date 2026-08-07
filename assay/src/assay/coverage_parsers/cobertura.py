"""Cobertura XML parser — ``<coverage><packages><package><classes><class>
<lines><line number=".." hits=".."/>``.

No prior art anywhere in this estate (handoff item 6: zero hits for
``cobertura`` across dstdns, topos, nyxloom and shared-ramdisk-depot-manager).
Built from the public Cobertura DTD
(``https://raw.githubusercontent.com/cobertura/web/master/htdocs/xml/
coverage-04.dtd``), the same one coverage.py's own ``coverage xml`` command
targets — cross-referenced against a REAL sample this estate already has,
``/workspaces/netcup-api-filter/coverage.xml`` (produced by coverage.py
7.15.2), which is reference material for the format's actual shape only; this
format's own test fixtures are independently hand-written (A-080), not
copied from that file.

The DTD's ``<line>`` element has no exclusion attribute at all —
``<!ATTLIST line number CDATA #REQUIRED>``, ``hits``, ``branch`` and
``condition-coverage`` are the complete list — confirmed against the real
sample, whose ``<line>`` elements carry only ``number``/``hits``. So, the
same reasoning as lcov: ``FileCoverage.excluded`` is ALWAYS ``None`` for this
format (DESIGN-GUIDE §11, A-008) — the format cannot say "zero exclusions"
any more than it can name one.

``<class filename="...">`` is the per-file key, not ``<class name="...">``:
``name`` is the importable module/class name, ``filename`` is the path
relative to ``<source>`` — the real sample's own ``account_auth.py`` class
carries both, and they differ in general (a package's ``__init__.py`` has
``name="__init__"``, ``filename="__init__.py"``). Grouped by filename rather
than assumed one ``<class>`` per file, because the DTD lets many ``<class>``
elements share a ``<package>`` and does not forbid two of them naming the
same file; where that happens, line hits are merged (executed wins on
conflict) the same way lcov's repeated ``DA:`` records are.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import CoverageProfile, FileCoverage


def sniff(text: str) -> bool:
    """The text contains a ``<coverage`` element open tag (DESIGN-GUIDE §5's
    literal signature: ``<coverage`` → cobertura)."""
    return "<coverage" in text


def parse(text: str) -> CoverageProfile:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise _malformed(f"not well-formed XML: {exc}") from exc
    if root.tag != "coverage":
        raise _malformed(f"root element is <{root.tag}>, expected <coverage>")

    hits_by_file: dict[str, dict[int, int]] = {}
    for class_el in root.iter("class"):
        filename = class_el.get("filename")
        if not filename:
            raise _malformed("a <class> element has no 'filename' attribute")
        hits = hits_by_file.setdefault(filename, {})
        lines_el = class_el.find("lines")
        if lines_el is None:
            continue
        for line_el in lines_el.findall("line"):
            number = _int_attr(line_el, "number", positive=True)
            count = _int_attr(line_el, "hits", positive=False)
            # executed wins if the same file's line is reported by more than
            # one <class> element (see module docstring).
            hits[number] = max(count, hits.get(number, 0))

    files = {
        path: FileCoverage(
            executed=frozenset(n for n, c in h.items() if c > 0),
            missing=frozenset(n for n, c in h.items() if c <= 0),
            excluded=None,
        )
        for path, h in hits_by_file.items()
    }
    return CoverageProfile(files=MappingProxyType(files))


def _int_attr(element: ET.Element, name: str, *, positive: bool) -> int:
    raw = element.get(name)
    if raw is None:
        raise _malformed(f"a <line> element has no {name!r} attribute")
    try:
        value = int(raw)
    except ValueError as exc:
        raise _malformed(f"<line {name}={raw!r}> is not an integer") from exc
    if positive and value <= 0:
        raise _malformed(f"<line {name}={value}> is not positive")
    if not positive and value < 0:
        raise _malformed(f"<line {name}={value}> is negative")
    return value


def _malformed(message: str) -> AssayError:
    return AssayError(
        f"cobertura XML: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
