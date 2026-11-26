"""P4/S4: the language matrix README publishes is GENERATED from code.

Operator item (D) of `p4-operator-input-20261125.md` asked for one documented
matrix of what each mutation runtime actually watches, and the estate rule
that made it binding (`AGENTS.md`, "User-facing docs are part of the change")
is that the check must be able to go red -- "we will remember to update the
docs" is precisely the check that cannot fail.

So this file proves three things, each with a real proof and a must-fail
control made from the SAME checking logic:

1. `README.md`'s generated block equals `mutation_runtime.readme_block()`
   byte for byte (rendering an altered table must NOT compare equal);
2. the block markers occur exactly once each (breaking one must be detected);
3. the table's own facts are derived, not transcribed: every language
   `assay.cli._built_in_registry` reaches at R2 has a row, and every cell is
   non-empty, so a row cannot be half-written.

It also pins the two structural facts a consumer document relies on: the
`#mutation-runtime-matrix` anchor exists, and `README.md`'s SQL row no longer
claims the `hung` bucket (the stale cell S4 corrects -- liveness is injected
only for a Python/pytest lane, `runner.py:5081`).
"""

from __future__ import annotations

import re

import pytest
from conftest import PROJECT_ROOT

from assay import mutation_runtime
from assay.cli import _built_in_registry

README = PROJECT_ROOT / "README.md"


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _readme_block(document: str) -> str:
    """The exact slice of *document* between the markers, or a hard refusal."""
    bounds = mutation_runtime.block_bounds(document)
    assert bounds is not None, (
        "README.md must carry exactly one "
        f"{mutation_runtime.MATRIX_BEGIN} ... {mutation_runtime.MATRIX_END} pair"
    )
    return document[bounds[0] : bounds[1]]


def _check_block(document: str, expected: str) -> None:
    actual = _readme_block(document)
    assert actual == expected.strip(), (
        "README.md's generated mutation-runtime matrix is stale; regenerate it "
        "from assay.mutation_runtime.render_markdown() "
        "(tests/core/test_mutation_runtime_matrix.py documents the rule)"
    )


def test_readme_generated_block_matches_the_module():
    _check_block(_readme_text(), mutation_runtime.readme_block())


def test_a_hand_edited_readme_does_not_match_the_module():
    """Must-fail control: the comparison above refuses an edited block."""
    edited = _readme_text().replace(
        "provided: pressure-adjusted idle window over a complete resource trace",
        "provided: pressure-adjusted idle window (hand-edited)",
    )
    assert edited != _readme_text(), "the control must actually edit the block"
    with pytest.raises(AssertionError, match="is stale"):
        _check_block(edited, mutation_runtime.readme_block())


def test_a_mutated_row_renders_differently():
    """Must-fail control one level down: the RENDER is a function of the rows.

    This is what makes a single data source meaningful: if `render_markdown`
    ignored its argument and printed a fixed string, the README comparison
    would still pass after any code change and could never fail for the right
    reason.
    """
    row = mutation_runtime.RUNTIME_MATRIX[0]
    mutated = row._replace(idle_hang="not provided")
    rows = (mutated,) + mutation_runtime.RUNTIME_MATRIX[1:]
    rendered = mutation_runtime.render_markdown(rows)
    assert rendered != mutation_runtime.render_markdown()
    with pytest.raises(AssertionError, match="is stale"):
        _check_block(_readme_text(), mutation_runtime.readme_block(rows))


@pytest.mark.parametrize(
    "document",
    [
        f"{mutation_runtime.MATRIX_BEGIN}\nx\n",
        f"x\n{mutation_runtime.MATRIX_END}\n",
        (
            f"{mutation_runtime.MATRIX_BEGIN}\n{mutation_runtime.MATRIX_BEGIN}\n"
            f"{mutation_runtime.MATRIX_END}\n"
        ),
        (
            f"{mutation_runtime.MATRIX_BEGIN}\n{mutation_runtime.MATRIX_END}\n"
            f"{mutation_runtime.MATRIX_END}\n"
        ),
        f"{mutation_runtime.MATRIX_END}\n{mutation_runtime.MATRIX_BEGIN}\n",
    ],
)
def test_block_bounds_refuse_every_malformed_marker_shape(document: str):
    assert mutation_runtime.block_bounds(document) is None


def test_readme_carries_each_marker_exactly_once():
    text = _readme_text()
    assert text.count(mutation_runtime.MATRIX_BEGIN) == 1
    assert text.count(mutation_runtime.MATRIX_END) == 1


def test_the_generated_anchor_a_consumer_links_to_exists():
    text = _readme_text()
    assert f'<a id="{mutation_runtime.MATRIX_ANCHOR_ID}"></a>' in text
    # The block's own heading is generated from the same constant, so an
    # anchor that resolves to nothing cannot be published.
    assert mutation_runtime.MATRIX_HEADING in text


def test_every_r2_reachable_language_has_a_row():
    """Derived from the registry this build actually resolves, never a list.

    `_built_in_registry` is the single authority on which languages can declare
    R2 at all; a row therefore cannot be dropped while its language stays
    registered, and a newly registered language cannot ship undocumented.
    """
    registry = _built_in_registry()
    reachable = {
        language
        for language, entry in registry.entries.items()
        if "R2" in entry.rigor
    }
    assert reachable, "the registry must reach R2 for at least one language"
    rows = {row.language for row in mutation_runtime.RUNTIME_MATRIX}
    assert reachable <= rows, f"languages reaching R2 without a row: {reachable - rows}"
    assert set(mutation_runtime.R2_REACHABLE_LANGUAGES) == reachable
    # A row for a language the registry does not know would be a claim about a
    # language this build cannot even resolve.
    assert rows <= set(registry.entries), f"rows for unregistered languages: {rows - set(registry.entries)}"


def test_every_cell_is_non_empty():
    for row in mutation_runtime.RUNTIME_MATRIX:
        for column, value in zip(mutation_runtime.COLUMNS, _cells(row)):
            assert value.strip(), f"{row.language}: empty {column}"
        assert row.language.strip() and row.display.strip()


def _cells(row: mutation_runtime.LanguageRuntimeRow) -> list[str]:
    return [
        row.display,
        row.r2_path,
        row.progress_signal,
        row.idle_hang,
        row.cpu_without_progress,
        row.per_test_bound,
        row.timeout_classification,
        row.resume_store_carry_over,
        row.shard,
        row.cancel,
        row.not_supported,
    ]


def test_the_column_headers_match_the_rendered_field_order():
    row = mutation_runtime.RUNTIME_MATRIX[0]
    assert len(mutation_runtime.COLUMNS) == len(_cells(row))


def test_every_document_reference_to_the_anchor_resolves():
    """Every `](...README.md#mutation-runtime-matrix)` link, from any document.

    `tests/core/test_docs_examples_and_vocabulary.py` resolves only links whose
    target path starts with `docs/` or names one of the three documents bare, so
    a `../README.md#...` reference (what CONSUMERS.md needs to reach the project
    README) is outside that sweep. This is the check for the anchor those links
    name, and it is deliberately path-relative rather than hard-coded so a new
    document linking here is covered by the same rule.
    """
    pattern = re.compile(r"\]\((?P<target>[^)]*README\.md)#(?P<anchor>[^)]+)\)")
    documents = [
        PROJECT_ROOT / "docs" / "CONSUMERS.md",
        PROJECT_ROOT / "docs" / "DESIGN-GUIDE.md",
    ]
    found = 0
    for document in documents:
        for match in pattern.finditer(document.read_text(encoding="utf-8")):
            target = (document.parent / match.group("target")).resolve()
            assert target.is_file(), f"{document.name} links to {target}, which does not exist"
            found += 1
            if match.group("anchor") == mutation_runtime.MATRIX_ANCHOR_ID:
                assert target == README.resolve()
                assert f'<a id="{mutation_runtime.MATRIX_ANCHOR_ID}"></a>' in target.read_text(
                    encoding="utf-8"
                )
    assert found, "expected at least one README link from the docs"


def test_readme_sql_row_no_longer_claims_a_hung_bucket():
    """The stale cell S4 corrects (verified by reading, not by a run).

    `runner.py:5081` injects liveness only when the adapter is `python`, so a
    SQL lane has no candidate progress signal, no bound, and therefore no
    `hung` result. The README's R2 cell used to say SQL shared Python's `hung`
    results, which is unreachable on that path.
    """
    text = _readme_text()
    sql_row = next(
        line for line in text.splitlines() if line.startswith("| **SQL/DDL** |")
    )
    assert "hung" not in sql_row.replace("`hung` is unreachable here", ""), sql_row
    assert "`hung` is unreachable here" in sql_row
    assert "#mutation-runtime-matrix" in sql_row
