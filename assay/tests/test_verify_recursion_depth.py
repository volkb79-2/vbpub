"""B074 — ``assay verify`` on a pathologically deep, well-formed JSON
document.

The THIRD site of one gap. ``adjudication.py``'s ``evaluate_provenance`` was
the first (`f0126b35`); ``attestation.py``'s ``parse_attestation`` was the
second (B072); this is the third, found by B072's own required sweep of every
``json.loads``/``json.load`` call site in ``src/assay``.

``json.loads`` is CPython's recursive-descent parser, and on a deeply nested
document it raises ``RecursionError`` at the real C-stack boundary — a
``RuntimeError`` subclass, **not** a ``ValueError``, so an
``except json.JSONDecodeError`` clause does not catch it.

**This is the site that matters most of the three.** ``assay verify`` exists
to read a verdict artifact *produced somewhere else* — "independently of how
it was produced" is its own ``--help`` text — from a path or from stdin. That
is untrusted input by definition, and it is the one command a consumer points
at an artifact whose producer they are trying to check. Its contract for an
unreadable document is already a returned failure list and exit 1; before
B074 a deeply nested one crashed the process with a traceback, which a CI
caller reads as a tooling fault rather than as a bad artifact.

Mirrors ``test_attestation_recursion_depth.py`` and
``test_adjudication_provenance_parse.py``'s equivalents, and — as B074's
acceptance requires — proves the fix through the real consumer path
(``cmd_verify``, both the stdin and the file arms, plus ``cli.main``), not
only the bare function.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from assay import verify
from assay.verify import cmd_verify, verify_document, verify_text

#: 100,000-deep nested array = 200,000 bytes. Legible JSON to ``json.loads``
#: at reasonable depths; only the C-stack boundary refuses it.
_DEPTH = 100_000
_PATHOLOGICAL = "[" * _DEPTH + "]" * _DEPTH


# --- the bare function ---------------------------------------------------------


def test_a_pathologically_deep_document_is_a_failure_list_not_a_raise():
    failures = verify_text(_PATHOLOGICAL)
    assert failures, "an unreadable document must produce at least one failure"
    assert any("not valid JSON" in failure for failure in failures), failures


def test_it_is_a_RecursionError_that_is_being_caught(monkeypatch: pytest.MonkeyPatch):
    """Pins the WHY, not just the what: a future CPython that raised some
    other exception here would leave the fix silently inert, and the depth
    fixture alone could not tell the difference."""

    def raise_recursion_error(*args, **kwargs):
        raise RecursionError("injected: maximum recursion depth exceeded")

    monkeypatch.setattr(verify.json, "loads", raise_recursion_error)
    failures = verify_text("{}")
    assert failures == ["not valid JSON: injected: maximum recursion depth exceeded"]


def test_an_ordinarily_malformed_document_is_unchanged():
    """The control: widening the ``except`` tuple must not change what an
    ordinary syntax error does."""
    failures = verify_text("{not json")
    assert any("not valid JSON" in failure for failure in failures), failures


def test_a_legible_document_still_reaches_verify_document():
    """The other control: the widening is a CATCH, not a validation change.
    A well-formed but wrong-shaped document must still be judged by
    ``verify_document`` rather than dismissed as unparseable."""
    failures = verify_text(json.dumps({"schema_version": 10}))
    assert failures, "an incomplete verdict must still be rejected"
    assert not any("not valid JSON" in failure for failure in failures), failures
    assert failures == verify_document({"schema_version": 10})


# --- the real consumer path ----------------------------------------------------


def test_assay_verify_exits_1_with_a_message_on_stdin(capsys: pytest.CaptureFixture[str]):
    """The refusal a consumer actually sees, through ``cmd_verify``'s stdin
    arm — the shape a CI pipeline uses."""
    err = io.StringIO()

    code = cmd_verify("-", stdin=io.StringIO(_PATHOLOGICAL), stderr=err)

    assert code == 1
    assert "assay verify: not valid JSON" in err.getvalue()


def test_assay_verify_exits_1_with_a_message_on_a_file_path(tmp_path: Path):
    """The file arm, separately: ``_read_file`` and the stdin read are two
    different ways in, and only one of them was exercised above."""
    artifact = tmp_path / "verdict.json"
    artifact.write_text(_PATHOLOGICAL, encoding="utf-8")
    err = io.StringIO()

    code = cmd_verify(str(artifact), stdin=io.StringIO(""), stderr=err)

    assert code == 1
    assert "assay verify: not valid JSON" in err.getvalue()


def test_it_is_the_same_through_cli_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """And once more through the actual entry point, so the wiring is proven
    and not assumed from the unit."""
    from assay.cli import main

    artifact = tmp_path / "verdict.json"
    artifact.write_text(_PATHOLOGICAL, encoding="utf-8")

    code = main(["verify", str(artifact)])

    assert code == 1
    assert "not valid JSON" in capsys.readouterr().err


# The estate-wide sweep guard that used to live here (a hard-coded 3-element
# tuple of file paths, matched by an exact `except (...)` STRING) has moved to
# `test_untrusted_json_parse_sweep.py` and been rewritten to derive its own
# site list. It was unfit twice over, and both faults are recorded there: it
# could not see a site nobody had listed, and it could not see a guard written
# with the same three names in a different ORDER -- which one module already
# had.
