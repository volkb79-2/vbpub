from __future__ import annotations

from lib import proc_stat


def test_split_after_comm_uses_last_parenthesis_after_comm():
    text = "42 (worker ) with ) parens) S 7 8 9"

    parsed = proc_stat.split_after_comm(text)

    assert parsed is not None
    close, fields = parsed
    assert text[close] == ")"
    assert fields == ("S", "7", "8", "9")


def test_split_after_comm_preserves_a_delimiter_at_offset_zero():
    assert proc_stat.split_after_comm(") S 7") == (0, ("S", "7"))


def test_split_after_comm_refuses_a_missing_closing_delimiter():
    assert proc_stat.split_after_comm("42 (worker") is None
