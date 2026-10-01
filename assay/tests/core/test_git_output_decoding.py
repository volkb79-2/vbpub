"""O1 (P15, controller repair A-134) — ``git.run`` decodes git's output
itself, as UTF-8, and refuses what it cannot represent.

Turning ``core.quotePath`` off is what lets a raw non-ASCII byte reach this
process at all, so the decoding half of that boundary has to be deliberate
too. ``subprocess``' ``text=True`` is not: it decodes with whatever codec the
ambient ``LC_CTYPE`` names (so the same repository is readable or not
depending on the environment), it raises a bare ``UnicodeDecodeError`` that no
caller catches, and it applies universal-newline translation — silently
turning a lone ``\\r`` INSIDE a source line into a second ``\\n``, which shifts
every changed-line number after it.

Negative: decoding with ``text=True`` again makes the non-UTF-8 fixture raise
``UnicodeDecodeError`` instead of a typed ``ERROR``/``GIT_FAILED``, and makes
the carriage-return fixture report a diff body one line longer than git wrote
— see this module's mutation evidence in the package LOG.
"""

from __future__ import annotations

import os

import pytest

from assay.errors import AssayError, Outcome, ReasonCode
from assay.git import run


def test_output_naming_a_non_utf8_path_is_rejected_as_git_failed(git_repo):
    # A real filename with bytes that cannot be UTF-8 (legal on a POSIX
    # filesystem -- only NUL and '/' are actually forbidden), COMMITTED, so
    # that git has to name it in the output of an ordinary query.
    target = os.fsencode(str(git_repo.path.resolve())) + b"/bad_\xff\xfename.py"
    os.close(os.open(target, os.O_CREAT | os.O_WRONLY, 0o644))
    git_repo.commit_all("commit the undecodable name")

    with pytest.raises(AssayError) as excinfo:
        run(git_repo.path, "log", "--name-only", "-1")

    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.GIT_FAILED
    assert "UTF-8" in str(excinfo.value)


def test_a_carriage_return_in_content_survives_as_itself(git_repo):
    # Universal-newline translation would rewrite this \r as \n, adding a
    # phantom line to the diff body that git never wrote. Asserted on the
    # returned text directly, against the byte git actually stored.
    git_repo.write("cr.py", 'x = "a\rb"\n')
    git_repo.commit_all("a line holding a bare CR")

    assert b"\r" in (git_repo.path / "cr.py").read_bytes()  # really there

    diff_text = run(git_repo.path, "show", "--unified=0", "--format=", "HEAD")
    added_body = [
        line
        for line in diff_text.split("\n")
        if line.startswith("+") and not line.startswith("+++")
    ]
    # Exactly ONE added line, still carrying the CR -- not two, the second of
    # them a phantom `b"` that translation would have manufactured.
    assert added_body == ['+x = "a\rb"']
