"""B015's semantic operator boundaries, proven at the adapter seam."""

from __future__ import annotations

from assay.adapters.python import PythonAdapter


ADAPTER = PythonAdapter()
UUID_OPERATORS = ("python:uuid-equality-swap",)
ENUM_OPERATORS = ("python:enum-comparison-swap",)
SEMANTIC_OPERATORS = UUID_OPERATORS + ENUM_OPERATORS


def _sites(text: str, *, operators=SEMANTIC_OPERATORS):
    return ADAPTER.generate_mutation_sites(
        text,
        set(range(1, len(text.splitlines()) + 1)),
        operators=operators,
        limit=100,
    )


def test_an_inplace_uuid_construction_is_a_uuid_equality_site():
    text = 'import uuid\n\nif user == uuid.UUID("00000000-0000-0000-0000-000000000000"):\n    handled = True\n'
    sites = _sites(text, operators=UUID_OPERATORS)

    assert len(sites) == 1
    assert sites[0].operator == "python:uuid-equality-swap"
    assert sites[0].description == "Eq->NotEq"
    assert text.encode()[sites[0].start_byte : sites[0].end_byte] == b"=="


def test_uuid_equality_flips_not_eq_back_to_eq():
    text = "import uuid\n\nif user != uuid.uuid4():\n    changed = True\n"
    sites = _sites(text, operators=UUID_OPERATORS)

    assert len(sites) == 1
    assert sites[0].description == "NotEq->Eq"
    assert text.encode()[sites[0].start_byte : sites[0].end_byte] == b"!="


def test_an_enum_member_comparison_is_an_enum_comparison_site():
    text = "from colors import Color\n\nif selected == Color.RED:\n    stop = True\n"
    sites = _sites(text, operators=ENUM_OPERATORS)

    assert len(sites) == 1
    assert sites[0].operator == "python:enum-comparison-swap"
    assert sites[0].description == "Eq->NotEq"
    assert text.encode()[sites[0].start_byte : sites[0].end_byte] == b"=="


def test_generated_mutants_parse_and_change_the_selected_token_only():
    import ast

    text = "import uuid\n\nif ticket != uuid.UUID(int=1):\n    missing = True\n"
    sites = _sites(text, operators=UUID_OPERATORS)

    assert len(sites) == 1
    mutated = site.apply(text.encode()) if (site := sites[0]) else None
    assert mutated is not None
    ast.parse(mutated)
    assert mutated.decode().count("==") == 1
    assert "uuid.UUID(int=1)" in mutated.decode()


def test_plain_comparisons_and_non_equality_operators_are_ineligible():
    text = "from colors import Color\n\nif count < Color.RED.size:\n    small = True\n"

    assert _sites(text, operators=SEMANTIC_OPERATORS) == ()


def test_bare_names_and_method_calls_are_never_semantic_sites():
    text = "if left == right:\n    same = True\nif value == helper.build():\n    built = True\n"

    assert _sites(text) == ()
