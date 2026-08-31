"""The statement-position correction, checked against EVERY frozen P27 witness.

A-334: a test double is not evidence about an external system. Every number
asserted here comes from one of two real artifacts, and neither is authored by
this test:

* `nyxloom-trove/carve-assets/P27/witness/coverage-*.out` — real
  `go test -coverprofile` output, frozen by the P27 carver (carver-owned; this
  suite reads them and never writes them);
* `nyxloom-trove/carve-assets/P27-recarve/stmtpos-witness-oracle.json` — real
  output of `assay/helpers/go/stmtpos/` run inside `tester-unified-go:local`
  under `--network=none`, with its own `PROVENANCE.md`.

So this suite needs no Go toolchain and does not shell out (A-042/A-043: this
devcontainer has none, and the gate container has none either — only
`tester-unified-go` does).

The EXPECTED line sets are the P27 carve's own stated truths, not this wave's
opinion: `BLOCKED-grammar.md` §1 for the collision pair, §2 for `seg.go`, §3
for `lit.go`, and the independent hand manifest `manifest/calc-statements.json`
— authored from source bytes before any profile existed — for `calc.go`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.coverage_parsers import go_cover
from assay.coverage_parsers.model import CoverageBlock, CoverageProfile, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode
from assay.statement_attribution import StatementBlock, attribute_statements

_CARVE = Path(__file__).resolve().parents[1] / "nyxloom-trove" / "carve-assets"
_WITNESS = _CARVE / "P27" / "witness"
_ORACLE_JSON = _CARVE / "P27-recarve" / "stmtpos-witness-oracle.json"


def _oracle_blocks() -> dict[str, tuple[StatementBlock, ...]]:
    """The committed oracle document, keyed by source basename."""
    document = json.loads(_ORACLE_JSON.read_text())
    assert document["schema"] == 1, (
        "the committed oracle artifact declares a schema this test was not "
        "written against"
    )
    out: dict[str, tuple[StatementBlock, ...]] = {}
    for entry in document["files"]:
        out[Path(entry["path"]).name] = tuple(
            StatementBlock(
                start_line=b["start_line"],
                start_col=b["start_col"],
                end_line=b["end_line"],
                end_col=b["end_col"],
                num_stmts=b["num_stmts"],
                stmt_lines=tuple(b["stmt_lines"]),
            )
            for b in entry["blocks"]
        )
    return out


def _attribute(profile_name: str, source_name: str) -> FileCoverage:
    """Parse a frozen profile, correct it with the frozen oracle, return the
    single file's corrected coverage."""
    profile = go_cover.parse(
        (_WITNESS / profile_name).read_text(), producer=None
    )
    assert not profile.statement_attributed, (
        "a parser must never mark a profile statement-attributed; only "
        "attribute_statements may"
    )
    (key,) = profile.files
    corrected = attribute_statements(
        profile, {key: _oracle_blocks()[source_name]}
    )
    assert corrected.statement_attributed
    return corrected.files[key]


# --------------------------------------------------------------------------
# The impossibility proof, answered.
# --------------------------------------------------------------------------


def test_the_collision_pair_resolves_to_different_lines_from_identical_bytes():
    """`BLOCKED-grammar.md` §1: two gofmt-clean sources emit a byte-identical
    profile while their statements begin on different lines. No profile-only
    rule can be right on both; a source-side oracle is right on both."""
    profile_text = (_WITNESS / "coverage-collision.out").read_text()
    # The premise the whole ruling rests on: ONE profile, both sources.
    assert profile_text.count("f.go") == 1

    col_a = _attribute("coverage-collision.out", "collision-colA.go")
    col_b = _attribute("coverage-collision.out", "collision-colB.go")

    assert set(col_a.executed) == {4, 6}
    assert set(col_b.executed) == {4, 5}
    assert set(col_a.executed) != set(col_b.executed)
    assert not col_a.missing and not col_b.missing


def test_the_naive_expansion_cannot_tell_the_collision_pair_apart():
    """The control that keeps the test above from passing vacuously: the
    un-corrected line sets ARE identical, which is the defect being fixed."""
    profile = go_cover.parse(
        (_WITNESS / "coverage-collision.out").read_text(), producer=None
    )
    (file_cov,) = profile.files.values()
    # `range(start, end + 1)` over `3.22,7.2`: the signature (3) and the
    # closing brace (7) are attributed as executable code, and lines 5 and 6
    # are indistinguishable between the two sources.
    assert set(file_cov.executed) == {3, 4, 5, 6, 7}


# --------------------------------------------------------------------------
# The discriminator, the caveat, and the shapes.
# --------------------------------------------------------------------------


def test_seg_go_is_derived_correctly_and_kills_the_fitted_rule():
    """`BLOCKED-grammar.md` §2: truth is `{4, 5, 7}`. The rule R1 the carve
    review constructed to pass all four original witnesses yields `{4, 5, 8}`
    and dies here, because `seg.go`'s third block puts its statement at
    `startLine` where the blocks above it put theirs after `startLine`."""
    file_cov = _attribute("coverage-seg.out", "seg.go")
    assert set(file_cov.executed) == {4, 5}
    assert set(file_cov.missing) == {7}
    assert set(file_cov.executable) == {4, 5, 7}
    # R1's answer, explicitly NOT what we produce.
    assert 8 not in file_cov.executable


def test_lit_go_drops_the_fabricated_signature_but_still_launders_line_four():
    """`BLOCKED-grammar.md` §3, honoured rather than overclaimed.

    FIXED: line 3 (`func H() int {`) is no longer reported as executable.

    NOT FIXED, and not fixable at line granularity: line 4 carries TWO counted
    statements — the assignment (block `3.14,4.18`, count 1) and the func
    literal's own body (block `4.18,4.30`, count 0) — so executed-wins still
    promotes it. `coverage.py` shares this limit. Recorded as A-393/B053.
    """
    file_cov = _attribute("coverage-lit.out", "lit.go")
    assert 3 not in file_cov.executable, "the signature is not code"
    assert set(file_cov.executed) == {4, 5, 6}
    assert set(file_cov.missing) == set()
    # State the unfixed half as an assertion, so a future change that DOES
    # fix it fails here loudly instead of quietly contradicting the docs.
    assert 4 in file_cov.executed


def test_shapes_go_attributes_every_shared_boundary_block_correctly():
    """The half-open proof. `28.22,29.2` ends at exactly the position
    `29.2,31.3` starts at; the statement there belongs to the first block
    only, which a positional intersection could not express."""
    file_cov = _attribute("coverage-shapes.out", "shapes.go")
    assert set(file_cov.executed) == {5, 7, 18, 22, 29, 30, 32, 37, 39}
    assert set(file_cov.missing) == {9, 11, 20, 24, 41}
    # Every fabrication the naive rule made is gone: function signatures,
    # `case` labels, closing braces and a condition continuation.
    for fabricated in (4, 6, 8, 17, 19, 21, 28, 31, 36, 38, 40):
        assert fabricated not in file_cov.executable


def test_edge_go_needs_no_rule_that_depends_on_the_end_column():
    """A-218 closed the end-column-1 case as discriminating nothing. The
    oracle is consistent with that: it derives `{7, 8, 10}` without any rule
    consulting an end column."""
    file_cov = _attribute("coverage-edge.out", "edge.go")
    assert set(file_cov.executed) == {7, 8, 10}
    assert set(file_cov.missing) == set()
    assert 6 not in file_cov.executable  # the signature


# --------------------------------------------------------------------------
# Agreement with the independent third witness.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "profile_name, source_name, executed, missing",
    [
        ("coverage-commit1.out", "calc-commit1.go", {5, 12, 13}, {15}),
        (
            "coverage-commit2.out",
            "calc-commit2.go",
            {5, 12, 13, 22, 25},
            {15, 30},
        ),
    ],
)
def test_calc_matches_the_hand_manifest_authored_before_any_profile_existed(
    profile_name, source_name, executed, missing
):
    """`manifest/calc-statements.json` is the third witness A-172 required:
    written from source bytes BEFORE any coverprofile was generated, and never
    an input to the oracle. For commit2 it states 7 statements, 5 executed and
    2 missing — which is what the correction derives."""
    file_cov = _attribute(profile_name, source_name)
    assert set(file_cov.executed) == executed
    assert set(file_cov.missing) == missing


def test_the_commit2_totals_equal_the_manifests_own_arithmetic():
    """`go` itself reported 71.4% = 5/7 for commit2, siding with the manifest
    against the naive expansion's 13 executable lines."""
    manifest = json.loads(
        (_CARVE / "P27" / "manifest" / "calc-statements.json").read_text()
    )
    file_cov = _attribute("coverage-commit2.out", "calc-commit2.go")
    assert len(file_cov.executable) == manifest["totals"]["statements"] == 7
    assert len(file_cov.executed) == manifest["totals"]["executed"] == 5
    assert len(file_cov.missing) == manifest["totals"]["missing"] == 2
    assert set(file_cov.executable) == {
        entry["line"] for entry in manifest["statements"]
    }
    # And every line the manifest lists as NOT a statement is absent.
    for lines in manifest["not_statements"].values():
        for line in lines:
            assert line not in file_cov.executable


# --------------------------------------------------------------------------
# The refusals. A wrong pairing must fail loudly, never attribute anyway.
# --------------------------------------------------------------------------


def _one_block_profile(**overrides) -> CoverageProfile:
    block = CoverageBlock(
        start_line=3,
        start_col=22,
        end_line=7,
        end_col=2,
        num_stmts=2,
        count=1,
        **overrides,
    )
    return CoverageProfile(
        files={
            "f.go": FileCoverage(
                executed=frozenset({3, 4, 5, 6, 7}),
                missing=frozenset(),
                excluded=None,
                blocks=(block,),
            )
        }
    )


def test_a_block_bearing_file_with_no_oracle_entry_refuses():
    with pytest.raises(AssayError) as excinfo:
        attribute_statements(_one_block_profile(), {})
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "no source-side statement positions were derived" in str(
        excinfo.value
    )


def test_an_extent_the_source_does_not_have_refuses_and_names_it():
    """A profile from a different revision than the source: the classic stale
    artifact. Attributing anyway would publish a verdict about lines that are
    not the lines that ran."""
    oracle = (
        StatementBlock(
            start_line=3,
            start_col=22,
            end_line=9,  # the source moved
            end_col=2,
            num_stmts=2,
            stmt_lines=(4, 6),
        ),
    )
    with pytest.raises(AssayError) as excinfo:
        attribute_statements(_one_block_profile(), {"f.go": oracle})
    message = str(excinfo.value)
    assert "not produced from the same revision" in message
    assert "3.22,7.2" in message  # only in the profile
    assert "3.22,9.2" in message  # only in the source


def test_a_num_stmts_disagreement_on_a_matched_extent_refuses():
    """The extents can match while the statement COUNT does not — an edit that
    added a statement without moving the block's boundaries. Caught, because
    `numStmts` is compared rather than assumed."""
    oracle = (
        StatementBlock(
            start_line=3,
            start_col=22,
            end_line=7,
            end_col=2,
            num_stmts=3,
            stmt_lines=(4, 5, 6),
        ),
    )
    with pytest.raises(AssayError) as excinfo:
        attribute_statements(_one_block_profile(), {"f.go": oracle})
    message = str(excinfo.value)
    assert "2 statement(s) in the coverage profile and 3 in the source" in message


def test_a_line_based_format_is_passed_through_untouched():
    """`blocks is None` means the format has no block concept at all — every
    line-based format. Nothing to correct, so nothing is changed, and the
    profile is still marked attributed for the caller that asked."""
    original = CoverageProfile(
        files={
            "a.py": FileCoverage(
                executed=frozenset({1, 2}),
                missing=frozenset({3}),
                excluded=frozenset({4}),
            )
        }
    )
    result = attribute_statements(original, {})
    assert result.files["a.py"] == original.files["a.py"]
    assert result.statement_attributed


def test_executed_wins_across_blocks_regardless_of_their_order():
    """The overlap rule `go_cover` already keeps, preserved through the
    correction: a never-taken block must not downgrade a line another block
    marked executed. Asserted in BOTH orders, so the result cannot depend on
    which record came first."""
    covered = StatementBlock(
        start_line=1, start_col=1, end_line=2, end_col=1,
        num_stmts=1, stmt_lines=(9,),
    )
    uncovered = StatementBlock(
        start_line=3, start_col=1, end_line=4, end_col=1,
        num_stmts=1, stmt_lines=(9,),
    )
    for order in ((1, 0), (0, 1)):
        parsed = tuple(
            CoverageBlock(
                start_line=b.start_line, start_col=b.start_col,
                end_line=b.end_line, end_col=b.end_col,
                num_stmts=1, count=count,
            )
            for b, count in [(covered, 1), (uncovered, 0)]
        )
        profile = CoverageProfile(
            files={
                "f.go": FileCoverage(
                    executed=frozenset({1}),
                    missing=frozenset(),
                    excluded=None,
                    blocks=tuple(parsed[i] for i in order),
                )
            }
        )
        oracle = tuple([covered, uncovered][i] for i in order)
        result = attribute_statements(profile, {"f.go": oracle})
        assert set(result.files["f.go"].executed) == {9}
        assert set(result.files["f.go"].missing) == set()
