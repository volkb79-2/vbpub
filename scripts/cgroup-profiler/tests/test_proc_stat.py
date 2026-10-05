from __future__ import annotations

from lib import proc_stat


def test_split_after_comm_uses_last_parenthesis_after_comm():
    text = "42 (worker ) with ) parens) S 7 8 9"

    fields = proc_stat.split_after_comm(text)

    assert fields == ("S", "7", "8", "9")


def test_split_after_comm_accepts_a_closing_delimiter_at_start():
    assert proc_stat.split_after_comm(") S 7") == ("S", "7")


def test_split_after_comm_refuses_a_missing_closing_delimiter():
    assert proc_stat.split_after_comm("42 (worker") is None
