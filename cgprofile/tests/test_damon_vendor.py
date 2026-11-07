"""The vendored DAMON analysis module must not drift from its source of truth.

`src/cgprofile/_damon/damon_analysis.py` is a byte copy of
`scripts/damon-analysis/lib/damon_analysis.py` (the sibling tool owns it). The
wheel cannot import across projects, so the copy ships inside the package; this
test is the sync mechanism. Update both together:
`cp ../damon-analysis/lib/damon_analysis.py src/cgprofile/_damon/damon_analysis.py`.
The file is SPDX GPL-2.0-only; see NOTICE and backlog CP-51 for the licence question.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENDORED = PROJECT_ROOT / "src" / "cgprofile" / "_damon" / "damon_analysis.py"
SOURCE_OF_TRUTH = PROJECT_ROOT.parent / "scripts" / "damon-analysis" / "lib" / "damon_analysis.py"


def test_the_vendored_copy_keeps_its_licence_header():
    head = VENDORED.read_text(encoding="utf-8").splitlines()[:5]
    assert any("SPDX-License-Identifier: GPL-2.0" in line for line in head)


@pytest.mark.skipif(
    not SOURCE_OF_TRUTH.is_file(),
    reason="sibling scripts/damon-analysis is not mounted in this environment",
)
def test_the_vendored_copy_equals_the_source_of_truth():
    assert VENDORED.read_bytes() == SOURCE_OF_TRUTH.read_bytes()


def test_the_notice_names_the_vendored_file():
    notice = (PROJECT_ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "cgprofile/_damon/damon_analysis.py" in notice
    assert "GPL-2.0-only" in notice
