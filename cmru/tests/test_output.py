"""Tests for CMRU terminal-only severity presentation."""
from __future__ import annotations

import io
import itertools
import re

import pytest

from cmru.output import SeverityStream


def _render(chunks):
    target = io.StringIO()
    stream = SeverityStream(target, time_short=False, colour=True)
    for chunk in chunks:
        stream.write(chunk)
    return target.getvalue()


def test_short_time_prefixes_a_split_info_print_without_delaying_the_message():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=True, colour=False)

    stream.write("[INFO] release started")
    stream.write("\n")

    assert re.fullmatch(r"\d{2}:\d{2}:\d{2} \[INFO\] release started\n", target.getvalue())


def test_severity_colour_wraps_only_the_machine_readable_prefix():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=False, colour=True)

    stream.write("[ERROR] release failed\n")

    assert target.getvalue() == "\033[31m[ERROR]\033[0m release failed\n"


def test_plain_partial_progress_is_forwarded_immediately_and_not_decorated():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=True, colour=True)

    stream.write("building 50%")

    assert target.getvalue() == "building 50%"


def test_incomplete_severity_token_is_preserved_on_flush():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=True, colour=True)

    stream.write("[IN")
    stream.flush()

    assert target.getvalue() == "[IN"


@pytest.mark.parametrize(
    "chunks",
    [
        ("[ERROR] first\n[WARN] second\n[INFO] third\n",),
        ("[ERROR] first\n", "[WARN] second\n", "[INFO] third\n"),
        ("[ER", "ROR] first\n[WA", "RN] second\n[IN", "FO] third\n"),
    ],
)
def test_multiline_write_matches_newline_and_mid_token_chunks(chunks):
    message = "".join(chunks)
    expected = (
        "\033[31m[ERROR]\033[0m first\n"
        "\033[33m[WARN]\033[0m second\n"
        "\033[32m[INFO]\033[0m third\n"
    )

    assert _render((message,)) == expected
    assert _render(chunks) == expected


def test_every_chunking_of_a_small_multiline_message_is_equivalent():
    message = "[INFO]\n[WARN]"
    expected = "\033[32m[INFO]\033[0m\n\033[33m[WARN]\033[0m"
    assert _render((message,)) == expected

    for count in range(len(message)):
        for cuts in itertools.combinations(range(1, len(message)), count):
            boundaries = (0, *cuts, len(message))
            chunks = tuple(
                message[start:end]
                for start, end in zip(boundaries, boundaries[1:])
            )
            assert _render(chunks) == expected, chunks


def test_plain_line_does_not_hide_a_following_severity_prefix():
    assert _render(("ordinary\n[WARN] next",)) == (
        "ordinary\n\033[33m[WARN]\033[0m next"
    )


def test_write_returns_input_length_when_a_candidate_precedes_multiline_text():
    target = io.StringIO()
    stream = SeverityStream(target, time_short=False, colour=True)
    stream.write("[WA")
    message = "RN] second\n[ERROR] third"

    assert stream.write(message) == len(message)
    assert target.getvalue() == (
        "\033[33m[WARN]\033[0m second\n"
        "\033[31m[ERROR]\033[0m third"
    )
