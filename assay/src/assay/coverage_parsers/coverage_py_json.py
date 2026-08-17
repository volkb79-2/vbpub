"""coverage.py's own JSON report (``coverage json`` / ``pytest
--cov-report=json``).

Format (the part this parser reads; ``totals`` is ignored — this registry
emits a normalized per-file model, not a re-serialization of the
artifact)::

    {
      "meta": {"branch_coverage": true},
      "files": {
        "<path>": {
          "executed_lines": [1, 2, 3],
          "missing_lines": [4, 5],
          "excluded_lines": [6],
          "executed_branches": [[1, 2]],
          "missing_branches": [[1, 3]],
          "summary": {"num_branches": 2, "covered_branches": 1}
        }
      }
    }

coverage.py is the ONE format in this registry that can express an exclusion
(``# pragma: no cover``) as a first-class, dedicated field distinct from
"missing" — so ``FileCoverage.excluded`` here is ALWAYS a ``frozenset``
(possibly empty), never ``None`` (DESIGN-GUIDE §11, A-008). Cross-referenced
against ``topos/tools/coverage_gate.py``'s ``_validate_cov_record`` (handoff
item 4) for the shape of "malformed": both ``executed_lines`` and
``missing_lines`` must be lists of ints, and this parser extends the same
check to ``excluded_lines`` for the same reason topos gives — coverage.py
guarantees the shape, so a deviation means the JSON was tampered with,
misread, or produced by a non-standard tool, and a gate that trusts an
untyped value there risks a silent green (dstdns's own returned
``dict[str, dict]`` shape is exactly what A-092 forbids repeating here).

**Branch capability (wave-1 §3.2/§3.3, A-265) is ARTIFACT-level and DETAIL
decides it.** Detail is any file record carrying ``executed_branches`` or
``missing_branches`` ANYWHERE in the artifact. ``meta.branch_coverage`` is a
CROSS-CHECK, never a veto: present detail with ``meta`` saying ``false`` (or
absent detail with ``meta`` saying ``true``) is refused, but a ``meta`` that
is simply absent — or present without a ``branch_coverage`` key, or with a
non-boolean value there — agrees with either detail state, exactly as an
absent ``meta.branch_coverage`` cannot veto present detail (the false-PASS
hole this rule exists to close). ``dst`` in an ``[src, dst]`` pair is an
opaque identity used ONLY to detect a repeated or contradictory arc before
the counts are aggregated (§3.1a) — never stored, and legitimately negative
(``coverage-py-json.exitarc.json``'s ``[11, -10]``, an arc leaving the
function starting at line 10).
"""

from __future__ import annotations

import json
from types import MappingProxyType

from ..errors import AssayError, Outcome, ReasonCode
from .model import BranchCoverage, CoverageProfile, FileCoverage

_SIGNATURE_KEY = '"files"'


def sniff(text: str) -> bool:
    """A JSON object whose text contains the top-level ``"files"`` key
    (DESIGN-GUIDE §5's literal signature: ``{"files":`` → coverage.py JSON).

    Deliberately a cheap substring/prefix check, not a full ``json.loads`` —
    sniffing answers "does this look like the declared format", parsing
    answers "is it well-formed"; collapsing the two would make an
    unparsable-JSON fixture raise ``FORMAT_MISMATCH`` from a hand-rolled
    parser inside the sniffer instead of the real parser's own
    ``UNREADABLE_ARTIFACT`` path.
    """
    stripped = text.lstrip()
    return stripped.startswith("{") and _SIGNATURE_KEY in text


def parse(text: str) -> CoverageProfile:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _malformed(f"not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise _malformed(f"top level is {type(document).__name__}, expected object")
    files_raw = document.get("files")
    if not isinstance(files_raw, dict):
        raise _malformed(
            f"no top-level 'files' object (got "
            f"{type(files_raw).__name__ if files_raw is not None else 'nothing'})"
        )

    has_branch_detail = any(
        isinstance(record, dict)
        and ("executed_branches" in record or "missing_branches" in record)
        for record in files_raw.values()
    )
    branch_metadata = _branch_metadata(document)
    if branch_metadata is True and not has_branch_detail:
        raise _malformed(
            "meta.branch_coverage is true, but no file record carries "
            "'executed_branches'/'missing_branches' -- the artifact's own "
            "claims about itself disagree"
        )
    if branch_metadata is False and has_branch_detail:
        raise _malformed(
            "meta.branch_coverage is false, but a file record carries "
            "branch arc detail -- the artifact's own claims about itself "
            "disagree"
        )

    files: dict[str, FileCoverage] = {}
    for path, record in files_raw.items():
        files[path] = _parse_record(path, record, has_branch_detail=has_branch_detail)
    return CoverageProfile(files=MappingProxyType(files))


def _branch_metadata(document: dict) -> bool | None:
    """``meta.branch_coverage``, when it is a genuine boolean; ``None``
    (no signal, agrees with either detail state) for an absent ``meta``, an
    absent key, or a present-but-non-boolean value -- ``json.loads`` only
    ever produces a real Python ``bool`` from a JSON ``true``/``false``, so a
    non-boolean here can only come from a hand-tampered artifact this parser
    is not asked to police any harder than that: DETAIL remains authoritative
    regardless (A-265).
    """
    meta = document.get("meta")
    if not isinstance(meta, dict):
        return None
    flag = meta.get("branch_coverage")
    return flag if isinstance(flag, bool) else None


def _parse_record(path: str, record: object, *, has_branch_detail: bool) -> FileCoverage:
    if not isinstance(record, dict):
        raise _malformed(
            f"record for {path!r} is {type(record).__name__}, expected object"
        )
    executed = _int_list(record, path, "executed_lines")
    missing = _int_list(record, path, "missing_lines")
    # excluded_lines is the one field a genuinely minimal coverage.py JSON
    # might omit (a report generated with no exclusion patterns configured
    # can still include it as []; some hand-built fixtures might not) — its
    # ABSENCE is not itself malformed, since it is meaningfully identical to
    # "reports zero exclusions" for a format that CAN express them. Only a
    # present-but-wrongly-typed value is rejected.
    if "excluded_lines" not in record:
        excluded: frozenset[int] = frozenset()
    else:
        excluded = frozenset(_int_list(record, path, "excluded_lines"))
    try:
        branches = _parse_branches(record, path) if has_branch_detail else None
        return FileCoverage(
            executed=frozenset(executed),
            missing=frozenset(missing),
            excluded=excluded,
            branches=branches,
        )
    except ValueError as exc:
        # P15 (finding 4): the common model enforces positive line numbers
        # and pairwise-disjoint buckets in ONE place (model.py) rather than
        # per format; this is the one parser whose input (three independent
        # JSON arrays, now five with the two branch arrays) can actually
        # violate either -- a line claimed both executed and missing
        # simultaneously, or a non-positive line number, both real artifact
        # defects.
        raise _malformed(f"record for {path!r}: {exc}") from exc


def _parse_branches(record: dict, path: str) -> BranchCoverage:
    """This artifact carries branch detail somewhere; build ONE file's
    :class:`BranchCoverage` from its ``executed_branches``/``missing_branches``
    arrays, or an empty one for a branch-free file (§3.2's "every parser
    must emit ``BranchCoverage(by_line={})``, not ``None``, for a
    branch-free file in a branch-tracking artifact").
    """
    has_executed = "executed_branches" in record
    has_missing = "missing_branches" in record
    if has_executed != has_missing:
        raise _malformed(
            f"record for {path!r} carries only one of "
            f"'executed_branches'/'missing_branches' -- likely a coverage.py "
            f"build too old to report per-line arcs"
        )
    executed_pairs = _branch_pairs(record, path, "executed_branches")
    missing_pairs = _branch_pairs(record, path, "missing_branches")
    _check_arc_identity(path, executed_pairs, missing_pairs)

    covered_by_src: dict[int, int] = {}
    total_by_src: dict[int, int] = {}
    for src, _dst in executed_pairs:
        covered_by_src[src] = covered_by_src.get(src, 0) + 1
        total_by_src[src] = total_by_src.get(src, 0) + 1
    for src, _dst in missing_pairs:
        total_by_src[src] = total_by_src.get(src, 0) + 1
    by_line = {
        src: (covered_by_src.get(src, 0), total) for src, total in total_by_src.items()
    }
    _check_branch_summary(record, path, by_line)
    return BranchCoverage(by_line=by_line)


def _branch_pairs(record: dict, path: str, key: str) -> list[tuple[int, int]]:
    """``[src, dst]`` pairs from *key*, or ``[]`` when *key* is absent
    entirely (a legal shape once ``_parse_branches`` has already checked the
    two branch keys are both present or both absent). Only the PAIR SHAPE is
    validated here (a 2-element list of non-bool integers) -- ``src``
    positivity is §3.1's invariant 1, enforced once in the model
    (:class:`~.model.BranchCoverage`) after aggregation, not duplicated here.
    ``dst`` is an opaque integer and is legitimately negative (A1:
    ``coverage-py-json.exitarc.json``'s ``[11, -10]``); it is returned, never
    validated for sign, since only §3.1a's identity check below needs it.
    """
    value = record.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise _malformed(
            f"record for {path!r}: {key!r} is {type(value).__name__}, expected list"
        )
    pairs: list[tuple[int, int]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(isinstance(n, bool) or not isinstance(n, int) for n in item)
        ):
            raise _malformed(
                f"record for {path!r}: {key!r} contains a malformed arc "
                f"{item!r}, expected a 2-element [src, dst] list of integers"
            )
        src, dst = item
        pairs.append((src, dst))
    return pairs


def _check_arc_identity(
    path: str,
    executed_pairs: list[tuple[int, int]],
    missing_pairs: list[tuple[int, int]],
) -> None:
    """§3.1a, BEFORE aggregation: a repeated ``(src, dst)`` within one array,
    or the same ``(src, dst)`` in BOTH arrays, is refused. Aggregating first
    would throw this identity away -- a duplicated covered arc with
    ``covered_branches`` incremented to match passes every totals
    cross-check and is undetectable afterwards (A-265, the independent
    review's own finding).
    """
    executed_set = set(executed_pairs)
    if len(executed_set) != len(executed_pairs):
        raise _malformed(
            f"record for {path!r}: 'executed_branches' repeats an arc identity"
        )
    missing_set = set(missing_pairs)
    if len(missing_set) != len(missing_pairs):
        raise _malformed(
            f"record for {path!r}: 'missing_branches' repeats an arc identity"
        )
    overlap = executed_set & missing_set
    if overlap:
        raise _malformed(
            f"record for {path!r}: arc(s) {sorted(overlap)} appear in both "
            f"'executed_branches' and 'missing_branches' -- an arc cannot "
            f"be simultaneously taken and not taken"
        )


def _check_branch_summary(
    record: dict, path: str, by_line: dict[int, tuple[int, int]]
) -> None:
    """Cross-check ``summary.num_branches``/``summary.covered_branches``
    against the DERIVED totals, when ``summary`` carries them. A ``summary``
    that omits the branch keys is not malformed -- some coverage.py builds
    or hand-built fixtures may not carry them -- so the cross-check is
    simply skipped, per key, when absent.
    """
    summary = record.get("summary")
    if not isinstance(summary, dict):
        return
    derived_total = sum(total for _covered, total in by_line.values())
    derived_covered = sum(covered for covered, _total in by_line.values())
    stated_total = summary.get("num_branches")
    if stated_total is not None and stated_total != derived_total:
        raise _malformed(
            f"record for {path!r}: summary.num_branches={stated_total!r} "
            f"does not match the derived branch total {derived_total}"
        )
    stated_covered = summary.get("covered_branches")
    if stated_covered is not None and stated_covered != derived_covered:
        raise _malformed(
            f"record for {path!r}: summary.covered_branches="
            f"{stated_covered!r} does not match the derived covered arc "
            f"count {derived_covered}"
        )


def _int_list(record: dict, path: str, key: str) -> list[int]:
    value = record.get(key)
    if value is None:
        raise _malformed(f"record for {path!r} is missing {key!r}")
    if not isinstance(value, list):
        raise _malformed(
            f"record for {path!r}: {key!r} is {type(value).__name__}, expected list"
        )
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool):
            raise _malformed(
                f"record for {path!r}: {key!r} contains "
                f"{type(item).__name__} ({item!r}), expected int"
            )
    return value


def _malformed(message: str) -> AssayError:
    return AssayError(
        f"coverage.py JSON: {message}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
