"""Tests for CMRU terminal-only severity presentation."""
from __future__ import annotations

import io
import re

from cmru.output import SeverityStream


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
