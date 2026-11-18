"""Direct controls for the pure sub-file candidate locus helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import random

import pytest

from assay import candidate_locus
from assay.candidate_locus import (
    LOCUS_SCHEMA,
    WINDOW_LINES,
    WINDOW_SCHEMA,
    Locus,
    LocusIndex,
    from_record,
    locus_for_site,
    window_key_for_site,
)


OPERATOR = "python:compare-swap"
REPLACEMENT = "b" * 64


def _span(source: bytes, needle: bytes, occurrence: int = 0) -> tuple[int, int]:
    start = -1
    cursor = 0
    for _index in range(occurrence + 1):
        start = source.index(needle, cursor)
        cursor = start + len(needle)
    assert start >= 0
    return start, start + len(needle)


def _segment(source: bytes, start_text: bytes, end_text: bytes) -> bytes:
    start = source.index(start_text)
    end = source.index(end_text, start) + len(end_text)
    return source[start:end]


def _expected_locus_key(
    path: str,
    qualname: str,
    segment_sha256: str,
    rel_start: int,
    rel_end: int,
) -> str:
    fields = (
        LOCUS_SCHEMA,
        path,
        qualname,
        segment_sha256,
        str(rel_start),
        str(rel_end),
        OPERATOR,
        REPLACEMENT,
    )
    return hashlib.sha256("\0".join(fields).encode("utf-8")).hexdigest()


def _expected_window_key(
    path: str,
    window: bytes,
    rel_start: int,
) -> str:
    fields = (
        WINDOW_SCHEMA.encode(),
        path.encode(),
        window,
        str(rel_start).encode(),
        OPERATOR.encode(),
        REPLACEMENT.encode(),
    )
    return hashlib.sha256(b"\0".join(fields)).hexdigest()


def test_locus_hashes_the_exact_segment_and_relative_span():
    source = b"def f():\n    return 1 + 2\n"
    start, end = _span(source, b"1")
    segment = _segment(source, b"def", b"+ 2")
    segment_sha256 = hashlib.sha256(segment).hexdigest()

    locus = locus_for_site("pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT)

    assert locus == Locus(
        key=_expected_locus_key(
            "pkg/mod.py", "f", segment_sha256, start, end
        ),
        qualname="f",
        segment_sha256=segment_sha256,
        rel_start=start,
        rel_end=end,
    )
    assert dataclasses.is_dataclass(Locus)
    with pytest.raises(dataclasses.FrozenInstanceError):
        locus.qualname = "changed"  # type: ignore[misc]


def test_a_function_move_keeps_the_locus_key_for_unchanged_segment_bytes():
    body = b"def f():\n    return 1 + 2\n"
    before = b"prefix = 0\n\n" + body
    after = b"# moved by unrelated code\nother = 3\n\n" + body

    original = locus_for_site(
        "pkg/mod.py", before, *_span(before, b"1"), OPERATOR, REPLACEMENT
    )
    moved = locus_for_site(
        "pkg/mod.py", after, *_span(after, b"1"), OPERATOR, REPLACEMENT
    )

    assert original is not None
    assert moved is not None
    assert moved.key == original.key
    assert moved.rel_start == original.rel_start
    assert moved.rel_end == original.rel_end


def test_an_edit_elsewhere_in_the_file_keeps_the_locus_key():
    source = b"def f():\n    return 1 + 2\n"
    edited = source + b"\ndef g():\n    return 3\n"

    original = locus_for_site(
        "pkg/mod.py", source, *_span(source, b"1"), OPERATOR, REPLACEMENT
    )
    elsewhere = locus_for_site(
        "pkg/mod.py", edited, *_span(edited, b"1"), OPERATOR, REPLACEMENT
    )

    assert original is not None
    assert elsewhere is not None
    assert elsewhere.key == original.key


@pytest.mark.parametrize(
    "edited",
    [
        b"def f():\n    return TARGET + 1\n",
        b"def f():\n    # comment\n    return TARGET\n",
        b"def f():\n\treturn TARGET\n",
    ],
)
def test_any_edit_inside_the_segment_changes_the_locus_key(edited: bytes):
    source = b"def f():\n    return TARGET\n"
    original = locus_for_site(
        "pkg/mod.py", source, *_span(source, b"TARGET"), OPERATOR, REPLACEMENT
    )
    changed = locus_for_site(
        "pkg/mod.py", edited, *_span(edited, b"TARGET"), OPERATOR, REPLACEMENT
    )

    assert original is not None
    assert changed is not None
    assert changed.key != original.key


def test_comment_after_the_last_statement_is_outside_the_segment():
    source = b"def f():\n    return TARGET\n"
    trailing_comment = b"def f():\n    return TARGET\n    # after the last statement\n"

    original = locus_for_site(
        "pkg/mod.py", source, *_span(source, b"TARGET"), OPERATOR, REPLACEMENT
    )
    after = locus_for_site(
        "pkg/mod.py",
        trailing_comment,
        *_span(trailing_comment, b"TARGET"),
        OPERATOR,
        REPLACEMENT,
    )

    assert original is not None
    assert after is not None
    assert after.key == original.key
    assert after.rel_start == original.rel_start
    assert after.rel_end == original.rel_end


def test_comment_before_the_last_statement_is_inside_the_segment():
    source = b"def f():\n    return TARGET\n"
    comment = b"def f():\n    # before the last statement\n    return TARGET\n"

    original = locus_for_site(
        "pkg/mod.py", source, *_span(source, b"TARGET"), OPERATOR, REPLACEMENT
    )
    changed = locus_for_site(
        "pkg/mod.py", comment, *_span(comment, b"TARGET"), OPERATOR, REPLACEMENT
    )

    assert original is not None
    assert changed is not None
    assert changed.key != original.key


def test_a_site_inside_a_nested_def_maps_to_the_outermost_def():
    source = b"def outer():\n    def inner():\n        return 1\n    return inner\n"
    start, end = _span(source, b"1")
    segment = _segment(source, b"def outer", b"return inner")
    locus = locus_for_site("pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT)

    assert locus is not None
    assert locus.qualname == "outer"
    assert locus.segment_sha256 == hashlib.sha256(segment).hexdigest()


@pytest.mark.parametrize(
    "source,end_text",
    [
        (
            b"def outer():\n"
            b"    def inner():\n"
            b"        return 1\n",
            b"return 1",
        ),
        (
            b"def outer():\n"
            b"    class Inner:\n"
            b"        value = 1\n",
            b"value = 1",
        ),
        (
            b"def outer():\n"
            b"    with context():\n"
            b"        value = 1\n",
            b"value = 1",
        ),
    ],
)
def test_compound_tail_still_ends_at_the_outer_def_end_lineno(
    source: bytes, end_text: bytes
):
    start, end = _span(source, b"1")
    segment = _segment(source, b"def outer", end_text)
    locus = locus_for_site("pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT)

    assert locus is not None
    assert locus.qualname == "outer"
    assert locus.segment_sha256 == hashlib.sha256(segment).hexdigest()


def test_decorators_are_part_of_the_segment_and_a_decorator_edit_rekeys_it():
    source = b"@decorate\ndef f():\n    return 1\n"
    edited = b"@other\ndef f():\n    return 1\n"
    locus = locus_for_site(
        "pkg/mod.py", source, *_span(source, b"1"), OPERATOR, REPLACEMENT
    )
    rekeyed = locus_for_site(
        "pkg/mod.py", edited, *_span(edited, b"1"), OPERATOR, REPLACEMENT
    )

    assert locus is not None
    assert locus.rel_start == _span(source, b"1")[0]
    assert rekeyed is not None
    assert rekeyed.key != locus.key


def test_multiline_parenthesized_decorator_starts_at_the_at_token():
    source = b"@(\n    decorate\n)\ndef f():\n    return 1\n"
    start, end = _span(source, b"1")
    segment = _segment(source, b"@(", b"return 1")
    locus = locus_for_site("pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT)

    assert locus is not None
    assert locus.rel_start == start - source.index(b"@(")
    assert locus.segment_sha256 == hashlib.sha256(segment).hexdigest()


def test_comments_between_decorator_lines_are_inside_the_segment():
    source = b"@a  # first decorator\n@b\ndef f():\n    return 1\n"
    edited = b"@a  # edited decorator\n@b\ndef f():\n    return 1\n"
    start, end = _span(source, b"1")
    segment = _segment(source, b"@a", b"return 1")
    locus = locus_for_site("pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT)
    rekeyed = locus_for_site(
        "pkg/mod.py", edited, *_span(edited, b"1"), OPERATOR, REPLACEMENT
    )

    assert locus is not None
    assert locus.rel_start == start - source.index(b"@a")
    assert locus.segment_sha256 == hashlib.sha256(segment).hexdigest()
    assert rekeyed is not None
    assert rekeyed.key != locus.key


def test_async_def_is_a_outermost_segment():
    source = b"async def f():\n    return 1\n"
    start, end = _span(source, b"1")
    segment = _segment(source, b"async def", b"return 1")
    locus = locus_for_site("pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT)

    assert locus is not None
    assert locus.qualname == "f"
    assert locus.rel_start == start
    assert locus.segment_sha256 == hashlib.sha256(segment).hexdigest()


def test_method_qualname_carries_nested_class_names():
    source = (
        b"class Outer:\n"
        b"    class Inner:\n"
        b"        @decorate\n"
        b"        def method(self):\n"
        b"            return 1\n"
    )
    start, end = _span(source, b"1")
    locus = locus_for_site("pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT)

    assert locus is not None
    assert locus.qualname == "Outer.Inner.method"
    assert locus.rel_start == start - source.index(b"@decorate")


@pytest.mark.parametrize(
    "source,needle",
    [
        (b"class C:\n    value = 1\n", b"1"),
        (b"VALUE = 1\n", b"1"),
        (b"function = lambda: 1\n", b"1"),
        (b"@decorate\nclass C:\n    pass\n", b"decorate"),
    ],
)
def test_sites_without_an_enclosing_def_have_no_locus(source: bytes, needle: bytes):
    start, end = _span(source, needle)

    assert locus_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    ) is None


def test_a_site_crossing_the_segment_boundary_has_no_locus():
    source = b"def f():\n    return 1\n\nVALUE = 2\n"
    start = source.index(b"return")
    end = source.index(b"2") + 1

    assert locus_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    ) is None


@pytest.mark.parametrize(
    "source",
    [
        b"def broken(:\n    return 1\n",
        b"VALUE = 1\0\n",
        b"VALUE = \xff\n",
    ],
)
def test_parse_and_encoding_failures_have_no_locus_or_window(source: bytes):
    start, end = 0, 1

    assert locus_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    ) is None
    assert (
        window_key_for_site(
            "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
        )
        is None
    )


@pytest.mark.parametrize(
    "source",
    [
        b"def f():\r    return 1\r",
        b"@decorate\rdef f():\r    return 1\r",
    ],
)
def test_bare_cr_sources_have_no_locus_or_window(source: bytes):
    start, end = _span(source, b"1")

    assert locus_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    ) is None
    assert (
        window_key_for_site(
            "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
        )
        is None
    )


def test_crlf_sources_still_have_a_locus_and_window():
    source = b"def f():\r\n    return 1\r\n"
    start, end = _span(source, b"1")
    segment = _segment(source, b"def", b"return 1")

    locus = locus_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    )
    window = window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    )

    assert locus is not None
    assert locus.qualname == "f"
    assert locus.segment_sha256 == hashlib.sha256(segment).hexdigest()
    assert window == _expected_window_key(
        "pkg/mod.py", segment, start - source.index(b"def")
    )


@pytest.mark.parametrize(
    "source",
    [
        b"def f():\n    return 1\r",
        b"def f():\r\r\n    return 1\r\n",
    ],
)
def test_bare_cr_edges_have_no_locus_or_window(source: bytes):
    start, end = _span(source, b"1")

    assert locus_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    ) is None
    assert (
        window_key_for_site(
            "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
        )
        is None
    )


def test_hostile_byte_input_never_raises():
    rng = random.Random(0)
    random_sources = [rng.randbytes(rng.randrange(0, 65)) for _ in range(64)]
    seeds = [
        b"def f():\n    return 1\n",
        b"def f():\r\n    return 1\r\n",
        b"def f():\r    return 1\r",
        b"@decorate\ndef f():\n    return 1\n",
        b"class C:\n    @decorate\n    def f(self):\n        return 1\n",
        b"VALUE = 1\0\n",
        b"VALUE = \xff\n",
        b"if True:\n\treturn 1\n",
    ]
    truncated_sources = [
        seed[:cutoff] for seed in seeds for cutoff in range(0, len(seed), 7)
    ]
    sources = random_sources + truncated_sources

    assert len(sources) <= 200
    for path in ("pkg/mod.py", "pkg/\ud800mod.py"):
        for source in sources:
            if len(source) > 1:
                start = rng.randrange(len(source) - 1)
                end = rng.randrange(start + 1, len(source) + 1)
            else:
                start, end = 0, 1
            locus = locus_for_site(
                path, source, start, end, OPERATOR, REPLACEMENT
            )
            window = window_key_for_site(
                path, source, start, end, OPERATOR, REPLACEMENT
            )
            assert locus is None or isinstance(locus, Locus)
            assert window is None or isinstance(window, str)


def test_duplicate_qualname_and_segment_sha_makes_both_sites_fall_back():
    source = (
        b"if condition:\n"
        b"    def duplicate():\n"
        b"        return 1\n"
        b"else:\n"
        b"    def duplicate():\n"
        b"        return 1\n"
    )
    first = _span(source, b"1", 0)
    second = _span(source, b"1", 1)

    assert locus_for_site(
        "pkg/mod.py", source, *first, OPERATOR, REPLACEMENT
    ) is None
    assert locus_for_site(
        "pkg/mod.py", source, *second, OPERATOR, REPLACEMENT
    ) is None
    assert (
        window_key_for_site(
            "pkg/mod.py", source, *first, OPERATOR, REPLACEMENT
        )
        is not None
    )


def test_locus_record_round_trip_is_exact():
    locus = Locus(
        key="a" * 64,
        qualname="Outer.method",
        segment_sha256="c" * 64,
        rel_start=3,
        rel_end=9,
    )

    record = locus.to_record()

    assert set(record) == {
        "schema",
        "key",
        "qualname",
        "segment_sha256",
        "rel_start",
        "rel_end",
    }
    assert from_record(record) == locus


GOOD_RECORD = {
    "schema": LOCUS_SCHEMA,
    "key": "a" * 64,
    "qualname": "Outer.method",
    "segment_sha256": "c" * 64,
    "rel_start": 3,
    "rel_end": 9,
}


def _without(key: str) -> dict[str, object]:
    value = dict(GOOD_RECORD)
    del value[key]
    return value


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {**GOOD_RECORD, "extra": 1},
        *[_without(key) for key in GOOD_RECORD],
        {**GOOD_RECORD, "schema": "assay-locus/2"},
        {**GOOD_RECORD, "key": None},
        {**GOOD_RECORD, "key": "a" * 63},
        {**GOOD_RECORD, "key": "A" * 64},
        {**GOOD_RECORD, "key": "z" * 64},
        {**GOOD_RECORD, "qualname": None},
        {**GOOD_RECORD, "qualname": ""},
        {**GOOD_RECORD, "qualname": "Outer\0method"},
        {**GOOD_RECORD, "schema": "\ud800"},
        {**GOOD_RECORD, "key": "\ud800"},
        {**GOOD_RECORD, "qualname": "\ud800"},
        {**GOOD_RECORD, "segment_sha256": "\ud800"},
        {**GOOD_RECORD, "segment_sha256": None},
        {**GOOD_RECORD, "segment_sha256": "c" * 63},
        {**GOOD_RECORD, "segment_sha256": "C" * 64},
        {**GOOD_RECORD, "segment_sha256": "z" * 64},
        {**GOOD_RECORD, "rel_start": True},
        {**GOOD_RECORD, "rel_start": "3"},
        {**GOOD_RECORD, "rel_start": -1},
        {**GOOD_RECORD, "rel_end": False},
        {**GOOD_RECORD, "rel_end": "9"},
        {**GOOD_RECORD, "rel_end": 3},
        {**GOOD_RECORD, "rel_end": 2},
    ],
)
def test_every_malformed_record_shape_is_refused(value: object):
    assert from_record(value) is None


@pytest.mark.parametrize(
    "change",
    [
        {"path": ""},
        {"path": "pkg/\0mod.py"},
        {"path": "\ud800"},
        {"operator": ""},
        {"operator": "op\0"},
        {"operator": "\ud800"},
        {"replacement_sha256": "B" * 64},
        {"replacement_sha256": "b" * 63},
        {"replacement_sha256": None},
        {"source": bytearray(b"x = 1\n")},
        {"start_byte": True},
        {"end_byte": False},
        {"start_byte": -1},
        {"end_byte": 0},
        {"start_byte": "0"},
        {"start_byte": 10},
    ],
)
def test_locus_and_window_refuse_unusable_inputs(change: dict[str, object]):
    source = b"x = 1\n"
    start, end = _span(source, b"1")
    arguments: dict[str, object] = {
        "path": "pkg/mod.py",
        "source": source,
        "start_byte": start,
        "end_byte": end,
        "operator": OPERATOR,
        "replacement_sha256": REPLACEMENT,
    }
    arguments.update(change)
    public = {
        key: value
        for key, value in arguments.items()
        if key != "replacement_sha256"
    }
    replacement = arguments["replacement_sha256"]

    assert locus_for_site(
        **public, replacement_sha256=replacement
    ) is None  # type: ignore[arg-type]
    assert (
        window_key_for_site(
            **public, replacement_sha256=replacement
        )
        is None
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("lines", [True, -1, "3"])
def test_window_refuses_a_bad_window_width(lines: object):
    source = b"VALUE = 1\n"
    start, end = _span(source, b"1")

    assert (
        window_key_for_site(
            "pkg/mod.py",
            source,
            start,
            end,
            OPERATOR,
            REPLACEMENT,
            lines,  # type: ignore[arg-type]
        )
        is None
    )


def test_window_key_is_clipped_to_the_enclosing_def():
    source = (
        b"before = 0\n"
        b"def f():\n"
        b"    return 1\n"
        b"after = 2\n"
    )
    start, end = _span(source, b"1")
    segment = _segment(source, b"def", b"return 1")

    key = window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    )

    assert key == _expected_window_key("pkg/mod.py", segment, start - source.index(b"def"))


def test_window_key_is_clipped_to_the_file_when_no_def_encloses_the_site():
    source = (
        b"one = 1\n"
        b"two = 2\n"
        b"three = 3\n"
        b"four = 4\n"
        b"five = 5\n"
        b"six = 6\n"
        b"seven = 7\n"
        b"eight = 8\n"
    )
    start, end = _span(source, b"4")
    window_start = source.index(b"one")
    window_end = source.index(b"eight")
    window = source[window_start:window_end]

    key = window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    )

    assert key == _expected_window_key("pkg/mod.py", window, start - window_start)


def test_window_key_is_stable_outside_the_window_and_changes_inside_it():
    source = (
        b"one = 1\ntwo = 2\nthree = 3\nfour = 4\n"
        b"five = 5\nsix = 6\nseven = 7\neight = 8\nnine = 9\n"
    )
    outside = source.replace(b"eight = 8", b"outside = 8")
    inside = source.replace(b"five = 5", b"five = 50")
    start, end = _span(source, b"4")
    outside_start, outside_end = _span(outside, b"4")
    inside_start, inside_end = _span(inside, b"4")

    original = window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    )
    stable = window_key_for_site(
        "pkg/mod.py", outside, outside_start, outside_end, OPERATOR, REPLACEMENT
    )
    changed = window_key_for_site(
        "pkg/mod.py", inside, inside_start, inside_end, OPERATOR, REPLACEMENT
    )

    assert stable == original
    assert changed != original


def test_window_key_handles_the_last_physical_line_without_a_newline():
    source = b"one = 1\nVALUE = 2"
    start, end = _span(source, b"2")

    key = window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT, lines=1
    )

    assert key == _expected_window_key("pkg/mod.py", source, start)


def test_window_key_for_a_nested_site_is_clipped_to_the_outermost_def():
    source = (
        b"before = 0\n"
        b"def outer():\n"
        b"    def inner():\n"
        b"        return 1\n"
        b"    return inner\n"
        b"after = 2\n"
    )
    start, end = _span(source, b"1")
    segment = _segment(source, b"def outer", b"return inner")

    key = window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    )

    assert key == _expected_window_key(
        "pkg/mod.py", segment, start - source.index(b"def outer")
    )


def test_locus_index_deduplicates_the_same_candidate_before_checking_ambiguity():
    locus_a = "a" * 64
    window_a = "b" * 64
    payload = {"outcome": "killed"}
    entries = [
        ("candidate-1", locus_a, window_a, payload),
        ("candidate-1", locus_a, window_a, {"outcome": "killed"}),
    ]

    index = LocusIndex(entries)

    assert index.by_locus(locus_a) == payload
    assert index.by_window(window_a) == (payload,)
    assert index.ambiguous_loci() == frozenset()


def test_locus_index_marks_only_different_candidate_ids_as_ambiguous():
    shared_locus = "a" * 64
    entries = [
        ("candidate-1", shared_locus, "b" * 64, "first"),
        ("candidate-2", shared_locus, "c" * 64, "second"),
    ]

    index = LocusIndex(entries)

    assert index.by_locus(shared_locus) is None
    assert index.ambiguous_loci() == frozenset({shared_locus})
    assert index.by_window("b" * 64) == ("first",)
    assert index.by_window("c" * 64) == ("second",)


def test_locus_index_window_payloads_are_in_candidate_id_order():
    window = "d" * 64
    first = [("z-candidate", None, window, "z"), ("a-candidate", None, window, "a")]
    second = list(reversed(first))

    assert LocusIndex(first).by_window(window) == ("a", "z")
    assert LocusIndex(second).by_window(window) == ("a", "z")


def test_locus_index_conflicting_duplicate_ids_choose_deterministically():
    locus_a = "a" * 64
    locus_b = "b" * 64
    first = [
        ("candidate-1", locus_b, None, "z-payload"),
        ("candidate-1", locus_a, None, "a-payload"),
    ]
    second = list(reversed(first))

    left = LocusIndex(first)
    right = LocusIndex(second)

    assert left.by_locus(locus_a) == right.by_locus(locus_a) == "a-payload"
    assert left.by_locus(locus_b) is None
    assert right.by_locus(locus_b) is None


@pytest.mark.parametrize(
    "key",
    ["z" * 63, "Z" * 64, "not-a-key"],
)
def test_locus_index_refuses_unshaped_lookups(key: str):
    index = LocusIndex([])

    assert index.by_locus(key) is None
    assert index.by_window(key) == ()


@pytest.mark.parametrize(
    "entry",
    [
        ("", None, None, "payload"),
        ("candidate\0id", None, None, "payload"),
        ("candidate-1", "A" * 64, None, "payload"),
        ("candidate-1", None, "z" * 63, "payload"),
    ],
)
def test_locus_index_refuses_invalid_identity_fields(
    entry: tuple[str, str | None, str | None, object],
):
    with pytest.raises(ValueError):
        LocusIndex([entry])


def _index_results(
    path: str,
    source: bytes,
    start: int,
    end: int,
) -> tuple[Locus | None, str | None]:
    return (
        locus_for_site(path, source, start, end, OPERATOR, REPLACEMENT),
        window_key_for_site(path, source, start, end, OPERATOR, REPLACEMENT),
    )


def test_cache_is_cold_warm_and_source_digest_safe():
    candidate_locus._cached_source_index.cache_clear()
    path = "pkg/mod.py"
    first_source = b"def f():\n    return 1\n"
    second_source = b"def f():\n    return 2\n"
    first_start, first_end = _span(first_source, b"1")
    second_start, second_end = _span(second_source, b"2")

    cold = _index_results(path, first_source, first_start, first_end)
    warm = _index_results(path, first_source, first_start, first_end)
    changed = _index_results(path, second_source, second_start, second_end)

    assert cold == warm
    assert changed != cold
    assert candidate_locus._cached_source_index.cache_info().misses == 2
    assert candidate_locus._cached_source_index.cache_info().hits == 4
    assert changed[0] is not None
    assert cold[0] is not None
    assert changed[0].key != cold[0].key


def test_window_default_is_three_lines():
    source = b"def f():\n    return 1\n"
    start, end = _span(source, b"1")

    assert WINDOW_LINES == 3
    assert window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT
    ) == window_key_for_site(
        "pkg/mod.py", source, start, end, OPERATOR, REPLACEMENT, lines=3
    )
