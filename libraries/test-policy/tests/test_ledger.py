"""The flake ledger is a stable, parseable format (docs/FORMATS.md): enforce its grammar."""

import pathlib
import re

LEDGER = pathlib.Path(__file__).resolve().parent.parent / "docs" / "FLAKE-LEDGER.md"
WORDS = ("FIXED-ON-BRANCH", "FIXED", "OPEN-HYPOTHESIS", "CANDIDATE", "NOT-A-FLAKE")
REF = re.compile(r"(?:\b[0-9a-f]{9,40}\b|\b[A-Z]{2,}-\d+\b)")


def rows():
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not re.match(r"\| FL-\d+ \|", line):
            continue
        # split on pipes that are not escaped and not inside backticks
        cells = [c.strip() for c in re.split(r"(?<!\\)\|(?=(?:[^`]*`[^`]*`)*[^`]*$)", line.strip().strip("|"))]
        out.append(cells)
    return out


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
