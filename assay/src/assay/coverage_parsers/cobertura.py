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

**Branch capability (wave-1 §3.2/§3.3, A-265): per-line detail decides, the
root count cross-checks.** Any ``<line branch="true" …>`` anywhere ⇒
``"reported"`` for every file; none anywhere ⇒ ``"unavailable"``. The root
``branches-valid`` attribute, when PRESENT, is a cross-check on that same
four-row A-265 table (never on whether it happens to equal the derived
number, which is a SEPARATE, additional cross-check): ``> 0`` while no
per-line detail exists, or ``0`` while detail exists, is refused; an ABSENT
attribute agrees with either state, exactly as an absent
``meta.branch_coverage`` does for coverage.py JSON. Per line,
``condition-coverage="P% (C/T)"`` is parsed for ``(C/T)`` only -- ``P`` is
never verified (§3.3: unwitnessed rounding grammar, and reading it would be
inventing a tolerance rule). **Multiple ``<class>`` elements may name the
same file**; for branch data (unlike the existing executed-wins line merge,
which is untouched) there is no safe merge, so a file+line reported twice
must carry IDENTICAL ``(C, T)`` or the artifact is refused (§3.1a's identity
discipline applied to Cobertura's own shape: the per-line ``(C, T)`` pair
already IS the atomic unit here, so "before aggregation" means "before a
second ``<class>`` element's value silently overwrites the first").
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import BranchCoverage, CoverageProfile, FileCoverage

#: ``condition-coverage="50% (1/2)"`` -- find "(C/T)" anywhere in the
#: attribute text; the leading percentage is deliberately never parsed.
_CONDITION_COVERAGE_RE = re.compile(r"\((\d+)/(\d+)\)")


def sniff(text: str) -> bool:
    """The text contains a ``<coverage`` element open tag (DESIGN-GUIDE §5's
    literal signature: ``<coverage`` → cobertura)."""
    return "<coverage" in text


def parse(text: str, *, producer: str | None) -> CoverageProfile:
    # `producer` is part of the uniform parser protocol (package docstring,
    # B045) and is deliberately unread here: `cobertura`'s producer
    # vocabulary is still CLOSED AND EMPTY (no speculative names,
    # DESIGN-GUIDE §5), so no lane can declare one to branch on.
    del producer
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise _malformed(f"not well-formed XML: {exc}") from exc
    if root.tag != "coverage":
        raise _malformed(f"root element is <{root.tag}>, expected <coverage>")

    hits_by_file: dict[str, dict[int, int]] = {}
    branches_by_file: dict[str, dict[int, tuple[int, int]]] = {}
    any_branch_detail = False
    for class_el in root.iter("class"):
        filename = class_el.get("filename")
        if not filename:
            raise _malformed("a <class> element has no 'filename' attribute")
        hits = hits_by_file.setdefault(filename, {})
        branches = branches_by_file.setdefault(filename, {})
        lines_el = class_el.find("lines")
        if lines_el is None:
            continue
        for line_el in lines_el.findall("line"):
            number = _int_attr(line_el, "number", positive=True)
            count = _int_attr(line_el, "hits", positive=False)
            # executed wins if the same file's line is reported by more than
            # one <class> element (see module docstring).
            hits[number] = max(count, hits.get(number, 0))
            if line_el.get("branch") == "true":
                any_branch_detail = True
                pair = _parse_condition_coverage(line_el, filename, number)
                # §3.1a's identity discipline: a second <class> reporting the
                # same file+line must agree, or the artifact cannot be read
                # without inventing a merge rule (module docstring) -- caught
                # HERE, before this pair overwrites whatever the first
                # <class> already recorded.
                if number in branches and branches[number] != pair:
                    raise _malformed(
                        f"file {filename!r} line {number} is reported by "
                        f"two <class> elements with different branch "
                        f"coverage: {branches[number]} vs {pair}"
                    )
                branches[number] = pair

    _check_root_branch_counts(root, branches_by_file, any_branch_detail)

    files = {
        path: _build_file_coverage(
            path, hits, branches_by_file[path], any_branch_detail
        )
        for path, hits in hits_by_file.items()
    }
    return CoverageProfile(files=MappingProxyType(files))


def _parse_condition_coverage(
    line_el: ET.Element, filename: str, number: int
) -> tuple[int, int]:
    raw = line_el.get("condition-coverage")
    if raw is None:
        raise _malformed(
            f"file {filename!r} line {number} has branch=\"true\" but no "
            f"'condition-coverage' attribute"
        )
    match = _CONDITION_COVERAGE_RE.search(raw)
    if match is None:
        raise _malformed(
            f"file {filename!r} line {number}: condition-coverage {raw!r} "
            f"has no parsable '(C/T)'"
        )
    return int(match.group(1)), int(match.group(2))


def _check_root_branch_counts(
    root: ET.Element,
    branches_by_file: dict[str, dict[int, tuple[int, int]]],
    any_branch_detail: bool,
) -> None:
    valid_attr = root.get("branches-valid")
    if valid_attr is not None:
        stated_valid = _parse_root_branch_count(valid_attr, "branches-valid")
        if (stated_valid > 0) != any_branch_detail:
            raise _malformed(
                f"root branches-valid={stated_valid} disagrees with whether "
                f"any <line branch=\"true\"> detail is present in the "
                f"document"
            )
        derived_valid = sum(
            total for lines in branches_by_file.values() for _c, total in lines.values()
        )
        if stated_valid != derived_valid:
            raise _malformed(
                f"root branches-valid={stated_valid} does not match the "
                f"derived total {derived_valid}"
            )
    covered_attr = root.get("branches-covered")
    if covered_attr is not None:
        stated_covered = _parse_root_branch_count(covered_attr, "branches-covered")
        derived_covered = sum(
            covered
            for lines in branches_by_file.values()
            for covered, _t in lines.values()
        )
        if stated_covered != derived_covered:
            raise _malformed(
                f"root branches-covered={stated_covered} does not match the "
                f"derived covered count {derived_covered}"
            )


def _parse_root_branch_count(raw: str, name: str) -> int:
    try:
        return int(raw)
    except ValueError as exc:
        raise _malformed(f"root {name}={raw!r} is not an integer") from exc


def _build_file_coverage(
    filename: str,
    hits: dict[int, int],
    branch_lines: dict[int, tuple[int, int]],
    has_branch_detail: bool,
) -> FileCoverage:
    try:
        branches = BranchCoverage(by_line=branch_lines) if has_branch_detail else None
        return FileCoverage(
            executed=frozenset(n for n, c in hits.items() if c > 0),
            missing=frozenset(n for n, c in hits.items() if c <= 0),
            excluded=None,
            branches=branches,
        )
    except ValueError as exc:
        raise _malformed(f"{filename!r}: {exc}") from exc


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
