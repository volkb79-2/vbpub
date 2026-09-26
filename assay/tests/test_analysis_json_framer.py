"""The bounded JSONL framer must distinguish complete, torn, and bad records.

These are behavioral cases for the progress-report scanner. In particular,
the scanner sees only oversized final records, where accepting malformed JSON
as a complete record would turn an evidence error into a running report.
"""

from __future__ import annotations

import json

import pytest

from assay import analysis


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (b"{}", "complete"),
        (b"[]", "complete"),
        (b'"text"', "complete"),
        (b"true", "complete"),
        (b"false", "complete"),
        (b"null", "complete"),
        (b'{"items":[null,true,false,0,0.1,0e1,-0,-12,1.2,1.22,1.2e+3,1e2,1e22,1E+2,1e-2]}',
         "complete"),
        (json.dumps({"text": 'quote " slash / backslash \\ tab\t newline\n €'}).encode(),
         "complete"),
        (b" \t\r\n[1, 2]\r\n", "complete"),
        (b"", "incomplete"),
        (b"   ", "incomplete"),
        (b"-", "incomplete"),
        (b"1.", "incomplete"),
        (b"1e", "incomplete"),
        (b"1e+", "incomplete"),
        (b'"open', "incomplete"),
        (b'"open\\', "incomplete"),
        (br'"open\u12', "incomplete"),
        (b"{", "incomplete"),
        (b"[1,", "incomplete"),
        (b"tru", "incomplete"),
        (b"x", "invalid"),
        (b"true false", "invalid"),
        (b"truX", "invalid"),
        (b'"bad\\q"', "invalid"),
        (br'"bad\u12xz"', "invalid"),
        (b'"bad\x01text"', "invalid"),
        (b"-x", "invalid"),
        (b"01", "invalid"),
        (b"0x", "invalid"),
        (b"12x", "invalid"),
        (b"1.x", "invalid"),
        (b"1ex", "invalid"),
        (b"1e+x", "invalid"),
        (b"1e3x", "invalid"),
        (b"1.2x", "invalid"),
        (b'{"key" 1}', "invalid"),
        (b'{"key":}', "invalid"),
        (b'{"key":1,}', "invalid"),
        (b"[1 2]", "invalid"),
        (b"[1,]", "invalid"),
        (b"[}", "invalid"),
        (b'"\\xff"', "invalid"),
    ],
    ids=[
        "empty-object", "empty-array", "string", "true", "false", "null",
        "all-number-states-and-delimiters", "escaped-string", "whitespace",
        "empty", "whitespace-only", "minus", "decimal-point", "exponent",
        "exponent-sign", "open-string", "open-escape", "open-unicode-escape",
        "open-object", "open-array", "open-literal", "bad-value-start",
        "trailing-root-value", "bad-literal", "bad-escape", "bad-unicode-escape",
        "control-in-string", "minus-followed-by-invalid", "leading-zero",
        "zero-followed-by-invalid", "integer-followed-by-invalid",
        "fraction-followed-by-invalid", "exponent-followed-by-invalid",
        "signed-exponent-followed-by-invalid", "exponent-digits-followed-by-invalid",
        "fraction-followed-by-invalid", "missing-colon", "missing-value",
        "object-trailing-comma", "missing-array-comma", "array-trailing-comma",
        "wrong-container-close", "escaped-hex-text-is-not-a-utf8-error",
    ],
)
def test_bounded_report_framer_classifies_json_records(record: bytes, expected: str):
    framer = analysis._ReportJSONFramer()
    framer.feed(record)

    assert framer.finish() == expected


def test_bounded_report_framer_keeps_utf8_and_json_escapes_across_chunks():
    record = '"price € and \\u20ac"'.encode("utf-8")
    euro = record.index("€".encode("utf-8"))
    framer = analysis._ReportJSONFramer()
    framer.feed(record[: euro + 1])
    framer.feed(record[euro + 1 : euro + 2])
    framer.feed(record[euro + 2 :])

    assert framer.finish() == "complete"


@pytest.mark.parametrize("record", [b'"\xff"', b'"\xe2\x82'])
def test_bounded_report_framer_refuses_invalid_or_truncated_utf8(record: bytes):
    framer = analysis._ReportJSONFramer()
    framer.feed(record)

    assert framer.finish() == "invalid"


def test_bounded_report_framer_stops_after_terminal_state(monkeypatch):
    framer = analysis._ReportJSONFramer()
    framer.feed(b"x")
    framer.feed(b'{"this": "must not be parsed"}')
    framer._char("{")

    assert framer.finish() == "invalid"

    monkeypatch.setattr(analysis, "_REPORT_JSON_DEPTH_LIMIT", 1)
    deep = analysis._ReportJSONFramer()
    deep.feed(b"[[")
    deep.feed(b"0]]")

    assert deep.finish() == "indeterminate"


@pytest.mark.parametrize(
    ("record", "expected"),
    [(b"1", "complete"), (b"1.2", "complete"), (b"1e2", "complete")],
)
def test_bounded_report_framer_finishes_terminal_number_states(record: bytes, expected: str):
    framer = analysis._ReportJSONFramer()
    framer.feed(record)

    assert framer.finish() == expected


def test_bounded_report_framer_rejects_corrupt_internal_container_closures():
    empty = analysis._ReportJSONFramer()
    empty._close_container("}")
    assert empty.finish() == "invalid"

    wrong_kind = analysis._ReportJSONFramer()
    wrong_kind.stack.append({"kind": "object", "state": "value"})
    wrong_kind._close_container("}")
    assert wrong_kind.finish() == "invalid"


def test_bounded_report_framer_rejects_an_unknown_internal_number_state():
    framer = analysis._ReportJSONFramer()
    framer.mode = "number"
    framer.number_state = "unknown"
    framer.feed(b"1")

    assert framer.finish() == "invalid"
