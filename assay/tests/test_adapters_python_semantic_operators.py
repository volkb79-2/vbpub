"""B015's two "semantic" operators are WITHDRAWN (B034/A-326).

This module used to prove their site boundaries. Every one of those
boundaries was inside `python:compare-swap`'s own output -- same byte span,
same replacement bytes -- so the family added no mutation coverage and
co-selecting it double-counted every shared site. What is proven here now is
the withdrawal itself, at all three seams it has to hold at: the producer
emits nothing, a lane declaring either name is refused at load, and the
spellings stay legal in a schema-v7 artifact so verdicts already emitted by
2.3.0/2.4.x keep verifying.
"""

from __future__ import annotations

import pytest

from assay.adapters.python import PythonAdapter
from assay.errors import LaneConfigError
from assay.vocabulary import (
    MUTATION_OPERATORS,
    MUTATION_OPERATORS_BY_LANGUAGE,
    WITHDRAWN_MUTATION_OPERATORS,
)


ADAPTER = PythonAdapter()
WITHDRAWN = ("python:uuid-equality-swap", "python:enum-comparison-swap")
#: The four that survive -- the original catalogue (A-112/A-114).
PRODUCIBLE = (
    "python:compare-swap",
    "python:boolop-swap",
    "python:bool-const-flip",
    "python:falsy-swap",
)


def _sites(text: str, *, operators):
    return ADAPTER.generate_mutation_sites(
        text,
        set(range(1, len(text.splitlines()) + 1)),
        operators=operators,
        limit=100,
    )


@pytest.mark.parametrize(
    "text",
    [
        'import uuid\n\nif user == uuid.UUID("0" * 32):\n    handled = True\n',
        "import uuid\n\nif user != uuid.uuid4():\n    changed = True\n",
        "from colors import Color\n\nif selected == Color.RED:\n    stop = True\n",
        # The false-positive class the shipped predicate actually matched:
        # any `name.attr`, enum or not. Untested by the suite this replaces.
        "if cfg.debug == True:\n    verbose = True\n",
        "if self_obj.count == 0:\n    empty = True\n",
    ],
)
def test_no_withdrawn_operator_produces_a_site_any_more(text):
    assert _sites(text, operators=WITHDRAWN) == ()


def test_compare_swap_still_covers_every_site_the_withdrawn_family_claimed():
    """The withdrawal loses nothing: this is the measured finding that
    justified it (B034(a)), asserted rather than described."""
    text = (
        "import uuid\n\n"
        'if user == uuid.UUID("0" * 32):\n'
        "    handled = True\n"
        "if cfg.debug == True:\n"
        "    verbose = True\n"
    )
    swap = _sites(text, operators=("python:compare-swap",))
    spans = {(site.start_byte, site.end_byte, site.replacement_sha256) for site in swap}

    assert len(spans) == 2
    assert text.encode()[swap[0].start_byte : swap[0].end_byte] == b"=="


def test_co_selection_no_longer_double_counts_a_shared_site():
    """B034(b): declaring `compare-swap` alongside the withdrawn names used
    to emit each shared site TWICE (identical span, identical replacement
    digest, two operator labels) because `MutationSite.identity` includes
    the operator. Selecting all six now yields exactly the four distinct
    sites `compare-swap` alone finds."""
    text = "if cfg.debug == True:\n    verbose = True\nif cfg.mode != 'x':\n    other = True\n"
    alone = _sites(text, operators=("python:compare-swap",))
    together = _sites(text, operators=("python:compare-swap", *WITHDRAWN))

    assert [site.identity for site in together] == [site.identity for site in alone]


def test_a_lane_declaring_a_withdrawn_operator_is_refused_at_load(tmp_path):
    (tmp_path / "src").mkdir()
    lane_file = tmp_path / "assay.toml"
    lane_file.write_text(
        "schema_version = 2\n\n"
        "[lanes.pylane]\n"
        'scope = "S1"\n'
        'rigor = ["R0", "R2"]\n'
        'enforcement = "gate"\n'
        'argv = ["true"]\n'
        'env_passthrough = ["PATH"]\n'
        "env = {}\n"
        'budget = "1m"\n'
        "allow_argv_append = false\n\n"
        "[lanes.pylane.judge]\n"
        'language = "python"\n'
        'source_roots = ["src"]\n'
        'base = "origin/main"\n\n'
        "[lanes.pylane.judge.mutation]\n"
        "jobs = 1\n"
        "max_mutants = 10\n"
        'operators = ["python:compare-swap", "python:enum-comparison-swap"]\n',
        encoding="utf-8",
    )
    from assay.config import load_lane_file

    with pytest.raises(LaneConfigError) as excinfo:
        load_lane_file(lane_file)

    message = str(excinfo.value)
    assert "withdrawn" in message
    assert "python:enum-comparison-swap" in message


def test_the_spelling_survives_in_the_v7_vocabulary_on_purpose():
    """A-326's deliberate asymmetry: behaviour withdrawn, spelling kept.

    Released `assay verify` builds ACCEPT a v7 document naming either
    operator and released `assay run` builds EMITTED them, so deleting the
    names from the catalogue (and therefore from the packaged schema's
    per-language `oneOf`) would stop real artifacts from verifying -- the
    breakage A-324 requires a schema-version bump for. The names go at the
    next bump, not here.
    """
    assert WITHDRAWN_MUTATION_OPERATORS == frozenset(WITHDRAWN)
    for name in WITHDRAWN:
        assert name in MUTATION_OPERATORS
        assert name in MUTATION_OPERATORS_BY_LANGUAGE["python"]
    assert set(MUTATION_OPERATORS_BY_LANGUAGE["python"]) == set(PRODUCIBLE) | set(
        WITHDRAWN
    )
