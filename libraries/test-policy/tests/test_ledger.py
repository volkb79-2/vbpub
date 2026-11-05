"""The flake ledger is a stable, parseable format (docs/FORMATS.md): enforce its grammar."""

import pathlib
import re

import pytest

LEDGER = pathlib.Path(__file__).resolve().parent.parent / "docs" / "FLAKE-LEDGER.md"
WORDS = ("FIXED-ON-BRANCH", "FIXED", "OPEN-HYPOTHESIS", "CANDIDATE", "NOT-A-FLAKE")
REF = re.compile(r"(?:\b[0-9a-f]{9,40}\b|\b[A-Z]{2,}-\d+\b)")


def rows(text=None):
    """Parse the ledger table (the first table, under the `| ID |` header). A table line that is neither the header,
    the separator nor a `| FL-NN |` row is an error, never a silent skip."""
    out = []
    in_table = False
    for line in (LEDGER.read_text(encoding="utf-8") if text is None else text).splitlines():
        if not in_table:
            in_table = line.startswith("| ID |")
            continue
        if not line.startswith("|"):
            break  # end of the ledger table
        if re.match(r"\|[-| ]+\|$", line):
            continue  # the header separator
        if not re.match(r"\| FL-\d+ \|", line):
            raise AssertionError(f"ledger line looks like a row but does not match '| FL-NN |': {line[:60]!r}")
        # split on pipes that are not escaped and not inside backticks
        cells = [c.strip() for c in re.split(r"(?<!\\)\|(?=(?:[^`]*`[^`]*`)*[^`]*$)", line.strip().strip("|"))]
        out.append(cells)
    return out


def test_a_malformed_row_fails_instead_of_being_skipped():
    good = "| ID | T | S | R | St |\n|---|---|---|---|---|\n| FL-01 | a | b | c | FIXED abc123def |\n"
    assert len(rows(good)) == 1
    for bad in ("| FL1 | a | b | c | CANDIDATE |\n", "| fl-02 | a | b | c | CANDIDATE |\n", "| | a | b | c | CANDIDATE |\n"):
        with pytest.raises(AssertionError, match="looks like a row"):
            rows(good + bad)
    assert len(rows(good + "\nprose after the table\n| FLX | not part of the ledger table |\n")) == 1


def test_every_row_has_five_cells_and_unique_ids_in_order():
    parsed = rows()
    assert parsed, "no ledger rows parsed"
    assert all(len(cells) == 5 for cells in parsed), [c[0] for c in parsed if len(c) != 5]
    ids = [cells[0] for cells in parsed]
    assert len(set(ids)) == len(ids)
    numbers = [int(i[3:]) for i in ids]
    assert numbers == sorted(numbers)


def test_status_starts_with_a_known_word_and_fixed_rows_cite_a_reference():
    for cells in rows():
        status = cells[4]
        word = re.match(r"[A-Z-]+", status).group(0)  # a status word may be followed by ':' or a space
        assert word in WORDS, f"{cells[0]}: unknown status word {word!r}"
        if word in ("FIXED", "FIXED-ON-BRANCH"):
            assert REF.search(status), f"{cells[0]}: {word} without a commit hash or backlog id"
        if word == "FIXED-ON-BRANCH":
            assert "pending merge" in status, f"{cells[0]}: a branch-only fix must say 'pending merge'"
            assert re.match(r"FIXED-ON-BRANCH [0-9a-f]{9,40} ", status), f"{cells[0]}: needs the branch commit hash first"
