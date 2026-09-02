"""The Python half of the Go statement-position oracle: what
:mod:`assay.adapters.go_stmtpos` accepts, and every shape it refuses.

**What these tests are, and what they are NOT.** They exercise ASSAY's OWN
code -- the document reader, the schema pin, the path round-trip, the
environment it forces -- against handcrafted input. That is legitimate here
and is not the thing A-334 forbids: the claim under test is "assay refuses an
output shape it does not recognise", whose subject is assay. The claim
"go1.25 emits these blocks for this source" has the Go toolchain as its
subject, and no test in this file asserts it; that claim is proven separately
against the real toolchain in `tester-unified-go:local` and recorded in
`nyxloom-trove/carve-assets/P27-recarve/`.

The distinction is the whole reason a synthetic document appears below at all.
A test that mocked `go run` and then asserted the resulting line numbers would
be asserting that assay can echo a document it wrote itself -- exactly the
proxy-that-shares-the-hypothesis's-assumption failure A-396 caught one item
over.
"""

import json
from pathlib import Path

import pytest

from assay.adapters.go_stmtpos import (
    HELPER_DIR,
    OUTPUT_SCHEMA,
    _read_document,
    derive_statement_blocks,
)
from assay.errors import AssayError, Outcome, ReasonCode


def _document(*, schema=OUTPUT_SCHEMA, go_version="go1.25.14", files=None) -> bytes:
    if files is None:
        files = [
            {
                "path": "/repo/a.go",
                "blocks": [
                    {
                        "start_line": 3,
                        "start_col": 22,
                        "end_line": 7,
                        "end_col": 2,
                        "num_stmts": 2,
                        "stmt_lines": [4, 6],
                    }
                ],
            }
        ]
    return json.dumps(
        {"schema": schema, "go_version": go_version, "files": files}
    ).encode("utf-8")


ARGS = {"/repo/a.go": "a.go"}


def test_the_helper_source_ships_where_the_invoker_looks_for_it():
    """:data:`HELPER_DIR` is derived from the installed package's own
    location, so this test fails if the helper is ever moved without the
    invoker following it -- the failure would otherwise appear only on a real
    Go lane, in an environment this devcontainer cannot run."""
    assert (HELPER_DIR / "stmtpos.go").is_file()
    assert (HELPER_DIR / "go.mod").is_file()


def test_the_pinned_schema_matches_the_helper_the_wheel_actually_ships():
    """The pin is only a pin if both ends are checked. A helper bumped to
    schema 2 with :data:`OUTPUT_SCHEMA` left at 1 would refuse every real
    lane; the reverse would read an unknown shape as the known one, which is
    the more expensive direction -- so the constant is compared against the
    shipped Go source's own declaration rather than trusted."""
    source = (HELPER_DIR / "stmtpos.go").read_text(encoding="utf-8")
    assert f"const outputSchema = {OUTPUT_SCHEMA}" in source


def test_a_well_formed_document_becomes_blocks_keyed_by_repo_relative_path():
    report = _read_document(_document(), ARGS, "/usr/local/go/bin/go")

    assert set(report.blocks_by_path) == {"a.go"}
    (block,) = report.blocks_by_path["a.go"]
    assert block.extent == (3, 22, 7, 2)
    assert block.num_stmts == 2
    assert block.stmt_lines == (4, 6)


def test_the_helper_identity_names_the_toolchain_the_helper_itself_reported():
    """A-395: ``helpers[].identity`` exists to record WHICH toolchain produced
    a verdict. The version half comes from the helper's own
    ``runtime.Version()`` -- a measurement of the toolchain that compiled and
    ran it -- never a string assay formatted from its own expectations."""
    report = _read_document(
        _document(go_version="go1.99.0"), ARGS, "/usr/local/go/bin/go"
    )

    assert report.helper.tool == "go"
    assert report.helper.identity == "go version go1.99.0"
    assert report.helper.resolved_path == "/usr/local/go/bin/go"


def test_an_unrecognised_output_schema_is_refused_not_read():
    with pytest.raises(AssayError) as caught:
        _read_document(_document(schema=2), ARGS, "/usr/local/go/bin/go")

    assert caught.value.outcome is Outcome.ERROR
    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "schema" in str(caught.value)


def test_a_missing_toolchain_version_is_refused_rather_than_defaulted():
    """No fallback identity string. A verdict claiming a helper ran without
    naming which one is a false certification, and "unknown" would be a
    shadowing default standing in for a fact the helper is supposed to
    report."""
    with pytest.raises(AssayError) as caught:
        _read_document(_document(go_version=""), ARGS, "/usr/local/go/bin/go")

    assert "toolchain" in str(caught.value)


def test_a_file_the_caller_never_asked_for_is_refused():
    document = _document(files=[{"path": "/repo/elsewhere.go", "blocks": []}])

    with pytest.raises(AssayError) as caught:
        _read_document(document, ARGS, "/usr/local/go/bin/go")

    assert "never asked for" in str(caught.value)


def test_a_requested_file_missing_from_the_result_is_refused_by_name():
    """An absent key would otherwise reach ``attribute_statements`` as "this
    file has no statement positions", which that function does refuse (A-391)
    -- but with a message naming the wrong cause. Refusing here keeps the
    diagnosis pointing at the helper."""
    with pytest.raises(AssayError) as caught:
        _read_document(_document(files=[]), ARGS, "/usr/local/go/bin/go")

    assert "a.go" in str(caught.value)


def test_a_non_integer_block_field_is_refused():
    document = _document(
        files=[
            {
                "path": "/repo/a.go",
                "blocks": [
                    {
                        "start_line": "3",
                        "start_col": 22,
                        "end_line": 7,
                        "end_col": 2,
                        "num_stmts": 2,
                        "stmt_lines": [4],
                    }
                ],
            }
        ]
    )

    with pytest.raises(AssayError) as caught:
        _read_document(document, ARGS, "/usr/local/go/bin/go")

    assert "start_line" in str(caught.value)


def test_statement_lines_violating_the_block_invariants_are_refused():
    """``StatementBlock`` enforces sorted, duplicate-free, 1-based statement
    lines at construction. An unsorted list is not repaired here -- assay does
    not know which order was meant, and guessing would put invented positions
    into a verdict."""
    document = _document(
        files=[
            {
                "path": "/repo/a.go",
                "blocks": [
                    {
                        "start_line": 3,
                        "start_col": 22,
                        "end_line": 7,
                        "end_col": 2,
                        "num_stmts": 2,
                        "stmt_lines": [6, 4],
                    }
                ],
            }
        ]
    )

    with pytest.raises(AssayError) as caught:
        _read_document(document, ARGS, "/usr/local/go/bin/go")

    assert "sorted" in str(caught.value)


def test_output_that_is_not_json_is_refused():
    with pytest.raises(AssayError) as caught:
        _read_document(b"not json at all", ARGS, "/usr/local/go/bin/go")

    assert "JSON" in str(caught.value)


def test_a_source_file_absent_from_the_working_tree_refuses_before_go_runs(
    tmp_path: Path,
):
    """The profile names a file the tree does not have: the two are not the
    same revision, and the refusal must say so rather than surfacing as a
    toolchain error. Reached without any toolchain, because the check is
    assay's and happens before the subprocess is built."""
    with pytest.raises(AssayError) as caught:
        derive_statement_blocks(tmp_path, ["gone.go"])

    assert caught.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "not the same revision" in str(caught.value)


def test_a_missing_helper_installation_refuses_with_its_own_message(
    tmp_path: Path,
):
    """The helper is package data, and A-396 measured that it does ship -- but
    "it ships today" is not a check. If it ever stops shipping, this is the
    message a consumer gets, instead of a bare ``go run .`` failure blaming
    their own repository."""
    (tmp_path / "a.go").write_text("package a\n", encoding="utf-8")

    with pytest.raises(AssayError) as caught:
        derive_statement_blocks(
            tmp_path, ["a.go"], helper_dir=tmp_path / "no-helper-here"
        )

    assert "missing from the installation" in str(caught.value)
