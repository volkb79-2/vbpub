"""O1/O2/O3 — the Go coverprofile parser, driven through
:func:`assay.coverage.load_coverage_profile`.

No Go toolchain is used anywhere here (A-042, §10) — every fixture is
literal committed text mirroring ``go test -coverprofile``'s own output
grammar, ported from the REASONING in
``shared-ramdisk-depot-manager/tools/covergate/profile.go`` (handoff item 5),
not its code.

Negative (O1): removing this format from the registry, binding it to Go
specifically, or collapsing the "executed wins on overlapping blocks" rule
changes a literal normalized fixture — this is DESIGN-GUIDE §11's own
"multiple blocks per line" case, and the Windows-drive-letter fixture is the
"drive-letter handling" the handoff names explicitly. Negative (O2): ignoring
a malformed block line, or selecting a parser from sniffed content rather
than the declared one, makes a reject fixture load. Negative (O3): reporting
``excluded=frozenset()`` instead of ``None`` falsely claims this format can
say "zero exclusions" — DESIGN-GUIDE §11's own worked example for why
``None`` exists at all. Negative (wave-1 O3, branches): reporting
``branches=BranchCoverage(by_line={})`` instead of ``None`` would claim this
format measured branches and found none, when it structurally cannot
measure them at all -- the identical A-008-class lie one field over.
"""

from __future__ import annotations

import pytest

from assay.coverage import FileCoverage, derive_branch_capability, load_coverage_profile
from assay.coverage_parsers import go_cover
from assay.errors import AssayError, Outcome, ReasonCode

#: `foo.go`'s line 5 is claimed by TWO overlapping blocks with different
#: counts (an if/else split) -- executed must win. `bar.go` uses a Windows
#: drive-letter path, whose own colon (after `C`) must not be mistaken for
#: the delimiter that separates the path from the position spec -- the LAST
#: colon is, and its line 12 repeats the same overlapping-block case.
DRIVE_LETTER_AND_OVERLAP_ARTIFACT = """\
mode: set
github.com/example/pkg/foo.go:3.10,5.2 2 1
github.com/example/pkg/foo.go:5.2,7.3 1 0
C:\\Users\\dev\\project\\pkg\\bar.go:10.5,12.9 1 1
C:\\Users\\dev\\project\\pkg\\bar.go:12.9,14.2 1 0
"""


def test_windows_drive_letter_path_and_overlapping_blocks_normalize_correctly():
    profile = load_coverage_profile(
        DRIVE_LETTER_AND_OVERLAP_ARTIFACT, declared_format="go-cover"
    )

    assert set(profile.files) == {
        "github.com/example/pkg/foo.go",
        "C:\\Users\\dev\\project\\pkg\\bar.go",
    }
    # line 5 is claimed by block 1 (executed, count=1) AND block 2
    # (never-taken, count=0) -- executed wins, so it must NOT appear missing.
    assert profile.files["github.com/example/pkg/foo.go"] == FileCoverage(
        executed=frozenset({3, 4, 5}), missing=frozenset({6, 7}), excluded=None
    )
    # the Windows path itself proves the LAST-colon split: if the FIRST colon
    # (after "C") were used instead, the path would be parsed as just "C" and
    # this key would not exist at all.
    assert profile.files["C:\\Users\\dev\\project\\pkg\\bar.go"] == FileCoverage(
        executed=frozenset({10, 11, 12}), missing=frozenset({13, 14}), excluded=None
    )


def test_executed_still_wins_when_the_never_taken_block_is_parsed_first():
    # Order independence: the merge rule must not depend on which block a
    # scanner happens to visit first.
    artifact = (
        "mode: set\n"
        "pkg/f.go:1.1,3.1 1 0\n"
        "pkg/f.go:2.1,2.1 1 1\n"
    )
    profile = load_coverage_profile(artifact, declared_format="go-cover")
    assert profile.files["pkg/f.go"] == FileCoverage(
        executed=frozenset({2}), missing=frozenset({1, 3}), excluded=None
    )


def test_excluded_is_always_none_for_this_format():
    profile = load_coverage_profile(
        DRIVE_LETTER_AND_OVERLAP_ARTIFACT, declared_format="go-cover"
    )
    for file_coverage in profile.files.values():
        assert file_coverage.excluded is None


def test_branches_is_always_none_for_this_format():
    """O3 (wave-1): a real go-cover fixture parses to
    ``branch_capability = "unavailable"`` -- the format is a statement-count
    profile with no arc concept at all, declared rather than omitted (the
    parser sets ``branches=None`` unconditionally, module docstring)."""
    profile = load_coverage_profile(
        DRIVE_LETTER_AND_OVERLAP_ARTIFACT, declared_format="go-cover"
    )
    for file_coverage in profile.files.values():
        assert file_coverage.branches is None
    assert derive_branch_capability(profile) == "unavailable"


def test_a_non_go_extension_parses_identically_proving_no_language_binding():
    # `.rs` (Rust) proves the parser reads the GRAMMAR, not the extension --
    # nothing in this module checks whether a path ends in `.go`.
    artifact = "mode: set\npkg/generated_from_proto.rs:1.1,1.1 1 1\n"
    profile = load_coverage_profile(artifact, declared_format="go-cover")
    assert profile.files["pkg/generated_from_proto.rs"] == FileCoverage(
        executed=frozenset({1}), missing=frozenset(), excluded=None
    )


@pytest.mark.parametrize(
    "artifact",
    [
        pytest.param("mode: set\npkg/f.go:1.1,2.2 1\n", id="only-two-fields-in-block"),
        pytest.param("mode: set\npkg/f.go:1.1,2.2 1 1 1\n", id="four-fields-in-block"),
        pytest.param("mode: set\npkg/f.go:bad,2.2 1 1\n", id="no-dot-in-start-position"),
        pytest.param("mode: set\npkg/f.go:1.1,bad 1 1\n", id="no-dot-in-end-position"),
        pytest.param("mode: set\npkg/f.go:x.5,2.2 1 1\n", id="start-position-has-a-dot-but-not-an-integer"),
        pytest.param("mode: set\npkg/f.go:1.5 1 1\n", id="position-spec-has-no-comma"),
        pytest.param("mode: set\npkg/f.go:0.1,2.2 1 1\n", id="start-line-not-positive"),
        pytest.param("mode: set\npkg/f.go:3.1,2.2 1 1\n", id="end-before-start"),
        pytest.param("mode: set\npkg/f.go:1.1,2.2 x 1\n", id="numStmts-not-an-integer"),
        pytest.param("mode: set\npkg/f.go:1.1,2.2 1 x\n", id="count-not-an-integer"),
        pytest.param("mode: set\npkg/f.go:1.1,2.2 1 -1\n", id="negative-count"),
        pytest.param("mode: set\n:1.1,2.2 1 1\n", id="empty-path-before-position"),
        pytest.param("mode: set\npkg/f.go 1 1\n", id="no-colon-at-all"),
    ],
)
def test_malformed_block_raises_unreadable_artifact(artifact: str):
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="go-cover")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_missing_mode_header_raises_unreadable_artifact_when_parsed_directly():
    # A profile with no `mode:` header fails SNIFF too (both check the same
    # first line), so this defect is only reachable by calling the parser
    # module directly -- exactly what this test does, to prove `parse()`
    # itself still guards it independent of the registry's sniff cross-check.
    with pytest.raises(AssayError) as excinfo:
        go_cover.parse("pkg/f.go:1.1,2.2 1 1\n", producer=None)
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT


def test_declared_format_mismatch_is_refused_before_any_record_is_read():
    coverage_py_json_text = '{"files": {"a.py": {"executed_lines": [1], "missing_lines": []}}}'
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(coverage_py_json_text, declared_format="go-cover")
    assert excinfo.value.reason_code is ReasonCode.FORMAT_MISMATCH


def test_sniff_is_a_cheap_signature_check_not_a_parse():
    assert go_cover.sniff(DRIVE_LETTER_AND_OVERLAP_ARTIFACT) is True
    assert go_cover.sniff('{"files": {}}') is False
    assert go_cover.sniff("SF:a.c\nend_of_record\n") is False
    assert go_cover.sniff("") is False


def test_sniff_skips_leading_blank_lines_to_find_the_first_real_line():
    assert go_cover.sniff("\n   \nmode: set\npkg/f.go:1.1,1.1 1 1\n") is True
    assert go_cover.sniff("\n   \nnot a mode line\n") is False


# --- B039/B047 item 4: the shared classified-line ceiling -------------------
#
# `parse` used to expand a block's `range(start, end + 1)` with no fixed
# bound at all -- the identical unbounded-materialization shape
# `coverage_istanbul_json` was given a fixed ceiling for. These tests pin the
# SAME shared `ClassifiedLineBudget` (`assay.coverage_parsers.model`) both
# parsers now spend from, exercised through go_cover's own call site --
# `test_coverage_parsers_coverage_istanbul_json.py`'s own boundary tests
# prove the identical arithmetic for the sibling parser.


def test_one_enormous_block_is_refused_rather_than_expanded():
    """The malicious shape B039 exists for: a ~60-byte block declaring an end
    line of 999999999 sits far inside the 16 MiB
    ``MAX_COVERAGE_ARTIFACT_BYTES`` read bound and would otherwise
    materialize close to a billion dict entries."""
    artifact = "mode: set\npkg/f.go:1.1,999999999.1 1 1\n"
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="go-cover")
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    assert "classified lines" in str(excinfo.value)


def test_an_ordinary_real_shaped_profile_still_parses_under_the_shared_bound():
    """The paired must-succeed control: an ordinary profile -- the exact
    fixture every other test in this module already proves normalizes
    correctly -- still parses cleanly now that every block spends the SAME
    shared budget istanbul's parser does, at the SHIPPED ceiling (not
    monkeypatched down for this one)."""
    profile = load_coverage_profile(
        DRIVE_LETTER_AND_OVERLAP_ARTIFACT, declared_format="go-cover"
    )
    assert profile.files["github.com/example/pkg/foo.go"] == FileCoverage(
        executed=frozenset({3, 4, 5}), missing=frozenset({6, 7}), excluded=None
    )


@pytest.fixture
def tiny_bound(monkeypatch: pytest.MonkeyPatch) -> int:
    """go_cover's own re-exported ``MAX_CLASSIFIED_LINES`` lowered to a
    handful of lines, the same off-by-one boundary pin
    ``test_coverage_parsers_coverage_istanbul_json.py``'s own ``tiny_bound``
    fixture already proves for istanbul -- here through go_cover's own call
    site, over the identical shared class."""
    monkeypatch.setattr(go_cover, "MAX_CLASSIFIED_LINES", 6)
    return 6


def test_a_block_exactly_at_the_bound_still_parses(tiny_bound: int):
    artifact = f"mode: set\npkg/f.go:1.1,{tiny_bound}.1 1 1\n"
    profile = load_coverage_profile(artifact, declared_format="go-cover")
    assert profile.files["pkg/f.go"].executed == frozenset(range(1, tiny_bound + 1))


def test_a_block_one_line_past_the_bound_is_refused(tiny_bound: int):
    artifact = f"mode: set\npkg/f.go:1.1,{tiny_bound + 1}.1 1 1\n"
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="go-cover")
    assert "classified lines" in str(excinfo.value)


def test_the_bound_is_spent_across_the_whole_profile_not_per_block(tiny_bound: int):
    """A profile made of many small blocks is refused by the same counter
    that refuses one huge block -- the bound is a PROFILE property, exactly
    as it already is for istanbul. Neither block here would breach it
    alone."""
    per_block = tiny_bound // 2 + 1
    artifact = "mode: set\n" + "".join(
        f"pkg/f{index}.go:1.1,{per_block}.1 1 1\n" for index in range(2)
    )
    with pytest.raises(AssayError) as excinfo:
        load_coverage_profile(artifact, declared_format="go-cover")
    assert "classified lines" in str(excinfo.value)


def test_the_shipped_bound_is_the_one_shared_documented_value():
    """The constant lives in ONE place now (B039's own acceptance box 2):
    go_cover's re-exported name and model's canonical one are the identical
    object, not two literals that happen to agree today."""
    from assay.coverage_parsers.model import MAX_CLASSIFIED_LINES as shared_ceiling

    assert go_cover.MAX_CLASSIFIED_LINES is shared_ceiling
    assert go_cover.MAX_CLASSIFIED_LINES == 2_000_000
